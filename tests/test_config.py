import textwrap
import pytest

from wg_admin import config


SAMPLE_INI = textwrap.dedent("""
[wg]
interface = wg0
subnet = 10.0.0.0/24
server_ip = 10.0.0.1

[peer_defaults]
endpoint_host = vpn.example.com
endpoint_port = 51820
allowed_ips = 0.0.0.0/0
dns = 1.1.1.1, 1.0.0.1

[server]
listen_port = 51821
session_lifetime_seconds = 3600
""")


def test_load_returns_parser_with_sections(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(SAMPLE_INI)
    cfg = config.load_config(p)
    assert cfg["wg"]["interface"] == "wg0"
    assert cfg["peer_defaults"]["endpoint_host"] == "vpn.example.com"
    assert cfg["server"]["listen_port"] == "51821"


def test_load_returns_defaults_when_file_missing(tmp_path):
    cfg = config.load_config(tmp_path / "nonexistent.ini")
    assert cfg["wg"]["interface"] == "wg0"
    assert cfg["wg"]["subnet"] == "10.0.0.0/24"
    assert cfg["wg"]["server_ip"] == "10.0.0.1"
    assert cfg["peer_defaults"]["allowed_ips"] == "0.0.0.0/0"
    assert cfg["server"]["listen_port"] == "51821"


def test_load_returns_defaults_when_section_missing(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[wg]\ninterface = wg0\n")
    cfg = config.load_config(p)
    assert cfg["peer_defaults"]["allowed_ips"] == "0.0.0.0/0"


def test_dns_list_parsed_correctly(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(SAMPLE_INI)
    cfg = config.load_config(p)
    dns = config.get_dns_list(cfg)
    assert dns == ["1.1.1.1", "1.0.0.1"]


def test_allowed_ips_list_parsed_correctly(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(SAMPLE_INI)
    cfg = config.load_config(p)
    ips = config.get_allowed_ips_list(cfg)
    assert ips == ["0.0.0.0/0"]


def test_load_has_quota_section_default(tmp_path):
    cfg = config.load_config(tmp_path / "nonexistent.ini")
    assert cfg["quota"]["global_quota_gb"] == "0"
    assert cfg["quota"].getfloat("global_quota_gb") == 0.0


def test_load_quota_section_overridden_by_file(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[quota]\nglobal_quota_gb = 250\n")
    cfg = config.load_config(p)
    assert cfg["quota"].getfloat("global_quota_gb") == 250.0
