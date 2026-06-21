"""
scryfall_enrichment.py

This script enriches EDHREC commander data with Scryfall metadata.

Why this exists:
- EDHREC scrape rows currently contain commander names, slugs, deck counts,
  tag counts, z-scores, and trend fields.
- They do not yet contain commander color identity or card image URLs.
- Scryfall is a strong source for that missing card metadata.

Main output:
    data/processed/<date>/commander_scryfall_metadata.json

Optional behavior:
    With --merge-website-files, the script also adds Scryfall metadata into
    website-facing JSON files such as:
      - affinity_rows_with_trends.json
      - global_leaderboard.json
      - tag_rankings.json
      - affinity_rows.json

Important:
- This script stores image URLs, not image binaries.
- This script uses a conservative delay between requests.
- This script uses a cache file so rerunning it does not repeatedly hit
  Scryfall for the same card names.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"

DEFAULT_USER_AGENT = (
    "EDHRECCommanderTagAffinityAnalysis/0.1 "
    "(personal learning project; contact: replace-with-your-email-if-desired)"
)

COLOR_ORDER = ["W", "U", "B", "R", "G"]

DEFAULT_WEBSITE_FILES = [
    "affinity_rows.json",
    "affinity_rows_with_trends.json",
    "global_leaderboard.json",
    "tag_rankings.json",
]


@dataclass(frozen=True)
class CommanderIdentity:
    """Minimal commander identity needed for Scryfall lookup."""

    commander_name: str
    commander_slug: str


def read_json(path: Path) -> Any:
    """Read a JSON file and return the decoded Python object."""
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    """Write JSON with readable indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_cache(path: Path) -> dict[str, Any]:
    """Load a local Scryfall lookup cache."""
    if not path.exists():
        return {}

    try:
        data = read_json(path)
    except json.JSONDecodeError:
        return {}

    if isinstance(data, dict):
        return data

    return {}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    """Save the Scryfall lookup cache."""
    write_json(path, cache)


