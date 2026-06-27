# Quotas, Graphs, syncconf, WhatsApp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 features to wg-admin — syncconf hot-reload, bandwidth quotas (per-peer + global), live bandwidth graphs, and WhatsApp peer sharing — plus a VPN kill-switch bonus.

**Architecture:** New `quota.py` module; `_apply_state_to_wg` moves from `app.py` to `wg.py` (with `mode` parameter) to avoid circular imports when the bandwidth timer calls it; bandwidth timer gains quota check after each sample; Flask routes added for `/api/bandwidth/*` and `/vpn/toggle`; Chart.js loaded from local vendor file; WhatsApp uses Web Share API on mobile with download+chat fallback on desktop.

**Tech Stack:** Python 3.11+, Flask, pytest (with monkeypatch), Jinja2, Chart.js v4 (local), vanilla JS, Web Share API Level 2.

**Spec:** `docs/superpowers/specs/2026-06-27-quota-graphs-syncconf-whatsapp-design.md`

---

## Working directory and conventions

- **Project root:** `/home/jp/projetos/wg-admin/`. All paths below are relative to it unless stated.
- **Run commands from** `/home/jp/projetos/wg-admin/`.
- **Venv:** `source venv/bin/activate` before running pytest.
- **Test discipline:** Write failing test → run to confirm fail → implement → run to confirm pass → commit.
- **Commits:** Conventional style (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). End every commit message body with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- **Test command:** `pytest tests/<file>.py -v` (single file) or `pytest -v` (all).
- **Coverage check:** `pytest --cov=wg_admin --cov-report=term-missing`. Target ≥90%.

---

## File map

**Created:**
- `src/wg_admin/quota.py` — quota check functions
- `tests/test_quota.py` — quota unit tests
- `static/vendor/chartjs.min.js` — Chart.js v4 local (~70KB, downloaded once)
- `static/js/bandwidth-modal.js` — modal logic
- `static/js/whatsapp-modal.js` — modal logic
- `static/js/global-chart.js` — top-of-page chart

**Modified:**
- `src/wg_admin/wg.py` — `wg_syncconf`, `wg_interface_active`, `apply_state_to_wg` (moved from app.py)
- `src/wg_admin/state.py` — `migrate_state`
- `src/wg_admin/config.py` — add `[quota]` defaults
- `src/wg_admin/bandwidth.py` — call `quota.check_quotas` after sample
- `src/wg_admin/app.py` — new routes, context processor, use `wg.apply_state_to_wg`
- `templates/peers.html` — sparkline, quota bar, WhatsApp button, modal markup
- `templates/peer_form.html` — `quota_gb` input
- `templates/peer_edit.html` — `quota_gb` input
- `templates/base.html` — kill switch, global quota display
- `tests/test_app.py` — update monkeypatch path (was `_apply_state_to_wg`, now `wg.apply_state_to_wg`)
- `tests/test_wg.py` — new tests for `wg_syncconf`, `wg_interface_active`, `apply_state_to_wg`
- `tests/test_state.py` — new test for `migrate_state`
- `tests/test_config.py` — new test for `[quota]` defaults
- `tests/test_bandwidth.py` — new test for quota integration
- `docs/smoke-test.md` — new manual checks
- `CHANGELOG.md` — entries under `[Unreleased]`
- `README.md` — note syncconf limitation removal + new features

---

### Task 1: Refactor — move `_apply_state_to_wg` from `app.py` to `wg.py`

**Why first:** Every other feature depends on this. The bandwidth timer needs to call it, and that requires it to live in `wg.py` (not `app.py`) to avoid circular imports. Also adds `mode` parameter for syncconf.

**Files:**
- Modify: `src/wg_admin/wg.py`
- Modify: `src/wg_admin/app.py:24-50` (delete module-level func, call `wg.apply_state_to_wg`)
- Modify: `tests/test_app.py:138,154` (update monkeypatch path)

- [ ] **Step 1: Write the failing test in `tests/test_wg.py`**

Append:

```python
def test_apply_state_to_wg_syncconf_success(tmp_path, monkeypatch):
    """apply_state_to_wg with mode=syncconf: calls syncconf, skips restart."""
    from wg_admin import wg

    interface_path = tmp_path / "wg0.conf"
    interface_path.write_text("[Interface]\nAddress = 10.0.0.1/24\nListenPort = 51820\nPrivateKey = X\n")

    calls = {"syncconf": False, "restart": False}
    def fake_syncconf(iface):
        calls["syncconf"] = True
        return True
    def fake_restart(iface):
        calls["restart"] = True

    monkeypatch.setattr(wg, "wg_syncconf", fake_syncconf)
    monkeypatch.setattr(wg, "wg_quick_restart", fake_restart)
    # The function reads /etc/wireguard/<iface>.conf — patch Path.exists to False
    # so it falls into the "fresh interface" branch (no file read).
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)

    cfg = type("C", (), {"__getitem__": lambda self, k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1", "subnet": "10.0.0.0/24"}}[k]})()
    s = {"peers": [{"public_key": "PUB", "ip": "10.0.0.2", "name": "x", "disabled": False}]}
    wg.apply_state_to_wg(s, cfg, mode="syncconf")
    assert calls["syncconf"] is True
    assert calls["restart"] is False


def test_apply_state_to_wg_syncconf_failure_falls_back_to_restart(monkeypatch, tmp_path):
    """If syncconf returns False, falls back to restart."""
    from wg_admin import wg

    calls = {"syncconf": False, "restart": False}
    monkeypatch.setattr(wg, "wg_syncconf", lambda iface: (calls.__setitem__("syncconf", True), True)[1] and False)
    monkeypatch.setattr(wg, "wg_quick_restart", lambda iface: calls.__setitem__("restart", True))
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)

    cfg = type("C", (), {"__getitem__": lambda self, k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1", "subnet": "10.0.0.0/24"}}[k]})()
    s = {"peers": []}
    wg.apply_state_to_wg(s, cfg, mode="syncconf")
    assert calls["syncconf"] is True
    assert calls["restart"] is True


def test_apply_state_to_wg_restart_mode_skips_syncconf(monkeypatch):
    """mode=restart never calls syncconf."""
    from wg_admin import wg

    calls = {"syncconf": False, "restart": False}
    monkeypatch.setattr(wg, "wg_syncconf", lambda iface: calls.__setitem__("syncconf", True))
    monkeypatch.setattr(wg, "wg_quick_restart", lambda iface: calls.__setitem__("restart", True))
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)

    cfg = type("C", (), {"__getitem__": lambda self, k: {"wg": {"interface": "wg0", "server_ip": "10.0.0.1", "subnet": "10.0.0.0/24"}}[k]})()
    s = {"peers": []}
    wg.apply_state_to_wg(s, cfg, mode="restart")
    assert calls["syncconf"] is False
    assert calls["restart"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_wg.py::test_apply_state_to_wg_syncconf_success tests/test_wg.py::test_apply_state_to_wg_syncconf_failure_falls_back_to_restart tests/test_wg.py::test_apply_state_to_wg_restart_mode_skips_syncconf -v
```

Expected: FAIL — `wg.apply_state_to_wg` does not exist (AttributeError).

- [ ] **Step 3: Implement `wg.apply_state_to_wg` in `src/wg_admin/wg.py`**

Append at end of file:

```python
def apply_state_to_wg(s: dict, cfg, mode: str = "syncconf") -> None:
    """Regenerate /etc/wireguard/<interface>.conf from state and apply.

    mode="syncconf": try wg syncconf (zero downtime), fall back to restart.
    mode="restart": wg-quick restart directly (needed to clean PostUp/iptables).
    """
    import os as _os
    from pathlib import Path
    interface = cfg["wg"]["interface"]
    interface_path = Path(f"/etc/wireguard/{interface}.conf")
    if interface_path.exists():
        existing = parse_wg_conf(interface_path.read_text())
        wg_interface = existing["interface"]
    else:
        wg_interface = {
            "Address": f"{cfg['wg']['server_ip']}/{cfg['wg']['subnet'].split('/')[-1]}",
            "ListenPort": "51820",
        }
    wg_peers = [
        {
            "PublicKey": p["public_key"],
            "AllowedIPs": f"{p['ip']}/32",
            "disabled": p.get("disabled", False) or p.get("quota_suspended", False),
            "name": p["name"],
        }
        for p in s["peers"]
    ]
    conf_text = generate_wg_conf(wg_interface, wg_peers)
    tmp = interface_path.with_suffix(".conf.tmp")
    tmp.write_text(conf_text)
    _os.replace(tmp, interface_path)

    if mode == "syncconf":
        if wg_syncconf(interface):
            return
    wg_quick_restart(interface)
```

- [ ] **Step 4: Delete the old `_apply_state_to_wg` from `src/wg_admin/app.py`**

Delete lines 24-50 (the function `_apply_state_to_wg`). Replace the 3 call sites (lines 257, 275, 293) — each currently `_apply_state_to_wg(s, cfg)` — with `wg.apply_state_to_wg(s, cfg, mode="restart")`.

Why "restart" mode for these: delete/toggle need to clean PostUp/iptables rules.

- [ ] **Step 5: Update existing test monkeypatches in `tests/test_app.py`**

Two occurrences at lines 138 and 154. Change:

```python
monkeypatch.setattr("wg_admin.app._apply_state_to_wg", lambda s, cfg: None)
```

to:

```python
monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)
```

- [ ] **Step 6: Run all tests to verify nothing broke**

```bash
pytest -v
```

Expected: all 116 + 3 new tests pass. If any existing tests still reference `_apply_state_to_wg`, grep for it:

```bash
grep -rn "_apply_state_to_wg" src/ tests/
```

Should return zero matches.

- [ ] **Step 7: Commit**

```bash
git add src/wg_admin/wg.py src/wg_admin/app.py tests/test_wg.py tests/test_app.py
git commit -m "$(cat <<'EOF'
refactor(wg): move apply_state_to_wg from app to wg module

Avoids circular import when bandwidth timer needs to call it for quota
enforcement. Adds mode parameter: "syncconf" (zero-downtime) vs "restart"
(needed for delete/toggle to clean iptables/PostUp).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `wg_syncconf` to `wg.py`

**Why:** Foundation for syncconf hot-reload feature.

**Files:**
- Modify: `src/wg_admin/wg.py`
- Modify: `tests/test_wg.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wg.py`:

```python
def test_wg_syncconf_success(monkeypatch):
    """wg_syncconf: makes two subprocess calls, returns True on success."""
    from wg_admin import wg
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class R:
            stdout = "stripped config"
            returncode = 0
        return R()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", fake_run)
    result = wg.wg_syncconf("wg0")
    assert result is True
    assert calls[0] == ["wg-quick", "strip", "wg0"]
    assert calls[1] == ["wg", "syncconf", "wg0", "/dev/stdin"]


def test_wg_syncconf_strip_failure_returns_false(monkeypatch):
    from wg_admin import wg
    import subprocess
    def fake_run(cmd, **kwargs):
        if cmd[1] == "strip":
            raise subprocess.CalledProcessError(1, cmd)
        class R:
            stdout = ""
            returncode = 0
        return R()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", fake_run)
    assert wg.wg_syncconf("wg0") is False


