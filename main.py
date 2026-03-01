from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import urlparse

import websockets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from models.schemas import (
    ApiResponse,
    AppState,
    FeedRequest,
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
                    # Hostname or IP of the printer (used for WebSocket connection at ws://host:9999)
                    # Example: "192.168.178.148"
                    "printer_url": "",
                    # Filament diameter used for mm->g conversion
                    "filament_diameter_mm": 1.75,
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
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        cfg = {}

    # Backward compat: extract hostname from legacy moonraker_url if printer_url not set
    if not cfg.get("printer_url"):
        mu = (cfg.get("moonraker_url") or "").strip()
        if mu:
            host = urlparse(mu).hostname or ""
            if host:
                print(f"[CONFIG] Migrating moonraker_url → printer_url (host={host!r})")
                cfg["printer_url"] = host

    cfg.setdefault("printer_url", "")
    cfg.setdefault("filament_diameter_mm", 1.75)
    cfg.setdefault("spoolman_url", "")
    return cfg


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
    data.setdefault("ws_slot_length_m", {})

    # Clear the stale "2A" schema default — active_slot is now driven by WS only
    if data.get("active_slot") == "2A":
        data["active_slot"] = None

    return data


_state_load_failed: bool = False  # True when last load fell back to default

def load_state() -> AppState:
    global _state_load_failed
    _ensure_data_files()
    try:
        data = json.loads(STATE_PATH.read_text())
        data = _migrate_state_dict(data)
        result = _model_validate(AppState, data)
        _state_load_failed = False
        return result
    except Exception as e:
        # Corrupt/partial state files should never prevent the app from starting.
        print(f"[STATE] load failed: {e}")
        _state_load_failed = True
        return default_state()


def save_state(state: AppState) -> None:
    # Never overwrite real state with a fallback default — that destroys user data.
    if _state_load_failed:
        print("[STATE] save skipped: last load returned fallback default")
        return
    state.updated_at = _now()
    STATE_PATH.write_text(json.dumps(_model_dump(state), indent=2, ensure_ascii=False))



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


def _spoolman_set_extra(spool_id: int, key: str, value: str) -> None:
    """PATCH Spoolman spool to write a single extra field. Fire-and-forget."""
    base = _spoolman_base_url()
    if not base or not spool_id:
        return
    try:
        url = f"{base}/api/v1/spool/{spool_id}"
        # Spoolman requires extra field values to be JSON-encoded strings (double-encoded)
        data = json.dumps({"extra": {key: json.dumps(value)}}).encode("utf-8")
        req = UrlRequest(url, data=data, headers={
            "User-Agent": "filament-manager/1.0",
            "Content-Type": "application/json",
        }, method="PATCH")
        with urlopen(req, timeout=3.0) as r:
            r.read()
        print(f"[SPOOLMAN] set extra {key}={value!r} on spool {spool_id}")
    except Exception as e:
        print(f"[SPOOLMAN] set extra failed for spool {spool_id}: {e}")


def _spoolman_autolink_by_rfid(slot: str, rfid: str, st) -> None:
    """Search active Spoolman spools for one with extra.cfs_rfid == rfid and auto-link."""
    global _ws_last_rfid
    base = _spoolman_base_url()
    if not base or not rfid:
        return
    try:
        spools = _http_get_json(f"{base}/api/v1/spool?allow_archived=false", timeout=5.0)
        if not isinstance(spools, list):
            return
        for sp in spools:
            extra = sp.get("extra") or {}
            raw = extra.get("cfs_rfid", "")
            # Spoolman stores extra values as JSON-encoded strings — decode before comparing
            try:
                stored_rfid = json.loads(raw) if raw else ""
            except Exception:
                stored_rfid = raw
            if stored_rfid != rfid:
                continue
            spool_id = sp.get("id")
            if not spool_id:
                continue
            slot_state = st.slots.get(slot)
            if slot_state is None:
                return
            slot_state.spoolman_id = spool_id
            st.slots[slot] = slot_state
            # Record RFID as seen so we don't re-trigger next cycle
            _ws_last_rfid[slot] = rfid
            save_state(st)
            print(f"[SPOOLMAN] Auto-linked slot {slot} → spool {spool_id} via RFID {rfid!r}")
            return
    except Exception as e:
        print(f"[SPOOLMAN] auto-link lookup failed for slot {slot}: {e}")


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


_WS_SAVE_INTERVAL = 10.0
_ws_last_save: float = 0.0
_ws_last_rfid: Dict[str, str] = {}  # slot → last seen RFID code

_moon_last_state: str = ""  # last known print_stats.state from Moonraker
_moon_job_start_lengths: Dict[str, float] = {}  # ws_slot_length_m snapshot at job start

_VALID_SLOT_IDS = frozenset(
    f"{b}{l}" for b in "1234" for l in "ABCD"
)

# Log unknown WS message top-level keys once per session to aid discovery
_ws_seen_keys: set = set()

# Spoolman-derived percent cache for manual (non-RFID) slots
_spoolman_manual_pct: Dict[str, Optional[int]] = {}  # slot → percent or None
_spoolman_pct_refresh_at: Dict[str, float] = {}      # slot → next refresh timestamp
_SPOOLMAN_PCT_TTL = 60.0

# Known WS key names for printer identity (tried in order)
_WS_NAME_KEYS = ("machineName", "printerName", "deviceName", "model", "MachineModel", "deviceModel")
_WS_FW_KEYS   = ("softVersion", "firmwareVersion", "version", "FirmwareVersion", "SoftwareVersion", "firmware")


def _printer_ws_url() -> str:
    cfg = load_config()
    host = (cfg.get("printer_url") or "").strip()
    if not host:
        mu = (cfg.get("moonraker_url") or "").strip()
        if mu:
            host = urlparse(mu).hostname or ""
    if not host:
        return ""
    return f"ws://{host.split(':')[0]}:9999"


def _moonraker_base_url() -> str:
    """Return the Moonraker HTTP base URL (port 7125), or empty string if not configured."""
    cfg = load_config()
    mu = (cfg.get("moonraker_url") or "").strip()
    if mu:
        parsed = urlparse(mu)
        host = parsed.hostname or ""
        port = parsed.port or 7125
        return f"http://{host}:{port}"
    host = (cfg.get("printer_url") or "").strip().split(":")[0]
    return f"http://{host}:7125" if host else ""


def _normalize_ws_color(raw: str) -> str:
    """Strip leading zero after '#' from Creality color format '#0RRGGBB' → '#RRGGBB'."""
    s = (raw or "").lstrip("#")
    if len(s) == 7 and s[0] == "0":
        return "#" + s[1:].lower()
    if len(s) == 6:
        return "#" + s.lower()
    return raw


def _parse_ws_printer_info(payload: dict) -> None:
    """Extract printer name / firmware from any WS status message and persist to state.

    Also logs any previously-unseen top-level keys once per session so we can
    discover the exact field names the printer uses.
    """
    global _ws_seen_keys
    new_keys = set(payload.keys()) - _ws_seen_keys
    if new_keys:
        _ws_seen_keys |= new_keys
        print(f"[WS] New message keys: {sorted(new_keys)}")

    name = ""
    for k in _WS_NAME_KEYS:
        v = str(payload.get(k) or "").strip()
        if v:
            name = v
            break

    fw = ""
    for k in _WS_FW_KEYS:
        v = str(payload.get(k) or "").strip()
        if v:
            fw = v
            break

    if not name and not fw:
        return

    st = load_state()
    changed = False
    if name and name != st.printer_name:
        st.printer_name = name
        changed = True
        print(f"[WS] Printer name: {name!r}")
    if fw and fw != st.printer_firmware:
        st.printer_firmware = fw
        changed = True
        print(f"[WS] Firmware: {fw!r}")
    if changed:
        save_state(st)


def _parse_ws_cfs_data(payload: dict) -> None:
    """Parse a boxsInfo WS payload and update local state + Spoolman."""
    global _ws_last_save
    try:
        boxes = (payload.get("boxsInfo") or {}).get("materialBoxs") or []
    except Exception:
        return

    st = load_state()
    active_slot: Optional[str] = None
    boxes_meta: dict = {}

    for box in boxes:
        if not isinstance(box, dict):
            continue
        if box.get("type") != 0:
            continue  # skip spool holders (type 1)
        box_id = box.get("id")
        if not isinstance(box_id, int) or box_id < 1 or box_id > 4:
            continue

        boxes_meta[str(box_id)] = {
            "connected": True,
            "temperature_c": float(box["temp"]) if isinstance(box.get("temp"), (int, float)) else None,
            "humidity_pct": float(box["humidity"]) if isinstance(box.get("humidity"), (int, float)) else None,
        }

        for mat in (box.get("materials") or []):
            if not isinstance(mat, dict):
                continue
            mat_id = mat.get("id")
            if not isinstance(mat_id, int) or mat_id < 0 or mat_id > 3:
                continue

            slot = f"{box_id}{'ABCD'[mat_id]}"
            if slot not in _VALID_SLOT_IDS:
                continue

            state_val = int(mat.get("state") or 0)
            selected = int(mat.get("selected") or 0)

            # state 2 = RFID: WS percent is real sensor data → use it
            # state 1 = manual: WS always reports 100 (no sensor) → use Spoolman cache
            # state 0 = empty: no percent
            if state_val == 2:
                pct = mat.get("percent")
            elif state_val == 1:
                pct = _spoolman_manual_pct.get(slot)  # None until async refresh fills it
            else:
                pct = None

            st.cfs_slots[slot] = {
                "percent": pct,
                "state": state_val,
                "rfid": mat.get("rfid", ""),
                "selected": selected,
                "present": state_val > 0,
            }

            if selected == 1:
                active_slot = slot

            # Update local slot metadata from CFS data (only if spool is physically present)
            if state_val > 0 and slot in st.slots:
                slot_obj = st.slots[slot]
                raw_color = mat.get("color", "")
                col = _normalize_ws_color(raw_color)
                if col and len(col) == 7 and col.startswith("#"):
                    slot_obj.color_hex = col
                mat_type = (mat.get("type") or "").strip().upper()
                if mat_type:
                    slot_obj.material = mat_type  # type: ignore[assignment]
                name = (mat.get("name") or "").strip()
                if name:
                    slot_obj.name = name
                vendor = (mat.get("vendor") or "").strip()
                if vendor:
                    slot_obj.manufacturer = vendor
                st.slots[slot] = slot_obj

            # RFID-based auto-link: react to any RFID change on this slot
            rfid = mat.get("rfid", "")
            if rfid and state_val == 2:  # state 2 = RFID-tagged spool
                prev_rfid = _ws_last_rfid.get(slot, "")
                if rfid != prev_rfid:
                    _ws_last_rfid[slot] = rfid
                    slot_obj2 = st.slots.get(slot)
                    if slot_obj2:
                        if getattr(slot_obj2, "spoolman_id", None):
                            # RFID changed on a linked slot — implicit spool swap
                            slot_obj2.spoolman_id = None
                            st.slots[slot] = slot_obj2
                            st.ws_slot_length_m.pop(slot, None)  # reset baseline
                        _spoolman_autolink_by_rfid(slot, rfid, st)

            # Track cumulative length for per-job Moonraker attribution
            cur_m = float(mat.get("usedMaterialLength") or 0)
            st.ws_slot_length_m[slot] = cur_m

    # Store box connection metadata so the frontend can show correct boxes
    if boxes_meta:
        st.cfs_slots["_boxes"] = boxes_meta

    # Always update active slot — clears stale value when printer is idle
    st.cfs_active_slot = active_slot
    if active_slot and active_slot in st.slots:
        st.active_slot = active_slot

    st.cfs_connected = True
    st.cfs_last_update = _now()
    st.printer_connected = True
    st.printer_last_error = ""

    now = _now()
    if now - _ws_last_save >= _WS_SAVE_INTERVAL:
        save_state(st)
        _ws_last_save = now


async def _refresh_manual_slot_pcts() -> None:
    """Calculate Spoolman-based percent for manual (non-RFID) slots and cache it.

    Called after each boxsInfo parse. Uses a per-slot TTL so Spoolman is queried
    at most once per _SPOOLMAN_PCT_TTL seconds per slot.
    """
    base = _spoolman_base_url()
    if not base:
        return
    st = load_state()
    now = _now()
    loop = asyncio.get_running_loop()

    for slot, cfs_meta in list(st.cfs_slots.items()):
        if not isinstance(cfs_meta, dict) or cfs_meta.get("state") != 1:
            continue
        slot_obj = st.slots.get(slot)
        spool_id = getattr(slot_obj, "spoolman_id", None) if slot_obj else None
        if not spool_id:
            _spoolman_manual_pct.pop(slot, None)
            continue
        if _spoolman_pct_refresh_at.get(slot, 0) > now:
            continue  # still fresh

        try:
            sp = await loop.run_in_executor(None, _spoolman_get_spool, base, spool_id)
            filament = sp.get("filament") or {}
            nominal_g = float(filament.get("weight") or 0)
            remaining_g = float(sp.get("remaining_weight") or 0)
            used_g = float(sp.get("used_weight") or 0)
            if nominal_g > 0:
                pct: Optional[int] = max(0, min(100, int(round(remaining_g / nominal_g * 100))))
            elif remaining_g + used_g > 0:
                pct = max(0, min(100, int(round(remaining_g / (remaining_g + used_g) * 100))))
            else:
                pct = None
            _spoolman_manual_pct[slot] = pct
            _spoolman_pct_refresh_at[slot] = now + _SPOOLMAN_PCT_TTL
            print(f"[SPOOLMAN] Slot {slot} manual percent: {pct}%")
        except Exception:
            _spoolman_pct_refresh_at[slot] = now + 10.0  # back off on error


async def _ws_connect_and_run(ws_url: str) -> None:
    """Open one WebSocket connection to the printer and run the polling loop."""
    async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None) as ws:
        # Skip only the very first burst (max 5 messages, 0.15 s each).
        # We keep this minimal — real CFS data arrives immediately and we must not lose it.
        for _ in range(5):
            try:
                await asyncio.wait_for(ws.recv(), timeout=0.15)
            except asyncio.TimeoutError:
                break

        # Heartbeat handshake. The printer may push status frames before "ok",
        # so scan up to 10 messages instead of assuming the very next one is the ack.
        await ws.send(json.dumps({"ModeCode": "heart_beat"}))
        for _ in range(10):
            try:
                reply = await asyncio.wait_for(ws.recv(), timeout=2.0)
                if str(reply).strip() == "ok":
                    break
            except asyncio.TimeoutError:
                break

        st = load_state()
        st.printer_connected = True
        st.printer_last_error = ""
        save_state(st)
        print(f"[WS] Connected to {ws_url}")

        # Request initial CFS data immediately after handshake
        await ws.send(json.dumps({"method": "get", "params": {"boxsInfo": 1}}))
        _last_request: float = asyncio.get_event_loop().time()

        # Continuous message loop — process everything the printer sends.
        # Never assume the next recv() is the response to our request; the printer
        # pushes status frames continuously between our request and its reply.
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=6.0)
            except asyncio.TimeoutError:
                # Printer went silent — re-request and wait again
                await ws.send(json.dumps({"method": "get", "params": {"boxsInfo": 1}}))
                _last_request = asyncio.get_event_loop().time()
                continue

            # Printer heartbeat ping — ack it immediately
            if isinstance(msg, str) and "heart_beat" in msg:
                await ws.send("ok")
                continue

            # Plain "ok" is the printer acking our heartbeat — nothing to do
            if isinstance(msg, str) and msg.strip() == "ok":
                continue

            try:
                data = json.loads(msg)
                _parse_ws_printer_info(data)
                if "boxsInfo" in data:
                    _parse_ws_cfs_data(data)
                    asyncio.create_task(_refresh_manual_slot_pcts())
            except Exception:
                pass

            # Re-request every 5 s so we keep receiving fresh pushes
            now = asyncio.get_event_loop().time()
            if now - _last_request >= 5.0:
                await ws.send(json.dumps({"method": "get", "params": {"boxsInfo": 1}}))
                _last_request = now


