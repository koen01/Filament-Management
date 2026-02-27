# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Filament-Management is a local web application for tracking 3D printer filament/spool usage, built for Creality K2 Plus CFS (4x4 slot grid) and Klipper/Moonraker-based printers. It runs as a FastAPI backend with a vanilla JavaScript SPA frontend. The UI supports German and English via `static/i18n.js` (auto-detects browser language, persists choice in localStorage).

## Development Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run development server (with hot-reload)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Health check
curl http://localhost:8000/api/health
```

There are no automated tests, linting tools, or CI/CD pipelines configured.

## Architecture

**Backend:** Single-file FastAPI app (`main.py`, ~1500 lines) with Pydantic models in `models/schemas.py`. Data is persisted as JSON files in `data/` (state.json, config.json, profiles.json) — no database.

**Frontend:** Vanilla JS SPA in `static/` (index.html, app.js, app.css, style.css). No build step, no framework — pure DOM manipulation.

**Moonraker integration:** Optional async background polling loop that queries the printer's Moonraker API for print job status, filament usage, and CFS slot info. Includes Creality K2 Plus-specific object parsing (box.T1-T4, filament_rack).

## Key Patterns

- **Pydantic v1/v2 compatibility:** Helper functions `_model_dump()`, `_model_validate()`, `_req_dump()` abstract over version differences. Always use these instead of calling `.dict()` or `.model_dump()` directly.
- **State migration:** `_migrate_state_dict()` handles legacy field names (e.g., `color` → `color_hex`, `vendor` → `manufacturer`) and older state.json formats.
- **Two API tiers:** `/api/*` returns raw JSON; `/api/ui/*` wraps responses in `{"result": {...}}` for the frontend.
- **Slot IDs:** Literal type `SlotId` = `"1A"` through `"4D"` (4 boxes × 4 colors, 16 total).
- **Spool epochs:** Incrementing `spool_epoch` counter tracks spool changes per slot, enabling per-spool history filtering.
- **History conventions:** `_hist_push()` prepends (newest-first); `_hist_upsert_by_src()` updates existing entries by source marker during live prints.
- **Internal functions** are prefixed with `_` (e.g., `_http_get_json`, `_hist_push`).
- **Filament calculation:** grams = density × π × (diameter/2)² × length, with material-specific density from profiles.json.

## Spoolman Integration (Optional)

Set `spoolman_url` in `data/config.json` to enable. This app acts as the only bridge between Spoolman and the printer (Moonraker's Spoolman plugin is not used). Spools are linked manually via the slot modal dropdown. On link, `remaining_weight` is imported from Spoolman. Consumption is synced back via `PUT /api/v1/spool/{id}/use` (fire-and-forget) when prints finalize or manual allocations are made. Roll changes auto-unlink the Spoolman spool. All Spoolman calls are best-effort and never block local tracking.

## Production Deployment

Installs to `/opt/filament-management/` as a systemd service. See `install.sh`, `update.sh`, `uninstall.sh`, and `filament-management.service.example`.
