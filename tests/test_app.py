# tests/test_app.py
import json


def test_index_redirects_to_login_when_not_authed(client):
    r = client.get("/")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"password" in r.data.lower()


def test_login_with_correct_password_redirects(client):
    r = client.post("/login", data={"password": "test-password"})
    assert r.status_code in (301, 302)
    assert "/peers" in r.headers["Location"]


def test_login_with_wrong_password_shows_error(client):
    r = client.post("/login", data={"password": "wrong"})
    assert r.status_code == 200  # stays on login page
    assert b"invalid" in r.data.lower() or b"credenciais" in r.data.lower()


def test_failed_login_is_logged(client, workdir):
    log_path = workdir["authlog_path"]
    client.post("/login", data={"password": "wrong"},
                headers={"User-Agent": "TestAgent/1.0"})
    entries = [json.loads(l) for l in log_path.read_text().splitlines() if l]
    assert len(entries) == 1
    assert entries[0]["success"] is False
    assert entries[0]["user_agent"] == "TestAgent/1.0"


def test_successful_login_is_logged(client, workdir):
    log_path = workdir["authlog_path"]
    client.post("/login", data={"password": "test-password"},
                headers={"User-Agent": "TestAgent/2.0"})
    entries = [json.loads(l) for l in log_path.read_text().splitlines() if l]
    assert len(entries) == 1
    assert entries[0]["success"] is True
    assert entries[0]["user_agent"] == "TestAgent/2.0"


def test_logout_clears_session(client):
    client.post("/login", data={"password": "test-password"})
    r = client.post("/logout")
    assert r.status_code in (301, 302)
    r2 = client.get("/")
    assert "/login" in r2.headers["Location"]


def test_duration_filter_formats_seconds():
    from wg_admin.app import format_duration
    assert format_duration(5) == "5s"
    assert format_duration(59) == "59s"
    assert format_duration(60) == "1min"
    assert format_duration(120) == "2min"
    assert format_duration(3599) == "59min"
    assert format_duration(3600) == "1h"
    assert format_duration(7200) == "2h"
    assert format_duration(86399) == "23h"
    assert format_duration(86400) == "1d"
    assert format_duration(172800) == "2d"


def test_duration_filter_passes_through_non_numeric():
    from wg_admin.app import format_duration
    assert format_duration(None) is None
    assert format_duration("?") == "?"
    assert format_duration("") == ""


def test_peers_list_shows_existing_peers(client, workdir):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345",
        "name": "Test iPhone",
        "notes": "",
        "public_key": "PUB",
        "private_key_enc": state_mod.encrypt_private_key("PRIVATE_KEY_HERE=", client.application.config["MASTER_KEY"]),
        "ip": "10.0.0.2",
        "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert r.status_code == 200
    assert b"Test iPhone" in r.data
    assert b"10.0.0.2" in r.data


def test_peers_list_shows_disabled_marker(client, workdir):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345",
        "name": "Disabled Peer",
        "public_key": "PUB",
        "ip": "10.0.0.2",
        "disabled": True,
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert b"disabled" in r.data.lower() or b"inativo" in r.data.lower()


def test_create_peer_adds_to_state(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(
        wg_mod, "wg_genkey",
        lambda: ("PRIVATE_MOCKED=", "PUBLIC_MOCKED=")
    )
    monkeypatch.setattr(
        wg_mod, "wg_quick_restart",
        lambda interface="wg0": None
    )

    client.post("/login", data={"password": "test-password"})

    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/new", data={
        "name": "New Test Peer",
        "notes": "test note",
        "csrf_token": csrf,
    })
    assert r.status_code in (301, 302)

    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert len(s["peers"]) == 1
    assert s["peers"][0]["name"] == "New Test Peer"
    assert s["peers"][0]["public_key"] == "PUBLIC_MOCKED="
    assert s["peers"][0]["ip"] == "10.0.0.2"


