#!/usr/bin/env bash
# uninstall.sh — remove wg-admin. Option --keep-state preserves secrets+state.
set -euo pipefail

# The backup tarball contains master/session keys — it must never be
# world-readable (the default umask on most systems would create 0644).
umask 077

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

# Stop writers FIRST so the backup captures a quiesced state/secrets dir.
info "Stopping services"
systemctl stop wg-admin.service wg-admin-bandwidth.timer wg-admin-bandwidth.service wg-admin.socket 2>/dev/null || true
systemctl disable wg-admin.service wg-admin-bandwidth.timer wg-admin.socket 2>/dev/null || true

if $KEEP_STATE; then
  BACKUP="/tmp/wg-admin-backup-$(date +%Y%m%d%H%M%S).tar.gz"
  info "Backing up state and secrets to $BACKUP (0600)"
  # No '|| true' here: if the backup fails we must NOT proceed to rm -rf.
  if ! tar -czf "$BACKUP" \
      -C "$INSTALL_DIR" \
      secrets state.json.enc state.json.enc.bak state.json.enc.bak1 config.ini 2>/dev/null; then
    err "Backup falhou — a abortar SEM remover $INSTALL_DIR (verifica se os ficheiros existem)"
  fi
  chmod 0600 "$BACKUP"
  [[ -s "$BACKUP" ]] || err "Backup vazio — a abortar SEM remover $INSTALL_DIR"
  echo "Backup saved to $BACKUP"
fi

info "Removing systemd units"
rm -f /etc/systemd/system/wg-admin.socket /etc/systemd/system/wg-admin.service
systemctl daemon-reload

info "Removing $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

info "Done. wg-admin fully removed."
if $KEEP_STATE; then
  echo "Secrets/state preserved in backup tarball:"
  echo "  $BACKUP (0600 — move para um local seguro e apaga quando acabares)"
fi
