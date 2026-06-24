# wg-admin Smoke Test

After a fresh install, run through this checklist:

1. **Login page loads** — visit `https://<host>:<port>/`, see login form.
2. **Wrong password rejected** — submit empty/wrong password, see "Credenciais inválidas".
3. **Correct password works** — submit the admin password set during install, get redirected to `/peers`.
4. **Existing peers visible** — the 3 peers imported from `/etc/wireguard/wg0.conf` appear in the list.
5. **Create a new peer** — click "+ Novo peer", fill name "test-peer", submit. See success message and new peer at the top.
6. **Verify in wg** — on the server: `wg show wg0` — should show 4 peers now.
7. **Download .conf** — click ".conf" on the new peer. Get a file with `[Interface]` and `[Peer]` sections.
8. **Download QR** — click "QR" — get a PNG that scans correctly in the WireGuard mobile app.
9. **Import .conf on a client** — actual WG client should be able to connect.
10. **Toggle disabled** — click "Desativar" on a peer. Verify in `wg show` that the peer is gone (config rewritten without it).
11. **Toggle enabled** — click "Ativar" — peer returns.
12. **Delete a peer** — confirm dialog, then verify in `wg show` it's removed.
13. **Rate limit** — submit wrong password 5 times rapidly. 6th attempt should return 429.
14. **CSRF** — open browser dev tools, manually submit a form without csrf_token, get 403.
15. **Logs** — `journalctl -u wg-admin.service -f` should show actions being logged.

If any of these fail, do not ship.
