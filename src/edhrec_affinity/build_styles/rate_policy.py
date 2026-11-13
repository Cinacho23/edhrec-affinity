"""Audited, one-time promotion of the initial WebKit collection budget."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .sources import source_time

APPROVAL_ID = "webkit-1rps-2026-11-13"
APPROVAL_TIME = datetime(2026, 11, 13, 22, 17, 9, tzinfo=timezone.utc)
WEBKIT_START_TIME = datetime(2026, 11, 13, 4, 52, 55, tzinfo=timezone.utc)
APPROVAL_EVIDENCE = "https://github.com/Cinacho23/edhrec-affinity/actions/runs/43516237383"


def promote_initial_browser_rate(state: dict, ceiling: float, *, approval: str | None,
                                 transport: str | None, now: datetime) -> bool:
    """Apply the user's tested 1-RPS approval once; never clear source stops.

    The initial .1 budget was persisted as learned_rate even without a new
    throttle. Only that exact legacy value qualifies. A newer throttle, an
    already-used approval, or any active/unknown stop preserves the old limit.
    """
    if (approval != APPROVAL_ID or transport != "webkit" or ceiling != 1
            or now < APPROVAL_TIME or state.get("block_reason") == "access_restriction"
            or state.get("blocked") is True or state.get("source_stop")):
        return False
    expiry = source_time(state.get("blocked_until"))
    if state.get("blocked_until") is not None and (expiry is None or expiry > now):
        return False
    saved = state.get("source_rate", {})
    if not isinstance(saved, dict):
        return False
    previous_approval = saved.get("rate_approval", {})
    if (not isinstance(previous_approval, dict) or saved.get("learned_rate") != .1
            or previous_approval.get("id") == APPROVAL_ID):
        return False
    throttle = saved.get("last_throttle")
    if throttle is not None:
        at = source_time(throttle.get("at")) if isinstance(throttle, dict) else None
        if at is None or at >= WEBKIT_START_TIME:
            return False
    saved["rate_approval"] = {"id": APPROVAL_ID, "approved_at": APPROVAL_TIME.isoformat(),
        "applied_at": now.isoformat(), "evidence": APPROVAL_EVIDENCE,
        "previous_controls": deepcopy(saved)}
    saved["learned_rate"] = 1.0
    # Throughput at .1 is obsolete for the new operating budget's ETA.
    saved.pop("observed_requests_per_second", None)
    saved.pop("observed_rate_at", None)
    state["source_rate"] = saved
    return True