def test_create_peer_validates_name_required(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(wg_mod, "wg_genkey", lambda: ("P=", "PUB="))
    monkeypatch.setattr(wg_mod, "wg_quick_restart", lambda interface="wg0": None)

    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/new", data={"name": "", "csrf_token": csrf})
    assert r.status_code == 200
    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert len(s["peers"]) == 0


def _seed_peer(workdir, client, peer_id="abc12345", name="To Delete"):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": peer_id,
        "name": name,
        "public_key": "PUB",
        "private_key_enc": state_mod.encrypt_private_key("PRIVATE_KEY_HERE=", client.application.config["MASTER_KEY"]),
        "ip": "10.0.0.2",
        "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])


def test_delete_peer_removes_from_state(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(wg_mod, "wg_quick_restart", lambda interface="wg0": None)
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    _seed_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/abc12345/delete", data={"csrf_token": csrf})
    assert r.status_code in (301, 302)

    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert len(s["peers"]) == 0


def test_toggle_peer_flips_disabled(client, workdir, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(wg_mod, "wg_quick_restart", lambda interface="wg0": None)

    _seed_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/abc12345/toggle", data={"csrf_token": csrf})
    assert r.status_code in (301, 302)

    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["disabled"] is True


def test_delete_unknown_peer_returns_404(client, workdir, monkeypatch):
    _seed_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/nonexistent/delete", data={"csrf_token": csrf})
    assert r.status_code == 404


def test_download_conf_returns_attachment(client, workdir):
    # _seed_peer from T18 should already be in the file
    _seed_peer(workdir, client, name="Downloadable")
    client.post("/login", data={"password": "test-password"})

    r = client.get("/peers/abc12345/conf")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    assert b"[Interface]" in r.data
    assert b"PRIVATE_KEY_HERE" in r.data  # decrypted private key value


def test_download_qr_returns_png(client, workdir):
    _seed_peer(workdir, client, name="QRPeer")
    client.post("/login", data={"password": "test-password"})

    r = client.get("/peers/abc12345/qr")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.data.startswith(b"\x89PNG")


def test_download_conf_unknown_peer_404(client, workdir):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/nonexistent/conf")
    assert r.status_code == 404


def test_404_returns_error_page(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/nonexistent/conf")
    assert r.status_code == 404
    assert b"404" in r.data


def test_change_password_requires_current(client, workdir):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    r = client.post("/change-password", data={
        "current": "wrong",
        "new": "newpassword",
        "confirm": "newpassword",
        "csrf_token": csrf,
    })
    assert r.status_code == 400
    assert b"incorreta" in r.data.lower()


def test_change_password_requires_min_length(client, workdir):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    r = client.post("/change-password", data={
        "current": "test-password",
        "new": "short",
        "confirm": "short",
        "csrf_token": csrf,
    })
    assert r.status_code == 400
    assert b"8" in r.data


def test_change_password_requires_match(client, workdir):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    r = client.post("/change-password", data={
        "current": "test-password",
        "new": "newpassword",
        "confirm": "different",
        "csrf_token": csrf,
    })
    assert r.status_code == 400
    assert b"coincidem" in r.data.lower() or b"n" in r.data


def test_change_password_success_allows_login_with_new(client, workdir):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    r = client.post("/change-password", data={
        "current": "test-password",
        "new": "brand-new-pwd-123",
        "confirm": "brand-new-pwd-123",
        "csrf_token": csrf,
    })
    assert r.status_code in (301, 302)

    # Logout
    client.post("/logout")
    # Login with new password
    r = client.post("/login", data={"password": "brand-new-pwd-123"})
    assert r.status_code in (301, 302)
    assert "/peers" in r.headers["Location"]


def test_change_password_requires_auth(client, workdir):
    r = client.get("/change-password")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_peer_new_uses_syncconf_mode(client, workdir, monkeypatch):
    """Creating a peer calls apply_state_to_wg with mode=syncconf."""
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))

    captured = {}
    def fake_apply(s, cfg, mode="syncconf"):
        captured["mode"] = mode
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", fake_apply)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    r = client.post("/peers/new", data={"name": "Test", "csrf_token": token})
    assert r.status_code in (301, 302)
    assert captured.get("mode") == "syncconf"


def test_peer_delete_uses_restart_mode(client, workdir, monkeypatch):
    """Deleting a peer still calls apply_state_to_wg with mode=restart."""
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    captured = {}
    def fake_apply(s, cfg, mode="syncconf"):
        captured["mode"] = mode
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", fake_apply)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/abc12345/delete", data={"csrf_token": token})
    assert r.status_code in (301, 302)
    assert captured.get("mode") == "restart"


def test_vpn_toggle_off_when_active(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: True)
    import subprocess as _sp
    monkeypatch.setattr("wg_admin.app.subprocess", _sp)
    calls = []
    monkeypatch.setattr("wg_admin.app.subprocess.run",
                        lambda cmd, **kw: calls.append(cmd))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/vpn/toggle", data={"csrf_token": token})
    assert r.status_code in (301, 302)
    assert ["systemctl", "stop", "wg-quick@wg0"] in calls


def test_vpn_toggle_on_when_inactive(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: False)
    import subprocess as _sp
    monkeypatch.setattr("wg_admin.app.subprocess", _sp)
    calls = []
    monkeypatch.setattr("wg_admin.app.subprocess.run",
                        lambda cmd, **kw: calls.append(cmd))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/vpn/toggle", data={"csrf_token": token})
    assert r.status_code in (301, 302)
    assert ["systemctl", "start", "wg-quick@wg0"] in calls


def test_vpn_toggle_requires_login(client):
    r = client.post("/vpn/toggle", data={})
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_vpn_toggle_requires_csrf(client, workdir):
    client.post("/login", data={"password": "test-password"})
    r = client.post("/vpn/toggle", data={})  # no csrf_token
    assert r.status_code == 403


def test_context_processor_does_not_break_page_render(client, workdir, monkeypatch):
    """Context processor gracefully degrades when subsystems fail."""
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: True)
    monkeypatch.setattr("wg_admin.bandwidth.load_bandwidth",
                        lambda path: {"peers": {}})
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert r.status_code == 200


def test_peer_new_saves_quota_gb(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={
        "name": "João", "quota_gb": "10.5", "csrf_token": token,
    })
    assert r.status_code in (301, 302)

    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["quota_gb"] == 10.5
    assert s["peers"][0]["quota_suspended"] is False


def test_peer_new_quota_gb_defaults_to_zero_when_blank(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={"name": "X", "csrf_token": token})
    assert r.status_code in (301, 302)

    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["quota_gb"] == 0.0


def test_peer_new_rejects_negative_quota(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={
        "name": "X", "quota_gb": "-5", "csrf_token": token,
    })
    # Form should re-render with error, not save
    assert r.status_code == 200
    assert b"negativ" in r.data.lower()  # matches "negativa"


