# wg-admin

Minimal WireGuard peer management panel for Linux servers. Python + Flask + systemd. NOC-style dark UI, designed for sysadmins.

![License](https://img.shields.io/badge/license-MIT-blue)
![Tests](https://img.shields.io/badge/tests-172-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-89%25-brightgreen)
![Python](https://img.shields.io/badge/python-3.11+-blue)

## Features

### Peer management
- **List, create, edit, delete** with auto-generated encrypted keypairs (AES-256-GCM)
- **Enable/disable** without losing config
- **Live status**: online/offline badge with pulsing green dot based on handshake recency
- **Per-peer bandwidth tracking**: 30-day and total stats, auto-sampled every 5 min

### Downloads & sharing
- **`.conf` file** with full WireGuard config
- **QR code** opens in elegant modal — scan with WireGuard mobile app
- **WhatsApp share** — modal asks for client phone, sends .conf as attachment via Web Share API (mobile) or download+chat (desktop)

### Quotas & monitoring
- **Per-peer quotas**: set GB limit per peer; auto-suspend on 30-day rolling overage
- **Global quota**: warning banner when total exceeds limit
- **VPN kill switch**: stop/start the entire VPN from the sidebar
- **Live charts**: sparkline per card, modal per peer, global stacked chart at top of /peers

### Security
- **Single admin password** (Argon2id, m=16MB/t=3/p=1)
- **Change password** page in UI
- **File-based rate limiting** — 5 fails / 30 min IP block, survives across processes
- **Auth attempt log** — successful and failed logins written as JSON lines to `/wg-admin/secrets/auth.log`, rotates at 100 KB (5 backups kept)
- **CSRF tokens** on every state-changing POST
- **Encrypted state at rest**: AES-256-GCM with HKDF-SHA256 key derivation
- **Per-peer private key encryption** with domain-separated HKDF info
- **Atomic writes** with `flock` and rotated `.bak`/`.bak1` backups
- **Gunicorn WSGI server** (4 workers) — replaces the single-threaded Werkzeug dev server, which silently dropped new connections once the accept queue filled under scanner load
- **Hardened systemd unit** (`ProtectSystem=strict`, `RestrictAddressFamilies` with `AF_NETLINK` for wg access)

### Install experience
- **Auto-install WireGuard** if missing (apt/dnf/pacman/apk)
- **Auto-create `/etc/wireguard/wg0.conf`** with generated keypair, NAT rules, IP forwarding
- **Auto-create initial admin peer** so you can connect immediately
- **Detect existing Let's Encrypt cert** and wire TLS automatically — or generate self-signed for IP endpoints
- **Auto-detect AWS/GCP/DigitalOcean** and print specific firewall instructions
- **Endpoint auto-updates** on IP change (AWS stop/start scenarios)
- **Auto-detect `server_public_key`** via `wg show` + `wg pubkey` from wg0.conf
- **Open firewall ports** in ufw and firewalld automatically
- **Ensure SSH (22/tcp)** is allowed before adding other rules — never lock yourself out

### UI
- **NOC-at-night dark theme**: Hanken Grotesk + JetBrains Mono, WireGuard-red accents
- **Sticky left sidebar** with brand, status indicator, nav, version, GitHub link
- **Card-based peer grid** instead of plain table
- **Stats bar** showing total / connected / imported counts
- **Responsive**: collapses to top bar on mobile
- **Custom SVG favicon**

## Requirements

- Linux with systemd ≥ 235
- Python 3.11+ (auto-installed if missing on Debian/Ubuntu)
- Root access

WireGuard is **auto-installed if missing**.

## Install

```bash
git clone https://github.com/joaopaulooooo/wg-admin.git /tmp/wg-admin
sudo bash /tmp/wg-admin/install.sh
```

The install script is **idempotent** and walks through everything with numbered menus:

1. **WireGuard auto-install** if `wg`/`wg-quick` are missing (detects Debian/Ubuntu, RHEL/Fedora, Arch, Alpine)
2. **Create `/etc/wireguard/wg0.conf`** if missing (asks subnet from 3 presets, server IP, port from 4 options; generates keypair; sets up NAT + IP forwarding; opens firewall; starts `wg-quick@wg0`)
3. Create `/wg-admin/` with venv and code
4. Generate `master.key`, `session.key` (32 random bytes each)
5. Prompt for admin password (Argon2id hashed)
6. Ask for endpoint hostname/IP (auto-detects public IP via ipify, accepts empty for default)
7. Ask for panel port (4 options + custom)
8. **Auto-populate `server_public_key`** in config.ini via 3 fallback sources (live `wg show`, `wg0.conf` parse, search all `*.conf` files)
9. **Detect existing Let's Encrypt cert** at `/etc/letsencrypt/live/<host>/` and wire it
10. **Generate self-signed cert** if hostname is an IP (Let's Encrypt can't issue for IPs)
11. Import existing peers from `/etc/wireguard/wg0.conf`
12. **Create initial admin peer** if state is empty — generates keypair, encrypts private key, regenerates wg0.conf
13. Install systemd units + bandwidth timer
14. Run first bandwidth sample immediately
15. Open firewalld/ufw ports
16. Print **post-install wizard** with provider-specific instructions (AWS Security Groups, GCP VPC, DO Cloud Firewalls, home router port-forwarding, DDNS tips)

After install, visit `https://<host>:<port>/`.

### Re-run / upgrade

```bash
cd /tmp/wg-admin && git pull
sudo bash /tmp/wg-admin/install.sh
```

State, secrets, and config are preserved. The script auto-detects IP changes and updates endpoint if needed.

## Uninstall

```bash
sudo bash /wg-admin/uninstall.sh             # full remove
sudo bash /wg-admin/uninstall.sh --keep-state # backup state + secrets to /tmp/
```

Does NOT touch `/etc/wireguard/wg0.conf` or Let's Encrypt certs — those are system resources.

## Development

```bash
git clone https://github.com/joaopaulooooo/wg-admin.git
cd wg-admin
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

172 tests, 89% coverage.

CI runs on every push: [.github/workflows/ci.yml](.github/workflows/ci.yml) — Python 3.11/3.12 matrix.

## Architecture

```
src/wg_admin/
├── app.py        # Flask factory, routes, auth, CSRF, rate limit, change-password, context processor, Jinja filters
├── bandwidth.py  # 5-min periodic sampler, daily buckets, 30-day retention, quota check
├── config.py     # Load config.ini with defaults (incl. [quota] section)
├── crypto.py     # HKDF-SHA256, AES-256-GCM, Argon2id
├── quota.py      # Per-peer and global quota checks (rolling 30-day)
├── state.py      # Encrypted state load/save, schema migration, IP allocation, per-peer key encryption
├── wg.py         # Subprocess wrappers + parser, apply_state_to_wg, wg_syncconf, wg_interface_active
├── confgen.py    # .conf + QR code generation
├── ratelimit.py  # File-based IP throttling (5 fails / 30 min block)
└── authlog.py    # Rotating JSON-lines auth attempt log

Production runs through **gunicorn** (4 workers, defined in `install.sh`'s
generated systemd unit and `systemd/wg-admin.service`). Flask's dev server
is not used in production.

templates/        # Jinja2: base, login, peers, peer_form, peer_edit, change_password, error
static/           # NOC-at-night CSS (style.css)
static/js/        # bandwidth-modal, whatsapp-modal, global-chart
static/vendor/    # Chart.js v4 (local copy, no CDN at runtime)
systemd/          # wg-admin.service, wg-admin-bandwidth.{service,timer}
install.sh        # Idempotent installer with provider auto-detection
uninstall.sh      # Cleanup with optional state backup
tests/            # 172 tests, integration smoke test
docs/             # specs, plans, smoke test checklist
.github/          # CI workflow, issue/PR templates
```

**State persistence** (`/wg-admin/state.json.enc`):
- AES-256-GCM envelope
- HKDF-SHA256 key derivation from `master.key` (per-write fresh salt + nonce)
- Atomic writes via temp + fsync + rename
- File lock with timeout
- Rotated `.bak` and `.bak1` backups

**Bandwidth tracking** (`/wg-admin/bandwidth.json`):
- Sampled every 5 min via systemd timer
- Cumulative totals that survive counter resets (wg-quick restarts, reboots)
- Daily buckets pruned after 30 days
- After each sample, runs quota check — peers exceeding their `quota_gb` (rolling 30-day) are auto-suspended; reactivated when usage drops below

## Documentation

- [Changelog](CHANGELOG.md)
- [v0.1.0 design spec](docs/superpowers/specs/2026-06-17-wg-admin-design.md)
- [v0.1.0 implementation plan](docs/superpowers/plans/2026-06-17-wg-admin-implementation.md)
- [Quotas/graphs/syncconf/WhatsApp design](docs/superpowers/specs/2026-06-27-quota-graphs-syncconf-whatsapp-design.md)
- [Quotas/graphs/syncconf/WhatsApp plan](docs/superpowers/plans/2026-06-27-quota-graphs-syncconf-whatsapp.md)
- [Smoke test checklist](docs/smoke-test.md)
- [Contributing](CONTRIBUTING.md)

## Limitations

- **Single admin**: no multi-user, no audit trail between users
- **Imported peers from existing `wg0.conf` cannot download `.conf`/QR**: their private keys only exist on client devices. Panel shows 🔒 marker; paste private key later via "Editar" if available
- **`wg-quick restart` on delete/toggle only** — create uses `wg syncconf` for zero-downtime. Delete and toggle still cause ~1s disconnect (required to clean PostUp/iptables rules).
- **No 2FA**: if password is compromised, attacker has full panel access

## License

MIT
