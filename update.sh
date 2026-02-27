#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/filament-management"
SERVICE_NAME="filament-management"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  echo "Please run as root: sudo ./update.sh" >&2
  exit 1
fi

REAL_USER="${SUDO_USER:-}" 
if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
  echo "This updater must be run via sudo from a normal user (SUDO_USER missing)." >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Not installed at $APP_DIR. Run install.sh first." >&2
  exit 1
fi

apt-get update -y
apt-get install -y rsync

# update code, keep data/
rsync -a --delete \
  --exclude "data/" \
  --exclude "*.pyc" \
  --exclude "__pycache__/" \
  "$SRC_DIR/" "$APP_DIR/"

chown -R "$REAL_USER":"$REAL_USER" "$APP_DIR"

# update deps
sudo -u "$REAL_USER" bash -lc "cd '$APP_DIR' && source venv/bin/activate && pip install -r requirements.txt"

systemctl restart "$SERVICE_NAME"

echo "✅ Updated & restarted"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