def test_peer_edit_saves_quota_gb(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/abc12345/edit", data={
        "name": "x", "notes": "", "quota_gb": "25", "csrf_token": token,
    })
    assert r.status_code in (301, 302)

    s2 = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s2["peers"][0]["quota_gb"] == 25.0


def test_peer_edit_rejects_negative_quota(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/abc12345/edit", data={
        "name": "x", "notes": "", "quota_gb": "-5", "csrf_token": token,
    })
    # Form re-renders (200), not saved
    assert r.status_code == 200
    assert b"negativ" in r.data.lower()

    # Verify state wasn't changed
    s2 = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s2["peers"][0]["quota_gb"] == 0.0


def test_api_peer_bandwidth_returns_30_days(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    from datetime import datetime, timezone, timedelta
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "...",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    bw_path = workdir["tmp_path"] / "bandwidth.json"
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH", bw_path)
    daily = {}
    for i in range(5):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"rx": 1024**3 * (i + 1), "tx": 0}
    bw_path.write_text(json.dumps({"peers": {"PUB": {
        "first_seen": "...", "total_rx": 0, "total_tx": 0,
        "daily": daily, "last_sample": {"ts": "", "rx": 0, "tx": 0},
    }}}))

    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/abc12345")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["dates"]) == 30
    assert len(data["rx"]) == 30
    assert len(data["tx"]) == 30
    # Sum of rx should equal 5 * 1GB across the 5 days we seeded
    assert sum(data["rx"]) == sum((i + 1) * 1024**3 for i in range(5))


def test_api_peer_bandwidth_404_for_unknown_peer(client, workdir):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/nonexistent")
    assert r.status_code == 404


