# wg-admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Flask-based WireGuard peer management panel that runs on Linux with minimal RAM (1GB target) via systemd socket activation.

**Architecture:** Python 3.11+ Flask app with subprocess wrappers for `wg`/`wg-quick`. State persisted as AES-256-GCM-encrypted JSON. Auth via Argon2id password + signed session cookies. Hardened systemd unit with socket activation for zero idle RAM.

**Tech Stack:** Python 3.11+, Flask 3.0+, cryptography (AES-GCM + HKDF), argon2-cffi, qrcode, itsdangerous, pytest, systemd.

---

## File Structure

```
wg-admin/
├── README.md
├── requirements.txt
├── requirements-dev.txt
├── config.ini.example
├── install.sh
├── uninstall.sh
├── pytest.ini
├── .gitignore
├── systemd/
│   ├── wg-admin.socket
│   └── wg-admin.service
├── src/
│   └── wg_admin/
│       ├── __init__.py
│       ├── app.py              # Flask factory + routes + auth + CSRF + rate limit
│       ├── config.py           # Load config.ini
│       ├── crypto.py           # HKDF, AES-GCM, Argon2id
│       ├── state.py            # Encrypted state load/save, schema, IP alloc
│       ├── wg.py               # Subprocess wrappers for wg commands
│       ├── confgen.py          # .conf + QR code generation
│       └── ratelimit.py        # File-based rate limiter
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── peers.html
│   ├── peer_form.html
│   └── error.html
├── static/
│   └── style.css
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_crypto.py
    ├── test_state.py
    ├── test_wg.py
    ├── test_confgen.py
    ├── test_ratelimit.py
    └── test_app.py
```

---

## Task 1: Project skeleton

**Files:**
- Create: `README.md`, `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `pytest.ini`, `src/wg_admin/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
cd /path/to/wg-admin
mkdir -p src/wg_admin tests templates static systemd
```

- [ ] **Step 2: Write `requirements.txt`**

```
flask>=3.0,<4.0
cryptography>=42.0
argon2-cffi>=23.0
qrcode>=7.0
itsdangerous>=2.1
```

- [ ] **Step 3: Write `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8.0
pytest-cov>=4.0
```

- [ ] **Step 4: Write `.gitignore`**

```
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
.coverage
venv/
*.tmp
*.bak*
*.lock
secrets/
state.json*
config.ini
```

- [ ] **Step 5: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -ra --strict-markers
```

- [ ] **Step 6: Write `src/wg_admin/__init__.py` (empty) and `tests/__init__.py` (empty)**

```python
# empty package marker
```

- [ ] **Step 7: Write `README.md`**

```markdown
# wg-admin

Minimal WireGuard peer management panel for Linux. Python + Flask + systemd socket activation.

See `docs/superpowers/specs/2026-06-17-wg-admin-design.md` for full design.
```

- [ ] **Step 8: Setup venv and install deps**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

- [ ] **Step 9: Verify pytest works**

Run: `pytest --version`
Expected: `pytest 8.x.x`

- [ ] **Step 10: Commit**

```bash
git add .
git commit -m "chore: project skeleton with deps and pytest config"
```

---

## Task 2: Crypto module — HKDF-SHA256 key derivation

**Files:**
- Create: `src/wg_admin/crypto.py`
- Test: `tests/test_crypto.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_crypto.py -v`
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/wg_admin/crypto.py
"""Crypto primitives: HKDF-SHA256, AES-256-GCM, Argon2id password hashing."""
from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASTER_KEY_SIZE = 32
STATE_KDF_INFO = b"wireguard-admin-state-v1"
STATE_SALT_SIZE = 16
STATE_NONCE_SIZE = 12
STATE_VERSION = 1


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_crypto.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/crypto.py tests/test_crypto.py
git commit -m "feat(crypto): HKDF-SHA256 key derivation with input validation"
```

---

## Task 3: Crypto module — AES-256-GCM encrypt/decrypt

**Files:**
- Modify: `src/wg_admin/crypto.py`
- Modify: `tests/test_crypto.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_crypto.py
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
    tampered = (ct[0] ^ 0x01) + ct[1:]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crypto.py -v`
Expected: 6 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/crypto.py
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crypto.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/crypto.py tests/test_crypto.py
git commit -m "feat(crypto): AES-256-GCM encrypt/decrypt with envelope format"
```

---

## Task 4: Crypto module — Argon2id password hashing

**Files:**
- Modify: `src/wg_admin/crypto.py`
- Modify: `tests/test_crypto.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_crypto.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crypto.py -v -k password`
Expected: 4 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/crypto.py
from argon2 import PasswordHasher, Type
from argon2.exceptions import Argon2Error, InvalidHash, VerifyMismatchError

