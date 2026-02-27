#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/filament-management"
SERVICE_NAME="filament-management"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  echo "Please run as root: sudo ./install.sh" >&2
  exit 1
fi

REAL_USER="${SUDO_USER:-}" 
if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
  echo "This installer must be run via sudo from a normal user (SUDO_USER missing)." >&2
  exit 1
fi

prompt() {
  local msg="$1"; local def="$2"; local var
  read -r -p "$msg (default $def): " var || true
  echo "${var:-$def}"
}

UI_PORT="$(prompt "UI Port" "8005")"
MOON_HOST="$(prompt "Moonraker Host/IP" "192.168.178.148")"
MOON_PORT="$(prompt "Moonraker Port" "7125")"
POLL="$(prompt "Poll interval (sec)" "5")"
DIAM="$(prompt "Filament diameter (mm)" "1.75")"
read -r -p "CFS Autosync? (y/N): " AUTOSYNC || true
AUTOSYNC=${AUTOSYNC:-N}
AUTOSYNC_BOOL=false
if [[ "$AUTOSYNC" =~ ^[Yy]$ ]]; then AUTOSYNC_BOOL=true; fi

echo ""
echo "Installing to: $APP_DIR"

# deps
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync

mkdir -p "$APP_DIR"

# copy code (keep data/)
rsync -a --delete \
  --exclude "data/" \
  --exclude "*.pyc" \
  --exclude "__pycache__/" \
  "$SRC_DIR/" "$APP_DIR/"

chown -R "$REAL_USER":"$REAL_USER" "$APP_DIR"

# venv + deps
sudo -u "$REAL_USER" bash -lc "cd '$APP_DIR' && python3 -m venv venv"
sudo -u "$REAL_USER" bash -lc "cd '$APP_DIR' && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

# config
mkdir -p "$APP_DIR/data"
if [[ ! -f "$APP_DIR/data/config.json" ]]; then
  cat > "$APP_DIR/data/config.json" <<CFG
{
  \"moonraker_url\": \"http://${MOON_HOST}:${MOON_PORT}\",
  \"poll_interval_sec\": ${POLL},
  \"filament_diameter_mm\": ${DIAM},
  \"cfs_autosync\": ${AUTOSYNC_BOOL}
}
CFG
  chown "$REAL_USER":"$REAL_USER" "$APP_DIR/data/config.json"
  echo "Created $APP_DIR/data/config.json"
else
  echo "Keeping existing $APP_DIR/data/config.json"
fi

# systemd
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SVC
[Unit]
Description=Filament Management
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${REAL_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${UI_PORT}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "✅ Installed & running"
echo "Open: http://${IP}:${UI_PORT}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