def test_api_peer_bandwidth_requires_login(client):
    r = client.get("/api/bandwidth/anything")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_api_global_bandwidth_returns_top_5_plus_outros(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    from datetime import datetime, timezone, timedelta
    s = state_mod.empty_state()
    s["peers"] = [
        {"id": str(i), "name": f"peer{i}", "public_key": f"PUB{i}",
         "private_key_enc": "", "ip": f"10.0.0.{i+2}", "disabled": False,
         "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
         "created_at": "..."}
        for i in range(7)
    ]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    bw_path = workdir["tmp_path"] / "bandwidth.json"
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH", bw_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bw_data = {"peers": {}}
    for i in range(7):
        bw_data["peers"][f"PUB{i}"] = {
            "daily": {today: {"rx": (i + 1) * 1024**3, "tx": 0}},
            "first_seen": "", "total_rx": 0, "total_tx": 0,
            "last_sample": {"ts": "", "rx": 0, "tx": 0},
        }
    bw_path.write_text(json.dumps(bw_data))

    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/global")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["dates"]) == 30
    series_names = [s["name"] for s in data["series"]]
    # Top 5 peers ( PUB6=7GB, PUB5=6GB, PUB4=5GB, PUB3=4GB, PUB2=3GB )
    # + "outros" ( PUB0=1GB + PUB1=2GB = 3GB )
    assert "outros" in series_names
    assert len(data["series"]) == 6  # 5 + outros


def test_api_global_bandwidth_empty(client, workdir, monkeypatch):
    bw_path = workdir["tmp_path"] / "bandwidth.json"
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH", bw_path)
    bw_path.write_text(json.dumps({"peers": {}}))
    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/global")
    assert r.status_code == 200
    data = r.get_json()
    assert data["series"] == []
    assert len(data["dates"]) == 30


def test_server_status_requires_auth(client):
    r = client.get("/server-status")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_server_status_page_returns_200(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/server-status")
    assert r.status_code == 200
    assert b"server status" in r.data.lower()


def test_api_server_status_requires_auth(client):
    r = client.get("/api/server-status")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_api_server_status_returns_json(client, monkeypatch):
    from wg_admin import serverstats
    monkeypatch.setattr(
        serverstats, "collect",
        lambda cfg: {"cpu_percent": 7.5, "mem_percent": 30.0}
    )
    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/server-status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["cpu_percent"] == 7.5
    assert data["mem_percent"] == 30.0



def _seed_imported_peer(workdir, client, peer_id="imp00001", name="Imported"):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": peer_id,
        "name": name,
        "public_key": "OLDPUB",
        "ip": "10.0.0.5",
        "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])


def _login_csrf(client):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def test_imported_peer_shows_generate_keys_button(client, workdir):
    _seed_imported_peer(workdir, client)
    _login_csrf(client)
    r = client.get("/peers")
    assert r.status_code == 200
    assert b"Gerar chaves" in r.data
    # wg-admin created peers don't get the button (they already have keys)
    assert b"Peer importado" not in r.data


def test_regenerate_imported_peer_creates_keys_and_unlocks_conf(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    from wg_admin import state as state_mod
    monkeypatch.setattr(wg_mod, "wg_genkey", lambda: ("NEWPRIV=", "NEWPUB="))
    applied = []
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": applied.append(mode))
    _seed_imported_peer(workdir, client)
    csrf = _login_csrf(client)

    r = client.post("/peers/imp00001/regenerate", data={"csrf_token": csrf})
    assert r.status_code in (301, 302)
    assert applied == ["syncconf"]

    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    p = s["peers"][0]
    assert p["public_key"] == "NEWPUB="
    assert p.get("private_key_enc")

    # .conf and QR now downloadable
    r = client.get("/peers/imp00001/conf")
    assert r.status_code == 200
    assert b"[Interface]" in r.data
    r = client.get("/peers/imp00001/qr")
    assert r.status_code == 200


def test_regenerate_works_for_created_peer_rotation_too(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    from wg_admin import state as state_mod
    monkeypatch.setattr(wg_mod, "wg_genkey", lambda: ("ROTPRIV=", "ROTPUB="))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    _seed_peer(workdir, client, name="RotateMe")
    csrf = _login_csrf(client)
    r = client.post("/peers/abc12345/regenerate", data={"csrf_token": csrf})
    assert r.status_code in (301, 302)
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["public_key"] == "ROTPUB="


def test_regenerate_requires_login(client):
    r = client.post("/peers/abc12345/regenerate", data={})
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_regenerate_unknown_peer_404(client, workdir, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("P=", "PUB="))
    csrf = _login_csrf(client)
    r = client.post("/peers/zzz99999/regenerate", data={"csrf_token": csrf})
    assert r.status_code == 404


def test_created_peer_shows_rotate_key_button(client, workdir):
    _seed_peer(workdir, client, name="WgAdminCreated")
    _login_csrf(client)
    r = client.get("/peers")
    assert r.status_code == 200
    assert b"Rodar chave" in r.data
    assert b"Gerar chaves" not in r.data


# ─── v0.3.0 security regressions ────────────────────────────────────────────

def test_change_password_revokes_all_sessions(client, workdir):
    """Bumping the epoch on password change kills every session, including
    a second browser still holding a valid-looking cookie."""
    client.post("/login", data={"password": "test-password"})

    other = client.application.test_client()
    other.post("/login", data={"password": "test-password"})
    assert other.get("/settings").status_code == 200

    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    r = client.post("/change-password", data={
        "current": "test-password",
        "new": "brand-new-pwd-9",
        "confirm": "brand-new-pwd-9",
        "csrf_token": csrf,
    })
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]

    # The other session's cookie is now worthless.
    r = other.get("/settings")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_session_epoch_bump_forces_relogin(client, workdir):
    client.post("/login", data={"password": "test-password"})
    assert client.get("/settings").status_code == 200

    from wg_admin.app import _read_auth_ini, _write_auth_ini
    phc, epoch = _read_auth_ini()
    _write_auth_ini(phc, epoch + 1)  # simulate out-of-band password change

    r = client.get("/settings")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_conf_and_qr_responses_are_no_store(client, workdir):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "k1", "name": "phone", "notes": "", "public_key": "PUB",
        "private_key_enc": state_mod.encrypt_private_key(
            "PRIVATE_KEY_HERE=", client.application.config["MASTER_KEY"]),
        "ip": "10.0.0.2", "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])
    client.post("/login", data={"password": "test-password"})

    conf = client.get("/peers/k1/conf")
    assert conf.status_code == 200
    assert conf.headers.get("Cache-Control") == "no-store"
    assert "attachment" in conf.headers.get("Content-Disposition", "")

    qr = client.get("/peers/k1/qr")
    assert qr.status_code == 200
    assert qr.headers.get("Cache-Control") == "no-store"


def test_security_headers_present(client):
    r = client.get("/login")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers.get("Content-Security-Policy", "")


def test_peer_new_rejects_newline_in_name(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    r = client.post("/peers/new", data={"name": "x\nPostUp = evil", "csrf_token": token})
    assert r.status_code == 200  # form re-rendered with the error
    assert "inválido".encode() in r.data

    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert st["peers"] == []


def test_peer_new_rejects_nan_quota(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    r = client.post("/peers/new", data={"name": "ok", "quota_gb": "nan", "csrf_token": token})
    assert r.status_code == 200

    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert st["peers"] == []


def test_peer_edit_rejects_mismatched_private_key(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "p1", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_pubkey", lambda priv: "DIFFERENT-PUB")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    r = client.post("/peers/p1/edit", data={
        "name": "x", "private_key": "A" * 43 + "=", "csrf_token": token})
    assert r.status_code == 200
    assert "Chave privada inválida".encode() in r.data

    st = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert st["peers"][0]["private_key_enc"] == ""


def test_vpn_toggle_ignores_foreign_referrer(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: False)
    import subprocess as _sp
    monkeypatch.setattr("wg_admin.app.subprocess", _sp)
    monkeypatch.setattr("wg_admin.app.subprocess.run", lambda cmd, **kw: None)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    # Foreign referrer -> same-app fallback, never an open redirect.
    r = client.post("/vpn/toggle", data={"csrf_token": token},
                    headers={"Referer": "https://evil.example/peers"})
    assert r.status_code in (301, 302)
    assert "/peers" in r.headers["Location"]

    # Same-origin referrer still honored.
    r2 = client.post("/vpn/toggle", data={"csrf_token": token},
                     headers={"Referer": "http://localhost/server-status"})
    assert r2.status_code in (301, 302)
    assert "server-status" in r2.headers["Location"]


# ─── v0.3.3 coverage push: main flows & error branches ─────────────────────

import os as _os
import time as _time
from types import SimpleNamespace


def seed_test_peer(workdir, client, peer_id="p1", **extra):
    from wg_admin import state as state_mod
    key = client.application.config["MASTER_KEY"]
    s = state_mod.empty_state()
    peer = {
        "id": peer_id, "name": "orig", "notes": "", "public_key": "PUB",
        "private_key_enc": state_mod.encrypt_private_key("OLD=", key),
        "ip": "10.0.0.2", "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }
    peer.update(extra)
    s["peers"] = [peer]
    state_mod.save_state(workdir["state_path"], s, key)
    return s


def test_change_password_get_renders_form(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/change-password")
    assert r.status_code == 200
    assert "PASSWORD ATUAL".encode() in r.data


def test_peer_new_get_renders_form(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/new")
    assert r.status_code == 200
    assert "Criar peer".encode() in r.data


def test_peer_new_garbage_quota_defaults_to_zero(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIV=", "PUB="))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={"name": "x", "quota_gb": "abc",
                                        "csrf_token": token})
    assert r.status_code in (301, 302)
    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"],
                              client.application.config["MASTER_KEY"])
    assert st["peers"][0]["quota_gb"] == 0.0


def test_peer_edit_success_updates_fields(client, workdir):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/edit", data={
        "name": "renamed", "notes": "note", "quota_gb": "7.5",
        "csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200
    assert "Peer atualizado".encode() in r.data
    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"],
                              client.application.config["MASTER_KEY"])
    p = st["peers"][0]
    assert (p["name"], p["notes"], p["quota_gb"]) == ("renamed", "note", 7.5)


def test_peer_edit_accepts_matching_private_key(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_pubkey", lambda priv: "PUB")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/edit", data={
        "name": "orig", "private_key": "B" * 43 + "=",
        "csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200
    assert "Chave privada atualizada".encode() in r.data
    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"],
                              client.application.config["MASTER_KEY"])
    assert state_mod.decrypt_private_key(
        st["peers"][0]["private_key_enc"],
        client.application.config["MASTER_KEY"]) == "B" * 43 + "="


def test_peer_edit_deleted_mid_flight_404(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.state.update_state",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("gone")))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/edit", data={"name": "x", "csrf_token": token})
    assert r.status_code == 404


def test_peer_regenerate_apply_failure_flags_pending(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("NEW=", "NEWPUB="))

    def boom(s, cfg, mode="syncconf"):
        raise RuntimeError("apply failed")
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", boom)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/regenerate", data={"csrf_token": token},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "apply ao wg falhou".encode() in r.data
    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"],
                              client.application.config["MASTER_KEY"])
    assert st["pending_apply"] is True
    assert st["peers"][0]["public_key"] == "NEWPUB="


def test_peer_delete_apply_failure_flashes(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda s, cfg, mode="restart": (_ for _ in ()).throw(RuntimeError("x")))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/delete", data={"csrf_token": token},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "apply falhou".encode() in r.data


def test_peer_toggle_deleted_mid_flight_404(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.state.update_state",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("gone")))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/toggle", data={"csrf_token": token})
    assert r.status_code == 404


def test_peer_toggle_apply_failure_flashes(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda s, cfg, mode="restart": (_ for _ in ()).throw(RuntimeError("x")))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/toggle", data={"csrf_token": token},
                    follow_redirects=True)
    assert "apply falhou".encode() in r.data


def test_peer_toggle_success_clears_pending_flag(client, workdir, monkeypatch):
    seed_test_peer(workdir, client, pending_apply=True)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda s, cfg, mode="restart": None)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/peers/p1/toggle", data={"csrf_token": token})
    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"],
                              client.application.config["MASTER_KEY"])
    assert st["pending_apply"] is False
    assert st["peers"][0]["disabled"] is True


def test_apply_failure_with_flag_write_failure_does_not_crash(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda s, cfg, mode="restart": (_ for _ in ()).throw(RuntimeError("x")))
    from wg_admin import state as state_mod
    real_update = state_mod.update_state
    calls = {"n": 0}

    def flaky(path, key, mut):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_update(path, key, mut)
        raise RuntimeError("flag write failed")
    monkeypatch.setattr("wg_admin.state.update_state", flaky)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/toggle", data={"csrf_token": token},
                    follow_redirects=True)
    assert r.status_code == 200  # flag write failure is logged, not fatal