def test_wg_syncconf_syncconf_failure_returns_false(monkeypatch):
    from wg_admin import wg
    import subprocess
    def fake_run(cmd, **kwargs):
        if cmd[1] == "syncconf":
            raise subprocess.CalledProcessError(1, cmd)
        class R:
            stdout = "stripped"
            returncode = 0
        return R()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", fake_run)
    assert wg.wg_syncconf("wg0") is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_wg.py::test_wg_syncconf_success tests/test_wg.py::test_wg_syncconf_strip_failure_returns_false tests/test_wg.py::test_wg_syncconf_syncconf_failure_returns_false -v
```

Expected: FAIL — `wg_syncconf` doesn't exist.

- [ ] **Step 3: Implement in `src/wg_admin/wg.py`**

Add near other wg wrappers (before `apply_state_to_wg`):

```python
def wg_syncconf(interface: str = "wg0") -> bool:
    """Apply config changes to a running interface without restart.

    Strips PostUp/PostDown/Address via `wg-quick strip` then syncs via netlink.
    Returns True on success, False on any failure (caller should fall back to
    wg-quick restart).
    """
    try:
        strip = subprocess.run(
            ["wg-quick", "strip", interface],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["wg", "syncconf", interface, "/dev/stdin"],
            input=strip.stdout, capture_output=True, text=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_wg.py -v
```

Expected: PASS all tests including the 3 from Task 1 (since `apply_state_to_wg` calls `wg_syncconf`).

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/wg.py tests/test_wg.py
git commit -m "$(cat <<'EOF'
feat(wg): add wg_syncconf for zero-downtime config reload

Uses `wg-quick strip` + `wg syncconf /dev/stdin` to apply peer
additions/removals without disconnecting active peers. Returns False
on any failure; caller falls back to wg-quick restart.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Use `mode="syncconf"` in `peer_new`

**Why:** Wire up Feature 1 — peer creation should use zero-downtime syncconf.

**Files:**
- Modify: `src/wg_admin/app.py` (peer_new route)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_peer_new_uses_syncconf_mode(client, workdir, monkeypatch):
    """Creating a peer calls apply_state_to_wg with mode=syncconf."""
    client.post("/login", data={"password": "test-password"})

    # Stub wg_genkey so we don't need wg binary
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))

    # Capture the mode parameter when apply_state_to_wg is called
    captured = {}
    def fake_apply(s, cfg, mode="syncconf"):
        captured["mode"] = mode
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", fake_apply)

    r = client.post("/peers/new", data={"name": "Test", "csrf_token": client.session.transaction...})
    # actually we need csrf_token from session — see fix below
```

Actually CSRF token comes from session. Update test:

```python
def test_peer_new_uses_syncconf_mode(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))

    captured = {}
    def fake_apply(s, cfg, mode="syncconf"):
        captured["mode"] = mode
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", fake_apply)

    # Get csrf token from session via client session_transaction
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    r = client.post("/peers/new", data={"name": "Test", "csrf_token": token})
    assert r.status_code in (301, 302)
    assert captured.get("mode") == "syncconf"


def test_peer_delete_uses_restart_mode(client, workdir, monkeypatch):
    """Deleting a peer calls apply_state_to_wg with mode=restart."""
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    captured = {}
    def fake_apply(s, cfg, mode="syncconf"):
        captured["mode"] = mode
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", fake_apply)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/abc12345/delete", data={"csrf_token": token})
    assert r.status_code in (301, 302)
    assert captured.get("mode") == "restart"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_peer_new_uses_syncconf_mode tests/test_app.py::test_peer_delete_uses_restart_mode -v
```

Expected: FAIL on first test (peer_new still calls with no mode → defaults to "syncconf" but test asserts explicitly).

Actually — since `apply_state_to_wg` defaults to `mode="syncconf"`, the test might pass even without changes. Update test to also stub the apply to NOT default, then check. Or update `peer_new` explicitly to pass `mode="syncconf"` even though it's default — this makes intent explicit.

For `peer_delete`, the current code calls `wg.apply_state_to_wg(s, cfg)` (mode defaults to syncconf). The test expects `restart`. So test will fail until we update peer_delete call.

- [ ] **Step 3: Update call sites in `src/wg_admin/app.py`**

In `peer_new` (currently `wg.apply_state_to_wg(s, cfg)` after Task 1) — make explicit:

```python
wg.apply_state_to_wg(s, cfg, mode="syncconf")
```

In `peer_delete` and `peer_toggle`:

```python
wg.apply_state_to_wg(s, cfg, mode="restart")
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(app): use syncconf for peer create, restart for delete/toggle

Peer creation is purely additive — syncconf handles it without dropping
active tunnels. Delete/toggle need wg-quick restart to clean up
PostUp/iptables rules.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Schema migration — add quota fields to peers

**Why:** Foundation for quota feature. Old states need migration.

**Files:**
- Modify: `src/wg_admin/state.py`
- Modify: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_state.py`:

```python
def test_migrate_state_adds_quota_fields_to_old_peer():
    """Peers created before quotas existed should get default fields."""
    from wg_admin.state import migrate_state
    state = {
        "version": 1,
        "peers": [{
            "id": "abc", "name": "x", "public_key": "PUB",
            "private_key_enc": "ENC", "ip": "10.0.0.2",
            "disabled": False, "created_at": "2026-06-17T00:00:00Z",
        }],
    }
    migrate_state(state)
    p = state["peers"][0]
    assert p["quota_gb"] == 0.0
    assert p["quota_suspended"] is False
    assert p["quota_state_updated_at"] is None


def test_migrate_state_preserves_existing_quota_fields():
    from wg_admin.state import migrate_state
    state = {"version": 1, "peers": [{
        "id": "abc", "name": "x", "public_key": "PUB",
        "private_key_enc": "ENC", "ip": "10.0.0.2",
        "disabled": False, "created_at": "...",
        "quota_gb": 5.5, "quota_suspended": True,
        "quota_state_updated_at": "2026-06-27T00:00:00Z",
    }]}
    migrate_state(state)
    p = state["peers"][0]
    assert p["quota_gb"] == 5.5
    assert p["quota_suspended"] is True
    assert p["quota_state_updated_at"] == "2026-06-27T00:00:00Z"


def test_migrate_state_handles_empty_peers():
    from wg_admin.state import migrate_state
    state = {"version": 1, "peers": []}
    migrate_state(state)  # should not raise
    assert state["peers"] == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_state.py::test_migrate_state_adds_quota_fields_to_old_peer tests/test_state.py::test_migrate_state_preserves_existing_quota_fields tests/test_state.py::test_migrate_state_handles_empty_peers -v
```

Expected: FAIL — `migrate_state` doesn't exist.

- [ ] **Step 3: Implement in `src/wg_admin/state.py`**

Add after `find_peer_by_id`:

```python
def migrate_state(state_data: dict) -> None:
    """Add new fields to peers from older state versions (in-place).

    Idempotent: peers that already have the fields are untouched.
    Currently adds: quota_gb, quota_suspended, quota_state_updated_at.
    """
    for peer in state_data.get("peers", []):
        peer.setdefault("quota_gb", 0.0)
        peer.setdefault("quota_suspended", False)
        peer.setdefault("quota_state_updated_at", None)
```

Then in `load_state`, after `json.loads(plaintext)`, call migration:

```python
def load_state(state_path: Path, master_key: bytes) -> dict:
    if not state_path.exists():
        return empty_state()
    envelope = json.loads(_atomic_read_bytes(state_path))
    plaintext = crypto.decrypt_state(envelope, master_key)
    state_data = json.loads(plaintext)
    migrate_state(state_data)
    return state_data
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_state.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/state.py tests/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): migrate peers to add quota fields

New fields: quota_gb (float, 0=unlimited), quota_suspended (bool),
quota_state_updated_at (ISO or None). Migration is idempotent and
runs on every load_state.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Config defaults — add `[quota]` section

**Why:** `global_quota_gb` needs a default so old `config.ini` files don't KeyError.

**Files:**
- Modify: `src/wg_admin/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_load_has_quota_section_default(tmp_path):
    cfg = config.load_config(tmp_path / "nonexistent.ini")
    assert cfg["quota"]["global_quota_gb"] == "0"
    assert cfg["quota"].getfloat("global_quota_gb") == 0.0


def test_load_quota_section_overridden_by_file(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[quota]\nglobal_quota_gb = 250\n")
    cfg = config.load_config(p)
    assert cfg["quota"].getfloat("global_quota_gb") == 250.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_config.py::test_load_has_quota_section_default tests/test_config.py::test_load_quota_section_overridden_by_file -v
```

Expected: FAIL — `KeyError: 'quota'`.

- [ ] **Step 3: Add quota to DEFAULTS in `src/wg_admin/config.py`**

```python
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
    "quota": {
        "global_quota_gb": "0",  # 0 = unlimited
    },
}
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/config.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat(config): add [quota] section default

Default global_quota_gb=0 (unlimited). User overrides via config.ini.
Prevents KeyError when accessing quota section on older config files.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Create `quota.py` module

**Why:** Core quota logic — called by bandwidth timer.

**Files:**
- Create: `src/wg_admin/quota.py`
- Create: `tests/test_quota.py`

- [ ] **Step 1: Write the failing tests in `tests/test_quota.py`**

```python
import pytest
from wg_admin import bandwidth, quota


def make_bw(pubkey, daily_bytes):
    """Build bandwidth.json structure where pubkey used daily_bytes (rx+tx summed) each day."""
    daily = {}
    for i, b in enumerate(daily_bytes):
        from datetime import datetime, timezone, timedelta
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"rx": b, "tx": 0}
    return {"peers": {pubkey: {
        "first_seen": "2026-06-01T00:00:00Z",
        "total_rx": sum(daily_bytes), "total_tx": 0,
        "daily": daily,
        "last_sample": {"ts": "2026-06-27T00:00:00Z", "rx": 0, "tx": 0},
    }}}


def test_check_quotas_peer_without_quota_unchanged():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 0, "quota_suspended": False}]}
    bw = make_bw("PUB", [1024**3] * 30)  # 30 GB
    changes = quota.check_quotas(state, bw, 0)
    assert changes == []
    assert state["peers"][0]["quota_suspended"] is False


def test_check_quotas_suspends_when_exceeded():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 5.0, "quota_suspended": False}]}
    bw = make_bw("PUB", [1024**3] * 10)  # 10 GB in 10 days
    changes = quota.check_quotas(state, bw, 0)
    assert len(changes) == 1
    assert changes[0]["action"] == "suspend"
    assert state["peers"][0]["quota_suspended"] is True
    assert state["peers"][0]["quota_state_updated_at"] is not None


def test_check_quotas_reactivates_when_below():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 100.0, "quota_suspended": True}]}
    bw = make_bw("PUB", [1024**3] * 5)  # 5 GB
    changes = quota.check_quotas(state, bw, 0)
    assert changes[0]["action"] == "reactivate"
    assert state["peers"][0]["quota_suspended"] is False


def test_check_quotas_no_change_when_already_suspended_and_still_over():
    state = {"peers": [{"id": "1", "name": "x", "public_key": "PUB",
                        "quota_gb": 5.0, "quota_suspended": True,
                        "quota_state_updated_at": "old"}]})
    bw = make_bw("PUB", [1024**3] * 10)
    changes = quota.check_quotas(state, bw, 0)
    assert changes == []
    assert state["peers"][0]["quota_state_updated_at"] == "old"  # not touched


def test_global_usage_gb_sums_all_peers():
    bw = {
        "peers": {
            "PUB1": {"daily": {"2026-06-25": {"rx": 1024**3, "tx": 0}}},
            "PUB2": {"daily": {"2026-06-25": {"rx": 0, "tx": 2 * 1024**3}}},
        }
    }
    # Use direct injection — bypass cutoff since test date is in past
    used = quota.global_usage_gb(bw)
    assert isinstance(used, float)


def test_global_quota_exceeded_with_zero_limit_returns_false():
    bw = {"peers": {}}
    assert quota.global_quota_exceeded(bw, 0) is False


def test_global_quota_exceeded_when_usage_over_limit():
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bw = {"peers": {"PUB": {"daily": {today: {"rx": 200 * 1024**3, "tx": 0}}}}}
    assert quota.global_quota_exceeded(bw, 100) is True


def test_global_quota_exceeded_when_usage_under_limit():
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bw = {"peers": {"PUB": {"daily": {today: {"rx": 50 * 1024**3, "tx": 0}}}}}
    assert quota.global_quota_exceeded(bw, 100) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_quota.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'wg_admin.quota'`.

- [ ] **Step 3: Implement `src/wg_admin/quota.py`**

```python
"""Quota checking: per-peer and global bandwidth quotas (rolling 30d)."""
from __future__ import annotations

from . import bandwidth, state


def check_quotas(state_data: dict, bw: dict, global_quota_gb: float) -> list:
    """Update quota_suspended on each peer based on 30-day usage.

    Returns list of changes: [{"peer_id", "name", "action": "suspend"|"reactivate"}].
    """
    changes = []
    for peer in state_data.get("peers", []):
        quota_gb = peer.get("quota_gb", 0.0)
        if quota_gb <= 0:
            continue  # unlimited

        bw_stats = bandwidth.get_peer_stats(bw, peer.get("public_key", ""))
        used_gb = (bw_stats["thirty_day_rx"] + bw_stats["thirty_day_tx"]) / (1024**3)

        was_suspended = peer.get("quota_suspended", False)
        if used_gb > quota_gb:
            if not was_suspended:
                peer["quota_suspended"] = True
                peer["quota_state_updated_at"] = state.utc_now_iso()
                changes.append({"peer_id": peer["id"], "name": peer["name"], "action": "suspend"})
        else:
            if was_suspended:
                peer["quota_suspended"] = False
                peer["quota_state_updated_at"] = state.utc_now_iso()
                changes.append({"peer_id": peer["id"], "name": peer["name"], "action": "reactivate"})
    return changes


def global_usage_gb(bw: dict) -> float:
    """Sum last-30-day usage across all peers (for sidebar display)."""
    cutoff = bandwidth.cutoff_date_str()
    total_bytes = 0
    for peer_data in bw.get("peers", {}).values():
        for d, v in peer_data.get("daily", {}).items():
            if d > cutoff:
                total_bytes += v.get("rx", 0) + v.get("tx", 0)
    return total_bytes / (1024**3)


def global_quota_exceeded(bw: dict, global_quota_gb: float) -> bool:
    """True if rolling 30-day usage exceeds the global limit. False if limit=0."""
    if global_quota_gb <= 0:
        return False
    return global_usage_gb(bw) > global_quota_gb
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_quota.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/quota.py tests/test_quota.py
git commit -m "$(cat <<'EOF'
feat(quota): add quota module with per-peer and global checks

check_quotas mutates peer.quota_suspended based on rolling 30-day usage.
global_usage_gb sums daily buckets for sidebar display.
global_quota_exceeded gates the banner warning.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Integrate quota check into bandwidth timer

**Why:** The systemd timer that samples bandwidth every 5 min should also enforce quotas.

**Files:**
- Modify: `src/wg_admin/bandwidth.py`
- Modify: `tests/test_bandwidth.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bandwidth.py`:

```python
def test_main_track_runs_quota_check_and_saves_changes(tmp_path, monkeypatch):
    """When quota changes peer state, main() saves state + applies to wg."""
    from wg_admin import bandwidth, state as state_mod, config as config_mod

    # Set up paths
    state_path = tmp_path / "state.json.enc"
    config_path = tmp_path / "config.ini"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    master_key = secrets.token_bytes(32)
    (secrets_dir / "master.key").write_bytes(master_key)

    monkeypatch.setattr("wg_admin.bandwidth.STATE_PATH", state_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.SECRETS_DIR", secrets_dir, raising=False)

    # Seed state with one peer over quota
    s = state_mod.empty_state()
    from datetime import datetime, timezone, timedelta
    daily = {}
    for i in range(10):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"rx": 1024**3, "tx": 0}  # 1 GB per day
    s["peers"] = [{
        "id": "1", "name": "x", "public_key": "PUB",
        "private_key_enc": "ENC", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 5.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "...",
    }]
    state_mod.save_state(state_path, s, master_key)

    # Seed bandwidth.json matching pubkey
    bw_path = tmp_path / "bandwidth.json"
    bw_path.write_text(json.dumps({"peers": {"PUB": {
        "first_seen": "2026-06-01T00:00:00Z",
        "total_rx": 10 * 1024**3, "total_tx": 0,
        "daily": daily,
        "last_sample": {"ts": "2026-06-27T00:00:00Z", "rx": 0, "tx": 0},
    }}}))

    # Write minimal config
    config_path.write_text("[wg]\ninterface = wg0\nsubnet = 10.0.0.0/24\nserver_ip = 10.0.0.1\n[quota]\nglobal_quota_gb = 0\n")

    # Stub wg_show_dump to return empty (no traffic to add)
    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: [])
    apply_calls = []
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": apply_calls.append(mode))

    # Run main
    import sys
    monkeypatch.setattr(sys, "argv", ["bandwidth.py", "track", str(bw_path)])
    bandwidth.main()

    # Verify state was updated
    new_state = state_mod.load_state(state_path, master_key)
    assert new_state["peers"][0]["quota_suspended"] is True
    assert apply_calls == ["syncconf"]


