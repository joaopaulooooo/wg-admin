import json
import re
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from wg_admin import crypto, state


def test_empty_state_has_required_fields():
    s = state.empty_state()
    assert s["version"] == 1
    assert s["peers"] == []
    assert "created_at" in s
    assert "updated_at" in s


def test_empty_state_timestamp_is_iso8601():
    s = state.empty_state()
    iso_re = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    assert re.match(iso_re, s["created_at"])


def test_new_peer_id_is_8_hex_chars():
    pid = state.new_peer_id()
    assert re.match(r"^[0-9a-f]{8}$", pid)


def test_new_peer_id_is_unique_across_calls():
    ids = {state.new_peer_id() for _ in range(100)}
    assert len(ids) == 100  # very low collision probability


def test_find_peer_by_id_returns_matching_peer():
    s = state.empty_state()
    s["peers"] = [{"id": "abc12345", "name": "x"}, {"id": "def67890", "name": "y"}]
    p = state.find_peer_by_id(s, "def67890")
    assert p["name"] == "y"


def test_find_peer_by_id_returns_none_if_missing():
    s = state.empty_state()
    s["peers"] = [{"id": "abc12345"}]
    assert state.find_peer_by_id(s, "missing") is None


def test_remove_peer_returns_true_when_removed():
    s = state.empty_state()
    s["peers"] = [{"id": "abc12345"}, {"id": "def67890"}]
    assert state.remove_peer(s, "abc12345") is True
    assert len(s["peers"]) == 1


def test_remove_peer_returns_false_when_missing():
    s = state.empty_state()
    s["peers"] = [{"id": "abc12345"}]
    assert state.remove_peer(s, "nope") is False


def test_set_peer_disabled_toggles_flag():
    s = state.empty_state()
    s["peers"] = [{"id": "abc12345", "disabled": False}]
    assert state.set_peer_disabled(s, "abc12345", True) is True
    assert s["peers"][0]["disabled"] is True


def test_allocate_ip_returns_first_free_in_subnet():
    s = state.empty_state()
    ip = state.allocate_ip(s, "10.0.0.0/24", "10.0.0.1")
    assert ip == "10.0.0.2"


def test_allocate_ip_skips_used_ips():
    s = state.empty_state()
    s["peers"] = [{"ip": "10.0.0.2"}, {"ip": "10.0.0.3"}, {"ip": "10.0.0.4"}]
    ip = state.allocate_ip(s, "10.0.0.0/24", "10.0.0.1")
    assert ip == "10.0.0.5"


def test_allocate_ip_skips_server_ip():
    s = state.empty_state()
    # 10.0.0.1 is server, should never be allocated
    for _ in range(3):
        ip = state.allocate_ip(s, "10.0.0.0/24", "10.0.0.1")
        s["peers"].append({"ip": ip})
        assert ip != "10.0.0.1"


def test_allocate_ip_finds_gap_in_middle():
    s = state.empty_state()
    s["peers"] = [{"ip": "10.0.0.2"}, {"ip": "10.0.0.3"}, {"ip": "10.0.0.5"}]
    ip = state.allocate_ip(s, "10.0.0.0/24", "10.0.0.1")
    assert ip == "10.0.0.4"


def test_allocate_ip_raises_when_subnet_exhausted():
    s = state.empty_state()
    # /30 has 2 usable hosts (.1 and .2) — both reserved/used
    s["peers"] = [{"ip": "10.0.0.2"}]
    with pytest.raises(RuntimeError):
        state.allocate_ip(s, "10.0.0.0/30", "10.0.0.1")


