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
  tag.textContent = meta.present === false ? 'empty' : (isActive ? 'active' : 'ready');
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
    const txt = await r.text().catch(() => "");
    throw new Error(txt || `HTTP ${r.status}`);
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
      const bdg = $('spoolmanBadge');
      const notLinked = $('spoolmanNotLinked');
      const linked = $('spoolmanLinked');
      const info = $('spoolmanInfo');
      const smId = meta.spoolman_id;
      if (smId) {
        if (bdg) { bdg.textContent = 'linked'; bdg.classList.remove('muted'); bdg.classList.add('ok'); }
        if (notLinked) notLinked.style.display = 'none';
        if (linked) linked.style.display = 'flex';
        if (info) {
          info.textContent = 'Loading spool data…';
          // Fetch live remaining from Spoolman
          fetch(`/api/ui/spoolman/spool_detail?slot=${encodeURIComponent(slotId)}`, { cache: 'no-store' })
            .then(r => r.json())
            .then(data => {
              if (data.spool) {
                const fil = data.spool.filament || {};
                const vendor = (fil.vendor || {}).name || meta.manufacturer || meta.vendor || '';
                const name = fil.name || meta.name || '';
                const material = (fil.material || '').toUpperCase();
                const remaining = data.spool.remaining_weight != null ? fmtG(data.spool.remaining_weight) : '—';
                info.textContent = [vendor, name, material, remaining].filter(Boolean).join(' · ');
              } else {
                info.textContent = data.error ? 'Spoolman unreachable' : `Spool #${smId}`;
              }
            })
            .catch(() => {
              info.textContent = 'Spoolman unreachable';
            });
        }
      } else {
        if (bdg) { bdg.textContent = 'not linked'; bdg.classList.add('muted'); bdg.classList.remove('ok'); }
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
  const list = $('spoolmanSelect');
  if (!list) return;
  list.innerHTML = '';
  const ph = document.createElement('div');
  ph.className = 'spoolmanListItem muted';
  ph.textContent = 'Loading spools…';
  list.appendChild(ph);

  try {
    const r = await fetch(`/api/ui/spoolman/spools?slot=${encodeURIComponent(slotId)}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const spools = data.spools || [];
    list.innerHTML = '';

    if (!spools.length) {
      const o = document.createElement('div');
      o.className = 'spoolmanListItem muted';
      o.textContent = 'No spools found';
      list.appendChild(o);
      return;
    }

    for (const sp of spools) {
      const item = document.createElement('div');
      item.className = 'spoolmanListItem';
      item.dataset.id = String(sp.id);

      const swatch = document.createElement('span');
      swatch.className = 'spoolmanListSwatch';
      const col = sp.color_hex ? (sp.color_hex.startsWith('#') ? sp.color_hex : '#' + sp.color_hex) : null;
      if (col) swatch.style.background = col;

      const label = document.createElement('span');
      const remaining = sp.remaining_weight != null ? fmtG(sp.remaining_weight) : '?';
      label.textContent = `#${sp.id} ${sp.vendor || ''} ${sp.filament_name || ''} · ${sp.material || ''} · ${remaining}`;

      item.appendChild(swatch);
      item.appendChild(label);
      item.addEventListener('click', () => {
        for (const el of list.querySelectorAll('.spoolmanListItem')) el.classList.remove('selected');
        item.classList.add('selected');
      });
      list.appendChild(item);
    }
  } catch (e) {
    list.innerHTML = '';
    const o = document.createElement('div');
    o.className = 'spoolmanListItem muted';
    o.textContent = `Spoolman error: ${e.message || String(e)}`;
    list.appendChild(o);
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
      const list = $('spoolmanSelect');
      const selected = list && list.querySelector('.spoolmanListItem.selected');
      const id = selected ? Number(selected.dataset.id) : 0;
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
        if (info) info.textContent = 'Loading spool data…';
        const r = await fetch(`/api/ui/spoolman/spool_detail?slot=${encodeURIComponent(spoolSlotId)}`, { cache: 'no-store' });
        const data = await r.json();
        if (data.spool) {
          const fil = data.spool.filament || {};
          const vendor = (fil.vendor || {}).name || '';
          const name = fil.name || '';
          const material = (fil.material || '').toUpperCase();
          const remaining = data.spool.remaining_weight != null ? fmtG(data.spool.remaining_weight) : '—';
          if (info) info.textContent = [vendor, name, material, remaining].filter(Boolean).join(' · ');
        } else {
          if (info) info.textContent = data.error ? 'Spoolman unreachable' : '—';
        }
      } catch (e) {
        if (info) info.textContent = `Spoolman error: ${e.message || String(e)}`;
      }
    };
  }
}