def test_conf_for_imported_peer_redirects(client, workdir):
    seed_test_peer(workdir, client, private_key_enc="")
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/p1/conf")
    assert r.status_code in (301, 302)
    r2 = client.get(r.headers["Location"])
    assert "importado do wg0.conf".encode() in r2.data


def test_qr_for_imported_peer_redirects(client, workdir):
    seed_test_peer(workdir, client, private_key_enc="")
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/p1/qr")
    assert r.status_code in (301, 302)
    r2 = client.get(r.headers["Location"])
    assert "chave privada não disponível".encode() in r2.data


def test_qr_unknown_peer_404(client):
    client.post("/login", data={"password": "test-password"})
    assert client.get("/peers/nope/qr").status_code == 404


def test_peers_list_with_live_status_quota_and_banner(client, workdir, monkeypatch):
    import json as _json
    from datetime import datetime, timezone, timedelta
    from wg_admin import state as state_mod
    from wg_admin.wg import PeerStatus
    key = client.application.config["MASTER_KEY"]
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    now = int(_time.time())
    pub = "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="

    def mk(pid, name, quota, suspended, gb):
        peer = {"id": pid, "name": name, "notes": "", "public_key": pub + pid,
                "private_key_enc": state_mod.encrypt_private_key("K=", key),
                "ip": f"10.0.0.{pid}", "disabled": False,
                "quota_gb": quota, "quota_suspended": suspended,
                "quota_state_updated_at": None,
                "created_at": "2026-06-17T00:00:00Z"}
        peers_usage[pub + pid] = gb
        return peer

    peers_usage = {}

    s = state_mod.empty_state()
    s["pending_apply"] = True
    s["peers"] = [
        mk("a", "ok-peer", 10.0, False, 3),      # 30% -> ok
        mk("b", "warn-peer", 10.0, False, 7.5),  # 75% -> warn
        mk("c", "susp-peer", 10.0, True, 3),     # suspended -> danger
    ]
    state_mod.save_state(workdir["state_path"], s, key)

    peers_bw = {}
    for p in s["peers"]:
        peers_bw[p["public_key"]] = {
            "first_seen": "2026-06-01T00:00:00Z", "total_rx": 1, "total_tx": 1,
            "daily": {today: {"rx": peers_usage[p["public_key"]] * (1024**3), "tx": 0},
                      yesterday: {"rx": 0, "tx": 0}},
            "last_sample": {"ts": "x", "rx": 0, "tx": 0}}
    workdir["tmp_path"].joinpath("bandwidth.json").write_text(
        _json.dumps({"peers": peers_bw}))

    statuses = [PeerStatus(public_key=p["public_key"], endpoint=None,
                           allowed_ips=[f"{p['ip']}/32"], latest_handshake=now,
                           transfer_rx=1, transfer_tx=1) for p in s["peers"]]
    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: statuses)
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH",
                        workdir["tmp_path"] / "bandwidth.json")

    client.post("/login", data={"password": "test-password"})
    html = client.get("/peers").get_data(as_text=True)
    assert "Conectados agora" in html
    assert "SUSPENSO" in html
    assert "quota-warn" in html
    assert "quota-danger" in html
    assert "A última alteração ainda não foi aplicada" in html  # pending banner