def test_save_then_load_preserves_data(tmp_path):
    state_path = tmp_path / "state.json.enc"
    master = bytes(32)
    s = state.empty_state()
    s["peers"] = [
        {"id": "abc12345", "name": "x", "ip": "10.0.0.2",
         "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None},
        {"id": "def67890", "name": "y", "ip": "10.0.0.3",
         "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None},
    ]
    state.save_state(state_path, s, master)
    loaded = state.load_state(state_path, master)
    assert loaded["peers"] == s["peers"]


def test_load_returns_empty_state_when_file_missing(tmp_path):
    state_path = tmp_path / "nonexistent.enc"
    s = state.load_state(state_path, bytes(32))
    assert s["peers"] == []
    assert s["version"] == 1


def test_save_creates_backup_files(tmp_path):
    state_path = tmp_path / "state.json.enc"
    bak = tmp_path / "state.json.enc.bak"
    bak1 = tmp_path / "state.json.enc.bak1"
    master = bytes(32)
    # First save
    s1 = state.empty_state()
    s1["peers"] = [{"id": "first"}]
    state.save_state(state_path, s1, master)
    assert not bak.exists()
    # Second save — should create .bak
    s2 = state.empty_state()
    s2["peers"] = [{"id": "second"}]
    state.save_state(state_path, s2, master)
    assert bak.exists()
    assert not bak1.exists()
    # Third save — should rotate .bak to .bak1
    s3 = state.empty_state()
    s3["peers"] = [{"id": "third"}]
    state.save_state(state_path, s3, master)
    assert bak.exists()
    assert bak1.exists()


def test_save_updated_at_changes_each_save(tmp_path):
    state_path = tmp_path / "state.json.enc"
    master = bytes(32)
    s = state.empty_state()
    first_updated = s["updated_at"]
    state.save_state(state_path, s, master)
    loaded = state.load_state(state_path, master)
    assert loaded["updated_at"] >= first_updated


def test_load_raises_on_corrupt_envelope(tmp_path):
    state_path = tmp_path / "state.json.enc"
    state_path.write_text("not valid json at all")
    with pytest.raises(json.JSONDecodeError):
        state.load_state(state_path, bytes(32))


def test_load_raises_on_wrong_master_key(tmp_path):
    state_path = tmp_path / "state.json.enc"
    state.save_state(state_path, state.empty_state(), bytes(32))
    with pytest.raises(InvalidTag):
        state.load_state(state_path, bytes([1] * 32))


def test_save_writes_encrypted_envelope_not_plaintext(tmp_path):
    state_path = tmp_path / "state.json.enc"
    s = state.empty_state()
    s["peers"] = [{"name": "secret-name"}]
    state.save_state(state_path, s, bytes(32))
    contents = state_path.read_text()
    assert "secret-name" not in contents
    assert "ciphertext" in contents  # envelope marker


def test_encrypt_private_key_roundtrips():
    master = bytes(32)
    blob = state.encrypt_private_key("PRIVATE_KEY_DATA=", master)
    assert isinstance(blob, str)
    assert "PRIVATE_KEY_DATA" not in blob
    recovered = state.decrypt_private_key(blob, master)
    assert recovered == "PRIVATE_KEY_DATA="


def test_decrypt_private_key_wrong_master_returns_garbage_or_raises():
    master = bytes(32)
    blob = state.encrypt_private_key("X=", master)
    from cryptography.exceptions import InvalidTag
    try:
        result = state.decrypt_private_key(blob, bytes([1] * 32))
        assert result != "X="
    except InvalidTag:
        pass


def test_encrypt_private_key_different_each_call():
    master = bytes(32)
    blob1 = state.encrypt_private_key("X=", master)
    blob2 = state.encrypt_private_key("X=", master)
    assert blob1 != blob2  # different nonces


def test_migrate_state_adds_quota_fields_to_old_peer():
    """Peers created before quotas existed should get default fields."""
    from wg_admin.state import migrate_state
    state = {
        "version": 1,
        "peers": [{
            "id": "abc", "name": "x", "public_key": "PUB",
            "private_key_enc": "ENC", "ip": "10.0.0.2",
            "disabled": False, "created_at": "2026-06-17T00:00:00Z",
        }],
    }
    migrate_state(state)
    p = state["peers"][0]
    assert p["quota_gb"] == 0.0
    assert p["quota_suspended"] is False
    assert p["quota_state_updated_at"] is None


def test_migrate_state_preserves_existing_quota_fields():
    from wg_admin.state import migrate_state
    state = {"version": 1, "peers": [{
        "id": "abc", "name": "x", "public_key": "PUB",
        "private_key_enc": "ENC", "ip": "10.0.0.2",
        "disabled": False, "created_at": "...",
        "quota_gb": 5.5, "quota_suspended": True,
        "quota_state_updated_at": "2026-06-27T00:00:00Z",
    }]}
    migrate_state(state)
    p = state["peers"][0]
    assert p["quota_gb"] == 5.5
    assert p["quota_suspended"] is True
    assert p["quota_state_updated_at"] == "2026-06-27T00:00:00Z"


def test_migrate_state_handles_empty_peers():
    from wg_admin.state import migrate_state
    state = {"version": 1, "peers": []}
    migrate_state(state)  # should not raise
    assert state["peers"] == []


def test_update_state_mutator_sees_authoritative_state(tmp_path):
    """A transaction started from a stale read cannot resurrect a concurrent
    change: the mutator always receives the on-disk (locked) state."""
    import os as _os
    from wg_admin import state

    key = _os.urandom(32)
    p = tmp_path / "state.json.enc"
    s = state.empty_state()
    s["peers"] = [{"id": "1", "name": "x", "public_key": "PUB", "ip": "10.0.0.2",
                   "disabled": False}]
    state.save_state(p, s, key)

    stale = state.load_state(p, key)  # what a slow worker would still hold

    def disable(st):
        st["peers"][0]["disabled"] = True

    state.update_state(p, key, disable)

    # A later transaction based on the stale snapshot re-reads under the
    # lock, so the disable is visible and cannot be silently lost.
    seen = {}
    def edit(st):
        seen["disabled"] = st["peers"][0]["disabled"]
        st["peers"][0]["notes"] = "edited"
    state.update_state(p, key, edit)

    assert seen["disabled"] is True
    final = state.load_state(p, key)
    assert final["peers"][0]["disabled"] is True
    assert final["peers"][0]["notes"] == "edited"
    assert stale["peers"][0]["disabled"] is False  # stale copy untouched


def test_save_state_keeps_main_file_present(tmp_path, monkeypatch):
    """During backup rotation the live file must never disappear (a lockless
    reader would otherwise see an empty state)."""
    import os as _os
    from wg_admin import state

    key = _os.urandom(32)
    p = tmp_path / "state.json.enc"
    state.save_state(p, state.empty_state(), key)  # first save creates it

    real_replace = _os.replace
    checks = []
    def spy(src, dst):
        if str(dst) == str(p):
            checks.append(p.exists())
        return real_replace(src, dst)
    monkeypatch.setattr(state.os, "replace", spy)

    state.save_state(p, state.empty_state(), key)
    state.save_state(p, state.empty_state(), key)
    assert checks and all(checks), "main state file vanished during rotation"


def test_update_state_nochanges_writes_nothing(tmp_path):
    """A mutator raising NoChanges aborts the transaction with no write."""
    import os as _os
    from wg_admin import state

    key = _os.urandom(32)
    p = tmp_path / "state.json.enc"
    state.save_state(p, state.empty_state(), key)
    before = p.stat().st_mtime_ns

    def mut(st):
        raise state.NoChanges

    _, result = state.update_state(p, key, mut)
    assert result is None
    assert p.stat().st_mtime_ns == before


# ─── v0.3.3 coverage push: helpers, lock contention, write failure paths ────

def test_add_peer_appends_in_place():
    from wg_admin import state
    s = state.empty_state()
    peer = {"id": "x", "name": "n"}
    state.add_peer(s, peer)
    assert s["peers"] == [peer]


def test_set_peer_disabled_unknown_peer_returns_false():
    from wg_admin import state
    s = state.empty_state()
    assert state.set_peer_disabled(s, "ghost", True) is False


def test_flock_times_out_when_lock_is_held(tmp_path, monkeypatch):
    """A second writer waiting beyond the timeout raises TimeoutError."""
    import fcntl as _fcntl
    import os as _os
    from wg_admin import state

    p = tmp_path / "state.json.enc"
    state.save_state(p, state.empty_state(), _os.urandom(32))

    real_flock = state._flock
    monkeypatch.setattr(state, "_flock",
                        lambda path, timeout=0.2: real_flock(path, timeout=0.2))

    # Hold the lock through a *different* open file description.
    holder_fd = _os.open(str(p) + ".lock", _os.O_CREAT | _os.O_RDWR, 0o600)
    try:
        _fcntl.flock(holder_fd, _fcntl.LOCK_EX)
        import pytest
        with pytest.raises(TimeoutError):
            state.save_state(p, state.empty_state(), _os.urandom(32))
    finally:
        _fcntl.flock(holder_fd, _fcntl.LOCK_UN)
        _os.close(holder_fd)


def test_atomic_write_cleanup_when_fsync_fails(tmp_path, monkeypatch):
    """A failing fsync removes the temp file and re-raises."""
    import os as _os
    from wg_admin import state

    p = tmp_path / "f.bin"
    before = list(tmp_path.iterdir())
    monkeypatch.setattr(state.os, "fsync",
                        lambda fd: (_ for _ in ()).throw(OSError("disk gone")))
    import pytest
    with pytest.raises(OSError):
        state._atomic_write_bytes(p, b"data")
    assert list(tmp_path.iterdir()) == before  # no temp leftovers


def test_atomic_write_cleanup_when_open_fails(tmp_path, monkeypatch):
    """A failing open also cleans up silently (unlink of nothing)."""
    import os as _os
    from wg_admin import state

    p = tmp_path / "f.bin"
    monkeypatch.setattr(state.os, "open",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("no fds")))
    import pytest
    with pytest.raises(OSError):
        state._atomic_write_bytes(p, b"data")
    assert not p.exists()
