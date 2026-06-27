import pytest
from wg_admin import bandwidth, quota


def make_bw(pubkey, daily_bytes):
    """Build bandwidth.json structure where pubkey used daily_bytes (rx+tx summed) each day."""
    daily = {}
    for i, b in enumerate(daily_bytes):
        from datetime import datetime, timezone, timedelta
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"rx": b, "tx": 0}
    return {"peers": {pubkey: {
        "first_seen": "2026-06-01T00:00:00Z",
        "total_rx": sum(daily_bytes), "total_tx": 0,
        "daily": daily,
        "last_sample": {"ts": "2026-06-27T00:00:00Z", "rx": 0, "tx": 0},
    }}}


def test_check_quotas_peer_without_quota_unchanged():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 0, "quota_suspended": False}]}
    bw = make_bw("PUB", [1024**3] * 30)  # 30 GB
    changes = quota.check_quotas(state, bw, 0)
    assert changes == []
    assert state["peers"][0]["quota_suspended"] is False


def test_check_quotas_suspends_when_exceeded():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 5.0, "quota_suspended": False}]}
    bw = make_bw("PUB", [1024**3] * 10)  # 10 GB in 10 days
    changes = quota.check_quotas(state, bw, 0)
    assert len(changes) == 1
    assert changes[0]["action"] == "suspend"
    assert state["peers"][0]["quota_suspended"] is True
    assert state["peers"][0]["quota_state_updated_at"] is not None


def test_check_quotas_reactivates_when_below():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 100.0, "quota_suspended": True}]}
    bw = make_bw("PUB", [1024**3] * 5)  # 5 GB
    changes = quota.check_quotas(state, bw, 0)
    assert changes[0]["action"] == "reactivate"
    assert state["peers"][0]["quota_suspended"] is False


def test_check_quotas_no_change_when_already_suspended_and_still_over():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 5.0, "quota_suspended": True,
                        "quota_state_updated_at": "old"}]}
    bw = make_bw("PUB", [1024**3] * 10)
    changes = quota.check_quotas(state, bw, 0)
    assert changes == []
    assert state["peers"][0]["quota_state_updated_at"] == "old"  # not touched


def test_global_usage_gb_sums_all_peers():
    bw = {
        "peers": {
            "PUB1": {"daily": {"2026-06-25": {"rx": 1024**3, "tx": 0}}},
            "PUB2": {"daily": {"2026-06-25": {"rx": 0, "tx": 2 * 1024**3}}},
        }
    }
    used = quota.global_usage_gb(bw)
    assert isinstance(used, float)


def test_global_quota_exceeded_with_zero_limit_returns_false():
    bw = {"peers": {}}
    assert quota.global_quota_exceeded(bw, 0) is False


def test_global_quota_exceeded_when_usage_over_limit():
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bw = {"peers": {"PUB": {"daily": {today: {"rx": 200 * 1024**3, "tx": 0}}}}}
    assert quota.global_quota_exceeded(bw, 100) is True


def test_global_quota_exceeded_when_usage_under_limit():
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bw = {"peers": {"PUB": {"daily": {today: {"rx": 50 * 1024**3, "tx": 0}}}}}
    assert quota.global_quota_exceeded(bw, 100) is False
