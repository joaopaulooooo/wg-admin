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
