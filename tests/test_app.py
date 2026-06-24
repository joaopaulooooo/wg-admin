# tests/test_app.py
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


def test_logout_clears_session(client):
    client.post("/login", data={"password": "test-password"})
    r = client.post("/logout")
    assert r.status_code in (301, 302)
    r2 = client.get("/")
    assert "/login" in r2.headers["Location"]


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
    monkeypatch.setattr("wg_admin.app._apply_state_to_wg", lambda s, cfg: None)

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
    monkeypatch.setattr("wg_admin.app._apply_state_to_wg", lambda s, cfg: None)
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
