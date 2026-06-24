# wg-admin

Minimal WireGuard peer management panel for Linux servers. Python + Flask + systemd.

## Features

- **Peer management**: list, create, edit, delete, enable/disable
- **Auto key generation**: each new peer gets a fresh keypair — private key encrypted at rest with AES-256-GCM
- **Live stats**: per-peer handshake time, transfer counters, endpoint
- **Download `.conf` or QR code** for each peer
- **Single admin password** (Argon2id, m=16MB/t=3/p=1)
- **Change password** page in the UI
- **File-based rate limiting** (5 fails / 5 min block) — survives across socket-activated processes
- **WireGuard auto-install**: if `wg` is missing, installs via apt/dnf/pacman/apk + creates initial `wg0.conf` + enables IP forwarding + opens firewall
- **Initial peer creation**: fresh installs create an admin peer automatically so you can connect right away
- **Encrypted state at rest**: AES-256-GCM with HKDF-SHA256 key derivation
- **Per-peer private key encryption** with domain-separated HKDF info
- **Atomic writes** with `flock` and rotated `.bak`/`.bak1` backups
- **TLS via Let's Encrypt**: detects existing certs and uses them automatically
- **Hardened systemd unit**: `ProtectSystem=strict`, `ReadWritePaths` scoped, `NoNewPrivileges`, etc.
- **Low footprint**: designed for 1GB-RAM hardware

## Requirements

- Linux with systemd ≥ 235
- Python 3.11+ (auto-installed if missing on Debian/Ubuntu)
- Root access

WireGuard itself is **auto-installed if missing** — you don't need to set it up manually.

## Install

```bash
git clone https://github.com/<user>/wg-admin.git /tmp/wg-admin
sudo bash /tmp/wg-admin/install.sh
```

The install script is **idempotent** — safe to re-run. It will:

1. **Install WireGuard** if `wg`/`wg-quick` are missing (detects Debian/Ubuntu, RHEL/Fedora, Arch, Alpine)
2. **Create `/etc/wireguard/wg0.conf`** if it doesn't exist (asks for subnet, IP, port; generates server keypair; sets up NAT; enables IP forwarding; opens firewall; starts `wg-quick@wg0`)
3. Create `/wg-admin/` with venv and code
4. Generate `master.key`, `session.key` (32 random bytes each)
5. Prompt for admin password (Argon2id hashed)
6. Ask for endpoint hostname and listen port
7. **Detect existing Let's Encrypt cert** at `/etc/letsencrypt/live/<host>/` and configure TLS automatically
8. Auto-populate `server_public_key` in config.ini (derives from `wg pubkey`)
9. Import existing peers from `/etc/wireguard/wg0.conf`
10. **Create initial admin peer** if state ended up empty (so you can connect immediately)
11. Install systemd units, enable + start service
12. Open firewalld port if present

Then visit `https://<host>:<port>/`.

## Uninstall

```bash
sudo bash /wg-admin/uninstall.sh             # full remove
sudo bash /wg-admin/uninstall.sh --keep-state # backup state + secrets to /tmp/
```

The uninstall does NOT touch `/etc/wireguard/wg0.conf` or Let's Encrypt certs — those are system resources.

## Development

```bash
git clone <repo>
cd wg-admin
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

103 tests, 91% coverage.

## Architecture

```
src/wg_admin/
├── app.py        # Flask factory, routes, auth, CSRF, rate limit, change-password
├── config.py     # Load config.ini with defaults
├── crypto.py     # HKDF-SHA256, AES-256-GCM, Argon2id
├── state.py      # Encrypted state load/save, schema, IP allocation, per-peer key encryption
├── wg.py         # Subprocess wrappers for wg commands (genkey, show, restart, pubkey)
├── confgen.py    # .conf + QR code generation
└── ratelimit.py  # File-based IP throttling

templates/        # Jinja2: base, login, peers, peer_form, peer_edit, change_password, error
static/           # CSS
systemd/          # wg-admin.service (no socket activation — direct listen with TLS)
install.sh        # Idempotent installer
uninstall.sh      # Cleanup with optional state backup
```

**State persistence** (`/wg-admin/state.json.enc`):
- AES-256-GCM envelope
- HKDF-SHA256 key derivation from `master.key` (per-write fresh salt + nonce)
- Atomic writes via temp + fsync + rename
- File lock with timeout
- Rotated `.bak` and `.bak1` backups
- Per-peer private keys encrypted with domain-separated HKDF info (`wireguard-admin-peerkey-v1`)

**Security model:**
- TLS (Let's Encrypt cert detected automatically)
- Single admin password (Argon2id, rate-limited 5 fails/min)
- CSRF tokens on every state-changing POST
- Session cookies: HttpOnly + Secure + SameSite=Strict, 1h lifetime
- Service runs as root (needed for `wg-quick`), but systemd unit hardens with `ProtectSystem=strict`, `NoNewPrivileges`, etc.

## Documentation

- [Design spec](docs/superpowers/specs/2026-06-17-wg-admin-design.md)
- [Implementation plan](docs/superpowers/plans/2026-06-17-wg-admin-implementation.md)
- [Smoke test checklist](docs/smoke-test.md)

## Limitations

- **Single admin**: no multi-user, no audit trail between users
- **Imported peers from existing `wg0.conf` cannot download `.conf`/QR**: their private keys only exist on the client devices. The panel shows a 🔒 marker; you can paste the private key later via the "Editar" page if you have it.
- **`wg-quick restart` on every mutation** (create/delete/toggle) — all active peers briefly disconnect (~1s). UI warns about this.
- **No 2FA**: if password is compromised, attacker has full panel access

## License

MIT
