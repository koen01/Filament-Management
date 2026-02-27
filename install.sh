#!/usr/bin/env bash
set -euo pipefail

REPO="jkef80/Filament-Management"
BRANCH="main"

APP_DIR="/opt/filament-management"
SERVICE_NAME="filament-management"

# tar.gz liegt bei dir im Repo-Root:
ARCHIVE_NAME="filament-management-1.0.2.tar.gz"

if [ "${EUID}" -ne 0 ]; then
  echo "Bitte mit sudo ausführen."
  exit 1
fi

REAL_USER="${SUDO_USER:-root}"

echo "=== Filament Management Online Installer ==="
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

apt update
apt install -y curl tar rsync python3 python3-venv python3-pip ca-certificates

TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="${TMP_DIR}/${ARCHIVE_NAME}"

URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/${ARCHIVE_NAME}"
echo "Downloading: ${URL}"
curl -fL "${URL}" -o "${ARCHIVE_PATH}"

echo "Extracting..."
tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"

SRC_DIR="$(find "${TMP_DIR}" -maxdepth 1 -type d -name 'filament-management-*' | head -n 1)"
if [ -z "${SRC_DIR}" ]; then
  echo "ERROR: could not find extracted folder"
  exit 1
fi

echo "Installing to: ${APP_DIR}"
mkdir -p "${APP_DIR}"

# Code drüber kopieren, data/ behalten
rsync -a --delete --exclude "data/" "${SRC_DIR}/" "${APP_DIR}/"
mkdir -p "${APP_DIR}/data"

# Rechte
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

# config.json nur wenn nicht vorhanden (wichtig!)
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

# >>>>>> WICHTIGER FIX: Port in env-file speichern (keine kaputte Expansion möglich)
cat > "${APP_DIR}/data/env" <<EOF
UI_PORT=${UI_PORT}
EOF
chown "${REAL_USER}:${REAL_USER}" "${APP_DIR}/data/env"
chmod 664 "${APP_DIR}/data/env"

# systemd service (PORT kommt aus EnvironmentFile)
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<'EOF'
[Unit]
Description=Filament Management
After=network.target

[Service]
EnvironmentFile=/opt/filament-management/data/env
WorkingDirectory=/opt/filament-management
ExecStart=/opt/filament-management/venv/bin/uvicorn main:app --host 0.0.0.0 --port ${UI_PORT}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

# Service soll als REAL_USER laufen (User-Zeile separat setzen)
# (damit wir den heredoc oben single-quoted lassen können)
sed -i "s|^\[Service\]$|[Service]\nUser=${REAL_USER}|" "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "Done."
echo "Open: http://${IP}:${UI_PORT}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