def test_main_track_no_changes_does_not_save_state(tmp_path, monkeypatch):
    """If no quota changes, state file is not rewritten."""
    from wg_admin import bandwidth, state as state_mod

    state_path = tmp_path / "state.json.enc"
    config_path = tmp_path / "config.ini"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    master_key = secrets.token_bytes(32)
    (secrets_dir / "master.key").write_bytes(master_key)

    monkeypatch.setattr("wg_admin.bandwidth.STATE_PATH", state_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr("wg_admin.bandwidth.SECRETS_DIR", secrets_dir, raising=False)

    s = state_mod.empty_state()
    s["peers"] = [{"id": "1", "name": "x", "public_key": "PUB",
                   "private_key_enc": "ENC", "ip": "10.0.0.2", "disabled": False,
                   "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
                   "created_at": "..."}]
    state_mod.save_state(state_path, s, master_key)
    original_mtime = state_path.stat().st_mtime

    config_path.write_text("[wg]\ninterface = wg0\nsubnet = 10.0.0.0/24\nserver_ip = 10.0.0.1\n")

    bw_path = tmp_path / "bandwidth.json"
    bw_path.write_text(json.dumps({"peers": {}}))

    monkeypatch.setattr("wg_admin.wg.wg_show_dump", lambda iface: [])
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda *a, **k: None)

    import sys, time
    monkeypatch.setattr(sys, "argv", ["bandwidth.py", "track", str(bw_path)])
    bandwidth.main()
    time.sleep(0.1)
    assert state_path.stat().st_mtime == original_mtime
```

Add `import secrets` and `import json` to top of `tests/test_bandwidth.py` if not already.

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_bandwidth.py::test_main_track_runs_quota_check_and_saves_changes tests/test_bandwidth.py::test_main_track_no_changes_does_not_save_state -v
```

Expected: FAIL — `STATE_PATH` attribute doesn't exist on `bandwidth` module yet; `main()` doesn't call quota check.

- [ ] **Step 3: Modify `src/wg_admin/bandwidth.py`**

At top of file, after imports, add:

```python
from pathlib import Path

# These mirror app.py paths. Patchable in tests.
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
SECRETS_DIR = Path("/wg-admin/secrets")
```

Replace `main()` with:

```python
def main():
    if len(sys.argv) > 1 and sys.argv[1] == "track":
        path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PATH
        track_sample(path)
        _run_quota_check(path)
        return 0
    print("Usage: python -m wg_admin.bandwidth track [path]", file=sys.stderr)
    return 1


def _run_quota_check(bw_path: Path) -> None:
    """After sampling, check quotas and apply state if any peer changed."""
    try:
        from . import config as config_mod, quota, state as state_mod, wg
        master_key_path = SECRETS_DIR / "master.key"
        if not master_key_path.exists():
            return  # not fully installed
        master_key = master_key_path.read_bytes()
        state_data = state_mod.load_state(STATE_PATH, master_key)
        cfg = config_mod.load_config(CONFIG_PATH)
        global_q = cfg["quota"].getfloat("global_quota_gb", 0)
        bw = load_bandwidth(bw_path)
        changes = quota.check_quotas(state_data, bw, global_q)
        if changes:
            state_mod.save_state(STATE_PATH, state_data, master_key)
            wg.apply_state_to_wg(state_data, cfg, mode="syncconf")
    except Exception as e:
        print(f"WARN: quota check failed: {e}", file=sys.stderr)
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_bandwidth.py -v
```

Expected: PASS all tests including the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/bandwidth.py tests/test_bandwidth.py
git commit -m "$(cat <<'EOF'
feat(bandwidth): integrate quota check into 5-min timer

After each sample, _run_quota_check loads state, evaluates per-peer
quotas, and saves + applies to wg if any peer's suspended state
changed. Failures are logged to stderr and don't break sampling.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Add `wg_interface_active` helper

**Why:** Needed for kill switch (Task 13).

**Files:**
- Modify: `src/wg_admin/wg.py`
- Modify: `tests/test_wg.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wg.py`:

```python
def test_wg_interface_active_true(monkeypatch):
    from wg_admin import wg
    class FakeResult:
        returncode = 0
    monkeypatch.setattr("wg_admin.wg.subprocess.run", lambda *a, **k: FakeResult())
    assert wg.wg_interface_active("wg0") is True


def test_wg_interface_active_false(monkeypatch):
    from wg_admin import wg
    class FakeResult:
        returncode = 3  # non-zero = inactive
    monkeypatch.setattr("wg_admin.wg.subprocess.run", lambda *a, **k: FakeResult())
    assert wg.wg_interface_active("wg0") is False


def test_wg_interface_active_file_not_found_returns_false(monkeypatch):
    from wg_admin import wg
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr("wg_admin.wg.subprocess.run", boom)
    assert wg.wg_interface_active("wg0") is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_wg.py::test_wg_interface_active_true tests/test_wg.py::test_wg_interface_active_false tests/test_wg.py::test_wg_interface_active_file_not_found_returns_false -v
```

