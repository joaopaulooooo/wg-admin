# tests/test_i18n.py — language catalogs, defaults and the settings switch
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ── unit ────────────────────────────────────────────────────────────────────

def test_normalize_language():
    from wg_admin import i18n
    assert i18n.normalize_language("en") == "en"
    assert i18n.normalize_language("pt") == "pt"
    assert i18n.normalize_language("PT") == "pt"
    assert i18n.normalize_language("  pt ") == "pt"
    assert i18n.normalize_language("fr") == "en"
    assert i18n.normalize_language("") == "en"
    assert i18n.normalize_language(None) == "en"


def test_translate_english_is_passthrough():
    from wg_admin import i18n
    assert i18n.translate("en", "Peers") == "Peers"
    assert i18n.translate("en", "Unknown string") == "Unknown string"


def test_translate_portuguese():
    from wg_admin import i18n
    assert i18n.translate("pt", "Peers") == "Peers"
    assert i18n.translate("pt", "Settings") == "Configurações"
    assert i18n.translate("pt", "Sign out") == "Sair"


def test_translate_missing_key_falls_back_to_english():
    from wg_admin import i18n
    assert i18n.translate("pt", "no such key") == "no such key"


def test_translate_placeholders():
    from wg_admin import i18n
    assert i18n.translate("en", "Peer created: {name} ({ip})",
                          name="x", ip="10.0.0.2") == "Peer created: x (10.0.0.2)"
    assert i18n.translate("pt", "Delete {name}? All active peers disconnect for ~1s.",
                          name="phone") == ("Apagar phone? "
                          "Todos os peers ativos vão desconectar por ~1s.")
    # without kwargs the placeholder is preserved (used by the JS side)
    assert "{tag}" in i18n.translate("pt",
        "Update to {tag}? The panel downloads the new version and restarts itself "
        "(usually under a minute). WireGuard peers are NOT affected and your session "
        "is kept. Progress: journalctl -u wg-admin-selfupdate -f")


def test_default_language_is_english():
    from wg_admin import i18n, config
    assert i18n.DEFAULT_LANGUAGE == "en"
    cfg = config.load_config(None)
    assert cfg["server"]["language"] == "en"


# ── catalog completeness: every t('...') used in code has a pt entry ────────

def _extract_keys():
    keys = set()
    pattern = re.compile(r"\bt\(\s*'([^']+)'")
    pattern_dq = re.compile(r'\bt\(\s*"([^"]+)"')
    for f in (REPO / "templates").glob("*.html"):
        text = f.read_text(encoding="utf-8")
        keys.update(pattern.findall(text))
        keys.update(pattern_dq.findall(text))
    app_src = (REPO / "src" / "wg_admin" / "app.py").read_text(encoding="utf-8")
    keys.update(pattern.findall(app_src))
    keys.update(pattern_dq.findall(app_src))
    return keys


def test_every_t_key_has_portuguese_translation():
    from wg_admin import i18n
    keys = _extract_keys()
    assert keys, "no t() keys found — extraction is broken"
    missing = sorted(k for k in keys if k not in i18n.CATALOGS["pt"])
    assert not missing, f"keys without pt translation: {missing}"


def test_catalog_values_have_no_stray_placeholders():
    from wg_admin import i18n
    fmt = re.compile(r"\{(\w+)\}")
    for key, value in i18n.CATALOGS["pt"].items():
        assert fmt.findall(key) == fmt.findall(value), f"placeholder mismatch: {key!r}"


# ── app rendering ───────────────────────────────────────────────────────────

def _make_app(tmp_path, monkeypatch, language=None):
    from wg_admin import app as app_mod, crypto
    sec = tmp_path / "secrets"
    sec.mkdir(exist_ok=True)
    (sec / "master.key").write_bytes(os.urandom(32))
    (sec / "session.key").write_bytes(os.urandom(32))
    (sec / "auth.ini").write_text("password_hash = " + crypto.hash_password("pw") + "\n")
    cfg_path = tmp_path / "config.ini"
    if language:
        cfg_path.write_text(f"[server]\nlanguage = {language}\n")
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


def test_default_english_when_no_config(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    html = app.test_client().get("/login").get_data(as_text=True)
    assert "Sign in →" in html
    assert "Entrar" not in html


def test_portuguese_when_configured(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, language="pt")
    html = app.test_client().get("/login").get_data(as_text=True)
    assert "Entrar →" in html
    assert 'lang="pt"' in html


def test_unknown_language_falls_back_to_english(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, language="klingon")
    html = app.test_client().get("/login").get_data(as_text=True)
    assert "Sign in →" in html


# ── settings switch ─────────────────────────────────────────────────────────

def test_settings_language_requires_login(client):
    r = client.post("/settings/language", data={"language": "en"})
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_settings_language_requires_csrf(client):
    client.post("/login", data={"password": "test-password"})
    r = client.post("/settings/language", data={"language": "en"})
    assert r.status_code == 403


def test_settings_language_rejects_unknown(client, workdir):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/settings/language",
                    data={"language": "fr", "csrf_token": token})
    assert r.status_code in (301, 302)
    assert "pt" in workdir["config_path"].read_text()  # unchanged


def test_settings_language_saves_and_schedules_restart(client, workdir):
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/settings/language",
                    data={"language": "en", "csrf_token": token})
    assert r.status_code in (301, 302)
    assert "language = en" in workdir["config_path"].read_text()


def test_settings_page_shows_language_card(client):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"Language" in r.data
    assert b"Portugu" in r.data
    assert b'value="pt" selected' in r.data
