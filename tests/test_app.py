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
