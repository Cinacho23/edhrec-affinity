"""Helpers for reading fields shared by EDHREC page JSON payloads."""

from __future__ import annotations

from typing import Any


CURRENT_DECK_COUNT_PATH = ("container", "json_dict", "card", "num_decks")
LEGACY_DECK_COUNT_FIELD = "num_decks_avg"


def _nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload

    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise KeyError(".".join(path))
        value = value[key]

    return value


def _coerce_deck_count(value: Any, source: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{source} must be an integer deck count, not bool")

    if isinstance(value, str):
        value = value.strip().replace(",", "")

    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} must be an integer deck count") from exc

    if count < 0:
        raise ValueError(f"{source} cannot be negative")

    return count


def extract_page_deck_count(payload: dict[str, Any]) -> int:
    """Return the deck count from current EDHREC JSON or its legacy field.

    EDHREC moved page deck counts from the top-level ``num_decks_avg`` field
    to ``container.json_dict.card.num_decks`` in 2026. Supporting both shapes
    keeps old fixtures and saved payloads readable while preferring the live
    schema.
    """
    if not isinstance(payload, dict):
        raise TypeError("EDHREC payload must be a dictionary")

    current_source = ".".join(CURRENT_DECK_COUNT_PATH)

    try:
        current_value = _nested_value(payload, CURRENT_DECK_COUNT_PATH)
    except KeyError:
        current_value = None
    else:
        return _coerce_deck_count(current_value, current_source)

    if LEGACY_DECK_COUNT_FIELD in payload:
        return _coerce_deck_count(
            payload[LEGACY_DECK_COUNT_FIELD],
            LEGACY_DECK_COUNT_FIELD,
        )

    raise KeyError(
        "EDHREC payload is missing both "
        f"'{current_source}' and legacy '{LEGACY_DECK_COUNT_FIELD}' deck counts"
    )
