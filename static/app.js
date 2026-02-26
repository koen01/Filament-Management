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
  const parts = [];
  if (meta.material) parts.push(meta.material);
  if (meta.color) parts.push(meta.color.toUpperCase());
  sub.textContent = parts.length ? parts.join(" · ") : "—";
  txt.appendChild(sub);

  // Optional spool info (local, derived)
  // We show Rest BIG. (verbrauchte/used is available in the history; keeping tiles clean.)
  const rem = (meta.spool_remaining_g != null ? meta.spool_remaining_g : meta.remaining_g);
  if (rem != null) {
    const row = document.createElement('div');
    row.className = 'spoolRow';

    const rest = document.createElement('div');
    rest.className = 'spoolRest';
    rest.textContent = fmtG(rem);
    row.appendChild(rest);

    // warning styles based on remaining grams
    const r = Number(rem);
    if (Number.isFinite(r)) {
      if (r <= 50) wrap.classList.add('spoolCrit');
      else if (r <= 150) wrap.classList.add('spoolLow');
    }

    txt.appendChild(row);
  }
  left.appendChild(txt);

  const right = document.createElement("div");
  right.className = "slotRight";
  const tag = document.createElement("div");
  tag.className = "tag" + (!meta.material ? " muted" : "");
  tag.textContent = meta.present === false ? "leer" : (isActive ? "aktiv" : "bereit");
  right.appendChild(tag);

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

function buildSlotIds(connectedBoxes) {
  const slotIds = [];
  for (const b of connectedBoxes) {
    for (const l of ["A", "B", "C", "D"]) slotIds.push(`${b}${l}`);
  }
  return slotIds;
}

function jobKeyFromMoon(e) {
  const base = (e.job_id || e.job || "").toString();
  const ts = Math.floor(Number(e.ts_end || 0) || 0);
  return `${base}:${ts}`;
}

// --- UI state preservation across auto-refresh ---
// The page re-renders periodically. Without preserving state, <details> elements
// collapse while the user is interacting (e.g. assigning slots).
const uiState = {
  moonOpenKeys: new Set(),
  slotOpenKeys: new Set(),
  moonSelectValues: {},
};

function captureUiState() {
  uiState.moonOpenKeys = new Set(
    Array.from(document.querySelectorAll('#moonHistory details.moonEntry[open]'))
      .map((d) => d.dataset.key)
      .filter(Boolean)
  );
  uiState.slotOpenKeys = new Set(
    Array.from(document.querySelectorAll('#slotHistory details.histEntry[open]'))
      .map((d) => d.dataset.key)
      .filter(Boolean)
  );
  uiState.moonSelectValues = {};
  for (const sel of document.querySelectorAll('#moonHistory select.assignSel')) {
    const k = sel.dataset.selkey;
    if (k) uiState.moonSelectValues[k] = sel.value;
  }
}

