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
BLOCK_SEC = 1800         # 30 minutes
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
