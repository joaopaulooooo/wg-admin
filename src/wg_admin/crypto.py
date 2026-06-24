"""Crypto primitives: HKDF-SHA256, AES-256-GCM, Argon2id password hashing."""
from __future__ import annotations

import os

from argon2 import PasswordHasher, Type
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASTER_KEY_SIZE = 32
STATE_KDF_INFO = b"wireguard-admin-state-v1"
STATE_SALT_SIZE = 16
STATE_NONCE_SIZE = 12
STATE_VERSION = 1

_argon2 = PasswordHasher(
    time_cost=3,
    memory_cost=16384,  # 16 MB
    parallelism=1,
    type=Type.ID,
)


def derive_state_key(master_key: bytes, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from master_key via HKDF-SHA256."""
    if len(master_key) != MASTER_KEY_SIZE:
        raise ValueError(f"master_key must be {MASTER_KEY_SIZE} bytes, got {len(master_key)}")
    if len(salt) != STATE_SALT_SIZE:
        raise ValueError(f"salt must be {STATE_SALT_SIZE} bytes, got {len(salt)}")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=STATE_KDF_INFO,
    ).derive(master_key)


def encrypt_state(plaintext: bytes, master_key: bytes) -> dict:
    """Encrypt plaintext, return JSON-serializable envelope."""
    salt = os.urandom(STATE_SALT_SIZE)
    nonce = os.urandom(STATE_NONCE_SIZE)
    aes_key = derive_state_key(master_key, salt)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, associated_data=None)
    return {
        "version": STATE_VERSION,
        "kdf": "hkdf-sha256",
        "kdf_salt": salt.hex(),
        "kdf_info": STATE_KDF_INFO.decode(),
        "cipher": "aes-256-gcm",
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
    }


def decrypt_state(envelope: dict, master_key: bytes) -> bytes:
    """Decrypt envelope. Raises InvalidTag on tamper or wrong key."""
    salt = bytes.fromhex(envelope["kdf_salt"])
    nonce = bytes.fromhex(envelope["nonce"])
    ciphertext = bytes.fromhex(envelope["ciphertext"])
    aes_key = derive_state_key(master_key, salt)
    return AESGCM(aes_key).decrypt(nonce, ciphertext, associated_data=None)


def hash_password(password: str) -> str:
    """Hash password using Argon2id. Returns PHC string."""
    return _argon2.hash(password)


def verify_password(password: str, phc_hash: str) -> bool:
    """Verify password against PHC hash. Returns True/False (never raises)."""
    try:
        return _argon2.verify(phc_hash, password)
    except (VerifyMismatchError, InvalidHashError, Argon2Error, TypeError, AttributeError):
        return False
