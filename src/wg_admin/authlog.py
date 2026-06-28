"""Rotating auth attempt log. JSON-lines, rotates at MAX_BYTES."""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MAX_BYTES = 100 * 1024   # 100 KB per file
BACKUP_COUNT = 5         # auth.log.1 .. auth.log.5


@contextmanager
def _flock(path: Path) -> Iterator[None]:
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _rotate(path: Path) -> None:
    """Shift backups aside and start a fresh file (unconditional)."""
    oldest = path.with_suffix(path.suffix + f".{BACKUP_COUNT}")
    if oldest.exists():
        oldest.unlink()
    for i in range(BACKUP_COUNT - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        dst = path.with_suffix(path.suffix + f".{i + 1}")
        if src.exists():
            src.replace(dst)
    if path.exists():
        path.replace(path.with_suffix(path.suffix + ".1"))


def log_attempt(
    path: Path,
    *,
    success: bool,
    client_ip: str,
    user_agent: str = "",
) -> None:
    """Append an auth attempt as a JSON line. Rotates if the new line
    would push the file past MAX_BYTES, so the current file never exceeds it."""
    with _flock(path):
        entry = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "success": bool(success),
            "ip": client_ip,
            "user_agent": user_agent,
        }
        line = json.dumps(entry) + "\n"
        line_size = len(line.encode("utf-8"))
        current_size = path.stat().st_size if path.exists() else 0
        if current_size + line_size > MAX_BYTES and current_size > 0:
            _rotate(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
