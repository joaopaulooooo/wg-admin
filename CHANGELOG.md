# Changelog

All notable changes to wg-admin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed
- **WhatsApp share button and modal** — dropped from the peers page along with its JS (`static/js/whatsapp-modal.js`). `.conf` download and QR remain.

### Fixed
- **Peer card action buttons standardized** — always two rows (row 1: `.conf`, `QR Code`, `Rodar chave`/`Gerar chaves`, `Editar`; row 2: `Ativar`/`Desativar`, `Apagar`) with uniform fixed height; the previous mixed `<a>`/`<button>` inline layout rendered mismatched heights (UA `line-height` differs between the two). Emojis dropped from button labels.
- **Peer toggle form was missing its CSRF token** — the Ativar/Desativar form posted without `csrf_token` and would 403 against the CSRF check.

### Added
- **Key generation for imported peers** — peers imported from `wg0.conf` (🔒, no stored private key) now have a **🔑 Gerar chaves** button on their card: generates a fresh keypair, stores the private key encrypted, updates the peer's public key in state + `wg0.conf` (applied via zero-downtime `syncconf`), which unlocks `.conf` download and QR. The confirm dialog warns that the device's current config stops working until it re-imports the new QR.
- **Per-peer private key rotation** — peers created in wg-admin now have a **🔑 Rodar chave** button on their card (same route): rotates that peer's keypair, applies via `syncconf`, and the new `.conf`/QR are immediately downloadable.

## [0.2.0] — 2026-09-03

### Added
- **WireGuard settings page** (`/settings`, sidebar → "Configurações") — full server-side WireGuard configuration without SSH:
  - **Interface**: Address (CIDR), ListenPort, MTU and PostUp/PostDown hooks, applied via a surgical `[Interface]`-section rewrite that preserves peer blocks (including `# name:` headers and disabled peers) byte-for-byte, then regenerated from state and applied with `wg-quick` restart. Validated: CIDR, port range, MTU 1280-9000.
  - **Peer defaults**: endpoint host/port, AllowedIPs, DNS used in generated `.conf`/QR — saved atomically to `config.ini` followed by a detached self-restart (`sleep 2; systemctl restart wg-admin`) so all gunicorn workers reload; skipped under `TESTING`.
  - **Key rotation**: one-click server private key rotation (`wg genkey`) with explicit confirm warning; peers section preserved, `wg-quick` restarted, new public key flashed. Combined with the live `wg show` pubkey lookup, generated `.conf`/QR files pick up the new key automatically — no manual edits.
  - **Service restarts**: "Serviços" card with one-click **Reiniciar wg-admin** and **Reiniciar WireGuard** (`wg-quick@<iface>`), both with confirm dialogs; restarts are scheduled detached (`sleep 1-2` + `systemctl`) so the HTTP response is delivered before the bounce, and peers reconnect on their own.
  - Current server public key displayed with copy button (live interface first, conf-derived fallback); the private key is never rendered.
