"""Small, cached EDHREC samples and local deck exports; no deckbuilder crawler."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import re
import sys
import threading
import time
import unicodedata
from typing import Any

import httpx

from edhrec_affinity.edhrec_payload import extract_page_deck_count
from edhrec_affinity.tag_scraper import DEFAULT_USER_AGENT


def card_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).replace("’", "'").strip().casefold()


_SINGLE_CARD_LAYOUTS = frozenset({"transform", "modal_dfc", "flip", "adventure", "prepare", "meld", "reversible_card"})
_PRINTING_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def _role_name_list(card):
    if not isinstance(card, dict):
        raise ValueError("Commander card must be an object")
    names = card.get("names") or [card.get("name")]
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name.strip() for name in names)
            or len({card_key(name) for name in names}) != len(names)):
        raise ValueError("Commander page has invalid commander names")
    return [name.strip() for name in names]


def _commander_roles(card):
    """Separate physical cards from EDHREC's list of their printed faces."""
    names = _role_name_list(card)
    display = card.get("name")
    display = display.strip() if isinstance(display, str) and display.strip() else names[0]
    parts = card.get("cards", [])
    parts = parts if isinstance(parts, list) else []
    named_keys = {card_key(name) for name in names}
    named_keys.update(card_key(face) for name in names for face in name.split(" // "))
    candidates = [part for part in parts if isinstance(part, dict)
                  and isinstance(part.get("name"), str) and card_key(part["name"]) in named_keys]
    identified = [part for part in candidates if isinstance(part, dict)
                  and isinstance(part.get("name"), str) and part["name"].strip()
                  and isinstance(part.get("id"), str) and _PRINTING_ID.fullmatch(part["id"])]
    groups = {}
    conflicting_names = {}
    for part in identified:
        conflicting_names.setdefault(card_key(part["name"]), set()).add(part["id"].lower())
    if (card.get("layout") in _SINGLE_CARD_LAYOUTS
            or any(part.get("layout") in _SINGLE_CARD_LAYOUTS for part in candidates)) and any(
                len(values) > 1 for values in conflicting_names.values()):
        raise ValueError("Commander faces have conflicting printing identities")
    if identified and len(identified) == len(candidates) and not any(len(values) > 1 for values in conflicting_names.values()):
        for part in identified:
            groups.setdefault(part["id"].lower(), []).append(part)
    single = card.get("layout") in _SINGLE_CARD_LAYOUTS
    if single and len(groups) > 1 and " // " not in display:
        raise ValueError("Single commander faces have conflicting printing identities")
    physical = list(groups.values()) if groups else []
    if not single and physical and not all(
            card_key(name) in {card_key(part["name"]) for part in identified}
            or all(card_key(face) in {card_key(part["name"]) for part in identified} for face in name.split(" // "))
            for name in names):
        physical = []  # Partial constituent hints cannot erase a real partner.
    if not physical:
        if single:
            physical = [[{"name": name, "layout": card.get("layout")} for name in names]]
        else:
            return [{"name": name, "aliases": [name], "backs": set()} for name in names]
    roles = []
    for group in physical:
        face_names = list(dict.fromkeys(part["name"].strip() for part in group))
        face_layout = single and len(physical) == 1 or any(part.get("layout") in _SINGLE_CARD_LAYOUTS for part in group)
        if single and len(physical) == 1:
            face_names.extend(name for name in names if card_key(name) not in {card_key(value) for value in face_names})
        if face_layout:
            # A whole Scryfall name is one spelling of these known faces,
            # never a third face. Only explicit physical-card layout evidence
            # allows this split; normal partner display names remain intact.
            face_names = list(dict.fromkeys(face for name in face_names for face in name.split(" // ")))
        group_keys = {card_key(name) for name in face_names}
        ordered = [name for name in names if card_key(name) in group_keys]
        ordered.extend(name for name in face_names if card_key(name) not in {card_key(value) for value in ordered})
        # EDHREC orders a single card's names front first; a page's own front
        # display name is stronger than the order of constituent lookup rows.
        front = display if len(physical) == 1 and " // " not in display and card_key(display) in group_keys else ordered[0]
        aliases = [front]
        backs = set()
        if face_layout and len(ordered) > 1:
            ordered = [front, *[name for name in ordered if card_key(name) != card_key(front)]]
            whole = " // ".join(ordered)
            aliases.append(whole)
            backs.update(card_key(name) for name in ordered[1:])
        elif len(physical) == 1 and len(names) == 1 and " // " in names[0]:
            # A full name supplied as one name is already one physical role.
            front = names[0]
            aliases = [front]
        roles.append({"name": front, "aliases": aliases, "backs": backs})
    order = {card_key(name): position for position, name in enumerate(names)}
    roles.sort(key=lambda role: min((order.get(card_key(alias), len(names)) for alias in role["aliases"]), default=len(names)))
    if len({card_key(role["name"]) for role in roles}) != len(roles):
        raise ValueError("Commander physical identities collapse to duplicate names")
    return roles


def commander_names(card: dict) -> list[str]:
    """Return physical commander roles, never one role per printed face."""
    return [role["name"] for role in _commander_roles(card)]


def commander_back_names(card: dict) -> set[str]:
    """Known standalone back-face keys that cannot fill a commander role."""
    return set().union(*(role["backs"] for role in _commander_roles(card)))