function restoreUiState() {
  for (const d of document.querySelectorAll('#moonHistory details.moonEntry')) {
    const k = d.dataset.key;
    if (k && uiState.moonOpenKeys && uiState.moonOpenKeys.has(k)) d.open = true;
  }
  for (const d of document.querySelectorAll('#slotHistory details.histEntry')) {
    const k = d.dataset.key;
    if (k && uiState.slotOpenKeys && uiState.slotOpenKeys.has(k)) d.open = true;
  }
  for (const sel of document.querySelectorAll('#moonHistory select.assignSel')) {
    const k = sel.dataset.selkey;
    if (k && uiState.moonSelectValues && Object.prototype.hasOwnProperty.call(uiState.moonSelectValues, k)) {
      sel.value = uiState.moonSelectValues[k];
    }
  }
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
  const st = $('spoolStats');
  if (title) title.textContent = `Box ${slotId[0]} · Slot ${slotId[1]}`;
  if (sub) sub.textContent = `${meta.material || '—'} · ${(meta.color || '').toUpperCase() || '—'}`;

  const startEl = $('spoolStart');
  const remEl = $('spoolRemain');
  // Prefill: use computed remaining if available (rounded), otherwise legacy remaining_g
  const prefRem = (meta.spool_remaining_g != null ? meta.spool_remaining_g : meta.remaining_g);
  if (remEl) remEl.value = (prefRem != null ? String(Math.round(Number(prefRem))) : '');
  // New roll input stays empty by default
  if (startEl) startEl.value = '';

  if (st) {
    const remG = (meta.spool_remaining_g != null ? meta.spool_remaining_g : meta.remaining_g);
    const usedG = meta.spool_used_g;
    const totalG = meta.spool_consumed_g;
    if (remG != null && usedG != null) {
      st.textContent = `Rest (berechnet): ${fmtG(remG)} · verbraucht seit Übernahme: ${fmtG(usedG)} · Gesamt (Slot): ${fmtG(totalG != null ? totalG : 0)}`;
    } else if (remG != null) {
      st.textContent = `Rest (aktuell): ${fmtG(remG)} · Tipp: "Istgewicht" eintragen und Übernehmen.`;
    } else {
      st.textContent = 'Noch kein Referenzwert. Trage "Istgewicht" ein und klicke Übernehmen.';
    }
  }

  m.style.display = 'block';
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
  const saveRemain = $('spoolSaveRemain');

  if (saveStart) {
    saveStart.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!spoolSlotId) return;
      const v = Number(($('spoolStart') || {}).value || 0);
      if (!Number.isFinite(v) || v <= 0) return;
      // Rollwechsel: new epoch + new reference
      await postJson('/api/ui/spool/set_start', { slot: spoolSlotId, start_g: v });
      closeSpoolModal();
      await tick();
    };
  }

  if (saveRemain) {
    saveRemain.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!spoolSlotId) return;
      const v = Number(($('spoolRemain') || {}).value || 0);
      if (!Number.isFinite(v) || v < 0) return;
      // Übernehmen: set measured remaining as reference (no epoch reset)
      await postJson('/api/ui/spool/set_remaining', { slot: spoolSlotId, remaining_g: v });
      closeSpoolModal();
      await tick();
    };
  }
}

