#!/usr/bin/env bash
set -euo pipefail

REPO="jkef80/Filament-Management"
APP_DIR="/opt/filament-management"
SERVICE_NAME="filament-management"

if [ "${EUID}" -ne 0 ]; then
  echo "Please run as root (sudo)."
  exit 1
fi

REAL_USER="${SUDO_USER:-root}"

echo "=== Filament Management Installer (GitHub Release) ==="
echo ""

read -p "UI Port (default 8005): " UI_PORT
UI_PORT=${UI_PORT:-8005}

read -p "Moonraker Host/IP (z.B. 192.168.178.148): " PRINTER_IP
read -p "Moonraker Port (default 7125): " PRINTER_PORT
PRINTER_PORT=${PRINTER_PORT:-7125}

read -p "Poll interval sec (default 5): " POLL
POLL=${POLL:-5}

read -p "Filament diameter mm (default 1.75): " DIA
DIA=${DIA:-1.75}

read -p "CFS Autosync? (y/N): " AUTOSYNC
AUTOSYNC=${AUTOSYNC:-N}

AUTOSYNC_BOOL="false"
if [[ "$AUTOSYNC" =~ ^[Yy]$ ]]; then
  AUTOSYNC_BOOL="true"
fi

echo ""
read -p "Version (leave empty = latest release): " VERSION
echo ""

apt update
apt install -y curl tar python3 python3-venv python3-pip rsync ca-certificates

# ---- Resolve download URL (latest or specific) ----
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
if [ -n "${VERSION}" ]; then
  API_URL="https://api.github.com/repos/${REPO}/releases/tags/v${VERSION}"
fi

ASSET_URL="$(curl -fsSL "${API_URL}" \
  | python3 - <<'PY'
import json,sys
data=json.load(sys.stdin)
assets=data.get("assets", [])
# pick first .tar.gz asset
for a in assets:
    name=a.get("name","")
    if name.endswith(".tar.gz"):
        print(a.get("browser_download_url",""))
        sys.exit(0)
print("")
sys.exit(1)
PY
)"

if [ -z "${ASSET_URL}" ]; then
  echo "ERROR: Could not find .tar.gz asset in GitHub release."
  echo "Check that your release contains a .tar.gz asset."
  exit 1
fi

TMP_DIR="$(mktemp -d)"
ARCHIVE="${TMP_DIR}/release.tar.gz"

echo "Downloading: ${ASSET_URL}"
curl -fL "${ASSET_URL}" -o "${ARCHIVE}"

echo "Extracting..."
tar -xzf "${ARCHIVE}" -C "${TMP_DIR}"

# find extracted top folder
SRC_DIR="$(find "${TMP_DIR}" -maxdepth 1 -type d -name 'filament-management-*' | head -n 1)"
if [ -z "${SRC_DIR}" ]; then
  # fallback: if archive doesn't have prefix folder
  SRC_DIR="${TMP_DIR}"
fi

echo "Installing to: ${APP_DIR}"
mkdir -p "${APP_DIR}"
rsync -a --delete --exclude "data/" "${SRC_DIR}/" "${APP_DIR}/"

mkdir -p "${APP_DIR}/data"
chown -R "${REAL_USER}:${REAL_USER}" "${APP_DIR}"
chmod -R u+rwX "${APP_DIR}/data"

# venv + deps
sudo -u "${REAL_USER}" bash -lc "
cd '${APP_DIR}'
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
"

# config.json only if missing
if [ ! -f "${APP_DIR}/data/config.json" ]; then
  cat > "${APP_DIR}/data/config.json" <<EOF
{
  "moonraker_url": "http://${PRINTER_IP}:${PRINTER_PORT}",
  "poll_interval_sec": ${POLL},
  "filament_diameter_mm": ${DIA},
  "cfs_autosync": ${AUTOSYNC_BOOL}
}
EOF
  chown "${REAL_USER}:${REAL_USER}" "${APP_DIR}/data/config.json"
  chmod 664 "${APP_DIR}/data/config.json"
  echo "Created ${APP_DIR}/data/config.json"
else
  echo "Keeping existing ${APP_DIR}/data/config.json"
fi

# systemd service
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Filament Management
After=network.target

[Service]
User=${REAL_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${UI_PORT}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "Done."
echo "Open: http://${IP}:${UI_PORT}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
