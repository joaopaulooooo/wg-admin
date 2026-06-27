"""Bandwidth tracking: samples wg show periodically and aggregates per peer.

State is stored in a separate JSON file (not encrypted — only contains
public keys + byte counters, both non-sensitive). Schema:

{
  "peers": {
    "<pubkey>": {
      "first_seen": "<iso8601>",
      "total_rx": <bytes cumulative>,
      "total_tx": <bytes cumulative>,
      "daily": {
        "YYYY-MM-DD": {"rx": <bytes>, "tx": <bytes>},
        ...
      },
      "last_sample": {"ts": "<iso8601>", "rx": <bytes>, "tx": <bytes>}
    }
  }
}

CLI: python -m wg_admin.bandwidth track
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from . import wg

DEFAULT_PATH = Path("/wg-admin/bandwidth.json")
RETENTION_DAYS = 30

# These mirror app.py paths. Patchable in tests.
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
SECRETS_DIR = Path("/wg-admin/secrets")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def cutoff_date_str(days: int = RETENTION_DAYS) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def format_bytes(n: int) -> str:
    """Human-readable byte formatting: 0 B / 1.0 KB / 5.2 MB / ..."""
    if n < 1024:
        return f"{n} B"
    units = [("KB", 1024), ("MB", 1024**2), ("GB", 1024**3), ("TB", 1024**4), ("PB", 1024**5)]
    for unit, factor in units:
        if n < factor * 1024 or unit == "PB":
            return f"{n / factor:.1f} {unit}"
    return f"{n / 1024**5:.1f} PB"


def sparkline_path(values: list, width: int = 80, height: int = 24) -> str:
    """Generate SVG path 'd' attribute for a sparkline.

    values: list of non-negative numbers (bytes per day, typically).
    Returns the SVG path string, or empty string if no data.
    """
    if not values or max(values) == 0 or len(values) < 2:
        return ""
    max_v = max(values)
    step = width / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = i * step
        y = height - (v / max_v) * height
        points.append(f"{x:.1f},{y:.1f}")
    return "M " + " L ".join(points)


def load_bandwidth(path: Path = DEFAULT_PATH) -> dict:
    if not path.exists():
        return {"peers": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"peers": {}}


def _atomic_write(path: Path, data: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    payload = json.dumps(data, separators=(",", ":")).encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def track_sample(path: Path = DEFAULT_PATH, interface: str = "wg0") -> None:
    """Sample wg show once, update running totals + daily buckets."""
    data = load_bandwidth(path)
    peers_dict = data.setdefault("peers", {})

    try:
        statuses = wg.wg_show_dump(interface)
    except Exception as e:
        print(f"WARN: wg show failed: {e}", file=sys.stderr)
        return

    today = today_str()
    now = utc_now_iso()

    for status in statuses:
        pub = status.public_key
        # Sanity check: skip invalid pubkeys (parser bug fallback)
        # Real WireGuard pubkeys are 44-char base64 ending with '='
        if not pub or "=" not in pub or len(pub) < 40 or pub == "(none)":
            continue
        peer = peers_dict.get(pub)

        if peer is None:
            # First time seeing this peer
            peer = {
                "first_seen": now,
                "total_rx": 0,
                "total_tx": 0,
                "daily": {},
                "last_sample": {"ts": now, "rx": status.transfer_rx, "tx": status.transfer_tx},
            }
            peers_dict[pub] = peer
            continue  # no delta to accumulate on first sample

        last = peer.get("last_sample", {"rx": 0, "tx": 0})
        prev_rx = last.get("rx", 0)
        prev_tx = last.get("tx", 0)

        # Counter reset detection: if current < previous, treat current as the delta
        # (happens on wg-quick restart, server reboot, peer recreation)
        delta_rx = status.transfer_rx if status.transfer_rx < prev_rx else status.transfer_rx - prev_rx
        delta_tx = status.transfer_tx if status.transfer_tx < prev_tx else status.transfer_tx - prev_tx

        peer["total_rx"] += delta_rx
        peer["total_tx"] += delta_tx

        daily = peer.setdefault("daily", {})
        bucket = daily.setdefault(today, {"rx": 0, "tx": 0})
        bucket["rx"] += delta_rx
        bucket["tx"] += delta_tx

        peer["last_sample"] = {"ts": now, "rx": status.transfer_rx, "tx": status.transfer_tx}

    # Prune daily buckets older than RETENTION_DAYS
    cutoff = cutoff_date_str()
    for peer in peers_dict.values():
        daily = peer.get("daily", {})
        peer["daily"] = {d: v for d, v in daily.items() if d >= cutoff}

    _atomic_write(path, data)


def get_peer_stats(bw: dict, pubkey: str) -> dict:
    """Return {total_rx, total_tx, thirty_day_rx, thirty_day_tx, first_seen} for a peer."""
    if not pubkey or pubkey == "(none)":
        return {
            "total_rx": 0, "total_tx": 0,
            "thirty_day_rx": 0, "thirty_day_tx": 0,
            "first_seen": None,
        }
    peer = bw.get("peers", {}).get(pubkey)
    if peer is None:
        return {
            "total_rx": 0, "total_tx": 0,
            "thirty_day_rx": 0, "thirty_day_tx": 0,
            "first_seen": None,
        }

    cutoff = cutoff_date_str()
    # Strict greater than cutoff — gives exactly 30 days inclusive of today
    thirty_rx = sum(v["rx"] for d, v in peer.get("daily", {}).items() if d > cutoff)
    thirty_tx = sum(v["tx"] for d, v in peer.get("daily", {}).items() if d > cutoff)

    return {
        "total_rx": peer.get("total_rx", 0),
        "total_tx": peer.get("total_tx", 0),
        "thirty_day_rx": thirty_rx,
        "thirty_day_tx": thirty_tx,
        "first_seen": peer.get("first_seen"),
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "track":
        path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PATH
        track_sample(path)
        _run_quota_check(path)
        return 0
    print("Usage: python -m wg_admin.bandwidth track [path]", file=sys.stderr)
    return 1


def _run_quota_check(bw_path: Path) -> None:
    """After sampling, check quotas and apply state if any peer changed."""
    try:
        from . import config as config_mod, quota, state as state_mod, wg
        master_key_path = SECRETS_DIR / "master.key"
        if not master_key_path.exists():
            return  # not fully installed
        master_key = master_key_path.read_bytes()
        state_data = state_mod.load_state(STATE_PATH, master_key)
        cfg = config_mod.load_config(CONFIG_PATH)
        global_q = cfg["quota"].getfloat("global_quota_gb", 0)
        bw = load_bandwidth(bw_path)
        changes = quota.check_quotas(state_data, bw, global_q)
        if changes:
            state_mod.save_state(STATE_PATH, state_data, master_key)
            wg.apply_state_to_wg(state_data, cfg, mode="syncconf")
    except Exception as e:
        print(f"WARN: quota check failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
