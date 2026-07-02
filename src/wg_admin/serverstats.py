"""Server metrics collection via psutil + wg show dump.

Stateless except for the network rate sample, which is kept module-level
so two consecutive collect() calls can compute rx/tx per second.
"""
from __future__ import annotations

import os
import platform
import time
from typing import Optional

import psutil

from . import wg

PHYSICAL_FSTYPES = {"ext4", "ext3", "ext2", "btrfs", "xfs", "zfs", "f2fs"}

_last_net_sample: Optional[dict] = None


def _format_rate(delta_bytes: int, elapsed: float) -> Optional[int]:
    if delta_bytes < 0 or elapsed <= 0:
        return None
    return int(delta_bytes / elapsed)


def collect(cfg) -> dict:
    """Snapshot of CPU/mem/disk/uptime/load/network/wg.

    Individual fields degrade to None if their source fails; wg section
    degrades to interface_up=False if wg0 is down.
    """
    global _last_net_sample

    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True) or 0

    mem = psutil.virtual_memory()
    mem_total_gb = mem.total / (1024**3)
    mem_used_gb = mem.used / (1024**3)

    disks = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype not in PHYSICAL_FSTYPES:
            continue
        if not part.mountpoint:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "device": part.device,
            "mount": part.mountpoint,
            "fstype": part.fstype,
            "percent": usage.percent,
            "used_gb": round(usage.used / (1024**3), 1),
            "total_gb": round(usage.total / (1024**3), 1),
        })

    boot_ts = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_ts) if boot_ts else 0
    try:
        load_avg = list(os.getloadavg())
    except (OSError, AttributeError):
        load_avg = []

    hostname = platform.node()
    kernel = platform.release()
    arch = platform.machine()

    endpoint = cfg["peer_defaults"].get("endpoint_host", "") if cfg.has_section("peer_defaults") else ""
    public_ip = endpoint.strip() if endpoint else ""

    wg_block = _collect_wg(cfg)

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "mem_percent": mem.percent,
        "mem_used_gb": round(mem_used_gb, 2),
        "mem_total_gb": round(mem_total_gb, 2),
        "disks": disks,
        "uptime_seconds": uptime_seconds,
        "load_avg": load_avg,
        "hostname": hostname,
        "kernel": kernel,
        "arch": arch,
        "public_ip": public_ip,
        "wg": wg_block,
    }


def _collect_wg(cfg) -> dict:
    """Aggregate WireGuard stats. Degrades gracefully when interface is down."""
    global _last_net_sample

    interface = cfg["wg"]["interface"] if cfg.has_section("wg") else "wg0"
    now = time.time()

    try:
        statuses = wg.wg_show_dump(interface)
    except Exception:
        _last_net_sample = None
        return {
            "interface_up": False,
            "online_peers": 0,
            "total_peers": 0,
            "total_rx": 0,
            "total_tx": 0,
            "rx_rate_bps": None,
            "tx_rate_bps": None,
        }

    now_ts = int(now)
    ONLINE_WINDOW = 180
    online = 0
    total_rx = 0
    total_tx = 0
    for st in statuses:
        if st.latest_handshake and st.latest_handshake > now_ts - ONLINE_WINDOW:
            online += 1
        total_rx += st.transfer_rx or 0
        total_tx += st.transfer_tx or 0

    rx_rate = None
    tx_rate = None
    prev = _last_net_sample
    if prev is not None:
        elapsed = now - prev["ts"]
        rx_rate = _format_rate(total_rx - prev["rx"], elapsed)
        tx_rate = _format_rate(total_tx - prev["tx"], elapsed)

    _last_net_sample = {"ts": now, "rx": total_rx, "tx": total_tx}

    return {
        "interface_up": True,
        "online_peers": online,
        "total_peers": len(statuses),
        "total_rx": total_rx,
        "total_tx": total_tx,
        "rx_rate_bps": rx_rate,
        "tx_rate_bps": tx_rate,
    }


def reset_net_sample() -> None:
    """Clear the module-level net sample. Used in tests to force a known state."""
    global _last_net_sample
    _last_net_sample = None