- **Server Status page** (`/server-status`) — live system metrics for the host running wg-admin: CPU %, memory % + totals, per-mount disk usage, uptime, load average, hostname, kernel, arch, public IP, and a WireGuard card (peers online/total, rx/tx totals + rate). Auto-refreshes every 5 s via fetch, pauses when the tab is hidden. New sidebar link between Password and footer. Metrics collected via `psutil` (new dependency); disk partitions filtered to physical fstypes (excludes tmpfs/proc/sysfs). WG section degrades to `interface_up=false` when `wg0` is down. Network rate computed between consecutive samples kept module-level — first request after startup shows `—`, second shows the real value.
- **Production WSGI server (gunicorn)** — replaced the single-threaded Werkzeug dev server with gunicorn (4 workers). Werkzeug's accept queue was filling under internet scanner load, silently dropping new SYNs even from legitimate admins. Gunicorn workers accept connections in parallel, eliminating the bottleneck.
- **Auth attempt log** — successful and failed logins written as JSON lines (`ts`, `iso`, `success`, `ip`, `user_agent`) to `/wg-admin/secrets/auth.log`. Rotates at 100 KB, keeping up to 5 backups (`auth.log.1` … `auth.log.5`).
- **Per-peer bandwidth quotas** — set `quota_gb` on each peer to auto-suspend when rolling 30-day usage exceeds the limit. Re-enables automatically when usage drops below.
- **Global bandwidth quota** — `[quota] global_quota_gb` in config.ini. Sidebar shows rolling 30-day total. Red banner when exceeded.
- **VPN kill switch** — sidebar button to stop/start `wg-quick@wg0` for emergencies.
- **Live bandwidth charts** — sparkline per peer card, modal with 30-day rx/tx line chart, global stacked area chart at top of `/peers`.
- **WhatsApp share** — modal that asks DDI + phone number, sends `.conf` as attachment via Web Share API on mobile or download+chat on desktop.
- **syncconf hot-reload** — new peers no longer drop active tunnels (zero-downtime create).
- **GitHub repo link** in the sidebar footer (opens in new tab with `rel="noopener"`).

### Fixed
- **Responsive layout rebuilt for mobile** — the VPN kill-switch block (`.sidebar-vpn`) was added after the original responsive section and had no mobile rules: its stacked column + full-width button broke the horizontal top bar, and without `flex-wrap` the whole bar overflowed horizontally on phones. Now: top bar wraps gracefully; VPN status/quota/toggle render as inline pills; GitHub link is icon-only; grids (`peer-grid`, `stats`, `metric-grid`, `info-grid`) use `minmax(min(Npx, 100%), 1fr)` so they can never overflow narrow viewports; QR image is fluid (`max-width: 280px`, no fixed 280×280); modals get page padding, `max-height` + vertical scroll (usable in landscape); ≤480px tightens `h1`, subtitle, peer meta and modal padding.

### Changed
- **`_get_server_public_key()` now prefers live `wg show` over config.ini** — the server public key embedded in generated `.conf`/QR files is read from the running interface (source of truth) first, falling back to the static `server_public_key` in `config.ini` only when `wg show` is unavailable. Previously a static (possibly stale) config.ini value won, so rotating the server keypair silently produced broken client configs until config.ini was manually edited.
- **Rate-limit block window increased from 5 min to 30 min** — 5 failed attempts within 60 s now blocks the source IP for 30 minutes instead of 5. Persists across socket-activated restarts as before.
- **Last-handshake time on the peers card now formats as `s`/`min`/`h`/`d`** instead of always showing seconds (e.g. "há 5min" instead of "há 300s"). Falls through `60s → 1min`, `3600s → 1h`, `86400s → 1d`.
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
- **`install.sh` now runs `systemctl enable wg-quick@wg0`** (previously only `start`) so the VPN auto-starts on reboot. Also installs a systemd drop-in at `/etc/systemd/system/wg-quick@wg0.service.d/10-wait-for-network.conf` that adds `After=network-online.target` + `ExecStartPre=/bin/sleep 3` to avoid boot races where the interface tries to come up before the network is fully ready.

### Tests
- 185 total (up from 172), 90% coverage across:
  - `serverstats.py` — new module, 9 tests covering CPU/MEM/disk collection, tmpfs filtering, unreadable mounts, wg-down degradation, network rate first/second-call delta, online-peer counting, public IP fallback, uptime
  - `app.py` — added 4 route tests for `/server-status` and `/api/server-status` (auth required + JSON response shape)
- Previous: 172 total (up from 170), 89% coverage across:
  - `app.py` — added tests for the `format_duration` filter (10 cases: seconds/minutes/hours/days boundaries + non-numeric pass-through)
- Previous: 170 total (up from 161), 89% coverage across:
  - `authlog.py` — new module, 6 tests for write / append / rotation / shift
  - `ratelimit.py` — added test asserting `BLOCK_SEC == 1800`
  - `app.py` — added tests asserting successful and failed logins are logged

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
