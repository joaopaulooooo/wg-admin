# tests/test_bandwidth.py
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from wg_admin import bandwidth
from wg_admin.wg import PeerStatus


def _make_status(pubkey="PUB1", rx=0, tx=0):
    return PeerStatus(
        public_key=pubkey,
        endpoint=None,
        allowed_ips=[],
        latest_handshake=0,
        transfer_rx=rx,
        transfer_tx=tx,
    )


def test_format_bytes_small():
    assert bandwidth.format_bytes(0) == "0 B"
    assert bandwidth.format_bytes(500) == "500 B"
    assert bandwidth.format_bytes(1024) == "1.0 KB"
    assert bandwidth.format_bytes(1024 * 1024) == "1.0 MB"
    assert bandwidth.format_bytes(1024 * 1024 * 1024) == "1.0 GB"


def test_format_bytes_large():
    assert bandwidth.format_bytes(5_500_000_000) == "5.1 GB"
    assert bandwidth.format_bytes(1024 ** 4) == "1.0 TB"


def test_track_sample_creates_file_if_missing(tmp_path, monkeypatch):
    bw_path = tmp_path / "bw.json"

    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=1000, tx=2000)]
    )

    bandwidth.track_sample(bw_path)

    data = json.loads(bw_path.read_text())
    assert "PUB1" in data["peers"]
    assert data["peers"]["PUB1"]["total_rx"] == 0  # first sample, no delta yet
    assert data["peers"]["PUB1"]["total_tx"] == 0
    assert data["peers"]["PUB1"]["last_sample"]["rx"] == 1000


def test_track_sample_accumulates_delta(tmp_path, monkeypatch):
    bw_path = tmp_path / "bw.json"

    # First sample
    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=1000, tx=2000)]
    )
    bandwidth.track_sample(bw_path)

    # Second sample with growth
    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=1500, tx=3000)]
    )
    bandwidth.track_sample(bw_path)

    data = json.loads(bw_path.read_text())
    p = data["peers"]["PUB1"]
    assert p["total_rx"] == 500  # 1500 - 1000
    assert p["total_tx"] == 1000  # 3000 - 2000


def test_track_sample_handles_counter_reset(tmp_path, monkeypatch):
    """wg-quick restart makes counters go to 0 — must treat as fresh, not negative delta."""
    bw_path = tmp_path / "bw.json"

    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=10000, tx=20000)]
    )
    bandwidth.track_sample(bw_path)

    # Counter resets to 0 (restart) then grows to 500
    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=500, tx=800)]
    )
    bandwidth.track_sample(bw_path)

    data = json.loads(bw_path.read_text())
    p = data["peers"]["PUB1"]
    # After reset: delta_rx=500 (entire current value), delta_tx=800
    assert p["total_rx"] == 500
    assert p["total_tx"] == 800


def test_track_sample_populates_daily_bucket(tmp_path, monkeypatch):
    bw_path = tmp_path / "bw.json"

    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=1000, tx=2000)]
    )
    bandwidth.track_sample(bw_path)

    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=3000, tx=5000)]
    )
    bandwidth.track_sample(bw_path)

    data = json.loads(bw_path.read_text())
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert today in data["peers"]["PUB1"]["daily"]
    assert data["peers"]["PUB1"]["daily"][today]["rx"] == 2000
    assert data["peers"]["PUB1"]["daily"][today]["tx"] == 3000


def test_prune_old_daily_buckets(tmp_path, monkeypatch):
    bw_path = tmp_path / "bw.json"

    # Seed with old + recent data
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")

    bw_path.write_text(json.dumps({
        "peers": {
            "PUB1": {
                "first_seen": old_date,
                "total_rx": 1000000,
                "total_tx": 2000000,
                "daily": {
                    old_date: {"rx": 1000, "tx": 2000},
                    recent_date: {"rx": 500, "tx": 800},
                },
                "last_sample": {"ts": recent_date, "rx": 0, "tx": 0}
            }
        }
    }))

    monkeypatch.setattr(
        bandwidth.wg, "wg_show_dump",
        lambda iface="wg0": [_make_status(rx=0, tx=0)]
    )
    bandwidth.track_sample(bw_path)

    data = json.loads(bw_path.read_text())
    daily = data["peers"]["PUB1"]["daily"]
    assert old_date not in daily  # pruned
    assert recent_date in daily   # kept


def test_get_peer_stats_returns_zero_for_unknown(tmp_path):
    bw_path = tmp_path / "bw.json"
    bw_path.write_text(json.dumps({"peers": {}}))

    bw = bandwidth.load_bandwidth(bw_path)
    stats = bandwidth.get_peer_stats(bw, "UNKNOWN")
    assert stats["total_rx"] == 0
    assert stats["total_tx"] == 0
    assert stats["thirty_day_rx"] == 0
    assert stats["thirty_day_tx"] == 0


def test_get_peer_stats_aggregates_30_days(tmp_path):
    bw_path = tmp_path / "bw.json"
    today = datetime.now(timezone.utc)
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(35)]

    daily = {}
    for i, d in enumerate(dates):
        daily[d] = {"rx": 100 * (i + 1), "tx": 50 * (i + 1)}

    bw_path.write_text(json.dumps({
        "peers": {
            "PUB1": {
                "first_seen": dates[-1],
                "total_rx": 999999999,
                "total_tx": 888888888,
                "daily": daily,
                "last_sample": {"ts": "2026-01-01T00:00:00Z", "rx": 0, "tx": 0}
            }
        }
    }))

    bw = bandwidth.load_bandwidth(bw_path)
    stats = bandwidth.get_peer_stats(bw, "PUB1")

    # Last 30 days only — days 31-35 should be excluded
    expected_rx = sum(100 * (i + 1) for i in range(30))
    expected_tx = sum(50 * (i + 1) for i in range(30))
    assert stats["thirty_day_rx"] == expected_rx
    assert stats["thirty_day_tx"] == expected_tx


def test_track_sample_handles_empty_wg(tmp_path, monkeypatch):
    """No peers in wg show — should be no-op."""
    bw_path = tmp_path / "bw.json"
    monkeypatch.setattr(bandwidth.wg, "wg_show_dump", lambda iface="wg0": [])
    bandwidth.track_sample(bw_path)

    data = json.loads(bw_path.read_text())
    assert data["peers"] == {}