def test_peers_list_wg_show_failure_degrades(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    monkeypatch.setattr("wg_admin.wg.wg_show_dump",
                        lambda iface: (_ for _ in ()).throw(RuntimeError("wg gone")))
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert r.status_code == 200
    assert b"orig" in r.data


# ─── v0.3.3 coverage push: settings failures, auth robustness, scheduling ───

def _login_csrf(client, password="test-password"):
    client.post("/login", data={"password": password})
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def test_settings_interface_conf_missing(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.update_interface_in_conf",
                        lambda p, u: (_ for _ in ()).throw(FileNotFoundError("gone")))
    r = client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "51820", "mtu": "",
        "csrf_token": token}, follow_redirects=True)
    assert "Conf não encontrado".encode() in r.data


def test_settings_interface_generic_failure(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.update_interface_in_conf",
                        lambda p, u: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "51820", "mtu": "",
        "csrf_token": token}, follow_redirects=True)
    assert "Falha ao aplicar".encode() in r.data


def test_settings_interface_apply_failure(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.update_interface_in_conf", lambda p, u: None)
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda s, cfg, mode="restart": (_ for _ in ()).throw(RuntimeError("x")))
    r = client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "51820", "mtu": "",
        "csrf_token": token}, follow_redirects=True)
    assert "será repetido automaticamente".encode() in r.data


def test_settings_defaults_rejects_bad_endpoint_host(client, workdir):
    token = _login_csrf(client)
    r = client.post("/settings/peer-defaults", data={
        "endpoint_host": "bad host!", "endpoint_port": "51820",
        "allowed_ips": "0.0.0.0/0", "dns": "", "csrf_token": token},
        follow_redirects=True)
    assert "Endpoint host inválido".encode() in r.data


def test_settings_defaults_rejects_bad_port(client, workdir):
    token = _login_csrf(client)
    r = client.post("/settings/peer-defaults", data={
        "endpoint_host": "vpn.example.com", "endpoint_port": "99999",
        "allowed_ips": "0.0.0.0/0", "dns": "", "csrf_token": token},
        follow_redirects=True)
    assert "Porta do endpoint inválida".encode() in r.data


def test_settings_defaults_config_write_failure(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.config.save_config",
                        lambda p, c: (_ for _ in ()).throw(OSError("disk full")))
    r = client.post("/settings/peer-defaults", data={
        "endpoint_host": "vpn.example.com", "endpoint_port": "51820",
        "allowed_ips": "0.0.0.0/0", "dns": "", "csrf_token": token},
        follow_redirects=True)
    assert "Falha ao gravar config.ini".encode() in r.data


def test_settings_language_config_write_failure(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.config.save_config",
                        lambda p, c: (_ for _ in ()).throw(OSError("disk full")))
    r = client.post("/settings/language", data={
        "language": "en", "csrf_token": token}, follow_redirects=True)
    assert "Falha ao gravar config.ini".encode() in r.data


