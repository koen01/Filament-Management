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
    # spool_epoch: increments on roll-change; used for auto-unlink detection
    spool_epoch: int = 0
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
    active_slot: Optional[str] = None  # legacy; frontend uses cfs_active_slot
    auto_mode: bool = False
    slots: Dict[SlotId, SlotState]
    updated_at: float = Field(default_factory=lambda: time.time())

    # printer connection info
    printer_connected: bool = False
    printer_last_error: str = ""

    # CFS / AMS info (read-only from printer, optional)
    cfs_connected: bool = False
    cfs_last_update: float = 0.0
    cfs_active_slot: Optional[SlotId] = None
    cfs_slots: Dict[str, Any] = Field(default_factory=dict)

    # Per-slot cumulative usedMaterialLength (m) from last WS snapshot.
    # Used to compute Spoolman usage deltas between updates.
    ws_slot_length_m: Dict[str, float] = Field(default_factory=dict)

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


class SelectSlotRequest(BaseModel):
    slot: SlotId


class SetAutoRequest(BaseModel):
    enabled: bool


class FeedRequest(BaseModel):
    mm: float = Field(gt=0, le=200)


class RetractRequest(BaseModel):
    mm: float = Field(gt=0, le=200)


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


class UiSpoolSetStartRequest(BaseModel):
    slot: SlotId
    start_g: Optional[float] = None  # accepted for backward compat, not stored locally


class SpoolmanLinkRequest(BaseModel):
    slot: SlotId
    spoolman_id: int = Field(gt=0)


class SpoolmanUnlinkRequest(BaseModel):
    slot: SlotId