def commander_display_name(card: dict, metadata: dict | None = None) -> str:
    """Show known front/back spellings without adding commander roles.

    A physical card's source faces take precedence over its canonical printing
    name, preserving Secret Lair display names. Metadata can supply a missing
    whole spelling only when it independently identifies that front name.
    """
    from .card_metadata import metadata_aliases

    displayed = []
    for role in _commander_roles(card):
        whole = next((name for name in role["aliases"] if " // " in name), None)
        if whole is None and isinstance(metadata, dict):
            matches = {}
            for candidate in metadata.values():
                if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str):
                    continue
                try:
                    known = metadata_aliases({"layout": "normal", "card_faces": [], **candidate})
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                if card_key(role["name"]) in known and " // " in candidate["name"]:
                    oracle_id = candidate.get("oracle_id")
                    identity = ("oracle", oracle_id) if isinstance(oracle_id, str) and oracle_id else ("name", card_key(candidate["name"]))
                    matches.setdefault(identity, set()).add(candidate["name"])
            if len(matches) == 1:
                spellings = next(iter(matches.values()))
                if len(spellings) == 1:
                    whole = next(iter(spellings))
        displayed.append(whole or role["name"])
    return " // ".join(displayed)


def _commander_name_groups(card: dict) -> list[list[str]]:
    """Read verified complete configurations, with front/whole DFC aliases."""
    try:
        roles = _commander_roles(card)
    except ValueError:
        return []
    names = [role["name"] for role in roles]
    groups = [names]
    # Each role's whole name is an alias of that same physical card; a back
    # face by itself never becomes a valid commander configuration.
    for role_index, role in enumerate(roles):
        for alias in role["aliases"][1:]:
            groups.extend([[*group[:role_index], alias, *group[role_index + 1:]] for group in list(groups)])
    references = card.get("flavor_refs", [])
    references = list(references) if isinstance(references, list) else []
    if isinstance(card.get("flavor_ref"), dict):
        references.append(card["flavor_ref"])
    forbidden_backs = set().union(*(role["backs"] for role in roles))
    for reference in references:
        if not isinstance(reference, dict):
            continue
        aliases = reference.get("names")
        if len(names) == 1 and isinstance(aliases, list) and len(aliases) > 1 and card.get("layout") in _SINGLE_CARD_LAYOUTS:
            # References can enumerate the alternate printing's two faces too.
            aliases = [reference.get("name") or aliases[0]]
        if aliases is None and isinstance(reference.get("name"), str):
            aliases = reference["name"].split(" // ") if len(names) > 1 else [reference["name"]]
        if (isinstance(aliases, list) and len(aliases) == len(names)
                and all(isinstance(name, str) and name.strip() for name in aliases)
                and len({card_key(name) for name in aliases}) == len(names)
                and not any(card_key(name) in forbidden_backs for name in aliases)):
            groups.append(list(aliases))
    unique, seen = [], set()
    for group in groups:
        key = frozenset(card_key(name) for name in group)
        if key not in seen:
            unique.append(group)
            seen.add(key)
    return unique


def commander_card_alias_groups(full_configuration_groups: list[list[str]]) -> list[list[str]]:
    """Derive individual aliases from explicit equivalent configurations.

    Commander order is not an identity mapping. Only configurations that differ
    by exactly one name establish a per-card equivalence; overlapping links may
    then establish transitive aliases. A component that would collapse two
    commanders occurring together is ambiguous and is never returned.
    """
    if not isinstance(full_configuration_groups, list) or not full_configuration_groups:
        return []
    first = full_configuration_groups[0]
    if (not isinstance(first, list) or not first
            or any(not isinstance(name, str) or not name.strip() for name in first)
            or len({card_key(name) for name in first}) != len(first)):
        return []
    names, configurations = {}, set()
    for group in full_configuration_groups:
        if (not isinstance(group, list) or len(group) != len(first)
                or any(not isinstance(name, str) or not name.strip() for name in group)):
            continue
        keys = frozenset(card_key(name) for name in group)
        if len(keys) != len(first):
            continue
        configurations.add(keys)
        for name in group:
            names.setdefault(card_key(name), name.strip())
    links = {}
    ordered = sorted(configurations, key=lambda keys: sorted(keys))
    for position, left in enumerate(ordered):
        for right in ordered[position + 1:]:
            removed, added = left - right, right - left
            if len(removed) == len(added) == 1:
                before, after = next(iter(removed)), next(iter(added))
                links.setdefault(before, set()).add(after)
                links.setdefault(after, set()).add(before)
    groups, visited = [], set()
    for key in sorted(links):
        if key in visited:
            continue
        component, pending = {key}, [key]
        while pending:
            for other in links[pending.pop()] - component:
                component.add(other)
                pending.append(other)
        visited.update(component)
        if any(len(component & configuration) > 1 for configuration in configurations):
            continue
        groups.append([names[alias] for alias in sorted(component)])
    return groups


def commander_printing_ids(card: dict) -> dict[str, str]:
    """Read exact constituent printing IDs without interpreting partner order.

    These are lookup hints, not identity proof: Scryfall must still return that
    printing ID and independently establish the requested English card name.
    """
    groups = _commander_name_groups(card) if isinstance(card, dict) else []
    if not groups:
        return {}
    names = {card_key(name): name for name in groups[0]}
    constituents = card.get("cards", [])
    constituents = list(constituents) if isinstance(constituents, list) else []
    if len(names) == 1:
        constituents.append(card)
    identifiers = {}
    for constituent in constituents:
        if not isinstance(constituent, dict) or not isinstance(constituent.get("name"), str):
            continue
        key, identifier = card_key(constituent["name"]), constituent.get("id")
        if key in names and isinstance(identifier, str) and re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier):
            identifiers.setdefault(key, set()).add(identifier.lower())
    return {names[key]: next(iter(values)) for key, values in identifiers.items() if len(values) == 1}


