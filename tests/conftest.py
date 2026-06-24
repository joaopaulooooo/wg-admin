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

    (secrets_dir / "master.key").write_bytes(os.urandom(32))
    (secrets_dir / "session.key").write_bytes(os.urandom(32))

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
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as c:
        yield c
