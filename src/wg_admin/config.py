"""Load config.ini with defaults."""
from __future__ import annotations

import configparser
from pathlib import Path
from typing import List

DEFAULTS = {
    "wg": {
        "interface": "wg0",
        "subnet": "10.0.0.0/24",
        "server_ip": "10.0.0.1",
    },
    "peer_defaults": {
        "endpoint_host": "",
        "endpoint_port": "51820",
        "allowed_ips": "0.0.0.0/0",
        "dns": "",
    },
    "server": {
        "listen_port": "51821",
        "session_lifetime_seconds": "3600",
    },
}


def load_config(path: Path | None = None) -> configparser.ConfigParser:
    """Load config from path, applying defaults. Missing file = all defaults."""
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULTS)
    if path is not None and path.exists():
        cfg.read(path)
    return cfg


def get_dns_list(cfg: configparser.ConfigParser) -> List[str]:
    raw = cfg["peer_defaults"].get("dns", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_allowed_ips_list(cfg: configparser.ConfigParser) -> List[str]:
    raw = cfg["peer_defaults"].get("allowed_ips", "0.0.0.0/0")
    return [s.strip() for s in raw.split(",") if s.strip()]
