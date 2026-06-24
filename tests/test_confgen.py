# tests/test_confgen.py
from wg_admin import confgen


def test_render_conf_has_interface_section():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=["1.1.1.1"],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "[Interface]" in out


def test_render_conf_has_peer_section():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=[],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "[Peer]" in out
    assert "PublicKey = PUB=" in out
    assert "Endpoint = vpn.example.com:51820" in out
    assert "AllowedIPs = 0.0.0.0/0" in out


def test_render_conf_omits_dns_when_empty():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=[],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "DNS" not in out


def test_render_conf_joins_multiple_dns():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=["1.1.1.1", "1.0.0.1"],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "DNS = 1.1.1.1,1.0.0.1" in out


def test_render_conf_joins_multiple_allowed_ips():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=[],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0", "::/0"],
    )
    out = confgen.render_conf(peer)
    assert "AllowedIPs = 0.0.0.0/0,::/0" in out


def test_render_qr_returns_valid_png():
    png = confgen.render_qr_png("[Interface]\nPrivateKey = x\n")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes


def test_render_qr_grows_with_data_size():
    small = confgen.render_qr_png("x")
    large = confgen.render_qr_png("x" * 200)
    assert len(large) > len(small)
