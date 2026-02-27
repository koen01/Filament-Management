# Filament-Management (lokal)

Lokales Filament-/Spool-Tracking für Creality/Klipper via **Moonraker**.

## Installation (tar.gz)

1. Lade das Release `filament-management-<VERSION>.tar.gz` herunter und kopiere es auf deinen Pi.
2. Install:

```bash
tar -xzf filament-management-<VERSION>.tar.gz
cd filament-management-<VERSION>
sudo ./install.sh
```

Der Installer fragt nach **UI-Port** und **Moonraker Host/IP** und richtet einen systemd Dienst ein.

## Update

Bei einer neuen Version:

```bash
tar -xzf filament-management-<NEW_VERSION>.tar.gz
cd filament-management-<NEW_VERSION>
sudo ./update.sh
```

⚠️ `data/` bleibt dabei erhalten (Config/State/History wird nicht überschrieben).

## Service

```bash
sudo systemctl status filament-management --no-pager
sudo journalctl -u filament-management -f
```

## Deinstallation

```bash
sudo ./uninstall.sh
```

## Dateien / Persistenz

- Installationspfad: `/opt/filament-management`
- Persistente Daten: `/opt/filament-management/data` (Config/State/History)

---

© bei jkef 2026
