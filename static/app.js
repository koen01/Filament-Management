/* Minimal read-only UI for Creality K2 Plus CFS via Moonraker */

const $ = (id) => document.getElementById(id);

function fmtTs(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  } catch {
    return "—";
  }
}

function badge(el, text, cls) {
  el.classList.remove("ok", "bad", "warn");
  if (cls) el.classList.add(cls);
  el.textContent = text;
}

function slotEl(slotId, label, meta, isActive) {
  const wrap = document.createElement("div");
  wrap.className = "slot" + (isActive ? " active" : "");
  wrap.dataset.slotid = slotId;

  const left = document.createElement("div");
  left.className = "slotLeft";

  const sw = document.createElement("div");
  sw.className = "swatch";
  sw.style.background = meta.color || "#2a3442";
  left.appendChild(sw);

  const txt = document.createElement("div");
  txt.className = "slotText";

  const nm = document.createElement("div");
  nm.className = "slotName";
  nm.textContent = label;
  txt.appendChild(nm);

  const sub = document.createElement("div");
  sub.className = "slotSub";
  // Line 2: brand + filament name if available, else material + color
  const brandName = [meta.manufacturer, meta.name].filter(Boolean).join(' ');
  if (brandName) {
    sub.textContent = brandName;
  } else {
    const parts = [];
    if (meta.material) parts.push(meta.material);
    if (meta.color) parts.push(meta.color.toUpperCase());
    sub.textContent = parts.length ? parts.join(" · ") : "—";
  }
  txt.appendChild(sub);

  // Line 3: material type + Spoolman link indicator (only shown when line 2 has brand/name info)
  const detailParts = [];
  if (brandName && meta.material) detailParts.push(meta.material);
  if (meta.spoolman_id) detailParts.push('SP #' + meta.spoolman_id);
  if (detailParts.length) {
    const detail = document.createElement("div");
    detail.className = "slotDetail";
    detail.textContent = detailParts.join(' · ');
    txt.appendChild(detail);
  }

  left.appendChild(txt);

  const right = document.createElement("div");
  right.className = "slotRight";
  const tag = document.createElement("div");
  tag.className = "tag" + (!meta.material ? " muted" : "");
  tag.textContent = meta.present === false ? t('status.empty') : (isActive ? t('status.active') : t('status.ready'));
  right.appendChild(tag);

  if (meta.percent != null) {
    const pct = document.createElement("div");
    pct.className = "spoolPct";
    pct.textContent = meta.percent + "%";
    right.appendChild(pct);
  }

  wrap.appendChild(left);
  wrap.appendChild(right);

  wrap.addEventListener("click", (ev) => {
    ev.preventDefault();
    openSpoolModal(slotId, meta);
  });
  return wrap;
}

function fmtMm(mm) {
  const m = (mm || 0) / 1000.0;
  if (m >= 10) return m.toFixed(1) + " m";
  return m.toFixed(2) + " m";
}

function fmtG(g) {
  if (g == null) return "0 g";
  const gg = Number(g);
  if (Number.isNaN(gg)) return "0 g";
  if (gg >= 100) return gg.toFixed(0) + " g";
  if (gg >= 10) return gg.toFixed(1) + " g";
  return gg.toFixed(2) + " g";
}

function fmtUsedFromMm(mm) {
  const m = (mm || 0) / 1000.0;
  if (m >= 10) return m.toFixed(1) + " m";
  return m.toFixed(2) + " m";
}


async function postJson(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    throw new Error(t || `HTTP ${r.status}`);
  }
  return r.json();
}

// --- Spoolman integration ---
let spoolmanConfigured = false;

// --- Spool editor modal (local only) ---
let spoolModalOpen = false;
let spoolPrevPaused = null;
let spoolSlotId = null;

function closeSpoolModal() {
  const m = $('spoolModal');
  if (m) m.style.display = 'none';
  spoolModalOpen = false;
  spoolSlotId = null;
  if (spoolPrevPaused !== null) {
    refreshPaused = spoolPrevPaused;
    spoolPrevPaused = null;
    applyRefreshTimer();
  }
}

