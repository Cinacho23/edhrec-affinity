"""Small, cached EDHREC samples and local deck exports; no deckbuilder crawler."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
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


class Cache:
    def __init__(self, root: Path, *, fetch: bool = False, delay: float = 1.0):
        self.root = root
        self.fetch = fetch
        self.delay = max(1.0, delay)
        self.last_request = 0.0
        self.blocked = False
        self.client = httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=25, follow_redirects=True,
        )
        self.provenance: list[dict[str, Any]] = []

    def close(self) -> None:
        self.client.close()

    def get(self, relative: str, url: str, *, html: bool = False) -> Any:
        path = self.root / relative
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            if not self.fetch:
                raise FileNotFoundError(f"Not cached: {relative}; use --fetch to collect it")
            if self.blocked:
                raise SourceBlocked("Collection stopped after an access challenge or rate limit")
            if httpx.URL(url).host not in {"edhrec.com", "json.edhrec.com"}:
                raise ValueError("This collector only requests public EDHREC pages")
            time.sleep(max(0.0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            response = self.client.get(url)
            if response.status_code in {401, 403, 429}:
                self.blocked = True
                raise SourceBlocked(f"HTTP {response.status_code}: {url}; no retry attempted")
            response.raise_for_status()
            text = response.text
            # Validate before caching a possible HTML challenge as a JSON response.
            try:
                if html:
                    extract_preview_data(text)
                elif not isinstance(json.loads(text), dict):
                    raise ValueError("Expected a JSON object")
            except (ValueError, KeyError) as error:
                self.blocked = True
                raise SourceBlocked(f"Unexpected response from {url}; collection stopped") from error
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps({
                "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }, indent=2), encoding="utf-8")
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {
            "url": url, "fetched_at": None,
            "note": "Existing cache without a collection timestamp",
        }
        self.provenance.append({**metadata, "cache_file": str(path)})
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


def parse_profile(payload: dict, slug: str, name: str) -> dict:
    """Keep observed, mature nonland card rates; omitted cards remain unknown."""
    count = extract_page_deck_count(payload)
    cards: dict[str, dict] = {}
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
        if category not in allowed:
            continue
        for card in group.get("cardviews", []):
            numerator = card.get("num_decks")
            denominator = card.get("potential_decks")
            if not isinstance(numerator, (float, int)) or not isinstance(denominator, (float, int)):
                ignored["missing_counts"] += 1
                continue
            if not 0 <= numerator <= denominator or denominator <= 0 or denominator > count:
                ignored["invalid_counts"] += 1
                continue
            # Recent cards can have a smaller eligible-deck window. Do not compare
            # that rate against an older card's full-history denominator.
            if denominator < 0.9 * count:
                ignored["short_exposure_window"] += 1
                continue
            key = card_key(card["name"])
            if key in land_keys:
                continue
            cards[key] = {
                "name": card["name"], "rate": numerator / denominator,
                "count": int(numerator), "denominator": int(denominator),
            }
    if not cards:
        raise ValueError(f"{slug}: no usable nonland cards; check the page schema")
    return {"tag_slug": slug, "tag_name": name, "deck_count": count,
            "cards": cards, "land_keys": sorted(land_keys), "ignored": dict(ignored)}


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