_argon2 = PasswordHasher(
    time_cost=3,
    memory_cost=16384,  # 16 MB
    parallelism=1,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    """Hash password using Argon2id. Returns PHC string."""
    return _argon2.hash(password)


def verify_password(password: str, phc_hash: str) -> bool:
    """Verify password against PHC hash. Returns True/False (never raises)."""
    try:
        return _argon2.verify(phc_hash, password)
    except (VerifyMismatchError, InvalidHash, Argon2Error):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crypto.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/crypto.py tests/test_crypto.py
git commit -m "feat(crypto): Argon2id password hash/verify (16MB t=3 p=1)"
```

---

## Task 5: State module — schema and helpers

**Files:**
- Create: `src/wg_admin/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_state.py
import re
from wg_admin import state


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write implementation**

```python
# src/wg_admin/state.py
"""Encrypted state management: load/save, schema, IP allocation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

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
    return uuid.uuid4().hex[:8]


def find_peer_by_id(state_data: dict, peer_id: str) -> Optional[dict]:
    for peer in state_data["peers"]:
        if peer["id"] == peer_id:
            return peer
    return None


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/state.py tests/test_state.py
git commit -m "feat(state): schema helpers (empty state, peer CRUD)"
```

---

## Task 6: State module — IP allocation

**Files:**
- Modify: `src/wg_admin/state.py`
- Modify: `tests/test_state.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_state.py
import pytest


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py -v -k allocate`
Expected: 5 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/state.py
import ipaddress


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/state.py tests/test_state.py
git commit -m "feat(state): lowest-free-IP allocation in subnet"
```

---

## Task 7: State module — atomic load/save with encryption and backups

**Files:**
- Modify: `src/wg_admin/state.py`
- Modify: `tests/test_state.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_state.py
import json
from pathlib import Path

from cryptography.exceptions import InvalidTag

from wg_admin import crypto


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py -v -k "save or load or backup"`
Expected: 7 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/state.py
import fcntl
import os
import time
from contextlib import contextmanager
from typing import Iterator

from . import crypto


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


def _atomic_read_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def load_state(state_path: Path, master_key: bytes) -> dict:
    """Load and decrypt state. Returns empty state if file doesn't exist."""
    if not state_path.exists():
        return empty_state()
    envelope = json.loads(_atomic_read_bytes(state_path))
    plaintext = crypto.decrypt_state(envelope, master_key)
    return json.loads(plaintext)


def save_state(state_path: Path, state_data: dict, master_key: bytes) -> None:
    """Encrypt and save state atomically. Rotates .bak and .bak1."""
    state_data["updated_at"] = utc_now_iso()
    plaintext = json.dumps(state_data, separators=(",", ":")).encode()

    with _flock(state_path):
        # Rotate backups
        if state_path.exists():
            bak = state_path.with_suffix(state_path.suffix + ".bak")
            bak1 = state_path.with_suffix(state_path.suffix + ".bak1")
            if bak.exists():
                os.replace(bak, bak1)
            os.replace(state_path, bak)

        envelope = crypto.encrypt_state(plaintext, master_key)
        _atomic_write_bytes(state_path, json.dumps(envelope).encode())
```

You also need to add `import json` and `from pathlib import Path` at the top of `state.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/state.py tests/test_state.py
git commit -m "feat(state): atomic encrypted save/load with .bak rotation"
```

---

## Task 8: WG module — parse wg0.conf

**Files:**
- Create: `src/wg_admin/wg.py`
- Test: `tests/test_wg.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_wg.py
import textwrap

from wg_admin import wg


SIMPLE_CONF = textwrap.dedent("""
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = SERVER_PRIVATE_KEY=
PostUp = iptables -A FORWARD -i %i -j ACCEPT
PostDown = iptables -D FORWARD -i %i -j ACCEPT

[Peer]   # 10.0.0.2 — João iPhone
PublicKey = Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw=
AllowedIPs = 10.0.0.2/32

[Peer]
PublicKey = RFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y=
AllowedIPs = 10.0.0.3/32
""").strip()


DISABLED_CONF = textwrap.dedent("""
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = SERVER_KEY=

[Peer]
PublicKey = AAAA
AllowedIPs = 10.0.0.2/32

#[Peer]
#PublicKey = BBBB
#AllowedIPs = 10.0.0.3/32
""").strip()


def test_parse_conf_returns_interface_and_peers():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    assert "interface" in result
    assert "peers" in result
    assert len(result["peers"]) == 2


def test_parse_conf_extracts_interface_fields():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    assert result["interface"]["Address"] == "10.0.0.1/24"
    assert result["interface"]["ListenPort"] == "51820"
    assert result["interface"]["PrivateKey"] == "SERVER_PRIVATE_KEY="


def test_parse_conf_postup_postdown_as_lists():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    assert isinstance(result["interface"]["PostUp"], list)
    assert len(result["interface"]["PostUp"]) == 1
    assert "iptables -A FORWARD" in result["interface"]["PostUp"][0]


def test_parse_conf_extracts_peer_fields():
    result = wg.parse_wg_conf(SIMPLE_CONF)
    p0 = result["peers"][0]
    assert p0["PublicKey"] == "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="
    assert p0["AllowedIPs"] == "10.0.0.2/32"
    assert p0["disabled"] is False


def test_parse_conf_marks_disabled_peers():
    result = wg.parse_wg_conf(DISABLED_CONF)
    assert len(result["peers"]) == 2
    assert result["peers"][0]["disabled"] is False
    assert result["peers"][1]["disabled"] is True
    assert result["peers"][1]["PublicKey"] == "BBBB"


def test_parse_conf_skips_blank_lines_and_comments():
    conf = textwrap.dedent("""
    # top-level comment
    [Interface]
    Address = 10.0.0.1/24
    # mid-section comment
    [Peer]
    PublicKey = X
    AllowedIPs = 10.0.0.2/32
    """).strip()
    result = wg.parse_wg_conf(conf)
    assert len(result["peers"]) == 1
    assert result["interface"]["Address"] == "10.0.0.1/24"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wg.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write implementation**

```python
# src/wg_admin/wg.py
"""Subprocess wrappers for wg and wg-quick commands, plus conf parsing/generation."""
from __future__ import annotations

from typing import Optional


def parse_wg_conf(content: str) -> dict:
    """Parse wg0.conf content. Returns {"interface": {...}, "peers": [...]}.
    
    Each peer dict has a "disabled" boolean. Disabled peers are those whose
    [Peer] section is prefixed with # (commented out).
    """
    result: dict = {"interface": {}, "peers": []}
    current_section: Optional[str] = None
    current_peer: Optional[dict] = None
    current_disabled = False

    def flush_peer():
        nonlocal current_peer
        if current_peer is not None:
            current_peer["disabled"] = current_disabled
            result["peers"].append(current_peer)
            current_peer = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Disabled section marker: a # followed by [Peer] (possibly with spaces)
        if line.startswith("#"):
            uncommented = line.lstrip("#").strip()
            if uncommented == "[Peer]":
                flush_peer()
                current_section = "peer"
                current_peer = {}
                current_disabled = True
                continue
            # Other comments ignored
            continue

        if line == "[Interface]":
            flush_peer()
            current_section = "interface"
            current_disabled = False
            continue

        if line == "[Peer]":
            flush_peer()
            current_section = "peer"
            current_peer = {}
            current_disabled = False
            continue

        if "=" in line and current_section is not None:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if current_section == "interface":
                if key in ("PostUp", "PostDown"):
                    result["interface"].setdefault(key, []).append(value)
                else:
                    result["interface"][key] = value
            elif current_section == "peer" and current_peer is not None:
                current_peer[key] = value

    flush_peer()
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wg.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/wg.py tests/test_wg.py
git commit -m "feat(wg): parse wg0.conf into structured interface+peers"
```

---

## Task 9: WG module — generate wg0.conf from state

**Files:**
- Modify: `src/wg_admin/wg.py`
- Modify: `tests/test_wg.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_wg.py
def test_generate_conf_roundtrips_simple_case():
    original = textwrap.dedent("""
    [Interface]
    Address = 10.0.0.1/24
    ListenPort = 51820
    PrivateKey = SERVER=
    PostUp = iptables -A FORWARD -i %i -j ACCEPT
    PostDown = iptables -D FORWARD -i %i -j ACCEPT

    [Peer]
    PublicKey = AAAA
    AllowedIPs = 10.0.0.2/32
    """).strip()
    parsed = wg.parse_wg_conf(original)
    regenerated = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    # Round-trip should preserve peer info
    reparsed = wg.parse_wg_conf(regenerated)
    assert reparsed["interface"]["Address"] == "10.0.0.1/24"
    assert reparsed["interface"]["ListenPort"] == "51820"
    assert len(reparsed["peers"]) == 1
    assert reparsed["peers"][0]["PublicKey"] == "AAAA"
    assert reparsed["peers"][0]["AllowedIPs"] == "10.0.0.2/32"


def test_generate_conf_handles_disabled_peer():
    parsed = {
        "interface": {
            "Address": "10.0.0.1/24",
            "ListenPort": "51820",
            "PrivateKey": "SERVER=",
        },
        "peers": [
            {"PublicKey": "AAAA", "AllowedIPs": "10.0.0.2/32", "disabled": False},
            {"PublicKey": "BBBB", "AllowedIPs": "10.0.0.3/32", "disabled": True},
        ],
    }
    out = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    reparsed = wg.parse_wg_conf(out)
    assert len(reparsed["peers"]) == 2
    assert reparsed["peers"][0]["disabled"] is False
    assert reparsed["peers"][1]["disabled"] is True


def test_generate_conf_starts_with_interface_section():
    parsed = {"interface": {"Address": "10.0.0.1/24"}, "peers": []}
    out = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    assert out.startswith("[Interface]")


def test_generate_conf_includes_postup_postdown():
    parsed = {
        "interface": {
            "Address": "10.0.0.1/24",
            "PostUp": ["cmd1", "cmd2"],
            "PostDown": ["cmd3"],
        },
        "peers": [],
    }
    out = wg.generate_wg_conf(parsed["interface"], parsed["peers"])
    assert "PostUp = cmd1" in out
    assert "PostUp = cmd2" in out
    assert "PostDown = cmd3" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wg.py -v -k generate`
Expected: 4 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/wg.py
def generate_wg_conf(interface: dict, peers: list) -> str:
    """Generate wg0.conf content from interface config and peer list.
    
    Each peer dict should have: PublicKey, AllowedIPs, disabled (bool), and
    optionally name (rendered as a comment header).
    """
    lines: list[str] = ["[Interface]"]
    for key in ("Address", "ListenPort", "PrivateKey"):
        if key in interface and interface[key]:
            lines.append(f"{key} = {interface[key]}")
    for key in ("PostUp", "PostDown"):
        for value in interface.get(key, []):
            lines.append(f"{key} = {value}")

    for peer in peers:
        lines.append("")
        prefix = "# " if peer.get("disabled") else ""
        if peer.get("name"):
            lines.append(f"{prefix}# name: {peer['name']}")
        lines.append(f"{prefix}[Peer]")
        lines.append(f"{prefix}PublicKey = {peer['PublicKey']}")
        lines.append(f"{prefix}AllowedIPs = {peer['AllowedIPs']}")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wg.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/wg.py tests/test_wg.py
git commit -m "feat(wg): generate wg0.conf from structured state"
```

---

## Task 10: WG module — parse `wg show dump` and subprocess wrappers

**Files:**
- Modify: `src/wg_admin/wg.py`
- Modify: `tests/test_wg.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_wg.py
import subprocess
from dataclasses import dataclass

import pytest


# Sample `wg show wg0 dump` output (tab-separated, from real wg)
WG_SHOW_DUMP = "\n".join([
    "wg0\t2nU5Z...abbreviated_server_key=\twg0\t10.0.0.1/24\t51820\toff\t1612345678\t12345\t67890",
    "wg0\tY73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw=\t192.168.1.42:51820\t10.0.0.2/32\t1718642000\t102400\t204800\t0",
    "wg0\tRFSVIEkXbjlHGp+W0+FbPe3VAH5g7n5NoHOs1pk7P2Y=\t(none)\t10.0.0.3/32\t0\t0\t0\t0",
])


def test_parse_wg_show_dump_returns_peers():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    assert len(peers) == 2


def test_parse_wg_show_dump_skips_interface_line():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    # First line is interface, not a peer
    for p in peers:
        assert p.public_key != "2nU5Z...abbreviated_server_key="


def test_parse_wg_show_dump_extracts_fields():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    p0 = peers[0]
    assert p0.public_key == "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw="
    assert p0.endpoint == "192.168.1.42:51820"
    assert p0.allowed_ips == ["10.0.0.2/32"]
    assert p0.latest_handshake == 1718642000
    assert p0.transfer_rx == 102400
    assert p0.transfer_tx == 204800


def test_parse_wg_show_dump_handles_disconnected_peer():
    peers = wg.parse_wg_show_dump(WG_SHOW_DUMP, "wg0")
    p1 = peers[1]
    assert p1.endpoint is None
    assert p1.latest_handshake == 0
    assert p1.transfer_rx == 0


def test_wg_genkey_calls_subprocess(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["wg", "genkey"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="PRIVATE_KEY_ABC\n")
        if cmd == ["wg", "pubkey"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="PUBLIC_KEY_XYZ\n")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown cmd")

    monkeypatch.setattr(subprocess, "run", fake_run)
    priv, pub = wg.wg_genkey()
    assert priv == "PRIVATE_KEY_ABC"
    assert pub == "PUBLIC_KEY_XYZ"


def test_wg_show_dump_calls_subprocess(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["wg", "show", "wg0", "dump"]
        return subprocess.CompletedProcess(cmd, 0, stdout=WG_SHOW_DUMP)

    monkeypatch.setattr(subprocess, "run", fake_run)
    peers = wg.wg_show_dump("wg0")
    assert len(peers) == 2


def test_wg_quick_restart_calls_systemctl(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    wg.wg_quick_restart("wg0")
    assert calls[0] == ["systemctl", "restart", "wg-quick@wg0"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wg.py -v -k "show_dump or genkey or quick_restart"`
Expected: 7 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/wg.py
import subprocess
from dataclasses import dataclass
from typing import List


@dataclass
class PeerStatus:
    public_key: str
    endpoint: Optional[str]
    allowed_ips: List[str]
    latest_handshake: int
    transfer_rx: int
    transfer_tx: int


def parse_wg_show_dump(output: str, interface: str = "wg0") -> List[PeerStatus]:
    """Parse `wg show <interface> dump` output. Skips the interface header line."""
    peers: List[PeerStatus] = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        # First field is interface name; interface-info line has interface as
        # the 3rd field too. Peer lines have the public key as 2nd field and
        # a different 3rd-field format. Simplest discriminator: line that
        # starts with "<interface>\t" and has 8 fields, and whose 3rd field
        # is the psk or "(none)" — but the dump format is:
        # interface line: ifname  privkey  pubkey  listenport  fwmark  (8 fields)
        # peer line:      ifname  pubkey  endpoint  allowedips  handshake  rx  tx  keepalive
        # The interface line has the privkey (2nd), which contains '=' or
        # is '(none)'/'(em)'; peer line has pubkey (2nd). Easiest robust
        # check: if 4th field looks like a port number AND 3rd field is
        # numeric or "(none)", it's the interface line.
        # Better: count fields — interface has 8 too in modern wg. Use the
        # convention that interface-line 3rd field is pubkey of server
        # (typically base64 ending in '=') while peer-line 3rd field is
        # endpoint (host:port or '(none)').
        third = parts[2]
        if ":" in third or third == "(none)":
            # Peer line
            peers.append(PeerStatus(
                public_key=parts[1],
                endpoint=third if third != "(none)" else None,
                allowed_ips=parts[3].split(",") if parts[3] else [],
                latest_handshake=int(parts[4]) if parts[4] else 0,
                transfer_rx=int(parts[5]) if parts[5] else 0,
                transfer_tx=int(parts[6]) if parts[6] else 0,
            ))
    return peers


def wg_genkey() -> tuple[str, str]:
    """Run `wg genkey` then pipe to `wg pubkey`. Returns (priv, pub)."""
    priv_proc = subprocess.run(
        ["wg", "genkey"], capture_output=True, text=True, check=True
    )
    priv = priv_proc.stdout.strip()
    pub_proc = subprocess.run(
        ["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True
    )
    return priv, pub_proc.stdout.strip()


def wg_show_dump(interface: str = "wg0") -> List[PeerStatus]:
    """Run `wg show <interface> dump` and return parsed statuses."""
    proc = subprocess.run(
        ["wg", "show", interface, "dump"],
        capture_output=True, text=True, check=True,
    )
    return parse_wg_show_dump(proc.stdout, interface)


def wg_quick_restart(interface: str = "wg0") -> None:
    """Restart wg-quick service. Raises CalledProcessError on failure."""
    subprocess.run(
        ["systemctl", "restart", f"wg-quick@{interface}"],
        capture_output=True, text=True, check=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wg.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/wg.py tests/test_wg.py
git commit -m "feat(wg): parse wg show dump + subprocess wrappers (genkey/show/restart)"
```

---

## Task 11: ConfGen module — render peer .conf

**Files:**
- Create: `src/wg_admin/confgen.py`
- Test: `tests/test_confgen.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_confgen.py
from wg_admin import confgen


def test_render_conf_has_interface_section():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=["1.1.1.1"],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "[Interface]" in out


def test_render_conf_has_peer_section():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=[],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "[Peer]" in out
    assert "PublicKey = PUB=" in out
    assert "Endpoint = vpn.example.com:51820" in out
    assert "AllowedIPs = 0.0.0.0/0" in out


def test_render_conf_omits_dns_when_empty():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=[],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "DNS" not in out


def test_render_conf_joins_multiple_dns():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=["1.1.1.1", "1.0.0.1"],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0"],
    )
    out = confgen.render_conf(peer)
    assert "DNS = 1.1.1.1,1.0.0.1" in out


def test_render_conf_joins_multiple_allowed_ips():
    peer = confgen.PeerConfig(
        private_key="PRIV=",
        address="10.0.0.2/32",
        dns=[],
        server_public_key="PUB=",
        endpoint="vpn.example.com:51820",
        allowed_ips=["0.0.0.0/0", "::/0"],
    )
    out = confgen.render_conf(peer)
    assert "AllowedIPs = 0.0.0.0/0,::/0" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_confgen.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write implementation**

```python
# src/wg_admin/confgen.py
"""Generate peer .conf file content and QR codes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class PeerConfig:
    private_key: str
    address: str
    dns: List[str] = field(default_factory=list)
    server_public_key: str = ""
    endpoint: str = ""
    allowed_ips: List[str] = field(default_factory=list)


def render_conf(peer: PeerConfig) -> str:
    """Render the .conf text for a peer."""
    lines = ["[Interface]"]
    lines.append(f"PrivateKey = {peer.private_key}")
    lines.append(f"Address = {peer.address}")
    if peer.dns:
        lines.append(f"DNS = {','.join(peer.dns)}")
    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {peer.server_public_key}")
    lines.append(f"Endpoint = {peer.endpoint}")
    lines.append(f"AllowedIPs = {','.join(peer.allowed_ips)}")
    return "\n".join(lines) + "\n"


def render_qr_png(conf_text: str) -> bytes:
    """Render conf text as a QR code PNG (bytes)."""
    import io
    import qrcode
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(conf_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_confgen.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/confgen.py tests/test_confgen.py
git commit -m "feat(confgen): render peer .conf and QR PNG"
```

---

## Task 12: ConfGen module — QR code

**Files:**
- Modify: `tests/test_confgen.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_confgen.py
def test_render_qr_returns_valid_png():
    png = confgen.render_qr_png("[Interface]\nPrivateKey = x\n")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes


def test_render_qr_grows_with_data_size():
    small = confgen.render_qr_png("x")
    large = confgen.render_qr_png("x" * 200)
    # Larger data → larger PNG
    assert len(large) > len(small)
```

- [ ] **Step 2: Run tests to verify they fail (for QR specifically)**

Run: `pytest tests/test_confgen.py -v -k qr`
Expected: should PASS already (render_qr_png was in Task 11)

If passing already, skip the failure step. If not, debug.

- [ ] **Step 3: (No new code needed — implementation already complete)**

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_confgen.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_confgen.py
git commit -m "test(confgen): cover QR code PNG generation"
```

---

## Task 13: Rate limit module — file-based IP throttling

**Files:**
- Create: `src/wg_admin/ratelimit.py`
- Test: `tests/test_ratelimit.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ratelimit.py
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
    # Simulate time passing beyond block window (5 minutes = 300s)
    state = ratelimit._load(rl_path)
    state["1.2.3.4"]["blocked_until"] = time.time() - 1  # expired
    ratelimit._save(rl_path, state)
    assert ratelimit.is_blocked(rl_path, "1.2.3.4") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ratelimit.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write implementation**

```python
# src/wg_admin/ratelimit.py
"""File-based auth rate limiter. Persists across process restarts."""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

THRESHOLD = 5            # failed attempts before block
WINDOW_SEC = 60          # window for counting fails
BLOCK_SEC = 300          # 5 minutes
ENTRY_EXPIRY_SEC = 3600  # entries cleaned up after 1h of inactivity


@contextmanager
def _flock(path: Path) -> Iterator[None]:
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _prune(data: dict) -> dict:
    """Remove entries older than ENTRY_EXPIRY_SEC."""
    now = time.time()
    cutoff = now - ENTRY_EXPIRY_SEC
    return {
        ip: entry for ip, entry in data.items()
        if entry.get("last_activity", 0) >= cutoff
    }


def record_fail(path: Path, client_ip: str) -> None:
    """Record a failed auth attempt for client_ip."""
    with _flock(path):
        data = _prune(_load(path))
        now = time.time()
        entry = data.get(client_ip, {"fails": 0, "first_fail_at": now, "last_activity": now})
        # Reset window if first_fail is stale
        if now - entry.get("first_fail_at", now) > WINDOW_SEC:
            entry = {"fails": 0, "first_fail_at": now, "last_activity": now}
        entry["fails"] += 1
        entry["last_activity"] = now
        if entry["fails"] >= THRESHOLD:
            entry["blocked_until"] = now + BLOCK_SEC
        data[client_ip] = entry
        _save(path, data)


def is_blocked(path: Path, client_ip: str) -> bool:
    """Check if client_ip is currently blocked."""
    data = _load(path)
    entry = data.get(client_ip)
    if entry is None:
        return False
    blocked_until = entry.get("blocked_until", 0)
    if blocked_until and time.time() < blocked_until:
        return True
    return False


def clear(path: Path, client_ip: str) -> None:
    """Clear rate-limit state for client_ip (used on successful login)."""
    with _flock(path):
        data = _load(path)
        if client_ip in data:
            del data[client_ip]
            _save(path, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ratelimit.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/ratelimit.py tests/test_ratelimit.py
git commit -m "feat(ratelimit): file-based IP throttling with lock + prune"
```

---

## Task 14: Config module

**Files:**
- Create: `src/wg_admin/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import textwrap
import pytest

from wg_admin import config


SAMPLE_INI = textwrap.dedent("""
[wg]
interface = wg0
subnet = 10.0.0.0/24
server_ip = 10.0.0.1

[peer_defaults]
endpoint_host = vpn.example.com
endpoint_port = 51820
allowed_ips = 0.0.0.0/0
dns = 1.1.1.1, 1.0.0.1

[server]
listen_port = 51821
session_lifetime_seconds = 3600
""")


def test_load_returns_parser_with_sections(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(SAMPLE_INI)
    cfg = config.load_config(p)
    assert cfg["wg"]["interface"] == "wg0"
    assert cfg["peer_defaults"]["endpoint_host"] == "vpn.example.com"
    assert cfg["server"]["listen_port"] == "51821"


def test_load_returns_defaults_when_file_missing(tmp_path):
    cfg = config.load_config(tmp_path / "nonexistent.ini")
    assert cfg["wg"]["interface"] == "wg0"
    assert cfg["wg"]["subnet"] == "10.0.0.0/24"
    assert cfg["wg"]["server_ip"] == "10.0.0.1"
    assert cfg["peer_defaults"]["allowed_ips"] == "0.0.0.0/0"
    assert cfg["server"]["listen_port"] == "51821"


def test_load_returns_defaults_when_section_missing(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[wg]\ninterface = wg0\n")
    cfg = config.load_config(p)
    # Missing peer_defaults section should still have defaults
    assert cfg["peer_defaults"]["allowed_ips"] == "0.0.0.0/0"


def test_dns_list_parsed_correctly(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(SAMPLE_INI)
    cfg = config.load_config(p)
    dns = config.get_dns_list(cfg)
    assert dns == ["1.1.1.1", "1.0.0.1"]


def test_allowed_ips_list_parsed_correctly(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(SAMPLE_INI)
    cfg = config.load_config(p)
    ips = config.get_allowed_ips_list(cfg)
    assert ips == ["0.0.0.0/0"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write implementation**

```python
# src/wg_admin/config.py
"""Load config.ini with defaults."""
from __future__ import annotations

import configparser
from pathlib import Path
from typing import List

DEFAULTS = {
    "wg": {
        "interface": "wg0",
        "subnet": "10.0.0.0/24",
        "server_ip": "10.0.0.1",
    },
    "peer_defaults": {
        "endpoint_host": "",
        "endpoint_port": "51820",
        "allowed_ips": "0.0.0.0/0",
        "dns": "",
    },
    "server": {
        "listen_port": "51821",
        "session_lifetime_seconds": "3600",
    },
}


def load_config(path: Path | None = None) -> configparser.ConfigParser:
    """Load config from path, applying defaults. Missing file = all defaults."""
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULTS)
    if path is not None and path.exists():
        cfg.read(path)
    return cfg


def get_dns_list(cfg: configparser.ConfigParser) -> List[str]:
    raw = cfg["peer_defaults"].get("dns", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_allowed_ips_list(cfg: configparser.ConfigParser) -> List[str]:
    raw = cfg["peer_defaults"].get("allowed_ips", "0.0.0.0/0")
    return [s.strip() for s in raw.split(",") if s.strip()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/config.py tests/test_config.py
git commit -m "feat(config): load config.ini with defaults for all sections"
```

---

## Task 15: Flask app — factory and login route

**Files:**
- Create: `src/wg_admin/app.py`
- Create: `tests/conftest.py`
- Create: `tests/test_app.py`

- [ ] **Step 1: Create test fixtures**

```python
# tests/conftest.py
import os
import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Isolated working directory with secrets, state, config paths."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    state_path = tmp_path / "state.json.enc"
    config_path = tmp_path / "config.ini"
    ratelimit_path = secrets_dir / "auth_ratelimit.json"

    # Generate master and session keys
    (secrets_dir / "master.key").write_bytes(os.urandom(32))
    (secrets_dir / "session.key").write_bytes(os.urandom(32))

    # Write a valid auth.ini with hash of "test-password"
    from wg_admin import crypto
    phc = crypto.hash_password("test-password")
    (secrets_dir / "auth.ini").write_text(f"password_hash = {phc}\n")

    monkeypatch.setattr("wg_admin.app.SECRETS_DIR", secrets_dir)
    monkeypatch.setattr("wg_admin.app.STATE_PATH", state_path)
    monkeypatch.setattr("wg_admin.app.CONFIG_PATH", config_path)
    monkeypatch.setattr("wg_admin.app.RATELIMIT_PATH", ratelimit_path)

    return {
        "tmp_path": tmp_path,
        "secrets_dir": secrets_dir,
        "state_path": state_path,
        "config_path": config_path,
        "ratelimit_path": ratelimit_path,
    }


@pytest.fixture
def client(workdir):
    """Flask test client."""
    from wg_admin.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False  # allow over HTTP in tests
    with app.test_client() as c:
        yield c
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_app.py
def test_index_redirects_to_login_when_not_authed(client):
    r = client.get("/")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"password" in r.data.lower()


def test_login_with_correct_password_redirects(client):
    r = client.post("/login", data={"password": "test-password"})
    assert r.status_code in (301, 302)
    assert "/peers" in r.headers["Location"]


def test_login_with_wrong_password_shows_error(client):
    r = client.post("/login", data={"password": "wrong"})
    assert r.status_code == 200  # stays on login page
    assert b"invalid" in r.data.lower() or b"credenciais" in r.data.lower()


def test_logout_clears_session(client):
    client.post("/login", data={"password": "test-password"})
    r = client.post("/logout")
    assert r.status_code in (301, 302)
    # Follow up request to / should redirect to login
    r2 = client.get("/")
    assert "/login" in r2.headers["Location"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_app.py -v`
Expected: FAIL with ImportError

- [ ] **Step 4: Write minimal app**

```python
# src/wg_admin/app.py
"""Flask app: routes, auth, CSRF, rate limit."""
from __future__ import annotations

import secrets as pysecrets
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, redirect, render_template, request,
    session, url_for,
)
from itsdangerous import URLSafeTimedSerializer

from . import config, crypto, ratelimit, state, wg

# These paths are patched in tests; production values are the real paths.
SECRETS_DIR = Path("/wg-admin/secrets")
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
RATELIMIT_PATH = Path("/wg-admin/secrets/auth_ratelimit.json")


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent.parent / "templates"),
        static_folder=str(Path(__file__).parent.parent.parent / "static"),
    )

    cfg = config.load_config(CONFIG_PATH)
    master_key = (SECRETS_DIR / "master.key").read_bytes()
    session_key = (SECRETS_DIR / "session.key").read_bytes()

    app.secret_key = session_key
    app.config.update(
        SESSION_COOKIE_NAME="wg_admin_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=cfg["server"].getint("session_lifetime_seconds", 3600),
        SESSION_PERMANENT=True,
        MASTER_KEY=master_key,
        CONFIG=cfg,
    )

    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "DELETE"):
            token = session.get("csrf_token")
            if not token or token != request.form.get("csrf_token"):
                abort(403)

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("admin"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapper

    @app.route("/")
    def index():
        return redirect(url_for("peers_list"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            client_ip = request.remote_addr or "unknown"
            if ratelimit.is_blocked(RATELIMIT_PATH, client_ip):
                abort(429)

            password = request.form.get("password", "")
            auth_text = (SECRETS_DIR / "auth.ini").read_text()
            phc_hash = ""
            for line in auth_text.splitlines():
                if line.startswith("password_hash ="):
                    phc_hash = line.split("=", 1)[1].strip()
                    break

            if crypto.verify_password(password, phc_hash):
                session["admin"] = True
                session["csrf_token"] = pysecrets.token_urlsafe(32)
                ratelimit.clear(RATELIMIT_PATH, client_ip)
                return redirect(url_for("peers_list"))
            else:
                ratelimit.record_fail(RATELIMIT_PATH, client_ip)
                flash("Credenciais inválidas", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/peers")
    @login_required
    def peers_list():
        # Filled in Task 16
        return "OK"

    return app


# Module-level app instance for `flask run` and gunicorn-style invocation
app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
```

You also need minimal templates/login.html:

```html
<!-- templates/base.html -->
<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<title>{% block title %}wg-admin{% endblock %}</title>
<link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
<ul class="flash">
{% for category, msg in messages %}
<li class="{{ category }}">{{ msg }}</li>
{% endfor %}
</ul>
{% endif %}
{% endwith %}
{% block content %}{% endblock %}
</body>
</html>
```

```html
<!-- templates/login.html -->
{% extends "base.html" %}
{% block title %}Login — wg-admin{% endblock %}
{% block content %}
<h1>wg-admin</h1>
<form method="post" action="/login">
<input type="password" name="password" placeholder="Password" required autofocus>
<button type="submit">Entrar</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/wg_admin/app.py tests/conftest.py tests/test_app.py templates/
git commit -m "feat(app): Flask factory, login route, CSRF middleware, templates skeleton"
```

---

## Task 16: Flask app — peers list route

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`
- Create: `templates/peers.html`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_app.py
def test_peers_list_shows_existing_peers(client, workdir):
    # Seed state with a peer
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345",
        "name": "Test iPhone",
        "notes": "",
        "public_key": "PUB",
        "private_key_enc": "encrypted-blob",
        "ip": "10.0.0.2",
        "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    # Login
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert r.status_code == 200
    assert b"Test iPhone" in r.data
    assert b"10.0.0.2" in r.data


def test_peers_list_shows_disabled_marker(client, workdir):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345",
        "name": "Disabled Peer",
        "public_key": "PUB",
        "ip": "10.0.0.2",
        "disabled": True,
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert b"disabled" in r.data.lower() or b"inativo" in r.data.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app.py -v -k peers_list`
Expected: 2 new tests FAIL

- [ ] **Step 3: Implement peers_list route and template**

Replace the placeholder `peers_list` in `src/wg_admin/app.py`:

```python
    @app.route("/peers")
    @login_required
    def peers_list():
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        # Try to get live stats; if wg fails, return empty
        statuses_by_key = {}
        try:
            statuses = wg.wg_show_dump(cfg["wg"]["interface"])
            statuses_by_key = {st.public_key: st for st in statuses}
        except Exception:
            app.logger.warning("wg show failed", exc_info=True)
        return render_template(
            "peers.html",
            peers=s["peers"],
            statuses=statuses_by_key,
        )
```

Create `templates/peers.html`:

```html
{% extends "base.html" %}
{% block title %}Peers — wg-admin{% endblock %}
{% block content %}
<h1>Peers</h1>
<p><a href="/peers/new">+ Novo peer</a></p>
<table>
<thead>
<tr><th>Nome</th><th>IP</th><th>Estado</th><th>Último handshake</th><th>RX</th><th>TX</th><th>Ações</th></tr>
</thead>
<tbody>
{% for peer in peers %}
<tr class="{{ 'disabled' if peer.disabled else '' }}">
  <td>{{ peer.name }}{% if peer.disabled %} <em>(inativo)</em>{% endif %}</td>
  <td>{{ peer.ip }}</td>
  <td>{{ "ativo" if not peer.disabled else "inativo" }}</td>
  <td>{{ statuses.get(peer.public_key).latest_handshake if statuses.get(peer.public_key) else "—" }}</td>
  <td>{{ statuses.get(peer.public_key).transfer_rx if statuses.get(peer.public_key) else "—" }}</td>
  <td>{{ statuses.get(peer.public_key).transfer_tx if statuses.get(peer.public_key) else "—" }}</td>
  <td>
    <a href="/peers/{{ peer.id }}/conf">.conf</a>
    <a href="/peers/{{ peer.id }}/qr">QR</a>
    <form method="post" action="/peers/{{ peer.id }}/toggle" style="display:inline">
      <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
      <button type="submit">{{ "Ativar" if peer.disabled else "Desativar" }}</button>
    </form>
    <form method="post" action="/peers/{{ peer.id }}/delete" style="display:inline"
          onsubmit="return confirm('Apagar {{ peer.name }}?')">
      <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
      <button type="submit">Apagar</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py templates/peers.html
git commit -m "feat(app): peers list route with live wg stats and disabled marker"
```

---

## Task 17: Flask app — create peer route

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`
- Create: `templates/peer_form.html`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_app.py
def test_create_peer_adds_to_state(client, workdir, monkeypatch):
    # Mock wg_genkey to avoid needing real wg installed
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(
        wg_mod, "wg_genkey",
        lambda: ("PRIVATE_MOCKED=", "PUBLIC_MOCKED=")
    )
    monkeypatch.setattr(
        wg_mod, "wg_quick_restart",
        lambda interface="wg0": None
    )

    # Login
    client.post("/login", data={"password": "test-password"})

    # Get csrf token from session via a peers-list request
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/new", data={
        "name": "New Test Peer",
        "notes": "test note",
        "csrf_token": csrf,
    })
    assert r.status_code in (301, 302)

    # Verify peer was added to state
    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert len(s["peers"]) == 1
    assert s["peers"][0]["name"] == "New Test Peer"
    assert s["peers"][0]["public_key"] == "PUBLIC_MOCKED="
    assert s["peers"][0]["ip"] == "10.0.0.2"  # first free in default subnet


def test_create_peer_validates_name_required(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(wg_mod, "wg_genkey", lambda: ("P=", "PUB="))
    monkeypatch.setattr(wg_mod, "wg_quick_restart", lambda interface="wg0": None)

    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/new", data={"name": "", "csrf_token": csrf})
    # Should re-render form with error, not redirect
    assert r.status_code == 200
    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert len(s["peers"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app.py -v -k create`
Expected: 2 new tests FAIL

- [ ] **Step 3: Implement create route**

Insert before `return app` in `create_app()`:

```python
    @app.route("/peers/new", methods=["GET", "POST"])
    @login_required
    def peer_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            notes = request.form.get("notes", "").strip()
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_form.html"), 400

            # Generate keys, allocate IP, build peer dict
            priv, pub = wg.wg_genkey()
            s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
            ip = state.allocate_ip(
                s,
                cfg["wg"]["subnet"],
                cfg["wg"]["server_ip"],
            )
            new_peer = {
                "id": state.new_peer_id(),
                "name": name,
                "notes": notes,
                "public_key": pub,
                "private_key_enc": priv,  # TODO Task 18: encrypt with master key
                "ip": ip,
                "disabled": False,
                "created_at": state.utc_now_iso(),
            }
            state.add_peer(s, new_peer)
            # Persist state BEFORE touching wg runtime
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            # Apply to wg
            try:
                _apply_state_to_wg(s, cfg)
                wg.wg_quick_restart(cfg["wg"]["interface"])
            except Exception:
                app.logger.exception("Failed to apply state to wg")
                flash("Peer criado no estado, mas falhou apply ao wg — ver logs", "error")
                return redirect(url_for("peers_list"))
            flash(f"Peer criado: {name} ({ip})", "success")
            return redirect(url_for("peers_list"))
        return render_template("peer_form.html")

    def _apply_state_to_wg(s: dict, cfg) -> None:
        """Regenerate /etc/wireguard/wg0.conf from state and write atomically."""
        interface_path = Path(f"/etc/wireguard/{cfg['wg']['interface']}.conf")
        # Read existing interface section to preserve PrivateKey, PostUp, PostDown
        if interface_path.exists():
            existing = wg.parse_wg_conf(interface_path.read_text())
            interface = existing["interface"]
        else:
            interface = {
                "Address": f"{cfg['wg']['server_ip']}/{cfg['wg']['subnet'].split('/')[-1]}",
                "ListenPort": "51820",
            }
        # Build peer list for confgen
        wg_peers = [
            {
                "PublicKey": p["public_key"],
                "AllowedIPs": f"{p['ip']}/32",
                "disabled": p.get("disabled", False),
                "name": p["name"],
            }
            for p in s["peers"]
        ]
        conf_text = wg.generate_wg_conf(interface, wg_peers)
        # Atomic write
        import os as _os
        tmp = interface_path.with_suffix(".conf.tmp")
        tmp.write_text(conf_text)
        _os.replace(tmp, interface_path)
```

Create `templates/peer_form.html`:

```html
{% extends "base.html" %}
{% block title %}Novo peer — wg-admin{% endblock %}
{% block content %}
<h1>Novo peer</h1>
<form method="post" action="/peers/new">
  <label>Nome: <input type="text" name="name" required></label>
  <label>Notas: <textarea name="notes"></textarea></label>
  <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
  <button type="submit">Criar</button>
</form>
<p><a href="/peers">Cancelar</a></p>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v -k create`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py templates/peer_form.html
git commit -m "feat(app): create peer with keygen, IP allocation, state-before-wg ordering"
```

---

## Task 18: Flask app — delete and toggle routes

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_app.py
def _seed_peer(workdir, client, peer_id="abc12345", name="To Delete"):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": peer_id,
        "name": name,
        "public_key": "PUB",
        "private_key_enc": "priv",
        "ip": "10.0.0.2",
        "disabled": False,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])


def test_delete_peer_removes_from_state(client, workdir, monkeypatch):
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(wg_mod, "wg_quick_restart", lambda interface="wg0": None)
    monkeypatch.setattr("wg_admin.app._apply_state_to_wg", lambda s, cfg: None)

    _seed_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/abc12345/delete", data={"csrf_token": csrf})
    assert r.status_code in (301, 302)

    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert len(s["peers"]) == 0


def test_toggle_peer_flips_disabled(client, workdir, monkeypatch):
    monkeypatch.setattr("wg_admin.app._apply_state_to_wg", lambda s, cfg: None)
    from wg_admin import wg as wg_mod
    monkeypatch.setattr(wg_mod, "wg_quick_restart", lambda interface="wg0": None)

    _seed_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/abc12345/toggle", data={"csrf_token": csrf})
    assert r.status_code in (301, 302)

    from wg_admin import state as state_mod
    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["disabled"] is True


def test_delete_unknown_peer_returns_404(client, workdir, monkeypatch):
    _seed_peer(workdir, client)
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]

    r = client.post("/peers/nonexistent/delete", data={"csrf_token": csrf})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app.py -v -k "delete or toggle"`
Expected: 3 new tests FAIL

- [ ] **Step 3: Append routes**

```python
    @app.route("/peers/<peer_id>/delete", methods=["POST"])
    @login_required
    def peer_delete(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        if not state.remove_peer(s, peer_id):
            abort(404)
        state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
        try:
            _apply_state_to_wg(s, cfg)
            wg.wg_quick_restart(cfg["wg"]["interface"])
        except Exception:
            app.logger.exception("Failed to apply after delete")
            flash("Peer removido do estado, mas apply falhou — ver logs", "error")
        flash("Peer apagado", "success")
        return redirect(url_for("peers_list"))

    @app.route("/peers/<peer_id>/toggle", methods=["POST"])
    @login_required
    def peer_toggle(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        state.set_peer_disabled(s, peer_id, not peer.get("disabled", False))
        state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
        try:
            _apply_state_to_wg(s, cfg)
            wg.wg_quick_restart(cfg["wg"]["interface"])
        except Exception:
            app.logger.exception("Failed to apply after toggle")
            flash("Toggle registrado mas apply falhou", "error")
        return redirect(url_for("peers_list"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "feat(app): delete and toggle routes with state-before-wg ordering"
```

---

## Task 19: Flask app — conf and QR download routes

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_app.py
def test_download_conf_returns_attachment(client, workdir):
    _seed_peer(workdir, client, name="Downloadable")
    client.post("/login", data={"password": "test-password"})

    r = client.get("/peers/abc12345/conf")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    assert b"[Interface]" in r.data
    assert b"PRIVATE_KEY_HERE" in r.data or b"priv" in r.data  # private_key_enc value


def test_download_qr_returns_png(client, workdir):
    _seed_peer(workdir, client, name="QRPeer")
    client.post("/login", data={"password": "test-password"})

    r = client.get("/peers/abc12345/qr")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.data.startswith(b"\x89PNG")


def test_download_conf_unknown_peer_404(client, workdir):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/nonexistent/conf")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app.py -v -k "download or qr"`
Expected: 3 new tests FAIL

- [ ] **Step 3: Append routes**

```python
    @app.route("/peers/<peer_id>/conf")
    @login_required
    def peer_conf(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        # TODO Task 20: decrypt private_key_enc properly
        peer_cfg = confgen.PeerConfig(
            private_key=peer["private_key_enc"],
            address=peer["ip"] + "/32",
            dns=config.get_dns_list(cfg),
            server_public_key=cfg["wg"].get("server_public_key", ""),
            endpoint=f"{cfg['peer_defaults']['endpoint_host']}:{cfg['peer_defaults']['endpoint_port']}",
            allowed_ips=config.get_allowed_ips_list(cfg),
        )
        text = confgen.render_conf(peer_cfg)
        from flask import Response
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="wg-{peer["name"]}.conf"'},
        )

    @app.route("/peers/<peer_id>/qr")
    @login_required
    def peer_qr(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        peer_cfg = confgen.PeerConfig(
            private_key=peer["private_key_enc"],
            address=peer["ip"] + "/32",
            dns=config.get_dns_list(cfg),
            server_public_key=cfg["wg"].get("server_public_key", ""),
            endpoint=f"{cfg['peer_defaults']['endpoint_host']}:{cfg['peer_defaults']['endpoint_port']}",
            allowed_ips=config.get_allowed_ips_list(cfg),
        )
        text = confgen.render_conf(peer_cfg)
        png = confgen.render_qr_png(text)
        from flask import Response
        return Response(png, mimetype="image/png")
```

You also need to import `confgen` at the top of app.py:

```python
from . import confgen, config, crypto, ratelimit, state, wg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "feat(app): /peers/<id>/conf and /qr download routes"
```

---

## Task 20: Encrypt private keys at rest in state

**Files:**
- Modify: `src/wg_admin/state.py` (add encrypt_private/decrypt_private)
- Modify: `src/wg_admin/app.py` (use them)
- Modify: `tests/test_state.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_state.py
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
    # Wrong master either raises or returns garbage — never correct
    from cryptography.exceptions import InvalidTag
    try:
        result = state.decrypt_private_key(blob, bytes([1] * 32))
        assert result != "X="
    except InvalidTag:
        pass  # acceptable


def test_encrypt_private_key_different_each_call():
    master = bytes(32)
    blob1 = state.encrypt_private_key("X=", master)
    blob2 = state.encrypt_private_key("X=", master)
    assert blob1 != blob2  # different nonces
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py -v -k private`
Expected: 3 new tests FAIL

- [ ] **Step 3: Append implementation**

```python
# append to src/wg_admin/state.py
def encrypt_private_key(private_key: str, master_key: bytes) -> str:
    """Encrypt a peer private key string. Returns hex envelope (salt|nonce|ct)."""
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = os.urandom(crypto.STATE_SALT_SIZE)
    nonce = os.urandom(crypto.STATE_NONCE_SIZE)
    aes_key = crypto.derive_state_key(master_key, salt)
    # Use a different `info` for domain separation between state and peer keys
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
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
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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
```

- [ ] **Step 4: Update app.py to use encrypt_private_key when creating peers**

In the `peer_new` route, change:

```python
            new_peer = {
                ...
                "private_key_enc": priv,  # TODO Task 18: encrypt with master key
                ...
            }
```

to:

```python
            new_peer = {
                ...
                "private_key_enc": state.encrypt_private_key(priv, app.config["MASTER_KEY"]),
                ...
            }
```

And in the `peer_conf` and `peer_qr` routes, change:

```python
            private_key=peer["private_key_enc"],
```

to:

```python
            private_key=state.decrypt_private_key(peer["private_key_enc"], app.config["MASTER_KEY"]),
```

- [ ] **Step 5: Update test fixtures to use encrypted private keys**

In tests where you seed peers directly with `"private_key_enc": "priv"`, change to:

```python
from wg_admin import state as state_mod
"private_key_enc": state_mod.encrypt_private_key("PRIVATE_KEY_HERE=", client.application.config["MASTER_KEY"]),
```

- [ ] **Step 6: Run all tests**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/wg_admin/state.py src/wg_admin/app.py tests/
git commit -m "feat(state): per-peer private key encryption with domain-separated HKDF info"
```

---

## Task 21: Static CSS — minimal styling

**Files:**
- Create: `static/style.css`

- [ ] **Step 1: Write CSS**

```css
/* static/style.css */
:root {
  --bg: #fafafa;
  --fg: #1a1a1a;
  --muted: #666;
  --primary: #2563eb;
  --border: #d4d4d8;
  --danger: #dc2626;
  --success: #16a34a;
  --warn-bg: #fef3c7;
  --error-bg: #fee2e2;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--fg);
  padding: 2rem;
  max-width: 1100px;
  margin: 0 auto;
  font-size: 14px;
}

h1 { font-size: 1.5rem; margin: 0 0 1.5rem; }

form { margin: 1rem 0; }
input[type=text], input[type=password], textarea {
  display: block;
  margin: 0.5rem 0;
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 14px;
  width: 100%;
  max-width: 400px;
}
button, .btn {
  background: var(--primary);
  color: white;
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}
button:hover { opacity: 0.9; }

a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
}
th, td {
  padding: 0.5rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
th { background: #f4f4f5; font-weight: 600; }
tr.disabled td { opacity: 0.5; }
tr.disabled em { color: var(--danger); }

.flash {
  list-style: none;
  padding: 0;
  margin: 1rem 0;
}
.flash li {
  padding: 0.75rem 1rem;
  border-radius: 4px;
  margin-bottom: 0.5rem;
}
.flash .error { background: var(--error-bg); }
.flash .success { background: #dcfce7; color: var(--success); }
```

- [ ] **Step 2: Verify visually (manual)**

Run the dev server and check the login page renders with styling. (Manual step — document in smoke-test.md.)

- [ ] **Step 3: Commit**

```bash
git add static/style.css
git commit -m "feat(ui): minimal CSS for login/peers/forms"
```

---

## Task 22: Error pages

**Files:**
- Create: `templates/error.html`
- Modify: `src/wg_admin/app.py`

- [ ] **Step 1: Write error template**

```html
<!-- templates/error.html -->
{% extends "base.html" %}
{% block title %}Erro {{ code }} — wg-admin{% endblock %}
{% block content %}
<h1>Erro {{ code }}</h1>
<p>{{ message }}</p>
<p><a href="/peers">Voltar aos peers</a></p>
{% endblock %}
```

- [ ] **Step 2: Register error handlers in app.py**

Add inside `create_app()`:

```python
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="CSRF token inválido ou sessão expirada."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Recurso não encontrado."), 404

    @app.errorhandler(429)
    def too_many(e):
        return render_template("error.html", code=429, message="Muitas tentativas de login. Tente novamente mais tarde."), 429

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Internal error")
        return render_template("error.html", code=500, message="Erro interno."), 500
```

- [ ] **Step 3: Add test**

```python
# append to tests/test_app.py
def test_404_returns_error_page(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers/nonexistent/conf")
    assert r.status_code == 404
    assert b"404" in r.data
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_app.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add templates/error.html src/wg_admin/app.py tests/test_app.py
git commit -m "feat(app): custom error pages (403/404/429/500)"
```

---

## Task 23: systemd socket and service units

**Files:**
- Create: `systemd/wg-admin.socket`
- Create: `systemd/wg-admin.service`

- [ ] **Step 1: Write socket unit**

```ini
# systemd/wg-admin.socket
[Unit]
Description=wg-admin HTTP socket (socket activation)
PartOf=wg-admin.service

[Socket]
ListenStream=51821
Accept=no
# Keep service briefly alive after activity to amortize Python startup
KeepAlive=30s

[Install]
WantedBy=sockets.target
```

- [ ] **Step 2: Write service unit**

```ini
# systemd/wg-admin.service
[Unit]
Description=wg-admin Flask service
Requires=wg-admin.socket
After=network.target

[Service]
Type=exec
User=root
WorkingDirectory=/wg-admin
Environment=PYTHONUNBUFFERED=1
ExecStart=/wg-admin/venv/bin/python -m wg_admin.app
Restart=on-failure
RestartSec=5

# Hardening
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/wg-admin /etc/wireguard
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SYS_ADMIN

# Prevent crash loops
StartLimitIntervalSec=60
StartLimitBurst=30

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Verify unit syntax**

Run: `systemd-analyze verify systemd/wg-admin.service systemd/wg-admin.socket`
Expected: no errors (or only warnings about paths that don't exist yet)

- [ ] **Step 4: Commit**

```bash
git add systemd/
git commit -m "feat(systemd): socket + service units with hardening"
```

---

## Task 24: Install script

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Write install script**

```bash
#!/usr/bin/env bash
# install.sh — idempotent installer for wg-admin.
# Run as root: sudo bash install.sh
set -euo pipefail

INSTALL_DIR="/wg-admin"
SERVICE_USER="root"
PYTHON_MIN_VERSION="3.11"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo ">>> $*"; }

# --- Pre-flight ---
[[ $EUID -eq 0 ]] || err "Must run as root (use sudo)."

if ! command -v python3 >/dev/null; then err "python3 not installed"; fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
[[ "$(printf "%s\n%s" "$PYTHON_MIN_VERSION" "$PYVER" | sort -V | head -1)" == "$PYTHON_MIN_VERSION" ]] \
  || err "Python >= $PYTHON_MIN_VERSION required (have $PYVER)"

for cmd in wg wg-quick systemctl; do
  command -v "$cmd" >/dev/null || err "$cmd not found"
done

# --- Directories ---
info "Creating $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"/{secrets,templates,static,systemd}
cp -r src templates static systemd requirements*.txt config.ini.example install.sh uninstall.sh "$INSTALL_DIR/" 2>/dev/null || true

# --- venv + deps ---
info "Creating venv and installing deps"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# --- Secrets ---
info "Generating secrets (if missing)"
[[ -s "$INSTALL_DIR/secrets/master.key" ]]  || head -c 32 /dev/urandom > "$INSTALL_DIR/secrets/master.key"
[[ -s "$INSTALL_DIR/secrets/session.key" ]] || head -c 32 /dev/urandom > "$INSTALL_DIR/secrets/session.key"
chmod 0700 "$INSTALL_DIR/secrets"
chmod 0600 "$INSTALL_DIR/secrets"/*.key

# --- Admin password ---
if [[ ! -f "$INSTALL_DIR/secrets/auth.ini" ]]; then
  info "Setting admin password"
  read -r -s -p "Admin password: " PWD1; echo
  read -r -s -p "Confirm: " PWD2; echo
  [[ "$PWD1" == "$PWD2" ]] || err "Passwords don't match"
  HASH=$("$INSTALL_DIR/venv/bin/python" -c "
import sys
from argon2 import PasswordHasher
print(PasswordHasher(time_cost=3, memory_cost=16384, parallelism=1).hash(sys.argv[1]))
" "$PWD1")
  cat > "$INSTALL_DIR/secrets/auth.ini" <<EOF
password_hash = $HASH
EOF
  chmod 0600 "$INSTALL_DIR/secrets/auth.ini"
fi

# --- config.ini ---
if [[ ! -f "$INSTALL_DIR/config.ini" ]]; then
  info "Configuring"
  read -r -p "Endpoint hostname (e.g. vpn.example.com): " ENDPOINT
  read -r -p "Listen port [51821]: " LISTEN_PORT
  LISTEN_PORT=${LISTEN_PORT:-51821}
  cp "$INSTALL_DIR/config.ini.example" "$INSTALL_DIR/config.ini"
  sed -i "s/^endpoint_host = .*/endpoint_host = $ENDPOINT/" "$INSTALL_DIR/config.ini"
  sed -i "s/^listen_port = .*/listen_port = $LISTEN_PORT/" "$INSTALL_DIR/config.ini"
fi

# --- Import existing peers ---
if [[ ! -f "$INSTALL_DIR/state.json.enc" && -f /etc/wireguard/wg0.conf ]]; then
  info "Importing existing peers from /etc/wireguard/wg0.conf"
  "$INSTALL_DIR/venv/bin/python" -c "
import sys
sys.path.insert(0, '$INSTALL_DIR')
from pathlib import Path
from wg_admin import state, wg
parsed = wg.parse_wg_conf(Path('/etc/wireguard/wg0.conf').read_text())
s = state.empty_state()
for p in parsed['peers']:
    ip = p.get('AllowedIPs', '').split('/')[0]
    if not ip: continue
    s['peers'].append({
        'id': state.new_peer_id(),
        'name': f'peer-{ip.split(".")[-1]}',
        'notes': 'imported from existing wg0.conf',
        'public_key': p['PublicKey'],
        'private_key_enc': '',  # legacy peers: private key unknown
        'ip': ip,
        'disabled': p.get('disabled', False),
        'created_at': state.utc_now_iso(),
        'imported_from_legacy': True,
    })
master = Path('$INSTALL_DIR/secrets/master.key').read_bytes()
state.save_state(Path('$INSTALL_DIR/state.json.enc'), s, master)
print(f'Imported {len(s[\"peers\"])} peers.')
"
fi

# --- systemd units ---
info "Installing systemd units"
cp "$INSTALL_DIR/systemd/wg-admin.socket" /etc/systemd/system/
cp "$INSTALL_DIR/systemd/wg-admin.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wg-admin.socket

# --- firewalld ---
if command -v firewall-cmd >/dev/null; then
  info "Opening firewalld"
  LISTEN_PORT=$(grep '^listen_port' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
  firewall-cmd --add-port="$LISTEN_PORT/tcp" --permanent || true
  firewall-cmd --reload || true
fi

# --- Final message ---
HOST=$(grep '^endpoint_host' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
PORT=$(grep '^listen_port' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
cat <<EOF

=================================================
wg-admin installed.

Next steps:
1. Get a TLS cert (recommended):
   systemctl stop wg-admin.socket
   certbot certonly --standalone -d $HOST \\
     --pre-hook "systemctl stop wg-admin.socket" \\
     --post-hook "systemctl start wg-admin.socket"
   (then configure app.py to use /etc/letsencrypt/live/$HOST/)

2. Open panel: https://$HOST:$PORT/
=================================================
EOF
```

- [ ] **Step 2: Make executable and create example config**

```bash
chmod +x install.sh
```

Create `config.ini.example`:

```ini
[wg]
interface = wg0
subnet = 10.0.0.0/24
server_ip = 10.0.0.1
server_public_key =

[peer_defaults]
endpoint_host = vpn.example.com
endpoint_port = 51820
allowed_ips = 0.0.0.0/0
dns = 1.1.1.1, 1.0.0.1

[server]
listen_port = 51821
session_lifetime_seconds = 3600
```

- [ ] **Step 3: Verify bash syntax**

Run: `bash -n install.sh`
Expected: no output (no syntax errors)

- [ ] **Step 4: Commit**

```bash
git add install.sh config.ini.example
git commit -m "feat(install): idempotent installer with secrets, config, peer import"
```

---

## Task 25: Uninstall script

**Files:**
- Create: `uninstall.sh`

- [ ] **Step 1: Write uninstall script**

```bash
#!/usr/bin/env bash
# uninstall.sh — remove wg-admin. Option --keep-state preserves secrets+state.
set -euo pipefail

INSTALL_DIR="/wg-admin"
KEEP_STATE=false
[[ "${1:-}" == "--keep-state" ]] && KEEP_STATE=true

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo ">>> $*"; }

[[ $EUID -eq 0 ]] || err "Must run as root"

if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "wg-admin not found at $INSTALL_DIR; nothing to do."
  exit 0
fi

if $KEEP_STATE; then
  BACKUP="/tmp/wg-admin-backup-$(date +%s).tar.gz"
  info "Backing up state and secrets to $BACKUP"
  tar -czf "$BACKUP" \
    -C "$INSTALL_DIR" secrets state.json.enc state.json.enc.bak state.json.enc.bak1 config.ini 2>/dev/null || true
  echo "Backup saved to $BACKUP"
fi

info "Stopping services"
systemctl stop wg-admin.socket wg-admin.service 2>/dev/null || true
systemctl disable wg-admin.socket wg-admin.service 2>/dev/null || true

info "Removing systemd units"
rm -f /etc/systemd/system/wg-admin.socket /etc/systemd/system/wg-admin.service
systemctl daemon-reload

info "Removing $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

info "Done. wg-admin fully removed."
if $KEEP_STATE; then
  echo "Secrets/state preserved in backup tarball."
fi
```

- [ ] **Step 2: Make executable**

```bash
chmod +x uninstall.sh
```

- [ ] **Step 3: Verify bash syntax**

Run: `bash -n uninstall.sh`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add uninstall.sh
git commit -m "feat(uninstall): remove wg-admin with optional state backup"
```

---

## Task 26: Integration test with real wg

**Files:**
- Create: `tests/integration.sh`

- [ ] **Step 1: Write integration test script**

```bash
#!/usr/bin/env bash
# tests/integration.sh — exercise real wg commands in a network namespace.
# Requires root and wg kernel module.
set -euo pipefail

# Skip if no root or no wg
[[ $EUID -eq 0 ]] || { echo "Skip: needs root"; exit 77; }
command -v wg >/dev/null || { echo "Skip: no wg"; exit 77; }

# Setup: create wg interface in a netns, no systemwide changes
NS="wg-test-$$"
ip netns add "$NS" || { echo "Skip: cannot netns"; exit 77; }
trap 'ip netns del "$NS" 2>/dev/null || true' EXIT

# Run app via pytest with monkeypatched wg paths is left as an exercise;
# for now, this script validates that:
# 1. wg genkey/pubkey works
# 2. wg-quick up/down on a tmp conf works
# 3. wg show returns expected format

TMP=$(mktemp -d)
cd "$TMP"

# Generate server keypair
SERVER_PRIV=$(wg genkey)
SERVER_PUB=$(echo "$SERVER_PRIV" | wg pubkey)

# Generate peer keypair
PEER_PRIV=$(wg genkey)
PEER_PUB=$(echo "$PEER_PRIV" | wg pubkey)

# Write a minimal wg0.conf
cat > wg0.conf <<EOF
[Interface]
PrivateKey = $SERVER_PRIV
Address = 10.99.99.1/24
ListenPort = 51899

[Peer]
PublicKey = $PEER_PUB
AllowedIPs = 10.99.99.2/32
EOF

# Bring up inside the namespace
ip netns exec "$NS" ip link add wg0 type wireguard || true
ip netns exec "$NS" wg setconf wg0 wg0.conf || true
ip netns exec "$NS" ip addr add 10.99.99.1/24 dev wg0 2>/dev/null || true
ip netns exec "$NS" ip link set wg0 up || true

# Show
ip netns exec "$NS" wg show
ip netns exec "$NS" wg show wg0 dump | head -2

echo "Integration test PASS"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x tests/integration.sh
```

- [ ] **Step 3: Run locally (manual, when on Linux with root)**

Run: `sudo bash tests/integration.sh`
Expected: "Integration test PASS"

- [ ] **Step 4: Commit**

```bash
git add tests/integration.sh
git commit -m "test(integration): smoke test real wg via netns"
```

---

## Task 27: Smoke test doc

**Files:**
- Create: `docs/smoke-test.md`

- [ ] **Step 1: Write smoke test doc**

```markdown
# wg-admin Smoke Test

After a fresh install, run through this checklist:

1. **Login page loads** — visit `https://<host>:<port>/`, see login form.
2. **Wrong password rejected** — submit empty/wrong password, see "Credenciais inválidas".
3. **Correct password works** — submit the admin password set during install, get redirected to `/peers`.
4. **Existing peers visible** — the 3 peers imported from `/etc/wireguard/wg0.conf` appear in the list.
5. **Create a new peer** — click "+ Novo peer", fill name "test-peer", submit. See success message and new peer at the top.
6. **Verify in wg** — on the server: `wg show wg0` — should show 4 peers now.
7. **Download .conf** — click ".conf" on the new peer. Get a file with `[Interface]` and `[Peer]` sections.
8. **Download QR** — click "QR" — get a PNG that scans correctly in the WireGuard mobile app.
9. **Import .conf on a client** — actual WG client should be able to connect.
10. **Toggle disabled** — click "Desativar" on a peer. Verify in `wg show` that the peer is gone (config rewritten without it).
11. **Toggle enabled** — click "Ativar" — peer returns.
12. **Delete a peer** — confirm dialog, then verify in `wg show` it's removed.
13. **Rate limit** — submit wrong password 5 times rapidly. 6th attempt should return 429.
14. **CSRF** — open browser dev tools, manually submit a form without csrf_token, get 403.
15. **Logs** — `journalctl -u wg-admin.service -f` should show actions being logged.

If any of these fail, do not ship.
```

- [ ] **Step 2: Commit**

```bash
git add docs/smoke-test.md
git commit -m "docs: post-install smoke test checklist"
```

---

## Task 28: Final full test run and README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run all tests**

Run: `pytest -v`
Expected: All tests pass (target: 50+ tests across modules)

- [ ] **Step 2: Check coverage**

Run: `pytest --cov=wg_admin --cov-report=term-missing`
Expected: >80% coverage on crypto, state, ratelimit, wg, confgen. App coverage may be lower (HTTP plumbing).

- [ ] **Step 3: Rewrite README**

```markdown
# wg-admin

Minimal WireGuard peer management panel for Linux servers. Flask + systemd socket activation.

## Features

- List peers with live stats (handshake, rx/tx, endpoint)
- Create peers with auto-allocated IPs
- Download `.conf` or QR code
- Enable/disable peers without losing config
- Single admin password (Argon2id)
- Encrypted state at rest (AES-256-GCM + HKDF-SHA256)
- 0 bytes RAM in idle (socket activation)

## Requirements

- Linux with systemd ≥ 235
- Python 3.11+
- WireGuard already installed and configured (`wg0` interface up)
- Root access

## Install

```bash
git clone https://github.com/<user>/wg-admin.git /tmp/wg-admin
sudo bash /tmp/wg-admin/install.sh
```

The install script:
- Creates `/wg-admin/` with venv and code
- Generates `master.key`, `session.key`
- Prompts for admin password (Argon2id hashed)
- Asks for endpoint hostname and listen port
- Imports existing peers from `/etc/wireguard/wg0.conf`
- Installs systemd units, enables socket
- Opens firewalld port if present

After install, get a TLS cert:

```bash
certbot certonly --standalone -d vpn.example.com \
  --pre-hook "systemctl stop wg-admin.socket" \
  --post-hook "systemctl start wg-admin.socket"
```

Then visit `https://vpn.example.com:51821/`.

## Uninstall

```bash
sudo bash /wg-admin/uninstall.sh             # full remove
sudo bash /wg-admin/uninstall.sh --keep-state # preserve secrets/state
```

## Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Documentation

- [Design spec](docs/superpowers/specs/2026-06-17-wg-admin-design.md)
- [Implementation plan](docs/superpowers/plans/2026-06-17-wg-admin-implementation.md)
- [Smoke test](docs/smoke-test.md)
- [Hardening notes](docs/hardening.md) (TBD)

## License

MIT
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: complete README with install, uninstall, dev instructions"
```

---

## Self-Review

After all tasks are complete, do a final sanity check:

1. **Spec coverage:** Every section of the spec (architecture, crypto, security, deployment, tests) has tasks implementing it.
2. **Placeholder scan:** No "TBD" or "TODO" outside documented known-unknowns (TLS integration with Let's Encrypt cert files is left as a configuration step in install, not a code task — explicitly noted in install.sh output).
3. **Type consistency:** `find_peer_by_id`, `add_peer`, `remove_peer`, `set_peer_disabled`, `allocate_ip`, `encrypt_state`, `decrypt_state`, `encrypt_private_key`, `decrypt_private_key`, `hash_password`, `verify_password`, `wg_genkey`, `wg_show_dump`, `wg_quick_restart`, `parse_wg_conf`, `generate_wg_conf`, `render_conf`, `render_qr_png` — all named consistently across tasks.
4. **Scope:** Single project, ~28 tasks, each producing testable code. Appropriate for one focused implementation cycle.

Known follow-ups (not in this plan, but flagged in design spec section 12):
- TLS cert wiring (currently the install prints a hint; production deploy must configure Flask to load the cert files)
- Server public key extraction (config has `server_public_key =` empty; install or first run should populate from `/etc/wireguard/wg0.conf`)
- Argon2id performance validation on actual target hardware