function openSpoolModal(slotId, meta) {
  // Only open if modal exists (older builds)
  const m = $('spoolModal');
  if (!m) return;
  spoolModalOpen = true;
  spoolSlotId = slotId;

  // Pause auto-refresh while editing so nothing collapses
  if (spoolPrevPaused === null) spoolPrevPaused = refreshPaused;
  refreshPaused = true;
  applyRefreshTimer();

  const title = $('spoolTitle');
  const sub = $('spoolSub');
  if (title) title.textContent = `Box ${slotId[0]} · Slot ${slotId[1]}`;
  if (sub) sub.textContent = `${meta.material || '—'} · ${(meta.color || '').toUpperCase() || '—'}`;

  // New roll input stays empty by default
  const startEl = $('spoolStart');
  if (startEl) startEl.value = '';

  // --- Spoolman section ---
  const smSec = $('spoolmanSection');
  if (smSec) {
    if (spoolmanConfigured) {
      smSec.style.display = '';
      const badge = $('spoolmanBadge');
      const notLinked = $('spoolmanNotLinked');
      const linked = $('spoolmanLinked');
      const info = $('spoolmanInfo');
      const smId = meta.spoolman_id;
      if (smId) {
        if (badge) { badge.textContent = t('spoolman.linked'); badge.classList.remove('muted'); badge.classList.add('ok'); }
        if (notLinked) notLinked.style.display = 'none';
        if (linked) linked.style.display = 'flex';
        if (info) {
          info.textContent = t('spoolman.loading_spool');
          // Fetch live remaining from Spoolman
          fetch(`/api/ui/spoolman/spool_detail?slot=${encodeURIComponent(slotId)}`, { cache: 'no-store' })
            .then(r => r.json())
            .then(data => {
              if (data.spool && data.spool.remaining_weight != null) {
                info.textContent = t('spoolman.linked_info', {
                  id: String(smId),
                  vendor: meta.manufacturer || meta.vendor || '',
                  name: meta.name || '',
                  remaining: fmtG(data.spool.remaining_weight),
                });
              } else {
                info.textContent = t('spoolman.linked_info', {
                  id: String(smId),
                  vendor: meta.manufacturer || meta.vendor || '',
                  name: meta.name || '',
                  remaining: data.error ? t('spoolman.unavailable') : '—',
                });
              }
            })
            .catch(() => {
              info.textContent = t('spoolman.linked_info', {
                id: String(smId),
                vendor: meta.manufacturer || meta.vendor || '',
                name: meta.name || '',
                remaining: t('spoolman.unavailable'),
              });
            });
        }
      } else {
        if (badge) { badge.textContent = t('spoolman.not_linked'); badge.classList.add('muted'); badge.classList.remove('ok'); }
        if (notLinked) notLinked.style.display = 'flex';
        if (linked) linked.style.display = 'none';
        loadSpoolmanDropdown(slotId);
      }
    } else {
      smSec.style.display = 'none';
    }
  }

  m.style.display = 'block';
}

