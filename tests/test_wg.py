import textwrap

from wg_admin import wg


SIMPLE_CONF = textwrap.dedent("""
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = SERVER_PRIVATE_KEY=
PostUp = iptables -A FORWARD -i %i -j ACCEPT
PostDown = iptables -D FORWARD -i %i -j ACCEPT

[Peer]   # 10.0.0.2 — João iPhone
PublicKey = Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw=
AllowedIPs = 10.0.0.2/32

[Peer]
PublicKey = RFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y=
AllowedIPs = 10.0.0.3/32
""").strip()


DISABLED_CONF = textwrap.dedent("""
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = SERVER_KEY=

[Peer]
PublicKey = AAAA
AllowedIPs = 10.0.0.2/32

#[Peer]
#PublicKey = BBBB
#AllowedIPs = 10.0.0.3/32
""").strip()


def test_parse_conf_returns_interface_and_peers():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    assert "interface" in result
    assert "peers" in result
    assert len(result["peers"]) == 2


def test_parse_conf_extracts_interface_fields():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    assert result["interface"]["Address"] == "10.0.0.1/24"
    assert result["interface"]["ListenPort"] == "51820"
    assert result["interface"]["PrivateKey"] == "SERVER_PRIVATE_KEY="


def test_parse_conf_postup_postdown_as_lists():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    assert isinstance(result["interface"]["PostUp"], list)
    assert len(result["interface"]["PostUp"]) == 1
    assert "iptables -A FORWARD" in result["interface"]["PostUp"][0]


def test_parse_conf_extracts_peer_fields():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    p0 = result["peers"][0]
    assert p0["PublicKey"] == "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="
    assert p0["AllowedIPs"] == "10.0.0.2/32"
    assert p0["disabled"] is False


def test_parse_conf_marks_disabled_peers():
    result = wg.parse_wg_conf(DISABLED_CONF)
    assert len(result["peers"]) == 2
    assert result["peers"][0]["disabled"] is False
    assert result["peers"][1]["disabled"] is True
    assert result["peers"][1]["PublicKey"] == "BBBB"


def test_parse_conf_skips_blank_lines_and_comments():
    conf = textwrap.dedent("""
    # top-level comment
    [Interface]
    Address = 10.0.0.1/24
    # mid-section comment
    [Peer]
    PublicKey = X
    AllowedIPs = 10.0.0.2/32
    """).strip()
    result = wg.parse_wg_conf(conf)
    assert len(result["peers"]) == 1
    assert result["interface"]["Address"] == "10.0.0.1/24"


def test_generate_conf_roundtrips_simple_case():
    original = textwrap.dedent("""
    [Interface]
    Address = 10.0.0.1/24
    ListenPort = 51820
    PrivateKey = SERVER=
    PostUp = iptables -A FORWARD -i %i -j ACCEPT
    PostDown = iptables -D FORWARD -i %i -j ACCEPT

    [Peer]
    PublicKey = AAAA
    AllowedIPs = 10.0.0.2/32
    """).strip()
    parsed = wg.parse_wg_conf(original)
    regenerated = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    reparsed = wg.parse_wg_conf(regenerated)
    assert reparsed["interface"]["Address"] == "10.0.0.1/24"
    assert reparsed["interface"]["ListenPort"] == "51820"
    assert len(reparsed["peers"]) == 1
    assert reparsed["peers"][0]["PublicKey"] == "AAAA"
    assert reparsed["peers"][0]["AllowedIPs"] == "10.0.0.2/32"


def test_generate_conf_handles_disabled_peer():
    parsed = {
        "interface": {
            "Address": "10.0.0.1/24",
            "ListenPort": "51820",
            "PrivateKey": "SERVER=",
        },
        "peers": [
            {"PublicKey": "AAAA", "AllowedIPs": "10.0.0.2/32", "disabled": False},
            {"PublicKey": "BBBB", "AllowedIPs": "10.0.0.3/32", "disabled": True},
        ],
    }
    out = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    reparsed = wg.parse_wg_conf(out)
    assert len(reparsed["peers"]) == 2
    assert reparsed["peers"][0]["disabled"] is False
    assert reparsed["peers"][1]["disabled"] is True


def test_generate_conf_starts_with_interface_section():
    parsed = {"interface": {"Address": "10.0.0.1/24"}, "peers": []}
    out = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    assert out.startswith("[Interface]")


