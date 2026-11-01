"""Flask app: routes, auth, CSRF, rate limit."""
from __future__ import annotations

import hmac
import ipaddress
import json
import math
import os
import re
import secrets as pysecrets
import subprocess
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request,
    session, url_for,
)
from werkzeug.utils import secure_filename

from . import authlog, bandwidth, confgen, config, crypto, i18n, ratelimit, serverstats, state, wg
from .version import __version__

# These paths are patched in tests via monkeypatch.
SECRETS_DIR = Path("/wg-admin/secrets")
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
RATELIMIT_PATH = Path("/wg-admin/secrets/auth_ratelimit.json")
AUTHLOG_PATH = Path("/wg-admin/secrets/auth.log")
BANDWIDTH_PATH = Path("/wg-admin/bandwidth.json")
WG_CONF_DIR = Path("/etc/wireguard")

_NAME_MAX = 100
_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _read_auth_ini() -> tuple[str, int]:
    """Return (password_hash, session_epoch) from secrets/auth.ini.

    Missing file or missing fields degrade to ("", 0) so verification
    fails closed instead of raising a 500.
    """
    try:
        text = (SECRETS_DIR / "auth.ini").read_text()
    except OSError:
        return "", 0
    phc = ""
    epoch = 0
    for line in text.splitlines():
        if line.startswith("password_hash ="):
            phc = line.split("=", 1)[1].strip()
        elif line.startswith("session_epoch ="):
            try:
                epoch = int(line.split("=", 1)[1].strip())
            except ValueError:
                epoch = 0
    return phc, epoch