def enrich_commander_name_groups(base: dict, metadata: dict) -> list[list[str]]:
    """Add complete canonical front/whole configurations with identity proof.

    An input dictionary key alone never proves a rename. Each display name
    must be a strict Scryfall metadata alias or have an existing unambiguous
    per-card EDHREC equivalence, and the resulting commanders must be distinct.
    """
    # card_metadata imports card_key from this module.
    from .card_metadata import metadata_aliases

    names = base.get("commander_names") if isinstance(base, dict) else None
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name.strip() for name in names)
            or len({card_key(name) for name in names}) != len(names)):
        return []
    configurations = base.get("commander_name_groups", [])
    configurations = configurations if isinstance(configurations, list) else []
    groups, seen = [], set()
    for group in [names, *configurations]:
        if (not isinstance(group, list) or len(group) != len(names)
                or any(not isinstance(name, str) or not name.strip() for name in group)):
            continue
        keys = frozenset(card_key(name) for name in group)
        if len(keys) == len(names) and keys not in seen:
            groups.append(list(group))
            seen.add(keys)
    if not isinstance(metadata, dict):
        return groups
    canonical, aliases = {}, {}
    for card in metadata.values():
        if not isinstance(card, dict) or not isinstance(card.get("name"), str) or not card["name"].strip():
            continue
        try:
            known_names = metadata_aliases({"layout": "normal", "card_faces": [], **card})
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        key, oracle_id = card_key(card["name"]), card.get("oracle_id")
        identity = ("oracle", oracle_id) if isinstance(oracle_id, str) and oracle_id else ("name", key)
        canonical.setdefault(key, {})[identity] = card
        for alias in known_names:
            aliases.setdefault(alias, {})[identity] = card
    equivalents = {}
    for group in commander_card_alias_groups(groups):
        keys = {card_key(name) for name in group}
        matches = {}
        for key in keys:
            matches.update(canonical.get(key) or aliases.get(key) or {})
        if len(matches) > 1:
            return groups  # Existing actual names disagree about this identity.
        for key in keys:
            equivalents[key] = matches
    resolved = []
    for name in names:
        key = card_key(name)
        matches = canonical.get(key) or aliases.get(key) or equivalents.get(key) or {}
        if len(matches) != 1:
            return groups
        identity, card = next(iter(matches.items()))
        spellings = [card["name"]]
        # Scryfall's whole DFC name and its canonical front are both valid
        # spellings of one physical card. An alternate printing's verified
        # front can resolve to either one in actual EDHREC previews. Back
        # faces and split/Room halves are excluded by metadata_aliases.
        faces = card.get("card_faces")
        if isinstance(faces, list) and faces and isinstance(faces[0], dict):
            front = faces[0].get("name")
            if (isinstance(front, str) and front.strip()
                    and card_key(front) in metadata_aliases({"layout": "normal", "card_faces": [], **card})
                    and card_key(front) != card_key(card["name"])):
                spellings.append(front)
        resolved.append((identity, spellings))
    if len({identity for identity, _ in resolved}) != len(names):
        return groups
    for configuration in product(*(spellings for _, spellings in resolved)):
        keys = frozenset(card_key(name) for name in configuration)
        if len(keys) == len(names) and keys not in seen:
            groups.append(list(configuration))
            seen.add(keys)
    return groups


