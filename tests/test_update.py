# tests/test_update.py — self-update check + trigger
import pytest


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Every test starts with an empty update-check cache."""
    from wg_admin import app as app_mod
    monkeypatch.setattr(app_mod, "_update_check_cache", {"ts": 0.0, "payload": None})


def _login(client):
    client.post("/login", data={"password": "test-password"})


def _csrf(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


# ─── version parsing ────────────────────────────────────────────────────────

def test_version_tuple_parses_and_rejects():
    from wg_admin.app import _version_tuple
    assert _version_tuple("v0.3.1") == (0, 3, 1)
    assert _version_tuple("v12.34.56") == (12, 34, 56)
    assert _version_tuple("0.3.1") is None      # missing v prefix
    assert _version_tuple("main") is None       # branch, not a tag
    assert _version_tuple("v1;rm -rf /") is None
    assert _version_tuple("") is None
    assert _version_tuple(None) is None


# ─── GET /api/update/check ──────────────────────────────────────────────────

def test_update_check_requires_login(client):
    r = client.get("/api/update/check")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_update_check_reports_newer_version(client, monkeypatch):
    monkeypatch.setattr("wg_admin.app._fetch_latest_tag", lambda: "v99.0.0")
    _login(client)
    d = client.get("/api/update/check").get_json()
    assert d["update_available"] is True
    assert d["latest"] == "v99.0.0"
    assert d["error"] is None


def test_update_check_same_version_not_available(client, monkeypatch):
    from wg_admin import app as app_mod
    monkeypatch.setattr("wg_admin.app._fetch_latest_tag",
                        lambda: "v" + app_mod.__version__)
    _login(client)
    d = client.get("/api/update/check").get_json()
    assert d["update_available"] is False


def test_update_check_offline_degrades_gracefully(client, monkeypatch):
    def boom():
        raise OSError("no route to host")
    monkeypatch.setattr("wg_admin.app._fetch_latest_tag", boom)
    _login(client)
    d = client.get("/api/update/check").get_json()
    assert d["update_available"] is False
    assert d["error"] == "lookup_failed"
    assert d["latest"] is None


def test_update_check_cached_within_ttl(client, monkeypatch):
    calls = []
    def fake_fetch():
        calls.append(1)
        return "v99.0.0"
    monkeypatch.setattr("wg_admin.app._fetch_latest_tag", fake_fetch)
    _login(client)
    client.get("/api/update/check")
    client.get("/api/update/check")
    client.get("/api/update/check")
    assert len(calls) == 1


# ─── POST /settings/update ──────────────────────────────────────────────────

def test_settings_update_requires_csrf(client):
    _login(client)
    r = client.post("/settings/update", data={"tag": "v99.0.0"})
    assert r.status_code == 403


@pytest.mark.parametrize("bad", ["main", "v9.9", "v1;rm -rf /",
                                 "../../etc", "v99.0.0\nPostUp = evil"])
def test_settings_update_rejects_bad_tags(client, monkeypatch, bad):
    _login(client)
    def must_not_schedule(app, tag):
        raise AssertionError("scheduler must not run for tag %r" % tag)
    monkeypatch.setattr("wg_admin.app._schedule_update", must_not_schedule)
    r = client.post("/settings/update",
                    data={"tag": bad, "csrf_token": _csrf(client)})
    assert r.status_code in (301, 302)  # flash + redirect, never scheduled


def test_settings_update_rejects_older_or_equal_version(client, monkeypatch):
    from wg_admin import app as app_mod
    _login(client)
    monkeypatch.setattr("wg_admin.wg.unit_is_active", lambda unit: False)
    scheduled = []
    monkeypatch.setattr("wg_admin.app._schedule_update",
                        lambda app, tag: scheduled.append(tag))
    for tag in ("v0.0.1", "v" + app_mod.__version__):
        r = client.post("/settings/update",
                        data={"tag": tag, "csrf_token": _csrf(client)})
        assert r.status_code in (301, 302)
    assert scheduled == []


def test_settings_update_success_schedules_transient_unit(client, monkeypatch):
    _login(client)
    monkeypatch.setattr("wg_admin.wg.unit_is_active", lambda unit: False)
    scheduled = []
    monkeypatch.setattr("wg_admin.app._schedule_update",
                        lambda app, tag: scheduled.append(tag) or True)
    r = client.post("/settings/update",
                    data={"tag": "v99.0.0", "csrf_token": _csrf(client)})
    assert r.status_code in (301, 302)
    assert scheduled == ["v99.0.0"]


def test_settings_update_conflicts_when_one_is_running(client, monkeypatch):
    _login(client)
    monkeypatch.setattr("wg_admin.wg.unit_is_active", lambda unit: True)
    scheduled = []
    monkeypatch.setattr("wg_admin.app._schedule_update",
                        lambda app, tag: scheduled.append(tag) or True)
    r = client.post("/settings/update",
                    data={"tag": "v99.0.0", "csrf_token": _csrf(client)})
    assert r.status_code in (301, 302)
    assert scheduled == []


# ─── UI wiring ──────────────────────────────────────────────────────────────

def test_sidebar_renders_version_hook(client):
    from wg_admin.version import __version__
    _login(client)
    r = client.get("/settings")
    assert r.status_code == 200
    assert b'id="wg-version"' in r.data
    assert b'data-csrf' in r.data
    assert __version__.encode() in r.data


# ─── v0.3.3 coverage push: real fetch parsing + scheduling internals ────────

import json as _json
from types import SimpleNamespace


class _FakeResp:
    def __init__(self, data):
        self._data = data
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_fetch_latest_tag_picks_max_semver(monkeypatch):
    from wg_admin import app as app_mod
    payload = _json.dumps([
        {"name": "v0.3.0"}, {"name": "v10.0.0"}, {"name": "v0.3.2"},
        {"name": "not-a-tag"}, {"name": "v0.3.2-rc1"},
    ]).encode()
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(payload))
    assert app_mod._fetch_latest_tag() == "v10.0.0"


def test_fetch_latest_tag_no_semver_tags(monkeypatch):
    from wg_admin import app as app_mod
    payload = _json.dumps([{"name": "main"}, {"name": "latest"}]).encode()
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(payload))
    assert app_mod._fetch_latest_tag() is None


def test_fetch_latest_tag_non_list_payload(monkeypatch):
    from wg_admin import app as app_mod
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(b'{"error": "x"}'))
    assert app_mod._fetch_latest_tag() is None


def test_schedule_update_uses_systemd_run(client, monkeypatch):
    from wg_admin import app as app_mod
    calls = []
    monkeypatch.setattr(
        "wg_admin.app.subprocess.run",
        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stderr=""))
    app = client.application
    app.config["TESTING"] = False
    try:
        assert app_mod._schedule_update(app, "v9.9.9") is True
    finally:
        app.config["TESTING"] = True
    assert calls and calls[0][0] == "systemd-run"
    assert "v9.9.9" in calls[0][-1]  # the tag is in the script payload


def test_schedule_update_systemd_run_failure_falls_back(client, monkeypatch):
    from wg_admin import app as app_mod
    monkeypatch.setattr(
        "wg_admin.app.subprocess.run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stderr="unit exists"))
    pops = []
    monkeypatch.setattr("wg_admin.app.subprocess.Popen", lambda cmd, **kw: pops.append(cmd))
    app = client.application
    app.config["TESTING"] = False
    try:
        assert app_mod._schedule_update(app, "v9.9.9") is True
    finally:
        app.config["TESTING"] = True
    assert pops and pops[0][0] == "sh"


def test_schedule_update_systemd_run_exception_falls_back(client, monkeypatch):
    from wg_admin import app as app_mod
    monkeypatch.setattr(
        "wg_admin.app.subprocess.run",
        lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError("no systemd-run")))
    pops = []
    monkeypatch.setattr("wg_admin.app.subprocess.Popen", lambda cmd, **kw: pops.append(cmd))
    app = client.application
    app.config["TESTING"] = False
    try:
        assert app_mod._schedule_update(app, "v9.9.9") is True
    finally:
        app.config["TESTING"] = True
    assert pops


def test_schedule_shell_runs_detached_command(client, monkeypatch):
    from wg_admin import app as app_mod
    pops = []
    monkeypatch.setattr("wg_admin.app.subprocess.Popen", lambda cmd, **kw: pops.append(cmd))
    app = client.application
    app.config["TESTING"] = False
    try:
        app_mod._schedule_shell(app, "true")
    finally:
        app.config["TESTING"] = True
    assert pops[0] == ["sh", "-c", "true"]


def test_schedule_shell_swallows_spawn_errors(client, monkeypatch):
    from wg_admin import app as app_mod
    monkeypatch.setattr("wg_admin.app.subprocess.Popen",
                        lambda cmd, **kw: (_ for _ in ()).throw(OSError("no fds")))
    app = client.application
    app.config["TESTING"] = False
    try:
        app_mod._schedule_shell(app, "true")  # must not raise
    finally:
        app.config["TESTING"] = True
