"""Audited, one-time changes to explicitly approved WebKit rate budgets."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .sources import source_time

APPROVAL_ID = "webkit-1rps-2026-11-13"
APPROVAL_TIME = datetime(2026, 11, 13, 22, 17, 9, tzinfo=timezone.utc)
WEBKIT_START_TIME = datetime(2026, 11, 13, 4, 52, 55, tzinfo=timezone.utc)
APPROVAL_EVIDENCE = "https://github.com/Cinacho23/edhrec-affinity/actions/runs/43516237383"
APPROVAL_1_5_ID = "webkit-1.5rps-2026-11-14"
APPROVAL_1_5_TIME = datetime(2026, 11, 14, 14, 44, 37, tzinfo=timezone.utc)
APPROVAL_1_5_EVIDENCE = "https://github.com/Cinacho23/edhrec-affinity/actions/runs/43604733040"

_POLICIES = {
    APPROVAL_ID: (1.0, (.1,), APPROVAL_TIME, APPROVAL_EVIDENCE),
    APPROVAL_1_5_ID: (1.5, (.1, 1.0), APPROVAL_1_5_TIME, APPROVAL_1_5_EVIDENCE),
}


def promote_initial_browser_rate(state: dict, ceiling: float, *, approval: str | None,
                                 transport: str | None, now: datetime) -> bool:
    """Apply a specific user-approved budget once; never clear source stops.

    Configured .1/1 budgets were persisted as learned_rate without a new
    throttle. Only the policy's exact prior values qualify. Any throttle since
    the WebKit migration, used approval or active/unknown stop stays effective.
    """
    policy = _POLICIES.get(approval)
    if policy is None:
        return False
    target, prior_rates, approved_at, evidence = policy
    if (transport != "webkit" or ceiling != target
            or now < approved_at or state.get("block_reason") == "access_restriction"
            or state.get("blocked") is True or state.get("source_stop")):
        return False
    expiry = source_time(state.get("blocked_until"))
    if state.get("blocked_until") is not None and (expiry is None or expiry > now):
        return False
    saved = state.get("source_rate", {})
    if not isinstance(saved, dict):
        return False
    previous_approval = saved.get("rate_approval", {})
    if (not isinstance(previous_approval, dict) or isinstance(saved.get("learned_rate"), bool)
            or saved.get("learned_rate") not in prior_rates
            or previous_approval.get("id") == approval):
        return False
    throttle = saved.get("last_throttle")
    if throttle is not None:
        at = source_time(throttle.get("at")) if isinstance(throttle, dict) else None
        if at is None or at >= WEBKIT_START_TIME:
            return False
    saved["rate_approval"] = {"id": approval, "approved_at": approved_at.isoformat(),
        "applied_at": now.isoformat(), "evidence": evidence,
        "previous_controls": deepcopy(saved)}
    saved["learned_rate"] = target
    # Throughput at the old budget is obsolete for the new budget's ETA.
    saved.pop("observed_requests_per_second", None)
    saved.pop("observed_rate_at", None)
    state["source_rate"] = saved
    return True