def test_generate_conf_includes_postup_postdown():
    parsed = {
        "interface": {
            "Address": "10.0.0.1/24",
            "PostUp": ["cmd1", "cmd2"],
            "PostDown": ["cmd3"],
        },
        "peers": [],
    }
    out = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    assert "PostUp = cmd1" in out
    assert "PostUp = cmd2" in out
    assert "PostDown = cmd3" in out


# --- T10: wg show dump + subprocess wrappers ---
import subprocess
from dataclasses import dataclass

import pytest


# Sample `wg show wg0 dump` output (tab-separated, from real wg)
WG_SHOW_DUMP = "\n".join([
    "wg0\t2nU5Z...abbreviated_server_key=\twg0\t10.0.0.1/24\t51820\toff\t1612345678\t12345\t67890",
    "wg0\tY73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw=\t192.168.1.42:51820\t10.0.0.2/32\t1718642000\t102400\t204800\t0",
    "wg0\tRFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y=\t(none)\t10.0.0.3/32\t0\t0\t0\t0",
])


def test_parse_wg_show_dump_returns_peers():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    assert len(peers) == 2


def test_parse_wg_show_dump_skips_interface_line():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    for p in peers:
        assert p.public_key != "2nU5Z...abbreviated_server_key="


def test_parse_wg_show_dump_extracts_fields():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    p0 = peers[0]
    assert p0.public_key == "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="
    assert p0.endpoint == "192.168.1.42:51820"
    assert p0.allowed_ips == ["10.0.0.2/32"]
    assert p0.latest_handshake == 1718642000
    assert p0.transfer_rx == 102400
    assert p0.transfer_tx == 204800


def test_parse_wg_show_dump_handles_disconnected_peer():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    p1 = peers[1]
    assert p1.endpoint is None
    assert p1.latest_handshake == 0
    assert p1.transfer_rx == 0


def test_wg_genkey_calls_subprocess(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["wg", "genkey"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="PRIVATE_KEY_ABC\n")
        if cmd == ["wg", "pubkey"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="PUBLIC_KEY_XYZ\n")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown cmd")

    monkeypatch.setattr(subprocess, "run", fake_run)
    priv, pub = wg.wg_genkey()
    assert priv == "PRIVATE_KEY_ABC"
    assert pub == "PUBLIC_KEY_XYZ"


def test_wg_show_dump_calls_subprocess(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["wg", "show", "wg0", "dump"]
        return subprocess.CompletedProcess(cmd, 0, stdout=WG_SHOW_DUMP)

    monkeypatch.setattr(subprocess, "run", fake_run)
    peers = wg.wg_show_dump("wg0")
    assert len(peers) == 2


def test_wg_quick_restart_calls_systemctl(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    wg.wg_quick_restart("wg0")
    assert calls[0] == ["systemctl", "restart", "wg-quick@wg0"]


# Modern wg show dump format (9 fields per peer, with PSK column)
WG_SHOW_DUMP_MODERN = "\n".join([
    "wg0\tSERVER_PRIV=\tSERVER_PUB=\t51820\toff",
    "wg0\tOvY3EwyTvRoeR6jPDx3dbUyhpnOSV2SaAJY86V77FzQ=\t(none)\t89.152.203.206:56996\t10.66.66.2/32\t1718642000\t1900544\t6972928\t0",
    "wg0\tRFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y=\tabcd=\t(none)\t10.66.66.3/32\t0\t0\t0\t0",
])


def test_parse_modern_format_9_fields():
    """Modern wg show dump has PSK field between pubkey and endpoint."""
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP_MODERN, "wg0")
    assert len(peers) == 2
    p0 = peers[0]
    assert p0.public_key == "OvY3EwyTvRoeR6jPDx3dbUyhpnOSV2SaAJY86V77FzQ="
    assert p0.endpoint == "89.152.203.206:56996"
    assert p0.allowed_ips == ["10.66.66.2/32"]
    assert p0.latest_handshake == 1718642000
    assert p0.transfer_rx == 1900544
    assert p0.transfer_tx == 6972928


def test_parse_modern_format_disconnected_peer():
    """Modern format with '(none)' endpoint and psk field present."""
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP_MODERN, "wg0")
    p1 = peers[1]
    assert p1.public_key == "RFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y="
    assert p1.endpoint is None  # was "(none)"
    assert p1.latest_handshake == 0


def test_parse_does_not_treat_psk_as_pubkey():
    """Critical regression: old parser used parts[2] as endpoint but in modern
    format that's the PSK field. Make sure PSK never becomes public_key."""
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP_MODERN, "wg0")
    for p in peers:
        assert p.public_key != "(none)"
        assert p.public_key != "abcd="
        assert "=" in p.public_key  # all pubkeys end with =
        assert len(p.public_key) == 44  # base64 32 bytes


