from __future__ import annotations

from typing import Dict, Literal, Optional, Any
import time
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator

SlotId = Literal[
    "2A", "2B", "2C", "2D",
    "1A", "1B", "1C", "1D",
    "3A", "3B", "3C", "3D",
    "4A", "4B", "4C", "4D",
]

MaterialType = Literal["PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "OTHER"]


class SlotState(BaseModel):
    slot: SlotId
    material: MaterialType = "PLA"
    color_hex: str = Field(default="#00aaff", pattern=r"^#[0-9a-fA-F]{6}$")
    name: str = ""
    manufacturer: str = ""
    # Optional spool bookkeeping (purely local):
    # We store a *reference point* and compute remaining based on consumption
    # since that reference.
    #
    # - spool_ref_remaining_g: measured remaining weight at reference time
    # - spool_ref_consumed_g: total consumed for this slot at reference time
    # - spool_ref_set_at: unix timestamp when reference was set
    # - spool_epoch: increments on roll-change; UI shows only current epoch
    spool_ref_remaining_g: Optional[float] = None
    spool_ref_consumed_g: Optional[float] = None
    spool_ref_set_at: Optional[float] = None
    spool_epoch: int = 0

    # Running total of consumed grams for the *current* spool epoch.
    # This is used for remaining-weight calculations so that UI history trimming
    # ("letzte 4") never changes accounting.
    spool_epoch_consumed_g_total: float = 0.0

    # Legacy fields from older versions (kept for backward compatibility).
    # They are no longer used for calculations.
    spool_start_g: Optional[float] = None
    remaining_g: Optional[float] = None
    notes: str = ""
    spoolman_id: Optional[int] = None

    @field_validator("material", mode="before")
    @classmethod
    def normalize_material(cls, v: Any):
        """Be tolerant for older/hand-edited state.json files.

        - Old versions used placeholders like '-', '—', etc.
        - Users may type anything; unknown strings should not crash the app.
        """
        if v is None:
            return "OTHER"
        if isinstance(v, str):
            vv = v.strip().upper()
            if vv in ("", "-", "—", "–", "N/A", "NA", "NONE"):
                return "OTHER"
            if vv in ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "OTHER"):
                return vv
            return "OTHER"
        return "OTHER"


class AppState(BaseModel):
    active_slot: SlotId = "2A"
    auto_mode: bool = False
    slots: Dict[SlotId, SlotState]
    updated_at: float = Field(default_factory=lambda: time.time())

    # Optional informational fields (UI only)
    current_job: str = ""
    current_job_filament_mm: int = 0
    current_job_filament_g: float = 0.0

    # printer connection info (Moonraker)
    printer_connected: bool = False
    printer_last_error: str = ""

    # CFS / AMS info (read-only from printer, optional)
    cfs_connected: bool = False
    cfs_last_update: float = 0.0
    cfs_active_slot: Optional[SlotId] = None
    cfs_slots: Dict[str, Any] = Field(default_factory=dict)
    cfs_raw: Dict[str, Any] = Field(default_factory=dict)

    # Bookkeeping for clean spool deduction (persisted)
    last_accounted_job_mm: int = 0
    last_accounted_slot: Optional[SlotId] = None

    # --- Read-only history / usage tracking (persisted) ---
    # Per-slot print history (newest first). Each entry is a dict with:
    #   ts: unix timestamp (float)
    #   job: gcode filename
    #   used_mm: int
    #   used_g: float
    slot_history: Dict[str, Any] = Field(default_factory=dict)

    # Current job tracking to attribute filament to slots during a print.
    job_track_name: str = ""
    job_track_started_at: float = 0.0
    job_track_last_mm: int = 0
    job_track_slot_mm: Dict[str, int] = Field(default_factory=dict)
    job_track_slot_g: Dict[str, float] = Field(default_factory=dict)
    job_track_last_state: str = ""

    # --- Moonraker global history (read-only, best effort) ---
    # Snapshot of Moonraker's /server/history/list.  Moonraker history does not
    # reliably provide per-slot attribution on Creality CFS, so we display this
    # separately from the per-slot tracker.
    moonraker_history: Any = Field(default_factory=list)

    # --- Manual attribution for Moonraker history (local only) ---
    # Keyed by a stable job key (e.g. "<job_id>:<ts_end>") with value:
    #   {"job": str, "ts": float, "alloc_g": {"2A": 12.3, ...}}
    # This never talks back to the printer; it's only used to build per-slot history.
    moonraker_allocations: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, v: Any):
        # Accept float/int timestamps or ISO8601 strings.
        if v is None:
            return time.time()
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip()
            try:
                if s.endswith("Z"):
                    dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                return time.time()
        return time.time()


class UpdateSlotRequest(BaseModel):
    material: Optional[MaterialType] = None
    color_hex: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    spool_start_g: Optional[float] = None
    remaining_g: Optional[float] = None
    notes: Optional[str] = None


class SelectSlotRequest(BaseModel):
    slot: SlotId


class SetAutoRequest(BaseModel):
    enabled: bool


class SpoolResetRequest(BaseModel):
    slot: SlotId
    remaining_g: float


class SpoolApplyUsageRequest(BaseModel):
    slot: SlotId
    used_g: float


class FeedRequest(BaseModel):
    mm: float = Field(gt=0, le=200)


class RetractRequest(BaseModel):
    mm: float = Field(gt=0, le=200)


class JobSetRequest(BaseModel):
    name: str


class JobUpdateRequest(BaseModel):
    used_mm: int = Field(ge=0)
    slot: Optional[SlotId] = None


class MoonrakerAllocateRequest(BaseModel):
    """Assign a Moonraker history job (or its per-color parts) to CFS slots.

    This is purely local bookkeeping (no POST to printer).
    """

    job_key: str
    job: str
    ts: float
    alloc_g: Dict[SlotId, float]


# --- UI compatibility (the static UI talks to /api/ui/* and expects {"result": ...}) ---


class ApiResponse(BaseModel):
    result: dict


class UiSetColorRequest(BaseModel):
    slot: SlotId
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class UiSlotUpdateRequest(BaseModel):
    slot: SlotId
    material: Optional[MaterialType] = None
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    name: Optional[str] = None
    vendor: Optional[str] = None
    spool_start_g: Optional[float] = None
    remaining_g: Optional[float] = None
    notes: Optional[str] = None


class UiSpoolSetStartRequest(BaseModel):
    slot: SlotId
    start_g: float = Field(gt=0)


class UiSpoolSetRemainingRequest(BaseModel):
    slot: SlotId
    remaining_g: float = Field(ge=0)


class UiSlotResetRequest(BaseModel):
    slot: SlotId
    remaining_g: float


class SpoolmanLinkRequest(BaseModel):
    slot: SlotId
    spoolman_id: int = Field(gt=0)


class SpoolmanUnlinkRequest(BaseModel):
    slot: SlotId
