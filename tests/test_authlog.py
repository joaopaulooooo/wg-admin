import json

from wg_admin import authlog


def _read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_log_attempt_creates_file(tmp_path):
    log_path = tmp_path / "auth.log"
    authlog.log_attempt(log_path, success=True, client_ip="1.2.3.4")
    assert log_path.exists()
    entries = _read_lines(log_path)
    assert len(entries) == 1
    assert entries[0]["success"] is True
    assert entries[0]["ip"] == "1.2.3.4"


def test_log_attempt_appends_multiple_lines(tmp_path):
    log_path = tmp_path / "auth.log"
    authlog.log_attempt(log_path, success=False, client_ip="1.1.1.1")
    authlog.log_attempt(log_path, success=True, client_ip="2.2.2.2")
    authlog.log_attempt(log_path, success=False, client_ip="3.3.3.3")
    entries = _read_lines(log_path)
    assert [e["ip"] for e in entries] == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert [e["success"] for e in entries] == [False, True, False]


def test_log_attempt_records_user_agent(tmp_path):
    log_path = tmp_path / "auth.log"
    authlog.log_attempt(
        log_path, success=True, client_ip="1.2.3.4",
        user_agent="Mozilla/5.0",
    )
    entry = _read_lines(log_path)[0]
    assert entry["user_agent"] == "Mozilla/5.0"


def test_log_attempt_has_iso_timestamp(tmp_path):
    log_path = tmp_path / "auth.log"
    authlog.log_attempt(log_path, success=True, client_ip="1.2.3.4")
    entry = _read_lines(log_path)[0]
    assert entry["iso"].endswith("Z")
    assert "T" in entry["iso"]
    assert isinstance(entry["ts"], float)


def test_log_rotates_when_exceeding_max_bytes(tmp_path):
    log_path = tmp_path / "auth.log"
    # Force a tiny rotation threshold for the test.
    authlog.MAX_BYTES = 500
    authlog.BACKUP_COUNT = 3
    try:
        # Each line is ~150 bytes; 10 lines pushes well past 500.
        for i in range(10):
            authlog.log_attempt(
                log_path, success=bool(i % 2),
                client_ip=f"10.0.0.{i}",
            )
        # Current file must be under the threshold.
        assert log_path.stat().st_size < 500
        # .1 backup must exist with previous content.
        backup1 = log_path.with_suffix(log_path.suffix + ".1")
        assert backup1.exists()
        # Backups beyond BACKUP_COUNT must not exist.
        assert not log_path.with_suffix(log_path.suffix + ".4").exists()
        # All backup entries must still be valid JSON.
        backup_entries = _read_lines(backup1)
        assert len(backup_entries) > 0
        for e in backup_entries:
            assert "ip" in e and "success" in e
    finally:
        # Restore module defaults so other tests are not affected.
        authlog.MAX_BYTES = 100 * 1024
        authlog.BACKUP_COUNT = 5


def test_log_rotation_shifts_backups(tmp_path):
    log_path = tmp_path / "auth.log"
    authlog.MAX_BYTES = 200
    authlog.BACKUP_COUNT = 2
    try:
        for i in range(20):
            authlog.log_attempt(log_path, success=True, client_ip=f"10.0.0.{i}")
        # With BACKUP_COUNT=2, only .1 and .2 should exist; .3 must not.
        assert log_path.with_suffix(log_path.suffix + ".1").exists()
        assert log_path.with_suffix(log_path.suffix + ".2").exists()
        assert not log_path.with_suffix(log_path.suffix + ".3").exists()
    finally:
        authlog.MAX_BYTES = 100 * 1024
        authlog.BACKUP_COUNT = 5
