#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/filament-management"
SERVICE_NAME="filament-management"
REPO_URL="https://github.com/koen01/Filament-Management.git"

if [[ ${EUID} -ne 0 ]]; then
  echo "Please run with sudo"
  exit 1
fi

REAL_USER="${SUDO_USER:-}"
if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
  echo "Run via sudo from normal user"
  exit 1
fi

ask() {
  local prompt="$1"
  local default="$2"
  local var

  if [ -r /dev/tty ]; then
    read -r -p "$prompt [$default]: " var < /dev/tty
  fi

  echo "${var:-$default}"
}

echo "=== CFSync Installer ==="

UI_PORT=$(ask "UI Port" "8005")
PRINTER_IP=$(ask "Printer IP" "192.168.1.144")
DIAM=$(ask "Filament diameter (mm)" "1.75")
SPOOLMAN_URL=$(ask "Spoolman URL (optional, e.g. http://host:7912)" "")

echo "Installing to $APP_DIR"

apt-get update -y
apt-get install -y python3 python3-venv python3-pip git rsync curl

mkdir -p "$APP_DIR"

echo "Cloning repository..."
rm -rf /tmp/filament-install
git clone --depth 1 "$REPO_URL" /tmp/filament-install

rsync -a --delete \
  --exclude ".git/" \
  --exclude "data/" \
  --exclude "__pycache__/" \
  /tmp/filament-install/ "$APP_DIR/"

rm -rf /tmp/filament-install

chown -R "$REAL_USER":"$REAL_USER" "$APP_DIR"

sudo -u "$REAL_USER" bash -lc "
cd '$APP_DIR'
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
"

mkdir -p "$APP_DIR/data"

cat > "$APP_DIR/data/config.json" <<CFG
{
  "printer_url": "${PRINTER_IP}",
  "filament_diameter_mm": ${DIAM},
  "spoolman_url": "${SPOOLMAN_URL}"
}
CFG

chown -R "$REAL_USER":"$REAL_USER" "$APP_DIR/data"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SVC
[Unit]
Description=CFSync
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
echo "✅ Installed successfully"
echo "Open: http://${IP}:${UI_PORT}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