async function loadSpoolmanDropdown(slotId) {
  const sel = $('spoolmanSelect');
  if (!sel) return;
  sel.innerHTML = '';
  const ph = document.createElement('option');
  ph.value = '';
  ph.textContent = t('spoolman.loading');
  sel.appendChild(ph);

  try {
    const r = await fetch(`/api/ui/spoolman/spools?slot=${encodeURIComponent(slotId)}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const spools = data.spools || [];
    sel.innerHTML = '';

    if (!spools.length) {
      const o = document.createElement('option');
      o.value = '';
      o.textContent = t('spoolman.no_spools');
      sel.appendChild(o);
      return;
    }

    const def = document.createElement('option');
    def.value = '';
    def.textContent = t('spoolman.select_ph');
    sel.appendChild(def);

    for (const sp of spools) {
      const o = document.createElement('option');
      o.value = String(sp.id);
      o.textContent = t('spoolman.option_label', {
        id: String(sp.id),
        vendor: sp.vendor || '',
        name: sp.filament_name || '',
        material: sp.material || '',
        remaining: sp.remaining_weight != null ? fmtG(sp.remaining_weight) : '?',
      });
      sel.appendChild(o);
    }
  } catch (e) {
    sel.innerHTML = '';
    const o = document.createElement('option');
    o.value = '';
    o.textContent = t('spoolman.error', { msg: e.message || String(e) });
    sel.appendChild(o);
  }
}

function initSpoolModal() {
  const m = $('spoolModal');
  if (!m) return;
  const closeBtn = $('spoolClose');
  const back = $('spoolBackdrop');
  // IMPORTANT: stop event bubbling so a click does not "fall through" to the
  // underlying slot card and immediately re-open the modal.
  if (closeBtn) closeBtn.onclick = (ev) => {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    closeSpoolModal();
  };
  if (back) back.onclick = (ev) => {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    closeSpoolModal();
  };

  // Esc closes the modal
  document.addEventListener('keydown', (ev) => {
    if (!spoolModalOpen) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      closeSpoolModal();
    }
  });

  const saveStart = $('spoolSaveStart');

  if (saveStart) {
    saveStart.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!spoolSlotId) return;
      // Rollwechsel: new epoch + auto-unlink Spoolman
      await postJson('/api/ui/spool/set_start', { slot: spoolSlotId });
      closeSpoolModal();
      await tick();
    };
  }

  // --- Spoolman button handlers ---
  const smLink = $('spoolmanLink');
  const smUnlink = $('spoolmanUnlink');
  const smRefresh = $('spoolmanRefresh');

  if (smLink) {
    smLink.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!spoolSlotId) return;
      const sel = $('spoolmanSelect');
      const id = sel ? Number(sel.value) : 0;
      if (!id) return;
      await postJson('/api/ui/spoolman/link', { slot: spoolSlotId, spoolman_id: id });
      closeSpoolModal();
      await tick();
    };
  }

  if (smUnlink) {
    smUnlink.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!spoolSlotId) return;
      await postJson('/api/ui/spoolman/unlink', { slot: spoolSlotId });
      closeSpoolModal();
      await tick();
    };
  }

  if (smRefresh) {
    smRefresh.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!spoolSlotId) return;
      // Re-fetch spool detail from Spoolman
      const info = $('spoolmanInfo');
      try {
        if (info) info.textContent = t('spoolman.loading_spool');
        const r = await fetch(`/api/ui/spoolman/spool_detail?slot=${encodeURIComponent(spoolSlotId)}`, { cache: 'no-store' });
        const data = await r.json();
        if (data.spool && data.spool.remaining_weight != null) {
          const stateR = await fetch('/api/ui/state', { cache: 'no-store' });
          const stateJ = await stateR.json();
          const stateData = stateJ.result || stateJ;
          const slotData = (stateData.slots || {})[spoolSlotId] || {};
          if (info) info.textContent = t('spoolman.linked_info', {
            id: String(data.spool.id || slotData.spoolman_id || ''),
            vendor: slotData.manufacturer || slotData.vendor || '',
            name: slotData.name || '',
            remaining: fmtG(data.spool.remaining_weight),
          });
        } else {
          if (info) info.textContent = data.error ? t('spoolman.unavailable') : '—';
        }
      } catch (e) {
        if (info) info.textContent = t('spoolman.error', { msg: e.message || String(e) });
      }
    };
  }
}


async function fetchAndRenderSpoolmanStatus(activeSlot, state) {
  const wrap = $("slotHistory");
  if (!wrap) return;

  const slot = activeSlot || state.active_slot || null;

  wrap.innerHTML = '';
  const loading = document.createElement('div');
  loading.className = 'tag muted';
  loading.textContent = t('spoolman.loading_spool');
  wrap.appendChild(loading);

  if (!spoolmanConfigured) {
    wrap.innerHTML = '';
    const msg = document.createElement('div');
    msg.className = 'tag muted';
    msg.textContent = t('spoolman.not_configured');
    wrap.appendChild(msg);
    return;
  }

  if (!slot) {
    wrap.innerHTML = '';
    const msg = document.createElement('div');
    msg.className = 'tag muted';
    msg.textContent = t('spoolman.slot_not_linked');
    wrap.appendChild(msg);
    return;
  }

  try {
    const r = await fetch(`/api/ui/spoolman/spool_detail?slot=${encodeURIComponent(slot)}`, { cache: 'no-store' });
    const data = await r.json();
    wrap.innerHTML = '';

    if (!data.linked) {
      const msg = document.createElement('div');
      msg.className = 'tag muted';
      msg.textContent = t('spoolman.slot_not_linked');
      wrap.appendChild(msg);
      return;
    }

    if (!data.spool) {
      const msg = document.createElement('div');
      msg.className = 'tag muted';
      msg.textContent = t('spoolman.unavailable');
      wrap.appendChild(msg);
      return;
    }

    const sp = data.spool;
    const card = document.createElement('div');
    card.className = 'spoolStatusCard';

    const rows = [
      { label: t('spoolman.remaining'), value: sp.remaining_weight != null ? fmtG(sp.remaining_weight) : '—' },
      { label: t('spoolman.used_total'), value: sp.used_weight != null ? fmtG(sp.used_weight) : '—' },
      { label: t('spoolman.first_used'), value: sp.first_used ? fmtTs(new Date(sp.first_used).getTime() / 1000) : '—' },
      { label: t('spoolman.last_used'), value: sp.last_used ? fmtTs(new Date(sp.last_used).getTime() / 1000) : '—' },
    ];

    for (const row of rows) {
      const div = document.createElement('div');
      div.className = 'spoolStatRow';
      const lbl = document.createElement('span');
      lbl.className = 'spoolStatLabel';
      lbl.textContent = row.label;
      const val = document.createElement('span');
      val.className = 'spoolStatValue';
      val.textContent = row.value;
      div.appendChild(lbl);
      div.appendChild(val);
      card.appendChild(div);
    }

    wrap.appendChild(card);
  } catch (e) {
    wrap.innerHTML = '';
    const msg = document.createElement('div');
    msg.className = 'tag muted';
    msg.textContent = t('spoolman.unavailable');
    wrap.appendChild(msg);
  }
}

function render(state) {
  const printerBadge = $("printerBadge");
  const cfsBadge = $("cfsBadge");

  const printerOk = !!state.printer_connected;
  badge(printerBadge, printerOk ? t('badge.printer_ok') : t('badge.printer_off'), printerOk ? "ok" : "bad");
  if (!printerOk && state.printer_last_error) {
    printerBadge.textContent += " (" + state.printer_last_error + ")";
  }

  const cfsOk = !!state.cfs_connected;
  badge(
    cfsBadge,
    cfsOk ? t('badge.cfs_ok', {ts: fmtTs(state.cfs_last_update)}) : t('badge.cfs_off'),
    cfsOk ? "ok" : "warn"
  );

  // We prefer Creality CFS slots (state.cfs_slots). Fallback to local slots if not present.
  const slots = (state.cfs_slots && Object.keys(state.cfs_slots).length) ? state.cfs_slots : state.slots;

  const active = state.cfs_active_slot || state.active_slot || null;

  const boxesGrid = $("boxesGrid");
  boxesGrid.innerHTML = "";

  // Determine which CFS boxes are actually connected.
  const boxesInfo = (slots && slots._boxes) ? slots._boxes : {};
  const connectedBoxes = [];
  for (const n of ["1", "2", "3", "4"]) {
    const bi = boxesInfo[n];
    if (bi && bi.connected === true) connectedBoxes.push(n);
  }
  // Fallback: if firmware doesn't provide box connection metadata, show Box 1 & 2.
  if (!connectedBoxes.length) connectedBoxes.push("1", "2");

  const metaFor = (sid) => {
    // We render slots primarily from Creality CFS data (state.cfs_slots),
    // BUT spool tracking (remaining/consumed + reference points) lives in state.slots.
    // Therefore we must merge both.
    const m = (slots && slots[sid]) ? slots[sid] : {};
    const local = (state.slots && state.slots[sid]) ? state.slots[sid] : {};

    // normalize fields from either cfs_slots or local slots
    const out = {
      present: (m.present ?? local.present ?? true),
      material: ((m.material ?? local.material) || "").toString().toUpperCase(),
      color: ((m.color ?? m.color_hex ?? local.color ?? local.color_hex) || "").toString().toLowerCase(),

      // spool epoch (for roll-change tracking)
      spool_epoch: (local.spool_epoch ?? null),

      // Spoolman
      spoolman_id: (local.spoolman_id ?? null),
      name: (local.name ?? ''),
      manufacturer: (local.manufacturer ?? local.vendor ?? ''),

      // CFS percent remaining from WS data
      percent: (m.percent != null ? m.percent : null),
    };
    return out;
  };

  function makeBoxCard(boxNum) {
    const card = document.createElement("div");
    card.className = "card";

    const head = document.createElement("div");
    head.className = "cardHead";

    const title = document.createElement("div");
    title.className = "cardTitle";
    title.textContent = `Box ${boxNum}`;

    const meta = document.createElement("div");
    meta.className = "cardMeta";

    const bi = boxesInfo[boxNum] || {};
    // Temperature / humidity per box (Creality reports these as numbers/strings)
    const tC = bi.temperature_c;
    const rh = bi.humidity_pct;
    const hasT = (typeof tC === "number" && !Number.isNaN(tC));
    const hasRh = (typeof rh === "number" && !Number.isNaN(rh));

    // Render as compact "chips" (bigger + clearer than plain text)
    if (hasT) {
      const sp = document.createElement("span");
      sp.className = "envItem";
      sp.textContent = `🌡 ${Math.round(tC)}°C`;
      meta.appendChild(sp);
    }
    if (hasRh) {
      const sp = document.createElement("span");
      sp.className = "envItem";
      sp.textContent = `💧 ${Math.round(rh)}%`;
      meta.appendChild(sp);
    }

    head.appendChild(title);
    if (meta.childNodes.length) head.appendChild(meta);
    card.appendChild(head);

    const slotsWrap = document.createElement("div");
    slotsWrap.className = "slots";
    for (const letter of ["A", "B", "C", "D"]) {
      const sid = `${boxNum}${letter}`;
      slotsWrap.appendChild(slotEl(sid, `Slot ${letter}`, metaFor(sid), sid === active));
    }
    card.appendChild(slotsWrap);
    return card;
  }

  for (const b of connectedBoxes) {
    boxesGrid.appendChild(makeBoxCard(b));
  }

  // Right-side Spoolman status panel
  fetchAndRenderSpoolmanStatus(active, state);

  // Active card
  const activeRow = $("activeRow");
  activeRow.innerHTML = "";
  const activeLive = $("activeLive");
  if (activeLive) {
    activeLive.style.display = "none";
    activeLive.innerHTML = "";
  }
  if (active && (slots[active] || state.slots[active])) {
    const m = metaFor(active);
    activeRow.appendChild(slotEl(active, `Box ${active[0]} · Slot ${active[1]}`, m, true));
    $("activeMeta").textContent = m.material ? (m.material + " · " + (m.color ? m.color.toUpperCase() : "")) : "—";
  } else {
    $("activeMeta").textContent = "—";
  }
}

async function tick() {
  try {
    const r = await fetch("/api/ui/state", { cache: "no-store" });
    const j = await r.json();
    const st = j.result || j;
    spoolmanConfigured = !!st.spoolman_configured;
    render(st);
  } catch (e) {
    badge($("printerBadge"), t('badge.printer_dash'), "warn");
    badge($("cfsBadge"), t('badge.cfs_off'), "warn");
  }
}

// --- Refresh control (client-side only) ---
let refreshTimer = null;
let refreshMs = Number(localStorage.getItem('refreshMs') || 10000);
if (!Number.isFinite(refreshMs) || refreshMs < 2000) refreshMs = 10000;
let refreshPaused = localStorage.getItem('refreshPaused') === '1';

function applyRefreshTimer() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;
  if (!refreshPaused) refreshTimer = setInterval(tick, refreshMs);

  const sel = $('refreshSelect');
  const btn = $('refreshToggle');
  if (sel) sel.value = String(refreshMs);
  if (btn) {
    btn.textContent = refreshPaused ? '▶' : '⏸';
    btn.classList.toggle('paused', refreshPaused);
  }
}

function initRefreshControls() {
  const sel = $('refreshSelect');
  const btn = $('refreshToggle');
  if (sel) {
    sel.value = String(refreshMs);
    sel.onchange = () => {
      refreshMs = Number(sel.value || 10000);
      if (!Number.isFinite(refreshMs) || refreshMs < 2000) refreshMs = 10000;
      localStorage.setItem('refreshMs', String(refreshMs));
      applyRefreshTimer();
    };
  }
  if (btn) {
    btn.onclick = () => {
      refreshPaused = !refreshPaused;
      localStorage.setItem('refreshPaused', refreshPaused ? '1' : '0');
      if (!refreshPaused) tick();
      applyRefreshTimer();
    };
  }
  applyRefreshTimer();
}

function initLangSwitcher() {
  const btns = document.querySelectorAll('.langBtn');
  function updateActive() {
    const cur = i18nLang();
    for (const b of btns) b.classList.toggle('active', b.dataset.lang === cur);
  }
  for (const b of btns) {
    b.addEventListener('click', () => {
      i18nSetLang(b.dataset.lang);
      updateActive();
      tick(); // re-render dynamic content with new language
    });
  }
  updateActive();
}

function boot() {
  i18nSetLang(i18nDetectLang());
  initLangSwitcher();
  initSpoolModal();
  initRefreshControls();
  tick();
}

// app.js may be loaded before some HTML (e.g. the spool modal) in certain
// templates. Ensure we wire up DOM-dependent handlers only after DOM is ready.
if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