def _write_auth_ini(phc_hash: str, epoch: int) -> None:
    """Atomically rewrite secrets/auth.ini as 0600."""
    path = SECRETS_DIR / "auth.ini"
    tmp = Path(f"{path}.tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"password_hash = {phc_hash}\n")
        f.write(f"session_epoch = {epoch}\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _valid_peer_name(name: str) -> bool:
    """Single line, non-empty, bounded length (no conf/JS breakout)."""
    return (
        bool(name)
        and len(name) <= _NAME_MAX
        and "\r" not in name
        and "\n" not in name
    )


def _valid_wg_private_key(priv: str, expected_pub: str) -> bool:
    """Well-formed base64 key whose derived pubkey matches the peer."""
    if not _WG_KEY_RE.match(priv or ""):
        return False
    try:
        return wg.wg_pubkey(priv) == expected_pub
    except Exception:
        return False


def _valid_endpoint_host(host: str) -> bool:
    """A single-line IP or hostname — no whitespace or control chars."""
    if not host or len(host) > 253 or any(c in host for c in " \t\r\n"):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?$", host) is not None


# ─── Self-update ────────────────────────────────────────────────────────────

UPDATE_REPO = "joaopaulooooo/wg-admin"
UPDATE_REPO_URL = f"https://github.com/{UPDATE_REPO}"
UPDATE_CLONE_DIR = Path("/wg-admin/.selfupdate")
UPDATE_UNIT = "wg-admin-selfupdate"
UPDATE_CHECK_TTL = 3600.0  # cache GitHub lookups for 1h (unauth API = 60 req/h)
_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _version_tuple(tag: str):
    """'v1.2.3' -> (1, 2, 3); anything else -> None."""
    m = _TAG_RE.match(tag or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _fetch_latest_tag():
    """Latest semver tag from the GitHub API. Raises on network errors."""
    url = f"https://api.github.com/repos/{UPDATE_REPO}/tags?per_page=20"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"wg-admin/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=4) as resp:
        tags = json.loads(resp.read().decode("utf-8"))
    best = None
    for t in tags if isinstance(tags, list) else []:
        vt = _version_tuple(t.get("name", ""))
        if vt and (best is None or vt > best):
            best = vt
    return f"v{best[0]}.{best[1]}.{best[2]}" if best else None


_update_check_cache = {"ts": 0.0, "payload": None}


def _update_check_payload() -> dict:
    """Cached {current, latest, update_available, error} for the sidebar."""
    cache = _update_check_cache
    now = time.monotonic()
    if cache["payload"] is not None and now - cache["ts"] < UPDATE_CHECK_TTL:
        return cache["payload"]

    current_t = _version_tuple("v" + __version__)
    error = None
    try:
        latest = _fetch_latest_tag()
    except Exception:
        latest, error = None, "lookup_failed"
    latest_t = _version_tuple(latest) if latest else None

    payload = {
        "current": __version__,
        "latest": latest,
        "update_available": bool(latest_t and current_t and latest_t > current_t),
        "error": error,
    }
    cache.update(ts=now, payload=payload)
    return payload


def _schedule_update(app: Flask, tag: str) -> bool:
    """Self-update: download the tag's tarball, run its idempotent install.sh.

    Runs in a transient systemd unit — NOT a detached child of this worker —
    because install.sh restarts wg-admin at the end, and systemd's default
    KillMode would kill any process still inside this service's cgroup
    mid-install. Fails soft: on any scheduling error the old panel keeps
    running untouched.
    """
    if app.config.get("TESTING"):
        return True
    script = (
        f"rm -rf {UPDATE_CLONE_DIR} && mkdir -p {UPDATE_CLONE_DIR} && "
        f"curl -fsSL --max-time 120 {UPDATE_REPO_URL}/archive/refs/tags/{tag}.tar.gz "
        f"| tar xz -C {UPDATE_CLONE_DIR} --strip-components=1 && "
        f"bash {UPDATE_CLONE_DIR}/install.sh; "
        f"rm -rf {UPDATE_CLONE_DIR}"
    )
    try:
        proc = subprocess.run(
            ["systemd-run", "--collect", "--unit", UPDATE_UNIT,
             "--description", f"wg-admin self-update to {tag}",
             "bash", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return True
        app.logger.warning("systemd-run failed: %s", proc.stderr.strip())
    except Exception:
        app.logger.exception("Could not systemd-run the self-update")
    # Fallback: detached shell. Good enough for the pre-restart part of
    # install.sh; the tail after the wg-admin restart may be cut short.
    _schedule_shell(app, script)
    return True


def format_duration(seconds):
    """Format seconds as a coarse human duration: 30s, 5min, 3h, 2d.

    Non-numeric input (None, '?') is passed through unchanged so templates
    can use the filter defensively without extra guards.
    """
    if not isinstance(seconds, (int, float)):
        return seconds
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _schedule_shell(app: Flask, shell_cmd: str) -> None:
    """Run a detached shell command after the current response completes.

    start_new_session double-forks so the command survives (and doesn't kill)
    the in-flight HTTP response. Skipped under TESTING.
    """
    if app.config.get("TESTING"):
        return
    try:
        subprocess.Popen(
            ["sh", "-c", shell_cmd],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        app.logger.exception("Could not schedule shell command: %s", shell_cmd)


def _schedule_self_restart(app: Flask) -> None:
    """Restart wg-admin in ~2s so all gunicorn workers reload config.ini."""
    _schedule_shell(app, "sleep 2; systemctl restart wg-admin")


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent.parent / "templates"),
        static_folder=str(Path(__file__).parent.parent.parent / "static"),
    )

    cfg = config.load_config(CONFIG_PATH)
    master_key = (SECRETS_DIR / "master.key").read_bytes()
    session_key = (SECRETS_DIR / "session.key").read_bytes()

    # Server-wide UI language (config.ini [server] language; 'en' default).
    language = i18n.normalize_language(cfg["server"].get("language", i18n.DEFAULT_LANGUAGE))

    def t(text: str, /, **kwargs) -> str:
        return i18n.translate(language, text, **kwargs)

    app.secret_key = session_key
    app.config.update(
        SESSION_COOKIE_NAME="wg_admin_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=cfg["server"].getint("session_lifetime_seconds", 3600),
        SESSION_PERMANENT=True,
        MAX_CONTENT_LENGTH=256 * 1024,
        MASTER_KEY=master_key,
        CONFIG=cfg,
    )

    @app.after_request
    def set_security_headers(resp):
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "DELETE"):
            # Login form is special: csrf token is generated AT login.
            # Allow login POST without csrf (it has nothing to protect yet).
            # Logout is also exempt: it only destroys the session and the
            # worst case of a CSRF-forced logout is the user has to log in again.
            if request.endpoint in ("login", "logout"):
                return
            token = session.get("csrf_token")
            # No session token means the user is unauthenticated — let
            # login_required handle the redirect rather than 403-ing on CSRF.
            if token is None:
                return
            if not hmac.compare_digest(token, request.form.get("csrf_token", "")):
                abort(403)

    from . import quota as quota_mod

    @app.context_processor
    def inject_globals():
        try:
            bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
            global_q = cfg["quota"].getfloat("global_quota_gb", 0)
            return {
                "vpn_active": wg.wg_interface_active(cfg["wg"]["interface"]),
                "global_used_gb": quota_mod.global_usage_gb(bw),
                "global_quota_gb": global_q,
                "global_exceeded": quota_mod.global_quota_exceeded(bw, global_q),
                "version": __version__,
                "lang": language,
            }
        except Exception:
            # If anything fails (bandwidth.json missing, etc.) — don't break every page
            return {
                "vpn_active": False,
                "global_used_gb": 0.0,
                "global_quota_gb": 0.0,
                "global_exceeded": False,
                "version": __version__,
                "lang": language,
            }

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("admin"):
                return redirect(url_for("login"))
            # Sessions are pinned to a server-side epoch bumped on password
            # change: an old (possibly stolen) cookie dies with the password.
            _, epoch = _read_auth_ini()
            if session.get("epoch") != epoch:
                session.clear()
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapper

    app.jinja_env.filters["duration"] = format_duration
    app.jinja_env.globals["t"] = t

    @app.route("/")
    @login_required
    def index():
        return redirect(url_for("peers_list"))

    def _apply_or_flag(s: dict, mode: str) -> bool:
        """Apply state to WireGuard; record pending_apply in state on failure
        so the bandwidth timer retries and the UI shows a warning banner."""
        try:
            wg.apply_state_to_wg(s, cfg, mode=mode)
        except Exception:
            app.logger.exception("Failed to apply state to wg")
            try:
                state.update_state(
                    STATE_PATH, app.config["MASTER_KEY"],
                    lambda st: st.update(pending_apply=True),
                )
            except Exception:
                app.logger.exception("Could not flag pending_apply")
            return False
        if s.get("pending_apply"):
            try:
                state.update_state(
                    STATE_PATH, app.config["MASTER_KEY"],
                    lambda st: st.update(pending_apply=False),
                )
            except Exception:
                app.logger.exception("Could not clear pending_apply")
        return True

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            client_ip = request.remote_addr or "unknown"
            if ratelimit.is_blocked(RATELIMIT_PATH, client_ip):
                abort(429)

            password = request.form.get("password", "")
            phc_hash, epoch = _read_auth_ini()

            user_agent = request.headers.get("User-Agent", "")
            if crypto.verify_password(password, phc_hash):
                session.clear()
                session["admin"] = True
                session["epoch"] = epoch
                session["csrf_token"] = pysecrets.token_urlsafe(32)
                session.permanent = True
                ratelimit.clear(RATELIMIT_PATH, client_ip)
                authlog.log_attempt(
                    AUTHLOG_PATH, success=True,
                    client_ip=client_ip, user_agent=user_agent,
                )
                return redirect(url_for("peers_list"))
            else:
                ratelimit.record_fail(RATELIMIT_PATH, client_ip)
                authlog.log_attempt(
                    AUTHLOG_PATH, success=False,
                    client_ip=client_ip, user_agent=user_agent,
                )
                flash(t("Invalid credentials."), "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            current = request.form.get("current", "")
            new = request.form.get("new", "")
            confirm = request.form.get("confirm", "")

            current_hash, epoch = _read_auth_ini()
            if not crypto.verify_password(current, current_hash):
                flash(t("Current password is incorrect."), "error")
                return render_template("change_password.html"), 400

            if len(new) < 8:
                flash(t("New password must be at least 8 characters."), "error")
                return render_template("change_password.html"), 400

            if new != confirm:
                flash(t("The new passwords do not match."), "error")
                return render_template("change_password.html"), 400

            # Bumping the epoch invalidates every existing session,
            # including this one — the admin logs in again everywhere.
            _write_auth_ini(crypto.hash_password(new), epoch + 1)
            session.clear()
            flash(t("Password updated. Sign in again."), "success")
            return redirect(url_for("login"))
        return render_template("change_password.html")

    @app.route("/peers")
    @login_required
    def peers_list():
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        statuses_by_key = {}
        try:
            statuses = wg.wg_show_dump(cfg["wg"]["interface"])
            statuses_by_key = {st.public_key: st for st in statuses}
        except Exception:
            app.logger.warning("wg show failed", exc_info=True)

        # Compute is_online + bandwidth per peer, pass as peer_views list
        import time as _time
        now_ts = int(_time.time())
        ONLINE_WINDOW = 180  # WireGuard re-handshake interval

        # Build a normalized lookup (strip whitespace) for fuzzy match
        normalized_statuses = {k.strip(): v for k, v in statuses_by_key.items()}

        bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
        peer_views = []
        connected_count = 0
        for peer in s["peers"]:
            pub = peer.get("public_key", "").strip()
            # Try exact match first, then normalized (whitespace-insensitive)
            status = statuses_by_key.get(pub)
            if status is None and pub:
                status = normalized_statuses.get(pub)
            is_online = bool(
                status
                and status.latest_handshake
                and status.latest_handshake > now_ts - ONLINE_WINDOW
            )
            if is_online:
                connected_count += 1
            bw_stats = bandwidth.get_peer_stats(bw, pub)

            # Sparkline: last 30 days of rx+tx
            today = datetime.now(timezone.utc).date()
            dates_30 = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
            peer_bw = bw.get("peers", {}).get(pub, {})
            daily = peer_bw.get("daily", {})
            sparkline_values = [
                daily.get(d, {"rx": 0}).get("rx", 0) + daily.get(d, {"tx": 0}).get("tx", 0)
                for d in dates_30
            ]
            sparkline = bandwidth.sparkline_path(sparkline_values)

            # Quota display
            quota_gb = peer.get("quota_gb", 0.0)
            used_30d_gb = (bw_stats["thirty_day_rx"] + bw_stats["thirty_day_tx"]) / (1024**3)
            if quota_gb > 0:
                quota_pct = min(100, (used_30d_gb / quota_gb) * 100)
                if peer.get("quota_suspended") or quota_pct >= 95:
                    quota_class = "danger"
                elif quota_pct >= 70:
                    quota_class = "warn"
                else:
                    quota_class = "ok"
            else:
                quota_pct = None
                quota_class = None

            peer_views.append({
                "peer": peer,
                "status": status,
                "is_online": is_online,
                "wg_pubkeys": list(statuses_by_key.keys()),
                "bandwidth": {
                    "total_rx": bandwidth.format_bytes(bw_stats["total_rx"]),
                    "total_tx": bandwidth.format_bytes(bw_stats["total_tx"]),
                    "thirty_day_rx": bandwidth.format_bytes(bw_stats["thirty_day_rx"]),
                    "thirty_day_tx": bandwidth.format_bytes(bw_stats["thirty_day_tx"]),
                    "first_seen": bw_stats["first_seen"],
                },
                "sparkline": sparkline,
                "quota_gb": quota_gb,
                "used_30d_gb": used_30d_gb,
                "quota_pct": quota_pct,
                "quota_class": quota_class,
            })

        return render_template(
            "peers.html",
            peer_views=peer_views,
            connected_count=connected_count,
            total_peers=len(s["peers"]),
            imported_count=sum(1 for p in s["peers"] if not p.get("private_key_enc")),
            now_ts=now_ts,
            pending_apply=s.get("pending_apply", False),
        )

    @app.route("/peers/new", methods=["GET", "POST"])
    @login_required
    def peer_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            notes = request.form.get("notes", "").strip()
            quota_raw = request.form.get("quota_gb", "").strip()
            try:
                quota_gb = float(quota_raw) if quota_raw else 0.0
            except ValueError:
                quota_gb = 0.0
            if not _valid_peer_name(name):
                flash(t("Invalid name (required, max 100 characters, no line breaks)."), "error")
                return render_template("peer_form.html")
            if not math.isfinite(quota_gb) or quota_gb < 0:
                flash(t("Invalid quota (non-negative number)."), "error")
                return render_template("peer_form.html")

            priv, pub = wg.wg_genkey()

            def mut(st):
                ip = state.allocate_ip(st, cfg["wg"]["subnet"], cfg["wg"]["server_ip"])
                st["peers"].append({
                    "id": state.new_peer_id(),
                    "name": name,
                    "notes": notes,
                    "public_key": pub,
                    "private_key_enc": state.encrypt_private_key(priv, app.config["MASTER_KEY"]),
                    "ip": ip,
                    "disabled": False,
                    "quota_gb": quota_gb,
                    "quota_suspended": False,
                    "quota_state_updated_at": None,
                    "created_at": state.utc_now_iso(),
                })
                return ip

            s, ip = state.update_state(STATE_PATH, app.config["MASTER_KEY"], mut)
            if not _apply_or_flag(s, mode="syncconf"):
                flash(t("Peer created in state, but the apply to wg failed — it will be retried automatically"), "error")
                return redirect(url_for("peers_list"))
            flash(t("Peer created: {name} ({ip})", name=name, ip=ip), "success")
            return redirect(url_for("peers_list"))
        return render_template("peer_form.html")

    @app.route("/peers/<peer_id>/delete", methods=["POST"])
    @login_required
    def peer_delete(peer_id):
        def mut(st):
            if not state.remove_peer(st, peer_id):
                raise KeyError(peer_id)
        try:
            s, _ = state.update_state(STATE_PATH, app.config["MASTER_KEY"], mut)
        except KeyError:
            abort(404)
        if not _apply_or_flag(s, mode="restart"):
            flash(t("Peer removed from state, but the apply failed — it will be retried automatically"), "error")
        flash(t("Peer deleted"), "success")
        return redirect(url_for("peers_list"))

    @app.route("/peers/<peer_id>/toggle", methods=["POST"])
    @login_required
    def peer_toggle(peer_id):
        def mut(st):
            peer = state.find_peer_by_id(st, peer_id)
            if peer is None:
                raise KeyError(peer_id)
            peer["disabled"] = not peer.get("disabled", False)
        try:
            s, _ = state.update_state(STATE_PATH, app.config["MASTER_KEY"], mut)
        except KeyError:
            abort(404)
        if not _apply_or_flag(s, mode="restart"):
            flash(t("Toggle registered but the apply failed — it will be retried automatically"), "error")
        return redirect(url_for("peers_list"))

    def _get_server_public_key() -> str:
        """Get server public key — live `wg show` first (source of truth, survives
        key rotation), fall back to config.ini, else empty."""
        try:
            key = wg.wg_server_public_key(cfg["wg"]["interface"])
            if key:
                return key
        except Exception:
            app.logger.warning("Could not get server public key via wg show", exc_info=True)
        key = cfg["wg"].get("server_public_key", "").strip()
        if key:
            app.logger.info("server_public_key: using static value from config.ini (wg show unavailable)")
        return key

    @app.route("/peers/<peer_id>/conf")
    @login_required
    def peer_conf(peer_id):
        from flask import Response
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        if not peer.get("private_key_enc"):
            flash(t("This peer was imported from wg0.conf — the private key only exists on the client device. A .conf or QR cannot be generated. Create a new peer or delete and re-import this one manually."), "error")
            return redirect(url_for("peers_list"))
        peer_cfg = confgen.PeerConfig(
            private_key=state.decrypt_private_key(peer["private_key_enc"], app.config["MASTER_KEY"]),
            address=peer["ip"] + "/32",
            dns=config.get_dns_list(cfg),
            server_public_key=_get_server_public_key(),
            endpoint=f"{cfg['peer_defaults']['endpoint_host']}:{cfg['peer_defaults']['endpoint_port']}",
            allowed_ips=config.get_allowed_ips_list(cfg),
        )
        text = confgen.render_conf(peer_cfg)
        fname = secure_filename(f"wg-{peer['name']}") or "wg-peer"
        return Response(
            text,
            mimetype="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}.conf"',
                "Cache-Control": "no-store",
            },
        )

    @app.route("/peers/<peer_id>/qr")
    @login_required
    def peer_qr(peer_id):
        from flask import Response
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        if not peer.get("private_key_enc"):
            flash(t("Imported peer — private key not available. QR cannot be generated."), "error")
            return redirect(url_for("peers_list"))
        peer_cfg = confgen.PeerConfig(
            private_key=state.decrypt_private_key(peer["private_key_enc"], app.config["MASTER_KEY"]),
            address=peer["ip"] + "/32",
            dns=config.get_dns_list(cfg),
            server_public_key=_get_server_public_key(),
            endpoint=f"{cfg['peer_defaults']['endpoint_host']}:{cfg['peer_defaults']['endpoint_port']}",
            allowed_ips=config.get_allowed_ips_list(cfg),
        )
        text = confgen.render_conf(peer_cfg)
        png = confgen.render_qr_png(text)
        return Response(png, mimetype="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.route("/api/bandwidth/<peer_id>")
    @login_required
    def api_peer_bandwidth(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
        peer_bw = bw.get("peers", {}).get(peer.get("public_key", ""), {})
        daily = peer_bw.get("daily", {})

        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        return jsonify({
            "dates": dates,
            "rx": [daily.get(d, {"rx": 0})["rx"] for d in dates],
            "tx": [daily.get(d, {"tx": 0})["tx"] for d in dates],
        })

    @app.route("/api/bandwidth/global")
    @login_required
    def api_global_bandwidth():
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)

        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        dates_set = set(dates)

        # Build per-peer total over the 30-day window, sorted desc
        peer_totals = []  # (name, pubkey, total_bytes)
        for peer in s["peers"]:
            pub = peer.get("public_key", "")
            peer_bw = bw.get("peers", {}).get(pub, {})
            total = 0
            for d, v in peer_bw.get("daily", {}).items():
                if d in dates_set:
                    total += v.get("rx", 0) + v.get("tx", 0)
            peer_totals.append((peer.get("name", "?"), pub, total))

        peer_totals.sort(key=lambda x: x[2], reverse=True)

        series = []
        # Top 5 named peers (only those with > 0 bytes)
        top = [p for p in peer_totals[:5] if p[2] > 0]
        # Aggregate "outros" = rest
        outros_total = sum(p[2] for p in peer_totals[5:])

        for name, pub, _ in top:
            peer_bw = bw.get("peers", {}).get(pub, {})
            daily = peer_bw.get("daily", {})
            series.append({
                "name": name,
                "data": [daily.get(d, {"rx": 0, "tx": 0}).get("rx", 0) +
                         daily.get(d, {"rx": 0, "tx": 0}).get("tx", 0)
                         for d in dates],
            })
        if outros_total > 0:
            outros_per_day = [0] * 30
            for name, pub, _ in peer_totals[5:]:
                peer_bw = bw.get("peers", {}).get(pub, {})
                daily = peer_bw.get("daily", {})
                for i, d in enumerate(dates):
                    v = daily.get(d, {"rx": 0, "tx": 0})
                    outros_per_day[i] += v.get("rx", 0) + v.get("tx", 0)
            series.append({"name": "outros", "data": outros_per_day})

        return jsonify({"dates": dates, "series": series})

    @app.route("/server-status")
    @login_required
    def server_status():
        return render_template("server_status.html")

    @app.route("/api/server-status")
    @login_required
    def api_server_status():
        return jsonify(serverstats.collect(cfg))

    # ─── WireGuard settings ────────────────────────────────────────────

    def _conf_path() -> Path:
        return WG_CONF_DIR / f"{cfg['wg']['interface']}.conf"

    def _read_iface() -> dict:
        try:
            p = _conf_path()
            if not p.exists():
                return {}
            return wg.parse_wg_conf(p.read_text())["interface"]
        except OSError:
            return {}

    def _current_server_pubkey() -> str:
        try:
            key = wg.wg_server_public_key(cfg["wg"]["interface"])
            if key:
                return key
        except Exception:
            pass
        try:
            return wg.wg_server_public_key_from_conf(str(_conf_path()))
        except Exception:
            return ""

    @app.route("/settings")
    @login_required
    def settings_page():
        return render_template(
            "settings.html",
            iface=_read_iface(),
            current_pub=_current_server_pubkey(),
            peer_defaults=cfg["peer_defaults"],
            interface=cfg["wg"]["interface"],
            language=language,
            languages=i18n.LANGUAGES,
        )

    @app.route("/settings/interface", methods=["POST"])
    @login_required
    def settings_interface():
        address = request.form.get("address", "").strip()
        listen_port = request.form.get("listen_port", "").strip()
        mtu = request.form.get("mtu", "").strip()
        post_up = [l.strip() for l in request.form.get("post_up", "").splitlines() if l.strip()]
        post_down = [l.strip() for l in request.form.get("post_down", "").splitlines() if l.strip()]

        try:
            ipaddress.ip_network(address, strict=False)
        except ValueError:
            flash(t("Invalid address — use CIDR format, e.g. 10.0.0.1/24"), "error")
            return redirect(url_for("settings_page"))
        if not listen_port.isdigit() or not (1 <= int(listen_port) <= 65535):
            flash(t("Invalid ListenPort (1-65535)"), "error")
            return redirect(url_for("settings_page"))
        if mtu and (not mtu.isdigit() or not (1280 <= int(mtu) <= 9000)):
            flash(t("Invalid MTU (1280-9000, or empty)"), "error")
            return redirect(url_for("settings_page"))

        updates = {
            "Address": address,
            "ListenPort": listen_port,
            "MTU": mtu,
            "PostUp": post_up,
            "PostDown": post_down,
        }
        try:
            wg.update_interface_in_conf(_conf_path(), updates)
            s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        except FileNotFoundError:
            flash(t("Conf not found: {path}", path=_conf_path()), "error")
            return redirect(url_for("settings_page"))
        except Exception:
            app.logger.exception("Failed to update interface settings")
            flash(t("Failed to apply — conf unchanged or partially applied, check logs"), "error")
            return redirect(url_for("settings_page"))
        if not _apply_or_flag(s, mode="restart"):
            flash(t("Failed to apply to WireGuard — it will be retried automatically (check logs)"), "error")
            return redirect(url_for("settings_page"))
        flash(t("Interface updated and wg-quick restarted — peers reconnect in seconds"), "success")
        return redirect(url_for("settings_page"))

    @app.route("/settings/peer-defaults", methods=["POST"])
    @login_required
    def settings_peer_defaults():
        endpoint_host = request.form.get("endpoint_host", "").strip()
        endpoint_port = request.form.get("endpoint_port", "").strip()
        allowed_ips = request.form.get("allowed_ips", "").strip()
        dns = request.form.get("dns", "").strip()

        if not _valid_endpoint_host(endpoint_host):
            flash(t("Invalid endpoint host (hostname or IP, no spaces or line breaks)."), "error")
            return redirect(url_for("settings_page"))
        if not endpoint_port.isdigit() or not (1 <= int(endpoint_port) <= 65535):
            flash(t("Invalid endpoint port (1-65535)"), "error")
            return redirect(url_for("settings_page"))
        try:
            for cidr in [c.strip() for c in allowed_ips.split(",") if c.strip()]:
                ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            flash(t("Invalid AllowedIPs — comma-separated list of CIDRs"), "error")
            return redirect(url_for("settings_page"))
        try:
            for ip in [d.strip() for d in dns.split(",") if d.strip()]:
                ipaddress.ip_address(ip)
        except ValueError:
            flash(t("Invalid DNS — comma-separated list of IPs (or empty)"), "error")
            return redirect(url_for("settings_page"))

        cfg["peer_defaults"]["endpoint_host"] = endpoint_host
        cfg["peer_defaults"]["endpoint_port"] = endpoint_port
        cfg["peer_defaults"]["allowed_ips"] = allowed_ips
        cfg["peer_defaults"]["dns"] = dns
        try:
            config.save_config(CONFIG_PATH, cfg)
        except Exception:
            app.logger.exception("Failed to save config.ini")
            flash(t("Failed to write config.ini — check logs"), "error")
            return redirect(url_for("settings_page"))
        flash(t("Defaults saved — wg-admin restarts in a few seconds to reload"), "success")
        _schedule_self_restart(app)
        return redirect(url_for("settings_page"))

    @app.route("/settings/rotate-key", methods=["POST"])
    @login_required
    def settings_rotate_key():
        priv, pub = wg.wg_genkey()
        try:
            wg.update_interface_in_conf(_conf_path(), {"PrivateKey": priv})
            s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        except FileNotFoundError:
            flash(t("Conf not found: {path}", path=_conf_path()), "error")
            return redirect(url_for("settings_page"))
        except Exception:
            app.logger.exception("Failed to rotate server key")
            flash(t("Failed to rotate the key — check logs (the conf may still have the old key)"), "error")
            return redirect(url_for("settings_page"))
        if not _apply_or_flag(s, mode="restart"):
            flash(t("Key rotated in conf, but the apply to wg failed — it will be retried automatically"), "error")
            return redirect(url_for("settings_page"))
        flash(
            t("Private key rotated. New public key: {pub} — peer .conf/QR files are already generated with the new key; clients must re-import the config.", pub=pub),
            "warning",
        )
        return redirect(url_for("settings_page"))

    @app.route("/settings/restart-wg-admin", methods=["POST"])
    @login_required
    def settings_restart_wg_admin():
        flash(t("wg-admin restart scheduled (~2s) — wait and reload the page"), "warning")
        _schedule_self_restart(app)
        return redirect(url_for("settings_page"))

    @app.route("/settings/restart-wireguard", methods=["POST"])
    @login_required
    def settings_restart_wireguard():
        interface = cfg["wg"]["interface"]
        flash(
            t("wg-quick@{iface} restart scheduled (~1s) — peers disconnect and reconnect on their own", iface=interface),
            "warning",
        )
        _schedule_shell(app, f"sleep 1; systemctl restart wg-quick@{interface}")
        return redirect(url_for("settings_page"))

    # ─── Self-update ────────────────────────────────────────────────────

    @app.route("/api/update/check")
    @login_required
    def api_update_check():
        return jsonify(_update_check_payload())

    @app.route("/settings/update", methods=["POST"])
    @login_required
    def settings_update():
        tag = request.form.get("tag", "").strip()
        tag_t = _version_tuple(tag)
        cur_t = _version_tuple("v" + __version__)
        if not tag_t or not cur_t:
            flash(t("Invalid version tag."), "error")
            return redirect(url_for("settings_page"))
        if tag_t <= cur_t:
            flash(t("You are already on the latest version ({version}).", version=__version__), "success")
            return redirect(url_for("settings_page"))
        if wg.unit_is_active(UPDATE_UNIT):
            flash(t("An update is already in progress."), "error")
            return redirect(url_for("settings_page"))
        _schedule_update(app, tag)
        flash(
            t("Update to {tag} scheduled — the panel restarts by itself at the end (the session is kept; progress in journalctl -u wg-admin-selfupdate -f).", tag=tag),
            "warning",
        )
        return redirect(url_for("peers_list"))

    @app.route("/settings/language", methods=["POST"])
    @login_required
    def settings_language():
        new_lang = request.form.get("language", "").strip()
        if new_lang not in i18n.LANGUAGES:
            flash(t("Invalid language."), "error")
            return redirect(url_for("settings_page"))
        cfg["server"]["language"] = new_lang
        try:
            config.save_config(CONFIG_PATH, cfg)
        except Exception:
            app.logger.exception("Failed to save config.ini")
            flash(t("Failed to write config.ini — check logs"), "error")
            return redirect(url_for("settings_page"))
        flash(t("Language set to {language} — wg-admin restarts in a few seconds.",
                language=i18n.LANGUAGES[new_lang]), "success")
        _schedule_self_restart(app)
        return redirect(url_for("settings_page"))

    @app.route("/peers/<peer_id>/edit", methods=["GET", "POST"])
    @login_required
    def peer_edit(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            notes = request.form.get("notes", "").strip()
            new_priv = request.form.get("private_key", "").strip()
            quota_raw = request.form.get("quota_gb", "").strip()
            try:
                quota_gb = float(quota_raw) if quota_raw else 0.0
            except ValueError:
                quota_gb = 0.0
            if not _valid_peer_name(name):
                flash(t("Invalid name (required, max 100 characters, no line breaks)."), "error")
                return render_template("peer_edit.html", peer=peer)
            if not math.isfinite(quota_gb) or quota_gb < 0:
                flash(t("Invalid quota (non-negative number)."), "error")
                return render_template("peer_edit.html", peer=peer)
            if new_priv and not _valid_wg_private_key(new_priv, peer.get("public_key", "")):
                flash(t("Invalid private key: must be base64 (44 chars) and match the peer public key."), "error")
                return render_template("peer_edit.html", peer=peer)

            def mut(st):
                p = state.find_peer_by_id(st, peer_id)
                if p is None:
                    raise KeyError(peer_id)
                p["name"] = name
                p["notes"] = notes
                p["quota_gb"] = quota_gb
                if new_priv:
                    p["private_key_enc"] = state.encrypt_private_key(
                        new_priv, app.config["MASTER_KEY"])

            try:
                state.update_state(STATE_PATH, app.config["MASTER_KEY"], mut)
            except KeyError:
                abort(404)
            if new_priv:
                flash(t("Private key updated — you can now download .conf and QR"), "success")
            else:
                flash(t("Peer updated"), "success")
            return redirect(url_for("peers_list"))
        return render_template("peer_edit.html", peer=peer)

    @app.route("/peers/<peer_id>/regenerate", methods=["POST"])
    @login_required
    def peer_regenerate(peer_id):
        priv, pub = wg.wg_genkey()

        def mut(st):
            peer = state.find_peer_by_id(st, peer_id)
            if peer is None:
                raise KeyError(peer_id)
            peer["private_key_enc"] = state.encrypt_private_key(priv, app.config["MASTER_KEY"])
            peer["public_key"] = pub
            return peer["name"]

        try:
            s, peer_name = state.update_state(STATE_PATH, app.config["MASTER_KEY"], mut)
        except KeyError:
            abort(404)
        if not _apply_or_flag(s, mode="syncconf"):
            flash(t("Keys generated in state, but the apply to wg failed — it will be retried automatically"), "error")
            return redirect(url_for("peers_list"))
        flash(
            t("New keypair for {name} — download the .conf/QR and re-import on the device; the old config stops working.", name=peer_name),
            "warning",
        )
        return redirect(url_for("peers_list"))

    @app.route("/vpn/toggle", methods=["POST"])
    @login_required
    def vpn_toggle():
        interface = cfg["wg"]["interface"]
        if wg.wg_interface_active(interface):
            subprocess.run(
                ["systemctl", "stop", f"wg-quick@{interface}"],
                capture_output=True, check=True,
            )
            flash(t("VPN deactivated — all peers disconnected"), "warning")
        else:
            subprocess.run(
                ["systemctl", "start", f"wg-quick@{interface}"],
                capture_output=True, check=True,
            )
            flash(t("VPN reactivated"), "success")
        ref = request.referrer
        if ref and ref.startswith(request.host_url):
            return redirect(ref)
        return redirect(url_for("peers_list"))

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message=t("Invalid CSRF token or expired session.")), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message=t("Resource not found.")), 404

    @app.errorhandler(429)
    def too_many(e):
        return render_template("error.html", code=429, message=t("Too many login attempts. Try again later.")), 429

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Internal error")
        return render_template("error.html", code=500, message=t("Internal error.")), 500

    return app


# Module-level app is created lazily on first attribute access so tests can
# monkeypatch SECRETS_DIR/STATE_PATH/CONFIG_PATH/RATELIMIT_PATH before any
# real filesystem access happens.
class _LazyApp:
    _cached = None

    def _get(self):
        if self._cached is None:
            self._cached = create_app()
        return self._cached

    def __getattr__(self, name):
        return getattr(self._get(), name)


app = _LazyApp()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
