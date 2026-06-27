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