def test_apply_state_to_wg_syncconf_success(tmp_path, monkeypatch):
    """apply_state_to_wg with mode=syncconf: calls syncconf, skips restart."""
    from wg_admin import wg

    interface_path = tmp_path / "wg0.conf"
    interface_path.write_text("[Interface]\nAddress = 10.0.0.1/24\nListenPort = 51820\nPrivateKey = X\n")

    calls = {"syncconf": False, "restart": False}
    def fake_syncconf(iface):
        calls["syncconf"] = True
        return True
    def fake_restart(iface):
        calls["restart"] = True

    monkeypatch.setattr(wg, "wg_syncconf", fake_syncconf)
    monkeypatch.setattr(wg, "wg_quick_restart", fake_restart)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    monkeypatch.setattr(wg, "_write_conf_atomic", lambda path, text, preserve_mode=False: None)

    cfg = type("C", (), {"__getitem__": lambda self, k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1", "subnet": "10.0.0.0/24"}}[k]})()
    s = {"peers": [{"public_key": "PUB", "ip": "10.0.0.2", "name": "x", "disabled": False}]}
    wg.apply_state_to_wg(s, cfg, mode="syncconf")
    assert calls["syncconf"] is True
    assert calls["restart"] is False


def test_apply_state_to_wg_syncconf_failure_falls_back_to_restart(monkeypatch, tmp_path):
    """If syncconf returns False, falls back to restart."""
    from wg_admin import wg

    calls = {"syncconf": False, "restart": False}
    monkeypatch.setattr(wg, "wg_syncconf", lambda iface: (calls.__setitem__("syncconf", True), False)[1])
    monkeypatch.setattr(wg, "wg_quick_restart", lambda iface: calls.__setitem__("restart", True))
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    monkeypatch.setattr(wg, "_write_conf_atomic", lambda path, text, preserve_mode=False: None)

    cfg = type("C", (), {"__getitem__": lambda self, k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1", "subnet": "10.0.0.0/24"}}[k]})()
    s = {"peers": []}
    wg.apply_state_to_wg(s, cfg, mode="syncconf")
    assert calls["syncconf"] is True
    assert calls["restart"] is True


def test_apply_state_to_wg_restart_mode_skips_syncconf(monkeypatch):
    """mode=restart never calls syncconf."""
    from wg_admin import wg

    calls = {"syncconf": False, "restart": False}
    monkeypatch.setattr(wg, "wg_syncconf", lambda iface: calls.__setitem__("syncconf", True))
    monkeypatch.setattr(wg, "wg_quick_restart", lambda iface: calls.__setitem__("restart", True))
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    monkeypatch.setattr(wg, "_write_conf_atomic", lambda path, text, preserve_mode=False: None)

    cfg = type("C", (), {"__getitem__": lambda self, k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1", "subnet": "10.0.0.0/24"}}[k]})()
    s = {"peers": []}
    wg.apply_state_to_wg(s, cfg, mode="restart")
    assert calls["syncconf"] is False
    assert calls["restart"] is True


def test_wg_syncconf_success(monkeypatch):
    """wg_syncconf: makes two subprocess calls, returns True on success."""
    from wg_admin import wg
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class R:
            stdout = "stripped config"
            returncode = 0
        return R()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", fake_run)
    result = wg.wg_syncconf("wg0")
    assert result is True
    assert calls[0] == ["wg-quick", "strip", "wg0"]
    assert calls[1] == ["wg", "syncconf", "wg0", "/dev/stdin"]


def test_wg_syncconf_strip_failure_returns_false(monkeypatch):
    from wg_admin import wg
    import subprocess
    def fake_run(cmd, **kwargs):
        if cmd[1] == "strip":
            raise subprocess.CalledProcessError(1, cmd)
        class R:
            stdout = ""
            returncode = 0
        return R()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", fake_run)
    assert wg.wg_syncconf("wg0") is False


def test_wg_syncconf_syncconf_failure_returns_false(monkeypatch):
    from wg_admin import wg
    import subprocess
    def fake_run(cmd, **kwargs):
        if cmd[1] == "syncconf":
            raise subprocess.CalledProcessError(1, cmd)
        class R:
            stdout = "stripped"
            returncode = 0
        return R()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", fake_run)
    assert wg.wg_syncconf("wg0") is False


def test_wg_interface_active_true(monkeypatch):
    from wg_admin import wg
    class FakeResult:
        returncode = 0
    monkeypatch.setattr("wg_admin.wg.subprocess.run", lambda *a, **k: FakeResult())
    assert wg.wg_interface_active("wg0") is True


def test_wg_interface_active_false(monkeypatch):
    from wg_admin import wg
    class FakeResult:
        returncode = 3  # non-zero = inactive
    monkeypatch.setattr("wg_admin.wg.subprocess.run", lambda *a, **k: FakeResult())
    assert wg.wg_interface_active("wg0") is False


def test_wg_interface_active_file_not_found_returns_false(monkeypatch):
    from wg_admin import wg
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", boom)
    assert wg.wg_interface_active("wg0") is False


def test_generate_wg_conf_renders_mtu():
    from wg_admin.wg import generate_wg_conf
    out = generate_wg_conf(
        {"Address": "10.0.0.1/24", "ListenPort": "51820", "MTU": "1420", "PrivateKey": "K="},
        [],
    )
    assert "MTU = 1420" in out


def test_generate_wg_conf_omits_absent_mtu():
    from wg_admin.wg import generate_wg_conf
    out = generate_wg_conf({"Address": "10.0.0.1/24", "ListenPort": "51820"}, [])
    assert "MTU" not in out


def test_update_interface_in_conf_preserves_peers_verbatim(tmp_path):
    from wg_admin.wg import update_interface_in_conf
    original = """[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = PRIV=

# name: phone
[Peer]
PublicKey = PUB=
AllowedIPs = 10.0.0.2/32
"""
    p = tmp_path / "wg0.conf"
    p.write_text(original)
    update_interface_in_conf(p, {"ListenPort": "51821", "MTU": "1420"})
    content = p.read_text()
    assert "ListenPort = 51821" in content
    assert "MTU = 1420" in content
    assert "PrivateKey = PRIV=" in content
    assert "# name: phone" in content
    assert "PUB=" in content
    # atomic: no leftover tmp
    assert not (tmp_path / "wg0.conf.tmp").exists()


def test_update_interface_in_conf_keeps_commented_peer_blocks(tmp_path):
    from wg_admin.wg import update_interface_in_conf
    original = """[Interface]
Address = 10.0.0.1/24
ListenPort = 51820

# name: old
# [Peer]
# PublicKey = PUBOLD=
# AllowedIPs = 10.0.0.9/32
"""
    p = tmp_path / "wg0.conf"
    p.write_text(original)
    update_interface_in_conf(p, {"ListenPort": "51822"})
    content = p.read_text()
    assert "# [Peer]" in content
    assert "# PublicKey = PUBOLD=" in content


def test_update_interface_in_conf_empty_value_removes_key(tmp_path):
    from wg_admin.wg import update_interface_in_conf
    p = tmp_path / "wg0.conf"
    p.write_text("[Interface]\nAddress = 10.0.0.1/24\nMTU = 1420\nListenPort = 51820\n")
    iface = update_interface_in_conf(p, {"MTU": ""})
    content = p.read_text()
    assert "MTU" not in content
    assert "Address = 10.0.0.1/24" in content
    assert iface["Address"] == "10.0.0.1/24"


def test_update_interface_in_conf_postup_postdown_lists(tmp_path):
    from wg_admin.wg import update_interface_in_conf
    p = tmp_path / "wg0.conf"
    p.write_text("[Interface]\nAddress = 10.0.0.1/24\nListenPort = 51820\n")
    update_interface_in_conf(p, {"PostUp": ["echo up", "echo up2"], "PostDown": ["echo down"]})
    content = p.read_text()
    assert content.count("PostUp = ") == 2
    assert "PostUp = echo up2" in content
    assert content.count("PostDown = ") == 1


# ─── v0.3.3 coverage push: legacy `wg show dump` formats + subprocess wrappers ──

from types import SimpleNamespace

PUB = "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="
PUB2 = "RFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y="


def test_parse_dump_empty_output_returns_empty():
    from wg_admin.wg import parse_wg_show_dump
    assert parse_wg_show_dump("", "wg0") == []


def test_parse_dump_skips_short_lines():
    from wg_admin.wg import parse_wg_show_dump
    out = "\n".join(["wg0\tshort\tline", PUB + "\t(none)"])
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_dump_skips_interface_line():
    from wg_admin.wg import parse_wg_show_dump
    out = "wg0\tPRIV=\tPUB=\t51820\tfwmark"
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_dump_modern_without_psk():
    from wg_admin.wg import parse_wg_show_dump
    out = f"{PUB}\t1.2.3.4:51820\t10.0.0.2/32\t1718642000\t102400\t204800\t0"
    peers = parse_wg_show_dump(out, "wg0")
    assert len(peers) == 1
    assert peers[0].public_key == PUB
    assert peers[0].endpoint == "1.2.3.4:51820"
    assert peers[0].latest_handshake == 1718642000
    assert (peers[0].transfer_rx, peers[0].transfer_tx) == (102400, 204800)


def test_parse_dump_legacy_with_ifname_and_psk():
    from wg_admin.wg import parse_wg_show_dump
    out = (f"wg0\t{PUB}\tPSK=\t1.2.3.4:51820\t10.0.0.2/32\t"
           "1718642000\t102400\t204800\t0")
    peers = parse_wg_show_dump(out, "wg0")
    assert len(peers) == 1
    assert peers[0].public_key == PUB
    assert peers[0].latest_handshake == 1718642000


def test_parse_dump_legacy_with_ifname_without_psk():
    from wg_admin.wg import parse_wg_show_dump
    out = f"wg0\t{PUB}\t1.2.3.4:51820\t10.0.0.2/32\t1718642000\t102400\t204800\t0"
    peers = parse_wg_show_dump(out, "wg0")
    assert len(peers) == 1
    assert peers[0].endpoint == "1.2.3.4:51820"


def test_parse_dump_skips_non_numeric_handshake():
    from wg_admin.wg import parse_wg_show_dump
    out = f"{PUB}\t(none)\t1.2.3.4:51820\t10.0.0.2/32\twhenever\t1\t2"
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_dump_skips_invalid_pubkey():
    from wg_admin.wg import parse_wg_show_dump
    out = "short\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t1718642000\t1\t2"
    assert parse_wg_show_dump(out, "wg0") == []


def test_wg_pubkey_wraps_subprocess(monkeypatch):
    from wg_admin import wg
    calls = []
    monkeypatch.setattr(
        wg.subprocess, "run",
        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(stdout=PUB + "\n", returncode=0))
    assert wg.wg_pubkey("PRIV=") == PUB
    assert calls[0] == ["wg", "pubkey"]


def test_wg_show_dump_wraps_subprocess(monkeypatch):
    from wg_admin import wg
    dump = (f"wg0\tPRIV=\tPUB=\t51820\tfwmark\n"
            f"{PUB}\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t1718642000\t100\t200\t0\n")
    monkeypatch.setattr(wg.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(stdout=dump, returncode=0))
    peers = wg.wg_show_dump("wg0")
    assert len(peers) == 1 and peers[0].public_key == PUB


def test_unit_is_active(monkeypatch):
    from wg_admin import wg
    monkeypatch.setattr(wg.subprocess, "run",
                        lambda cmd: SimpleNamespace(returncode=0))
    assert wg.unit_is_active("wg-admin.service") is True
    monkeypatch.setattr(wg.subprocess, "run",
                        lambda cmd: SimpleNamespace(returncode=3))
    assert wg.unit_is_active("wg-admin.service") is False
    monkeypatch.setattr(wg.subprocess, "run",
                        lambda cmd: (_ for _ in ()).throw(FileNotFoundError("x")))
    assert wg.unit_is_active("wg-admin.service") is False


def test_server_pubkey_from_conf_without_private_key(tmp_path):
    from wg_admin.wg import wg_server_public_key_from_conf
    conf = tmp_path / "wg0.conf"
    conf.write_text("[Interface]\nAddress = 10.0.0.1/24\n")
    assert wg_server_public_key_from_conf(str(conf)) == ""


def test_server_pubkey_from_conf_derives_key(tmp_path, monkeypatch):
    from wg_admin import wg
    conf = tmp_path / "wg0.conf"
    conf.write_text("[Interface]\nPrivateKey = PRIV=\n")
    monkeypatch.setattr(wg.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(stdout=PUB + "\n", returncode=0))
    assert wg.wg_server_public_key_from_conf(str(conf)) == PUB


def test_update_interface_preserves_mode_on_stat_error(tmp_path, monkeypatch):
    from wg_admin import wg
    conf = tmp_path / "wg0.conf"
    conf.write_text("[Interface]\nPrivateKey = X\n")
    real_stat = wg.os.stat
    monkeypatch.setattr(wg.os, "stat",
                        lambda p, **kw: (_ for _ in ()).throw(OSError("gone")))
    try:
        wg.update_interface_in_conf(conf, {"MTU": "1400"})
    finally:
        monkeypatch.setattr(wg.os, "stat", real_stat)
    assert (conf.stat().st_mode & 0o777) == 0o600
    assert "MTU = 1400" in conf.read_text()


def test_apply_state_to_wg_reads_existing_conf(tmp_path, monkeypatch):
    """Existing wg0.conf interface section is reused (not defaulted)."""
    from pathlib import Path
    from wg_admin import wg

    conf = tmp_path / "wg0.conf"
    conf.write_text("[Interface]\nAddress = 10.9.9.1/24\nListenPort = 51820\n"
                    "PrivateKey = OLD=\n")

    def fake_path(p):
        return conf if str(p).endswith("wg0.conf") else Path(p)
    monkeypatch.setattr(wg, "Path", fake_path)
    written = {}
    monkeypatch.setattr(wg, "_write_conf_atomic",
                        lambda path, text, preserve_mode=False: written.update(path=path, text=text))
    monkeypatch.setattr(wg, "wg_syncconf", lambda iface: True)

    cfg = type("C", (), {"__getitem__": staticmethod(
        lambda k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1",
                          "subnet": "10.0.0.0/24"}}[k])})()
    wg.apply_state_to_wg({"peers": []}, cfg, mode="syncconf")
    assert "Address = 10.9.9.1/24" in written["text"]
    assert "ListenPort = 51820" in written["text"]


def test_parse_dump_legacy_ifname_too_few_fields():
    from wg_admin.wg import parse_wg_show_dump
    # 7 fields starting with the interface name: legacy branch, <8 -> skip
    out = "wg0\tA\tB\tC\tD\tE\tF"
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_dump_modern_short_line_skipped():
    from wg_admin.wg import parse_wg_show_dump
    # 5 fields, not interface-prefixed: modern branch, <7 -> skip
    out = "PUBKEY44=\tx\ty\t1718642000\t5"
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_dump_skips_non_numeric_transfer():
    from wg_admin.wg import parse_wg_show_dump
    out = f"{PUB}\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t1718642000\tabc\t1\t0"
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_dump_invalid_pubkey_with_valid_fields():
    from wg_admin.wg import parse_wg_show_dump
    out = f"short\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t1718642000\t1\t2\t0"
    assert parse_wg_show_dump(out, "wg0") == []


def test_parse_conf_commented_kv_inside_disabled_peer():
    from wg_admin.wg import parse_wg_conf
    content = ("[Interface]\nPrivateKey = X\n\n"
               "#[Peer]\n# just a comment header\n#PublicKey = Y=\n"
               "#AllowedIPs = 10.0.0.2/32\n")
    parsed = parse_wg_conf(content)
    assert len(parsed["peers"]) == 1
    assert parsed["peers"][0]["PublicKey"] == "Y="
    assert parsed["peers"][0]["disabled"] is True


def test_wg_server_public_key_live_wrapper(monkeypatch):
    from wg_admin import wg
    monkeypatch.setattr(wg.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(stdout=PUB + "\n", returncode=0))
    assert wg.wg_server_public_key("wg0") == PUB


def test_parse_conf_strips_inline_comments_on_active_lines():
    from wg_admin.wg import parse_wg_conf
    content = ("[Interface]\n"
               "ListenPort = 51820 # inline note\n"
               "PrivateKey = X= # another\n")
    parsed = parse_wg_conf(content)
    assert parsed["interface"]["ListenPort"] == "51820"
    assert parsed["interface"]["PrivateKey"] == "X="