function renderMoonHistory(state, connectedBoxes) {
  const wrap = $("moonHistory");
  if (!wrap) return;
  wrap.innerHTML = "";

  const hist = Array.isArray(state.moonraker_history) ? state.moonraker_history : [];
  if (!hist.length) {
    const empty = document.createElement("div");
    empty.className = "tag muted";
    empty.textContent = "Keine Moonraker-History Daten";
    wrap.appendChild(empty);
    return;
  }

  const slotIds = buildSlotIds(connectedBoxes);
  const allocStore = (state.moonraker_allocations && typeof state.moonraker_allocations === "object") ? state.moonraker_allocations : {};

  for (const e of hist.slice(0, 12)) {
    const key = jobKeyFromMoon(e);
    // If this job is already assigned locally, it should disappear from the
    // Moonraker list (it will show up under "Historie pro Slot").
    if (allocStore[key]) continue;

    const det = document.createElement("details");
    det.className = "moonEntry";
    det.dataset.key = key;

    const sum = document.createElement("summary");
    const row = document.createElement("div");
    row.className = "moonRow";

    const job = document.createElement("div");
    job.className = "moonJob";
    job.textContent = e.job || "(ohne name)";

    const nums = document.createElement("div");
    nums.className = "moonNums";
    const gTotal = (typeof e.filament_used_g_total === "number") ? e.filament_used_g_total : null;
    const mm = (typeof e.filament_used_mm === "number") ? e.filament_used_mm : null;
    // primary: grams (user relevant). fallback: meters.
    nums.textContent = gTotal != null ? fmtG(gTotal) : (mm != null ? fmtUsedFromMm(mm) : "—");

    row.appendChild(job);
    row.appendChild(nums);
    sum.appendChild(row);
    det.appendChild(sum);

    const sub = document.createElement("div");
    sub.className = "moonSub";

    const when = document.createElement("span");
    when.textContent = "🕒 " + fmtTs(e.ts_end || e.ts_start);
    sub.appendChild(when);

    const st = document.createElement("span");
    st.textContent = "📌 " + String(e.status || "");
    sub.appendChild(st);

    if (e.filament_type) {
      const ft = document.createElement("span");
      ft.textContent = "🧵 " + String(e.filament_type);
      sub.appendChild(ft);
    }

    // --- Slot assignment (local) ---
    const existing = null;

    const assign = document.createElement("div");
    assign.className = "assignWrap" + (existing ? " assigned" : "");

    const assignTitle = document.createElement("div");
    assignTitle.className = "assignTitle";
    assignTitle.textContent = existing ? "Zuordnung (lokal gespeichert)" : "Zu Slot zuordnen (lokal)";
    assign.appendChild(assignTitle);

    // When already assigned: keep UI clean, allow optional edit.
    const editBtn = document.createElement("button");
    editBtn.className = "btn mini";
    editBtn.type = "button";
    editBtn.textContent = existing ? "Ändern" : "";
    editBtn.style.display = existing ? "inline-flex" : "none";
    editBtn.onclick = () => {
      assign.classList.toggle("assigned");
    };
    assign.appendChild(editBtn);

    const cols = Array.isArray(e.colors) ? e.colors : [];
    const isMulti = Array.isArray(e.filament_used_g) && e.filament_used_g.length > 1;

    const rows = document.createElement("div");
    rows.className = "assignRows";

    const makeSelect = (pre, selKey) => {
      const sel = document.createElement("select");
      sel.className = "assignSel";
      if (selKey) sel.dataset.selkey = selKey;
      const opt0 = document.createElement("option");
      opt0.value = "";
      opt0.textContent = "— Slot wählen —";
      sel.appendChild(opt0);
      for (const sid of slotIds) {
        const o = document.createElement("option");
        o.value = sid;
        o.textContent = `Box ${sid[0]} · ${sid}`;
        sel.appendChild(o);
      }
      if (pre) sel.value = pre;
      return sel;
    };

    const perColor = [];
    if (Array.isArray(e.filament_used_g) && e.filament_used_g.length) {
      for (let i = 0; i < e.filament_used_g.length; i++) {
        const g = Number(e.filament_used_g[i] || 0);
        if (g <= 0) continue;
        const c = (cols[i] && typeof cols[i] === "string" && cols[i].startsWith("#")) ? cols[i].toUpperCase() : ("#" + String(i + 1));
        perColor.push({ color: c, g });
      }
    } else if (gTotal != null && gTotal > 0) {
      perColor.push({ color: "gesamt", g: Number(gTotal) });
    }

    if (!perColor.length) {
      const note = document.createElement("div");
      note.className = "tag muted";
      note.textContent = "Kein Verbrauch in History gefunden";
      assign.appendChild(note);
    } else {
      // Build UI rows
      let idx = 0;
      for (const it of perColor) {
        const r = document.createElement("div");
        r.className = "assignRow";

        const pill = document.createElement("span");
        pill.className = "miniPill";
        pill.textContent = `${it.color} · ${fmtG(it.g)}`;
        r.appendChild(pill);

        const sel = makeSelect("", `${key}:${idx}`);
        r.appendChild(sel);
        rows.appendChild(r);
        it._sel = sel;
        idx += 1;
      }

      assign.appendChild(rows);

      const actions = document.createElement("div");
      actions.className = "assignActions";
      const btn = document.createElement("button");
      btn.className = "btn";
      btn.textContent = existing ? "Zuordnung aktualisieren" : "Zuordnen";
      btn.onclick = async () => {
        try {
          const alloc = {};
          for (const it of perColor) {
            const sid = it._sel.value;
            if (!sid) continue;
            alloc[sid] = (alloc[sid] || 0) + Number(it.g || 0);
          }
          if (!Object.keys(alloc).length) {
            alert("Bitte mindestens einen Slot wählen.");
            return;
          }
          const payload = { job_key: key, job: e.job || "", ts: Number(e.ts_end || e.ts_start || 0), alloc_g: alloc };
          await postJson("/api/ui/moonraker/allocate", payload);
          // Force refresh
          await tick();
        } catch (err) {
          alert("Konnte nicht speichern: " + (err && err.message ? err.message : String(err)));
        }
      };
      actions.appendChild(btn);

      if (existing && typeof existing === "object") {
        const info = document.createElement("div");
        info.className = "tag";
        const parts = [];
        for (const [sid, g] of Object.entries(existing)) parts.push(`${sid}: ${fmtG(g)}`);
        info.textContent = "Aktuell: " + parts.join(" · ");
        actions.appendChild(info);
      }
      assign.appendChild(actions);
    }

    sub.appendChild(assign);

    det.appendChild(sub);
    wrap.appendChild(det);
  }
}

