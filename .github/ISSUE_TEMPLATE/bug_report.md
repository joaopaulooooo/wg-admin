---
name: Bug report
about: Report something that's broken or behaves unexpectedly
title: "[BUG] "
labels: bug
---

## What happened?

<!-- Short description of the problem -->

## What did you expect?

<!-- What you thought would happen -->

## Steps to reproduce

1.
2.
3.

## Environment

- OS / distro:
- Python version: `python3 --version`
- WireGuard version: `wg --version`
- wg-admin version (commit SHA or release): 
- Installed via: [ ] install.sh on fresh server [ ] install.sh on existing WireGuard [ ] manual

## Logs / output

```
paste relevant output from `journalctl -u wg-admin.service` or browser devtools here
```

## Config (redact secrets)

```
[wg]
interface = 
subnet = 
server_ip = 

[peer_defaults]
endpoint_host = 
endpoint_port = 
allowed_ips = 

[server]
listen_port = 
```
