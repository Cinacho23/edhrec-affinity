"""Shared, exact-name Scryfall metadata for sampled deck card pools.

The collection API accepts at most 75 identifiers in a POST. This module keeps
successful Oracle metadata across commanders; it never substitutes fuzzy names
or caches negative lookups. Source: https://scryfall.com/docs/api/cards/collection
"""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterable

import httpx

from edhrec_affinity.tag_scraper import DEFAULT_USER_AGENT
from .sources import card_key


COLLECTION_URL = "https://api.scryfall.com/cards/collection"
DEFAULT_CACHE_PATH = Path("data/raw/build-style-card-metadata.json")
COLLECTION_BATCH_SIZE = 75
REQUEST_DELAY_SECONDS = 0.1
CACHE_MAX_AGE_DAYS = 7
_LEGALITIES = {"legal", "not_legal", "restricted", "banned"}
# The front of these cards identifies the playable card. A split/aftermath half
# is deliberately not accepted as an alias for its full card name.
_FRONT_FACE_LAYOUTS = {"modal_dfc", "transform", "flip", "adventure", "prepare"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Missing metadata check timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Metadata timestamp must include a timezone")
    return value


def _stale(card: dict[str, Any], now: datetime, max_age_days: float = CACHE_MAX_AGE_DAYS) -> bool:
    checked = datetime.fromisoformat(card["checked_at"].replace("Z", "+00:00"))
    age_seconds = (now - checked).total_seconds()
    # A future timestamp cannot establish freshness either.
    return age_seconds < 0 or age_seconds >= max_age_days * 86400


def _strict_json(text: str) -> Any:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"Nonfinite JSON number: {value}")

    data = json.loads(text, parse_constant=invalid_constant)
    # json.loads also accepts an overflowing exponent, for example 1e999.
    json.dumps(data, allow_nan=False)
    return data


def _string(value: Any, field: str, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"Invalid Scryfall {field}")
    return value


