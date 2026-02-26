# 3D Drucker Filament Manager (lokal)


# Filament Management

Web UI zur Verwaltung von Filament-Rollen/Slots (Creality CFS / Moonraker).

## Installation (Release tar.gz)

1. Lade das aktuelle Release `filament-management-x.y.z.tar.gz` herunter und kopiere es auf deinen Pi.
2. Entpacken & installieren:

```bash
tar -xzf filament-management-x.y.z.tar.gz
cd filament-management-x.y.z
sudo ./scripts/install.sh

**Pfad:** `/opt/3d-drucker-filement/app`

**UI/API Port:** `8005`

## Starten (manuell)

```bash
cd /opt/3d-drucker-filement/app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start
uvicorn main:app --host 0.0.0.0 --port 8005
```

Dann im Browser:

- `http://<pi-ip>:8005/`

## API (Kurz)

- `GET /api/state`
- `POST /api/select_slot`  `{ "slot": "2A" }`
- `PATCH /api/slots/2A`  z.B. `{ "material": "ABS", "color_hex": "#ff0000", "remaining_g": 1000 }`
- `POST /api/spool/reset` `{ "slot": "2A", "remaining_g": 1000 }`
- `POST /api/spool/apply_usage` `{ "slot": "2A", "used_g": 123.4 }`

## Hinweis

`/api/feed` und `/api/retract` sind aktuell **Dummy-Adapter** (loggen nur). Wenn du das später an echte Hardware/Moonraker/CFS hängen willst, ersetzen wir nur die Funktionen `adapter_feed()` / `adapter_retract()` in `main.py`.

---

## Installation via Installer (systemd)

Voraussetzungen:

- Python 3
- systemd

Im Projektordner:

```bash
chmod +x install.sh
./install.sh
```

Der Installer fragt u.a. nach:

- Port für die UI
- Moonraker IP/Port

Danach läuft ein systemd Dienst: `filament-management`.

```bash
sudo systemctl status filament-management --no-pager
sudo journalctl -u filament-management -f
```

Deinstallation:

```bash
chmod +x uninstall.sh
./uninstall.sh
```
