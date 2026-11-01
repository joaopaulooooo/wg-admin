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
    authlog_path = secrets_dir / "auth.log"

    (secrets_dir / "master.key").write_bytes(os.urandom(32))
    (secrets_dir / "session.key").write_bytes(os.urandom(32))
    # The suite was written against the Portuguese UI — pin it explicitly
    # (the app default for fresh installs is English since v0.3.2).
    config_path.write_text("[server]\nlanguage = pt\n")

    from wg_admin import crypto
    phc = crypto.hash_password("test-password")
    (secrets_dir / "auth.ini").write_text(f"password_hash = {phc}\n")

    monkeypatch.setattr("wg_admin.app.SECRETS_DIR", secrets_dir)
    monkeypatch.setattr("wg_admin.app.STATE_PATH", state_path)
    monkeypatch.setattr("wg_admin.app.CONFIG_PATH", config_path)
    monkeypatch.setattr("wg_admin.app.RATELIMIT_PATH", ratelimit_path)
    monkeypatch.setattr("wg_admin.app.AUTHLOG_PATH", authlog_path)

    return {
        "tmp_path": tmp_path,
        "secrets_dir": secrets_dir,
        "state_path": state_path,
        "config_path": config_path,
        "ratelimit_path": ratelimit_path,
        "authlog_path": authlog_path,
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


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Factory: isolated app on a private tmp dir (no workdir side effects).

    make_app(language="pt") pre-writes config.ini; without it the config is
    absent (English defaults).
    """
    def _make(language=None, extra_config=""):
        import os as _os
        from wg_admin import app as app_mod, crypto
        sec = tmp_path / "secrets"
        sec.mkdir(exist_ok=True)
        (sec / "master.key").write_bytes(_os.urandom(32))
        (sec / "session.key").write_bytes(_os.urandom(32))
        (sec / "auth.ini").write_text(
            "password_hash = " + crypto.hash_password("pw") + "\n")
        cfg_path = tmp_path / "config.ini"
        parts = []
        if language:
            parts.append(f"[server]\nlanguage = {language}\n")
        if extra_config:
            parts.append(extra_config)
        if parts:
            cfg_path.write_text("\n".join(parts))
        monkeypatch.setattr(app_mod, "SECRETS_DIR", sec)
        monkeypatch.setattr(app_mod, "STATE_PATH", tmp_path / "state.enc")
        monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
        monkeypatch.setattr(app_mod, "RATELIMIT_PATH", sec / "rl.json")
        monkeypatch.setattr(app_mod, "AUTHLOG_PATH", sec / "auth.log")
        monkeypatch.setattr(app_mod, "BANDWIDTH_PATH", tmp_path / "bw.json")
        monkeypatch.setattr(app_mod, "WG_CONF_DIR", tmp_path)
        app = app_mod.create_app()
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        return app

    return _make