Expected: FAIL — `wg_interface_active` doesn't exist.

- [ ] **Step 3: Implement in `src/wg_admin/wg.py`**

Add near `wg_quick_restart`:

```python
def wg_interface_active(interface: str = "wg0") -> bool:
    """True if wg-quick@<interface> systemd unit is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", f"wg-quick@{interface}"],
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_wg.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/wg.py tests/test_wg.py
git commit -m "$(cat <<'EOF'
feat(wg): add wg_interface_active helper

Wraps systemctl is-active --quiet wg-quick@<iface>. Returns bool.
Used by kill switch route and sidebar status display.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Add `/vpn/toggle` route

**Why:** Endpoint for kill switch button.

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_vpn_toggle_off_when_active(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: True)
    import subprocess as _sp
    monkeypatch.setattr("wg_admin.app.subprocess", _sp)
    calls = []
    monkeypatch.setattr("wg_admin.app.subprocess.run",
                        lambda cmd, **kw: calls.append(cmd))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/vpn/toggle", data={"csrf_token": token})
    assert r.status_code in (301, 302)
    assert ["systemctl", "stop", "wg-quick@wg0"] in calls


def test_vpn_toggle_on_when_inactive(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: False)
    import subprocess as _sp
    monkeypatch.setattr("wg_admin.app.subprocess", _sp)
    calls = []
    monkeypatch.setattr("wg_admin.app.subprocess.run",
                        lambda cmd, **kw: calls.append(cmd))
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/vpn/toggle", data={"csrf_token": token})
    assert r.status_code in (301, 302)
    assert ["systemctl", "start", "wg-quick@wg0"] in calls


def test_vpn_toggle_requires_login(client):
    r = client.post("/vpn/toggle", data={})
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]


def test_vpn_toggle_requires_csrf(client, workdir):
    client.post("/login", data={"password": "test-password"})
    r = client.post("/vpn/toggle", data={})  # no csrf_token
    assert r.status_code == 403
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_vpn_toggle_off_when_active tests/test_app.py::test_vpn_toggle_on_when_inactive tests/test_app.py::test_vpn_toggle_requires_login tests/test_app.py::test_vpn_toggle_requires_csrf -v
```

Expected: FAIL — route doesn't exist (404).

- [ ] **Step 3: Add route to `src/wg_admin/app.py`**

Add `import subprocess` at top of `app.py` (with other stdlib imports). Then add route after `peer_edit`:

```python
    @app.route("/vpn/toggle", methods=["POST"])
    @login_required
    def vpn_toggle():
        interface = cfg["wg"]["interface"]
        if wg.wg_interface_active(interface):
            subprocess.run(
                ["systemctl", "stop", f"wg-quick@{interface}"],
                capture_output=True, check=True,
            )
            flash("VPN desativada — todos os peers desconectados", "warning")
        else:
            subprocess.run(
                ["systemctl", "start", f"wg-quick@{interface}"],
                capture_output=True, check=True,
            )
            flash("VPN reativada", "success")
        return redirect(request.referrer or url_for("peers_list"))
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_app.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(app): add /vpn/toggle route for kill switch

Stops or starts wg-quick@<interface> based on current state.
Requires login + CSRF. Redirects back to referrer.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Add context processor for sidebar globals

**Why:** Templates need `vpn_active`, `global_used_gb`, `global_quota_gb`, `global_exceeded` on every page.

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_context_processor_injects_vpn_and_quota_globals(client, workdir, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: True)
    monkeypatch.setattr("wg_admin.bandwidth.load_bandwidth",
                        lambda path: {"peers": {}})
    client.post("/login", data={"password": "test-password"})
    r = client.get("/peers")
    assert b"VPN:" in r.data  # sidebar shows "VPN: ativa"
    assert b"GB" in r.data  # quota display "0.0 / 0 GB"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_context_processor_injects_vpn_and_quota_globals -v
```

Expected: FAIL — template doesn't render the new sidebar elements yet (the context processor alone won't make this test pass without the template change in Task 13).

Adjust: split into two tests. One that checks context processor injects values (no template needed):

```python
def test_context_processor_injects_globals(client, workdir, monkeypatch):
    monkeypatch.setattr("wg_admin.wg.wg_interface_active", lambda iface: True)
    monkeypatch.setattr("wg_admin.bandwidth.load_bandwidth",
                        lambda path: {"peers": {}})
    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        pass  # ensure app is initialized
    # Access the context processor directly
    from wg_admin.app import app as app_module
    # The context processor is registered; we can check by hitting any template
    r = client.get("/peers")
    # Templates will have access via {{ vpn_active }} etc.
    # We assert by examining the rendered HTML for the sidebar additions
    # which come in Task 13. For now, assert no exception raised.
    assert r.status_code == 200
```

Move the assertions on rendered HTML to Task 13.

- [ ] **Step 3: Add context processor to `src/wg_admin/app.py`**

After `csrf_protect` block, before route definitions:

```python
    from . import quota as quota_mod

    @app.context_processor
    def inject_globals():
        try:
            bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
            global_q = cfg["quota"].getfloat("global_quota_gb", 0)
            return {
                "vpn_active": wg.wg_interface_active(cfg["wg"]["interface"]),
                "global_used_gb": quota_mod.global_usage_gb(bw),
                "global_quota_gb": global_q,
                "global_exceeded": quota_mod.global_quota_exceeded(bw, global_q),
            }
        except Exception:
            # If anything fails (bandwidth.json missing, etc.) — don't break every page
            return {
                "vpn_active": False,
                "global_used_gb": 0.0,
                "global_quota_gb": 0.0,
                "global_exceeded": False,
            }
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS. The context processor won't break anything since it gracefully degrades.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(app): context processor injects vpn + quota globals

vpn_active, global_used_gb, global_quota_gb, global_exceeded available
in every template. Gracefully degrades to defaults if bandwidth.json
is missing.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Update `peer_new` POST to read `quota_gb`

**Why:** Wire up the create form to save the quota field.

**Files:**
- Modify: `src/wg_admin/app.py` (peer_new route)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_peer_new_saves_quota_gb(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={
        "name": "João", "quota_gb": "10.5", "csrf_token": token,
    })
    assert r.status_code in (301, 302)

    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["quota_gb"] == 10.5
    assert s["peers"][0]["quota_suspended"] is False


def test_peer_new_quota_gb_defaults_to_zero_when_blank(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={"name": "X", "csrf_token": token})
    assert r.status_code in (301, 302)

    s = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s["peers"][0]["quota_gb"] == 0.0


def test_peer_new_rejects_negative_quota(client, workdir, monkeypatch):
    client.post("/login", data={"password": "test-password"})
    monkeypatch.setattr("wg_admin.wg.wg_genkey", lambda: ("PRIVATE", "PUBLIC"))
    monkeypatch.setattr("wg_admin.wg.apply_state_to_wg", lambda s, cfg, mode="syncconf": None)

    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/new", data={
        "name": "X", "quota_gb": "-5", "csrf_token": token,
    })
    # Form should re-render with error, not save
    assert r.status_code == 200
    assert b"negativ" in r.data.lower()  # matches "negativa"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_peer_new_saves_quota_gb tests/test_app.py::test_peer_new_quota_gb_defaults_to_zero_when_blank tests/test_app.py::test_peer_new_rejects_negative_quota -v
```

Expected: FAIL — peer_new doesn't read quota_gb field.

- [ ] **Step 3: Update `peer_new` in `src/wg_admin/app.py`**

Replace the peer_new POST branch:

```python
    @app.route("/peers/new", methods=["GET", "POST"])
    @login_required
    def peer_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            notes = request.form.get("notes", "").strip()
            quota_raw = request.form.get("quota_gb", "").strip()
            try:
                quota_gb = float(quota_raw) if quota_raw else 0.0
            except ValueError:
                quota_gb = 0.0
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_form.html")
            if quota_gb < 0:
                flash("Cota não pode ser negativa", "error")
                return render_template("peer_form.html"), 400

            priv, pub = wg.wg_genkey()
            s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
            ip = state.allocate_ip(s, cfg["wg"]["subnet"], cfg["wg"]["server_ip"])
            new_peer = {
                "id": state.new_peer_id(),
                "name": name,
                "notes": notes,
                "public_key": pub,
                "private_key_enc": state.encrypt_private_key(priv, app.config["MASTER_KEY"]),
                "ip": ip,
                "disabled": False,
                "quota_gb": quota_gb,
                "quota_suspended": False,
                "quota_state_updated_at": None,
                "created_at": state.utc_now_iso(),
            }
            state.add_peer(s, new_peer)
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            try:
                wg.apply_state_to_wg(s, cfg, mode="syncconf")
            except Exception:
                app.logger.exception("Failed to apply state to wg")
                flash("Peer criado no estado, mas falhou apply ao wg — ver logs", "error")
                return redirect(url_for("peers_list"))
            flash(f"Peer criado: {name} ({ip})", "success")
            return redirect(url_for("peers_list"))
        return render_template("peer_form.html")
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(app): peer_new reads and validates quota_gb

Accepts float, defaults 0 (unlimited), rejects negative. Saves to
state with quota_suspended=False and quota_state_updated_at=None.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Update `peer_edit` POST to read `quota_gb`

**Why:** Allow editing quota on existing peers.

**Files:**
- Modify: `src/wg_admin/app.py` (peer_edit route)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_peer_edit_saves_quota_gb(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "2026-06-17T00:00:00Z",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    client.post("/login", data={"password": "test-password"})
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    r = client.post("/peers/abc12345/edit", data={
        "name": "x", "notes": "", "quota_gb": "25", "csrf_token": token,
    })
    assert r.status_code in (301, 302)

    s2 = state_mod.load_state(workdir["state_path"], client.application.config["MASTER_KEY"])
    assert s2["peers"][0]["quota_gb"] == 25.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_peer_edit_saves_quota_gb -v
```

Expected: FAIL.

- [ ] **Step 3: Update `peer_edit` in `src/wg_admin/app.py`**

Replace peer_edit POST branch:

