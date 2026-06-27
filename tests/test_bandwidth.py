# tests/test_bandwidth.py
import json
import secrets
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from wg_admin import bandwidth
from wg_admin.wg import PeerStatus


def _make_status(pubkey="Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw=", rx=0, tx=0):
    return PeerStatus(
        public_key=pubkey,
        endpoint=None,
        allowed_ips=[],
        latest_handshake=0,
        transfer_rx=rx,
        transfer_tx=tx,
    )


# Short alias for tests that need a default fake key (used in early tests)
PUB1 = "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="


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
    assert PUB1 in data["peers"]
    assert data["peers"][PUB1]["total_rx"] == 0  # first sample, no delta yet
    assert data["peers"][PUB1]["total_tx"] == 0
    assert data["peers"][PUB1]["last_sample"]["rx"] == 1000


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
    p = data["peers"][PUB1]
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
    p = data["peers"][PUB1]
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
    assert today in data["peers"][PUB1]["daily"]
    assert data["peers"][PUB1]["daily"][today]["rx"] == 2000
    assert data["peers"][PUB1]["daily"][today]["tx"] == 3000


def test_prune_old_daily_buckets(tmp_path, monkeypatch):
    bw_path = tmp_path / "bw.json"

    # Seed with old + recent data
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")

    bw_path.write_text(json.dumps({
        "peers": {
            PUB1: {
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
    daily = data["peers"][PUB1]["daily"]
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
            PUB1: {
                "first_seen": dates[-1],
                "total_rx": 999999999,
                "total_tx": 888888888,
                "daily": daily,
                "last_sample": {"ts": "2026-01-01T00:00:00Z", "rx": 0, "tx": 0}
            }
        }
    }))

    bw = bandwidth.load_bandwidth(bw_path)
    stats = bandwidth.get_peer_stats(bw, PUB1)

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


def test_main_track_runs_quota_check_and_saves_changes(tmp_path, monkeypatch):
    """When quota changes peer state, main() saves state + applies to wg."""
    from wg_admin import bandwidth, state as state_mod, config as config_mod

    # Set up paths
    state_path = tmp_path / "state.json.enc"
    config_path = tmp_path / "config.ini"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    master_key = secrets.token_bytes(32)
    (secrets_dir / "master.key").write_bytes(master_key)

    monkeypatch.setattr("wg_admin.bandwidth.STATE_PATH", state_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.SECRETS_DIR", secrets_dir, raising=False)

    # Seed state with one peer over quota
    s = state_mod.empty_state()
    from datetime import datetime, timezone, timedelta
    daily = {}
    for i in range(10):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"rx": 1024**3, "tx": 0}  # 1 GB per day
    s["peers"] = [{
        "id": "1", "name": "x", "public_key": "PUB",
        "private_key_enc": "ENC", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 5.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "...",
    }]
    state_mod.save_state(state_path, s, master_key)

    # Seed bandwidth.json matching pubkey
    bw_path = tmp_path / "bandwidth.json"
    bw_path.write_text(json.dumps({"peers": {"PUB": {
        "first_seen": "2026-06-01T00:00:00Z",
        "total_rx": 10 * 1024**3, "total_tx": 0,
        "daily": daily,
        "last_sample": {"ts": "2026-06-27T00:00:00Z", "rx": 0, "tx": 0},
    }}}))

    # Write minimal config
    config_path.write_text("[wg]\ninterface = wg0\nsubnet = 10.0.0.0/24\nserver_ip = 10.0.0.1\n[quota]\nglobal_quota_gb = 0\n")

    # Stub wg_show_dump to return empty (no traffic to add)
    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: [])
    apply_calls = []
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": apply_calls.append(mode))

    # Run main
    import sys
    monkeypatch.setattr(sys, "argv", ["bandwidth.py", "track", str(bw_path)])
    bandwidth.main()

    # Verify state was updated
    new_state = state_mod.load_state(state_path, master_key)
    assert new_state["peers"][0]["quota_suspended"] is True
    assert apply_calls == ["syncconf"]


def test_main_track_no_changes_does_not_save_state(tmp_path, monkeypatch):
    """If no quota changes, state file is not rewritten."""
    from wg_admin import bandwidth, state as state_mod

    state_path = tmp_path / "state.json.enc"
    config_path = tmp_path / "config.ini"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    master_key = secrets.token_bytes(32)
    (secrets_dir / "master.key").write_bytes(master_key)

    monkeypatch.setattr("wg_admin.bandwidth.STATE_PATH", state_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.SECRETS_DIR", secrets_dir, raising=False)

    s = state_mod.empty_state()
    s["peers"] = [{"id": "1", "name": "x", "public_key": "PUB",
                   "private_key_enc": "ENC", "ip": "10.0.0.2", "disabled": False,
                   "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
                   "created_at": "..."}]
    state_mod.save_state(state_path, s, master_key)
    original_mtime = state_path.stat().st_mtime

    config_path.write_text("[wg]\ninterface = wg0\nsubnet = 10.0.0.0/24\nserver_ip = 10.0.0.1\n")

    bw_path = tmp_path / "bandwidth.json"
    bw_path.write_text(json.dumps({"peers": {}}))

    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: [])
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda *a, **k: None)

    import sys, time
    monkeypatch.setattr(sys, "argv", ["bandwidth.py", "track", str(bw_path)])
    bandwidth.main()
    time.sleep(0.1)
    assert state_path.stat().st_mtime == original_mtime


def test_sparkline_path_empty_values_returns_empty():
    from wg_admin.bandwidth import sparkline_path
    assert sparkline_path([]) == ""
    assert sparkline_path([0, 0, 0]) == ""


def test_sparkline_path_returns_valid_svg_path():
    from wg_admin.bandwidth import sparkline_path
    result = sparkline_path([1, 5, 3, 8, 2], width=100, height=20)
    assert result.startswith("M ")
    assert " L " in result
    # 5 points -> 4 "L" segments
    assert result.count(" L ") == 4


def test_sparkline_path_normalizes_to_height():
    from wg_admin.bandwidth import sparkline_path
    # Max value should map to y=0 (top of svg)
    result = sparkline_path([0, 10, 0], width=30, height=10)
    # Path looks like "M 0.0,10.0 L 15.0,0.0 L 30.0,10.0"
    parts = result.replace("M ", "").split(" L ")
    assert parts[1] == "15.0,0.0"


def test_sparkline_path_single_value_does_not_crash():
    from wg_admin.bandwidth import sparkline_path
    # Only 1 value -- would divide by zero. Acceptable: return empty.
    result = sparkline_path([5], width=80, height=24)
    assert isinstance(result, str)
    assert result == ""  # spec: return empty for single value
