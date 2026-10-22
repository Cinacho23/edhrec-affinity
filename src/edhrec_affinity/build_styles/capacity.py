"""Request-throughput estimates for checking the complete commander catalog.

These are request-only lower bounds. A configured rate is a ceiling, not evidence
that EDHREC, GitHub runners or the analysis pipeline sustain that throughput.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Iterable, Mapping


def _number(value, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")
    return number


def _count(value, name: str) -> int:
    number = _number(value, name)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def plan_capacity(
    index_rows: Iterable[Mapping], *, target_hours: float = 96,
    max_requests_per_second: float = 40,
    observed_requests_per_second: float | None = None,
    elapsed_seconds: float = 0, remaining_previews: int | None = None,
    profile_requests_per_commander: int = 17,
    remaining_overhead_requests: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Estimate a complete-source pass against a deadline, without sampling.

    ``total_decks`` is summed across distinct commander slugs. This counts deck
    entries, which can overlap between commanders, and is not a proven inventory
    of unique decks. Missing counts make the result explicitly incomplete.

    The default per-commander overhead allows one base page, one deck index,
    twelve tag profiles and five bracket profiles. Callers with actual remaining
    work can provide remaining previews and overhead. ``observed`` must be real
    completed network requests per wall-clock second, including idle time.
    """
    target_hours = _number(target_hours, "target_hours", positive=True)
    ceiling = _number(max_requests_per_second, "max_requests_per_second", positive=True)
    elapsed = _number(elapsed_seconds, "elapsed_seconds")
    observed = None if observed_requests_per_second is None else _number(
        observed_requests_per_second, "observed_requests_per_second")
    profile_requests = _count(profile_requests_per_commander, "profile_requests_per_commander")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    now = now.astimezone(timezone.utc)

    unique_rows = {}
    for index, row in enumerate(index_rows):
        if not isinstance(row, Mapping):
            raise ValueError("Commander inventory rows must be objects")
        slug = row.get("commander_slug")
        key = ("slug", slug) if isinstance(slug, str) and slug else ("row", index)
        unique_rows[key] = row
    total_entries = 0
    unknown_commanders = 0
    for row in unique_rows.values():
        value = row.get("total_decks")
        if value is None:
            unknown_commanders += 1
        else:
            total_entries += _count(value, "total_decks")

    overhead = len(unique_rows) * (2 + profile_requests)
    previews = total_entries if remaining_previews is None else _count(remaining_previews, "remaining_previews")
    remaining_overhead = overhead if remaining_overhead_requests is None else _count(
        remaining_overhead_requests, "remaining_overhead_requests")
    requests = previews + remaining_overhead
    remaining_window = max(0.0, target_hours * 3600 - elapsed)
    required = requests / remaining_window if remaining_window else (0.0 if not requests else None)
    preview_rate = previews / remaining_window if remaining_window else (0.0 if not previews else None)
    minimum_seconds = requests / ceiling
    measured_seconds = requests / observed if observed else (0.0 if not requests else None)
    ceiling_sufficient = not requests or (required is not None and ceiling >= required)
    measured_sufficient = None if observed is None else not requests or (required is not None and observed >= required)
    if not requests and not unknown_commanders:
        status = "complete"
    elif unknown_commanders:
        status = "inventory_incomplete"
    elif not ceiling_sufficient or measured_sufficient is False:
        status = "behind_target"
    elif observed is None:
        status = "unmeasured"
    else:
        status = "on_track"

    return {
        "schema_version": 1, "objective": "all_available_deck_entries", "evaluated_at": now.isoformat(),
        "target_hours": target_hours, "deadline_at": (now + timedelta(seconds=target_hours * 3600 - elapsed)).isoformat(),
        "commander_count": len(unique_rows), "estimated_catalog_deck_entries": total_entries,
        "inventory_basis": "sum_of_commander_total_decks", "distinct_decks_known": False,
        "inventory_complete": unknown_commanders == 0, "unknown_deck_counts": unknown_commanders,
        "overhead_requests": overhead, "profile_requests_per_commander": profile_requests,
        "remaining_previews": previews, "remaining_overhead_requests": remaining_overhead,
        "remaining_requests": requests, "elapsed_seconds": elapsed, "remaining_seconds": remaining_window,
        "minimum_required_preview_rate": preview_rate, "minimum_required_request_rate": required,
        "configured_requests_per_second": ceiling, "observed_requests_per_second": observed,
        "estimated_minimum_seconds": minimum_seconds,
        "earliest_possible_finish_at": (now + timedelta(seconds=minimum_seconds)).isoformat(),
        "measured_projection_seconds": measured_seconds,
        "projected_finish_at": (now + timedelta(seconds=measured_seconds)).isoformat() if measured_seconds is not None else None,
        "configured_capacity_sufficient": ceiling_sufficient if not unknown_commanders or not ceiling_sufficient else None,
        "measured_capacity_sufficient": measured_sufficient if not unknown_commanders or measured_sufficient is False else None,
        "status": status,
        "estimate_note": "Request-only estimate. Configured throughput is not measured capacity; analysis, latency, retries, runner gaps and source changes can extend completion.",
    }