async def printer_ws_loop() -> None:
    """Outer reconnect loop for the printer WebSocket connection."""
    ws_url = _printer_ws_url()
    if not ws_url:
        print("[WS] No printer_url configured — WebSocket loop not started.")
        return

    print(f"[WS] Starting WebSocket loop for {ws_url}")
    backoff = 2.0

    while True:
        last_err = ""
        try:
            await _ws_connect_and_run(ws_url)
            backoff = 2.0  # reset on clean exit
        except Exception as e:
            last_err = str(e)
            print(f"[WS] Connection lost: {e}")

        try:
            st = load_state()
            st.printer_connected = False
            st.cfs_connected = False
            st.printer_last_error = last_err
            save_state(st)
        except Exception:
            pass

        print(f"[WS] Reconnecting in {backoff:.0f}s…")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _moon_report_job_usage(filament_mm: float) -> None:
    """Attribute Moonraker's filament_used proportionally across slots using WS deltas."""
    global _moon_job_start_lengths
    st = load_state()
    slot_deltas: Dict[str, float] = {}
    for slot, cur_m in st.ws_slot_length_m.items():
        start_m = _moon_job_start_lengths.get(slot, cur_m)
        delta = cur_m - start_m
        if delta > 0.01:
            slot_deltas[slot] = delta
    total_delta_m = sum(slot_deltas.values())
    if not slot_deltas or total_delta_m <= 0:
        print(f"[MOON] Job complete: {filament_mm:.0f}mm used, no WS slot deltas to attribute")
        _moon_job_start_lengths = {}
        return
    for slot, delta_m in slot_deltas.items():
        slot_obj = st.slots.get(slot)
        if not slot_obj:
            continue
        spool_id = getattr(slot_obj, "spoolman_id", None)
        if not spool_id:
            continue
        proportion = delta_m / total_delta_m
        mat_str = str(getattr(slot_obj, "material", "OTHER") or "OTHER")
        g = mm_to_g(mat_str, filament_mm * proportion)
        if g > 0:
            _spoolman_report_usage(spool_id, g)
            print(f"[MOON] Slot {slot}: {g:.2f}g ({proportion * 100:.0f}% of job)")
    _moon_job_start_lengths = {}


