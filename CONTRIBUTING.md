# Contributing to wg-admin

Thanks for considering a contribution!

## Reporting bugs

Open an issue with the **Bug report** template. Include:
- OS / distro + version
- Python version
- WireGuard version (`wg --version`)
- wg-admin commit SHA
- Output of `journalctl -u wg-admin.service --since "10 min ago"`
- Steps to reproduce

## Suggesting features

Open an issue with the **Feature request** template. Describe the problem you're trying to solve before proposing a solution.

## Development setup

```bash
git clone https://github.com/joaopaulooooo/wg-admin.git
cd wg-admin
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Before opening a PR

1. **Run the tests**: `pytest -v` — all green
2. **Run coverage**: `pytest --cov=wg_admin --cov-report=term-missing` — the project
   holds **100% coverage**; new code comes with tests for every branch, including
   failure paths. Lines that are unreachable by design (`if __name__` guards,
   dev-server entrypoint, defensive fallbacks) are marked `# pragma: no cover`
   instead of carrying fake tests — use it sparingly and say why on the line.
3. **Don't break existing behavior** — if changing a public interface, update callers and tests
4. **Keep changes focused** — one feature/fix per PR
5. **Follow existing style** — Python is formatted to PEP 8, no exotic dependencies
6. **Add tests** for new behavior
7. **Tests must be machine-independent** — never rely on files outside the test's
   own `tmp_path` (e.g. whether `/etc/wireguard/wg0.conf` happens to exist); patch
   module paths via fixtures like the existing suite does

## Project layout

```
src/wg_admin/      Python source (one responsibility per module)
tests/             pytest tests (359, 100% coverage)
templates/         Jinja2 HTML (i18n via t())
static/            CSS + JS
systemd/           systemd unit files
docs/              specs, plans, smoke test
install.sh         installer
uninstall.sh       uninstaller
```

## Design principles

- **Minimal dependencies** — stdlib first, then well-maintained libraries
- **Low footprint** — must run on 1GB RAM servers
- **Fail loudly** — better to error out than silently do the wrong thing
- **Defense in depth** — encrypt at rest, validate at boundaries, harden systemd

## Commit message style

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `refactor:` code restructure, no behavior change
- `test:` test additions
- `chore:` maintenance

Keep the subject line under 70 chars. Body explains **why**, not what.

## License

By contributing you agree that your contributions are licensed under the MIT license.
