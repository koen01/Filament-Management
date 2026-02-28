from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from models.schemas import (
    ApiResponse,
    AppState,
    FeedRequest,
    JobSetRequest,
    JobUpdateRequest,
    MoonrakerAllocateRequest,
    RetractRequest,
    SelectSlotRequest,
    SetAutoRequest,
    SlotState,
    SpoolmanLinkRequest,
    SpoolmanUnlinkRequest,
    UiSetColorRequest,
    UiSpoolSetStartRequest,
    UiSlotUpdateRequest,
    UpdateSlotRequest,
)


# ---- Pydantic v1/v2 compatibility helpers ----

def _model_dump(obj) -> dict:
    """Return a plain dict for both Pydantic v1 and v2 models."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()


def _model_validate(cls, data):
    """Validate/parse a dict into a Pydantic model (v1/v2 compatible)."""
    if hasattr(cls, "model_validate"):
        return cls.model_validate(data)
    return cls.parse_obj(data)


def _req_dump(obj, *, exclude_unset: bool = False) -> dict:
    """Dump request models (v1/v2 compatible) with optional exclude_unset."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_unset=exclude_unset)
    return obj.dict(exclude_unset=exclude_unset)


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
STATE_PATH = DATA_DIR / "state.json"
PROFILES_PATH = DATA_DIR / "profiles.json"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_SLOTS = [
    "1A", "1B", "1C", "1D",
    "2A", "2B", "2C", "2D",
    "3A", "3B", "3C", "3D",
    "4A", "4B", "4C", "4D",
]


def _now() -> float:
    return time.time()