async function fetchAndRenderSpoolmanStatus(activeSlot, state) {
  const wrap = $("slotHistory");
  if (!wrap) return;

  const slot = activeSlot || null;

  wrap.innerHTML = '';
  const loading = document.createElement('div');
  loading.className = 'tag muted';
  loading.textContent = 'Loading spool data…';
  wrap.appendChild(loading);

  if (!spoolmanConfigured) {
    wrap.innerHTML = '';
    const msg = document.createElement('div');
    msg.className = 'tag muted';
    msg.textContent = 'Spoolman not configured';
    wrap.appendChild(msg);
    return;
  }

  if (!slot) {
    wrap.innerHTML = '';
    const msg = document.createElement('div');
    msg.className = 'tag muted';
    msg.textContent = 'No spool linked';
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
      msg.textContent = 'No spool linked';
      wrap.appendChild(msg);
      return;
    }

    if (!data.spool) {
      const msg = document.createElement('div');
      msg.className = 'tag muted';
      msg.textContent = 'Spoolman unreachable';
      wrap.appendChild(msg);
      return;
    }

    const sp = data.spool;
    const filament = sp.filament || {};
    const filamentName = filament.name || '';
    const material = (filament.material || '').toUpperCase();
    const vendor = (filament.vendor || {}).name || '';
    const colorHex = filament.color_hex
      ? (filament.color_hex.startsWith('#') ? filament.color_hex : '#' + filament.color_hex)
      : null;

    const card = document.createElement('div');
    card.className = 'spoolStatusCard';

    // Filament identity header
    const header = document.createElement('div');
    header.className = 'spoolStatusFilament';
    const swatch = document.createElement('span');
    swatch.className = 'spoolmanListSwatch';
    swatch.style.width = '20px';
    swatch.style.height = '20px';
    swatch.style.borderRadius = '5px';
    swatch.style.flexShrink = '0';
    if (colorHex) swatch.style.background = colorHex;
    header.appendChild(swatch);
    const info = document.createElement('div');
    info.className = 'spoolStatusFilamentInfo';
    const nameEl = document.createElement('div');
    nameEl.className = 'spoolStatusFilamentName';
    nameEl.textContent = [vendor, filamentName].filter(Boolean).join(' · ') || `Spool #${sp.id}`;
    const subEl = document.createElement('div');
    subEl.className = 'spoolStatusFilamentSub';
    subEl.textContent = [material, `#${sp.id}`].filter(Boolean).join(' · ');
    info.appendChild(nameEl);
    info.appendChild(subEl);
    header.appendChild(info);
    card.appendChild(header);

    const rows = [
      { label: 'Remaining', value: sp.remaining_weight != null ? fmtG(sp.remaining_weight) : '—' },
      { label: 'Used total', value: sp.used_weight != null ? fmtG(sp.used_weight) : '—' },
      { label: 'First used', value: sp.first_used ? fmtTs(new Date(sp.first_used).getTime() / 1000) : '—' },
      { label: 'Last used', value: sp.last_used ? fmtTs(new Date(sp.last_used).getTime() / 1000) : '—' },
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
    msg.textContent = 'Spoolman unreachable';
    wrap.appendChild(msg);
  }
}

function hexBrightness(hex) {
  const h = (hex || '').replace('#', '');
  if (h.length !== 6) return 128;
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return (r * 299 + g * 587 + b * 114) / 1000;
}

function makeSpoolSvg(meta) {
  const present = meta.present !== false;
  const rawColor = meta.color || '';
  const hasColor = present && rawColor && rawColor !== '#2a3442' && rawColor.length >= 4;

  if (!hasColor) {
    // Empty slot — dark disk with diagonal slash
    return `<svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="40" cy="40" r="36" fill="#1e2230" stroke="#141720" stroke-width="3"/>
      <line x1="22" y1="58" x2="58" y2="22" stroke="#484d5a" stroke-width="4" stroke-linecap="round"/>
    </svg>`;
  }

  const c = rawColor.startsWith('#') ? rawColor : '#' + rawColor;
  const bright = hexBrightness(c);
  const tick  = bright > 145 ? 'rgba(0,0,0,0.28)' : 'rgba(255,255,255,0.18)';

  return `<svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="40" cy="40" r="36" fill="${c}" stroke="#141720" stroke-width="3"/>
    <circle cx="40" cy="40" r="20" fill="none" stroke="${tick}" stroke-width="1.5"/>
    <line x1="40" y1="22" x2="40" y2="29" stroke="${tick}" stroke-width="2.5" stroke-linecap="round"/>
    <line x1="40" y1="51" x2="40" y2="58" stroke="${tick}" stroke-width="2.5" stroke-linecap="round"/>
    <line x1="22" y1="40" x2="29" y2="40" stroke="${tick}" stroke-width="2.5" stroke-linecap="round"/>
    <line x1="51" y1="40" x2="58" y2="40" stroke="${tick}" stroke-width="2.5" stroke-linecap="round"/>
    <circle cx="40" cy="40" r="10" fill="#1e2230" stroke="#2e3346" stroke-width="1.5"/>
    <circle cx="40" cy="40" r="3.5" fill="#50576a"/>
  </svg>`;
}

