# tests/test_settings.py
"""Settings page: WireGuard interface, peer defaults, key rotation."""
import pytest

FAKE_CONF = """[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = OLDPRIVATEKEY=
PostUp = iptables -A FORWARD -i %i -j ACCEPT
PostDown = iptables -D FORWARD -i %i -j ACCEPT

# name: iPhone JP
[Peer]
PublicKey = PUBJPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
AllowedIPs = 10.0.0.2/32

# name: old phone
# [Peer]
# PublicKey = PUBOLDDISABLEDAAAAAAAAAAAAAAAAAAAAAAAAAAA=
# AllowedIPs = 10.0.0.9/32
"""


@pytest.fixture
def wgconf(workdir, monkeypatch):
    """Fake /etc/wireguard dir with a wg0.conf."""
    wg_dir = workdir["tmp_path"] / "wireguard"
    wg_dir.mkdir()
    (wg_dir / "wg0.conf").write_text(FAKE_CONF)
    monkeypatch.setattr("wg_admin.app.WG_CONF_DIR", wg_dir)
    return wg_dir


@pytest.fixture
def authed(client):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    return csrf


def test_settings_requires_login(client):
    r = client.get("/settings")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_settings_page_renders(client, wgconf, authed, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key", lambda iface="wg0": "LIVEPUBKEY=")
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"Configura" in r.data
    assert b"LIVEPUBKEY=" in r.data
    # private key must never be rendered
    assert b"OLDPRIVATEKEY=" not in r.data


def test_settings_page_pubkey_falls_back_to_conf(client, wgconf, authed, monkeypatch):
    def boom(iface="wg0"):
        raise RuntimeError("wg show unavailable")
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key", boom)
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key_from_conf", lambda p: "CONFPUBKEY=")
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"CONFPUBKEY=" in r.data


def test_interface_update_rewrites_conf_and_keeps_peers(client, wgconf, authed, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    r = client.post("/settings/interface", data={
        "address": "10.9.9.1/24",
        "listen_port": "51821",
        "mtu": "1420",
        "post_up": "iptables -A FORWARD -i %i -j ACCEPT\niptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE",
        "post_down": "iptables -D FORWARD -i %i -j ACCEPT",
        "csrf_token": authed,
    })
    assert r.status_code in (301, 302)
    content = (wgconf / "wg0.conf").read_text()
    assert "Address = 10.9.9.1/24" in content
    assert "ListenPort = 51821" in content
    assert "MTU = 1420" in content
    assert "PrivateKey = OLDPRIVATEKEY=" in content
    assert content.count("PostUp = ") == 2
    # peers preserved verbatim, including disabled and name comments
    assert "# name: iPhone JP" in content
    assert "PUBJPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" in content
    assert "# [Peer]" in content
    assert "# AllowedIPs = 10.0.0.9/32" in content
    assert "# name: old phone" in content


def test_interface_update_empty_mtu_removes_key(client, wgconf, authed, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    # first set MTU, then clear it
    client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "51820", "mtu": "1420",
        "post_up": "", "post_down": "", "csrf_token": authed,
    })
    assert "MTU = 1420" in (wgconf / "wg0.conf").read_text()
    client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "51820", "mtu": "",
        "post_up": "", "post_down": "", "csrf_token": authed,
    })
    assert "MTU" not in (wgconf / "wg0.conf").read_text()


def test_interface_update_rejects_bad_address(client, wgconf, authed):
    r = client.post("/settings/interface", data={
        "address": "not-a-cidr", "listen_port": "51820",
        "mtu": "", "post_up": "", "post_down": "", "csrf_token": authed,
    }, follow_redirects=True)
    assert b"inv" in r.data
    assert "Address = 10.0.0.1/24" in (wgconf / "wg0.conf").read_text()


def test_interface_update_rejects_bad_port(client, wgconf, authed):
    client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "99999",
        "mtu": "", "post_up": "", "post_down": "", "csrf_token": authed,
    })
    assert "ListenPort = 51820" in (wgconf / "wg0.conf").read_text()


def test_interface_update_rejects_bad_mtu(client, wgconf, authed):
    client.post("/settings/interface", data={
        "address": "10.0.0.1/24", "listen_port": "51820",
        "mtu": "100", "post_up": "", "post_down": "", "csrf_token": authed,
    })
    assert "MTU" not in (wgconf / "wg0.conf").read_text()


