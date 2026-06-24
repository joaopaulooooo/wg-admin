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