def clean_commander_name_for_scryfall(name: str) -> str:
    """
    Convert an EDHREC display name into a better Scryfall card-name candidate.

    Example:
      "Aang, A Lot to Learn (Commander)" -> "Aang, A Lot to Learn"
    """
    cleaned = str(name).strip()
    cleaned = re.sub(r"\s*\(Commander\)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def split_commander_name_candidates(name: str) -> list[str]:
    """
    Return likely Scryfall card-name candidates.

    The full cleaned name is tried first. If it looks like a partner pair,
    Doctor/Companion pair, or similar EDHREC paired commander display, each side
    is also tried.
    """
    cleaned = clean_commander_name_for_scryfall(name)
    candidates = [cleaned]

    split_patterns = [
        r"\s+//\s+",
        r"\s+\+\s+",
    ]

    for pattern in split_patterns:
        if re.search(pattern, cleaned):
            parts = [
                part.strip()
                for part in re.split(pattern, cleaned)
                if part.strip()
            ]

            for part in parts:
                if part not in candidates:
                    candidates.append(part)

    return candidates


def make_cache_key(name: str, search_mode: str) -> str:
    """Build a stable cache key."""
    return f"{search_mode}:{name.lower()}"


def fetch_scryfall_named(
    client: httpx.Client,
    name: str,
    *,
    search_mode: str,
    delay_seconds: float,
    cache: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Fetch one card from Scryfall's /cards/named endpoint.

    search_mode:
      exact:
        Use exact-name lookup.

      fuzzy:
        Use fuzzy-name lookup.

    Returns:
      A Scryfall card object if found, otherwise None.
    """
    if search_mode not in {"exact", "fuzzy"}:
        raise ValueError(f"Unsupported search_mode: {search_mode}")

    cache_key = make_cache_key(name, search_mode)

    if cache_key in cache:
        return cache[cache_key]

    params = {search_mode: name}

    try:
        response = client.get(SCRYFALL_NAMED_URL, params=params)
    except httpx.HTTPError:
        cache[cache_key] = None
        return None

    time.sleep(delay_seconds)

    if response.status_code == 404:
        cache[cache_key] = None
        return None

    if response.status_code == 429:
        # Back off once if rate-limited, then retry one time.
        time.sleep(max(delay_seconds * 4, 2.0))

        try:
            response = client.get(SCRYFALL_NAMED_URL, params=params)
        except httpx.HTTPError:
            cache[cache_key] = None
            return None

        time.sleep(delay_seconds)

        if response.status_code == 404:
            cache[cache_key] = None
            return None

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        cache[cache_key] = None
        return None

    card = response.json()
    cache[cache_key] = card
    return card


def make_uri_cache_key(uri: str) -> str:
    """Build a stable cache key for Scryfall API URI fetches."""
    return f"uri:{uri}"


def fetch_scryfall_uri(
    client: httpx.Client,
    uri: str,
    *,
    delay_seconds: float,
    cache: dict[str, Any],
) -> dict[str, Any] | None:
    """Fetch and cache an arbitrary Scryfall API URI."""
    cache_key = make_uri_cache_key(uri)

    if cache_key in cache:
        return cache[cache_key]

    try:
        response = client.get(uri)
    except httpx.HTTPError:
        cache[cache_key] = None
        return None

    time.sleep(delay_seconds)

    if response.status_code == 404:
        cache[cache_key] = None
        return None

    if response.status_code == 429:
        time.sleep(max(delay_seconds * 4, 2.0))

        try:
            response = client.get(uri)
        except httpx.HTTPError:
            cache[cache_key] = None
            return None

        time.sleep(delay_seconds)

        if response.status_code == 404:
            cache[cache_key] = None
            return None

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        cache[cache_key] = None
        return None

    data = response.json()
    cache[cache_key] = data
    return data


def fetch_card_prints(
    client: httpx.Client,
    prints_search_uri: str,
    *,
    delay_seconds: float,
    cache: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch all Scryfall print objects from a card's prints_search_uri."""
    prints: list[dict[str, Any]] = []
    next_uri: str | None = prints_search_uri

    while next_uri:
        page = fetch_scryfall_uri(
            client,
            next_uri,
            delay_seconds=delay_seconds,
            cache=cache,
        )

        if not isinstance(page, dict):
            break

        page_cards = page.get("data", [])

        if isinstance(page_cards, list):
            prints.extend(card for card in page_cards if isinstance(card, dict))

        next_uri = page.get("next_page") if page.get("has_more") else None

    return prints


def release_sort_key(card: dict[str, Any]) -> tuple[str, str]:
    """Sort cards by earliest release date, then set code for stability."""
    return (
        str(card.get("released_at") or "9999-99-99"),
        str(card.get("set") or ""),
    )


def find_origin_card(
    client: httpx.Client,
    card: dict[str, Any],
    *,
    delay_seconds: float,
    cache: dict[str, Any],
    use_print_history: bool,
) -> dict[str, Any]:
    """Return the earliest known print for a Scryfall card when available."""
    if not use_print_history:
        return card

    prints_search_uri = card.get("prints_search_uri")

    if not prints_search_uri:
        return card

    prints = fetch_card_prints(
        client,
        str(prints_search_uri),
        delay_seconds=delay_seconds,
        cache=cache,
    )

    if not prints:
        return card

    return sorted(prints, key=release_sort_key)[0]


def find_cards_for_commander(
    client: httpx.Client,
    commander_name: str,
    *,
    delay_seconds: float,
    cache: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """
    Find one or more Scryfall card objects for a commander.

    Strategy:
      1. Try exact match on the full cleaned name.
      2. If the name looks paired, try exact match on each part.
      3. If no exact match works, try fuzzy match on the full cleaned name.
      4. If still no match and the name is paired, try fuzzy match on each part.

    Returns:
      (cards, match_strategy)
    """
    candidates = split_commander_name_candidates(commander_name)
    full_name = candidates[0]
    part_names = candidates[1:]

    # 1. Exact full-name match.
    exact_full = fetch_scryfall_named(
        client,
        full_name,
        search_mode="exact",
        delay_seconds=delay_seconds,
        cache=cache,
    )

    if exact_full:
        return [exact_full], "exact_full_name"

    # 2. Exact part-name matches for paired commanders.
    exact_part_cards: list[dict[str, Any]] = []

    for part_name in part_names:
        card = fetch_scryfall_named(
            client,
            part_name,
            search_mode="exact",
            delay_seconds=delay_seconds,
            cache=cache,
        )

        if card:
            exact_part_cards.append(card)

    if exact_part_cards:
        return exact_part_cards, "exact_split_parts"

    # 3. Fuzzy full-name match.
    fuzzy_full = fetch_scryfall_named(
        client,
        full_name,
        search_mode="fuzzy",
        delay_seconds=delay_seconds,
        cache=cache,
    )

    if fuzzy_full:
        return [fuzzy_full], "fuzzy_full_name"

    # 4. Fuzzy part-name matches.
    fuzzy_part_cards: list[dict[str, Any]] = []

    for part_name in part_names:
        card = fetch_scryfall_named(
            client,
            part_name,
            search_mode="fuzzy",
            delay_seconds=delay_seconds,
            cache=cache,
        )

        if card:
            fuzzy_part_cards.append(card)

    if fuzzy_part_cards:
        return fuzzy_part_cards, "fuzzy_split_parts"

    return [], "not_found"


def ordered_color_identity(colors: list[str]) -> list[str]:
    """
    Return color identity in WUBRG order.

    Scryfall color_identity values are normally arrays like ["G", "W"].
    For display and filtering, WUBRG order is more stable.
    """
    color_set = set(colors)
    return [color for color in COLOR_ORDER if color in color_set]


def get_card_image_url(card: dict[str, Any]) -> str | None:
    """
    Extract a useful card image URL from a Scryfall card object.

    Normal cards usually have:
      card["image_uris"]["normal"]

    Multi-face cards may instead have:
      card["card_faces"][0]["image_uris"]["normal"]
    """
    image_uris = card.get("image_uris")

    if isinstance(image_uris, dict):
        return (
            image_uris.get("normal")
            or image_uris.get("large")
            or image_uris.get("small")
            or image_uris.get("png")
        )

    card_faces = card.get("card_faces")

    if isinstance(card_faces, list):
        for face in card_faces:
            if not isinstance(face, dict):
                continue

            face_image_uris = face.get("image_uris")

            if isinstance(face_image_uris, dict):
                return (
                    face_image_uris.get("normal")
                    or face_image_uris.get("large")
                    or face_image_uris.get("small")
                    or face_image_uris.get("png")
                )

    return None


def get_origin_set_info(card: dict[str, Any]) -> dict[str, Any] | None:
    """Extract set metadata from a Scryfall card object."""
    set_code = card.get("set")

    if not set_code:
        return None

    return {
        "set_code": str(set_code).lower(),
        "set_name": card.get("set_name") or str(set_code).upper(),
        "released_at": card.get("released_at"),
        "scryfall_set_uri": card.get("scryfall_set_uri"),
        "set_uri": card.get("set_uri"),
    }


def unique_origin_sets(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a stable unique origin-set list from Scryfall card objects."""
    origin_sets: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    for card in cards:
        origin_set = get_origin_set_info(card)

        if not origin_set:
            continue

        set_code = origin_set["set_code"]

        if set_code in seen_codes:
            continue

        seen_codes.add(set_code)
        origin_sets.append(origin_set)

    return origin_sets


def build_metadata_row(
    commander: CommanderIdentity,
    cards: list[dict[str, Any]],
    *,
    match_strategy: str,
    origin_cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Convert one or more Scryfall card objects into one commander metadata row.

    For paired commanders:
      - color_identity is the union of all matched cards.
      - card_image_url is the first matched card image.
      - partner_card_image_urls stores all matched card images.
    """
    colors: list[str] = []
    image_urls: list[str] = []
    scryfall_uris: list[str] = []
    matched_names: list[str] = []
    scryfall_ids: list[str] = []
    origin_sets = unique_origin_sets(origin_cards or cards)
    primary_origin_set = origin_sets[0] if origin_sets else None

    for card in cards:
        card_colors = card.get("color_identity", [])

        if isinstance(card_colors, list):
            colors.extend(str(color) for color in card_colors)

        image_url = get_card_image_url(card)

        if image_url:
            image_urls.append(image_url)

        scryfall_uri = card.get("scryfall_uri")

        if scryfall_uri:
            scryfall_uris.append(str(scryfall_uri))

        card_name = card.get("name")

        if card_name:
            matched_names.append(str(card_name))

        card_id = card.get("id")

        if card_id:
            scryfall_ids.append(str(card_id))

    return {
        "commander_name": commander.commander_name,
        "commander_slug": commander.commander_slug,
        "scryfall_match_found": bool(cards),
        "scryfall_match_strategy": match_strategy,
        "scryfall_card_names": matched_names,
        "scryfall_ids": scryfall_ids,
        "color_identity": ordered_color_identity(colors),
        "card_image_url": image_urls[0] if image_urls else None,
        "partner_card_image_urls": image_urls,
        "scryfall_uri": scryfall_uris[0] if scryfall_uris else None,
        "partner_scryfall_uris": scryfall_uris,
        "origin_set_code": primary_origin_set.get("set_code")
        if primary_origin_set
        else None,
        "origin_set_name": primary_origin_set.get("set_name")
        if primary_origin_set
        else None,
        "origin_released_at": primary_origin_set.get("released_at")
        if primary_origin_set
        else None,
        "scryfall_set_uri": primary_origin_set.get("scryfall_set_uri")
        if primary_origin_set
        else None,
        "set_uri": primary_origin_set.get("set_uri") if primary_origin_set else None,
        "origin_sets": origin_sets,
    }


def extract_commander_identities_from_rows(rows: list[dict[str, Any]]) -> list[CommanderIdentity]:
    """
    Extract unique commander identities from row-based processed data.

    The input can be:
      - commanders_clean.json
      - affinity_rows_with_trends.json
      - affinity_rows.json
      - another row file containing commander_name and commander_slug
    """
    commander_map: dict[str, CommanderIdentity] = {}

    for row in rows:
        commander_slug = row.get("commander_slug")

        if not commander_slug:
            continue

        commander_name = (
            row.get("commander_name")
            or row.get("commander")
            or commander_slug
        )

        if commander_slug not in commander_map:
            commander_map[commander_slug] = CommanderIdentity(
                commander_name=str(commander_name),
                commander_slug=str(commander_slug),
            )

    return sorted(
        commander_map.values(),
        key=lambda commander: commander.commander_slug,
    )


def load_commander_identities(processed_dir: Path) -> list[CommanderIdentity]:
    """
    Load commanders from the best available processed file.

    Preferred:
      commanders_clean.json

    Fallbacks:
      affinity_rows_with_trends.json
      affinity_rows.json
    """
    candidate_files = [
        processed_dir / "commanders_clean.json",
        processed_dir / "affinity_rows_with_trends.json",
        processed_dir / "affinity_rows.json",
    ]

    for path in candidate_files:
        if not path.exists():
            continue

        data = read_json(path)

        if not isinstance(data, list):
            continue

        commanders = extract_commander_identities_from_rows(data)

        if commanders:
            return commanders

    raise FileNotFoundError(
        "Could not find commander data. Expected one of: "
        "commanders_clean.json, affinity_rows_with_trends.json, affinity_rows.json"
    )


def enrich_commanders_with_scryfall(
    commanders: list[CommanderIdentity],
    *,
    user_agent: str,
    delay_seconds: float,
    cache_path: Path,
    use_print_history_for_origin: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Enrich all commanders with Scryfall metadata.

    Returns:
      (metadata_rows, failure_rows)
    """
    cache = load_cache(cache_path)
    metadata_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }

    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
        for index, commander in enumerate(commanders, start=1):
            print(
                f"[{index}/{len(commanders)}] Looking up "
                f"{commander.commander_name} ({commander.commander_slug})"
            )

            cards, match_strategy = find_cards_for_commander(
                client,
                commander.commander_name,
                delay_seconds=delay_seconds,
                cache=cache,
            )

            origin_cards = [
                find_origin_card(
                    client,
                    card,
                    delay_seconds=delay_seconds,
                    cache=cache,
                    use_print_history=use_print_history_for_origin,
                )
                for card in cards
            ]

            metadata_row = build_metadata_row(
                commander,
                cards,
                match_strategy=match_strategy,
                origin_cards=origin_cards,
            )

            metadata_rows.append(metadata_row)

            if not cards:
                failure_rows.append(
                    {
                        "commander_name": commander.commander_name,
                        "commander_slug": commander.commander_slug,
                        "reason": "No Scryfall match found",
                        "candidate_names": split_commander_name_candidates(
                            commander.commander_name
                        ),
                    }
                )

            # Save cache periodically so progress is preserved if interrupted.
            if index % 50 == 0:
                save_cache(cache_path, cache)

    save_cache(cache_path, cache)

    return metadata_rows, failure_rows


def merge_metadata_into_rows(
    rows: list[dict[str, Any]],
    metadata_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Add Scryfall metadata to row-based website files.

    Joins on commander_slug.
    """
    metadata_by_slug = {
        row["commander_slug"]: row
        for row in metadata_rows
        if row.get("commander_slug")
    }

    enriched_rows: list[dict[str, Any]] = []

    for row in rows:
        commander_slug = row.get("commander_slug")
        metadata = metadata_by_slug.get(commander_slug)

        if not metadata:
            enriched_rows.append(row)
            continue

        enriched_rows.append(
            {
                **row,
                "color_identity": metadata.get("color_identity"),
                "card_image_url": metadata.get("card_image_url"),
                "partner_card_image_urls": metadata.get("partner_card_image_urls"),
                "scryfall_uri": metadata.get("scryfall_uri"),
                "partner_scryfall_uris": metadata.get("partner_scryfall_uris"),
                "scryfall_card_names": metadata.get("scryfall_card_names"),
                "scryfall_match_strategy": metadata.get("scryfall_match_strategy"),
                "origin_set_code": metadata.get("origin_set_code"),
                "origin_set_name": metadata.get("origin_set_name"),
                "origin_released_at": metadata.get("origin_released_at"),
                "scryfall_set_uri": metadata.get("scryfall_set_uri"),
                "set_uri": metadata.get("set_uri"),
                "origin_sets": metadata.get("origin_sets"),
            }
        )

    return enriched_rows


def merge_metadata_into_website_files(
    processed_dir: Path,
    metadata_rows: list[dict[str, Any]],
    *,
    file_names: list[str],
) -> dict[str, int]:
    """
    Merge Scryfall metadata into selected website JSON files.

    Returns:
      A dictionary mapping filename -> row count written.
    """
    written_counts: dict[str, int] = {}

    for file_name in file_names:
        path = processed_dir / file_name

        if not path.exists():
            continue

        data = read_json(path)

        if not isinstance(data, list):
            continue

        enriched_data = merge_metadata_into_rows(data, metadata_rows)
        write_json(path, enriched_data)
        written_counts[file_name] = len(enriched_data)

    return written_counts


def build_summary(
    *,
    commanders: list[CommanderIdentity],
    metadata_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
    merged_file_counts: dict[str, int],
    output_files: dict[str, str],
) -> dict[str, Any]:
    """Build a run summary JSON object."""
    matched_count = sum(
        1 for row in metadata_rows if row.get("scryfall_match_found")
    )

    image_count = sum(
        1 for row in metadata_rows if row.get("card_image_url")
    )

    color_count = sum(
        1 for row in metadata_rows if row.get("color_identity")
    )

    origin_set_count = sum(
        1 for row in metadata_rows if row.get("origin_set_code")
    )

    return {
        "commander_count": len(commanders),
        "metadata_row_count": len(metadata_rows),
        "matched_count": matched_count,
        "unmatched_count": len(failure_rows),
        "rows_with_card_image_url": image_count,
        "rows_with_color_identity": color_count,
        "rows_with_origin_set": origin_set_count,
        "merged_file_counts": merged_file_counts,
        "output_files": output_files,
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Enrich EDHREC commander data with Scryfall metadata."
    )

    parser.add_argument(
        "--processed-dir",
        required=True,
        type=Path,
        help="Processed snapshot directory, e.g. data/processed/2026-05-07",
    )

    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help=(
            "Optional Scryfall cache path. Defaults to "
            "<processed-dir>/scryfall_lookup_cache.json"
        ),
    )

    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.55,
        help="Delay between Scryfall requests. Default: 0.55 seconds.",
    )

    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User-Agent header sent to Scryfall.",
    )

    parser.add_argument(
        "--merge-website-files",
        action="store_true",
        help=(
            "If set, merge Scryfall metadata into website-facing JSON files "
            "in the processed directory."
        ),
    )

    parser.add_argument(
        "--skip-origin-print-history",
        action="store_true",
        help=(
            "Use the matched Scryfall card's set as the origin set instead of "
            "fetching prints_search_uri to find the earliest print."
        ),
    )

    parser.add_argument(
        "--website-files",
        nargs="*",
        default=DEFAULT_WEBSITE_FILES,
        help=(
            "Specific JSON files to enrich when --merge-website-files is set."
        ),
    )

    return parser.parse_args()


def main() -> None:
    """Command-line entry point."""
    args = parse_args()

    processed_dir: Path = args.processed_dir
    cache_path: Path = (
        args.cache_path
        if args.cache_path is not None
        else processed_dir / "scryfall_lookup_cache.json"
    )

    commanders = load_commander_identities(processed_dir)

    metadata_rows, failure_rows = enrich_commanders_with_scryfall(
        commanders,
        user_agent=args.user_agent,
        delay_seconds=args.delay_seconds,
        cache_path=cache_path,
        use_print_history_for_origin=not args.skip_origin_print_history,
    )

    metadata_path = processed_dir / "commander_scryfall_metadata.json"
    failures_path = processed_dir / "commander_scryfall_failures.json"

    write_json(metadata_path, metadata_rows)
    write_json(failures_path, failure_rows)

    merged_file_counts: dict[str, int] = {}

    if args.merge_website_files:
        merged_file_counts = merge_metadata_into_website_files(
            processed_dir,
            metadata_rows,
            file_names=args.website_files,
        )

    summary_path = processed_dir / "scryfall_enrichment_summary.json"

    summary = build_summary(
        commanders=commanders,
        metadata_rows=metadata_rows,
        failure_rows=failure_rows,
        merged_file_counts=merged_file_counts,
        output_files={
            "metadata": metadata_path.name,
            "failures": failures_path.name,
            "cache": cache_path.name,
            "summary": summary_path.name,
        },
    )

    write_json(summary_path, summary)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