async def moonraker_job_poll_loop() -> None:
    """Poll Moonraker print_stats every 5s and attribute filament usage at job completion."""
    global _moon_last_state, _moon_job_start_lengths

    base = _moonraker_base_url()
    if not base:
        print("[MOON] No printer URL configured — job poll loop not started.")
        return

    print(f"[MOON] Starting job poll loop against {base}")

    _ACTIVE_STATES = {"printing", "paused"}
    _ENDED_STATES = {"complete", "error", "cancelled", "standby"}

    while True:
        await asyncio.sleep(5.0)
        try:
            url = f"{base}/printer/objects/query?print_stats"
            data = _http_get_json(url, timeout=5.0)
            ps = (data.get("result") or {}).get("status", {}).get("print_stats") or {}
            new_state = str(ps.get("state") or "").lower()
            filament_used_mm = float(ps.get("filament_used") or 0)

            prev = _moon_last_state
            if new_state == prev:
                continue

            _moon_last_state = new_state
            print(f"[MOON] State: {prev!r} → {new_state!r}")

            if new_state in _ACTIVE_STATES and prev not in _ACTIVE_STATES:
                # Job started — snapshot current ws_slot_length_m
                st = load_state()
                _moon_job_start_lengths = dict(st.ws_slot_length_m)
                print(f"[MOON] Job started; snapshotted {len(_moon_job_start_lengths)} slot lengths")

            elif new_state == "complete" and prev in _ACTIVE_STATES:
                print(f"[MOON] Job complete: {filament_used_mm:.0f}mm filament used")
                _moon_report_job_usage(filament_used_mm)

            elif new_state in {"error", "cancelled"} and prev in _ACTIVE_STATES:
                print(f"[MOON] Job {new_state} — skipping usage report")
                _moon_job_start_lengths = {}

        except Exception as e:
            # Network errors are expected when printer is off — don't log verbosely
            pass