def test_rotate_key_conf_missing(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIV=", "PUB="))
    monkeypatch.setattr("wg_admin.wg.update_interface_in_conf",
                        lambda p, u: (_ for _ in ()).throw(FileNotFoundError("gone")))
    r = client.post("/settings/rotate-key", data={"csrf_token": token},
                    follow_redirects=True)
    assert "Conf não encontrado".encode() in r.data


def test_rotate_key_generic_failure(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIV=", "PUB="))
    monkeypatch.setattr("wg_admin.wg.update_interface_in_conf",
                        lambda p, u: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.post("/settings/rotate-key", data={"csrf_token": token},
                    follow_redirects=True)
    assert "Falha ao rodar a chave".encode() in r.data


def test_rotate_key_apply_failure(client, workdir, monkeypatch):
    token = _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIV=", "PUB="))
    monkeypatch.setattr("wg_admin.wg.update_interface_in_conf", lambda p, u: None)
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda s, cfg, mode="restart": (_ for _ in ()).throw(RuntimeError("x")))
    r = client.post("/settings/rotate-key", data={"csrf_token": token},
                    follow_redirects=True)
    assert "apply ao wg falhou".encode() in r.data


def test_login_429_after_five_failures(client, workdir):
    for _ in range(5):
        r = client.post("/login", data={"password": "wrong"})
        assert r.status_code == 200
    r = client.post("/login", data={"password": "wrong"})
    assert r.status_code == 429
    assert "Muitas tentativas".encode() in r.data


def test_500_handler_renders_error_page(client):
    app = client.application
    old = (app.config.get("TESTING"), app.config.get("PROPAGATE_EXCEPTIONS"))
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        @app.route("/__boom")
        def __boom():
            raise RuntimeError("boom")
        r = client.get("/__boom")
        assert r.status_code == 500
        assert "Erro interno".encode() in r.data
    finally:
        app.config["TESTING"], app.config["PROPAGATE_EXCEPTIONS"] = old


def test_login_without_auth_ini_fails_closed(client, workdir):
    (workdir["secrets_dir"] / "auth.ini").unlink()
    r = client.post("/login", data={"password": "anything"})
    assert r.status_code == 200  # re-renders, never a 500
    assert "Credenciais inválidas".encode() in r.data


def test_login_with_garbage_session_epoch(client, workdir):
    (workdir["secrets_dir"] / "auth.ini").write_text(
        "password_hash = " + __import__("wg_admin.crypto", fromlist=["crypto"]).hash_password("ok-pw")
        + "\nsession_epoch = banana\n")
    r = client.post("/login", data={"password": "ok-pw"})
    assert r.status_code in (301, 302)


def test_valid_wg_private_key_malformed_rejected():
    from wg_admin.app import _valid_wg_private_key
    assert _valid_wg_private_key("not-base64!", "PUB") is False


def test_valid_wg_private_key_wg_error_rejected(monkeypatch):
    from wg_admin import app as app_mod
    monkeypatch.setattr("wg_admin.wg.wg_pubkey",
                        lambda priv: (_ for _ in ()).throw(RuntimeError("wg gone")))
    assert app_mod._valid_wg_private_key("A" * 43 + "=", "PUB") is False


def test_server_pubkey_falls_back_to_config(make_app, monkeypatch):
    app = make_app(language="pt",
                   extra_config="[wg]\nserver_public_key = CFGPUBKEY=\n")
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key",
                        lambda iface: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: [])
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: False)
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{"id": "k1", "name": "x", "notes": "", "public_key": "PUB",
                   "private_key_enc": state_mod.encrypt_private_key("PRIV=", app.config["MASTER_KEY"]),
                   "ip": "10.0.0.2", "disabled": False,
                   "created_at": "2026-06-17T00:00:00Z"}]
    import pathlib
    state_mod.save_state(pathlib.Path("/nonexistent-never") if False else _app_state_path(app), s,
                         app.config["MASTER_KEY"])
    c = app.test_client()
    c.post("/login", data={"password": "pw"})
    body = c.get("/peers/k1/conf").get_data(as_text=True)
    assert "PublicKey = CFGPUBKEY=" in body


def _app_state_path(app):
    from wg_admin import app as app_mod
    return app_mod.STATE_PATH


def test_settings_page_survives_conf_parse_error(client, monkeypatch):
    _login_csrf(client)
    monkeypatch.setattr("wg_admin.wg.parse_wg_conf",
                        lambda text: (_ for _ in ()).throw(OSError("io")))
    r = client.get("/settings")
    assert r.status_code == 200


# ─── v0.3.3 coverage push: final micro-batch ────────────────────────────────

def test_index_redirects_to_peers_when_logged_in(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/")
    assert r.status_code in (301, 302)
    assert "/peers" in r.headers["Location"]


def test_schedule_update_testing_mode_short_circuits(client):
    from wg_admin import app as app_mod
    assert app_mod._schedule_update(client.application, "v9.9.9") is True


def test_context_processor_fallback_on_bandwidth_error(client, monkeypatch):
    monkeypatch.setattr("wg_admin.bandwidth.load_bandwidth",
                        lambda p: (_ for _ in ()).throw(OSError("corrupt")))
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda i: False)
    r = client.get("/login")
    assert r.status_code == 200


def test_toggle_unknown_peer_404_real_transaction(client, workdir):
    from wg_admin import state as state_mod
    state_mod.save_state(workdir["state_path"], state_mod.empty_state(),
                         client.application.config["MASTER_KEY"])
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    assert client.post("/peers/ghost/toggle", data={"csrf_token": token}).status_code == 404


def test_peer_edit_unknown_peer_404(client, workdir):
    from wg_admin import state as state_mod
    state_mod.save_state(workdir["state_path"], state_mod.empty_state(),
                         client.application.config["MASTER_KEY"])
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    assert client.post("/peers/ghost/edit",
                       data={"name": "x", "csrf_token": token}).status_code == 404


def test_peer_edit_deleted_between_check_and_transaction(client, workdir, monkeypatch):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    from wg_admin import state as state_mod
    real_find = state_mod.find_peer_by_id
    seen = {"n": 0}

    def vanishing(st, peer_id):
        seen["n"] += 1
        return real_find(st, peer_id) if seen["n"] == 1 else None
    monkeypatch.setattr("wg_admin.state.find_peer_by_id", vanishing)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/edit", data={"name": "x", "csrf_token": token})
    assert r.status_code == 404


