# Changelog

All notable changes to wg-admin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Per-peer bandwidth quotas** — set `quota_gb` on each peer to auto-suspend when rolling 30-day usage exceeds the limit. Re-enables automatically when usage drops below.
- **Global bandwidth quota** — `[quota] global_quota_gb` in config.ini. Sidebar shows rolling 30-day total. Red banner when exceeded.
- **VPN kill switch** — sidebar button to stop/start `wg-quick@wg0` for emergencies.
- **Live bandwidth charts** — sparkline per peer card, modal with 30-day rx/tx line chart, global stacked area chart at top of `/peers`.
- **WhatsApp share** — modal that asks DDI + phone number, sends `.conf` as attachment via Web Share API on mobile or download+chat on desktop.
- **syncconf hot-reload** — new peers no longer drop active tunnels (zero-downtime create).
- **GitHub repo link** in the sidebar footer (opens in new tab with `rel="noopener"`).

### Changed
- **Removed the warning banner** from the peers list page (`/peers`). The "wg-quick
  restart disconnects active peers" warning now only shows on the create-peer form,
  where it's contextually relevant.
- **Simplified status badge**: dropped the `configurado` middle state. Peers now show
  either `online` (handshake within last 180s) or `offline` (anything else, including
  no recent handshake or peer not in `wg show`). `inativo` still used for disabled
  peers.
- **`_apply_state_to_wg` moved from `app.py` to `wg.py`** as `apply_state_to_wg(s, cfg, mode)`. Required for bandwidth timer to enforce quotas.
- **Unauthenticated POSTs redirect to login instead of 403** — CSRF check now skips when there's no session, letting `login_required` handle the redirect.
- **State schema gained 3 fields per peer**: `quota_gb` (float, 0=unlimited), `quota_suspended` (bool), `quota_state_updated_at` (ISO 8601 or None). Migration runs idempotently on every `load_state` via `setdefault`.
- **`config.ini` gained `[quota]` section** with `global_quota_gb` default `0` (unlimited).

### Tests
- 161 total (up from 116), 89% coverage across:
  - `wg.py` — added tests for `wg_syncconf`, `wg_interface_active`, `apply_state_to_wg(mode=...)` (8 new)
  - `state.py` — added `migrate_state` tests (3 new)
  - `config.py` — added `[quota]` defaults tests (2 new)
  - `quota.py` — new module, 8 tests for `check_quotas`, `global_usage_gb`, `global_quota_exceeded`
  - `bandwidth.py` — added sparkline tests + quota integration tests (6 new)
  - `app.py` — added tests for `/api/bandwidth/*`, `/vpn/toggle`, peer_new/edit `quota_gb` validation (18 new)

## [0.1.0] — 2026-06-24

### Added — Initial public release

#### Architecture
- Flask + Python 3.11+ panel for managing WireGuard peers
- Dark "NOC at night" theme with Hanken Grotesk + JetBrains Mono fonts
- Sticky left sidebar nav with brand, status indicator, version, logout
- Card-based peer grid with stats bar (total / connected / imported)
- Responsive layout — collapses to top bar on mobile
- Custom SVG favicon

#### Crypto & Security
- AES-256-GCM envelope for state.json.enc with HKDF-SHA256 key derivation
- Per-peer private key encryption at rest using domain-separated HKDF info
  (`wireguard-admin-peerkey-v1`)
- Argon2id admin password hashing (m=16MB, t=3, p=1) — calibrated for 1GB-RAM servers
- File-based rate limiting (5 fails / 5 min IP block) that survives across
  socket-activated processes
