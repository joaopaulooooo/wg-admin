# tests/test_crypto.py
import pytest
from wg_admin import crypto


def test_derive_state_key_produces_32_bytes():
    master = bytes(32)
    salt = bytes(16)
    key = crypto.derive_state_key(master, salt)
    assert len(key) == 32


def test_derive_state_key_deterministic():
    master = bytes(32)
    salt = bytes(16)
    key1 = crypto.derive_state_key(master, salt)
    key2 = crypto.derive_state_key(master, salt)
    assert key1 == key2


def test_derive_state_key_different_salts_produce_different_keys():
    master = bytes(32)
    key1 = crypto.derive_state_key(master, bytes(16))
    key2 = crypto.derive_state_key(master, bytes([1] * 16))
    assert key1 != key2


def test_derive_state_key_different_masters_produce_different_keys():
    salt = bytes(16)
    key1 = crypto.derive_state_key(bytes(32), salt)
    key2 = crypto.derive_state_key(bytes([1] * 32), salt)
    assert key1 != key2


def test_derive_state_key_rejects_wrong_master_size():
    with pytest.raises(ValueError):
        crypto.derive_state_key(bytes(16), bytes(16))


def test_derive_state_key_rejects_wrong_salt_size():
    with pytest.raises(ValueError):
        crypto.derive_state_key(bytes(32), bytes(8))


import json

from cryptography.exceptions import InvalidTag


def test_encrypt_decrypt_roundtrip():
    master = bytes(32)
    plaintext = b'{"peers": []}'
    envelope = crypto.encrypt_state(plaintext, master)
    recovered = crypto.decrypt_state(envelope, master)
    assert recovered == plaintext


def test_encrypt_state_envelope_has_required_fields():
    envelope = crypto.encrypt_state(b"x", bytes(32))
    for field in ("version", "kdf", "kdf_salt", "kdf_info", "cipher", "nonce", "ciphertext"):
        assert field in envelope
    assert envelope["version"] == 1
    assert envelope["kdf"] == "hkdf-sha256"
    assert envelope["cipher"] == "aes-256-gcm"
    assert envelope["kdf_info"] == "wireguard-admin-state-v1"


def test_decrypt_tampered_ciphertext_fails():
    envelope = crypto.encrypt_state(b"sensitive", bytes(32))
    # Flip a bit in the ciphertext
    ct = bytes.fromhex(envelope["ciphertext"])
    tampered = bytes([ct[0] ^ 0x01]) + ct[1:]
    envelope["ciphertext"] = tampered.hex()
    with pytest.raises(InvalidTag):
        crypto.decrypt_state(envelope, bytes(32))


def test_decrypt_wrong_master_key_fails():
    envelope = crypto.encrypt_state(b"data", bytes(32))
    with pytest.raises(InvalidTag):
        crypto.decrypt_state(envelope, bytes([1] * 32))


def test_encrypt_generates_fresh_nonce_each_call():
    e1 = crypto.encrypt_state(b"x", bytes(32))
    e2 = crypto.encrypt_state(b"x", bytes(32))
    assert e1["nonce"] != e2["nonce"]
    assert e1["kdf_salt"] != e2["kdf_salt"]


def test_decrypt_rejects_invalid_hex():
    envelope = crypto.encrypt_state(b"x", bytes(32))
    envelope["ciphertext"] = "not-hex!"
    with pytest.raises(ValueError):
        crypto.decrypt_state(envelope, bytes(32))


def test_hash_password_returns_phc_string():
    h = crypto.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")


def test_verify_password_accepts_correct_password():
    h = crypto.hash_password("my secret")
    assert crypto.verify_password("my secret", h) is True


def test_verify_password_rejects_wrong_password():
    h = crypto.hash_password("my secret")
    assert crypto.verify_password("wrong", h) is False


def test_hashed_passwords_differ_for_same_input():
    h1 = crypto.hash_password("same")
    h2 = crypto.hash_password("same")
    assert h1 != h2  # different salts


def test_verify_password_rejects_malformed_hash():
    """InvalidHash branch coverage — protects against corrupt state file."""
    assert crypto.verify_password("anything", "not-a-phc-string") is False


def test_verify_password_handles_empty_password():
    h = crypto.hash_password("")
    assert crypto.verify_password("", h) is True
    assert crypto.verify_password("not empty", h) is False


def test_verify_password_handles_long_password():
    long_pw = "a" * 10_000
    h = crypto.hash_password(long_pw)
    assert crypto.verify_password(long_pw, h) is True


def test_verify_password_handles_unicode_password():
    pw = "üñîçødé-P@ssw0rd-日本語"
    h = crypto.hash_password(pw)
    assert crypto.verify_password(pw, h) is True


def test_verify_password_returns_false_for_non_string_input():
    """TypeError catch coverage."""
    assert crypto.verify_password("x", None) is False
    assert crypto.verify_password(None, "$argon2id$") is False


def test_phc_records_configured_parameters():
    """Lock Argon2id parameters against accidental change."""
    h = crypto.hash_password("x")
    assert "m=16384" in h
    assert "t=3" in h
    assert "p=1" in h
