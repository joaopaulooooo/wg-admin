"""Tests for serverstats.collect()."""
import time

import pytest

from wg_admin import serverstats, wg


class FakeSDiskPart:
    def __init__(self, device, mountpoint, fstype):
        self.device = device
        self.mountpoint = mountpoint
        self.fstype = fstype


class FakeUsage:
    def __init__(self, percent, used, total):
        self.percent = percent
        self.used = used
        self.total = total


class FakeMem:
    def __init__(self, percent, used, total):
        self.percent = percent
        self.used = used
        self.total = total


def make_cfg(monkeypatch, endpoint="203.0.113.10", interface="wg0"):
    """Build a ConfigParser with the keys serverstats reads."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "wg": {"interface": interface},
        "peer_defaults": {"endpoint_host": endpoint},
    })
    return cfg


def patch_psutil(monkeypatch, *, disk_parts, mem, cpu_percent=12.5, cpu_count=4,
                 boot_time=1_000_000.0):
    """Replace psutil entry points used by collect()."""
    monkeypatch.setattr(serverstats.psutil, "cpu_percent", lambda interval=None: cpu_percent)
    monkeypatch.setattr(serverstats.psutil, "cpu_count", lambda logical=True: cpu_count)
    monkeypatch.setattr(serverstats.psutil, "virtual_memory", lambda: FakeMem(*mem))

    parts = [FakeSDiskPart(*p) for p in disk_parts["parts"]]
    usage_map = disk_parts["usage"]

    def fake_partitions(all=False):
        return parts

    def fake_disk_usage(path):
        if path not in usage_map:
            raise PermissionError(path)
        return FakeUsage(*usage_map[path])

    monkeypatch.setattr(serverstats.psutil, "disk_partitions", fake_partitions)
    monkeypatch.setattr(serverstats.psutil, "disk_usage", fake_disk_usage)
    monkeypatch.setattr(serverstats.psutil, "boot_time", lambda: boot_time)


def patch_wg(monkeypatch, statuses_by_interface):
    """wg_show_dump returns the provided list; raises KeyError if interface unknown."""
    def fake_dump(interface="wg0"):
        if interface not in statuses_by_interface:
            raise FileNotFoundError(f"wg0 not found: {interface}")
        return statuses_by_interface[interface]
    monkeypatch.setattr(serverstats.wg, "wg_show_dump", fake_dump)


def patch_platform(monkeypatch, hostname="vpn1", kernel="6.8.0-124", arch="x86_64"):
    monkeypatch.setattr(serverstats.platform, "node", lambda: hostname)
    monkeypatch.setattr(serverstats.platform, "release", lambda: kernel)
    monkeypatch.setattr(serverstats.platform, "machine", lambda: arch)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_collect_returns_all_expected_keys(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [("/dev/sda1", "/", "ext4")], "usage": {"/": (45.0, 45 * 1024**3, 100 * 1024**3)}},
                 mem=(41.0, 1_600_000_000, 4_000_000_000))
    patch_wg(monkeypatch, {"wg0": []})
    patch_platform(monkeypatch)
    monkeypatch.setattr(serverstats.time, "time", lambda: 2_000_000.0)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    expected = {
        "cpu_percent", "cpu_count", "mem_percent", "mem_used_gb", "mem_total_gb",
        "disks", "uptime_seconds", "load_avg", "hostname", "kernel", "arch",
        "public_ip", "wg",
    }
    assert expected.issubset(data.keys())
    assert data["cpu_percent"] == 12.5
    assert data["cpu_count"] == 4
    assert data["mem_percent"] == 41.0
    assert data["hostname"] == "vpn1"
    assert data["kernel"] == "6.8.0-124"
    assert data["arch"] == "x86_64"
    assert data["public_ip"] == "203.0.113.10"


def test_collect_disk_filters_tmpfs(monkeypatch):
    parts = [
        ("/dev/sda1", "/", "ext4"),
        ("tmpfs", "/tmp", "tmpfs"),
        ("proc", "/proc", "proc"),
        ("/dev/sda2", "/boot", "ext4"),
        ("/dev/mapper/data", "/data", "xfs"),
    ]
    usage = {
        "/": (67.0, 67 * 1024**3, 100 * 1024**3),
        "/boot": (22.0, 0.22 * 1024**3, 1 * 1024**3),
        "/data": (50.0, 500 * 1024**3, 1000 * 1024**3),
    }
    patch_psutil(monkeypatch,
                 disk_parts={"parts": parts, "usage": usage},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))
    patch_wg(monkeypatch, {"wg0": []})
    patch_platform(monkeypatch)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    mounts = [d["mount"] for d in data["disks"]]
    assert mounts == ["/", "/boot", "/data"]
    assert all(d["fstype"] in serverstats.PHYSICAL_FSTYPES for d in data["disks"])


def test_collect_disk_skips_unreadable_mount(monkeypatch):
    parts = [
        ("/dev/sda1", "/", "ext4"),
        ("/dev/sda2", "/restricted", "ext4"),
    ]
    usage = {"/": (10.0, 10 * 1024**3, 100 * 1024**3)}  # /restricted raises PermissionError
    patch_psutil(monkeypatch,
                 disk_parts={"parts": parts, "usage": usage},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))
    patch_wg(monkeypatch, {"wg0": []})
    patch_platform(monkeypatch)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    mounts = [d["mount"] for d in data["disks"]]
    assert mounts == ["/"]


def test_collect_handles_wg_down(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [], "usage": {}},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))
    patch_wg(monkeypatch, {})  # no entry for wg0 → raises
    patch_platform(monkeypatch)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    assert data["wg"]["interface_up"] is False
    assert data["wg"]["online_peers"] == 0
    assert data["wg"]["total_peers"] == 0
    assert data["wg"]["rx_rate_bps"] is None
    assert data["wg"]["tx_rate_bps"] is None


def test_collect_rate_first_call_is_none(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [], "usage": {}},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))
    peer = wg.PeerStatus(
        public_key="X=",
        endpoint=None,
        allowed_ips=[],
        latest_handshake=0,
        transfer_rx=1_000_000,
        transfer_tx=500_000,
    )
    patch_wg(monkeypatch, {"wg0": [peer]})
    patch_platform(monkeypatch)
    monkeypatch.setattr(serverstats.time, "time", lambda: 1_000.0)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    assert data["wg"]["interface_up"] is True
    assert data["wg"]["rx_rate_bps"] is None
    assert data["wg"]["tx_rate_bps"] is None
    assert data["wg"]["total_rx"] == 1_000_000
    assert data["wg"]["total_tx"] == 500_000


def test_collect_rate_second_call_computes_delta(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [], "usage": {}},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))

    peer_state = {"rx": 1_000_000, "tx": 500_000}

    def make_peer():
        return wg.PeerStatus(
            public_key="X=",
            endpoint=None,
            allowed_ips=[],
            latest_handshake=0,
            transfer_rx=peer_state["rx"],
            transfer_tx=peer_state["tx"],
        )

    patch_wg(monkeypatch, {"wg0": []})  # placeholder, replaced below
    serverstats.wg.wg_show_dump = lambda interface="wg0": [make_peer()]
    patch_platform(monkeypatch)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()

    clock = {"t": 1_000.0}
    monkeypatch.setattr(serverstats.time, "time", lambda: clock["t"])

    serverstats.collect(cfg)  # first sample

    peer_state["rx"] = 1_500_000  # +500KB
    peer_state["tx"] = 600_000    # +100KB
    clock["t"] = 1_005.0          # +5s
    data = serverstats.collect(cfg)

    # 500_000 bytes / 5s = 100_000 B/s
    assert data["wg"]["rx_rate_bps"] == 100_000
    # 100_000 bytes / 5s = 20_000 B/s
    assert data["wg"]["tx_rate_bps"] == 20_000


def test_collect_counts_online_peers(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [], "usage": {}},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))
    now = 2_000_000
    peer_online = wg.PeerStatus(
        public_key="A=",
        endpoint=None,
        allowed_ips=[],
        latest_handshake=now - 60,  # 1 min ago
        transfer_rx=0, transfer_tx=0,
    )
    peer_offline = wg.PeerStatus(
        public_key="B=",
        endpoint=None,
        allowed_ips=[],
        latest_handshake=now - 3600,  # 1h ago
        transfer_rx=0, transfer_tx=0,
    )
    patch_wg(monkeypatch, {"wg0": [peer_online, peer_offline]})
    patch_platform(monkeypatch)
    monkeypatch.setattr(serverstats.time, "time", lambda: float(now))

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    assert data["wg"]["online_peers"] == 1
    assert data["wg"]["total_peers"] == 2


def test_collect_public_ip_falls_back_empty(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [], "usage": {}},
                 mem=(50.0, 2_000_000_000, 4_000_000_000))
    patch_wg(monkeypatch, {"wg0": []})
    patch_platform(monkeypatch)

    cfg = make_cfg(monkeypatch, endpoint="")
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    assert data["public_ip"] == ""


def test_collect_uptime(monkeypatch):
    patch_psutil(monkeypatch,
                 disk_parts={"parts": [], "usage": {}},
                 mem=(50.0, 2_000_000_000, 4_000_000_000),
                 boot_time=1_000.0)
    patch_wg(monkeypatch, {"wg0": []})
    patch_platform(monkeypatch)
    monkeypatch.setattr(serverstats.time, "time", lambda: 1_900.0)

    cfg = make_cfg(monkeypatch)
    serverstats.reset_net_sample()
    data = serverstats.collect(cfg)

    assert data["uptime_seconds"] == 900