function renderHistory(state, slots, connectedBoxes) {
  const wrap = $("slotHistory");
  if (!wrap) return;
  wrap.innerHTML = "";

  const history = state.slot_history || {};
  const active = state.cfs_active_slot || state.active_slot || null;

  const slotIds = buildSlotIds(connectedBoxes);

  const metaFor = (sid) => {
    const m = (slots && slots[sid]) ? slots[sid] : {};
    const local = (state.slots && state.slots[sid]) ? state.slots[sid] : {};
    return {
      present: (m.present ?? local.present ?? true),
      material: ((m.material ?? local.material) || "").toString().toUpperCase(),
      color: ((m.color ?? m.color_hex ?? local.color ?? local.color_hex) || "").toString().toLowerCase(),
      remaining_g: (local.remaining_g ?? null),
      spool_remaining_g: (local.spool_remaining_g ?? null),
      spool_used_g: (local.spool_used_g ?? null),
      spool_consumed_g: (local.spool_consumed_g ?? null),
    };
  };

  for (const sid of slotIds) {
    const m = metaFor(sid);
    const epoch = Number(((state.slots || {})[sid] || {}).spool_epoch || 0);
    const rawEntries = Array.isArray(history[sid]) ? history[sid] : [];
    const entries = rawEntries.filter(e => Number((e || {}).epoch || 0) === epoch).slice(0,4);

    const card = document.createElement("div");
    card.className = "histSlot";

    const head = document.createElement("div");
    head.className = "histHead";

    const title = document.createElement("div");
    title.className = "histTitle";

    const sw = document.createElement("div");
    sw.className = "swatch";
    sw.style.width = "22px";
    sw.style.height = "22px";
    sw.style.background = m.color || "#2a3442";
    title.appendChild(sw);

    const nm = document.createElement("div");
    nm.className = "histSlotName";
    nm.textContent = `Box ${sid[0]} · Slot ${sid[1]}` + (sid === active ? " · aktiv" : "");
    title.appendChild(nm);

    head.appendChild(title);

    // totals
    let sumMm = 0;
    let sumG = 0;
    for (const e of entries) {
      sumMm += Number(e.used_mm || 0);
      sumG += Number(e.used_g || 0);
    }
    const meta = document.createElement("div");
    meta.className = "histMeta";
    // Primary: grams (this is what matters). Keep meters as detail in entry.
    meta.textContent = entries.length ? `${fmtG(sumG)}` : "—";
    head.appendChild(meta);
    card.appendChild(head);

    const list = document.createElement("div");
    list.className = "histList";

    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "tag muted";
      empty.textContent = "Noch keine Daten";
      list.appendChild(empty);
    } else {
      for (const e of entries) {
        const det = document.createElement("details");
        det.className = "histEntry";
        det.dataset.key = `${sid}:${String(e.ts || '')}:${String(e.job || '')}`;

        const sum = document.createElement("summary");
        const row = document.createElement("div");
        row.className = "histRow";

        const job = document.createElement("div");
        job.className = "histJob";
        job.textContent = (e.job || "(ohne name)");

        const nums = document.createElement("div");
        nums.className = "histNums";
        const mmTxt = Number(e.used_mm || 0) > 0 ? ` (${fmtMm(e.used_mm)})` : "";
        nums.textContent = `${fmtG(e.used_g)}${mmTxt}`;

        row.appendChild(job);
        row.appendChild(nums);
        sum.appendChild(row);
        det.appendChild(sum);

        const sub = document.createElement("div");
        sub.className = "histSub";
        const when = document.createElement("span");
        when.textContent = "🕒 " + fmtTs(e.ts);
        const res = document.createElement("span");
        res.textContent = "✅ " + String(e.result || "");
        const mat = document.createElement("span");
        mat.textContent = "🧵 " + (m.material || "—") + (m.color ? " " + m.color.toUpperCase() : "");
        sub.appendChild(when);
        sub.appendChild(mat);
        if (e.result) sub.appendChild(res);
        det.appendChild(sub);

        list.appendChild(det);
      }
    }

    card.appendChild(list);
    wrap.appendChild(card);
  }
}

