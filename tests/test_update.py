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
