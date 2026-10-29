"""Numeric EDHPowerLevel score policy for generated decks.

The repository owner supplied these site thresholds on 2026-10-27, and the
official public client confirmed ``bracketCurve:[0,4.7,6.7,7.7,9.25,10]``:
https://edhpowerlevel.com/js/main-C1lbqCDd.js. They apply to the numeric power
score, independently of card restrictions that may raise the site's reported
bracket. A playability percentage is a different measure.
"""

from __future__ import annotations

from bisect import bisect_right
import math
import re
from typing import Any


POWER_SCORE_BRACKET_THRESHOLDS = (4.7, 6.7, 7.7, 9.25)
POWER_SCORE_BRACKET_POLICY_VERSION = 2
POWER_SCORE_BRACKET_SOURCE = "https://edhpowerlevel.com/js/main-C1lbqCDd.js"
_DECIMAL_SCORE = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")


def parse_power_score(value: Any) -> float | None:
    """Read a finite nonnegative exact score; leave ranges and labels unknown.

    Keep observations such as ``10+`` available for display, but do not silently
    turn a bound, a range or prose into an exact measurement used for repairs.
    """
    if isinstance(value, str):
        value = value.strip()
        if not _DECIMAL_SCORE.fullmatch(value):
            return None
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def bracket_for_power_score(value: Any) -> int | None:
    """Return brackets 1–5; each threshold belongs to the higher bracket."""
    score = parse_power_score(value)
    return None if score is None else 1 + bisect_right(POWER_SCORE_BRACKET_THRESHOLDS, score)


def verified_score_bracket(rating: Any) -> int | None:
    """Use only a current normalized exact-list EDHPowerLevel observation.

    External validation checks the source timestamp and card-list hash before
    setting ``status=verified``. Stale or unverified observations cannot drive
    a deck repair, and an externally supplied ``score_bracket`` is never trusted.
    """
    if (not isinstance(rating, dict) or rating.get("status") != "verified"
            or rating.get("source") != "edhpowerlevel.com"):
        return None
    return bracket_for_power_score(rating.get("power_level"))
