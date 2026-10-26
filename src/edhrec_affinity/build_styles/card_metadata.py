"""Shared, exact-name Scryfall metadata for sampled deck card pools.

The collection API accepts at most 75 identifiers in a POST. Unresolved names
fall back to an EDHREC-supplied printing UUID, Scryfall's exact named endpoint,
then explicitly supplied EDHREC individual-card equivalents. This module keeps
successful Oracle metadata and verified English alternate-printing names across
commanders; it never uses fuzzy
names or caches negative lookups. Source: https://scryfall.com/docs/api/cards/collection

Non-deck objects such as Planes can share an exact name with a main-deck card.
Only those intrinsically non-deck layouts trigger a complete, exact-name
search for a unique physical Oracle identity. Normal banned cards remain
authoritative banned observations. Search: https://scryfall.com/docs/api/cards/search
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
from uuid import UUID

import httpx

from edhrec_affinity.tag_scraper import DEFAULT_USER_AGENT
from .sources import card_key


COLLECTION_URL = "https://api.scryfall.com/cards/collection"
NAMED_URL = "https://api.scryfall.com/cards/named"
SEARCH_URL = "https://api.scryfall.com/cards/search"
CARD_URL = "https://api.scryfall.com/cards"
DEFAULT_CACHE_PATH = Path("data/raw/build-style-card-metadata.json")
COLLECTION_BATCH_SIZE = 75
REQUEST_DELAY_SECONDS = 0.1
CACHE_MAX_AGE_DAYS = 7
_LEGALITIES = {"legal", "not_legal", "restricted", "banned"}
# The front of these cards identifies the playable card. A split/aftermath half
# is deliberately not accepted as an alias for its full card name.
_FRONT_FACE_LAYOUTS = {"modal_dfc", "transform", "flip", "adventure", "prepare"}
_PRINTED_NAME_FIELDS = {"flavor_name", "printed_name"}
# These objects cannot be cards in a Commander main deck, irrespective of
# their reported format legality. Normal banned/not-legal cards remain valid
# metadata observations and must never trigger a search for a legal substitute.
_NON_DECK_LAYOUTS = frozenset({
    "token", "double_faced_token", "emblem", "art_series", "planar", "scheme", "vanguard", "front_card",
})


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
            **{field: _string(face[field], f"face {field}", empty=False)
               for field in _PRINTED_NAME_FIELDS if field in face},
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
    result = {
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
           for field in ("id", "lang", "oracle_id", "scryfall_uri", "power", "toughness",
                         "flavor_name", "printed_name") if field in raw},
        "checked_at": _timestamp(checked_at),
    }
    # A printing can have a different displayed name while sharing the same
    # Oracle identity. Preserve the exact English printing evidence separately
    # so refreshing to a regular printing cannot erase a verified alternate name.
    proofs = raw.get("name_aliases", [])
    if not isinstance(proofs, list):
        raise ValueError("Invalid Scryfall name alias evidence")
    validated = []
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != {
            "name", "source_field", "printing_id", "lang", "oracle_id", "canonical_name",
        }:
            raise ValueError("Invalid Scryfall name alias evidence")
        if any(not isinstance(value, str) or not value.strip() for value in proof.values()):
            raise ValueError("Invalid Scryfall name alias evidence")
        if (proof["source_field"] not in _PRINTED_NAME_FIELDS or proof["lang"] != "en"
                or proof["oracle_id"] != result.get("oracle_id")
                or card_key(proof["canonical_name"]) != card_key(result["name"])):
            raise ValueError("Scryfall name alias evidence has a different card identity")
        if proof not in validated:
            validated.append(dict(proof))
    if result.get("id") and result.get("oracle_id") and result.get("lang") == "en":
        sources = [result]
        if result["layout"] in _FRONT_FACE_LAYOUTS and normalized_faces:
            sources.append(normalized_faces[0])
        for source in sources:
            for field in sorted(_PRINTED_NAME_FIELDS):
                if field in source:
                    proof = {"name": source[field], "source_field": field,
                             "printing_id": result["id"], "lang": "en",
                             "oracle_id": result["oracle_id"], "canonical_name": result["name"]}
                    if proof not in validated:
                        validated.append(proof)
    if validated:
        result["name_aliases"] = validated
    return result


def metadata_aliases(card: dict[str, Any]) -> set[str]:
    """Strict known names, including verified English alternate printings.

    Callers supply normalized metadata, never arbitrary input-name mappings.
    Split/Room halves and back faces are deliberately excluded.
    """
    keys = {card_key(card["name"])}
    if card["layout"] in _FRONT_FACE_LAYOUTS and card["card_faces"]:
        keys.add(card_key(card["card_faces"][0]["name"]))
    for proof in card.get("name_aliases", []):
        if (proof.get("oracle_id") == card.get("oracle_id") and proof.get("lang") == "en"
                and proof.get("source_field") in _PRINTED_NAME_FIELDS
                and card_key(proof.get("canonical_name", "")) == card_key(card["name"])
                and isinstance(proof.get("printing_id"), str) and proof["printing_id"]
                and isinstance(proof.get("name"), str) and proof["name"].strip()):
            keys.add(card_key(proof["name"]))
    return keys


_aliases = metadata_aliases


def _merge_cards(cards: dict[str, dict], fresh: dict[str, dict]) -> None:
    for key, card in fresh.items():
        old = cards.get(key)
        if old and card.get("oracle_id") and old.get("oracle_id") == card["oracle_id"]:
            proofs = copy.deepcopy(old.get("name_aliases", []))
            proofs.extend(proof for proof in card.get("name_aliases", []) if proof not in proofs)
            if proofs:
                card = {**card, "name_aliases": proofs}
        cards[key] = card


def _alias_groups(groups: list[list[str]] | None) -> list[list[str]]:
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise ValueError("Commander alias groups must be lists of equivalent individual-card names")
    result = []
    for group in groups:
        if not isinstance(group, list):
            raise ValueError("Commander alias groups must contain lists")
        names = list(_requested_names(group).values())
        if len(names) < 2:
            raise ValueError("Commander alias groups require at least two different names")
        result.append(names)
    return result


def _printing_identifiers(identifiers: dict[str, str] | None) -> dict[str, str]:
    if identifiers is None:
        return {}
    if not isinstance(identifiers, dict):
        raise ValueError("Printing identifiers must map card names to Scryfall UUIDs")
    result = {}
    for name, value in identifiers.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(value, str):
            raise ValueError("Printing identifiers must map card names to Scryfall UUIDs")
        try:
            identifier = str(UUID(value))
        except (ValueError, AttributeError) as error:
            raise ValueError("Printing identifiers require canonical Scryfall UUIDs") from error
        if value.lower() != identifier:
            raise ValueError("Printing identifiers require canonical Scryfall UUIDs")
        key = card_key(name)
        if key in result and result[key] != identifier:
            raise ValueError("Conflicting Scryfall printing identifiers for the same card name")
        result[key] = identifier
    return result


def _group_lookup(lookup: dict[str, dict], groups: list[list[str]]) -> tuple[dict[str, dict], list[list[str]]]:
    """Apply explicit individual-card equivalences only to unambiguous identities."""
    if not groups:
        return lookup, []
    candidates: dict[str, list[dict]] = {}
    conflicts = []
    for group in groups:
        matches = [lookup[card_key(name)] for name in group if card_key(name) in lookup]
        identities = {card.get("oracle_id") or card_key(card["name"]) for card in matches}
        if len(identities) > 1:
            conflicts.append(group)
            continue
        if matches:
            for name in group:
                candidates.setdefault(card_key(name), []).extend(matches)
    result = dict(lookup)
    for key, matches in candidates.items():
        identities = {card.get("oracle_id") or card_key(card["name"]) for card in matches}
        if key not in result and len(identities) == 1:
            result[key] = matches[0]
    return result, conflicts


def _index(cards: dict[str, dict]) -> dict[str, dict]:
    """Prefer full card names, and omit aliases shared by different cards."""
    result = {key: card for key, card in cards.items() if card["layout"] not in _NON_DECK_LAYOUTS}
    candidates: dict[str, list[dict]] = {}
    for canonical, card in result.items():
        for alias in _aliases(card) - {canonical}:
            candidates.setdefault(alias, []).append(card)
    for alias, matches in candidates.items():
        if alias not in result and len(matches) == 1:
            result[alias] = matches[0]
    return result


def _exact_deck_card(payload: Any, name: str, checked_at: str) -> dict:
    """Resolve a non-deck namesake only from one complete exact Oracle result.

    Scryfall's unique=cards search separates Oracle identities, including
    distinct funny/playtest cards with identical names. Never choose whichever
    result happens to be legal, newest, or first in the returned list.
    """
    if (not isinstance(payload, dict) or payload.get("object") != "list"
            or not isinstance(payload.get("data"), list) or payload.get("has_more") is not False
            or payload.get("next_page")):
        raise ValueError("Exact physical-card search must return a complete unpaginated list")
    rows = payload["data"]
    total = payload.get("total_cards", len(rows))
    if type(total) is not int or total != len(rows) or not rows:
        raise ValueError("Exact physical-card search returned an incomplete or empty list")
    cards = []
    identities = set()
    for raw in rows:
        card = _metadata(raw, checked_at)
        if card["layout"] in _NON_DECK_LAYOUTS or card_key(name) not in metadata_aliases(card):
            raise ValueError(f"Exact physical-card search does not exactly identify a playable card named {name!r}")
        try:
            identity = str(UUID(card.get("oracle_id", "")))
        except (ValueError, AttributeError) as error:
            raise ValueError("Exact physical-card search requires a verified Oracle UUID") from error
        identities.add(identity)
        cards.append(card)
    if len(identities) != 1:
        raise ValueError("Exact physical-card search returned multiple distinct Oracle identities")
    return min(cards, key=lambda card: (card_key(card["name"]) != card_key(name), card.get("id", ""), card["name"]))


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
        if card["layout"] in _NON_DECK_LAYOUTS:
            rejected.append({"returned_name": card["name"], "aliases": sorted(_aliases(card)),
                             "layout": card["layout"],
                             "error": f"Returned card {card['name']!r} uses non-deck layout {card['layout']!r}"})
            continue
        canonical = card_key(card["name"])
        previous = cards.get(canonical)
        if previous and previous.get("oracle_id") != card.get("oracle_id"):
            raise ValueError("Scryfall returned conflicting identities for the same canonical name")
        _merge_cards(cards, {canonical: card})
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

    def load(
        self, names: Iterable[str], *, fetch: bool = False,
        commander_alias_groups: list[list[str]] | None = None,
        printing_ids: dict[str, str] | None = None,
    ) -> tuple[dict[str, dict], dict[str, Any]]:
        requested = _requested_names(names)
        groups = _alias_groups(commander_alias_groups)
        identifiers = _printing_identifiers(printing_ids)
        with self._lock:
            self._read_once()
            return self._load(requested, fetch=fetch, groups=groups, identifiers=identifiers)

    def _load(
        self, requested: dict[str, str], *, fetch: bool, groups: list[list[str]],
        identifiers: dict[str, str],
    ) -> tuple[dict[str, dict], dict[str, Any]]:
        summary: dict[str, Any] = {
            "source_url": COLLECTION_URL, "checked_at": _now(), "network_enabled": fetch,
            "requested": len(requested), "cached": 0, "fetched": 0, "requests": 0,
            "resolved": 0, "missing": [], "not_found": [], "failures": [], "blocked": self.blocked,
            "cache_max_age_days": self.max_age_days, "stale_cached": 0,
            "stale_names": [], "oldest_checked_at": None,
            "alias_resolutions": [], "alias_conflicts": [],
            "collection_rejections": [],
            "printing_requests": 0,
            "physical_name_searches": 0, "non_deck_resolutions": [],
        }
        if self._cache_error is not None:
            summary["cache_error"] = self._cache_error
        now = datetime.now(timezone.utc)
        non_deck_names = {key: card for key in requested
                          if (card := self._cards.get(key)) is not None and card["layout"] in _NON_DECK_LAYOUTS}
        lookup, _ = _group_lookup(self._lookup, groups)
        pending = [name for key, name in requested.items()
                   if key not in lookup or _stale(lookup[key], now, self.max_age_days)]
        refreshed_cards: set[str] = set()

        def save(fresh: dict[str, dict]) -> None:
            if not fresh:
                return
            _merge_cards(self._cards, fresh)
            self._lookup = _index(self._cards)
            _write_cache(self.cache_path, self._cards)
            self._cache_error = None
            refreshed_cards.update(fresh)

        if fetch and pending and self.blocked:
            summary["failures"].append({
                "names": pending, "status_code": self._blocked_status,
                "error": "Repository paused after an access or rate limit response; no request was retried",
            })
        elif fetch and pending:
            with httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT,
                                       "Accept": "application/json"}, timeout=30,
                              follow_redirects=False) as client:
                def request(method: str, url: str, names: list[str], **kwargs: Any) -> Any:
                    # Collection and exact-name fallback share one paced stream
                    # and the same repository-wide access stop.
                    time.sleep(REQUEST_DELAY_SECONDS)
                    summary["requests"] += 1
                    response = client.request(method, url, **kwargs)
                    if response.status_code in {401, 403, 429}:
                        self._blocked_status = response.status_code
                        summary["blocked"] = True
                        summary["failures"].append({"names": names, "status_code": response.status_code,
                                                    "error": "Access or rate limit response; collection stopped without retry"})
                        return None
                    if method == "GET" and response.status_code == 404:
                        return None
                    response.raise_for_status()
                    return _strict_json(response.text)

                def physical_namesake(card: dict, exact: str, requested_name: str) -> dict | None:
                    if card["layout"] not in _NON_DECK_LAYOUTS:
                        return card
                    # include_extras retains funny/playtest identities so they
                    # cannot disappear and make an ambiguous name look unique.
                    # Exclude only intrinsically non-deck layouts, not banned
                    # cards or particular sets. Strict alias checks below still
                    # validate every result rather than trusting the query.
                    query = "!" + json.dumps(exact, ensure_ascii=False)
                    query += " " + " ".join(f"-layout:{layout}" for layout in sorted(_NON_DECK_LAYOUTS))
                    summary["physical_name_searches"] += 1
                    payload = request("GET", SEARCH_URL, [requested_name], params={
                        "q": query, "unique": "cards", "include_extras": "true",
                    })
                    if payload is None:
                        return None
                    replacement = _exact_deck_card(payload, exact, _now())
                    summary["non_deck_resolutions"].append({
                        "requested_name": requested_name, "canonical_name": replacement["name"],
                        "oracle_id": replacement["oracle_id"], "excluded_layout": card["layout"],
                        "source_url": SEARCH_URL,
                    })
                    return replacement

                missing_names: list[str] = []
                fallback_names: list[str] = []
                collection_rejections: list[dict] = []
                for start in range(0, len(pending), COLLECTION_BATCH_SIZE):
                    # A previous batch can resolve an alias in a subsequent batch.
                    batch_now = datetime.now(timezone.utc)
                    lookup, _ = _group_lookup(self._lookup, groups)
                    batch = [name for name in pending[start:start + COLLECTION_BATCH_SIZE]
                             if card_key(name) not in lookup
                             or _stale(lookup[card_key(name)], batch_now, self.max_age_days)]
                    if not batch:
                        continue
                    try:
                        payload = request("POST", COLLECTION_URL, batch,
                                          json={"identifiers": [{"name": _identifier_name(name)} for name in batch]})
                        if payload is None:
                            break
                        fresh, missing, rejected = _collection_cards(payload, batch, _now())
                        save(fresh)
                        missing_names.extend(name for name in missing if name not in missing_names)
                        fallback_names.extend(name for name in missing if name not in fallback_names)
                        for rejection in rejected:
                            fallback_names.extend(name for name in rejection["names"] if name not in fallback_names)
                            for returned in rejection.get("returned_cards", []):
                                if returned.get("layout") in _NON_DECK_LAYOUTS:
                                    for name in rejection["names"]:
                                        key = card_key(name)
                                        if key in returned.get("aliases", []):
                                            non_deck_names[key] = returned
                        collection_rejections.extend(rejected)
                    except (httpx.HTTPError, ValueError, OSError, TypeError) as error:
                        summary["failures"].append({"names": batch, "error": str(error)})
                        break
                # Collection identifiers can miss English alternate printings
                # accepted by the exact named endpoint. Never use fuzzy search.
                for name in fallback_names:
                    if self.blocked:
                        break
                    lookup, conflicts = _group_lookup(self._lookup, groups)
                    key = card_key(name)
                    if key in lookup and not _stale(lookup[key], datetime.now(timezone.utc), self.max_age_days):
                        continue
                    # EDHREC may expose the exact Scryfall printing even when
                    # name search does not index its Secret Lair display name.
                    # The UUID is only a lookup hint: both the returned printing
                    # and its exact English name evidence must independently agree.
                    if key in identifiers:
                        try:
                            summary["printing_requests"] += 1
                            payload = request("GET", f"{CARD_URL}/{identifiers[key]}", [name])
                            if payload is not None:
                                card = _metadata(payload, _now())
                                if card.get("id") != identifiers[key]:
                                    raise ValueError("Returned Scryfall printing does not match the requested UUID")
                                if key not in metadata_aliases(card):
                                    raise ValueError(f"Scryfall printing does not exactly identify {name!r}")
                                card = physical_namesake(card, name, name)
                                if card is None:
                                    continue
                                save({card_key(card["name"]): card})
                                continue
                        except (httpx.HTTPError, ValueError, OSError, TypeError) as error:
                            summary["failures"].append({"names": [name], "error": str(error)})
                    # Explicit EDHREC per-card equivalences are a second fallback,
                    # not positional guesses from partner configurations.
                    alternatives = [name]
                    for group in groups:
                        if key in {card_key(alias) for alias in group} and group not in conflicts:
                            alternatives.extend(alias for alias in group if alias not in alternatives)
                    for exact in alternatives:
                        if self.blocked:
                            break
                        try:
                            payload = request("GET", NAMED_URL, [name], params={"exact": exact})
                            if payload is None:
                                card = non_deck_names.get(card_key(exact))
                                if card is None or self.blocked:
                                    continue
                            else:
                                card = _metadata(payload, _now())
                                if card_key(exact) not in metadata_aliases(card):
                                    raise ValueError(f"Returned card {card['name']!r} does not exactly match {exact!r}")
                            card = physical_namesake(card, exact, name)
                            if card is None:
                                continue
                            # Do not overwrite a known canonical identity through
                            # an alternate name whose Oracle identity conflicts.
                            known = [self._lookup[card_key(alias)] for group in groups
                                     if key in {card_key(item) for item in group}
                                     for alias in group if card_key(alias) in self._lookup]
                            if any((match.get("oracle_id") or card_key(match["name"])) !=
                                   (card.get("oracle_id") or card_key(card["name"])) for match in known):
                                raise ValueError("Commander alias group has conflicting Oracle identities")
                            save({card_key(card["name"]): card})
                            break
                        except (httpx.HTTPError, ValueError, OSError, TypeError) as error:
                            summary["failures"].append({"names": [name], "error": str(error)})
                            break
                lookup, _ = _group_lookup(self._lookup, groups)
                summary["not_found"] = [name for name in missing_names if card_key(name) not in lookup]
                summary["collection_rejections"] = collection_rejections
                unresolved_rejections = []
                for rejection in collection_rejections:
                    unresolved = [name for name in rejection["names"] if card_key(name) not in lookup]
                    if unresolved or not rejection["names"]:
                        unresolved_rejections.append({**rejection, "names": unresolved})
                summary["failures"] = unresolved_rejections + summary["failures"]
        # Resolve just this pool, without scanning the complete shared cache
        # again for every commander. Preserve safe full/front-name aliases.
        result = {}
        lookup, conflicts = _group_lookup(self._lookup, groups)
        summary["alias_conflicts"] = conflicts
        for key in requested:
            card = lookup.get(key)
            if card is not None:
                for alias in _aliases(card):
                    if self._lookup.get(alias) is card:
                        result[alias] = card
                result[key] = card
                if key != card_key(card["name"]):
                    summary["alias_resolutions"].append({
                        "requested_name": requested[key], "canonical_name": card["name"],
                        "oracle_id": card.get("oracle_id"),
                        "source": "scryfall" if key in self._lookup else "edhrec_commander_equivalence",
                    })
        summary["resolved"] = sum(key in result for key in requested)
        summary["fetched"] = sum(key in result and card_key(result[key]["name"]) in refreshed_cards for key in requested)
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
    commander_alias_groups: list[list[str]] | None = None,
    printing_ids: dict[str, str] | None = None,
) -> tuple[dict[str, dict], dict[str, Any]]:
    """Resolve one pool with the legacy seven-day freshness policy.

    For catalog collection, reuse a ``MetadataRepository`` across commanders.
    Offline lookups and failed refreshes retain their original dated metadata;
    unresolved and stale input names remain explicit in the summary.
    """
    return MetadataRepository(cache_path, max_age_days=CACHE_MAX_AGE_DAYS).load(
        names, fetch=fetch, commander_alias_groups=commander_alias_groups,
        printing_ids=printing_ids,
    )
