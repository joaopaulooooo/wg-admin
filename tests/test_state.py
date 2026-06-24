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
        {"id": "abc12345", "name": "x", "ip": "10.0.0.2"},
        {"id": "def67890", "name": "y", "ip": "10.0.0.3"},
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
