# Changelog

All notable changes to wg-admin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] — 2026-11-01

### Added
- **English UI — now the default language — with Portuguese available** (`src/wg_admin/i18n.py`, dependency-free): every template, flash message and inline-JS string goes through `t()`, with English as the catalog key (missing keys fall back to English). Placeholders use `{name}` syntax; JS strings are injected with `|tojson`.
  - **Install-time choice**: `install.sh` asks "Panel language / Idioma do painel" (English default, Português option) on fresh installs.
  - **Existing installs are migrated to `language = pt`** — a config without the new key is pinned to Portuguese on the first re-run of `install.sh`, so nobody's panel changes language under them. New installs default to English.
  - **Settings page → "Language · Idioma" card**: pick the language, save; applied via the existing config-save + self-restart flow. The card title and options are intentionally bilingual so you can always find it.
  - `<html lang>` now reflects the active language; the `data-confirm` dialog handler moved from `peers.html` to `base.html` (used by settings forms too, and the settings confirms were migrated off inline `onsubmit` JS).
  - A catalog-completeness test greps every `t('...')` in templates and `app.py` and fails if a key lacks a Portuguese translation; another test asserts placeholder parity between keys and translations.

## [0.3.1] — 2026-11-01

### Fixed
- **Brand no longer reads "wg wg-admin"** — the sidebar brand and the login logo showed the red `wg` badge followed by the wordmark `wg-admin`, which reads as a duplicated name. Both now show just **wg-admin** (red accent on the dash); the `wg` badge lives on in the favicon.

## [0.3.0] — 2026-11-01

### Security
- **State transactions are now serialized end-to-end** — `state.update_state(path, key, mutator)` holds the flock across load → mutate → save, so concurrent gunicorn workers and the bandwidth timer can no longer clobber each other (previously a stale read-modify-write could resurrect a disabled/deleted peer, duplicate an allocated IP, or drop a quota suspension). All mutating routes and the quota check use it.
- **Backup rotation no longer makes the state file disappear** — `save_state` used to move the live file to `.bak` before writing the new one, so a concurrent lockless reader saw "no file" and got an **empty state**. The new order writes the fresh envelope over the live file via `os.replace` and only then refreshes `.bak`/`.bak1` from the previous bytes. Temp files are per-writer unique (`pid + monotonic_ns`), still 0600.
- **Failed applies are now retried, not silently dropped** — when `apply_state_to_wg` fails (create/delete/toggle/regenerate/quota/settings), the state is flagged `pending_apply`; the bandwidth timer retries it every 5 min until it succeeds, and `/peers` shows a persistent red banner meanwhile. Fixes the quota-suspension case where the state said SUSPENSO but WireGuard kept authorizing the peer. The timer's systemd unit now has `ReadWritePaths=/wg-admin /etc/wireguard` (quota enforcement needs to write the conf — `strict` without it made every apply fail on systemd ≥ 235).
- **Changing the password now revokes all sessions** — sessions are pinned to a `session_epoch` counter in `auth.ini` (bumped on every password change). Old cookies — including a stolen copy that survives logout — die with the password. Note: upgrading to 0.3.0 logs every existing session out once.
- **Input validation closes config-injection paths** — peer names must be single-line ≤100 chars; private keys pasted in "Editar" must be well-formed base64 **and** derive the peer's exact public key; `endpoint_host` must be a real hostname/IP (no whitespace/newlines). WireGuard conf generation additionally collapses whitespace in `# name:` comment headers, so a crafted name can never break out into an active directive (`PostUp`, …). `quota_gb` rejects `nan`/`inf`.
- **XSS sink removed from peer cards** — the confirm dialogs interpolated the peer name inside a JS string in an `onsubmit` attribute (HTML-entity decoding lets the string be broken out). Dialogs now live in `data-confirm` attributes — properly HTML-escaped — read by a small delegated listener.
- **`.conf`/QR responses (private keys!) are `Cache-Control: no-store`** — browsers/proxies can no longer cache them; download filename uses `secure_filename`.
- **Security headers on every response** — CSP (`default-src 'self'`, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Strict-Transport-Security` (1 year). Inline handlers remain (hence `unsafe-inline` in script-src for now); a strict-CSP refactor is future work.
- **`install.sh` no longer accepts an empty/short admin password** (min 8 chars, confirmed in a retry loop) and passes it to the hasher via **stdin** instead of argv (argv is visible in `/proc`). Runs under `umask 077`, so `master.key`/`session.key`/`auth.ini` are 0600 from the instant they're created — no world-readable window.
- **`uninstall.sh --keep-state` no longer exports secrets world-readable** — the tarball (master/session keys!) is created under `umask 077` → 0600, services are stopped *before* the backup so state is quiesced, and a failed/empty backup aborts the uninstall instead of `rm -rf`-ing the only copy.
- **wg conf rewrites preserve file mode** — `update_interface_in_conf` used to recreate a 0600 conf as 0644 (umask-dependent); it now writes via a 0600 temp and restores the original mode on replace. `apply_state_to_wg` does the same.
- **CSRF token comparison is constant-time** (`hmac.compare_digest`); `/vpn/toggle` only honors same-origin `Referer` (no more open-redirect via a foreign referrer header); request bodies are capped at 256 KB (`MAX_CONTENT_LENGTH`).
- **gunicorn floor raised to `>=23.0`** — 21.x/22.x are affected by CVE-2024-6827 and CVE-2024-1135 (HTTP request smuggling, fixed in 22.0).
- **CI actions pinned by commit SHA** (checkout, setup-python, upload-artifact) — tag-floating `uses:` is a supply-chain risk.
- Added `SECURITY.md` with a private vulnerability reporting policy.

### Removed
- **WhatsApp share button and modal** — dropped from the peers page along with its JS (`static/js/whatsapp-modal.js`). `.conf` download and QR remain.

### Fixed
- **Peer card action buttons standardized** — always two rows (row 1: `.conf`, `QR Code`, `Rodar chave`/`Gerar chaves`, `Editar`; row 2: `Ativar`/`Desativar`, `Apagar`) with uniform fixed height; the previous mixed `<a>`/`<button>` inline layout rendered mismatched heights (UA `line-height` differs between the two). Emojis dropped from button labels.
- **Peer toggle form was missing its CSRF token** — the Ativar/Desativar form posted without `csrf_token` and would 403 against the CSRF check.

### Added
- **In-panel self-update** — the sidebar checks GitHub (server-side, cached for 1 h) and shows an update button next to the version whenever a newer tag exists. One click + confirm downloads that tag's tarball and re-runs the idempotent `install.sh` inside a transient systemd unit (`wg-admin-selfupdate`, `journalctl -u wg-admin-selfupdate -f` to watch). The transient unit survives the panel restart that ends the install — peers are untouched and the admin session persists (the page polls and reloads itself on the new version). Tags are strictly validated (`vX.Y.Z`, newer than current) and the trigger is CSRF-protected.
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