class CommanderAliasCatalog:
    """Index explicit flavor-name equivalents from already saved base pages.

    Alternate-art commander pages can omit their canonical names, while the
    canonical page explicitly links to those alternate configurations. Read
    the local source cache once and retain these small name groups rather than
    making another source request or guessing identities from card names.
    """

    def __init__(self, source_root: Path | None = None, *, cards: list[dict] | None = None):
        self.source_root = source_root
        self._loaded = False
        self._lock = threading.Lock()
        self._names: dict[frozenset[str], list[str]] = {}
        self._links: dict[frozenset[str], set[frozenset[str]]] = {}
        for card in cards or []:
            self._register(card)

    def _register(self, card: dict) -> None:
        groups = _commander_name_groups(card)
        if not groups:
            return
        root = frozenset(card_key(name) for name in groups[0])
        for group in groups:
            key = frozenset(card_key(name) for name in group)
            self._names.setdefault(key, group)
            self._links.setdefault(root, set()).add(key)
            self._links.setdefault(key, set()).add(root)

    def _read_once(self) -> None:
        if self._loaded:
            return
        if self.source_root is not None:
            for path in sorted(self.source_root.glob("*/base.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    card = payload["container"]["json_dict"]["card"]
                    if isinstance(card, dict):
                        self._register(card)
                except (OSError, ValueError, KeyError, TypeError):
                    # A corrupt unrelated cache must never broaden identities.
                    continue
        self._loaded = True

    def groups(self, card: dict) -> list[list[str]]:
        with self._lock:
            self._read_once()
            self._register(card)
            names = _commander_name_groups(card)
            if not names:
                raise ValueError("Commander page has invalid commander names")
            expected = frozenset(card_key(name) for name in names[0])
            seen, pending = {expected}, [expected]
            while pending:
                for equivalent in self._links.get(pending.pop(), set()) - seen:
                    seen.add(equivalent)
                    pending.append(equivalent)
            return [names[0], *[list(self._names[key]) for key in sorted(seen - {expected}, key=lambda keys: sorted(keys))]]


def checked_identifier(value: str, *, deck: bool = False) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+" if deck else r"[a-z0-9-]+", value):
        raise ValueError(f"Invalid {'deck ID' if deck else 'slug'}: {value!r}")
    return value


class SourceBlocked(RuntimeError):
    """Stop the collection session on an access challenge or rate limit."""


class BudgetExpired(RuntimeError):
    """Stop new work for this run while retaining inputs for immediate resume."""


class SourceFormatError(ValueError):
    """A source response could not be read; preserve the item for a later retry."""


class RequestLimiter:
    """One request-start budget shared by every collection worker.

    Waiting reserves a start time, not the whole HTTP request, so slow responses
    can overlap without multiplying the configured source request rate. An
    access challenge stops all workers using this limiter immediately.
    """

    def __init__(self, delay: float = 1.0, *, max_rate: float | None = None,
                 start_rate: float = 2.0, ramp_after: int = 100,
                 learned_rate: float | None = None,
                 deadline: float | None = None,
                 monotonic=time.monotonic, sleep=time.sleep):
        if not math.isfinite(delay) or delay <= 0:
            raise ValueError("Shared request delay must be finite and positive")
        if max_rate is not None and (not math.isfinite(max_rate) or max_rate <= 0):
            raise ValueError("Maximum request rate must be finite and positive")
        if learned_rate is not None and (isinstance(learned_rate, bool)
                or not isinstance(learned_rate, (int, float))
                or not math.isfinite(learned_rate) or learned_rate <= 0):
            raise ValueError("Learned request rate must be finite and positive")
        if not math.isfinite(start_rate) or start_rate <= 0 or ramp_after < 1:
            raise ValueError("Starting rate and ramp interval must be positive")
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("Request deadline must be finite")
        self.deadline = deadline
        self.configured_maximum_rate = max_rate if max_rate is not None else 1 / delay
        self.learned_rate = min(learned_rate, self.configured_maximum_rate) if learned_rate is not None else None
        self.minimum_delay = 1 / (self.learned_rate or self.configured_maximum_rate)
        if not math.isfinite(self.minimum_delay):
            raise ValueError("Request rate must allow a finite pacing delay")
        self.delay = max(self.minimum_delay, 1 / start_rate if max_rate is not None else delay)
        self.starting_delay = self.delay
        self.ramp_after = ramp_after
        self._successes = 0
        self.monotonic, self.sleep = monotonic, sleep
        self._lock = threading.Lock()
        self._block_lock = threading.Lock()
        self._next_request = 0.0
        self._blocked_reason: str | None = None
        self._block_kind: str | None = None
        self._blocked_event = threading.Event()
        self._started_at: float | None = None
        self._source_started_at: str | None = None
        self._source_completed_at: str | None = None
        self._requests_started = 0
        self._successful_responses = 0
        self._failures = 0
        self._deferrals = 0
        self._retry_after_until = 0.0
        self._throttles = 0
        self._last_throttle: dict | None = None

    @property
    def blocked(self) -> bool:
        return self._blocked_event.is_set()

    @property
    def budget_expired(self) -> bool:
        return self.deadline is not None and self.monotonic() >= self.deadline

    def check_budget(self) -> None:
        if self.budget_expired:
            raise BudgetExpired("Collection run time budget reached; resume from saved inputs")

    @property
    def stats(self) -> dict:
        with self._lock:
            elapsed = max(0.0, self.monotonic() - self._started_at) if self._started_at is not None else 0.0
            return {
                "requests_started": self._requests_started, "successful_responses": self._successful_responses,
                "failures": self._failures + int(self.blocked), "cooldowns": self._deferrals,
                "maximum_requests_per_second": 1 / self.minimum_delay,
                "configured_maximum_requests_per_second": self.configured_maximum_rate,
                "learned_maximum_requests_per_second": self.learned_rate,
                "current_requests_per_second": 1 / self.delay,
                "observed_requests_per_second": max(0, self._requests_started - 1) / elapsed if elapsed else 0.0,
                "retry_after_seconds": max(0.0, self._retry_after_until - self.monotonic()),
                "elapsed_seconds": elapsed, "blocked": self.blocked,
                "started_at": self._source_started_at, "completed_at": self._source_completed_at,
                "block_kind": self._block_kind,
                "throttles": self._throttles,
                "last_throttle": dict(self._last_throttle) if self._last_throttle else None,
            }

    @property
    def learning_state(self) -> dict:
        """The learned ceiling can be carried into the next collection job."""
        with self._lock:
            return {"learned_rate": self.learned_rate, "throttles": self._throttles,
                    "last_throttle": dict(self._last_throttle) if self._last_throttle else None}

    def wait(self) -> None:
        # Holding the lock while waiting prevents newly arriving workers from
        # overtaking the current waiter or booking requests past a cooldown.
        with self._lock:
            if self.blocked:
                raise SourceBlocked("Collection stopped: " + self._blocked_reason)
            self.check_budget()
            delay = max(0.0, self._next_request - self.monotonic())
            if self.deadline is not None:
                delay = min(delay, max(0, self.deadline - self.monotonic()))
            self.sleep(delay)
            if self.blocked:
                raise SourceBlocked("Collection stopped: " + self._blocked_reason)
            self.check_budget()
            current = self.monotonic()
            if self._started_at is None:
                self._started_at = current
                self._source_started_at = datetime.now(timezone.utc).isoformat()
            self._requests_started += 1
            self._next_request = current + self.delay

    acquire = wait

    def defer(self, seconds: float) -> None:
        """Apply a shared server cooldown without automatically retrying."""
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Cooldown must be finite and nonnegative")
        with self._lock:
            deadline = self.monotonic() + seconds
            self._next_request = max(self._next_request, deadline)
            self._retry_after_until = max(self._retry_after_until, deadline)
            self._deferrals += 1

    def block(self, reason: str, *, kind: str | None = None) -> None:
        # Do not wait behind another worker's pacing sleep before announcing an
        # access block. That waiter checks again before issuing its request.
        if kind not in {None, "rate_limit", "access_restriction"}:
            raise ValueError("Unknown source block kind")
        with self._block_lock:
            # A later response from an already running request can reveal an
            # access restriction after a 429. Never let it become an automatic
            # rate-limit resume, even if another 429 or generic stop follows.
            if (self._block_kind is None or kind == "access_restriction"
                    or kind == self._block_kind):
                self._blocked_reason = reason
                if kind is not None:
                    self._block_kind = kind
            self._blocked_event.set()

    def record_success(self) -> None:
        with self._lock:
            self._source_completed_at = datetime.now(timezone.utc).isoformat()
            self._successes += 1
            self._successful_responses += 1
            if self._successes % self.ramp_after == 0:
                self.delay = max(self.minimum_delay, self.delay / 1.25)

    def record_failure(self, *, cooldown: float = 5.0) -> None:
        with self._lock:
            self._source_completed_at = datetime.now(timezone.utc).isoformat()
            self._successes = 0
            self._failures += 1
            self._deferrals += 1
            self.delay = min(self.starting_delay, self.delay * 2)
            self._next_request = max(self._next_request, self.monotonic() + cooldown)

    def record_throttle(self, *, now: datetime | None = None) -> None:
        """Learn once from this job's 429; the access stop and cooldown still apply.

        Several requests can already be in flight when the first 429 arrives.
        Their responses describe the same event, so they must not compound the
        reduction once per worker. A new job can learn a further lower ceiling.
        """
        with self._lock:
            self._source_completed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
            if self._throttles:
                return
            rate_before = 1 / self.delay
            # The floating-point guard only prevents underflow; there is no
            # practical fixed floor that could exceed a lower source limit.
            learned_rate = max(rate_before * .75, min(rate_before, sys.float_info.min))
            self.learned_rate = min(self.learned_rate or self.configured_maximum_rate, learned_rate)
            self.minimum_delay = 1 / self.learned_rate
            self.delay = max(self.delay, self.minimum_delay)
            self.starting_delay = max(self.starting_delay, self.minimum_delay)
            self._successes = 0
            self._throttles = 1
            self._last_throttle = {
                "at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "rate_before": rate_before, "rate_after": 1 / self.delay,
            }
            self._next_request = max(self._next_request, self.monotonic() + self.delay)


def source_time(value: Any) -> datetime | None:
    """Read an observation timestamp; absent or malformed dates are unknown."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def source_is_stale(value: Any, max_age_seconds: float, now: datetime) -> bool:
    checked = source_time(value)
    return checked is None or not -300 <= (now - checked).total_seconds() <= max_age_seconds


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return max(0.0, (deadline - now).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class Cache:
    def __init__(self, root: Path, *, fetch: bool = False, delay: float = 1.0,
                 max_age_seconds: float | None = None, limiter: RequestLimiter | None = None,
                 preview_transport=None, now=None):
        self.root = root
        self.fetch = fetch
        self.delay = max(1.0, delay)
        if max_age_seconds is not None and (not math.isfinite(max_age_seconds) or max_age_seconds < 0):
            raise ValueError("Cache maximum age must be finite and nonnegative")
        self.max_age_seconds, self.limiter = max_age_seconds, limiter
        self.preview_transport = preview_transport
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_request = 0.0
        self._blocked = False
        self.client = httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=25, follow_redirects=True,
        )
        self.provenance: list[dict[str, Any]] = []
        self._observations: dict[str, dict] = {}
        self._provenance_lock = threading.Lock()

    def close(self) -> None:
        self.client.close()

    @property
    def blocked(self) -> bool:
        return self._blocked or bool(self.limiter and self.limiter.blocked)

    def _block(self, reason: str, *, kind: str = "access_restriction") -> None:
        self._blocked = True
        if self.limiter is not None:
            self.limiter.block(reason, kind=kind)

    def provenance_for(self, url: str) -> list[dict]:
        with self._provenance_lock:
            observation = self._observations.get(url)
            return [dict(observation)] if observation else []

    def alias_observation(self, alias: str, url: str) -> None:
        with self._provenance_lock:
            if url in self._observations:
                self._observations[alias] = self._observations[url]

    def get_preview(self, identifier: str, **kwargs) -> dict:
        if self.preview_transport is not None:
            return self.preview_transport.get(self, identifier, **kwargs)
        return self.get(f"previews/{identifier}.html", f"https://edhrec.com/deckpreview/{identifier}", html=True, **kwargs)

    def get(self, relative: str, url: str, *, html: bool = False, rsc: bool = False,
            max_age_seconds: float | None = None, force_refresh: bool = False,
            validate=None) -> Any:
        path = self.root / relative
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {
            "url": url, "fetched_at": None,
            "note": "Existing cache without a collection timestamp",
        }
        age_limit = self.max_age_seconds if max_age_seconds is None else max_age_seconds
        if age_limit is not None and (not math.isfinite(age_limit) or age_limit < 0):
            raise ValueError("Cache maximum age must be finite and nonnegative")
        stale = age_limit is not None and source_is_stale(metadata.get("fetched_at"), age_limit, self.now())
        # Offline reads retain the original timestamp and explicitly report stale
        # observations. Merely opening a file never refreshes its source age.
        needs_fetch = not path.exists() or (self.fetch and (force_refresh or stale))
        if not needs_fetch:
            text = path.read_text(encoding="utf-8")
        else:
            if not self.fetch:
                raise FileNotFoundError(f"Not cached: {relative}; use --fetch to collect it")
            if self.blocked:
                raise SourceBlocked("Collection stopped after an access challenge or rate limit")
            if httpx.URL(url).host not in {"edhrec.com", "json.edhrec.com"}:
                raise ValueError("This collector only requests public EDHREC pages")
            if self.limiter is not None:
                self.limiter.wait()
            else:
                time.sleep(max(0.0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.client.get(url, headers={"RSC": "1"}) if rsc else self.client.get(url)
            except httpx.TransportError:
                if self.limiter is not None:
                    self.limiter.record_failure()
                raise
            if response.status_code in {401, 403, 429}:
                self._block(f"HTTP {response.status_code}: {url}",
                            kind="rate_limit" if response.status_code == 429 else "access_restriction")
                if response.status_code == 429 and self.limiter is not None:
                    self.limiter.record_throttle(now=self.now())
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"), self.now())
                if retry_after is not None and self.limiter is not None:
                    self.limiter.defer(retry_after)
                raise SourceBlocked(f"HTTP {response.status_code}: {url}; no retry attempted")
            if response.status_code >= 500 and self.limiter is not None:
                self.limiter.record_failure()
            response.raise_for_status()
            text = response.text
            # A changed schema is different from an access challenge. Only clear
            # challenge indicators stop the shared session; unreadable source
            # data remains retryable without excluding the deck permanently.
            lower_text = text.lower()
            challenge = response.headers.get("cf-mitigated", "").lower() == "challenge" or (
                text.lstrip().startswith("<") and any(marker in lower_text for marker in (
                    "/cdn-cgi/challenge-platform/", "cf_chl_opt", 'id="challenge-form"',
                    "id='challenge-form'", "<title>just a moment", "<title>attention required! | cloudflare",
                ))
            )
            if challenge:
                self._block(f"Access challenge from {url}")
                raise SourceBlocked(f"Access challenge from {url}; collection stopped")
            # Validate before caching, so neither a challenge nor an unsupported
            # response can replace a previously usable observation.
            try:
                if rsc:
                    parsed = extract_preview_stream(text)
                elif html:
                    parsed = extract_preview_data(text)
                else:
                    parsed = json.loads(text)
                    if not isinstance(parsed, dict):
                        raise ValueError("Expected a JSON object")
                if validate is not None:
                    validate(parsed)
            except (ValueError, KeyError, TypeError) as error:
                if self.limiter is not None:
                    self.limiter.record_failure()
                raise SourceFormatError(f"Unsupported or invalid response from {url}: {error}") from error
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            metadata = {
                "url": url, "fetched_at": self.now().isoformat(),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            stale = False
            if self.limiter is not None:
                self.limiter.record_success()
        observation = {**metadata, "cache_file": str(path)}
        if age_limit is not None:
            observation.update(cache_max_age_seconds=age_limit, cache_stale=stale)
        if force_refresh and not self.fetch:
            observation["refresh_unavailable_offline"] = True
        with self._provenance_lock:
            self.provenance.append(observation)
            self._observations[url] = observation
        if rsc:
            return extract_preview_stream(text)
        return extract_preview_data(text) if html else json.loads(text)


class _NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.fragments: list[str] = []
        self.scripts: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("id") == "__NEXT_DATA__"
            self._script = [] if not dict(attrs).get("src") else None

    def handle_endtag(self, tag):
        if tag == "script":
            if self._script is not None:
                self.scripts.append("".join(self._script))
                self._script = None
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.fragments.append(data)
        if self._script is not None:
            self._script.append(data)


def _flight_preview_data(stream: str) -> dict[str, Any]:
    """Read JSON model rows from the public React stream without executing JS."""
    records = {}
    for line in stream.splitlines():
        identifier, separator, value = line.partition(":")
        if separator and re.fullmatch(r"[0-9a-f]+", identifier):
            try:
                records[identifier] = json.loads(value)
            except ValueError:
                # Import, resource and text rows are not JSON model records.
                continue

    def resolve(value):
        seen = set()
        while isinstance(value, str) and re.fullmatch(r"\$[0-9a-f]+", value):
            identifier = value[1:]
            if identifier in seen or identifier not in records:
                break
            seen.add(identifier)
            value = records[identifier]
        return value

    # A gallery also has `recs`; require the deckpreview page's decklist to
    # avoid confusing gallery display cards with counted mainboard entries.
    pending = list(records.values())
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            route = resolve(value.get("route"))
            if isinstance(route, dict) and route.get("section") == "deckpreview":
                decklist = resolve(value.get("decklist"))
                if not isinstance(decklist, dict):
                    raise ValueError("Deck preview is missing its counted decklist")
                recs = resolve(decklist.get("recs"))
                categories = resolve(decklist.get("categories", []))
                if not isinstance(recs, list) or not isinstance(categories, list):
                    raise ValueError("Deck preview has an invalid decklist")
                labels = {}
                for entry in categories:
                    entry = resolve(entry)
                    if isinstance(entry, dict):
                        if not isinstance(entry.get("slug"), str) or not isinstance(entry.get("label"), str):
                            raise ValueError("Deck preview has an invalid category label")
                        labels[entry.get("slug")] = entry.get("label")
                commanders, cards = [], {}
                for entry in recs:
                    entry = resolve(entry)
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                        raise ValueError("Deck preview has an invalid card entry")
                    name = entry["name"]
                    # React escapes literal strings beginning with '$'.
                    name = name[1:] if name.startswith("$$") else name
                    quantity = _quantity(entry.get("count"))
                    category = entry.get("category")
                    role = entry.get("role")
                    if category == "type-commander" or role in {"commander", "partner"}:
                        commanders.append(name)
                    elif role not in {"sideboard", "maybeboard", "considering", "companion"}:
                        label = labels.get(category) or str(category or "Unknown").removeprefix("type-").title()
                        if label.casefold() not in {"sideboard", "maybeboard", "considering", "companion"}:
                            cards.setdefault(label, []).append([name, quantity])
                if not commanders or not cards:
                    raise ValueError("Deck preview has no commanders or mainboard cards")
                return {"deck": {"commander": commanders, "cards": cards},
                        "tags": resolve(decklist.get("tags", [])), "url": decklist.get("url", ""),
                        "savedate": decklist.get("savedate"), "urlhash": route.get("deckpreviewSlug")}
            pending.extend(value.values())
    raise ValueError("Deck preview has no supported embedded deck data; schema may have changed")


def extract_preview_stream(text: str) -> dict[str, Any]:
    # Some caches/CDNs serve HTML despite the public RSC rendering header.
    return extract_preview_data(text) if text.lstrip().startswith("<") else _flight_preview_data(text)


def extract_preview_data(html: str) -> dict[str, Any]:
    parser = _NextDataParser()
    parser.feed(html)
    if not parser.fragments:
        fragments = []
        decoder = json.JSONDecoder()
        for script in parser.scripts:
            for match in re.finditer(r"(?:^|;)\s*self\.__next_f\.push\(", script):
                try:
                    chunk, _ = decoder.raw_decode(script[match.end():])
                except ValueError:
                    continue
                if (isinstance(chunk, list) and len(chunk) == 2
                        and chunk[0] == 1 and isinstance(chunk[1], str)):
                    fragments.append(chunk[1])
        return _flight_preview_data("".join(fragments))
    payload = json.loads("".join(parser.fragments))
    data = payload["props"]["pageProps"]["data"]
    if not isinstance(data, dict) or not isinstance(data.get("deck"), dict):
        raise ValueError("Deck preview is missing its deck object")
    return data


def _compact_preview_data(payload: dict) -> dict:
    props = payload.get("pageProps")
    data = props.get("data") if isinstance(props, dict) else None
    if (not isinstance(data, dict) or not isinstance(data.get("deck"), dict)
            or not isinstance(data["deck"].get("cards"), dict)):
        raise ValueError("Compact deck response is missing its deck object")
    return data


class PreviewTransport:
    """Reuse the public page's compact data across framework deployments.

    The build ID is discovered from the approved public HTML page, never guessed.
    Legacy pages expose a shared build ID; newer pages expose a public React
    stream at the same URL. A compact 404 or changed response rediscovers the
    format once; an access block propagates without trying another route.
    """

    def __init__(self, *, max_build_age_seconds: float = 3600, monotonic=time.monotonic):
        self.max_build_age_seconds = max_build_age_seconds
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._build_id: str | None = None
        self._mode: str | None = None
        self._build_checked = 0.0

    def _bootstrap(self, cache: Cache, identifier: str, **kwargs) -> dict:
        url = f"https://edhrec.com/deckpreview/{identifier}"
        relative = f"previews/{identifier}.html"
        data = cache.get(relative, url, html=True, **{**kwargs, "force_refresh": cache.fetch})
        parser = _NextDataParser()
        parser.feed((cache.root / relative).read_text(encoding="utf-8"))
        build_id = json.loads("".join(parser.fragments)).get("buildId") if parser.fragments else None
        self._build_id = build_id if isinstance(build_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", build_id) else None
        self._mode = "compact" if self._build_id else "rsc"
        self._build_checked = self.monotonic()
        return data

    def get(self, cache: Cache, identifier: str, **kwargs) -> dict:
        checked_identifier(identifier, deck=True)
        canonical = f"https://edhrec.com/deckpreview/{identifier}"
        relative = f"previews/{identifier}.json"
        path = cache.root / relative
        rsc_relative = f"previews/{identifier}.rsc"
        rsc_path = cache.root / rsc_relative
        if rsc_path.exists():
            meta_path = rsc_path.with_suffix(".rsc.meta.json")
            metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            limit = kwargs.get("max_age_seconds", cache.max_age_seconds)
            fresh = limit is None or not source_is_stale(metadata.get("fetched_at"), limit, cache.now())
            if not cache.fetch or (fresh and not kwargs.get("force_refresh")):
                return cache.get(rsc_relative, canonical, rsc=True, **kwargs)
        if path.exists():
            meta_path = path.with_suffix(path.suffix + ".meta.json")
            metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            limit = kwargs.get("max_age_seconds", cache.max_age_seconds)
            fresh = limit is None or not source_is_stale(metadata.get("fetched_at"), limit, cache.now())
            if not cache.fetch or (fresh and not kwargs.get("force_refresh")):
                source_url = metadata.get("url") or canonical
                payload = cache.get(relative, source_url, validate=_compact_preview_data, **kwargs)
                cache.alias_observation(canonical, source_url)
                return _compact_preview_data(payload)
        if not cache.fetch:
            return cache.get(f"previews/{identifier}.html", canonical, html=True, **kwargs)
        with self._lock:
            if self._mode is None or self.monotonic() - self._build_checked >= self.max_build_age_seconds:
                return self._bootstrap(cache, identifier, **kwargs)
            build_id = self._build_id
            mode = self._mode
        if mode == "rsc":
            return cache.get(rsc_relative, canonical, rsc=True, **kwargs)
        url = f"https://edhrec.com/_next/data/{build_id}/deckpreview/{identifier}.json"
        try:
            payload = cache.get(relative, url, validate=_compact_preview_data, **kwargs)
            cache.alias_observation(canonical, url)
            return _compact_preview_data(payload)
        except (httpx.HTTPStatusError, SourceFormatError) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code != 404:
                raise
            # A successful HTML fallback also supplies this deck's data. Updating
            # the shared build under one lock avoids one refresh per worker.
            with self._lock:
                if self._mode == "compact" and self._build_id == build_id:
                    return self._bootstrap(cache, identifier, **kwargs)
                new_build_id = self._build_id
                mode = self._mode
            if mode == "rsc":
                return cache.get(rsc_relative, canonical, rsc=True, **kwargs)
            new_url = f"https://edhrec.com/_next/data/{new_build_id}/deckpreview/{identifier}.json"
            payload = cache.get(relative, new_url, validate=_compact_preview_data, **kwargs)
            cache.alias_observation(canonical, new_url)
            return _compact_preview_data(payload)


def parse_profile(payload: dict, slug: str, name: str, *, allow_empty: bool = False) -> dict:
    """Keep observed, mature nonland card rates; omitted cards remain unknown."""
    count = extract_page_deck_count(payload)
    cards: dict[str, dict] = {}
    ranking_cards: dict[str, dict] = {}
    ignored = Counter()
    allowed = {
        "topcards", "highliftcards", "highsynergycards", "creatures", "instants",
        "sorceries", "utilityartifacts", "enchantments", "planeswalkers",
        "manaartifacts", "battles", "auras", "equipment",
    }
    cardlists = payload.get("container", {}).get("json_dict", {}).get("cardlists")
    if not isinstance(cardlists, list):
        raise ValueError(f"{slug}: missing cardlists")
    land_keys = {
        card_key(card["name"]) for group in cardlists
        if group.get("tag") in {"lands", "utilitylands", "basiclands", "nonbasiclands"}
        for card in group.get("cardviews", [])
    }
    for group in cardlists:
        category = group.get("tag")
        for card in group.get("cardviews", []):
            numerator = card.get("num_decks")
            denominator = card.get("potential_decks")
            if not isinstance(numerator, (float, int)) or not isinstance(denominator, (float, int)):
                ignored["missing_counts"] += 1
                continue
            if not 0 <= numerator <= denominator or denominator <= 0 or denominator > count:
                ignored["invalid_counts"] += 1
                continue
            key = card_key(card["name"])
            lift = card.get("lift")
            measurement = {
                "name": card["name"], "rate": numerator / denominator,
                "count": int(numerator), "denominator": int(denominator),
                "lift": lift if isinstance(lift, (int, float)) and math.isfinite(lift) and lift >= 0 else None,
            }
            # Deck rankings use the page's actual observations, including lands
            # and recent cards. The mature-card restriction below remains only
            # for statistical profile comparisons.
            ranking_cards[key] = measurement
            if category not in allowed:
                continue
            # Recent cards can have a smaller eligible-deck window. Do not compare
            # that rate against an older card's full-history denominator.
            if denominator < 0.9 * count:
                ignored["short_exposure_window"] += 1
                continue
            if key in land_keys:
                continue
            cards[key] = measurement
    if not cards and not allow_empty:
        raise ValueError(f"{slug}: no usable nonland cards; check the page schema")
    return {"tag_slug": slug, "tag_name": name, "deck_count": count,
            "cards": cards, "ranking_cards": ranking_cards,
            "land_keys": sorted(land_keys), "ignored": dict(ignored)}


def _quantity(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or value <= 0:
        raise ValueError(f"Invalid card quantity: {value!r}")
    return int(value)


def parse_preview(data: dict, deck_id: str = "") -> dict:
    deck = data["deck"]
    commanders = deck.get("commander", data.get("commanders", []))
    cards = []
    for category, entries in deck["cards"].items():
        if category.casefold() in {"sideboard", "maybeboard", "considering", "commander"}:
            continue
        for name, quantity in entries:
            cards.append({"name": name, "quantity": _quantity(quantity), "type_line": category})
    return {"id": deck_id or data.get("urlhash", "import"), "commanders": commanders,
            "cards": cards, "tags": data.get("tags", []), "source_url": data.get("url", ""),
            "preview_url": f"https://edhrec.com/deckpreview/{deck_id}" if deck_id else "",
            "savedate": data.get("savedate"), "source": "edhrec_preview"}


def parse_deck_json(data: dict, identifier: str) -> dict:
    """Accept normalized exports and captured Archidekt/Moxfield JSON shapes."""
    if "props" in data:
        data = data["props"]["pageProps"]["data"]
    if "deck" in data and isinstance(data["deck"], dict):
        return parse_preview(data, identifier)
    if "commanders" in data and isinstance(data.get("cards"), list):
        cards = [{**card, "quantity": _quantity(card.get("quantity", 1))} for card in data["cards"]]
        return {**data, "id": identifier, "cards": cards, "source": "local_export"}
    cards, commanders = [], []
    if isinstance(data.get("cards"), list):  # Archidekt's captured deck response
        for entry in data["cards"]:
            categories = {str(c).casefold() for c in entry.get("categories", [])}
            if categories & {"sideboard", "maybeboard", "considering"}:
                continue
            oracle = entry["card"]["oracleCard"]
            if "commander" in categories:
                commanders.append(oracle["name"])
            else:
                cards.append({"name": oracle["name"], "quantity": _quantity(entry["quantity"]),
                              "type_line": " ".join(oracle.get("types", []))})
    else:  # Moxfield v2/v3: inspect only mainboard and commanders
        boards = data.get("boards", data)
        for board_name in ("mainboard", "commanders"):
            board = boards.get(board_name, {})
            entries = board.get("cards", board) if isinstance(board, dict) else {}
            for entry in entries.values():
                card = entry["card"]
                if board_name == "commanders":
                    commanders.append(card["name"])
                else:
                    cards.append({"name": card["name"], "quantity": _quantity(entry["quantity"]),
                                  "type_line": card.get("type_line", "")})
    if not cards or not commanders:
        raise ValueError("Unrecognized deck JSON or missing commanders; use a normalized or text export")
    return {"id": identifier, "commanders": commanders, "cards": cards,
            "source": "local_export", "source_url": data.get("url", ""), "tags": []}


def parse_deck_text(text: str, identifier: str, commanders: list[str]) -> dict:
    """Import quantity/name exports with optional set codes and board headings."""
    cards = []
    active = True
    commander_keys = {card_key(name) for name in commanders}
    found_commanders: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        heading = line.lstrip("/# ").rstrip(":").casefold()
        if heading in {"sideboard", "maybeboard", "considering", "companion"}:
            active = False
            continue
        if heading in {"commander", "commanders", "mainboard", "main", "deck"}:
            active = True
            continue
        if not active or line.startswith(("//", "#")):
            continue
        match = re.fullmatch(r"(\d+)x?\s+(.+)", line)
        if not match:
            raise ValueError(f"Unrecognized export line {number}: {line!r}")
        name = re.sub(r"\s+\([A-Za-z0-9]+\)(?:\s+\S+)?(?:\s+\*[^*]+\*)?$", "", match[2])
        name = re.sub(r"\s+\[[^]]+\].*$", "", name).strip()
        if card_key(name) in commander_keys:
            found_commanders.add(card_key(name))
        else:
            cards.append({"name": name, "quantity": _quantity(int(match[1])), "type_line": ""})
    if found_commanders != commander_keys:
        raise ValueError("Text export must contain the requested commander(s)")
    return {"id": identifier, "commanders": commanders, "cards": cards,
            "source": "local_export", "source_url": "", "tags": []}


def import_decks(folder: Path, commanders: list[str]) -> tuple[list[dict], list[dict]]:
    decks, failures = [], []
    for path in sorted(folder.iterdir()):
        if path.suffix not in {".json", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
            deck = (parse_deck_json(json.loads(text), path.stem) if path.suffix == ".json"
                    else parse_deck_text(text, path.stem, commanders))
            deck["import_file"] = str(path)
            decks.append(deck)
        except (ValueError, KeyError, TypeError) as error:
            failures.append({"file": str(path), "error": str(error)})
    return decks, failures