def test_peer_defaults_saved_to_config_ini(client, workdir, authed):
    r = client.post("/settings/peer-defaults", data={
        "endpoint_host": "vpn.example.pt",
        "endpoint_port": "51821",
        "allowed_ips": "10.0.0.0/24, 192.168.1.0/24",
        "dns": "1.1.1.1, 1.0.0.1",
        "csrf_token": authed,
    })
    assert r.status_code in (301, 302)
    from wg_admin import config as config_mod
    cfg = config_mod.load_config(workdir["config_path"])
    assert cfg["peer_defaults"]["endpoint_host"] == "vpn.example.pt"
    assert cfg["peer_defaults"]["endpoint_port"] == "51821"
    assert cfg["peer_defaults"]["allowed_ips"] == "10.0.0.0/24, 192.168.1.0/24"
    assert cfg["peer_defaults"]["dns"] == "1.1.1.1, 1.0.0.1"
    # no leftover tmp file
    assert not (workdir["config_path"].parent / "config.ini.tmp").exists()


def test_peer_defaults_rejects_bad_allowed_ips(client, workdir, authed):
    client.post("/settings/peer-defaults", data={
        "endpoint_host": "vpn.example.pt", "endpoint_port": "51820",
        "allowed_ips": "banana", "dns": "", "csrf_token": authed,
    })
    assert not workdir["config_path"].exists()


def test_peer_defaults_rejects_bad_dns(client, workdir, authed):
    client.post("/settings/peer-defaults", data={
        "endpoint_host": "vpn.example.pt", "endpoint_port": "51820",
        "allowed_ips": "0.0.0.0/0", "dns": "not-an-ip", "csrf_token": authed,
    })
    assert not workdir["config_path"].exists()


def test_rotate_key_updates_conf_and_flashes_new_pub(client, wgconf, authed, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("NEWPRIVATEKEY=", "NEWPUBKEY="))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
    r = client.post("/settings/rotate-key", data={"csrf_token": authed}, follow_redirects=True)
    assert r.status_code == 200
    assert b"NEWPUBKEY=" in r.data
    content = (wgconf / "wg0.conf").read_text()
    assert "PrivateKey = NEWPRIVATEKEY=" in content
    assert "OLDPRIVATEKEY=" not in content
    # peers untouched
    assert "PUBJPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" in content


def test_rotate_key_requires_login(client):
    r = client.post("/settings/rotate-key", data={})
    assert r.status_code in (301, 302)


def test_restart_wg_admin_requires_login(client):
    r = client.post("/settings/restart-wg-admin", data={})
    assert r.status_code in (301, 302)


def test_restart_wg_admin_schedules_and_flashes(client, wgconf, authed, monkeypatch):
    calls = []
    monkeypatch.setattr("wg_admin.app._schedule_self_restart", lambda app: calls.append("self"))
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key", lambda iface="wg0": "PUB=")
    r = client.post("/settings/restart-wg-admin", data={"csrf_token": authed}, follow_redirects=True)
    assert r.status_code == 200
    assert calls == ["self"]
    assert b"wg-admin agendado" in r.data


def test_restart_wireguard_requires_login(client):
    r = client.post("/settings/restart-wireguard", data={})
    assert r.status_code in (301, 302)


def test_restart_wireguard_schedules_correct_unit(client, wgconf, authed, monkeypatch):
    cmds = []
    monkeypatch.setattr("wg_admin.app._schedule_shell", lambda app, cmd: cmds.append(cmd))
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key", lambda iface="wg0": "PUB=")
    r = client.post("/settings/restart-wireguard", data={"csrf_token": authed}, follow_redirects=True)
    assert r.status_code == 200
    assert len(cmds) == 1
    assert "systemctl restart wg-quick@wg0" in cmds[0]
    assert cmds[0].startswith("sleep 1;")
    assert b"wg-quick@wg0" in r.data


def test_settings_page_lists_service_buttons(client, wgconf, authed, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.wg_server_public_key", lambda iface="wg0": "PUB=")
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"Reiniciar wg-admin" in r.data
    assert b"Reiniciar WireGuard" in r.data