```python
    @app.route("/peers/<peer_id>/edit", methods=["GET", "POST"])
    @login_required
    def peer_edit(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            notes = request.form.get("notes", "").strip()
            new_priv = request.form.get("private_key", "").strip()
            quota_raw = request.form.get("quota_gb", "").strip()
            try:
                quota_gb = float(quota_raw) if quota_raw else 0.0
            except ValueError:
                quota_gb = 0.0
            if not name:
                flash("Nome é obrigatório", "error")
                return render_template("peer_edit.html", peer=peer), 400
            if quota_gb < 0:
                flash("Cota não pode ser negativa", "error")
                return render_template("peer_edit.html", peer=peer), 400
            peer["name"] = name
            peer["notes"] = notes
            peer["quota_gb"] = quota_gb
            if new_priv:
                peer["private_key_enc"] = state.encrypt_private_key(new_priv, app.config["MASTER_KEY"])
                flash("Chave privada atualizada — já podes descarregar .conf e QR", "success")
            else:
                flash("Peer atualizado", "success")
            state.save_state(STATE_PATH, s, app.config["MASTER_KEY"])
            return redirect(url_for("peers_list"))
        return render_template("peer_edit.html", peer=peer)
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(app): peer_edit reads and saves quota_gb

Allows editing quota on existing peers. Same validation as peer_new.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Add `quota_gb` field to `peer_form.html` (create)

**Why:** UI for entering quota at peer creation.

**Files:**
- Modify: `templates/peer_form.html`

- [ ] **Step 1: Update the form template**

Insert after the `notes` field, before `csrf_token`:

```html
    <label for="quota_gb">COTA (GB, 0 = ilimitado)</label>
    <input type="number" id="quota_gb" name="quota_gb" step="0.1" min="0"
           value="0" placeholder="0">
    <p class="hint">Soma dos últimos 30 dias. Ao exceder, suspende automaticamente;
       reativa sozinho quando baixar abaixo do limite.</p>
```

- [ ] **Step 2: Manual check**

```bash
# Start dev server (if you have a way to) and visit /peers/new
# Or just inspect the rendered template
source venv/bin/activate && python -c "
from wg_admin.app import create_app
app = create_app()
app.config['TESTING'] = True
app.config['SESSION_COOKIE_SECURE'] = False
with app.test_client() as c:
    c.post('/login', data={'password': open('/wg-admin/secrets/auth.ini').read().split('=', 1)[1].strip() and 'YOUR_DEV_PW'})
    r = c.get('/peers/new')
    print(r.data.decode())
" 2>&1 | grep -A 1 "quota_gb"
```

If you don't have a dev server set up, just visually confirm the field is in the template.

- [ ] **Step 3: Run all tests to ensure nothing breaks**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add templates/peer_form.html
git commit -m "$(cat <<'EOF'
feat(ui): add quota_gb field to peer create form

Number input with step=0.1, default 0 (unlimited). Hint explains
the rolling 30-day semantics.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Add `quota_gb` field to `peer_edit.html`

**Why:** UI for editing quota on existing peers.

**Files:**
- Modify: `templates/peer_edit.html`

- [ ] **Step 1: Update the form template**

Insert after `notes` field, before the `<details>` for private key:

```html
    <label for="quota_gb">COTA (GB, 0 = ilimitado)</label>
    <input type="number" id="quota_gb" name="quota_gb" step="0.1" min="0"
           value="{{ peer.quota_gb if peer.quota_gb is defined else 0 }}">
    <p class="hint">Soma dos últimos 30 dias. Ao exceder, suspende automaticamente;
       reativa sozinho quando baixar abaixo do limite.</p>
```

- [ ] **Step 2: Run all tests**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add templates/peer_edit.html
git commit -m "$(cat <<'EOF'
feat(ui): add quota_gb field to peer edit form

Same semantics as create form. Pre-populates from peer.quota_gb.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Add sparkline helper

**Why:** Generates inline SVG path for per-card sparkline. Pure function, easy to unit test.

**Files:**
- Modify: `src/wg_admin/bandwidth.py` (add helper here since it's bandwidth-related)
- Modify: `tests/test_bandwidth.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bandwidth.py`:

```python
def test_sparkline_path_empty_values_returns_empty():
    from wg_admin.bandwidth import sparkline_path
    assert sparkline_path([]) == ""
    assert sparkline_path([0, 0, 0]) == ""


def test_sparkline_path_returns_valid_svg_path():
    from wg_admin.bandwidth import sparkline_path
    result = sparkline_path([1, 5, 3, 8, 2], width=100, height=20)
    assert result.startswith("M ")
    assert " L " in result
    # 5 points → 4 "L" segments
    assert result.count(" L ") == 4


def test_sparkline_path_normalizes_to_height():
    from wg_admin.bandwidth import sparkline_path
    # Max value should map to y=0 (top of svg)
    result = sparkline_path([0, 10, 0], width=30, height=10)
    # Point at index 1 (value 10 = max) should have y=0
    # Path looks like "M 0.0,10.0 L 15.0,0.0 L 30.0,10.0"
    parts = result.replace("M ", "").split(" L ")
    assert parts[1] == "15.0,0.0"


def test_sparkline_path_single_value_does_not_crash():
    from wg_admin.bandwidth import sparkline_path
    # Only 1 value — step calculation would divide by zero
    # Acceptable: return empty or a dot
    result = sparkline_path([5], width=80, height=24)
    # Either empty or "M 0.0,0.0" or similar
    assert isinstance(result, str)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_bandwidth.py::test_sparkline_path_empty_values_returns_empty tests/test_bandwidth.py::test_sparkline_path_returns_valid_svg_path tests/test_bandwidth.py::test_sparkline_path_normalizes_to_height tests/test_bandwidth.py::test_sparkline_path_single_value_does_not_crash -v
```

Expected: FAIL — `sparkline_path` doesn't exist.

- [ ] **Step 3: Implement in `src/wg_admin/bandwidth.py`**

Append at end of module (before `main` if you prefer, or after):

```python
def sparkline_path(values: list, width: int = 80, height: int = 24) -> str:
    """Generate SVG path 'd' attribute for a sparkline.

    values: list of non-negative numbers (bytes per day, typically).
    Returns the SVG path string, or empty string if no data.
    """
    if not values or max(values) == 0 or len(values) < 2:
        return ""
    max_v = max(values)
    step = width / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = i * step
        y = height - (v / max_v) * height
        points.append(f"{x:.1f},{y:.1f}")
    return "M " + " L ".join(points)
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_bandwidth.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/bandwidth.py tests/test_bandwidth.py
git commit -m "$(cat <<'EOF'
feat(bandwidth): add sparkline_path helper

Generates SVG path string for inline sparkline rendering in peer
cards. Returns empty string for empty/all-zero/single-value inputs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Add `/api/bandwidth/<peer_id>` endpoint

**Why:** Data source for per-peer bandwidth modal chart.

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_api_peer_bandwidth_returns_30_days(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    from datetime import datetime, timezone, timedelta
    s = state_mod.empty_state()
    s["peers"] = [{
        "id": "abc12345", "name": "x", "notes": "", "public_key": "PUB",
        "private_key_enc": "", "ip": "10.0.0.2", "disabled": False,
        "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
        "created_at": "...",
    }]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    # Seed bandwidth
    from wg_admin.app import BANDWIDTH_PATH
    bw_path = workdir["tmp_path"] / "bandwidth.json"
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH", bw_path)
    daily = {}
    for i in range(5):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"rx": 1024**3 * (i + 1), "tx": 0}
    bw_path.write_text(json.dumps({"peers": {"PUB": {
        "first_seen": "...", "total_rx": 0, "total_tx": 0,
        "daily": daily, "last_sample": {"ts": "", "rx": 0, "tx": 0},
    }}}))

    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/abc12345")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["dates"]) == 30
    assert len(data["rx"]) == 30
    assert len(data["tx"]) == 30
    # Sum of rx should equal 5 * 1GB across the 5 days we seeded
    assert sum(data["rx"]) == sum((i + 1) * 1024**3 for i in range(5))


def test_api_peer_bandwidth_404_for_unknown_peer(client, workdir):
    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/nonexistent")
    assert r.status_code == 404


def test_api_peer_bandwidth_requires_login(client):
    r = client.get("/api/bandwidth/anything")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers["Location"]
```

Add `import json` to top of test file if not present.

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_api_peer_bandwidth_returns_30_days tests/test_app.py::test_api_peer_bandwidth_404_for_unknown_peer tests/test_app.py::test_api_peer_bandwidth_requires_login -v
```

Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Add route to `src/wg_admin/app.py`**

Add `from datetime import datetime, timezone, timedelta` at top, plus `from flask import jsonify`. Then add route after `peer_qr`:

```python
    @app.route("/api/bandwidth/<peer_id>")
    @login_required
    def api_peer_bandwidth(peer_id):
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        peer = state.find_peer_by_id(s, peer_id)
        if peer is None:
            abort(404)
        bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
        peer_bw = bw.get("peers", {}).get(peer.get("public_key", ""), {})
        daily = peer_bw.get("daily", {})

        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        return jsonify({
            "dates": dates,
            "rx": [daily.get(d, {"rx": 0})["rx"] for d in dates],
            "tx": [daily.get(d, {"tx": 0})["tx"] for d in dates],
        })
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_app.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(api): add /api/bandwidth/<peer_id> endpoint

Returns 30 days of rx/tx as parallel arrays for Chart.js consumption.
404 for unknown peer. Requires login.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Add `/api/bandwidth/global` endpoint

**Why:** Data source for top-of-page stacked chart.

**Files:**
- Modify: `src/wg_admin/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_api_global_bandwidth_returns_top_5_plus_outros(client, workdir, monkeypatch):
    from wg_admin import state as state_mod
    from datetime import datetime, timezone, timedelta
    s = state_mod.empty_state()
    s["peers"] = [
        {"id": str(i), "name": f"peer{i}", "public_key": f"PUB{i}",
         "private_key_enc": "", "ip": f"10.0.0.{i+2}", "disabled": False,
         "quota_gb": 0.0, "quota_suspended": False, "quota_state_updated_at": None,
         "created_at": "..."}
        for i in range(7)
    ]
    state_mod.save_state(workdir["state_path"], s, client.application.config["MASTER_KEY"])

    bw_path = workdir["tmp_path"] / "bandwidth.json"
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH", bw_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bw_data = {"peers": {}}
    for i in range(7):
        bw_data["peers"][f"PUB{i}"] = {
            "daily": {today: {"rx": (i + 1) * 1024**3, "tx": 0}},
            "first_seen": "", "total_rx": 0, "total_tx": 0,
            "last_sample": {"ts": "", "rx": 0, "tx": 0},
        }
    bw_path.write_text(json.dumps(bw_data))

    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/global")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["dates"]) == 30
    series_names = [s["name"] for s in data["series"]]
    # Top 5 peers ( PUB6=7GB, PUB5=6GB, PUB4=5GB, PUB3=4GB, PUB2=3GB )
    # + "outros" ( PUB0=1GB + PUB1=2GB = 3GB )
    assert "outros" in series_names
    assert len(data["series"]) == 6  # 5 + outros


