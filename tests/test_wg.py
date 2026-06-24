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