function render(state) {
  const printerBadge = $("printerBadge");
  const cfsBadge = $("cfsBadge");

  const printerOk = !!state.printer_connected;
  badge(printerBadge, printerOk ? "Printer: verbunden" : "Printer: getrennt", printerOk ? "ok" : "bad");
  if (!printerOk && state.printer_last_error) {
    printerBadge.textContent += " (" + state.printer_last_error + ")";
  }

  const cfsOk = !!state.cfs_connected;
  badge(
    cfsBadge,
    cfsOk ? ("CFS: erkannt · " + fmtTs(state.cfs_last_update)) : "CFS: —",
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

      // spool fields (local bookkeeping)
      remaining_g: (local.remaining_g ?? null),
      spool_remaining_g: (local.spool_remaining_g ?? null),
      spool_used_g: (local.spool_used_g ?? null),
      spool_consumed_g: (local.spool_consumed_g ?? null),
      spool_epoch: (local.spool_epoch ?? null),
      spool_ref_remaining_g: (local.spool_ref_remaining_g ?? null),
      spool_ref_consumed_g: (local.spool_ref_consumed_g ?? null),
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

  // Right-side history panel
  renderHistory(state, slots, connectedBoxes);
  renderMoonHistory(state, connectedBoxes);

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

    // Live consumption while printing: use slot mm deltas (job_track_slot_mm)
    // and convert to grams using the current job's g/mm ratio (if available).
    const isPrinting = String(state.job_track_last_state || "").toLowerCase() === "printing";
    const slotMm = (state.job_track_slot_mm && typeof state.job_track_slot_mm === 'object') ? Number(state.job_track_slot_mm[active] || 0) : 0;
    const jobMm = Number(state.current_job_filament_mm || 0);
    const jobG = Number(state.current_job_filament_g || 0);
    const ratio = (jobMm > 0 && jobG > 0) ? (jobG / jobMm) : 0;

    const slotM = slotMm > 0 ? (slotMm / 1000) : 0;

    // Prefer backend-provided per-slot grams (robust for multi-color and firmware quirks)
    const slotG_direct = (state.job_track_slot_g && typeof state.job_track_slot_g === 'object') ? Number(state.job_track_slot_g[active] || 0) : 0;
    const slotG = (slotG_direct > 0) ? slotG_direct : ((ratio > 0 && slotMm > 0) ? (slotMm * ratio) : 0);

    if (activeLive && isPrinting && slotMm > 0) {
      const p1 = document.createElement('span');
      p1.className = 'pill';
      p1.textContent = `Live: ${slotM.toFixed(slotM < 10 ? 2 : 1)} m`;
      activeLive.appendChild(p1);
      if (slotG > 0) {
        const p2 = document.createElement('span');
        p2.className = 'pill';
        p2.textContent = `≈ ${slotG.toFixed(1)} g`;
        activeLive.appendChild(p2);
      }
      activeLive.style.display = 'flex';
    }
  } else {
    $("activeMeta").textContent = "—";
  }
}

async function tick() {
  try {
    // Preserve open accordions / select values so assignment doesn't collapse
    // during auto-refresh.
    captureUiState();
    const rightCol = document.querySelector('.rightCol');
    const scrollTop = rightCol ? rightCol.scrollTop : null;
    const r = await fetch("/api/ui/state", { cache: "no-store" });
    const j = await r.json();
    render(j.result || j);
    restoreUiState();
    if (rightCol && scrollTop != null) rightCol.scrollTop = scrollTop;
  } catch (e) {
    badge($("printerBadge"), "Printer: —", "warn");
    badge($("cfsBadge"), "CFS: —", "warn");
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
