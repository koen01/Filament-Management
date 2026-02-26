#!/usr/bin/env bash
set -euo pipefail

# Filament-Management uninstaller

if [[ "${EUID}" -eq 0 ]]; then
  echo "Bitte nicht als root starten. Nutze deinen normalen User; sudo wird bei Bedarf abgefragt." >&2
  exit 1
fi

DEFAULT_INSTALL_DIR="/opt/filament-management"
SERVICE_NAME="filament-management"

read -r -p "Installationspfad löschen? [${DEFAULT_INSTALL_DIR}] (leer = nein): " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-}"

echo "==> Stoppe/Deaktiviere Service…"
sudo systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
sudo rm -f "/etc/systemd/system/${SERVICE_NAME}.service" || true
sudo systemctl daemon-reload

if [[ -n "${INSTALL_DIR}" ]]; then
  echo "==> Lösche ${INSTALL_DIR}…"
  sudo rm -rf "${INSTALL_DIR}"
else
  echo "==> Installationsordner wurde NICHT gelöscht."
fi

echo "Fertig ✅"
