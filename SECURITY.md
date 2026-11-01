# Security Policy

## Supported versions

Only the latest release line (`main` / newest tag) receives security fixes.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's **private vulnerability reporting** instead:

1. Go to the repository's **Security** tab → **Report a vulnerability**.

Alternatively, open a [security advisory draft](https://github.com/joaopaulooooo/wg-admin/security/advisories/new).

Include when possible: affected version/commit, steps to reproduce, impact
assessment, and whether you plan to disclose publicly. You should receive a
response within 72 hours.

## Scope

wg-admin is a single-admin WireGuard panel designed to run on a dedicated
machine as root (required for `wg`/`wg-quick`/`systemctl`). The threat model
(documented in `docs/superpowers/specs/2026-06-17-wg-admin-design.md` §7)
explicitly assumes:

- A compromised admin password means full panel access (no 2FA).
- A compromised server root is game over.

Out of scope: attacks requiring root on the server, physical access, or
zero-days in upstream dependencies themselves.
