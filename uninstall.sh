#!/usr/bin/env bash
# uninstall.sh — remove wg-admin. Option --keep-state preserves secrets+state.
set -euo pipefail

INSTALL_DIR="/wg-admin"
KEEP_STATE=false
[[ "${1:-}" == "--keep-state" ]] && KEEP_STATE=true

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo ">>> $*"; }

[[ $EUID -eq 0 ]] || err "Must run as root"

if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "wg-admin not found at $INSTALL_DIR; nothing to do."
  exit 0
fi

if $KEEP_STATE; then
  BACKUP="/tmp/wg-admin-backup-$(date +%s).tar.gz"
  info "Backing up state and secrets to $BACKUP"
  tar -czf "$BACKUP" \
    -C "$INSTALL_DIR" secrets state.json.enc state.json.enc.bak state.json.enc.bak1 config.ini 2>/dev/null || true
  echo "Backup saved to $BACKUP"
fi

info "Stopping services"
systemctl stop wg-admin.socket wg-admin.service 2>/dev/null || true
systemctl disable wg-admin.socket wg-admin.service 2>/dev/null || true

info "Removing systemd units"
rm -f /etc/systemd/system/wg-admin.socket /etc/systemd/system/wg-admin.service
systemctl daemon-reload

info "Removing $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

info "Done. wg-admin fully removed."
if $KEEP_STATE; then
  echo "Secrets/state preserved in backup tarball."
fi
