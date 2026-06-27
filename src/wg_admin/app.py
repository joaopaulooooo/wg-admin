"""Flask app: routes, auth, CSRF, rate limit."""
from __future__ import annotations

import os as _os
import secrets as pysecrets
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, redirect, render_template, request,
    session, url_for,
)

from . import bandwidth, confgen, config, crypto, ratelimit, state, wg

# These paths are patched in tests via monkeypatch.
SECRETS_DIR = Path("/wg-admin/secrets")
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
RATELIMIT_PATH = Path("/wg-admin/secrets/auth_ratelimit.json")
BANDWIDTH_PATH = Path("/wg-admin/bandwidth.json")


def _apply_state_to_wg(s: dict, cfg) -> None:
    """Regenerate /etc/wireguard/<interface>.conf from state and write atomically.

    Module-level so T18 tests can monkeypatch `wg_admin.app._apply_state_to_wg`.
    """
    interface_path = Path(f"/etc/wireguard/{cfg['wg']['interface']}.conf")
    if interface_path.exists():
        existing = wg.parse_wg_conf(interface_path.read_text())
        interface = existing["interface"]
    else:
        interface = {
            "Address": f"{cfg['wg']['server_ip']}/{cfg['wg']['subnet'].split('/')[-1]}",
            "ListenPort": "51820",
        }
    wg_peers = [
        {
            "PublicKey": p["public_key"],
            "AllowedIPs": f"{p['ip']}/32",
            "disabled": p.get("disabled", False),
            "name": p["name"],
        }
        for p in s["peers"]
    ]
    conf_text = wg.generate_wg_conf(interface, wg_peers)
    tmp = interface_path.with_suffix(".conf.tmp")
    tmp.write_text(conf_text)
    _os.replace(tmp, interface_path)


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
            if not token or token != request.form.get("csrf_token"):
                abort(403)

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

            if crypto.verify_password(password, phc_hash):
                session["admin"] = True
                session["csrf_token"] = pysecrets.token_urlsafe(32)
                ratelimit.clear(RATELIMIT_PATH, client_ip)
                return redirect(url_for("peers_list"))
            else:
                ratelimit.record_fail(RATELIMIT_PATH, client_ip)
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

        bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
        peer_views = []
        connected_count = 0
        for peer in s["peers"]:
            pub = peer.get("public_key", "")
            status = statuses_by_key.get(pub)
            is_online = bool(
                status
                and status.latest_handshake
                and status.latest_handshake > now_ts - ONLINE_WINDOW
            )
            if is_online:
                connected_count += 1
            if not status and pub:
                app.logger.info(
                    "peer %r (%s) not matched in wg show (have: %s)",
                    peer.get("name"), pub, list(statuses_by_key.keys())
                )
            bw_stats = bandwidth.get_peer_stats(bw, pub)
            peer_views.append({
                "peer": peer,
                "status": status,
                "is_online": is_online,
                "bandwidth": {
                    "total_rx": bandwidth.format_bytes(bw_stats["total_rx"]),
                    "total_tx": bandwidth.format_bytes(bw_stats["total_tx"]),
                    "thirty_day_rx": bandwidth.format_bytes(bw_stats["thirty_day_rx"]),
                    "thirty_day_tx": bandwidth.format_bytes(bw_stats["thirty_day_tx"]),
                    "first_seen": bw_stats["first_seen"],
                },
            })

        return render_template(
            "peers.html",
            peer_views=peer_views,
            connected_count=connected_count,
            total_peers=len(s["peers"]),
            imported_count=sum(1 for p in s["peers"] if not p.get("private_key_enc")),
        )

    @app.route("/peers/new", methods=["GET", "POST"])
    @login_required
    def peer_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            notes = request.form.get("notes", "").strip()
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_form.html")

            priv, pub = wg.wg_genkey()
            s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
            ip = state.allocate_ip(
                s,
                cfg["wg"]["subnet"],
                cfg["wg"]["server_ip"],
            )
            new_peer = {
                "id": state.new_peer_id(),
                "name": name,
                "notes": notes,
                "public_key": pub,
                "private_key_enc": state.encrypt_private_key(priv, app.config["MASTER_KEY"]),
                "ip": ip,
                "disabled": False,
                "created_at": state.utc_now_iso(),
            }
            state.add_peer(s, new_peer)
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            try:
                _apply_state_to_wg(s, cfg)
                wg.wg_quick_restart(cfg["wg"]["interface"])
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
            _apply_state_to_wg(s, cfg)
            wg.wg_quick_restart(cfg["wg"]["interface"])
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
            _apply_state_to_wg(s, cfg)
            wg.wg_quick_restart(cfg["wg"]["interface"])
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
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_edit.html", peer=peer), 400
            peer["name"] = name
            peer["notes"] = notes
            if new_priv:
                peer["private_key_enc"] = state.encrypt_private_key(new_priv, app.config["MASTER_KEY"])
                flash("Chave privada atualizada — já podes descarregar .conf e QR", "success")
            else:
                flash("Peer atualizado", "success")
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            return redirect(url_for("peers_list"))
        return render_template("peer_edit.html", peer=peer)

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
