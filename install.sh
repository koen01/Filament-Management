#!/usr/bin/env bash
set -euo pipefail

# Filament-Management installer (systemd + venv)
# Run as normal user (not root). The script uses sudo when required.

if [[ "${EUID}" -eq 0 ]]; then
  echo "Bitte nicht als root starten. Nutze deinen normalen User; sudo wird bei Bedarf abgefragt." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEFAULT_INSTALL_DIR="/opt/filament-management"
DEFAULT_PORT="8005"
DEFAULT_MOONRAKER_HOST="192.168.178.148"
DEFAULT_MOONRAKER_PORT="7125"

read -r -p "Installationspfad [${DEFAULT_INSTALL_DIR}]: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

read -r -p "UI Port [${DEFAULT_PORT}]: " UI_PORT
UI_PORT="${UI_PORT:-$DEFAULT_PORT}"

read -r -p "Moonraker IP/Host [${DEFAULT_MOONRAKER_HOST}]: " MR_HOST
MR_HOST="${MR_HOST:-$DEFAULT_MOONRAKER_HOST}"

read -r -p "Moonraker Port [${DEFAULT_MOONRAKER_PORT}]: " MR_PORT
MR_PORT="${MR_PORT:-$DEFAULT_MOONRAKER_PORT}"

MOONRAKER_URL="http://${MR_HOST}:${MR_PORT}"

read -r -p "CFS Autosync aktivieren? (y/N): " CFS_AUTOSYNC
CFS_AUTOSYNC="${CFS_AUTOSYNC:-N}"
if [[ "${CFS_AUTOSYNC}" =~ ^[Yy]$ ]]; then
  CFS_AUTOSYNC_JSON=true
else
  CFS_AUTOSYNC_JSON=false
fi

read -r -p "Poll-Intervall in Sekunden [5]: " POLL
POLL="${POLL:-5}"

SERVICE_NAME="filament-management"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

echo
echo "==> Installiere nach: ${INSTALL_DIR}"
echo "==> UI Port: ${UI_PORT}"
echo "==> Moonraker: ${MOONRAKER_URL}"
echo

echo "==> Kopiere Dateien…"
sudo mkdir -p "${INSTALL_DIR}"
# Copy the whole app directory into ${INSTALL_DIR}/app
sudo rsync -a --delete "${SCRIPT_DIR}/" "${INSTALL_DIR}/app/"
# Ensure ownership back to the installing user
sudo chown -R "${USER}:${USER}" "${INSTALL_DIR}"

echo "==> Python venv + Dependencies…"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip >/dev/null
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/app/requirements.txt" >/dev/null

echo "==> Schreibe Konfiguration…"
mkdir -p "${INSTALL_DIR}/app/data"
cat > "${INSTALL_DIR}/app/data/config.json" <<CFG
{
  "moonraker_url": "${MOONRAKER_URL}",
  "poll_interval_sec": ${POLL},
  "filament_diameter_mm": 1.75,
  "cfs_autosync": ${CFS_AUTOSYNC_JSON}
}
CFG

echo "==> Systemd Service…"
sudo tee "${SERVICE_PATH}" >/dev/null <<UNIT
[Unit]
Description=Filament-Management (FastAPI via Uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${INSTALL_DIR}/app
Environment=PYTHONUNBUFFERED=1
ExecStart=${INSTALL_DIR}/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port ${UI_PORT}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

echo "==> Service aktivieren & starten…"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}.service"

echo
echo "Fertig ✅"
echo "Status:   sudo systemctl status ${SERVICE_NAME} --no-pager"
echo "Logs:     sudo journalctl -u ${SERVICE_NAME} -f"
echo "UI:       http://<DEIN-PI>:${UI_PORT}"