app = FastAPI(title="CFSync", version="0.1.1")


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
    asyncio.create_task(printer_ws_loop())
    asyncio.create_task(moonraker_job_poll_loop())


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# --- Public API ---
@app.get("/api/state", response_model=AppState)
def api_state():
    return load_state()



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

    d.setdefault("printer_connected", False)
    d.setdefault("printer_last_error", "")
    d.setdefault("cfs_connected", False)
    d.setdefault("cfs_last_update", 0.0)
    d.setdefault("cfs_active_slot", None)
    d.setdefault("cfs_slots", {})
    d["spoolman_configured"] = bool(_spoolman_base_url())

    return d


# --- UI API (static frontend uses /api/ui/* and expects {"result": ...}) ---
@app.get("/api/ui/state", response_model=ApiResponse)
def api_ui_state() -> ApiResponse:
    return ApiResponse(result=_ui_state_dict(load_state()))


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
    # Reset WS length baseline so next snapshot doesn't trigger a false delta
    state.ws_slot_length_m.pop(slot, None)
    # Clear RFID cache so re-inserting any spool triggers auto-link again
    _ws_last_rfid.pop(slot, None)
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

    # Write the slot's CFS RFID to the Spoolman spool's extra field for future auto-linking
    rfid = (state.cfs_slots.get(slot) or {}).get("rfid", "")
    if rfid:
        _spoolman_set_extra(req.spoolman_id, "cfs_rfid", rfid)
        _ws_last_rfid[slot] = rfid  # mark as seen so auto-link doesn't re-trigger this cycle

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
            "Set printer_url in data/config.json to your printer's IP to enable live CFS slot sync via WebSocket.\n"
            "Link a Spoolman spool to a slot to track filament consumption automatically."
        )
    else:
        text = (
            "Klick einen Slot, um ihn aktiv zu setzen.\n"
            "Trage printer_url in data/config.json mit der IP deines Druckers ein, um die CFS-Slots per WebSocket zu synchronisieren.\n"
            "Verknüpfe einen Spoolman-Spool mit einem Slot, um den Filamentverbrauch automatisch zu verfolgen."
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
        printer_connected=False,
        printer_last_error="",
        cfs_connected=False,
        cfs_last_update=0.0,
        cfs_active_slot=None,
        cfs_slots={},
    )
