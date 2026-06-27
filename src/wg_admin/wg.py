"""Subprocess wrappers for wg and wg-quick commands, plus conf parsing/generation."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional


def parse_wg_conf(content: str) -> dict:
    """Parse wg0.conf content. Returns {"interface": {...}, "peers": [...]}.

    Each peer dict has a "disabled" boolean. Disabled peers are those whose
    [Peer] section is prefixed with # (commented out).
    """
    result: dict = {"interface": {}, "peers": []}
    current_section: Optional[str] = None
    current_peer: Optional[dict] = None
    current_disabled = False

    def flush_peer():
        nonlocal current_peer
        if current_peer is not None:
            current_peer["disabled"] = current_disabled
            result["peers"].append(current_peer)
            current_peer = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Disabled section marker: a # followed by [Peer] (possibly with spaces)
        if line.startswith("#"):
            uncommented = line.lstrip("#").strip()
            if uncommented == "[Peer]":
                flush_peer()
                current_section = "peer"
                current_peer = {}
                current_disabled = True
                continue
            # When inside a disabled peer section, commented key=value lines
            # belong to the disabled peer.
            if current_disabled and current_peer is not None and "=" in uncommented:
                key, _, value = uncommented.partition("=")
                current_peer[key.strip()] = value.strip()
            # Other comments ignored
            continue

        # Strip inline comments (e.g. "[Peer]   # note" or "Key = v  # note")
        # but only when not inside a disabled section (disabled lines start with #).
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue

        if line == "[Interface]":
            flush_peer()
            current_section = "interface"
            current_disabled = False
            continue

        if line == "[Peer]":
            flush_peer()
            current_section = "peer"
            current_peer = {}
            current_disabled = False
            continue

        if "=" in line and current_section is not None:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if current_section == "interface":
                if key in ("PostUp", "PostDown"):
                    result["interface"].setdefault(key, []).append(value)
                else:
                    result["interface"][key] = value
            elif current_section == "peer" and current_peer is not None:
                current_peer[key] = value

    flush_peer()
    return result


def generate_wg_conf(interface: dict, peers: list) -> str:
    """Generate wg0.conf content from interface config and peer list.

    Each peer dict should have: PublicKey, AllowedIPs, disabled (bool), and
    optionally name (rendered as a comment header).
    """
    lines: list[str] = ["[Interface]"]
    for key in ("Address", "ListenPort", "PrivateKey"):
        if key in interface and interface[key]:
            lines.append(f"{key} = {interface[key]}")
    for key in ("PostUp", "PostDown"):
        for value in interface.get(key, []):
            lines.append(f"{key} = {value}")

    for peer in peers:
        lines.append("")
        prefix = "# " if peer.get("disabled") else ""
        if peer.get("name"):
            lines.append(f"{prefix}# name: {peer['name']}")
        lines.append(f"{prefix}[Peer]")
        lines.append(f"{prefix}PublicKey = {peer['PublicKey']}")
        lines.append(f"{prefix}AllowedIPs = {peer['AllowedIPs']}")

    return "\n".join(lines) + "\n"


@dataclass
class PeerStatus:
    public_key: str
    endpoint: Optional[str]
    allowed_ips: List[str]
    latest_handshake: int
    transfer_rx: int
    transfer_tx: int


def parse_wg_show_dump(output: str, interface: str = "wg0") -> List[PeerStatus]:
    """Parse `wg show <interface> dump` output.

    Real format (per `man wg`):
      Interface line: ifname  privkey  pubkey  listenport  fwmark         (5 fields)
      Peer line:      ifname  pubkey  psk  endpoint  allowed-ips  handshake  rx  tx  keepalive  (9 fields)

    Older/legacy format (some wg versions):
      Peer line:      ifname  pubkey  endpoint  allowed-ips  handshake  rx  tx  keepalive  (8 fields)

    This parser auto-detects field count and indexes accordingly.
    """
    peers: List[PeerStatus] = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        # Peer line has 8 (legacy) or 9 (modern with PSK) fields. Interface has 5-6.
        if len(parts) < 8:
            continue
        # Modern format: parts[2] is PSK, parts[3] is endpoint
        # Legacy format:  parts[2] is endpoint
        if len(parts) >= 9:
            pubkey = parts[1]
            endpoint = parts[3] if parts[3] != "(none)" else None
            allowed_ips_field = parts[4]
            handshake_field = parts[5]
            rx_field = parts[6]
            tx_field = parts[7]
        else:
            # 8 fields, legacy
            pubkey = parts[1]
            endpoint = parts[2] if parts[2] != "(none)" else None
            allowed_ips_field = parts[3]
            handshake_field = parts[4]
            rx_field = parts[5]
            tx_field = parts[6]

        # Skip interface line that might have leaked through (pubkey contains "="
        # but parts[1] of interface line is private key, also "=" — check handshake
        # is a number to confirm it's a peer line)
        try:
            latest_handshake = int(handshake_field) if handshake_field else 0
        except ValueError:
            continue  # not a peer line

        try:
            transfer_rx = int(rx_field) if rx_field else 0
            transfer_tx = int(tx_field) if tx_field else 0
        except ValueError:
            continue

        peers.append(PeerStatus(
            public_key=pubkey,
            endpoint=endpoint,
            allowed_ips=allowed_ips_field.split(",") if allowed_ips_field else [],
            latest_handshake=latest_handshake,
            transfer_rx=transfer_rx,
            transfer_tx=transfer_tx,
        ))
    return peers


def wg_genkey() -> tuple[str, str]:
    """Run `wg genkey` then pipe to `wg pubkey`. Returns (priv, pub)."""
    priv_proc = subprocess.run(
        ["wg", "genkey"], capture_output=True, text=True, check=True
    )
    priv = priv_proc.stdout.strip()
    pub_proc = subprocess.run(
        ["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True
    )
    return priv, pub_proc.stdout.strip()


def wg_show_dump(interface: str = "wg0") -> List[PeerStatus]:
    """Run `wg show <interface> dump` and return parsed statuses."""
    proc = subprocess.run(
        ["wg", "show", interface, "dump"],
        capture_output=True, text=True, check=True,
    )
    return parse_wg_show_dump(proc.stdout, interface)


def wg_quick_restart(interface: str = "wg0") -> None:
    """Restart wg-quick service. Raises CalledProcessError on failure."""
    subprocess.run(
        ["systemctl", "restart", f"wg-quick@{interface}"],
        capture_output=True, text=True, check=True,
    )


def wg_server_public_key(interface: str = "wg0") -> str:
    """Return the server's own public key via `wg show <interface> public-key`."""
    proc = subprocess.run(
        ["wg", "show", interface, "public-key"],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def wg_server_public_key_from_conf(conf_path: str = "/etc/wireguard/wg0.conf") -> str:
    """Read PrivateKey from wg0.conf and derive PublicKey via `wg pubkey`.

    Fallback used when wg0 interface is not yet up (e.g. during install).
    """
    from pathlib import Path
    content = Path(conf_path).read_text()
    parsed = parse_wg_conf(content)
    priv = parsed["interface"].get("PrivateKey", "").strip()
    if not priv:
        return ""
    proc = subprocess.run(
        ["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()
