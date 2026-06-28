"""Flask app: routes, auth, CSRF, rate limit."""
from __future__ import annotations

import secrets as pysecrets
import subprocess
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request,
    session, url_for,
)

from . import authlog, bandwidth, confgen, config, crypto, ratelimit, state, wg

# These paths are patched in tests via monkeypatch.
SECRETS_DIR = Path("/wg-admin/secrets")
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
RATELIMIT_PATH = Path("/wg-admin/secrets/auth_ratelimit.json")
AUTHLOG_PATH = Path("/wg-admin/secrets/auth.log")
BANDWIDTH_PATH = Path("/wg-admin/bandwidth.json")


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent.parent / "templates"),
        static_folder=str(Path(__file__).parent.parent.parent / "static"),
    )

    cfg = config.load_config(CONFIG_PATH)
    master_key = (SECRETS_DIR / "master.key").read_bytes()
    session_key = (SECRETS_DIR / "session.key").read_bytes()

    app.secret_key = session_key
    app.config.update(
        SESSION_COOKIE_NAME="wg_admin_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=cfg["server"].getint("session_lifetime_seconds", 3600),
        SESSION_PERMANENT=True,
        MASTER_KEY=master_key,
        CONFIG=cfg,
    )

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
            if token != request.form.get("csrf_token"):
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
            }
        except Exception:
            # If anything fails (bandwidth.json missing, etc.) — don't break every page
            return {
                "vpn_active": False,
                "global_used_gb": 0.0,
                "global_quota_gb": 0.0,
                "global_exceeded": False,
            }

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("admin"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapper

    @app.route("/")
    @login_required
    def index():
        return redirect(url_for("peers_list"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            client_ip = request.remote_addr or "unknown"
            if ratelimit.is_blocked(RATELIMIT_PATH, client_ip):
                abort(429)

            password = request.form.get("password", "")
            auth_text = (SECRETS_DIR / "auth.ini").read_text()
            phc_hash = ""
            for line in auth_text.splitlines():
                if line.startswith("password_hash ="):
                    phc_hash = line.split("=", 1)[1].strip()
                    break

            user_agent = request.headers.get("User-Agent", "")
            if crypto.verify_password(password, phc_hash):
                session["admin"] = True
                session["csrf_token"] = pysecrets.token_urlsafe(32)
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
                flash("Credenciais inválidas", "error")
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

            # Read current hash
            auth_text = (SECRETS_DIR / "auth.ini").read_text()
            current_hash = ""
            for line in auth_text.splitlines():
                if line.startswith("password_hash ="):
                    current_hash = line.split("=", 1)[1].strip()
                    break

            if not crypto.verify_password(current, current_hash):
                flash("Password atual incorreta.", "error")
                return render_template("change_password.html"), 400

            if len(new) < 8:
                flash("Nova password tem de ter pelo menos 8 caracteres.", "error")
                return render_template("change_password.html"), 400

            if new != confirm:
                flash("As passwords novas não coincidem.", "error")
                return render_template("change_password.html"), 400

            new_hash = crypto.hash_password(new)
            (SECRETS_DIR / "auth.ini").write_text(f"password_hash = {new_hash}\n")
            import os as _os
            _os.chmod(SECRETS_DIR / "auth.ini", 0o600)
            flash("Password atualizada.", "success")
            return redirect(url_for("peers_list"))
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
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_form.html")
            if quota_gb < 0:
                flash("Cota não pode ser negativa", "error")
                return render_template("peer_form.html")

            priv, pub = wg.wg_genkey()
            s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
            ip = state.allocate_ip(s, cfg["wg"]["subnet"], cfg["wg"]["server_ip"])
            new_peer = {
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
            }
            state.add_peer(s, new_peer)
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            try:
                wg.apply_state_to_wg(s, cfg, mode="syncconf")
            except Exception:
                app.logger.exception("Failed to apply state to wg")
                flash("Peer criado no estado, mas falhou apply ao wg — ver logs", "error")
                return redirect(url_for("peers_list"))
            flash(f"Peer criado: {name} ({ip})", "success")
            return redirect(url_for("peers_list"))
        return render_template("peer_form.html")

    @app.route("/peers/<peer_id>/delete", methods=["POST"])
    @login_required
    def peer_delete(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        if not state.remove_peer(s, peer_id):
            abort(404)
        state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
        try:
            wg.apply_state_to_wg(s, cfg, mode="restart")
        except Exception:
            app.logger.exception("Failed to apply after delete")
            flash("Peer removido do estado, mas apply falhou — ver logs", "error")
        flash("Peer apagado", "success")
        return redirect(url_for("peers_list"))

    @app.route("/peers/<peer_id>/toggle", methods=["POST"])
    @login_required
    def peer_toggle(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        state.set_peer_disabled(s, peer_id, not peer.get("disabled", False))
        state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
        try:
            wg.apply_state_to_wg(s, cfg, mode="restart")
        except Exception:
            app.logger.exception("Failed to apply after toggle")
            flash("Toggle registrado mas apply falhou", "error")
        return redirect(url_for("peers_list"))

    def _get_server_public_key() -> str:
        """Get server public key — try config.ini first, fall back to live `wg show`."""
        key = cfg["wg"].get("server_public_key", "").strip()
        if key:
            return key
        try:
            return wg.wg_server_public_key(cfg["wg"]["interface"])
        except Exception:
            app.logger.warning("Could not get server public key via wg show", exc_info=True)
            return ""

    @app.route("/peers/<peer_id>/conf")
    @login_required
    def peer_conf(peer_id):
        from flask import Response
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        if not peer.get("private_key_enc"):
            flash("Este peer foi importado do wg0.conf — a chave privada só existe no dispositivo cliente. Não é possível gerar .conf nem QR. Cria um peer novo ou apaga este e volta a importar manualmente.", "error")
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
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="wg-{peer["name"]}.conf"'},
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
            flash("Peer importado — chave privada não disponível. Não é possível gerar QR.", "error")
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
        return Response(png, mimetype="image/png")

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
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_edit.html", peer=peer)
            if quota_gb < 0:
                flash("Cota não pode ser negativa", "error")
                return render_template("peer_edit.html", peer=peer)
            peer["name"] = name
            peer["notes"] = notes
            peer["quota_gb"] = quota_gb
            if new_priv:
                peer["private_key_enc"] = state.encrypt_private_key(new_priv, app.config["MASTER_KEY"])
                flash("Chave privada atualizada — já podes descarregar .conf e QR", "success")
            else:
                flash("Peer atualizado", "success")
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            return redirect(url_for("peers_list"))
        return render_template("peer_edit.html", peer=peer)

    @app.route("/vpn/toggle", methods=["POST"])
    @login_required
    def vpn_toggle():
        interface = cfg["wg"]["interface"]
        if wg.wg_interface_active(interface):
            subprocess.run(
                ["systemctl", "stop", f"wg-quick@{interface}"],
                capture_output=True, check=True,
            )
            flash("VPN desativada — todos os peers desconectados", "warning")
        else:
            subprocess.run(
                ["systemctl", "start", f"wg-quick@{interface}"],
                capture_output=True, check=True,
            )
            flash("VPN reativada", "success")
        return redirect(request.referrer or url_for("peers_list"))

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="CSRF token inválido ou sessão expirada."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Recurso não encontrado."), 404

    @app.errorhandler(429)
    def too_many(e):
        return render_template("error.html", code=429, message="Muitas tentativas de login. Tente novamente mais tarde."), 429

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Internal error")
        return render_template("error.html", code=500, message="Erro interno."), 500

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