function render(state) {
  // Update subtitle with printer identity from WS
  const sub = $("printerSubtitle");
  if (sub) {
    const parts = [state.printer_name, state.printer_firmware].filter(Boolean);
    sub.textContent = parts.length ? parts.join(" · ") : "";
  }

  const printerBadge = $("printerBadge");
  const cfsBadge = $("cfsBadge");

  const printerOk = !!state.printer_connected;
  badge(printerBadge, printerOk ? 'Printer: connected' : 'Printer: disconnected', printerOk ? "ok" : "bad");
  if (!printerOk && state.printer_last_error) {
    printerBadge.textContent += " (" + state.printer_last_error + ")";
  }

  const cfsOk = !!state.cfs_connected;
  badge(
    cfsBadge,
    cfsOk ? `CFS: detected · ${fmtTs(state.cfs_last_update)}` : 'CFS: —',
    cfsOk ? "ok" : "warn"
  );

  // We prefer Creality CFS slots (state.cfs_slots). Fallback to local slots if not present.
  const slots = (state.cfs_slots && Object.keys(state.cfs_slots).length) ? state.cfs_slots : state.slots;

  const active = state.cfs_active_slot || null;

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
    const row = document.createElement("div");
    row.className = "boxRow";

    // Left: box header showing box number + env data
    const header = document.createElement("div");
    header.className = "boxHeader";

    const hTitle = document.createElement("div");
    hTitle.className = "boxHeaderTitle";
    hTitle.textContent = `Box ${boxNum}`;
    header.appendChild(hTitle);

    const bi = boxesInfo[boxNum] || {};
    const tC = bi.temperature_c;
    const rh = bi.humidity_pct;
    if (typeof tC === "number" && !Number.isNaN(tC)) {
      const chip = document.createElement("div");
      chip.className = "boxEnvChip";
      chip.textContent = `🌡 ${Math.round(tC)}°C`;
      header.appendChild(chip);
    }
    if (typeof rh === "number" && !Number.isNaN(rh)) {
      const chip = document.createElement("div");
      chip.className = "boxEnvChip";
      chip.textContent = `💧 ${Math.round(rh)}%`;
      header.appendChild(chip);
    }
    row.appendChild(header);

    // Right: 4 slot pods
    const slotsWrap = document.createElement("div");
    slotsWrap.className = "boxSlots";

    for (const letter of ["A", "B", "C", "D"]) {
      const sid = `${boxNum}${letter}`;
      const m = metaFor(sid);
      const isAct = sid === active;

      const pod = document.createElement("div");
      pod.className = "slotPod" + (isAct ? " active" : "");
      pod.dataset.slotid = sid;

      // Slot ID badge
      const idBadge = document.createElement("div");
      idBadge.className = "slotPodId";
      idBadge.textContent = sid;
      pod.appendChild(idBadge);

      // Spool graphic
      const spoolWrap = document.createElement("div");
      spoolWrap.className = "slotPodSpool";
      spoolWrap.innerHTML = makeSpoolSvg(m);
      pod.appendChild(spoolWrap);

      // Material
      const matEl = document.createElement("div");
      matEl.className = "slotPodMaterial";
      matEl.textContent = m.material || "—";
      pod.appendChild(matEl);

      // Percent remaining (if available from CFS)
      if (m.percent != null) {
        const pctEl = document.createElement("div");
        pctEl.className = "slotPodPct";
        pctEl.textContent = m.percent + "%";
        pod.appendChild(pctEl);
      }

      // Bottom icon: eye for active, pencil for others
      const iconEl = document.createElement("div");
      iconEl.className = "slotPodIcon";
      iconEl.textContent = isAct ? "◉" : "✏";
      pod.appendChild(iconEl);

      pod.addEventListener("click", (ev) => {
        ev.preventDefault();
        openSpoolModal(sid, m);
      });

      slotsWrap.appendChild(pod);
    }

    row.appendChild(slotsWrap);
    return row;
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
    badge($("printerBadge"), 'Printer: —', "warn");
    badge($("cfsBadge"), 'CFS: —', "warn");
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

function boot() {
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
