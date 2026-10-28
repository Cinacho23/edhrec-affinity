"""Attach dated human-observed ratings to an exact generated card list.

This module performs no external requests. A changed deck cannot inherit a
previous list's rating, even if it retains the same commander or package name.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from .power_brackets import bracket_for_power_score
from .sources import card_key


DEFAULT_VALIDATION_DIRECTORY = Path(__file__).resolve().parents[3] / "data/build-styles/validations"
VALIDATION_MAX_AGE_DAYS = 7
FUTURE_TOLERANCE_SECONDS = 300
SOURCE = "edhpowerlevel.com"


def _card_counts(cards: Iterable[dict]) -> Counter:
    if isinstance(cards, (str, bytes, dict)):
        raise ValueError("Expected a list of cards")
    counts: Counter = Counter()
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get("name"), str) or not card["name"].strip():
            raise ValueError("Cards must have nonempty names")
        quantity = card.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError("Card quantities must be positive integers")
        counts[card_key(card["name"])] += quantity
    if not counts:
        raise ValueError("Card list is empty")
    return counts


def cardlist_hash(cards: Iterable[dict]) -> str:
    """Hash quantities and normalized full names, independent of order or tags."""
    counts = _card_counts(cards)
    canonical = [[counts[name], name] for name in sorted(counts)]
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deck_identity(deck: dict) -> tuple[str, int]:
    if not isinstance(deck, dict) or not isinstance(deck.get("cards"), list):
        raise ValueError("Deck has no card list")
    counts = _card_counts(deck["cards"])
    count = sum(counts.values())
    reported = deck.get("card_count", count)
    if isinstance(reported, bool) or not isinstance(reported, int) or reported != count or count != 100:
        raise ValueError("External validation requires the exact complete 100-card list")
    return cardlist_hash(deck["cards"]), count


def _strict_json(contents: str) -> Any:
    def invalid(value: str) -> None:
        raise ValueError(f"Nonfinite JSON number: {value}")

    value = json.loads(contents, parse_constant=invalid)
    json.dumps(value, allow_nan=False)
    return value


def _check_bracket_restrictions(value: Any, deck_names: set[str]) -> dict:
    """Validate optional observed restrictions against the exact checked list."""
    single_keys = {"game_changers", "extra_turns", "mass_land_denial"}
    combo_keys = {"early_combos", "late_combos"}
    if not isinstance(value, dict) or set(value) - single_keys - combo_keys:
        raise ValueError("Invalid external bracket restriction categories")

    def names(items: Any, *, combo: bool = False) -> list[str]:
        if not isinstance(items, list):
            raise ValueError("External bracket restrictions must list full card names")
        cleaned, seen = [], set()
        for name in items:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("External bracket restrictions must list full card names")
            key = card_key(name)
            if key not in deck_names:
                raise ValueError("External bracket restriction names a card outside the checked list")
            if key in seen:
                raise ValueError("External bracket restriction contains duplicate card names")
            seen.add(key)
            cleaned.append(name.strip())
        if combo and len(cleaned) < 2:
            raise ValueError("External combo restrictions need at least two distinct cards")
        return cleaned

    result = {}
    for key, entries in value.items():
        if key in single_keys:
            result[key] = names(entries)
        else:
            if not isinstance(entries, list):
                raise ValueError("External combo restrictions must list card groups")
            result[key] = [names(group, combo=True) for group in entries]
    return result


def _check_record(record: Any, sha256: str, count: int, deck_names: set[str]) -> dict:
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        raise ValueError("Unrecognized external validation record")
    if record.get("deck_sha256") != sha256:
        raise ValueError("External rating belongs to another card list")
    if type(record.get("card_count")) is not int or record["card_count"] != count or count != 100:
        raise ValueError("External rating must describe all 100 cards")
    if record.get("source") != SOURCE:
        raise ValueError("Unexpected external rating source")
    checked_at = record.get("checked_at")
    if not isinstance(checked_at, str):
        raise ValueError("External rating needs a dated observation")
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise ValueError("External rating timestamp must include a timezone")
    now = datetime.now(timezone.utc)
    if checked > now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS):
        raise ValueError("External rating timestamp is in the future")
    playability = record.get("playability_pct")
    if (isinstance(playability, bool) or not isinstance(playability, (int, float))
            or not math.isfinite(playability) or not 0 <= playability <= 100):
        raise ValueError("Invalid external playability percentage")
    bracket = record.get("bracket")
    if type(bracket) is not int or not 1 <= bracket <= 5:
        raise ValueError("Invalid external bracket")
    source_url = record.get("source_url")
    if not isinstance(source_url, str):
        raise ValueError("External rating needs its source URL")
    parsed = urlparse(source_url)
    if (parsed.scheme != "https" or parsed.hostname != SOURCE or parsed.username is not None
            or parsed.password is not None or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"} or not parse_qs(parsed.query).get("d")):
        raise ValueError("External rating URL must be an EDHPowerLevel deck preload")
    result = {"schema_version": 1, "deck_sha256": sha256, "source": SOURCE,
              "checked_at": checked_at, "card_count": count,
              "playability_pct": playability, "bracket": bracket, "source_url": source_url}
    if "power_level" in record:
        power = record["power_level"]
        if isinstance(power, str):
            if not power.strip():
                raise ValueError("Invalid external power level")
        elif isinstance(power, bool) or not isinstance(power, (int, float)) or not math.isfinite(power):
            raise ValueError("Invalid external power level")
        result["power_level"] = power
        # Preserve the site's reported bracket separately: card restrictions
        # can raise it above the numeric score's bracket. Always derive this
        # field again rather than trusting a value supplied in the record.
        score_bracket = bracket_for_power_score(power)
        if score_bracket is not None:
            result["score_bracket"] = score_bracket
    if "bracket_restrictions" in record:
        result["bracket_restrictions"] = _check_bracket_restrictions(record["bracket_restrictions"], deck_names)
    result["status"] = "stale" if now - checked >= timedelta(days=VALIDATION_MAX_AGE_DAYS) else "verified"
    return result


def attach_external_validation(
    deck: dict, directory: Path | str = DEFAULT_VALIDATION_DIRECTORY,
) -> dict:
    """Attach an exact-list record, or an explicit unverified state; return it."""
    rating = {"status": "unverified", "source": SOURCE}
    try:
        sha256, count = _deck_identity(deck)
        path = Path(directory) / f"{sha256}.json"
        if path.exists():
            rating = _check_record(_strict_json(path.read_text(encoding="utf-8")), sha256, count,
                                   set(_card_counts(deck["cards"])))
    except (ValueError, TypeError, OSError) as error:
        rating["reason"] = str(error)
    deck.setdefault("validation", {})["external_rating"] = rating
    return rating


def record_external_validation(
    deck: dict, values: dict, directory: Path | str = DEFAULT_VALIDATION_DIRECTORY,
) -> dict:
    """Persist supplied observations atomically, then attach the checked record.

    The caller supplies the actual checked_at, card_count, source, source_url,
    playability_pct and bracket. This helper never generates a rating itself.
    """
    if not isinstance(values, dict):
        raise ValueError("Expected external validation observations")
    sha256, count = _deck_identity(deck)
    record = {"schema_version": 1, "deck_sha256": sha256, **values}
    checked = _check_record(record, sha256, count, set(_card_counts(deck["cards"])))
    stored = {key: value for key, value in checked.items() if key != "status"}
    destination = Path(directory) / f"{sha256}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".rating-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(stored, file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    deck.setdefault("validation", {})["external_rating"] = checked
    return checked