def test_api_global_bandwidth_empty(client, workdir, monkeypatch):
    bw_path = workdir["tmp_path"] / "bandwidth.json"
    monkeypatch.setattr("wg_admin.app.BANDWIDTH_PATH", bw_path)
    bw_path.write_text(json.dumps({"peers": {}}))
    client.post("/login", data={"password": "test-password"})
    r = client.get("/api/bandwidth/global")
    assert r.status_code == 200
    data = r.get_json()
    assert data["series"] == []
    assert len(data["dates"]) == 30
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_app.py::test_api_global_bandwidth_returns_top_5_plus_outros tests/test_app.py::test_api_global_bandwidth_empty -v
```

Expected: FAIL.

- [ ] **Step 3: Add route to `src/wg_admin/app.py`**

```python
    @app.route("/api/bandwidth/global")
    @login_required
    def api_global_bandwidth():
        s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
        bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)

        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]

        # Build per-peer total over the 30-day window, sorted desc
        peer_totals = []  # (name, pubkey, total_bytes)
        for peer in s["peers"]:
            pub = peer.get("public_key", "")
            peer_bw = bw.get("peers", {}).get(pub, {})
            total = 0
            for d, v in peer_bw.get("daily", {}).items():
                if d in dates:
                    total += v.get("rx", 0) + v.get("tx", 0)
            peer_totals.append((peer.get("name", "?"), pub, total))

        peer_totals.sort(key=lambda x: x[2], reverse=True)

        series = []
        # Top 5 named peers (only those with > 0 bytes)
        top = [p for p in peer_totals[:5] if p[2] > 0]
        # Aggregate "outros" = rest
        outros_total = sum(p[2] for p in peer_totals[5:])

        for name, pub, _ in top:
            peer_bw = bw.get("peers", {}).get(pub, {})
            daily = peer_bw.get("daily", {})
            series.append({
                "name": name,
                "data": [daily.get(d, {"rx": 0, "tx": 0}).get("rx", 0) +
                         daily.get(d, {"rx": 0, "tx": 0}).get("tx", 0)
                         for d in dates],
            })
        if outros_total > 0:
            # Sum of all "outros" peers per day
            outros_per_day = [0] * 30
            for name, pub, _ in peer_totals[5:]:
                peer_bw = bw.get("peers", {}).get(pub, {})
                daily = peer_bw.get("daily", {})
                for i, d in enumerate(dates):
                    v = daily.get(d, {"rx": 0, "tx": 0})
                    outros_per_day[i] += v.get("rx", 0) + v.get("tx", 0)
            series.append({"name": "outros", "data": outros_per_day})

        return jsonify({"dates": dates, "series": series})
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_app.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(api): add /api/bandwidth/global endpoint

Returns 30-day series for top 5 peers by traffic + aggregated "outros".
Series entries: {name, data: [30 daily totals]}.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Download Chart.js v4 to `static/vendor/`

**Why:** Local copy avoids runtime CDN dependency (panel may run on isolated network).

**Files:**
- Create: `static/vendor/chartjs.min.js`

- [ ] **Step 1: Download Chart.js**

```bash
mkdir -p static/vendor
curl -fsSL https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js -o static/vendor/chartjs.min.js
```

- [ ] **Step 2: Verify file is non-empty and valid JS**

```bash
test -s static/vendor/chartjs.min.js && head -c 200 static/vendor/chartjs.min.js
```

Expected: first bytes look like `/*! * Chart.js v4.4.1 ...`.

- [ ] **Step 3: Commit**

```bash
git add static/vendor/chartjs.min.js
git commit -m "$(cat <<'EOF'
chore: vendor Chart.js v4.4.1 locally

Avoids runtime CDN dependency — panel may run on isolated networks.
~70KB UMD bundle.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: Update `peers.html` — sparkline + quota bar + WhatsApp button in card

**Why:** Wire card UI to new data.

**Files:**
- Modify: `src/wg_admin/app.py` (peers_list — compute sparkline data + quota display)
- Modify: `templates/peers.html`

- [ ] **Step 1: Update `peers_list` in `src/wg_admin/app.py`**

Inside the `for peer in s["peers"]` loop, add sparkline + quota calculations. Replace the `peer_views.append({...})` block with:

```python
            bw_stats = bandwidth.get_peer_stats(bw, pub)

            # Sparkline: last 30 days of rx+tx
            today = datetime.now(timezone.utc).date()
            dates_30 = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
            peer_bw = bw.get("peers", {}).get(pub, {})
            daily = peer_bw.get("daily", {})
            sparkline_values = [
                daily.get(d, {"rx": 0}).get("rx", 0) + daily.get(d, {"tx": 0}).get("tx", 0)
                for d in dates_30
            ]
            sparkline = bandwidth.sparkline_path(sparkline_values)

            # Quota display
            quota_gb = peer.get("quota_gb", 0.0)
            used_30d_gb = (bw_stats["thirty_day_rx"] + bw_stats["thirty_day_tx"]) / (1024**3)
            if quota_gb > 0:
                quota_pct = min(100, (used_30d_gb / quota_gb) * 100)
                if peer.get("quota_suspended") or quota_pct >= 95:
                    quota_class = "danger"
                elif quota_pct >= 70:
                    quota_class = "warn"
                else:
                    quota_class = "ok"
            else:
                quota_pct = None
                quota_class = None

            peer_views.append({
                "peer": peer,
                "status": status,
                "is_online": is_online,
                "wg_pubkeys": list(statuses_by_key.keys()),
                "bandwidth": {
                    "total_rx": bandwidth.format_bytes(bw_stats["total_rx"]),
                    "total_tx": bandwidth.format_bytes(bw_stats["total_tx"]),
                    "thirty_day_rx": bandwidth.format_bytes(bw_stats["thirty_day_rx"]),
                    "thirty_day_tx": bandwidth.format_bytes(bw_stats["thirty_day_tx"]),
                    "first_seen": bw_stats["first_seen"],
                },
                "sparkline": sparkline,
                "quota_gb": quota_gb,
                "used_30d_gb": used_30d_gb,
                "quota_pct": quota_pct,
                "quota_class": quota_class,
            })
