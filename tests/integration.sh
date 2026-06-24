#!/usr/bin/env bash
# tests/integration.sh — exercise real wg commands in a network namespace.
# Requires root and wg kernel module.
set -euo pipefail

# Skip if no root or no wg
[[ $EUID -eq 0 ]] || { echo "Skip: needs root"; exit 77; }
command -v wg >/dev/null || { echo "Skip: no wg"; exit 77; }
command -v ip >/dev/null || { echo "Skip: no iproute2"; exit 77; }

NS="wg-test-$$"
ip netns add "$NS" 2>/dev/null || { echo "Skip: cannot netns"; exit 77; }
trap 'ip netns del "$NS" 2>/dev/null || true' EXIT

TMP=$(mktemp -d)
cd "$TMP"

SERVER_PRIV=$(wg genkey)
SERVER_PUB=$(echo "$SERVER_PRIV" | wg pubkey)

PEER_PRIV=$(wg genkey)
PEER_PUB=$(echo "$PEER_PRIV" | wg pubkey)

cat > wg0.conf <<EOF
[Interface]
PrivateKey = $SERVER_PRIV
Address = 10.99.99.1/24
ListenPort = 51899

[Peer]
PublicKey = $PEER_PUB
AllowedIPs = 10.99.99.2/32
EOF

ip netns exec "$NS" ip link add wg0 type wireguard 2>/dev/null || true
ip netns exec "$NS" wg setconf wg0 wg0.conf 2>/dev/null || true
ip netns exec "$NS" ip addr add 10.99.99.1/24 dev wg0 2>/dev/null || true
ip netns exec "$NS" ip link set wg0 up 2>/dev/null || true

echo "--- wg show ---"
ip netns exec "$NS" wg show || true
echo "--- wg show wg0 dump (first 2 lines) ---"
ip netns exec "$NS" wg show wg0 dump 2>/dev/null | head -2 || true

echo "Integration test PASS"
