"""Shared minimum source coverage for commander build discovery."""

from __future__ import annotations

import math
from typing import Any


MIN_COMMANDER_DECKS = 200


def valid_deck_count(value: Any) -> int | None:
    """Return a nonnegative whole deck count; malformed counts are unknown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def validate_minimum(value: Any) -> int:
    minimum = valid_deck_count(value)
    if minimum is None or minimum < MIN_COMMANDER_DECKS:
        raise ValueError(f"Commander minimum must be a whole number of at least {MIN_COMMANDER_DECKS} decks")
    return minimum


class CommanderBelowMinimum(ValueError):
    """The current source page is valid but below the discovery threshold."""

    def __init__(self, count: int, minimum: int = MIN_COMMANDER_DECKS):
        self.count = count
        self.minimum = minimum
        super().__init__(f"Commander has {count} EDHREC decks; build discovery requires at least {minimum}")


def commander_is_eligible(value: Any, minimum: int = MIN_COMMANDER_DECKS) -> bool:
    minimum = validate_minimum(minimum)
    count = valid_deck_count(value)
    return count is not None and count >= minimum


def require_eligible_commander(value: Any, minimum: int = MIN_COMMANDER_DECKS) -> int:
    minimum = validate_minimum(minimum)
    count = valid_deck_count(value)
    if count is None:
        raise ValueError("Commander page must report a nonnegative whole deck count")
    if count < minimum:
        raise CommanderBelowMinimum(count, minimum)
    return count