def _metadata(raw: Any, checked_at: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("object", "card") != "card":
        raise ValueError("Expected a Scryfall card object")
    colors = raw.get("color_identity")
    legalities = raw.get("legalities")
    keywords = raw.get("keywords", [])
    cmc = raw.get("cmc")
    if not isinstance(colors, list) or any(c not in "WUBRG" or len(c) != 1 for c in colors if isinstance(c, str)):
        raise ValueError("Invalid Scryfall color identity")
    if any(not isinstance(c, str) for c in colors) or len(set(colors)) != len(colors):
        raise ValueError("Invalid Scryfall color identity")
    if not isinstance(legalities, dict) or legalities.get("commander") not in _LEGALITIES:
        raise ValueError("Missing Scryfall Commander legality")
    if any(not isinstance(k, str) or value not in _LEGALITIES for k, value in legalities.items()):
        raise ValueError("Invalid Scryfall legalities")
    if not isinstance(keywords, list) or any(not isinstance(k, str) for k in keywords):
        raise ValueError("Invalid Scryfall keywords")
    if isinstance(cmc, bool) or not isinstance(cmc, (int, float)) or not math.isfinite(cmc) or cmc < 0:
        raise ValueError("Invalid Scryfall mana value")
    faces = raw.get("card_faces", [])
    if not isinstance(faces, list):
        raise ValueError("Invalid Scryfall card faces")
    normalized_faces = []
    for face in faces:
        if not isinstance(face, dict):
            raise ValueError("Invalid Scryfall card face")
        normalized_faces.append({
            "name": _string(face.get("name"), "face name", empty=False),
            "type_line": _string(face.get("type_line", ""), "face type line"),
            "oracle_text": _string(face.get("oracle_text", ""), "face Oracle text"),
            "mana_cost": _string(face.get("mana_cost", ""), "face mana cost"),
            **{field: _string(face[field], f"face {field}")
               for field in ("power", "toughness") if field in face},
        })
    game_changer = raw.get("game_changer")
    if game_changer is not None and not isinstance(game_changer, bool):
        raise ValueError("Invalid Scryfall game changer flag")
    produced_mana = raw.get("produced_mana", [])
    if not isinstance(produced_mana, list) or any(
        not isinstance(symbol, str) or symbol not in {"W", "U", "B", "R", "G", "C"}
        for symbol in produced_mana
    ):
        raise ValueError("Invalid Scryfall produced mana")
    return {
        "name": _string(raw.get("name"), "name", empty=False),
        "type_line": _string(raw.get("type_line"), "type line", empty=False),
        "oracle_text": _string(raw.get("oracle_text", ""), "Oracle text"),
        "color_identity": list(colors),
        "legalities": dict(legalities),
        "keywords": list(keywords),
        "mana_cost": _string(raw.get("mana_cost", ""), "mana cost"),
        "cmc": cmc,
        "card_faces": normalized_faces,
        "layout": _string(raw.get("layout"), "layout", empty=False),
        "game_changer": game_changer,
        "produced_mana": list(produced_mana),
        **{field: _string(raw[field], field, empty=False)
           for field in ("oracle_id", "scryfall_uri", "power", "toughness") if field in raw},
        "checked_at": _timestamp(checked_at),
    }


def _aliases(card: dict[str, Any]) -> set[str]:
    keys = {card_key(card["name"])}
    if card["layout"] in _FRONT_FACE_LAYOUTS and card["card_faces"]:
        keys.add(card_key(card["card_faces"][0]["name"]))
    return keys


def _index(cards: dict[str, dict]) -> dict[str, dict]:
    """Prefer full card names, and omit aliases shared by different cards."""
    result = dict(cards)
    candidates: dict[str, list[dict]] = {}
    for canonical, card in cards.items():
        for alias in _aliases(card) - {canonical}:
            candidates.setdefault(alias, []).append(card)
    for alias, matches in candidates.items():
        if alias not in result and len(matches) == 1:
            result[alias] = matches[0]
    return result


def _identifier_name(name: str) -> str:
    # Collection identifiers resolve split/Room cards by the first printed name,
    # while returned metadata retains the complete canonical "front // back".
    # Validation below still compares that full identity to the original input;
    # this does not make split-card halves aliases in the returned mapping.
    return name.split(" // ", 1)[0]


def _read_cache(path: Path) -> dict[str, dict]:
    raw = _strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("cards"), dict):
        raise ValueError("Unrecognized Scryfall metadata cache")
    _timestamp(raw.get("updated_at"))
    result = {}
    for key, value in raw["cards"].items():
        if not isinstance(value, dict):
            raise ValueError("Invalid cached Scryfall card")
        card = _metadata(value, value.get("checked_at"))
        if key != card_key(card["name"]):
            raise ValueError("Cached Scryfall key does not match its card name")
        result[key] = card
    return result


