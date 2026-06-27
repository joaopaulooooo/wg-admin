"""Encrypted state management: load/save, schema, IP allocation."""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from . import crypto

STATE_VERSION = 1
LOCK_TIMEOUT_SEC = 5.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "peers": [],
    }


def new_peer_id() -> str:
    return secrets.token_hex(4)


def find_peer_by_id(state_data: dict, peer_id: str) -> Optional[dict]:
    for peer in state_data["peers"]:
        if peer["id"] == peer_id:
            return peer
    return None


def migrate_state(state_data: dict) -> None:
    """Add new fields to peers from older state versions (in-place).

    Idempotent: peers that already have the fields are untouched.
    Currently adds: quota_gb, quota_suspended, quota_state_updated_at.
    """
    for peer in state_data.get("peers", []):
        peer.setdefault("quota_gb", 0.0)
        peer.setdefault("quota_suspended", False)
        peer.setdefault("quota_state_updated_at", None)


def add_peer(state_data: dict, peer: dict) -> None:
    state_data["peers"].append(peer)


def remove_peer(state_data: dict, peer_id: str) -> bool:
    before = len(state_data["peers"])
    state_data["peers"] = [p for p in state_data["peers"] if p["id"] != peer_id]
    return len(state_data["peers"]) < before


def set_peer_disabled(state_data: dict, peer_id: str, disabled: bool) -> bool:
    peer = find_peer_by_id(state_data, peer_id)
    if peer is None:
        return False
    peer["disabled"] = disabled
    return True


def allocate_ip(state_data: dict, subnet: str, server_ip: str) -> str:
    """Find the lowest free IP in subnet, skipping server_ip and existing peers."""
    network = ipaddress.ip_network(subnet, strict=False)
    used = {server_ip}
    for peer in state_data["peers"]:
        used.add(peer["ip"])
    for host in network.hosts():
        ip_str = str(host)
        if ip_str not in used:
            return ip_str
    raise RuntimeError(f"Subnet {subnet} exhausted (no free IPs)")


@contextmanager
def _flock(state_path: Path, timeout: float = LOCK_TIMEOUT_SEC) -> Iterator[None]:
    lock_path = str(state_path) + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Lock timeout on {state_path}")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = Path(str(path) + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            fd = -1  # already closed by os.fdopen context
            raise
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_read_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def load_state(state_path: Path, master_key: bytes) -> dict:
    """Load and decrypt state. Returns empty state if file doesn't exist."""
    if not state_path.exists():
        return empty_state()
    envelope = json.loads(_atomic_read_bytes(state_path))
    plaintext = crypto.decrypt_state(envelope, master_key)
    state_data = json.loads(plaintext)
    migrate_state(state_data)
    return state_data


def save_state(state_path: Path, state_data: dict, master_key: bytes) -> None:
    """Encrypt and save state atomically. Rotates .bak and .bak1.

    Encryption happens BEFORE any filesystem mutation so a crypto failure
    cannot destroy the live state file. The caller's state_data dict is
    mutated (updated_at) only inside the lock — failure leaves it untouched.
    """
    plaintext = json.dumps(state_data, separators=(",", ":")).encode()
    envelope = crypto.encrypt_state(plaintext, master_key)
    envelope_bytes = json.dumps(envelope).encode()

    with _flock(state_path):
        state_data["updated_at"] = utc_now_iso()
        # Re-serialize with the new updated_at timestamp.
        plaintext = json.dumps(state_data, separators=(",", ":")).encode()
        envelope = crypto.encrypt_state(plaintext, master_key)
        envelope_bytes = json.dumps(envelope).encode()

        # Rotate backups (only after encryption succeeded).
        if state_path.exists():
            bak = state_path.with_suffix(state_path.suffix + ".bak")
            bak1 = state_path.with_suffix(state_path.suffix + ".bak1")
            if bak.exists():
                os.replace(bak, bak1)
            os.replace(state_path, bak)

        _atomic_write_bytes(state_path, envelope_bytes)


def encrypt_private_key(private_key: str, master_key: bytes) -> str:
    """Encrypt a peer private key string. Returns hex envelope (salt|nonce|ct)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    salt = os.urandom(crypto.STATE_SALT_SIZE)
    nonce = os.urandom(crypto.STATE_NONCE_SIZE)
    peer_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"wireguard-admin-peerkey-v1",
    ).derive(master_key)
    ct = AESGCM(peer_key).encrypt(nonce, private_key.encode(), associated_data=None)
    return f"{salt.hex()}|{nonce.hex()}|{ct.hex()}"


def decrypt_private_key(envelope: str, master_key: bytes) -> str:
    """Inverse of encrypt_private_key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    salt_hex, nonce_hex, ct_hex = envelope.split("|", 2)
    salt = bytes.fromhex(salt_hex)
    nonce = bytes.fromhex(nonce_hex)
    ct = bytes.fromhex(ct_hex)
    peer_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"wireguard-admin-peerkey-v1",
    ).derive(master_key)
    plaintext = AESGCM(peer_key).decrypt(nonce, ct, associated_data=None)
    return plaintext.decode()