def _parse_iso_ts(val: str) -> Optional[float]:
    try:
        # Accept "Z" and timezone offsets
        if val.endswith("Z"):
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _ensure_data_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    if not PROFILES_PATH.exists():
        PROFILES_PATH.write_text(
            json.dumps(
                {
                    "PLA": {"density_g_cm3": 1.24, "notes": "Default profile"},
                    "ABS": {"density_g_cm3": 1.04, "notes": "Default profile"},
                    "PETG": {"density_g_cm3": 1.27, "notes": "Default profile"},
                    "TPU": {"density_g_cm3": 1.20, "notes": "Default profile"},
                    "ASA": {"density_g_cm3": 1.07, "notes": "Default profile"},
                    "PA": {"density_g_cm3": 1.15, "notes": "Default profile"},
                    "PC": {"density_g_cm3": 1.20, "notes": "Default profile"},
                    "OTHER": {"density_g_cm3": 1.20, "notes": "Fallback"},
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    # Optional: set this to enable automatic job usage reading from Moonraker
                    # Example: "http://192.168.178.148:7125"
                    "moonraker_url": "",
                    "poll_interval_sec": 5,
                    # Filament diameter used for mm->g conversion
                    "filament_diameter_mm": 1.75,
                    # If true, import material/color/name from detected CFS objects into local slots (read-only to printer)
                    "cfs_autosync": False,
                    # Optional: Spoolman URL for spool inventory integration
                    # Example: "http://192.168.178.148:7912"
                    "spoolman_url": "",
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    if not STATE_PATH.exists():
        slots: Dict[str, dict] = {}
        for s in DEFAULT_SLOTS:
            slots[s] = _model_dump(SlotState(slot=s))
        state = {
            "active_slot": "2A",
            "auto_mode": False,
            "slots": slots,
            "current_job": "",
            "current_job_filament_mm": 0,
            "current_job_filament_g": 0.0,
            # in-flight job attribution (persisted so a restart doesn't lose the active print)
            "job_track_name": "",
            "job_track_started_at": 0.0,
            "job_track_last_mm": 0,
            "job_track_slot_mm": {},
            "job_track_slot_g": {},
            "job_track_last_state": "",
            # snapshot from Moonraker history (global list)
            "moonraker_history": [],
            # idempotency markers for Moonraker history -> Spoolman sync
            "moonraker_allocations": {},
            "updated_at": _now(),
        }
        STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def load_profiles() -> dict:
    _ensure_data_files()
    try:
        return json.loads(PROFILES_PATH.read_text())
    except Exception:
        return {}


def load_config() -> dict:
    _ensure_data_files()
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {
            "moonraker_url": "",
            "poll_interval_sec": 5,
            "filament_diameter_mm": 1.75,
            "cfs_autosync": False,
            "spoolman_url": "",
        }


def _migrate_state_dict(data: dict) -> dict:
    """Make state.json tolerant to older/hand-edited formats."""
    if not isinstance(data, dict):
        return data

    # updated_at: allow ISO string
    if isinstance(data.get("updated_at"), str):
        ts = _parse_iso_ts(data["updated_at"])
        if ts is not None:
            data["updated_at"] = ts

    # Some users wrote last_update instead of updated_at
    if "updated_at" not in data and "last_update" in data:
        if data["last_update"] is None:
            data["updated_at"] = 0.0
        elif isinstance(data["last_update"], str):
            data["updated_at"] = _parse_iso_ts(data["last_update"]) or 0.0
        else:
            try:
                data["updated_at"] = float(data["last_update"])
            except Exception:
                data["updated_at"] = 0.0

    # Ensure new fields exist
    data.setdefault("current_job", data.get("job", {}).get("name", ""))
    data.setdefault("current_job_filament_mm", int(data.get("job", {}).get("used_mm", 0) or 0))
    data.setdefault("current_job_filament_g", float(data.get("job", {}).get("used_g", 0.0) or 0.0))

    # Slots: allow keys like "2A": {material,color,...} without slot field
    slots = data.get("slots", {}) or {}
    if isinstance(slots, dict):
        for slot_id, sd in list(slots.items()):
            if not isinstance(sd, dict):
                continue
            sd.setdefault("slot", slot_id)
            # allow 'color' key
            if "color" in sd and "color_hex" not in sd:
                sd["color_hex"] = sd.pop("color")
            # legacy key 'vendor' -> 'manufacturer'
            if "vendor" in sd and "manufacturer" not in sd:
                sd["manufacturer"] = sd.pop("vendor")
            # tolerate placeholders for material
            mat = sd.get("material")
            if isinstance(mat, str) and mat.strip() in ("", "-", "—", "–"):
                sd["material"] = "OTHER"
            # Spoolman integration (optional)
            sd.setdefault("spoolman_id", None)
            slots[slot_id] = sd
        # ensure all CFS banks exist (1A-4D)
        for sid in (
            "1A", "1B", "1C", "1D",
            "2A", "2B", "2C", "2D",
            "3A", "3B", "3C", "3D",
            "4A", "4B", "4C", "4D",
        ):
            if sid not in slots:
                slots[sid] = {
                    "slot": sid,
                    "material": "OTHER",
                    "color_hex": "#00aaff",
                    "name": "",
                    "manufacturer": "",
                }
        data["slots"] = slots

    data.setdefault("printer_connected", False)
    data.setdefault("printer_last_error", "")

    data.setdefault("cfs_connected", False)
    data.setdefault("cfs_last_update", 0.0)
    data.setdefault("cfs_active_slot", None)
    data.setdefault("cfs_slots", {})
    data.setdefault("cfs_raw", {})

    data.setdefault("job_track_name", "")
    data.setdefault("job_track_started_at", 0.0)
    data.setdefault("job_track_last_mm", 0)
    data.setdefault("job_track_slot_mm", {})
    data.setdefault("job_track_slot_g", {})
    data.setdefault("job_track_last_state", "")

    # Moonraker history snapshot
    data.setdefault("moonraker_history", [])
    data.setdefault("moonraker_allocations", {})

    return data


def load_state() -> AppState:
    _ensure_data_files()
    try:
        data = json.loads(STATE_PATH.read_text())
        data = _migrate_state_dict(data)
        return _model_validate(AppState, data)
    except Exception as e:
        # Corrupt/partial state files should never prevent the app from starting.
        print(f"[STATE] load failed: {e}")
        return default_state()


def _job_key(job_id: str, ts_end: Optional[float], job: str) -> str:
    """Build a stable key for a job in our local allocation store."""
    j = (job_id or "").strip() or (job or "").strip()
    try:
        te = float(ts_end) if ts_end is not None else 0.0
    except Exception:
        te = 0.0
    return f"{j}:{te:.0f}"


def save_state(state: AppState) -> None:
    state.updated_at = _now()
    STATE_PATH.write_text(json.dumps(_model_dump(state), indent=2, ensure_ascii=False))


# --- Printer adapter (Dummy) ---
# Keep it minimal: this project is about material management.
# You can later replace these functions with real Moonraker/CFS actions.

def adapter_feed(mm: float) -> None:
    print(f"[ADAPTER] feed {mm}mm")


def adapter_retract(mm: float) -> None:
    print(f"[ADAPTER] retract {mm}mm")


# --- Conversion helpers ---

def mm_to_g(material: str, mm: float) -> float:
    cfg = load_config()
    d_mm = float(cfg.get("filament_diameter_mm", 1.75) or 1.75)
    profiles = load_profiles()
    density = float((profiles.get(material) or {}).get("density_g_cm3", 1.20))

    # grams = density(g/cm^3) * volume(cm^3)
    # volume = area * length
    # area(mm^2) = pi*(d/2)^2 ; to cm^2 => /100
    # length(mm) to cm => /10
    area_cm2 = math.pi * (d_mm / 2.0) ** 2 / 100.0
    length_cm = mm / 10.0
    g = density * area_cm2 * length_cm
    return float(max(0.0, g))



# --- Minimal Moonraker polling (optional) ---

def _http_get_json(url: str, timeout: float = 2.5) -> dict:
    # NOTE: FastAPI also exports a Request type; avoid name clash by using
    # UrlRequest for outbound HTTP requests.
    req = UrlRequest(url, headers={"User-Agent": "filament-manager/1.0"})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _http_put_json(url: str, body: dict, timeout: float = 3.0) -> dict:
    """PUT JSON body and return parsed response (stdlib only)."""
    data = json.dumps(body).encode("utf-8")
    req = UrlRequest(url, data=data, headers={
        "User-Agent": "filament-manager/1.0",
        "Content-Type": "application/json",
    }, method="PUT")
    with urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


# --- Spoolman integration (optional) ---

def _spoolman_base_url() -> str:
    """Return the configured Spoolman base URL, or empty string if not set."""
    cfg = load_config()
    return (cfg.get("spoolman_url") or "").rstrip("/")


def _spoolman_get_spools(base: str) -> list[dict]:
    """GET /api/v1/spool — return non-archived spools."""
    url = base + "/api/v1/spool"
    spools = _http_get_json(url, timeout=5.0)
    if not isinstance(spools, list):
        return []
    return [s for s in spools if not s.get("archived", False)]


def _spoolman_get_spool(base: str, spool_id: int) -> dict:
    """GET /api/v1/spool/{id} — return single spool."""
    url = f"{base}/api/v1/spool/{spool_id}"
    return _http_get_json(url, timeout=5.0)


def _spoolman_report_usage(spool_id: int, grams: float) -> None:
    """PUT /api/v1/spool/{id}/use — fire-and-forget."""
    if not spool_id or grams <= 0:
        return
    base = _spoolman_base_url()
    if not base:
        return
    try:
        url = f"{base}/api/v1/spool/{spool_id}/use"
        _http_put_json(url, {"use_weight": round(grams, 2)})
        print(f"[SPOOLMAN] reported usage: spool {spool_id} -= {grams:.2f}g")
    except Exception as e:
        print(f"[SPOOLMAN] usage report failed for spool {spool_id}: {e}")


def _spoolman_report_measure(spool_id: int, weight_g: float) -> None:
    """PUT /api/v1/spool/{id} — set remaining_weight directly. Fire-and-forget."""
    if not spool_id:
        return
    base = _spoolman_base_url()
    if not base:
        return
    try:
        url = f"{base}/api/v1/spool/{spool_id}"
        data = json.dumps({"remaining_weight": round(weight_g, 2)}).encode("utf-8")
        req = UrlRequest(url, data=data, headers={
            "User-Agent": "filament-manager/1.0",
            "Content-Type": "application/json",
        }, method="PATCH")
        with urlopen(req, timeout=3.0) as r:
            r.read()
        print(f"[SPOOLMAN] reported measure: spool {spool_id} = {weight_g:.2f}g")
    except Exception as e:
        print(f"[SPOOLMAN] measure report failed for spool {spool_id}: {e}")


def _color_distance(hex1: str, hex2: str) -> float:
    """Simple Euclidean RGB distance between two hex colors."""
    try:
        h1 = hex1.lstrip("#")
        h2 = hex2.lstrip("#")
        r1, g1, b1 = int(h1[0:2], 16), int(h1[2:4], 16), int(h1[4:6], 16)
        r2, g2, b2 = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        return math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)
    except Exception:
        return 999.0


def _moonraker_fetch_history(base: str, limit: int = 20) -> list[dict]:
    """Fetch Moonraker job history list (best effort).

    Moonraker provides this at:
      GET /server/history/list?limit=<n>&order=desc
    Note: Creality firmware usually exposes the history component, but
    per-slot attribution is not guaranteed.
    """
    try:
        url = base.rstrip("/") + "/server/history/list?" + urlencode({"limit": int(limit), "order": "desc"})
        data = _http_get_json(url, timeout=3.5)
        jobs = (((data or {}).get("result") or {}).get("jobs") or [])
        out: list[dict] = []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            fn = j.get("filename") or ""
            if isinstance(fn, str) and "/" in fn:
                fn = fn.rsplit("/", 1)[-1]
            # Moonraker reports filament_used as float; documentation says mm,
            # however some frontends treat it as meters. We keep both a raw
            # value and a derived mm estimate.
            fu = j.get("filament_used")
            fu_raw = None
            fu_mm = None
            try:
                fu_raw = float(fu)
                # Heuristic: if the value is small (< 200) it's likely meters.
                # Otherwise treat it as mm.
                fu_mm = fu_raw * 1000.0 if fu_raw < 200 else fu_raw
            except Exception:
                pass

            meta = j.get("metadata") or {}
            fu_g_list = None
            try:
                lst = meta.get("filament_used_g")
                if isinstance(lst, list) and lst:
                    fu_g_list = [float(x) for x in lst]
            except Exception:
                fu_g_list = None

            # If firmware didn't provide grams, compute a best-effort estimate from mm + filament_type
            fu_g_total = None
            try:
                if isinstance(fu_g_list, list) and fu_g_list:
                    fu_g_total = float(sum(fu_g_list))
                elif fu_mm is not None:
                    mat = None
                    if isinstance(meta, dict):
                        mat = meta.get("filament_type")
                    mat_s = str(mat).strip().upper() if mat else "OTHER"
                    fu_g_total = float(mm_to_g(mat_s, float(fu_mm)))
            except Exception:
                fu_g_total = None

            out.append(
                {
                    "job_id": j.get("job_id") or j.get("uid") or "",
                    "ts_start": j.get("start_time"),
                    "ts_end": j.get("end_time"),
                    "status": j.get("status") or "",
                    "job": fn,
                    "filament_used_raw": fu_raw,
                    "filament_used_mm": fu_mm,
                    "filament_used_g": fu_g_list,
                    "filament_used_g_total": (float(round(fu_g_total, 2)) if fu_g_total is not None else None),
                    "filament_type": (meta.get("filament_type") if isinstance(meta, dict) else None),
                    "colors": (meta.get("default_filament_colour") if isinstance(meta, dict) else None),
                }
            )
        return out
    except Exception:
        return []

def _moonraker_build_url(base: str, objects: list[str]) -> str:
    """Build Moonraker objects/query URL.

    Moonraker supports multiple syntaxes depending on version/vendor fork.
    Creality K-series (K2 Plus) reliably supports the ampersand form:
      /printer/objects/query?print_stats&virtual_sdcard&box&filament_rack

    Some upstream versions also accept `objects=toolhead,print_stats`, but that
    isn't consistently supported on Creality firmware. For maximum compatibility
    we use the ampersand form.
    """
    safe = [str(o).strip() for o in (objects or []) if str(o).strip()]
    qs = "&".join(safe)
    return base.rstrip("/") + "/printer/objects/query?" + qs


def _moonraker_list_objects(base: str) -> list[str]:
    data = _http_get_json(base.rstrip("/") + "/printer/objects/list")
    return list((((data or {}).get("result") or {}).get("objects") or []))


def _walk(obj, path=""):
    # generator over (path, value) for nested dict/list
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, v
            yield from _walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{path}[{i}]"
            yield p, v
            yield from _walk(v, p)


_SLOT_RE = __import__("re").compile(r"^[1-4][A-D]$")


def _extract_cfs_slot_data(status: dict) -> tuple[Optional[str], dict]:
    """Best-effort extraction of CFS slot metadata from Moonraker status.

    Creality's firmware is not standardized, so we try heuristics:
    - Any dict key that looks like '1A', '2D', ... is treated as a slot.
    - Any nested dict with fields like slot/id/index and color/material/name.
    Returns (active_slot, slots_dict).
    """
    active = None
    slots: dict[str, dict] = {}

    # --- Creality K-series "box" + "filament_rack" objects (K2 Plus / CFS) ---
    # Firmware exposes:
    #   box.T1..T4 with arrays: color_value/material_type/remain_len, and box.<Tn>.filament = "A".."D"
    #   filament_rack.remain_material_color/type
    # We normalize to internal slot ids: "1A".."4D".
    try:
        box = (status or {}).get("box")
        rack = (status or {}).get("filament_rack")
        if isinstance(box, dict):
            # Build lookups from box.same_material: [material_code, color_code, ["T2D"], "ABS"]
            mat_name_by_code: dict[str, str] = {}
            sm = box.get("same_material")
            if isinstance(sm, list):
                for row in sm:
                    if not isinstance(row, list) or len(row) < 4:
                        continue
                    mcode, _ccode, _slots_list, mname = row[0], row[1], row[2], row[3]
                    if isinstance(mcode, str) and isinstance(mname, str):
                        mat_name_by_code[mcode] = mname.strip().upper()

            def _hex_color(creality_val: str) -> Optional[str]:
                if not isinstance(creality_val, str):
                    return None
                v = creality_val.strip().lower()
                # values look like "0ffa800" or "00a2989"; take last 6 hex chars
                hex6 = v[-6:]
                if len(hex6) == 6 and all(ch in "0123456789abcdef" for ch in hex6):
                    return f"#{hex6}".lower()
                return None

            boxes: dict[str, dict] = {}

            for ti in ("T1", "T2", "T3", "T4"):
                t = box.get(ti)
                if not isinstance(t, dict):
                    continue

                # Box connection state: "connect" when a CFS is present.
                bnum = str(ti[1])
                bstate = str(t.get("state") or "")
                is_conn = (bstate.lower() == "connect")
                boxes[bnum] = {
                    "connected": is_conn,
                    "state": bstate,
                    # Best-effort environmental info per CFS box (Creality)
                    "temperature_c": None,
                    "humidity_pct": None,
                }

                # Temperature / humidity are often strings like "32" and "31"
                try:
                    tval = t.get("temperature")
                    hval = t.get("dry_and_humidity")
                    if tval is not None and str(tval).strip().lower() != "none":
                        boxes[bnum]["temperature_c"] = float(str(tval).strip())
                    if hval is not None and str(hval).strip().lower() != "none":
                        boxes[bnum]["humidity_pct"] = float(str(hval).strip())
                except Exception:
                    pass

                # If the box isn't connected, mark its slots as not present and continue.
                if not is_conn:
                    for letter in ("A", "B", "C", "D"):
                        sid = f"{bnum}{letter}"
                        slots[sid] = {"present": False}
                    continue
                colors = t.get("color_value")
                mats = t.get("material_type")
                if not (isinstance(colors, list) and isinstance(mats, list)):
                    continue

                for idx, letter in enumerate(("A", "B", "C", "D")):
                    sid = f"{ti[1]}{letter}"  # "1A".."4D"
                    raw_color = colors[idx] if idx < len(colors) else None
                    raw_mat = mats[idx] if idx < len(mats) else None
                    out: dict = {"present": True}

                    # Creality uses "-1" to signal an empty slot
                    if isinstance(raw_mat, str) and raw_mat.strip() == "-1":
                        slots[sid] = {"present": False, "material": "", "color": ""}
                        continue

                    col = _hex_color(str(raw_color)) if raw_color is not None else None
                    if col:
                        out["color"] = col
                    if isinstance(raw_mat, str):
                        out["material"] = mat_name_by_code.get(raw_mat, raw_mat).strip().upper()

                    slots[sid] = out

                fil = t.get("filament")
                if isinstance(fil, str) and fil in ("A", "B", "C", "D"):
                    active = f"{ti[1]}{fil}"

            if active is None and isinstance(rack, dict):
                rc = rack.get("remain_material_color")
                rt = rack.get("remain_material_type")
                rc_hex = _hex_color(str(rc)) if rc is not None else None
                rt_norm = mat_name_by_code.get(rt, rt).strip().upper() if isinstance(rt, str) else None
                if rc_hex and rt_norm:
                    for sid, meta in slots.items():
                        if meta.get("color") == rc_hex and meta.get("material") == rt_norm:
                            active = sid
                            break

            if slots:
                mp = box.get("map")
                if isinstance(mp, dict):
                    slots["_map"] = {"raw": mp}
                # Add box connection metadata for the frontend
                if boxes:
                    slots["_boxes"] = boxes
                return active, slots
    except Exception:
        pass

    # 1) Direct keys
    for k, v in (status or {}).items():
        if isinstance(k, str) and _SLOT_RE.match(k) and isinstance(v, dict):
            slots[k] = v

    # 2) Walk nested structures to find slot-like dicts
    for p, v in _walk(status or {}):
        if not isinstance(v, dict):
            continue
        # Active slot hints
        for ak in ("active_slot", "current_slot", "slot", "cfs_slot", "ams_slot"):
            if ak in v and isinstance(v[ak], str) and _SLOT_RE.match(v[ak]):
                active = v[ak]
        # Slot dictionaries keyed by slot id
        if any(key in p.lower() for key in ("cfs", "ams", "mmu", "filament", "spool")):
            for kk, vv in v.items():
                if isinstance(kk, str) and _SLOT_RE.match(kk) and isinstance(vv, dict):
                    slots.setdefault(kk, vv)

    # Normalize fields we care about
    norm: dict[str, dict] = {}
    for sid, raw in slots.items():
        if not isinstance(raw, dict):
            continue
        out = {}
        # presence / loaded flags
        for pk in ("present", "loaded", "has_filament", "is_loaded", "enabled"):
            if pk in raw and isinstance(raw[pk], (bool, int)):
                out["present"] = bool(raw[pk])
                break
        # material
        for mk in ("material", "type", "filament_type"):
            if mk in raw and isinstance(raw[mk], str):
                out["material"] = raw[mk].strip().upper()
                break
        # color
        for ck in ("color", "color_hex", "colour", "rgb"):
            if ck in raw:
                out["color"] = raw[ck]
                break
        # name/vendor
        for nk in ("name", "label", "spool_name"):
            if nk in raw and isinstance(raw[nk], str):
                out["name"] = raw[nk]
                break
        for vk in ("vendor", "manufacturer", "brand"):
            if vk in raw and isinstance(raw[vk], str):
                out["manufacturer"] = raw[vk]
                break

        norm[sid] = out or {"raw": raw}

    return active, norm




async def moonraker_poll_loop() -> None:
    cfg = load_config()
    base = (cfg.get("moonraker_url") or "").strip()
    if not base:
        return

    interval = float(cfg.get("poll_interval_sec", 5) or 5)
    if interval < 1:
        interval = 1

    # Always query job usage
    base_objects = ["print_stats", "virtual_sdcard"]

    # Best-effort: discover CFS-related objects once, then include them in polling.
    cfs_objects: list[str] = []
    try:
        objs = await asyncio.to_thread(_moonraker_list_objects, base)
        for o in objs:
            lo = str(o).lower()
            if any(x in lo for x in ("cfs", "ams", "mmu", "spool", "filament_box", "filamentbox")):
                cfs_objects.append(str(o))
            # Creality K-series / K2 Plus objects
            if lo in ("box", "filament_rack"):
                cfs_objects.append(str(o))
        # Keep the poll URL reasonably short
        cfs_objects = cfs_objects[:12]
    except Exception:
        cfs_objects = []

    poll_objects = base_objects + cfs_objects
    url = _moonraker_build_url(base, poll_objects)

    # Optional: if enabled, we import material/color/name from CFS objects into our local slots.
    cfs_autosync = bool(cfg.get("cfs_autosync", False))

    # Pull Moonraker's global history occasionally (read-only).
    last_hist_fetch = 0.0
    hist_every_sec = 60.0

    while True:
        try:
            data = await asyncio.to_thread(_http_get_json, url)
            status = (((data or {}).get("result") or {}).get("status") or {})
            ps = status.get("print_stats") or {}
            vsd = status.get("virtual_sdcard") or {}

            ps_state = str(ps.get("state") or "").lower()

            filename = ps.get("filename") or vsd.get("file_path") or ""
            if isinstance(filename, str) and "/" in filename:
                filename = filename.rsplit("/", 1)[-1]
            used = ps.get("filament_used")
            if used is None:
                used_mm = 0
            else:
                used_mm = int(float(used))

            used_g = 0.0
            try:
                meta = ((vsd.get("cur_print_data") or {}).get("metadata") or {})
                lst = meta.get("filament_used_g")
                if isinstance(lst, list) and lst:
                    used_g = float(sum(float(x) for x in lst if x is not None))
            except Exception:
                used_g = 0.0

            st = load_state()
            st.printer_connected = True
            st.printer_last_error = ""

            # --- CFS read-only extraction (best effort) ---
            cfs_status = {k: v for k, v in (status or {}).items() if k not in ("print_stats", "virtual_sdcard")}
            if cfs_status:
                active_slot, slots_meta = _extract_cfs_slot_data(cfs_status)
                st.cfs_connected = True
                st.cfs_last_update = _now()
                st.cfs_active_slot = active_slot
                st.cfs_slots = slots_meta
                # store a small raw snapshot for debugging in the UI
                st.cfs_raw = {k: cfs_status[k] for k in list(cfs_status)[:4]}

                # If the printer reports an active slot, we can reflect it locally (no POST to printer)
                if active_slot and active_slot in st.slots:
                    st.active_slot = active_slot

                # Optional: import metadata into local slots (still read-only to printer)
                if cfs_autosync and slots_meta:
                    for sid, meta in slots_meta.items():
                        if sid not in st.slots:
                            continue
                        s = st.slots[sid]
                        mat = meta.get("material")
                        if isinstance(mat, str) and mat.strip():
                            # unknown material will be normalized to OTHER by schema
                            s.material = mat.strip().upper()  # type: ignore
                        col = meta.get("color")
                        if isinstance(col, str) and col.startswith("#") and len(col) == 7:
                            s.color_hex = col.lower()
                        name = meta.get("name")
                        if isinstance(name, str):
                            s.name = name
                        mfg = meta.get("manufacturer")
                        if isinstance(mfg, str):
                            s.manufacturer = mfg
                        st.slots[sid] = s
            else:
                st.cfs_connected = False

            # --- Per-slot filament tracking ---
            # Attribute delta filament_used(mm) to the active slot during a print.
            # Accumulated grams per slot are reported to Spoolman at print finalize.
            try:
                is_printing = ps_state in ("printing", "paused")
                tracking = bool(st.job_track_name)
                curr_slot = (st.cfs_active_slot or st.active_slot or "").strip()

                # Start tracking when a print begins
                if is_printing and filename:
                    if (not tracking) or (st.job_track_name != filename):
                        st.job_track_name = filename
                        st.job_track_started_at = _now()
                        st.job_track_last_mm = 0
                        st.job_track_slot_mm = {}
                        st.job_track_slot_g = {}
                        st.job_track_last_state = ps_state

                    # Attribute delta to current slot
                    last_mm = int(st.job_track_last_mm or 0)
                    delta_mm = max(0, int(used_mm) - last_mm)
                    if delta_mm > 0 and curr_slot:
                        st.job_track_slot_mm[curr_slot] = int(st.job_track_slot_mm.get(curr_slot, 0)) + int(delta_mm)

                        # Convert delta_mm to grams for this slot's material and track it
                        try:
                            mat = st.slots.get(curr_slot).material if curr_slot in st.slots else "OTHER"
                            g_delta = float(mm_to_g(str(mat), float(delta_mm)))
                        except Exception:
                            g_delta = 0.0
                        if g_delta > 0:
                            st.job_track_slot_g[curr_slot] = float(st.job_track_slot_g.get(curr_slot, 0.0)) + float(g_delta)
                    st.job_track_last_mm = int(used_mm)
                    st.job_track_last_state = ps_state

                # Finalize when printing ends (complete/cancel/error/standby)
                if (not is_printing) and tracking and st.job_track_name:
                    # Report per-slot consumption to Spoolman
                    slot_g = st.job_track_slot_g if isinstance(st.job_track_slot_g, dict) else {}
                    for sid, g in slot_g.items():
                        try:
                            gv = float(g)
                            if gv <= 0:
                                continue
                            slot_obj = st.slots.get(sid)
                            if slot_obj and getattr(slot_obj, "spoolman_id", None):
                                _spoolman_report_usage(slot_obj.spoolman_id, gv)
                        except Exception:
                            continue

                    # Reset tracking
                    st.job_track_name = ""
                    st.job_track_started_at = 0.0
                    st.job_track_last_mm = 0
                    st.job_track_slot_mm = {}
                    st.job_track_slot_g = {}
                    st.job_track_last_state = ps_state
            except Exception:
                pass

            # --- Job usage accounting ---
            if filename or used_mm:
                st.current_job = filename or st.current_job or ""
                st.current_job_filament_mm = int(max(0, used_mm))
                if used_g > 0.0:
                    st.current_job_filament_g = float(round(used_g, 2))
                else:
                    try:
                        mat = (st.slots.get(st.active_slot) or SlotState(slot=st.active_slot)).material
                    except Exception:
                        mat = "OTHER"
                    st.current_job_filament_g = mm_to_g(mat, float(max(0, used_mm)))

            # --- Moonraker history snapshot (global) ---
            # This is useful to show past jobs even if our per-slot tracker
            # wasn't running.  It won't reliably attribute usage to CFS slots,
            # so the UI shows it separately.
            try:
                now = _now()
                if (now - last_hist_fetch) >= hist_every_sec:
                    hist = await asyncio.to_thread(_moonraker_fetch_history, base, 20)
                    if hist:
                        st.moonraker_history = hist
                    last_hist_fetch = now
            except Exception:
                pass

            save_state(st)
        except Exception as e:
            st = load_state()
            st.printer_connected = False
            st.printer_last_error = str(e)
            st.updated_at = time.time()
            save_state(st)

        await asyncio.sleep(interval)



app = FastAPI(title="3D Printer Filament Manager", version="0.1.1")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Disable browser caching for /static assets.

    This project is frequently updated in-place on the host. Some browsers keep
    serving an older /static/app.js via 304 responses unless caching is
    explicitly disabled. Prevent that.
    """
    response = await call_next(request)
    path = request.url.path or ""
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Static UI on /
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _startup():
    _ensure_data_files()
    cfg = load_config()
    if (cfg.get("moonraker_url") or "").strip():
        asyncio.create_task(moonraker_poll_loop())


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# --- Public API ---
@app.get("/api/state", response_model=AppState)
def api_state():
    return load_state()


@app.post("/api/moonraker/allocate", response_model=AppState)
def api_moonraker_allocate(req: MoonrakerAllocateRequest):
    """Store local per-slot allocation for a Moonraker history job and sync to Spoolman.

    Idempotent: if the same key was already allocated, returns early to prevent
    double-syncing to Spoolman.
    """
    st = load_state()
    key = (req.job_key or "").strip() or _job_key(req.job_key, req.ts, req.job)

    # Idempotency guard: don't double-sync to Spoolman
    if key in st.moonraker_allocations:
        return st

    # Normalize alloc_g: drop zeros/negatives
    alloc: Dict[str, float] = {}
    for sid, g in (req.alloc_g or {}).items():
        try:
            gv = float(g)
            if gv > 0:
                alloc[str(sid)] = float(round(gv, 2))
        except Exception:
            continue

    if not alloc:
        raise HTTPException(status_code=400, detail="alloc_g must contain at least one positive value")

    # Persist allocation marker (no alloc_g stored — Spoolman owns the running total)
    st.moonraker_allocations[key] = {"job": req.job, "ts": float(req.ts)}

    # Sync consumption to Spoolman per slot
    for sid, g in alloc.items():
        try:
            slot_obj = st.slots.get(sid)
            if slot_obj and getattr(slot_obj, "spoolman_id", None) and float(g) > 0:
                _spoolman_report_usage(slot_obj.spoolman_id, float(g))
        except Exception:
            pass

    save_state(st)
    return st


def _ui_state_dict(state: AppState) -> dict:
    """Convert internal AppState to the UI payload the static frontend expects."""
    d = _model_dump(state)
    slots_in = d.get("slots", {}) or {}
    slots_out: Dict[str, dict] = {}
    for slot_id, sd in slots_in.items():
        if not isinstance(sd, dict):
            sd = _model_dump(sd)
        out = dict(sd)
        if "color_hex" in out and "color" not in out:
            out["color"] = out.pop("color_hex")
        if "manufacturer" in out and "vendor" not in out:
            out["vendor"] = out.get("manufacturer", "")

        slots_out[slot_id] = out
    d["slots"] = slots_out

    # UI expects job info as flat fields
    d.setdefault("current_job", "")
    d.setdefault("current_job_filament_mm", 0)
    d.setdefault("current_job_filament_g", 0.0)

    # printer connection info for header badge
    d.setdefault("printer_connected", False)
    d.setdefault("printer_last_error", "")

    d.setdefault("cfs_connected", False)
    d.setdefault("cfs_last_update", 0.0)
    d.setdefault("cfs_active_slot", None)
    d.setdefault("cfs_slots", {})
    d.setdefault("cfs_raw", {})

    d["spoolman_configured"] = bool(_spoolman_base_url())

    return d


# --- UI API (static frontend uses /api/ui/* and expects {"result": ...}) ---
@app.get("/api/ui/state", response_model=ApiResponse)
def api_ui_state() -> ApiResponse:
    return ApiResponse(result=_ui_state_dict(load_state()))


@app.post("/api/ui/moonraker/allocate", response_model=ApiResponse)
def api_ui_moonraker_allocate(req: MoonrakerAllocateRequest) -> ApiResponse:
    st = api_moonraker_allocate(req)
    return ApiResponse(result=_ui_state_dict(st))


@app.post("/api/select_slot", response_model=AppState)
def api_select_slot(req: SelectSlotRequest):
    state = load_state()
    if req.slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")
    state.active_slot = req.slot
    save_state(state)
    return state


@app.post("/api/ui/select_slot", response_model=ApiResponse)
def api_ui_select_slot(req: SelectSlotRequest) -> ApiResponse:
    state = api_select_slot(req)
    return ApiResponse(result=_ui_state_dict(state))


@app.post("/api/set_auto", response_model=AppState)
def api_set_auto(req: SetAutoRequest):
    state = load_state()
    state.auto_mode = bool(req.enabled)
    save_state(state)
    return state


@app.post("/api/ui/set_auto", response_model=ApiResponse)
def api_ui_set_auto(req: SetAutoRequest) -> ApiResponse:
    state = api_set_auto(req)
    return ApiResponse(result=_ui_state_dict(state))


@app.patch("/api/slots/{slot}", response_model=AppState)
def api_update_slot(slot: str, req: UpdateSlotRequest):
    state = load_state()
    if slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")

    s = state.slots[slot]
    update = _req_dump(req, exclude_unset=True)
    for k, v in update.items():
        if hasattr(s, k):
            setattr(s, k, v)

    state.slots[slot] = s
    save_state(state)
    return state


@app.post("/api/ui/slot/update", response_model=ApiResponse)
def api_ui_slot_update(req: UiSlotUpdateRequest) -> ApiResponse:
    state = load_state()
    slot = req.slot
    if slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")

    s = state.slots[slot]
    upd = _req_dump(req, exclude_unset=True)

    # UI uses 'color' but internal uses 'color_hex'
    if "color" in upd:
        s.color_hex = upd.pop("color")

    upd.pop("slot", None)

    # vendor -> manufacturer
    if "vendor" in upd and upd.get("vendor") is not None:
        upd["manufacturer"] = upd.pop("vendor")

    for k, v in upd.items():
        if v is None:
            continue
        if hasattr(s, k):
            setattr(s, k, v)

    state.slots[slot] = s
    save_state(state)
    return ApiResponse(result=_ui_state_dict(state))



@app.post("/api/ui/spool/set_start", response_model=ApiResponse)
def api_ui_spool_set_start(req: UiSpoolSetStartRequest) -> ApiResponse:
    """Roll change: increment epoch and auto-unlink Spoolman spool."""
    state = load_state()
    slot = req.slot
    if slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")

    s = state.slots[slot]
    # New roll => new epoch (hides old history in Spoolman status, triggers auto-unlink)
    try:
        s.spool_epoch = int(getattr(s, "spool_epoch", 0) or 0) + 1
    except Exception:
        s.spool_epoch = 1
    # Roll change auto-unlinks Spoolman spool
    s.spoolman_id = None
    state.slots[slot] = s
    save_state(state)
    return ApiResponse(result=_ui_state_dict(state))



# --- Spoolman integration endpoints ---

@app.get("/api/ui/spoolman/spools")
def api_ui_spoolman_spools(slot: str = "1A"):
    """Fetch available Spoolman spools, sorted by match quality for the given slot."""
    base = _spoolman_base_url()
    if not base:
        raise HTTPException(status_code=400, detail="Spoolman URL not configured")

    state = load_state()
    s = state.slots.get(slot)
    slot_material = (getattr(s, "material", "PLA") or "PLA").upper() if s else "PLA"
    slot_color = (getattr(s, "color_hex", "") or "").lower() if s else ""

    try:
        raw = _spoolman_get_spools(base)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Spoolman unreachable: {e}")

    spools = []
    for sp in raw:
        filament = sp.get("filament") or {}
        mat = (filament.get("material") or "").upper()
        color_hex = (filament.get("color_hex") or "").lower()
        name = filament.get("name") or ""
        vendor = (filament.get("vendor") or {}).get("name", "")
        remaining = sp.get("remaining_weight")

        # Score: lower is better. Same material gets a big bonus.
        score = 0
        if mat == slot_material:
            score -= 1000
        if slot_color and color_hex:
            score += _color_distance(slot_color, color_hex)

        spools.append({
            "id": sp.get("id"),
            "filament_name": name,
            "vendor": vendor,
            "material": mat,
            "color_hex": color_hex,
            "remaining_weight": remaining,
            "_score": score,
        })

    spools.sort(key=lambda x: x["_score"])
    for sp in spools:
        del sp["_score"]

    return {"spools": spools, "slot": slot}


@app.post("/api/ui/spoolman/link", response_model=ApiResponse)
def api_ui_spoolman_link(req: SpoolmanLinkRequest) -> ApiResponse:
    """Link a Spoolman spool to a CFS slot. Imports remaining_weight as local reference."""
    base = _spoolman_base_url()
    if not base:
        raise HTTPException(status_code=400, detail="Spoolman URL not configured")

    state = load_state()
    slot = req.slot
    if slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")

    try:
        sp = _spoolman_get_spool(base, req.spoolman_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Spoolman unreachable: {e}")

    filament = sp.get("filament") or {}

    s = state.slots[slot]
    s.spoolman_id = req.spoolman_id

    # Import spool metadata from Spoolman
    mat_raw = (filament.get("material") or "").strip().upper()
    if mat_raw in ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC"):
        s.material = mat_raw
    color_hex = (filament.get("color_hex") or "").strip()
    if color_hex and len(color_hex) == 7 and color_hex.startswith("#"):
        s.color_hex = color_hex
    fname = (filament.get("name") or "").strip()
    if fname:
        s.name = fname
    vendor_name = ((filament.get("vendor") or {}).get("name") or "").strip()
    if vendor_name:
        s.manufacturer = vendor_name

    state.slots[slot] = s
    save_state(state)
    return ApiResponse(result=_ui_state_dict(state))


@app.post("/api/ui/spoolman/unlink", response_model=ApiResponse)
def api_ui_spoolman_unlink(req: SpoolmanUnlinkRequest) -> ApiResponse:
    """Clear Spoolman link on a slot. Local tracking is unaffected."""
    state = load_state()
    slot = req.slot
    if slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")

    state.slots[slot].spoolman_id = None
    save_state(state)
    return ApiResponse(result=_ui_state_dict(state))


@app.get("/api/ui/spoolman/spool_detail")
def api_ui_spoolman_spool_detail(slot: str = "1A"):
    """Proxy Spoolman spool status for a given CFS slot.

    Returns {"linked": bool, "slot": str, "spool": dict|null, "error": str|null}.
    Never raises HTTP 502 — Spoolman unavailability is returned as a structured error
    so the frontend can degrade gracefully.
    """
    state = load_state()
    slot_obj = state.slots.get(slot)
    if slot_obj is None:
        raise HTTPException(status_code=404, detail="Unknown slot")

    spool_id = getattr(slot_obj, "spoolman_id", None)
    if not spool_id:
        return {"linked": False, "slot": slot, "spool": None, "error": None}

    base = _spoolman_base_url()
    if not base:
        return {"linked": True, "slot": slot, "spool": None, "error": "not_configured"}

    try:
        sp = _spoolman_get_spool(base, spool_id)
        return {"linked": True, "slot": slot, "spool": sp, "error": None}
    except Exception as e:
        return {"linked": True, "slot": slot, "spool": None, "error": "unreachable"}


@app.post("/api/ui/set_color", response_model=ApiResponse)
def api_ui_set_color(req: UiSetColorRequest) -> ApiResponse:
    state = load_state()
    if req.slot not in state.slots:
        raise HTTPException(status_code=404, detail="Unknown slot")
    state.slots[req.slot].color_hex = req.color
    save_state(state)
    return ApiResponse(result=_ui_state_dict(state))



@app.post("/api/job/set", response_model=AppState)
def api_job_set(req: JobSetRequest):
    state = load_state()
    state.current_job = req.name
    state.current_job_filament_mm = 0
    state.current_job_filament_g = 0.0
    save_state(state)
    return state


@app.post("/api/ui/job/set", response_model=ApiResponse)
def api_ui_job_set(req: JobSetRequest) -> ApiResponse:
    state = api_job_set(req)
    return ApiResponse(result=_ui_state_dict(state))


@app.post("/api/job/update", response_model=AppState)
def api_job_update(req: JobUpdateRequest):
    state = load_state()
    slot_id = req.slot or state.active_slot
    total_mm = int(max(0, req.used_mm))
    try:
        mat = (state.slots.get(slot_id) or SlotState(slot=slot_id)).material
    except Exception:
        mat = "OTHER"
    state.current_job_filament_mm = total_mm
    state.current_job_filament_g = mm_to_g(mat, float(total_mm))
    save_state(state)
    return state


@app.post("/api/ui/job/update", response_model=ApiResponse)
def api_ui_job_update(req: JobUpdateRequest) -> ApiResponse:
    state = api_job_update(req)
    return ApiResponse(result=_ui_state_dict(state))


@app.post("/api/feed")
def api_feed(req: FeedRequest):
    adapter_feed(req.mm)
    return {"ok": True}


@app.post("/api/ui/feed", response_model=ApiResponse)
def api_ui_feed(req: FeedRequest) -> ApiResponse:
    api_feed(req)
    return ApiResponse(result={"ok": True})


@app.post("/api/retract")
def api_retract(req: RetractRequest):
    adapter_retract(req.mm)
    return {"ok": True}


@app.post("/api/ui/retract", response_model=ApiResponse)
def api_ui_retract(req: RetractRequest) -> ApiResponse:
    api_retract(req)
    return ApiResponse(result={"ok": True})


@app.get("/api/ui/help", response_model=ApiResponse)
def api_ui_help(lang: str = "de") -> ApiResponse:
    if lang == "en":
        text = (
            "Click a slot to set it as active.\n"
            "Use the color presets to set the color on the active slot.\n"
            "Feed/Retract are currently adapter hooks (dummy) until real hardware is connected.\n"
            "Job consumption: If you use Moonraker, set moonraker_url in data/config.json — job + filament_used will be picked up automatically.\n"
            "Alternatively you can use /api/ui/job/update manually."
        )
    else:
        text = (
            "Klick einen Slot, um ihn aktiv zu setzen.\n"
            "Mit den Farb-Presets setzt du die Farbe auf den aktiven Slot.\n"
            "Zuführ/Zurückziehen sind aktuell Adapter-Hooks (Dummy), bis wir echte Hardware anbinden.\n"
            "Job-Verbrauch: Wenn du Moonraker nutzt, trage moonraker_url in data/config.json ein, dann wird der Job + filament_used automatisch übernommen.\n"
            "Alternativ kannst du manuell /api/ui/job/update nutzen."
        )
    return ApiResponse(result={"text": text})


# Health
@app.get("/api/health")
def api_health():
    return {"ok": True, "ts": _now()}



def default_state() -> AppState:
    """Safe defaults if state.json is missing/broken.

    Must always include all 4x4 CFS slots so the UI never crashes, even if the
    state file is corrupted.
    """
    slots: Dict[str, SlotState] = {}
    for sid in DEFAULT_SLOTS:
        slots[sid] = SlotState(slot=sid, material="OTHER", color_hex="#00aaff")

    # Sensible demo defaults for Box 2 (matches the UI screenshot vibe)
    slots["2A"].material = "ABS"
    slots["2A"].color_hex = "#4b0082"  # indigo-ish

    return AppState(
        active_slot="2A",
        auto_mode=False,
        updated_at=_now(),
        slots=slots,  # type: ignore[arg-type]
        current_job="",
        current_job_filament_mm=0,
        current_job_filament_g=0.0,
        printer_connected=False,
        printer_last_error="",
        cfs_connected=False,
        cfs_last_update=0.0,
        cfs_active_slot=None,
        cfs_slots={},
        cfs_raw={},
    )