def test_peer_edit_get_renders_form(client, workdir):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/p1/edit")
    assert r.status_code == 200
    assert "Editar peer".encode() in r.data


def test_peer_edit_garbage_quota_defaults_to_zero(client, workdir):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/edit", data={"name": "orig", "quota_gb": "abc",
                                            "csrf_token": token})
    assert r.status_code in (301, 302)
    from wg_admin import state as state_mod
    st = state_mod.load_state(workdir["state_path"],
                              client.application.config["MASTER_KEY"])
    assert st["peers"][0]["quota_gb"] == 0.0


def test_peer_edit_blank_name_re_renders(client, workdir):
    seed_test_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/edit", data={"name": "", "csrf_token": token})
    assert r.status_code == 200
    assert "Nome inválido".encode() in r.data


def test_unflag_write_failure_is_logged_not_fatal(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    key = client.application.config["MASTER_KEY"]
    s = state_mod.empty_state()
    s["pending_apply"] = True
    s["peers"] = [{"id": "p1", "name": "x", "notes": "", "public_key": "PUB",
                   "private_key_enc": state_mod.encrypt_private_key("K=", key),
                   "ip": "10.0.0.2", "disabled": False,
                   "created_at": "2026-06-17T00:00:00Z"}]
    state_mod.save_state(workdir["state_path"], s, key)

    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda st, cfg, mode="restart": None)
    real_update = state_mod.update_state
    calls = {"n": 0}

    def flaky(path, k, mut):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_update(path, k, mut)
        raise RuntimeError("unflag write failed")
    monkeypatch.setattr("wg_admin.state.update_state", flaky)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/p1/toggle", data={"csrf_token": token},
                    follow_redirects=True)
    assert r.status_code == 200  # toggle still succeeds; unflag failure logged


def test_toggle_success_clears_top_level_pending_flag(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    key = client.application.config["MASTER_KEY"]
    s = state_mod.empty_state()
    s["pending_apply"] = True
    s["peers"] = [{"id": "p1", "name": "x", "notes": "", "public_key": "PUB",
                   "private_key_enc": state_mod.encrypt_private_key("K=", key),
                   "ip": "10.0.0.2", "disabled": False,
                   "created_at": "2026-06-17T00:00:00Z"}]
    state_mod.save_state(workdir["state_path"], s, key)
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg",
                        lambda st, cfg, mode="restart": None)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/peers/p1/toggle", data={"csrf_token": token})
    st = state_mod.load_state(workdir["state_path"], key)
    assert st["pending_apply"] is False


def test_server_pubkey_prefers_live_wg_show(make_app, monkeypatch):
    app = make_app(language="pt",
                   extra_config="[wg]\nserver_public_key = STALEKEY=\n")
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key", lambda iface: "LIVEPUB=")
    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: [])
    from wg_admin import app as app_mod, state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{"id": "k1", "name": "x", "notes": "", "public_key": "PUB",
                   "private_key_enc": state_mod.encrypt_private_key("PRIV=", app.config["MASTER_KEY"]),
                   "ip": "10.0.0.2", "disabled": False,
                   "created_at": "2026-06-17T00:00:00Z"}]
    state_mod.save_state(app_mod.STATE_PATH, s, app.config["MASTER_KEY"])
    c = app.test_client()
    c.post("/login", data={"password": "pw"})
    body = c.get("/peers/k1/conf").get_data(as_text=True)
    assert "PublicKey = LIVEPUB=" in body
    assert "STALEKEY" not in body


def test_settings_read_iface_survives_parse_oserror(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    (workdir["tmp_path"] / "wg0.conf").write_text("[Interface]\nAddress = 10.0.0.1/24\n")
    monkeypatch.setattr("wg_admin.app.WG_CONF_DIR", workdir["tmp_path"])
    monkeypatch.setattr("wg_admin.wg.parse_wg_conf",
                        lambda text: (_ for _ in ()).throw(OSError("io")))
    r = client.get("/settings")
    assert r.status_code == 200


def test_valid_endpoint_host_accepts_bare_ip():
    from wg_admin.app import _valid_endpoint_host
    assert _valid_endpoint_host("192.0.2.10") is True
    assert _valid_endpoint_host("2001:db8::1") is True
    assert _valid_endpoint_host("vpn.example.com") is True
    assert _valid_endpoint_host("") is False
    assert _valid_endpoint_host("has space.example.com") is False
    assert _valid_endpoint_host("a" * 254) is False


def test_settings_page_reads_real_conf(client, workdir, monkeypatch):
    """_read_iface success path with a deterministic patched conf dir."""
    client.post("/login", data={"password": "test-password"})
    (workdir["tmp_path"] / "wg0.conf").write_text(
        "[Interface]\nAddress = 10.9.9.1/24\nListenPort = 51820\nPrivateKey = X=\n")
    monkeypatch.setattr("wg_admin.app.WG_CONF_DIR", workdir["tmp_path"])
    html = client.get("/settings").get_data(as_text=True)
    assert "10.9.9.1/24" in html


def test_settings_page_without_conf_shows_empty_iface(client, workdir, monkeypatch):
    """_read_iface with no wg0.conf in the conf dir (CI machines)."""
    client.post("/login", data={"password": "test-password"})
    empty = workdir["tmp_path"] / "no-conf-here"
    empty.mkdir()
    monkeypatch.setattr("wg_admin.app.WG_CONF_DIR", empty)
    r = client.get("/settings")
    assert r.status_code == 200