- CSRF tokens on every state-changing POST
- Atomic writes via temp + fsync + rename with `flock`
- Rotated `.bak` / `.bak1` backups
- Hardened systemd unit (`ProtectSystem=strict`, `NoNewPrivileges`, `LockPersonality`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK`)

#### Peer Management
- List peers with live status from `wg show`
- Create peer with auto-generated encrypted keypair + auto-allocated IP
- Edit peer (name, notes, paste private key for legacy imports)
- Delete / enable / disable
- Download `.conf` with full WireGuard config
- Download QR code — opens in modal with ESC/click-outside/close-button, includes
  download links for `.conf` and PNG

#### Bandwidth Tracking
- Per-peer usage: total since creation + last 30 days
- systemd timer samples `wg show wg0 dump` every 5 minutes
- Cumulative totals survive counter resets (wg-quick restarts, reboots)
- Daily buckets pruned after 30 days
- Stored in `/wg-admin/bandwidth.json` (JSON, non-sensitive)

#### Install Experience
- **WireGuard auto-install** if missing (apt/dnf/pacman/apk + distro auto-detection)
- **Create `/etc/wireguard/wg0.conf`** if missing — subnet menu (3 presets), server IP
  with validation (must be in subnet), port menu (4 options including 443 for stealth)
- **Auto-create initial admin peer** so the user can connect immediately after install
- **Auto-detect existing Let's Encrypt cert** at `/etc/letsencrypt/live/<host>/`
- **Generate self-signed cert** if hostname is an IP (with proper `subjectAltName=IP:...`)
- **Endpoint auto-update** on IP change — re-running install detects public IP drift
  (common AWS stop/start scenario) and updates config.ini + regenerates cert
- **Auto-detect cloud provider** (AWS EC2, DigitalOcean, GCP) via instance metadata
  and prints specific firewall instructions in the post-install wizard
- **Auto-populate `server_public_key`** with 3 fallback sources:
  1. `wg show wg0 public-key` (live runtime)
  2. `wg0.conf` PrivateKey + `wg pubkey` derivation
  3. Search all `/etc/wireguard/*.conf` files
- **Open firewall ports** in ufw and firewalld
- **Ensure SSH (22/tcp)** is allowed before adding other rules — prevents lockout
- **Self-signed cert fallback** for IP endpoints (with clear browser-warning explanation)

#### UI Polish
- QR code modal with backdrop blur, ESC key, click-outside, focus management
- Pulsing green "online" badge, idle/inactive variants
- Handshake recency determines online state (180s window — WireGuard re-handshake interval)
- Tooltip with full pubkey on hover (truncated in card)
- Visible `estado wg` row showing handshake age or `NÃO ENCONTRADO` with diagnostic
- Sidebar status indicator ("service online" with pulsing dot)
- GitHub repo link in sidebar footer

#### Documentation
- Full design spec (PT)
- 28-task implementation plan with TDD breakdown
- Smoke test checklist (15 manual verification steps)
- Contributing guide with commit-style conventions
- Issue templates (bug report + feature request)
- PR template
- LICENSE (MIT)
- GitHub Actions CI (Python 3.11/3.12 matrix, ruff check, bash syntax check)

#### Tests
- 116 unit tests, 91% coverage across:
  - `crypto.py` (HKDF, AES-GCM, Argon2id) — 22 tests
  - `state.py` (schema, IP alloc, atomic save/load) — 24 tests
  - `wg.py` (parser auto-detect, conf genkey, subprocess wrappers) — 20 tests
  - `confgen.py` (.conf + QR) — 7 tests
  - `ratelimit.py` (file-based throttling) — 7 tests
  - `config.py` (defaults + override) — 5 tests
  - `bandwidth.py` (tracking + aggregation) — 10 tests
  - `app.py` (Flask routes, auth, CSRF) — 21 tests

#### Notable bug fixes during development
- `python3-venv` missing on Ubuntu 24.04/25.04 → auto-install
- `/etc/sysctl.conf` removed on Ubuntu 25.04 → use `/etc/sysctl.d/`
- Server IP validation (must be in chosen subnet)
- `wg-quick restart` "already exists" error → use stop + start
- Subnet chosen in install not propagating to config.ini
- Endpoint required → defaults to detected public IP
- `systemctl enable --now` doesn't restart running service → use `enable` + `restart`
- `server_public_key` only populated on fresh install → always populate
- `python3-venv` PATH detection
- Base64 trailing `=` truncated by `cut -d=` in shell parser
- Heredoc unquoted → `$VAR` expanded unintentionally
- AF_NETLINK missing from `RestrictAddressFamilies` → `wg show` failed silently
- `now()` doesn't exist in Jinja → moved timestamp logic to Python
- `wg show dump` parser assumed 8-field format → modern format has 9 (with PSK) or
  no ifname prefix; auto-detect all 4 variants
- Bandwidth tracker used `(none)` as pubkey due to old parser bug → validate pubkey
  before tracking
- AWS public IP change between stop/start → auto-detect and update config.ini
- Idempotent re-runs losing config.ini values → always re-populate critical fields

[Unreleased]: https://github.com/joaopaulooooo/wg-admin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/joaopaulooooo/wg-admin/releases/tag/v0.1.0
