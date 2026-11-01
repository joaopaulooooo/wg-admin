import time
import pytest

from wg_admin import ratelimit


def test_is_blocked_returns_false_initially(tmp_path):
    rl_path = tmp_path / "rl.json"
    assert ratelimit.is_blocked(rl_path, "1.2.3.4") is False


def test_record_fail_increments_counter(tmp_path):
    rl_path = tmp_path / "rl.json"
    ratelimit.record_fail(rl_path, "1.2.3.4")
    ratelimit.record_fail(rl_path, "1.2.3.4")
    state = ratelimit._load(rl_path)
    assert state["1.2.3.4"]["fails"] == 2


def test_record_fail_separates_by_ip(tmp_path):
    rl_path = tmp_path / "rl.json"
    ratelimit.record_fail(rl_path, "1.2.3.4")
    ratelimit.record_fail(rl_path, "5.6.7.8")
    state = ratelimit._load(rl_path)
    assert state["1.2.3.4"]["fails"] == 1
    assert state["5.6.7.8"]["fails"] == 1


def test_blocks_after_threshold(tmp_path):
    rl_path = tmp_path / "rl.json"
    for _ in range(5):
        ratelimit.record_fail(rl_path, "1.2.3.4")
    assert ratelimit.is_blocked(rl_path, "1.2.3.4") is True


def test_does_not_block_below_threshold(tmp_path):
    rl_path = tmp_path / "rl.json"
    for _ in range(4):
        ratelimit.record_fail(rl_path, "1.2.3.4")
    assert ratelimit.is_blocked(rl_path, "1.2.3.4") is False


def test_clear_removes_entry(tmp_path):
    rl_path = tmp_path / "rl.json"
    for _ in range(5):
        ratelimit.record_fail(rl_path, "1.2.3.4")
    ratelimit.clear(rl_path, "1.2.3.4")
    assert ratelimit.is_blocked(rl_path, "1.2.3.4") is False


def test_block_expires_after_window(monkeypatch, tmp_path):
    rl_path = tmp_path / "rl.json"
    for _ in range(5):
        ratelimit.record_fail(rl_path, "1.2.3.4")
    state = ratelimit._load(rl_path)
    state["1.2.3.4"]["blocked_until"] = time.time() - 1  # expired
    ratelimit._save(rl_path, state)
    assert ratelimit.is_blocked(rl_path, "1.2.3.4") is False


def test_block_duration_is_30_minutes(tmp_path):
    """After threshold, blocked_until must be ~30 minutes in the future."""
    rl_path = tmp_path / "rl.json"
    before = time.time()
    for _ in range(5):
        ratelimit.record_fail(rl_path, "1.2.3.4")
    after = time.time()
    state = ratelimit._load(rl_path)
    blocked_until = state["1.2.3.4"]["blocked_until"]
    # Block window must be 30 min, allowing for the small elapsed time of the test.
    assert blocked_until >= before + ratelimit.BLOCK_SEC - 1
    assert blocked_until <= after + ratelimit.BLOCK_SEC + 1
    assert ratelimit.BLOCK_SEC == 1800


# ─── v0.3.3 coverage push: corrupt file & window reset ──────────────────────

def test_load_returns_empty_on_corrupt_json(tmp_path):
    from wg_admin import ratelimit
    p = tmp_path / "rl.json"
    p.write_text("{not json")
    ratelimit.record_fail(p, "1.2.3.4")
    assert ratelimit.is_blocked(p, "1.2.3.4") is False
    # the corrupt file was replaced by a valid write
    import json
    assert "1.2.3.4" in json.loads(p.read_text())


def test_record_fail_resets_window_older_than_60s(tmp_path):
    import json
    import time as _time
    from wg_admin import ratelimit
    p = tmp_path / "rl.json"
    p.write_text(json.dumps({
        "1.2.3.4": {"fails": 4, "first_fail_at": _time.time() - 120,
                    "last_activity": _time.time() - 120}}))
    ratelimit.record_fail(p, "1.2.3.4")
    data = json.loads(p.read_text())
    assert data["1.2.3.4"]["fails"] == 1  # window expired -> counter restarted
