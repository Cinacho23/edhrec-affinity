"""Small, cached EDHREC samples and local deck exports; no deckbuilder crawler."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any

import httpx

from edhrec_affinity.edhrec_payload import extract_page_deck_count
from edhrec_affinity.tag_scraper import DEFAULT_USER_AGENT


def card_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).replace("’", "'").strip().casefold()


def checked_identifier(value: str, *, deck: bool = False) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+" if deck else r"[a-z0-9-]+", value):
        raise ValueError(f"Invalid {'deck ID' if deck else 'slug'}: {value!r}")
    return value


class SourceBlocked(RuntimeError):
    """Stop the collection session on an access challenge or rate limit."""


class RequestLimiter:
    """One request-start budget shared by every collection worker.

    Waiting reserves a start time, not the whole HTTP request, so slow responses
    can overlap without multiplying the configured source request rate. An
    access challenge stops all workers using this limiter immediately.
    """

    def __init__(self, delay: float = 1.0, *, max_rate: float | None = None,
                 start_rate: float = 2.0, ramp_after: int = 100,
                 monotonic=time.monotonic, sleep=time.sleep):
        if not math.isfinite(delay) or delay <= 0:
            raise ValueError("Shared request delay must be finite and positive")
        if max_rate is not None and (not math.isfinite(max_rate) or max_rate <= 0):
            raise ValueError("Maximum request rate must be finite and positive")
        if not math.isfinite(start_rate) or start_rate <= 0 or ramp_after < 1:
            raise ValueError("Starting rate and ramp interval must be positive")
        self.minimum_delay = 1 / max_rate if max_rate is not None else delay
        self.delay = max(self.minimum_delay, 1 / start_rate) if max_rate is not None else delay
        self.starting_delay = self.delay
        self.ramp_after = ramp_after
        self._successes = 0
        self.monotonic, self.sleep = monotonic, sleep
        self._lock = threading.Lock()
        self._next_request = 0.0
        self._blocked_reason: str | None = None
        self._blocked_event = threading.Event()
        self._started_at: float | None = None
        self._requests_started = 0
        self._successful_responses = 0
        self._failures = 0
        self._deferrals = 0
        self._retry_after_until = 0.0

    @property
    def blocked(self) -> bool:
        return self._blocked_event.is_set()

    @property
    def stats(self) -> dict:
        with self._lock:
            elapsed = max(0.0, self.monotonic() - self._started_at) if self._started_at is not None else 0.0
            return {
                "requests_started": self._requests_started, "successful_responses": self._successful_responses,
                "failures": self._failures + int(self.blocked), "cooldowns": self._deferrals,
                "maximum_requests_per_second": 1 / self.minimum_delay,
                "current_requests_per_second": 1 / self.delay,
                "observed_requests_per_second": max(0, self._requests_started - 1) / elapsed if elapsed else 0.0,
                "retry_after_seconds": max(0.0, self._retry_after_until - self.monotonic()),
                "elapsed_seconds": elapsed, "blocked": self.blocked,
            }

    def wait(self) -> None:
        # Holding the lock while waiting prevents newly arriving workers from
        # overtaking the current waiter or booking requests past a cooldown.
        with self._lock:
            if self.blocked:
                raise SourceBlocked("Collection stopped: " + self._blocked_reason)
            self.sleep(max(0.0, self._next_request - self.monotonic()))
            if self.blocked:
                raise SourceBlocked("Collection stopped: " + self._blocked_reason)
            current = self.monotonic()
            if self._started_at is None:
                self._started_at = current
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

    def block(self, reason: str) -> None:
        # Do not wait behind another worker's pacing sleep before announcing an
        # access block. That waiter checks again before issuing its request.
        self._blocked_reason = reason
        self._blocked_event.set()

    def record_success(self) -> None:
        with self._lock:
            self._successes += 1
            self._successful_responses += 1
            if self._successes % self.ramp_after == 0:
                self.delay = max(self.minimum_delay, self.delay / 1.25)

    def record_failure(self, *, cooldown: float = 5.0) -> None:
        with self._lock:
            self._successes = 0
            self._failures += 1
            self._deferrals += 1
            self.delay = min(self.starting_delay, self.delay * 2)
            self._next_request = max(self._next_request, self.monotonic() + cooldown)


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

    def _block(self, reason: str) -> None:
        self._blocked = True
        if self.limiter is not None:
            self.limiter.block(reason)

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

    def get(self, relative: str, url: str, *, html: bool = False,
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
                response = self.client.get(url)
            except httpx.TransportError:
                if self.limiter is not None:
                    self.limiter.record_failure()
                raise
            if response.status_code in {401, 403, 429}:
                self._block(f"HTTP {response.status_code}: {url}")
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"), self.now())
                if retry_after is not None and self.limiter is not None:
                    self.limiter.defer(retry_after)
                raise SourceBlocked(f"HTTP {response.status_code}: {url}; no retry attempted")
            if response.status_code >= 500 and self.limiter is not None:
                self.limiter.record_failure()
            response.raise_for_status()
            text = response.text
            # Validate before caching a possible HTML challenge as a JSON response.
            try:
                if html:
                    extract_preview_data(text)
                elif not isinstance(json.loads(text), dict):
                    raise ValueError("Expected a JSON object")
                if validate is not None:
                    validate(json.loads(text))
            except (ValueError, KeyError) as error:
                self._block(f"Unexpected response from {url}")
                raise SourceBlocked(f"Unexpected response from {url}; collection stopped") from error
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
        return extract_preview_data(text) if html else json.loads(text)


class _NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.fragments: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.fragments.append(data)


def extract_preview_data(html: str) -> dict[str, Any]:
    parser = _NextDataParser()
    parser.feed(html)
    if not parser.fragments:
        raise ValueError("Deck preview has no embedded Next.js data; schema may have changed")
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
    """Reuse the public page's compact Next.js data with a shared build ID.

    The build ID is discovered from the approved public HTML page, never guessed.
    A deployment-related compact 404 refreshes that page once; an access block
    propagates immediately without trying another route.
    """

    def __init__(self, *, max_build_age_seconds: float = 3600, monotonic=time.monotonic):
        self.max_build_age_seconds = max_build_age_seconds
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._build_id: str | None = None
        self._build_checked = 0.0

    def _bootstrap(self, cache: Cache, identifier: str, **kwargs) -> dict:
        url = f"https://edhrec.com/deckpreview/{identifier}"
        relative = f"previews/{identifier}.html"
        data = cache.get(relative, url, html=True, **{**kwargs, "force_refresh": cache.fetch})
        parser = _NextDataParser()
        parser.feed((cache.root / relative).read_text(encoding="utf-8"))
        build_id = json.loads("".join(parser.fragments)).get("buildId")
        self._build_id = build_id if isinstance(build_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", build_id) else None
        self._build_checked = self.monotonic()
        return data

    def get(self, cache: Cache, identifier: str, **kwargs) -> dict:
        checked_identifier(identifier, deck=True)
        canonical = f"https://edhrec.com/deckpreview/{identifier}"
        relative = f"previews/{identifier}.json"
        path = cache.root / relative
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
            if self._build_id is None or self.monotonic() - self._build_checked >= self.max_build_age_seconds:
                return self._bootstrap(cache, identifier, **kwargs)
            build_id = self._build_id
        url = f"https://edhrec.com/_next/data/{build_id}/deckpreview/{identifier}.json"
        try:
            payload = cache.get(relative, url, validate=_compact_preview_data, **kwargs)
            cache.alias_observation(canonical, url)
            return _compact_preview_data(payload)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
            # A successful HTML fallback also supplies this deck's data. Updating
            # the shared build under one lock avoids one refresh per worker.
            with self._lock:
                if self._build_id is None or self._build_id == build_id:
                    return self._bootstrap(cache, identifier, **kwargs)
                new_build_id = self._build_id
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
