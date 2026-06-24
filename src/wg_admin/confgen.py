# src/wg_admin/confgen.py
"""Generate peer .conf file content and QR codes."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List

import qrcode


@dataclass
class PeerConfig:
    private_key: str
    address: str
    dns: List[str] = field(default_factory=list)
    server_public_key: str = ""
    endpoint: str = ""
    allowed_ips: List[str] = field(default_factory=list)


def render_conf(peer: PeerConfig) -> str:
    """Render the .conf text for a peer."""
    lines = ["[Interface]"]
    lines.append(f"PrivateKey = {peer.private_key}")
    lines.append(f"Address = {peer.address}")
    if peer.dns:
        lines.append(f"DNS = {','.join(peer.dns)}")
    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {peer.server_public_key}")
    lines.append(f"Endpoint = {peer.endpoint}")
    lines.append(f"AllowedIPs = {','.join(peer.allowed_ips)}")
    return "\n".join(lines) + "\n"


def render_qr_png(conf_text: str) -> bytes:
    """Render conf text as a QR code PNG (bytes)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(conf_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