def _write_cache(path: Path, cards: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = json.dumps({"schema_version": 1, "source_url": COLLECTION_URL,
                           "updated_at": _now(), "cards": cards}, allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _collection_cards(
    payload: Any, batch: list[str], checked_at: str,
) -> tuple[dict[str, dict], list[str], list[dict]]:
    if not isinstance(payload, dict) or payload.get("object") != "list" or not isinstance(payload.get("data"), list):
        raise ValueError("Expected a Scryfall collection list")
    if payload.get("has_more"):
        raise ValueError("Unexpected paginated Scryfall collection response")
    not_found = payload.get("not_found", [])
    if not isinstance(not_found, list):
        raise ValueError("Invalid Scryfall not_found list")
    requested = {card_key(name): name for name in batch}
    identifier_names: dict[str, list[str]] = {}
    for name in batch:
        identifier_names.setdefault(card_key(_identifier_name(name)), []).append(name)
    missing = []
    for identifier in not_found:
        if not isinstance(identifier, dict) or not isinstance(identifier.get("name"), str):
            raise ValueError("Invalid missing Scryfall name")
        key = card_key(identifier["name"])
        matches = identifier_names.get(key) or ([requested[key]] if key in requested else [])
        if not matches:
            raise ValueError("Scryfall returned an unrequested missing name")
        missing.extend(name for name in matches if name not in missing)
    cards, rejected = {}, []
    for raw in payload["data"]:
        try:
            card = _metadata(raw, checked_at)
        except (ValueError, TypeError) as error:
            rejected.append({"returned_name": raw.get("name") if isinstance(raw, dict) else None,
                             "aliases": [], "error": str(error)})
            continue
        if not _aliases(card).intersection(requested):
            aliases = sorted(_aliases(card))
            rejected.append({"returned_name": card["name"], "aliases": aliases,
                             "error": f"Returned card {card['name']!r} (aliases: {aliases!r}) does not exactly match any request"})
            continue
        cards[card_key(card["name"])] = card
    resolved = _index(cards)
    missing_keys = {card_key(name) for name in missing}
    if any(key in resolved for key in missing_keys):
        raise ValueError("Scryfall collection both resolved and rejected an identifier")
    unresolved = [name for key, name in requested.items() if key not in resolved and key not in missing_keys]
    failures = []
    if unresolved or rejected:
        details = "; ".join(card["error"] for card in rejected)
        failures.append({"names": unresolved,
                         "error": details or "Scryfall collection omitted a requested identifier",
                         "returned_cards": rejected})
    return cards, missing, failures


def _requested_names(names: Iterable[str]) -> dict[str, str]:
    if isinstance(names, (str, bytes)):
        raise ValueError("Card names must be an iterable of names")
    requested = {}
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Card names must be nonempty strings")
        requested.setdefault(card_key(name), name.strip())
    return requested


class MetadataRepository:
    """Resolve many commander pools using one thread-safe in-memory cache.

    The cache is parsed and indexed once. Concurrent callers share exact
    matches and one serialized Scryfall request stream, including its pacing
    and access-stop state. Returned records are independent of the repository
    so deck analysis cannot contaminate another commander's metadata.
    """

    def __init__(self, cache_path: Path | str = DEFAULT_CACHE_PATH, *, max_age_days: float = 3):
        if (isinstance(max_age_days, bool) or not isinstance(max_age_days, (int, float))
                or not math.isfinite(max_age_days) or max_age_days <= 0):
            raise ValueError("Metadata maximum age must be a positive finite number of days")
        self.cache_path = Path(cache_path)
        self.max_age_days = max_age_days
        self._lock = threading.RLock()
        self._loaded = False
        self._cards: dict[str, dict] = {}
        self._lookup: dict[str, dict] = {}
        self._cache_error: str | None = None
        self._blocked_status: int | None = None

    @property
    def blocked(self) -> bool:
        with self._lock:
            return self._blocked_status is not None

    def _read_once(self) -> None:
        if self._loaded:
            return
        try:
            self._cards = _read_cache(self.cache_path)
        except FileNotFoundError:
            pass
        except (ValueError, OSError, TypeError) as error:
            self._cache_error = str(error)
        self._lookup = _index(self._cards)
        self._loaded = True

    def load(self, names: Iterable[str], *, fetch: bool = False) -> tuple[dict[str, dict], dict[str, Any]]:
        requested = _requested_names(names)
        with self._lock:
            self._read_once()
            return self._load(requested, fetch=fetch)

    def _load(self, requested: dict[str, str], *, fetch: bool) -> tuple[dict[str, dict], dict[str, Any]]:
        summary: dict[str, Any] = {
            "source_url": COLLECTION_URL, "checked_at": _now(), "network_enabled": fetch,
            "requested": len(requested), "cached": 0, "fetched": 0, "requests": 0,
            "resolved": 0, "missing": [], "not_found": [], "failures": [], "blocked": self.blocked,
            "cache_max_age_days": self.max_age_days, "stale_cached": 0,
            "stale_names": [], "oldest_checked_at": None,
        }
        if self._cache_error is not None:
            summary["cache_error"] = self._cache_error
        now = datetime.now(timezone.utc)
        pending = [name for key, name in requested.items()
                   if key not in self._lookup or _stale(self._lookup[key], now, self.max_age_days)]
        refreshed_keys: set[str] = set()
        if fetch and pending and self.blocked:
            summary["failures"].append({
                "names": pending, "status_code": self._blocked_status,
                "error": "Repository paused after an access or rate limit response; no request was retried",
            })
        elif fetch and pending:
            with httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT,
                                       "Accept": "application/json"}, timeout=30,
                              follow_redirects=False) as client:
                for start in range(0, len(pending), COLLECTION_BATCH_SIZE):
                    # A previous batch can resolve an alias in a subsequent batch.
                    batch_now = datetime.now(timezone.utc)
                    batch = [name for name in pending[start:start + COLLECTION_BATCH_SIZE]
                             if card_key(name) not in self._lookup
                             or _stale(self._lookup[card_key(name)], batch_now, self.max_age_days)]
                    if not batch:
                        continue
                    # The lock keeps this delay global across repository users,
                    # including the first request of the following commander.
                    time.sleep(REQUEST_DELAY_SECONDS)
                    summary["requests"] += 1
                    try:
                        response = client.post(COLLECTION_URL,
                                               json={"identifiers": [{"name": _identifier_name(name)} for name in batch]})
                        if response.status_code in {401, 403, 429}:
                            self._blocked_status = response.status_code
                            summary["blocked"] = True
                            summary["failures"].append({"names": batch, "status_code": response.status_code,
                                                        "error": "Access or rate limit response; collection stopped without retry"})
                            break
                        response.raise_for_status()
                        fresh, missing, rejected = _collection_cards(_strict_json(response.text), batch, _now())
                        if fresh:
                            self._cards.update(fresh)
                            self._lookup = _index(self._cards)
                            # Preserve completed batches if a later request fails.
                            _write_cache(self.cache_path, self._cards)
                            self._cache_error = None
                            refreshed_keys.update(key for key in _index(fresh) if key in requested)
                        summary["not_found"].extend(missing)
                        summary["failures"].extend(rejected)
                    except (httpx.HTTPError, ValueError, OSError, TypeError) as error:
                        summary["failures"].append({"names": batch, "error": str(error)})
                        break
        # Resolve just this pool, without scanning the complete shared cache
        # again for every commander. Preserve safe full/front-name aliases.
        result = {}
        for key in requested:
            card = self._lookup.get(key)
            if card is not None:
                for alias in _aliases(card):
                    if self._lookup.get(alias) is card:
                        result[alias] = card
        summary["resolved"] = sum(key in result for key in requested)
        summary["fetched"] = sum(key in result for key in refreshed_keys)
        summary["cached"] = summary["resolved"] - summary["fetched"]
        summary["missing"] = [name for key, name in requested.items() if key not in result]
        final_now = datetime.now(timezone.utc)
        summary["stale_names"] = [name for key, name in requested.items()
                                  if key in result and _stale(result[key], final_now, self.max_age_days)]
        summary["stale_cached"] = len(summary["stale_names"])
        checked_dates = [card["checked_at"] for card in result.values()]
        if checked_dates:
            summary["oldest_checked_at"] = min(checked_dates, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
        return copy.deepcopy(result), summary


def load_card_metadata(
    names: Iterable[str], cache_path: Path | str = DEFAULT_CACHE_PATH, *, fetch: bool = False,
) -> tuple[dict[str, dict], dict[str, Any]]:
    """Resolve one pool with the legacy seven-day freshness policy.

    For catalog collection, reuse a ``MetadataRepository`` across commanders.
    Offline lookups and failed refreshes retain their original dated metadata;
    unresolved and stale input names remain explicit in the summary.
    """
    return MetadataRepository(cache_path, max_age_days=CACHE_MAX_AGE_DAYS).load(names, fetch=fetch)