```

Make sure `from datetime import datetime, timezone, timedelta` is at the top of `app.py`.

- [ ] **Step 2: Update peer card markup in `templates/peers.html`**

Inside `<div class="peer-card">`, after the `<dl class="peer-meta">...</dl>` block and before `<div class="peer-actions">`, add:

```html
        {# Sparkline + quota bar #}
        {% if pv.sparkline %}
        <div class="peer-sparkline"
             data-peer-id="{{ peer.id }}"
             data-peer-name="{{ peer.name }}"
             role="button"
             tabindex="0"
             title="Clica para ver detalhes">
          <svg viewBox="0 0 80 24" width="80" height="24">
            <path d="{{ pv.sparkline }}" fill="none"
                  stroke="var(--accent)" stroke-width="1.5"/>
          </svg>
        </div>
        {% endif %}

        {% if pv.quota_gb and pv.quota_gb > 0 %}
        <div class="quota-bar" title="{{ pv.used_30d_gb|round(2) }} / {{ pv.quota_gb }} GB (30d)">
          <div class="quota-fill quota-{{ pv.quota_class }}"
               style="width: {{ pv.quota_pct }}%"></div>
          <span class="quota-text">{{ pv.used_30d_gb|round(1) }} / {{ pv.quota_gb }} GB</span>
        </div>
        {% if peer.quota_suspended %}
        <span class="badge badge-danger">SUSPENSO</span>
        {% endif %}
        {% endif %}
```

Then in the `<div class="peer-actions">` block, after the QR button (inside the `{% if peer.private_key_enc %}` block), add the WhatsApp button:

```html
          <a href="#" class="btn secondary wa-trigger"
             data-peer-id="{{ peer.id }}"
             data-peer-name="{{ peer.name }}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <path d="M17.5 14.4c-.3-.1-1.7-.9-2-1-.3-.1-.5-.1-.7.1-.2.3-.7.9-.9 1.1-.2.2-.3.2-.6.1-1.8-.9-3-1.6-4.2-3.6-.3-.5.3-.5.9-1.6.1-.2 0-.4 0-.5-.1-.2-.7-1.7-1-2.3-.2-.6-.5-.5-.7-.5h-.6c-.2 0-.5.1-.8.4-.3.3-1 1-1 2.5s1.1 2.9 1.2 3.1c.1.2 2.1 3.2 5 4.5 1.9.8 2.6.9 3.5.8.6-.1 1.7-.7 2-1.4.2-.7.2-1.2.2-1.4-.1-.2-.3-.2-.6-.4z"/>
              <path d="M12 2C6.5 2 2 6.5 2 12c0 1.8.5 3.5 1.3 5L2 22l5.2-1.4c1.5.8 3.1 1.2 4.8 1.2 5.5 0 10-4.5 10-10S17.5 2 12 2zm0 18.3c-1.5 0-3-.4-4.3-1.2l-.3-.2-3.1.8.8-3-.2-.3c-.9-1.4-1.3-2.9-1.3-4.5C3.6 7.3 7.3 3.6 12 3.6s8.4 3.7 8.4 8.4-3.7 8.3-8.4 8.3z"/>
            </svg>
            WhatsApp
          </a>
```

- [ ] **Step 3: Add CSS for new card elements in `static/style.css`** (find existing file)

```bash
ls static/*.css
```

Append:

```css
.peer-sparkline {
  margin: 0.5rem 0;
  cursor: pointer;
  opacity: 0.85;
  transition: opacity 0.15s;
}
.peer-sparkline:hover { opacity: 1; }
.peer-sparkline:focus { outline: 1px solid var(--accent); outline-offset: 2px; }

.quota-bar {
  position: relative;
  height: 18px;
  background: var(--bg-elev, #1a1d24);
  border-radius: 4px;
  overflow: hidden;
  margin: 0.5rem 0;
}
.quota-fill {
  height: 100%;
  transition: width 0.3s ease;
}
.quota-fill.quota-ok { background: #10b981; }
.quota-fill.quota-warn { background: #f59e0b; }
.quota-fill.quota-danger { background: #ef4444; }
.quota-text {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-family: var(--font-mono);
  color: white;
  text-shadow: 0 0 4px rgba(0,0,0,0.7);
}

.badge {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-size: 0.65rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.badge-danger { background: #ef4444; color: white; }

.banner {
  padding: 0.75rem 1rem;
  border-radius: 4px;
  margin-bottom: 1rem;
  font-weight: 500;
}
.banner-danger { background: rgba(239, 68, 68, 0.15); border-left: 4px solid #ef4444; color: #fca5a5; }
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wg_admin/app.py templates/peers.html static/style.css
git commit -m "$(cat <<'EOF'
feat(ui): sparkline, quota bar, and WhatsApp button in peer card

Sparkline: inline SVG of last-30-day rx+tx, click opens modal.
Quota bar: green/yellow/red fill when peer has quota_gb > 0.
WhatsApp button: same visibility rule as .conf/QR.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: Add bandwidth modal to `peers.html`

**Why:** Per-peer detailed chart, triggered by sparkline click.

**Files:**
- Modify: `templates/peers.html`
- Create: `static/js/bandwidth-modal.js`

- [ ] **Step 1: Add modal markup to `templates/peers.html`**

After the existing QR modal (`<div class="modal" id="qr-modal">...</div>`), add:

```html
{# ─── Bandwidth Modal ─── #}
<div class="modal" id="bw-modal" aria-hidden="true" role="dialog" aria-labelledby="bw-modal-title">
  <div class="modal-backdrop" data-modal-close></div>
  <div class="modal-card">
    <button class="modal-close" data-modal-close aria-label="Fechar">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    </button>

    <h2 id="bw-modal-title" class="modal-title">
      Bandwidth — <span class="peer-name"></span>
    </h2>

    <canvas id="bw-chart" width="600" height="300"></canvas>

    <div class="modal-actions">
      <button data-modal-close class="btn">Fechar</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Create `static/js/bandwidth-modal.js`**

```javascript
(function() {
  const modal = document.getElementById('bw-modal');
  if (!modal) return;

  const title = modal.querySelector('.peer-name');
  const canvas = modal.querySelector('#bw-chart');
  let chart = null;
  let lastFocused = null;

  async function open(trigger) {
    const peerId = trigger.dataset.peerId;
    const peerName = trigger.dataset.peerName;
    title.textContent = peerName;

    lastFocused = document.activeElement;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';

    try {
      const resp = await fetch(`/api/bandwidth/${peerId}`);
      const data = await resp.json();
      if (chart) chart.destroy();
      chart = new Chart(canvas, {
        type: 'line',
        data: {
          labels: data.dates,
          datasets: [
            { label: 'Download (rx)', data: data.rx, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.1)', tension: 0.3 },
            { label: 'Upload (tx)', data: data.tx, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', tension: 0.3 },
          ],
        },
        options: {
          responsive: true,
          scales: {
            y: {
              ticks: { callback: (v) => formatBytes(v) },
            },
          },
          plugins: { legend: { labels: { color: '#ccc' } } },
        },
      });
    } catch (e) {
      console.error('Failed to load bandwidth data', e);
    }

    setTimeout(() => modal.querySelector('.modal-close').focus(), 50);
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    const units = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(1) + ' ' + units[i];
  }

  function close() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (lastFocused) lastFocused.focus();
  }

  document.querySelectorAll('.peer-sparkline').forEach(el => {
    el.addEventListener('click', (e) => { e.preventDefault(); open(el); });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(el); }
    });
  });

  modal.querySelectorAll('[data-modal-close]').forEach(el => {
    el.addEventListener('click', close);
  });

  document.addEventListener('keydown', (e) => {
    if (!modal.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
  });
})();
```

- [ ] **Step 3: Add script includes to `templates/peers.html`**

After the existing `<script>` block (end of template, before `{% endblock %}`):

```html
<script src="{{ url_for('static', filename='vendor/chartjs.min.js') }}"></script>
<script src="{{ url_for('static', filename='js/bandwidth-modal.js') }}"></script>
```

- [ ] **Step 4: Manual check**

Run dev server and click a sparkline. Modal should open, chart should render.

If you can't run a dev server, just verify the file paths are correct.

- [ ] **Step 5: Commit**

```bash
git add templates/peers.html static/js/bandwidth-modal.js
git commit -m "$(cat <<'EOF'
feat(ui): bandwidth modal with Chart.js line chart

Sparkline click opens modal showing 30-day rx/tx line chart.
Reuses QR modal CSS. Fetches data from /api/bandwidth/<id>.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 21: Add WhatsApp modal to `peers.html`

**Why:** Modal for entering phone number and sending .conf as attachment.

**Files:**
- Modify: `templates/peers.html`
- Create: `static/js/whatsapp-modal.js`

- [ ] **Step 1: Add modal markup to `templates/peers.html`**

After the bandwidth modal, add:

```html
{# ─── WhatsApp Modal ─── #}
<div class="modal" id="wa-modal" aria-hidden="true" role="dialog" aria-labelledby="wa-modal-title">
  <div class="modal-backdrop" data-modal-close></div>
  <div class="modal-card">
    <button class="modal-close" data-modal-close aria-label="Fechar">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    </button>

    <h2 id="wa-modal-title" class="modal-title">
      Partilhar via WhatsApp — <span class="peer-name"></span>
    </h2>

    <label for="wa-ddi">DDI (código do país)</label>
    <select id="wa-ddi">
      <option value="351">🇵🇹 +351 (PT)</option>
      <option value="55">🇧🇷 +55 (BR)</option>
      <option value="34">🇪🇸 +34 (ES)</option>
      <option value="33">🇫🇷 +33 (FR)</option>
      <option value="1">🇺🇸 +1 (US)</option>
      <option value="">Outro (escreve DDI+número abaixo)</option>
    </select>

    <label for="wa-phone">NÚMERO</label>
    <input type="tel" id="wa-phone" placeholder="912 345 678" autocomplete="off">

    <p class="hint">
      Se DDI = "Outro", escreve DDI+número juntos (ex: <code>44 7700 900123</code>).
      Caso contrário, só o número sem DDI. Apenas dígitos.
    </p>

    <details>
      <summary>Pré-visualizar mensagem</summary>
      <pre class="wa-preview" style="white-space: pre-wrap; font-size: 0.75rem; max-height: 200px; overflow: auto;"></pre>
    </details>

    <div class="modal-actions">
      <button id="wa-send" class="btn" disabled>Abrir WhatsApp</button>
      <button data-modal-close class="btn secondary">Cancelar</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Create `static/js/whatsapp-modal.js`**

```javascript
(function() {
  const modal = document.getElementById('wa-modal');
  if (!modal) return;

  const title = modal.querySelector('.peer-name');
  const phoneInput = document.getElementById('wa-phone');
  const ddiSelect = document.getElementById('wa-ddi');
  const sendBtn = document.getElementById('wa-send');
  const preview = modal.querySelector('.wa-preview');
  let lastFocused = null;
  let currentPeer = { id: '', name: '' };

  // Restore last DDI from localStorage
  const savedDdi = localStorage.getItem('wa.ddi');
  if (savedDdi !== null) ddiSelect.value = savedDdi;

  function messageFor(name) {
    return `Olá ${name}! Segue em anexo a configuração da tua VPN wg-admin.\nImporta em: app WireGuard → Adicionar → Importar tunnel(s) from file.`;
  }

  async function open(trigger) {
    currentPeer = { id: trigger.dataset.peerId, name: trigger.dataset.peerName };
    title.textContent = currentPeer.name;
    preview.textContent = messageFor(currentPeer.name);
    phoneInput.value = '';
    sendBtn.disabled = true;

    lastFocused = document.activeElement;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    setTimeout(() => phoneInput.focus(), 50);
  }

  function close() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (lastFocused) lastFocused.focus();
  }

  phoneInput.addEventListener('input', () => {
    sendBtn.disabled = phoneInput.value.replace(/\D/g, '').length < 6;
  });

  ddiSelect.addEventListener('change', () => {
    localStorage.setItem('wa.ddi', ddiSelect.value);
  });

  async function send() {
    const ddi = ddiSelect.value;
    const phone = phoneInput.value.replace(/\D/g, '');
    const fullNumber = ddi + phone;
    if (fullNumber.length < 6) return;

    const message = messageFor(currentPeer.name);
    const fileName = `wg-${currentPeer.name}.conf`;

    // Fetch .conf text
    let confText = '';
    try {
      const resp = await fetch(`/peers/${currentPeer.id}/conf`);
      confText = await resp.text();
    } catch (e) {
      alert('Falha ao buscar .conf.');
      return;
    }

    // Mobile path: Web Share API with file attachment
    if (navigator.canShare && navigator.canShare({ files: [new File([confText], fileName, { type: 'text/plain' })] })) {
      const file = new File([confText], fileName, { type: 'text/plain' });
      try {
        await navigator.share({ text: message, files: [file] });
        close();
        return;
      } catch (e) {
        if (e.name === 'AbortError') return;  // user cancelled
        // fall through to desktop path
      }
    }

    // Desktop path: download .conf + open chat
    const blob = new Blob([confText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    setTimeout(() => {
      window.open(
        `https://wa.me/${fullNumber}?text=${encodeURIComponent(message)}`,
        '_blank', 'noopener'
      );
    }, 500);
    close();
  }

  sendBtn.addEventListener('click', send);

  document.querySelectorAll('.wa-trigger').forEach(btn => {
    btn.addEventListener('click', (e) => { e.preventDefault(); open(btn); });
  });

  modal.querySelectorAll('[data-modal-close]').forEach(el => {
    el.addEventListener('click', close);
  });

  document.addEventListener('keydown', (e) => {
    if (!modal.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
  });
})();
```

- [ ] **Step 3: Add script include to `templates/peers.html`**

After the bandwidth-modal script:

```html
<script src="{{ url_for('static', filename='js/whatsapp-modal.js') }}"></script>
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS (no logic changes, just templates).

- [ ] **Step 5: Commit**

```bash
git add templates/peers.html static/js/whatsapp-modal.js
git commit -m "$(cat <<'EOF'
feat(ui): WhatsApp share modal with phone input

Asks DDI (saved to localStorage) + phone number. Preview message.
On send: mobile uses Web Share API to attach .conf directly; desktop
downloads .conf and opens wa.me chat for manual attach.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 22: Add global chart at top of `/peers`

**Why:** Aggregate view of all peer traffic.

**Files:**
- Modify: `templates/peers.html`
- Create: `static/js/global-chart.js`

- [ ] **Step 1: Add chart container to `templates/peers.html`**

Before `<div class="peer-grid">` (inside the `{% if peer_views %}` block), add:

```html
  {# Global bandwidth chart #}
  <section class="global-chart">
    <h3><span class="accent-bar"></span>Tráfego 30d — todos os peers</h3>
    <canvas id="global-chart" width="1200" height="200"></canvas>
  </section>
```

- [ ] **Step 2: Create `static/js/global-chart.js`**

```javascript
(function() {
  const canvas = document.getElementById('global-chart');
  if (!canvas) return;

  const colors = ['#10b981', '#3b82f6', '#f59e0b', '#ec4899', '#8b5cf6', '#6b7280'];

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    const units = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(1) + ' ' + units[i];
  }

  fetch('/api/bandwidth/global')
    .then(r => r.json())
    .then(data => {
      const datasets = data.series.map((s, i) => ({
        label: s.name,
        data: s.data,
        backgroundColor: colors[i % colors.length] + '99',
        borderColor: colors[i % colors.length],
        fill: true,
        tension: 0.3,
      }));
      new Chart(canvas, {
        type: 'line',
        data: { labels: data.dates, datasets },
        options: {
          responsive: true,
          scales: {
            y: { stacked: true, ticks: { callback: (v) => formatBytes(v) } },
          },
          plugins: { legend: { labels: { color: '#ccc' } } },
        },
      });
    })
    .catch(e => console.error('Failed to load global bandwidth', e));
})();
```

- [ ] **Step 3: Add script include to `templates/peers.html`**

After the whatsapp-modal script:

```html
<script src="{{ url_for('static', filename='js/global-chart.js') }}"></script>
```

- [ ] **Step 4: Add CSS for global chart container**

Append to `static/style.css`:

```css
.global-chart {
  background: var(--bg-elev, #1a1d24);
  border: 1px solid var(--border, #2a2d34);
  border-radius: 6px;
  padding: 1rem;
  margin-bottom: 1.5rem;
}
.global-chart h3 {
  margin: 0 0 0.5rem 0;
  font-size: 0.85rem;
  color: var(--text-muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

- [ ] **Step 5: Commit**

```bash
git add templates/peers.html static/js/global-chart.js static/style.css
git commit -m "$(cat <<'EOF'
feat(ui): global 30-day stacked chart at top of /peers

Loads via fetch on page render. Top 5 peers by traffic + "outros".
Uses Chart.js stacked area.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 23: Add global quota banner at top of `/peers`

**Why:** Warn admin when global quota exceeded.

**Files:**
- Modify: `templates/peers.html`

- [ ] **Step 1: Add banner markup**

At the very top of the content block (after `<p class="subtitle">`), before the `<div class="stats">`:

```html
  {% if global_exceeded %}
  <div class="banner banner-danger">
    ⚠ Cota global excedida — {{ global_used_gb|round(1) }} / {{ global_quota_gb|int }} GB · 30d.
    Considera desativar a VPN no sidebar ou reduzir cotas individuais.
  </div>
  {% endif %}
```

- [ ] **Step 2: Manual check**

Force test by temporarily setting `global_quota_gb = 0.0001` in `config.ini` and load `/peers`. Banner should appear.

Reset to default after.

- [ ] **Step 3: Commit**

```bash
git add templates/peers.html
git commit -m "$(cat <<'EOF'
feat(ui): global quota exceeded banner

Shows when 30-day rolling usage exceeds global_quota_gb. Context
processor (Task 10) provides global_exceeded flag.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 24: Add VPN kill switch + global quota display to sidebar

**Why:** Sidebar shows VPN state, kill switch button, and quota counter on every page.

**Files:**
- Modify: `templates/base.html`

- [ ] **Step 1: Read existing `templates/base.html`**

```bash
cat templates/base.html
```

Find the sidebar — likely has the brand, status indicator, nav, version, GitHub link.

- [ ] **Step 2: Add kill switch + quota display below status indicator**

After the existing "service online" status block, add:

```html
    {# VPN kill switch + quota display #}
    <div class="sidebar-vpn">
      <div class="vpn-status">
        <span class="dot {{ 'active' if vpn_active else 'inactive' }}"></span>
        VPN: {{ 'ativa' if vpn_active else 'desativada' }}
      </div>
      <div class="quota-display {{ 'danger' if global_exceeded else '' }}">
        <span class="text-sm text-muted">30d</span>
        {{ global_used_gb|round(1) }}{% if global_quota_gb > 0 %} / {{ global_quota_gb|int }}{% endif %} GB
      </div>
      <form method="post" action="{{ url_for('vpn_toggle') }}"
            onsubmit="return confirm('Desativar VPN? Todos os peers vão desconectar imediatamente.')">
        <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
        <button type="submit" class="btn {{ 'danger' if vpn_active else 'success' }} btn-block">
          {{ 'Desativar VPN' if vpn_active else 'Ativar VPN' }}
        </button>
      </form>
    </div>
```

- [ ] **Step 3: Add CSS for sidebar additions**

Append to `static/style.css`:

```css
.sidebar-vpn {
  margin: 1rem 0;
  padding-top: 1rem;
  border-top: 1px solid var(--border, #2a2d34);
}
.sidebar-vpn .vpn-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.sidebar-vpn .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.sidebar-vpn .dot.active { background: #10b981; box-shadow: 0 0 6px #10b981; }
.sidebar-vpn .dot.inactive { background: #6b7280; }
.sidebar-vpn .quota-display {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}
.sidebar-vpn .quota-display.danger { color: #ef4444; }
.sidebar-vpn .btn-block { width: 100%; }
.sidebar-vpn .btn-danger { background: #ef4444; color: white; }
.sidebar-vpn .btn-success { background: #10b981; color: white; }
```

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/base.html static/style.css
git commit -m "$(cat <<'EOF'
feat(ui): VPN kill switch + global quota display in sidebar

Shows VPN state, rolling 30-day usage against global quota, and
button to stop/start wg-quick@<iface>. Visible on every page.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 25: Update smoke test doc

**Why:** Document manual verification for QA.

**Files:**
- Modify: `docs/smoke-test.md`

- [ ] **Step 1: Read existing `docs/smoke-test.md`**

```bash
cat docs/smoke-test.md
```

- [ ] **Step 2: Append new sections**

```markdown

## Quotas

- [ ] Create peer with `quota_gb = 0.001`. On a client connected to the VPN, run `curl http://speedtest.tele2.net/1MB.zip -o /dev/null` to generate ~1MB of traffic. Wait ≤5 min (next timer tick). Refresh `/peers`. Verify the peer shows `SUSPENSO` badge and `wg show wg0 dump` no longer lists it.
- [ ] Edit peer, change quota to `10`. Wait ≤5 min. Verify badge disappears and peer returns to `wg show`.
- [ ] Set `global_quota_gb = 0.001` in `/wg-admin/config.ini`, restart `wg-admin-bandwidth.timer` (`systemctl restart wg-admin-bandwidth.timer`). Wait 5 min. Reload `/peers`. Verify red banner appears at top.
- [ ] Reset `global_quota_gb = 0` after testing.

## Kill switch

- [ ] Click "Desativar VPN" in sidebar. Confirm dialog appears. Confirm. Run `wg show wg0` on server — should return empty/error (interface down). All clients disconnect.
- [ ] Reload `/peers` — sidebar shows "VPN: desativada" with green "Ativar VPN" button.
- [ ] Click "Ativar VPN". Within ~3s, `wg show wg0` shows peers again. Clients can reconnect.

## Bandwidth graphs

- [ ] On `/peers`, verify each peer card shows sparkline (small line graph). Cards with no traffic show "sem dados".
- [ ] Click sparkline. Modal opens with Chart.js line chart, 30 days of rx (green) and tx (blue). Close with ESC.
- [ ] Top of `/peers` shows stacked area chart with top 5 peers + "outros". Legend visible.

## WhatsApp share

- [ ] Click "WhatsApp" button on a peer with private key. Modal opens asking DDI + phone.
- [ ] Select DDI = +351, type phone. Preview shows "Olá {name}!...".
- [ ] On desktop: click "Abrir WhatsApp". `.conf` downloads to ~/Downloads. New tab opens wa.me/<number>. Manually attach downloaded file.
- [ ] On mobile (with HTTPS): Web Share API opens share sheet. Select WhatsApp. File is attached automatically.
- [ ] Verify DDI selection persists across page reloads (localStorage).
- [ ] WhatsApp button NOT visible on imported peers (no private key).

## syncconf hot-reload

- [ ] Connect a client to the VPN. Verify `wg show wg0 dump` shows recent handshake.
- [ ] Create a new peer via panel. Within ~1s, verify existing client's tunnel stays up (ping continues uninterrupted). Previously this caused ~1s disconnect.
- [ ] Verify new peer appears in `wg show wg0 dump`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/smoke-test.md
git commit -m "$(cat <<'EOF'
docs: smoke test additions for quotas, kill switch, graphs, WhatsApp

Manual verification checklist for the 4 new features + bonus kill switch.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 26: Update CHANGELOG and README

**Why:** Document the new features.

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Update `CHANGELOG.md`**

In the `[Unreleased]` section, add new feature entries:

```markdown
## [Unreleased]

### Added
- **Per-peer bandwidth quotas** — set `quota_gb` on each peer to auto-suspend when rolling 30-day usage exceeds the limit. Re-enables automatically when usage drops below.
- **Global bandwidth quota** — `[quota] global_quota_gb` in config.ini. Sidebar shows rolling 30-day total. Red banner when exceeded.
- **VPN kill switch** — sidebar button to stop/start `wg-quick@wg0` for emergencies.
- **Live bandwidth charts** — sparkline per peer card, modal with 30-day rx/tx line chart, global stacked area chart at top of `/peers`.
- **WhatsApp share** — modal that asks DDI + phone number, sends `.conf` as attachment via Web Share API on mobile or download+chat on desktop.
- **syncconf hot-reload** — new peers no longer drop active tunnels (zero-downtime create).
- **GitHub repo link** in the sidebar footer (opens in new tab with `rel="noopener"`).

### Changed
- **`_apply_state_to_wg` moved from `app.py` to `wg.py`** as `apply_state_to_wg(s, cfg, mode)`. Required for bandwidth timer to enforce quotas.
- **Removed the warning banner** from the peers list page (`/peers`). The "wg-quick restart disconnects active peers" warning now only shows on the create-peer form, where it's contextually relevant.
- **Simplified status badge**: dropped the `configurado` middle state. Peers now show either `online` (handshake within last 180s) or `offline` (anything else, including no recent handshake or peer not in `wg show`). `inativo` still used for disabled peers.
```

- [ ] **Step 2: Update `README.md`**

Update the **Features** section to mention new features. Find the `### Peer management` block and add a new section:

```markdown
### Quotas & monitoring
- **Per-peer quotas**: set GB limit per peer; auto-suspend on 30-day rolling overage
- **Global quota**: warning banner when total exceeds limit
- **VPN kill switch**: stop/start the entire VPN from the sidebar
- **Live charts**: sparkline per card, modal per peer, global stacked chart at top of /peers
```

Update the `### Peer management` section to remove the "wg-quick restart on every mutation" limitation note (or update to "no longer applies to create"). Find:

```
- **`wg-quick restart` on every mutation** (create/delete/toggle) — all active peers briefly disconnect (~1s). UI warns about this only on the create form
```

Replace with:

```
- **`wg-quick restart` on delete/toggle only** — create uses `wg syncconf` for zero-downtime. Delete and toggle still cause ~1s disconnect (required to clean PostUp/iptables rules).
```

Add WhatsApp to the Downloads section:

```markdown
### Downloads & sharing
- **`.conf` file** with full WireGuard config
- **QR code** opens in elegant modal — scan with WireGuard mobile app
- **WhatsApp share** — modal asks for client phone, sends .conf as attachment via Web Share API (mobile) or download+chat (desktop)
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "$(cat <<'EOF'
docs: CHANGELOG and README for new features

Quotas, kill switch, charts, WhatsApp share, syncconf hot-reload.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Verification (post-implementation)

After all 26 tasks are complete:

- [ ] Run full test suite: `pytest -v` — all tests pass
- [ ] Check coverage: `pytest --cov=wg_admin --cov-report=term-missing` — ≥90%
- [ ] Run linter: `ruff check src/ tests/` — no errors
- [ ] Manual smoke test from `docs/smoke-test.md` — all new sections pass
- [ ] Verify install on a clean VM via `install.sh` (if you have one handy)
- [ ] Bump version in `pyproject.toml` or wherever the version lives (search `0.1.0`)
- [ ] Tag a release if appropriate

## Known limitations after this work

- syncconf only used on `create` — delete/toggle still cause ~1s downtime.
- Quota check has no hysteresis (same threshold for suspend/reactivate).
- WhatsApp desktop requires manual file attach after download.
- Web Share API requires HTTPS (already configured by install.sh).
- Kill switch via systemctl may show stale state for ~1 request after toggle.
