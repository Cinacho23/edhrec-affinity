#!/usr/bin/env python3
"""
export_full_site_data.py

Exports the complete processed EDHREC affinity dataset into smaller static JSON
files suitable for GitHub Pages.

This does NOT discard rows.

Instead of deploying one huge file like:

    affinity_rows_with_trends.json

it writes:

    data/latest/commanders/<commander_slug>.json
    data/latest/tags/<tag_slug>.json
    data/latest/theme-brackets/<tag_slug>.json
    data/latest/theme-report.json
    data/latest/quirky-builds.json
    data/latest/leaderboard/page_0001.json
    data/latest/leaderboard/page_0002.json

This keeps the full dataset available while avoiding one giant browser download.

Important:
The browser requires strict valid JSON. Python can accidentally serialize NaN,
Infinity, and -Infinity unless allow_nan=False is used. This script sanitizes
those values to null before writing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edhrec_affinity.build_styles.eligibility import commander_is_eligible, valid_deck_count, validate_minimum

import pandas as pd

from edhrec_affinity.scryfall_enrichment import (
    build_commander_cards,
    get_card_image_url,
    get_meld_result_part,
    load_cache,
    make_uri_cache_key,
    make_cache_key,
    ordered_color_identity,
    split_commander_name_candidates,
)


LEADERBOARD_PAGE_SIZE = 500
THEME_BRACKET_MIN_Z = 1
BRACKET_SIGNAL_TAG_SLUGS = {
    "cedh",
    "aggro",
    "control",
    "midrange",
    "tempo",
    "combo",
}

SUMMARY_FILES = [
    "analysis_summary.json",
    "trend_summary.json",
    "tag_summary.json",
]

AFFINITY_MODEL_FIELDS = [
    "legacy_z", "affinity_model_version", "affinity_model_status",
    "tag_affinity_adjusted_pct", "tag_affinity_lower_pct", "tag_affinity_upper_pct",
    "tag_prior_mean_pct", "tag_prior_strength", "tag_prior_std_pct",
    "tag_reference_row_count",
]


def safe_json_filename(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip("-")
    return value or "unknown"


def has_value(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, float) and math.isnan(value):
        return False

    if isinstance(value, str):
        return bool(value.strip())

    try:
        result = pd.isna(value)

        if isinstance(result, bool):
            return not result
    except (TypeError, ValueError):
        pass

    return True


def as_sort_float(value: Any, fallback: float = 0.0) -> float:
    if not has_value(value):
        return fallback

    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize_list_value(value: Any) -> list[Any]:
    if not has_value(value):
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, dict):
        return [value]

    if isinstance(value, str):
        text = value.strip()

        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return []

            return parsed if isinstance(parsed, list) else []

        return [part.strip() for part in text.split(",") if part.strip()]

    return []


def extract_set_code_from_scryfall_uri(uri: Any) -> str | None:
    if not has_value(uri):
        return None

    match = re.search(r"/card/([^/]+)/", str(uri), flags=re.IGNORECASE)

    if not match:
        return None

    return safe_json_filename(match.group(1))


def normalize_origin_set(origin_set: Any) -> dict[str, Any] | None:
    if not isinstance(origin_set, dict):
        return None

    set_code = (
        origin_set.get("set_code")
        or origin_set.get("code")
        or origin_set.get("origin_set_code")
    )

    if not has_value(set_code):
        return None

    set_code = safe_json_filename(str(set_code))

    return {
        "set_code": set_code,
        "set_name": origin_set.get("set_name")
        or origin_set.get("name")
        or origin_set.get("origin_set_name")
        or set_code.upper(),
        "released_at": origin_set.get("released_at")
        or origin_set.get("origin_released_at"),
        "scryfall_set_uri": origin_set.get("scryfall_set_uri")
        or origin_set.get("scryfall_uri"),
        "set_uri": origin_set.get("set_uri"),
    }


def extract_origin_sets_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    origin_sets: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    def add_origin(origin_set: dict[str, Any] | None) -> None:
        if not origin_set:
            return

        set_code = origin_set["set_code"]

        if set_code in seen_codes:
            return

        seen_codes.add(set_code)
        origin_sets.append(origin_set)

    for origin_set in normalize_list_value(row.get("origin_sets")):
        add_origin(normalize_origin_set(origin_set))

    add_origin(
        normalize_origin_set(
            {
                "set_code": row.get("origin_set_code"),
                "set_name": row.get("origin_set_name"),
                "released_at": row.get("origin_released_at"),
                "scryfall_set_uri": row.get("scryfall_set_uri"),
                "set_uri": row.get("set_uri"),
            }
        )
    )

    uris = [row.get("scryfall_uri"), *normalize_list_value(row.get("partner_scryfall_uris"))]

    for uri in uris:
        set_code = extract_set_code_from_scryfall_uri(uri)

        if set_code:
            add_origin(
                {
                    "set_code": set_code,
                    "set_name": set_code.upper(),
                    "released_at": None,
                    "scryfall_set_uri": None,
                    "set_uri": None,
                }
            )

    return origin_sets


def ensure_origin_set_metadata(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    object_columns = [
        "origin_sets",
        "origin_set_code",
        "origin_set_name",
        "origin_released_at",
        "scryfall_set_uri",
        "set_uri",
    ]

    for column in object_columns:
        if column not in df.columns:
            df[column] = pd.Series([None] * len(df), dtype="object")
        else:
            df[column] = df[column].astype("object")

    for index, row in df.iterrows():
        origin_sets = extract_origin_sets_from_row(row.to_dict())

        if not origin_sets:
            continue

        primary = origin_sets[0]
        df.at[index, "origin_sets"] = origin_sets
        df.at[index, "origin_set_code"] = primary.get("set_code")
        df.at[index, "origin_set_name"] = primary.get("set_name")
        df.at[index, "origin_released_at"] = primary.get("released_at")
        df.at[index, "scryfall_set_uri"] = primary.get("scryfall_set_uri")
        df.at[index, "set_uri"] = primary.get("set_uri")

    return df


def sanitize_for_json(value: Any) -> Any:
    """
    Recursively convert values into strict JSON-safe values.

    Converts:
    - NaN -> None
    - Infinity -> None
    - -Infinity -> None
    - pandas NA/NaT -> None

    This prevents browser-side response.json() / JSON.parse() failures.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}

    if isinstance(value, list):
        return [sanitize_for_json(item) for item in value]

    if isinstance(value, tuple):
        return [sanitize_for_json(item) for item in value]

    return value


def read_json_records(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required input file: {path}")

    return pd.read_json(path)


def read_json_object(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing required input file: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    sanitized = sanitize_for_json(data)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            sanitized,
            file,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            ensure_ascii=False,
            allow_nan=False,
        )


def write_records(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    records = df.to_dict(orient="records")
    sanitized = sanitize_for_json(records)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            sanitized,
            file,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    fallback_pairs = [
        ("total_decks", ["total_decks", "total_decks_current", "total_decks_x"]),
        ("tag_decks", ["tag_decks", "tag_decks_current", "tag_decks_x"]),
        (
            "tag_affinity_pct",
            ["tag_affinity_pct", "affinity_pct_current", "tag_affinity_pct_x"],
        ),
        ("z", ["z", "z_current", "z_x"]),
        (
            "rank_within_tag_by_z",
            [
                "rank_within_tag_by_z",
                "rank_within_tag_by_z_current",
                "rank_within_tag_by_z_x",
            ],
        ),
    ]

    for target, possible_sources in fallback_pairs:
        if target not in df.columns:
            for source in possible_sources:
                if source in df.columns:
                    df[target] = df[source]
                    break

    if "tag_affinity_pct_display" not in df.columns and "tag_affinity_pct" in df.columns:
        df["tag_affinity_pct_display"] = df["tag_affinity_pct"] * 100

    return df


def select_existing_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    existing = [column for column in columns if column in df.columns]
    return df[existing].copy()


def ensure_commander_card_metadata(df: pd.DataFrame, cache_path: Path) -> pd.DataFrame:
    """Upgrade restored legacy data from cached Scryfall objects, without requests.

    Older processed snapshots retain full Scryfall responses but only export the
    first image of each card. Recover all faces once per commander at export time.
    When the cache is missing/incomplete, leave the legacy image fields available
    for the frontend's fallback instead of claiming the preview is complete.
    """
    if "commander_slug" not in df.columns:
        return df
    cache = load_cache(cache_path)

    cards_by_name = {}
    cards_by_image = {}
    for cached in cache.values():
        if not isinstance(cached, dict):
            continue
        cached_cards = cached.get("data", []) if cached.get("object") == "list" else [cached]
        for card in cached_cards:
            if not isinstance(card, dict) or not card.get("name"):
                continue
            cards_by_name.setdefault(str(card["name"]).casefold(), card)
            for face in card.get("card_faces") or []:
                if isinstance(face, dict) and face.get("name"):
                    cards_by_name.setdefault(str(face["name"]).casefold(), card)
            image_url = get_card_image_url(card)
            if image_url:
                cards_by_image[image_url] = card

    unique_rows = df.drop_duplicates(subset=["commander_slug"]).to_dict(orient="records")
    standalone_colors = {}
    for row in unique_rows:
        candidates = split_commander_name_candidates(str(row.get("commander_name") or ""))
        matched_names = normalize_list_value(row.get("scryfall_card_names"))
        is_single = len(candidates) == 1 or matched_names == [candidates[0]]
        if is_single and isinstance(row.get("color_identity"), list):
            for name in [candidates[0], *matched_names]:
                standalone_colors[str(name).casefold()] = row["color_identity"]

    cards_by_slug = {}
    colors_by_slug = {}
    for row in unique_rows:
        candidates = split_commander_name_candidates(str(row.get("commander_name") or ""))
        matched_names = normalize_list_value(row.get("scryfall_card_names"))
        # A full exact name may be a single double-faced card. Otherwise resolve
        # every display-name member, even when the old enrichment found only one.
        full_card = cache.get(make_cache_key(candidates[0], "exact"))
        full_card = full_card or cards_by_name.get(candidates[0].casefold())
        is_pair = len(candidates) > 1 and not full_card and matched_names != [candidates[0]]
        names = candidates[1:] if is_pair else matched_names or candidates[:1]
        images = normalize_list_value(row.get("partner_card_image_urls"))
        if not images and has_value(row.get("card_image_url")):
            images = [row["card_image_url"]]
        cards = []
        identities = []
        for name in names:
            # Only align legacy images against their own matched names: a partial
            # pair may contain an image for its second member but not its first.
            index = matched_names.index(name) if name in matched_names else len(images)
            card = cards_by_image.get(images[index]) if index < len(images) else None
            card = card or cache.get(make_cache_key(str(name), "exact"))
            card = card or cache.get(make_cache_key(str(name), "fuzzy"))
            card = card or cards_by_name.get(str(name).casefold())
            colors = card.get("color_identity") if isinstance(card, dict) else None
            identities.append(colors if isinstance(colors, list) else standalone_colors.get(str(name).casefold()))
            if not isinstance(card, dict):
                cards.append(None)
                continue
            # Two face names can refer to the same physical double-faced card.
            if not any(existing and existing.get("name") == card.get("name") for existing in cards):
                cards.append(card)
        if is_pair and all(isinstance(colors, list) for colors in identities):
            colors_by_slug[row["commander_slug"]] = ordered_color_identity(
                [color for colors in identities for color in colors]
            )
        if not normalize_list_value(row.get("commander_cards")):
            if cards and all(cards):
                meld_results = {}
                for card in cards:
                    meld_part = get_meld_result_part(card)
                    if meld_part:
                        result = cache.get(make_uri_cache_key(meld_part["uri"]))
                        if not isinstance(result, dict):
                            result = cards_by_name.get(str(meld_part.get("name")).casefold())
                        if result:
                            meld_results[meld_part["uri"]] = result
                previews = build_commander_cards(cards, meld_results=meld_results)
                if previews:
                    cards_by_slug[row["commander_slug"]] = previews

    if not cards_by_slug and not colors_by_slug:
        return df
    df = df.copy()
    for column, values_by_slug in [("commander_cards", cards_by_slug), ("color_identity", colors_by_slug)]:
        if values_by_slug:
            existing = df[column] if column in df else [None] * len(df)
            df[column] = [
                values_by_slug.get(slug, value)
                for slug, value in zip(df["commander_slug"], existing)
            ]
    return df


def sort_for_leaderboard(df: pd.DataFrame) -> pd.DataFrame:
    sort_columns = []
    ascending = []

    if "z" in df.columns:
        sort_columns.append("z")
        ascending.append(False)

    if "tag_decks" in df.columns:
        sort_columns.append("tag_decks")
        ascending.append(False)

    if "total_decks" in df.columns:
        sort_columns.append("total_decks")
        ascending.append(False)

    if sort_columns:
        return df.sort_values(sort_columns, ascending=ascending, na_position="last")

    return df


def sort_for_commander_detail(df: pd.DataFrame) -> pd.DataFrame:
    if "z" in df.columns:
        columns = [column for column in ["z", "commander_name", "tag_name", "tag_slug"] if column in df.columns]
        return df.sort_values(columns, ascending=[False, *([True] * (len(columns) - 1))], na_position="last", kind="stable")

    if "tag_decks" in df.columns:
        return df.sort_values("tag_decks", ascending=False, na_position="last")

    return df


def sort_for_tag_detail(df: pd.DataFrame) -> pd.DataFrame:
    if "rank_within_tag_by_z" in df.columns:
        return df.sort_values("rank_within_tag_by_z", ascending=True, na_position="last")

    if "z" in df.columns:
        return df.sort_values("z", ascending=False, na_position="last")

    return df


def build_commander_index(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "commander_name",
        "commander_slug",
        "color_identity",
        "total_decks",
        "card_image_url",
        "partner_card_image_urls",
        "commander_cards",
        "scryfall_uri",
        "partner_scryfall_uris",
        "scryfall_card_names",
        "origin_set_code",
        "origin_set_name",
        "origin_released_at",
        "scryfall_set_uri",
        "set_uri",
        "origin_sets",
    ]

    index_df = select_existing_columns(df, columns)
    index_df = index_df.drop_duplicates(subset=["commander_slug"])

    if "commander_name" in index_df.columns:
        return index_df.sort_values("commander_name", na_position="last")

    return index_df


def build_tag_index(df: pd.DataFrame, tag_summary_df: pd.DataFrame | None) -> pd.DataFrame:
    if tag_summary_df is not None and not tag_summary_df.empty:
        if "tag_name" in tag_summary_df.columns:
            return tag_summary_df.sort_values("tag_name", na_position="last")
        return tag_summary_df

    columns = ["tag_name", "tag_slug"]
    tag_df = select_existing_columns(df, columns)
    tag_df = tag_df.drop_duplicates(subset=["tag_slug"])

    if "tag_name" in tag_df.columns:
        return tag_df.sort_values("tag_name", na_position="last")

    return tag_df


def export_commander_files(df: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    commanders_dir = output_dir / "commanders"
    commanders_dir.mkdir(parents=True, exist_ok=True)

    commander_index = build_commander_index(df)
    write_records(commanders_dir / "index.json", commander_index)

    commander_count = 0
    max_rows = 0

    for commander_slug, group in df.groupby("commander_slug"):
        safe_slug = safe_json_filename(commander_slug)
        sorted_group = sort_for_commander_detail(group)
        write_records(commanders_dir / f"{safe_slug}.json", sorted_group)

        commander_count += 1
        max_rows = max(max_rows, len(sorted_group))

    return {
        "commander_count": commander_count,
        "max_rows_in_commander_file": max_rows,
        "index_file": "commanders/index.json",
        "file_pattern": "commanders/<commander_slug>.json",
    }


def build_set_commander_row(
    row: dict[str, Any],
    origin_set: dict[str, Any],
) -> dict[str, Any]:
    return {
        "commander_name": row.get("commander_name"),
        "commander_slug": row.get("commander_slug"),
        "tag_name": row.get("tag_name"),
        "tag_slug": row.get("tag_slug"),
        "color_identity": row.get("color_identity"),
        "total_decks": row.get("total_decks"),
        "tag_decks": row.get("tag_decks"),
        "tag_affinity_pct": row.get("tag_affinity_pct"),
        "tag_affinity_pct_display": row.get("tag_affinity_pct_display"),
        "z": row.get("z"),
        "rank_within_tag_by_z": row.get("rank_within_tag_by_z"),
        "rank_delta": row.get("rank_delta"),
        "z_delta": row.get("z_delta"),
        "tag_decks_delta": row.get("tag_decks_delta"),
        "trend_status": row.get("trend_status") or row.get("snapshot_status"),
        "card_image_url": row.get("card_image_url"),
        "partner_card_image_urls": row.get("partner_card_image_urls"),
        "commander_cards": row.get("commander_cards"),
        "scryfall_uri": row.get("scryfall_uri"),
        "partner_scryfall_uris": row.get("partner_scryfall_uris"),
        "scryfall_card_names": row.get("scryfall_card_names"),
        "origin_set_code": origin_set.get("set_code"),
        "origin_set_name": origin_set.get("set_name"),
        "origin_released_at": origin_set.get("released_at"),
        "scryfall_set_uri": origin_set.get("scryfall_set_uri"),
        "set_uri": origin_set.get("set_uri"),
        "origin_sets": row.get("origin_sets"),
        **{field: row[field] for field in AFFINITY_MODEL_FIELDS if field in row},
    }


def export_set_files(df: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    sets_dir = output_dir / "sets"
    sets_dir.mkdir(parents=True, exist_ok=True)

    rows_by_set: dict[str, list[dict[str, Any]]] = {}
    set_info_by_code: dict[str, dict[str, Any]] = {}

    for row in df.to_dict(orient="records"):
        for origin_set in extract_origin_sets_from_row(row):
            set_code = origin_set["set_code"]
            rows_by_set.setdefault(set_code, []).append(
                build_set_commander_row(row, origin_set)
            )

            current_set_info = set_info_by_code.get(set_code)

            if current_set_info is None:
                set_info_by_code[set_code] = {
                    "set_code": set_code,
                    "set_name": origin_set.get("set_name") or set_code.upper(),
                    "released_at": origin_set.get("released_at"),
                    "scryfall_set_uri": origin_set.get("scryfall_set_uri"),
                    "set_uri": origin_set.get("set_uri"),
                    "file": f"sets/{safe_json_filename(set_code)}.json",
                }
                continue

            if not has_value(current_set_info.get("released_at")) and has_value(
                origin_set.get("released_at")
            ):
                current_set_info["released_at"] = origin_set.get("released_at")

            if not has_value(current_set_info.get("scryfall_set_uri")) and has_value(
                origin_set.get("scryfall_set_uri")
            ):
                current_set_info["scryfall_set_uri"] = origin_set.get(
                    "scryfall_set_uri"
                )

    set_index = []
    max_rows = 0

    for set_code, rows in rows_by_set.items():
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                not has_value(row.get("z")),
                -as_sort_float(row.get("z")),
                str(row.get("commander_name") or ""),
                str(row.get("tag_name") or ""),
                str(row.get("tag_slug") or ""),
            ),
        )
        write_json(sets_dir / f"{safe_json_filename(set_code)}.json", sorted_rows)

        set_info = set_info_by_code[set_code]
        set_info["commander_count"] = len(
            {
                row.get("commander_slug")
                for row in sorted_rows
                if has_value(row.get("commander_slug"))
            }
        )
        set_info["row_count"] = len(sorted_rows)
        set_index.append(set_info)
        max_rows = max(max_rows, len(sorted_rows))

    set_index.sort(
        key=lambda item: (
            str(item.get("released_at") or ""),
            str(item.get("set_name") or ""),
        ),
        reverse=True,
    )

    write_json(sets_dir / "index.json", set_index)

    return {
        "set_count": len(set_index),
        "max_rows_in_set_file": max_rows,
        "index_file": "sets/index.json",
        "file_pattern": "sets/<set_code>.json",
    }


def build_theme_bracket_signal_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag_name": row.get("tag_name"),
        "tag_slug": row.get("tag_slug"),
        "z": row.get("z"),
        "tag_decks": row.get("tag_decks"),
        "tag_affinity_pct": row.get("tag_affinity_pct"),
        "tag_affinity_adjusted_pct": row.get("tag_affinity_adjusted_pct"),
        "tag_affinity_lower_pct": row.get("tag_affinity_lower_pct"),
        "tag_affinity_upper_pct": row.get("tag_affinity_upper_pct"),
        "affinity_model_status": row.get("affinity_model_status"),
        "affinity_model_version": row.get("affinity_model_version"),
        "rank_within_tag_by_z": row.get("rank_within_tag_by_z"),
        **({"legacy_z": row["legacy_z"]} if "legacy_z" in row else {}),
    }


def build_theme_bracket_signal_rows(
    commander_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signal_rows = [
        build_theme_bracket_signal_row(row)
        for row in commander_rows
        if safe_json_filename(row.get("tag_slug")) in BRACKET_SIGNAL_TAG_SLUGS
    ]
    return sorted(signal_rows, key=lambda row: (
        not has_value(row.get("z")), -as_sort_float(row.get("z")),
        str(row.get("tag_name") or ""), str(row.get("tag_slug") or ""),
    ))


def qualify_theme_bracket_rows(group: pd.DataFrame, theme_slug: str) -> pd.DataFrame:
    if safe_json_filename(theme_slug) in BRACKET_SIGNAL_TAG_SLUGS:
        qualified = group.copy()
    else:
        z_scores = pd.to_numeric(group.get("z"), errors="coerce")
        qualified = group.loc[z_scores >= THEME_BRACKET_MIN_Z]

    if "commander_slug" in qualified.columns:
        qualified = qualified.sort_values("z", ascending=False, na_position="last")
        qualified = qualified.drop_duplicates(subset=["commander_slug"])

    return qualified


def build_theme_affinity_fields(theme_row: dict[str, Any]) -> dict[str, Any]:
    """Keep both score versions and the selected theme's own model metrics."""
    return {
        "theme_z": theme_row.get("z"),
        "theme_affinity_z": theme_row.get("z"),
        **({"theme_legacy_z": theme_row["legacy_z"]} if "legacy_z" in theme_row else {}),
        "theme_tag_decks": theme_row.get("tag_decks"),
        "theme_affinity_pct": theme_row.get("tag_affinity_pct"),
        "theme_affinity_adjusted_pct": theme_row.get("tag_affinity_adjusted_pct"),
        "theme_affinity_lower_pct": theme_row.get("tag_affinity_lower_pct"),
        "theme_affinity_upper_pct": theme_row.get("tag_affinity_upper_pct"),
        "theme_affinity_model_status": theme_row.get("affinity_model_status"),
        "theme_affinity_model_version": theme_row.get("affinity_model_version"),
        "theme_rank_within_tag_by_z": theme_row.get("rank_within_tag_by_z"),
    }


def build_theme_bracket_commander_row(
    theme_row: dict[str, Any],
    commander_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "commander_name": theme_row.get("commander_name"),
        "commander_slug": theme_row.get("commander_slug"),
        "color_identity": theme_row.get("color_identity"),
        "total_decks": theme_row.get("total_decks"),
        "card_image_url": theme_row.get("card_image_url"),
        "partner_card_image_urls": theme_row.get("partner_card_image_urls"),
        "commander_cards": theme_row.get("commander_cards"),
        "scryfall_uri": theme_row.get("scryfall_uri"),
        "partner_scryfall_uris": theme_row.get("partner_scryfall_uris"),
        "scryfall_card_names": theme_row.get("scryfall_card_names"),
        "theme_tag_name": theme_row.get("tag_name"),
        "theme_tag_slug": theme_row.get("tag_slug"),
        **build_theme_affinity_fields(theme_row),
        "bracket_tag_rows": build_theme_bracket_signal_rows(commander_rows),
    }


def export_theme_bracket_files(
    df: pd.DataFrame,
    tag_summary_df: pd.DataFrame | None,
    output_dir: Path,
) -> dict[str, Any]:
    theme_brackets_dir = output_dir / "theme-brackets"
    theme_brackets_dir.mkdir(parents=True, exist_ok=True)

    commander_rows_by_slug = {
        str(commander_slug): group.to_dict(orient="records")
        for commander_slug, group in df.groupby("commander_slug")
    }
    tag_index = build_tag_index(df, tag_summary_df)
    tag_info_by_slug = {
        safe_json_filename(row.get("tag_slug")): row
        for row in tag_index.to_dict(orient="records")
        if has_value(row.get("tag_slug"))
    }
    theme_index = []
    max_commanders = 0

    for raw_theme_slug, group in df.groupby("tag_slug"):
        theme_slug = safe_json_filename(raw_theme_slug)
        uses_bracket_rules_only = theme_slug in BRACKET_SIGNAL_TAG_SLUGS

        qualified = qualify_theme_bracket_rows(group, theme_slug)

        rows = []

        for theme_row in qualified.to_dict(orient="records"):
            commander_slug = str(theme_row.get("commander_slug") or "")
            rows.append(
                build_theme_bracket_commander_row(
                    theme_row,
                    commander_rows_by_slug.get(commander_slug, [theme_row]),
                )
            )

        filename = f"theme-brackets/{theme_slug}.json"
        write_json(output_dir / filename, {
            "qualification_score_field": "z",
            "minimum_z": THEME_BRACKET_MIN_Z,
            "rows": rows,
        })
        max_commanders = max(max_commanders, len(rows))

        theme_info = dict(tag_info_by_slug.get(theme_slug, {}))
        theme_info.setdefault("tag_slug", raw_theme_slug)
        theme_info.setdefault("tag_name", group.iloc[0].get("tag_name"))
        theme_info["qualified_commander_count"] = len(rows)
        theme_info["minimum_z"] = (
            None if uses_bracket_rules_only else THEME_BRACKET_MIN_Z
        )
        theme_info["qualification_rule"] = (
            "bracket_rules_only"
            if uses_bracket_rules_only
            else "theme_z_and_bracket_rules"
        )
        theme_info["qualification_score_field"] = "z"
        theme_info["file"] = filename
        theme_index.append(theme_info)

    theme_index.sort(
        key=lambda item: str(item.get("tag_name") or item.get("tag_slug") or "").lower()
    )
    write_json(theme_brackets_dir / "index.json", theme_index)

    return {
        "theme_count": len(theme_index),
        "minimum_z": THEME_BRACKET_MIN_Z,
        "qualification_score_field": "z",
        "max_commanders_in_theme_file": max_commanders,
        "index_file": "theme-brackets/index.json",
        "file_pattern": "theme-brackets/<tag_slug>.json",
    }


def export_theme_report(
    df: pd.DataFrame,
    tag_summary_df: pd.DataFrame | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Share commander signals once across all themes, without deck-count filters."""
    report_df = select_existing_columns(
        df,
        [
            "commander_slug",
            "commander_name",
            "total_decks",
            "color_identity",
            "commander_cards",
            "card_image_url",
            "partner_card_image_urls",
            "scryfall_uri",
            "partner_scryfall_uris",
            "scryfall_card_names",
            "tag_slug",
            "tag_name",
            "z",
            "tag_decks",
            "tag_affinity_pct",
            "rank_within_tag_by_z",
            *AFFINITY_MODEL_FIELDS,
        ],
    )
    tag_info_by_slug = {
        safe_json_filename(row.get("tag_slug")): row
        for row in build_tag_index(df, tag_summary_df).to_dict(orient="records")
        if has_value(row.get("tag_slug"))
    }
    themes = []
    included_commander_slugs = set()
    row_count = 0

    for raw_theme_slug, group in report_df.groupby("tag_slug"):
        qualified = qualify_theme_bracket_rows(group, raw_theme_slug)
        rows = [
            {
                "commander_slug": row.get("commander_slug"),
                **build_theme_affinity_fields(row),
            }
            for row in qualified.to_dict(orient="records")
        ]
        theme_info = tag_info_by_slug.get(safe_json_filename(raw_theme_slug), {})
        themes.append(
            {
                "tag_slug": raw_theme_slug,
                "tag_name": theme_info.get("tag_name") or group.iloc[0].get("tag_name"),
                "rows": rows,
            }
        )
        row_count += len(rows)
        included_commander_slugs.update(row["commander_slug"] for row in rows)

    commanders = []
    included_rows = report_df.loc[
        report_df["commander_slug"].isin(included_commander_slugs)
    ]

    for commander_slug, group in included_rows.groupby("commander_slug"):
        commander_rows = group.to_dict(orient="records")
        row = commander_rows[0]
        commanders.append(
            {
                "commander_slug": commander_slug,
                "commander_name": row.get("commander_name"),
                "total_decks": row.get("total_decks"),
                "color_identity": row.get("color_identity"),
                "commander_cards": row.get("commander_cards"),
                "card_image_url": row.get("card_image_url"),
                "partner_card_image_urls": row.get("partner_card_image_urls"),
                "scryfall_uri": row.get("scryfall_uri"),
                "partner_scryfall_uris": row.get("partner_scryfall_uris"),
                "scryfall_card_names": row.get("scryfall_card_names"),
                "bracket_tag_rows": build_theme_bracket_signal_rows(commander_rows),
            }
        )

    themes.sort(
        key=lambda item: str(item.get("tag_name") or item.get("tag_slug") or "").lower()
    )
    filename = "theme-report.json"
    write_json(
        output_dir / filename,
        {
            "commanders": commanders,
            "themes": themes,
            "qualification_score_field": "z",
            "minimum_z": THEME_BRACKET_MIN_Z,
        },
        compact=True,
    )

    return {
        "file": filename,
        "theme_count": len(themes),
        "commander_count": len(commanders),
        "row_count": row_count,
        "minimum_z": THEME_BRACKET_MIN_Z,
        "qualification_score_field": "z",
    }


def export_quirky_builds_report(
    df: pd.DataFrame,
    tag_summary_df: pd.DataFrame | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Export every observed commander/theme pair without theme eligibility gates.

    Row values follow one shared field list rather than repeating field names
    for every pair. Commander metadata and bracket signals are also shared so
    uncommon themes do not require downloading all complete tag files.
    """
    row_fields = ["commander_slug", *build_theme_affinity_fields({})]
    if "legacy_z" in df.columns:
        row_fields.append("theme_legacy_z")

    themes = []
    commanders = []
    row_count = 0

    if not df.empty:
        report_df = select_existing_columns(
            df,
            [
                "commander_slug",
                "commander_name",
                "total_decks",
                "color_identity",
                "commander_cards",
                "card_image_url",
                "partner_card_image_urls",
                "scryfall_uri",
                "partner_scryfall_uris",
                "scryfall_card_names",
                "tag_slug",
                "tag_name",
                "z",
                "tag_decks",
                "tag_affinity_pct",
                "rank_within_tag_by_z",
                *AFFINITY_MODEL_FIELDS,
            ],
        )
        tag_info_by_slug = {
            safe_json_filename(row.get("tag_slug")): row
            for row in build_tag_index(df, tag_summary_df).to_dict(orient="records")
            if has_value(row.get("tag_slug"))
        }

        for raw_theme_slug, group in report_df.groupby("tag_slug"):
            # Match canonical commander ordering when duplicate source rows
            # exist, without imposing any z-score or deck-count threshold.
            observed = sort_for_commander_detail(group).drop_duplicates(
                subset=["commander_slug"]
            )
            rows = []
            for row in observed.to_dict(orient="records"):
                fields = {
                    "commander_slug": row.get("commander_slug"),
                    **build_theme_affinity_fields(row),
                }
                rows.append([fields.get(field) for field in row_fields])

            theme_info = tag_info_by_slug.get(safe_json_filename(raw_theme_slug), {})
            themes.append({
                "tag_slug": raw_theme_slug,
                "tag_name": theme_info.get("tag_name") or group.iloc[0].get("tag_name"),
                "rows": rows,
            })
            row_count += len(rows)

        for commander_slug, group in report_df.groupby("commander_slug"):
            commander_rows = group.to_dict(orient="records")
            row = commander_rows[0]
            commanders.append({
                "commander_slug": commander_slug,
                "commander_name": row.get("commander_name"),
                "total_decks": row.get("total_decks"),
                "color_identity": row.get("color_identity"),
                "commander_cards": row.get("commander_cards"),
                "card_image_url": row.get("card_image_url"),
                "partner_card_image_urls": row.get("partner_card_image_urls"),
                "scryfall_uri": row.get("scryfall_uri"),
                "partner_scryfall_uris": row.get("partner_scryfall_uris"),
                "scryfall_card_names": row.get("scryfall_card_names"),
                "bracket_tag_rows": build_theme_bracket_signal_rows(commander_rows),
            })

    themes.sort(
        key=lambda item: str(item.get("tag_name") or item.get("tag_slug") or "").lower()
    )
    filename = "quirky-builds.json"
    schema_version = 1
    coverage = "all_observed_commander_tags"
    write_json(
        output_dir / filename,
        {
            "schema_version": schema_version,
            "coverage": coverage,
            "row_fields": row_fields,
            "commanders": commanders,
            "themes": themes,
        },
        compact=True,
    )

    return {
        "file": filename,
        "theme_count": len(themes),
        "commander_count": len(commanders),
        "row_count": row_count,
        "schema_version": schema_version,
        "coverage": coverage,
    }


def export_tag_files(
    df: pd.DataFrame,
    tag_summary_df: pd.DataFrame | None,
    output_dir: Path,
) -> dict[str, Any]:
    tags_dir = output_dir / "tags"
    tags_dir.mkdir(parents=True, exist_ok=True)

    tag_index = build_tag_index(df, tag_summary_df)
    write_records(tags_dir / "index.json", tag_index)

    tag_count = 0
    max_rows = 0

    for tag_slug, group in df.groupby("tag_slug"):
        safe_slug = safe_json_filename(tag_slug)
        sorted_group = sort_for_tag_detail(group)
        write_records(tags_dir / f"{safe_slug}.json", sorted_group)

        tag_count += 1
        max_rows = max(max_rows, len(sorted_group))

    return {
        "tag_count": tag_count,
        "max_rows_in_tag_file": max_rows,
        "index_file": "tags/index.json",
        "file_pattern": "tags/<tag_slug>.json",
    }


def export_leaderboard_pages(
    df: pd.DataFrame,
    output_dir: Path,
    page_size: int,
) -> dict[str, Any]:
    leaderboard_dir = output_dir / "leaderboard"
    leaderboard_dir.mkdir(parents=True, exist_ok=True)

    sorted_df = sort_for_leaderboard(df)

    total_rows = len(sorted_df)
    page_count = math.ceil(total_rows / page_size) if page_size > 0 else 0

    pages = []

    for page_index in range(page_count):
        start = page_index * page_size
        end = start + page_size

        page_number = page_index + 1
        filename = f"page_{page_number:04d}.json"

        page_df = sorted_df.iloc[start:end]
        write_records(leaderboard_dir / filename, page_df)

        pages.append(
            {
                "page": page_number,
                "file": f"leaderboard/{filename}",
                "start_row": start,
                "end_row": min(end, total_rows),
                "row_count": len(page_df),
            }
        )

    index = {
        "total_rows": total_rows,
        "page_size": page_size,
        "page_count": page_count,
        "pages": pages,
    }

    write_json(leaderboard_dir / "index.json", index)

    return {
        "total_rows": total_rows,
        "page_size": page_size,
        "page_count": page_count,
        "index_file": "leaderboard/index.json",
        "file_pattern": "leaderboard/page_0001.json",
    }


def copy_summary_files(processed_dir: Path, output_dir: Path) -> dict[str, str]:
    summaries_dir = output_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    copied = {}

    for filename in SUMMARY_FILES:
        source = processed_dir / filename
        data = read_json_object(source)

        destination = summaries_dir / filename
        write_json(destination, data)

        copied[filename] = f"summaries/{filename}"

    return copied


def reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON number: {value}")


def read_build_style_reports(directory: Path) -> dict[str, dict[str, Any]]:
    """Read optional samples independently; a bad sample must not break affinity export."""
    reports = {}
    if not directory.is_dir():
        return reports
    for path in sorted(directory.glob("*.json")):
        if path.stem == "index":
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite_constant)
            # The JSON decoder also accepts overflowing exponents (1e999) as
            # infinity; validate the entire tree before one bad sample can stop
            # the otherwise independent static affinity export.
            json.dumps(report, allow_nan=False)
            slug = report["commander_slug"]
            if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
                raise ValueError("invalid commander slug")
            if slug != path.stem:
                raise ValueError("report commander does not match its filename")
            datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
            if not isinstance(report["commander_name"], str) or not report["commander_name"].strip():
                raise ValueError("missing commander name")
            if not isinstance(report["profiles"]["profiles"], list):
                raise ValueError("missing profile list")
            if any(not isinstance(profile, dict) or not profile.get("tag_slug") or not profile.get("tag_name")
                   for profile in report["profiles"]["profiles"]):
                raise ValueError("invalid profile entry")
            analysis = report["deck_analysis"]
            if analysis["status"] not in {"fitted", "insufficient_data"}:
                raise ValueError("unknown deck analysis status")
            if not isinstance(analysis["packages"], list) or not isinstance(analysis["unique_decks"], int):
                raise ValueError("invalid deck analysis")
            if analysis["status"] == "fitted" and not analysis["packages"]:
                raise ValueError("fitted analysis has no packages")
            if any(not isinstance(package, dict) or "id" not in package or not isinstance(package.get("top_cards"), list)
                   for package in analysis["packages"]):
                raise ValueError("invalid package entry")
            # An attempted commander can legitimately have too little data for
            # either comparison. Preserve that outcome so catalog coverage does
            # not confuse an insufficient sample with an unprocessed commander.
            reports[slug] = report
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            print(f"Skipping invalid build-style report {path}: {error}")
    return reports


def read_build_collection_progress(directories: list[Path]) -> dict[str, Any] | None:
    """Publish a compact checkpoint snapshot without exposing the collection state."""
    candidates = []
    fields = ("updated_at", "cycle_started_at", "blocked", "blocked_until", "time_budget_reached",
              "no_progress", "resource_limit")
    nested_fields = {
        "capacity": ("evaluated_at", "status", "remaining_requests", "remaining_previews",
                     "remaining_overhead_requests", "inventory_complete", "distinct_decks_known",
                     "configured_requests_per_second", "effective_requests_per_second", "rate_limit_learned",
                     "observed_requests_per_second", "observed_rate_at", "cooldown_seconds", "estimated_minimum_seconds", "earliest_possible_finish_at",
                     "measured_projection_seconds", "projected_finish_at", "unavailable_previews", "analysis_pending"),
        "rate_control": ("status", "learned_rate", "throttles", "observed_rate_at", "configured_requests_per_second",
                         "effective_requests_per_second"),
        "measured_request_rate": ("successful_responses", "requests_started", "failures", "cooldowns",
                                  "current_requests_per_second", "observed_requests_per_second", "elapsed_seconds"),
    }
    for directory in directories:
        for path, checkpoint in ((directory / "_collection/state.json", True),
                                 (directory.parent / "build_style_batch.json", False)):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite_constant)
                json.dumps(payload, allow_nan=False)
                progress = payload.get("progress") if checkpoint else payload
                if not isinstance(progress, dict) or not isinstance(progress.get("capacity"), dict):
                    continue
                updated_at = progress.get("updated_at") or progress["capacity"].get("evaluated_at")
                updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    continue
                public = {key: progress[key] for key in fields if key in progress}
                public["updated_at"] = updated_at
                if progress.get("block_reason") in {"rate_limit", "access_restriction"}:
                    public["block_reason"] = progress["block_reason"]
                for key, allowed in nested_fields.items():
                    if isinstance(progress.get(key), dict):
                        public[key] = {field: progress[key][field] for field in allowed if field in progress[key]}
                control = progress.get("rate_control")
                throttle = control.get("last_throttle") if isinstance(control, dict) else None
                if isinstance(throttle, dict):
                    public.setdefault("rate_control", {})["last_throttle"] = {
                        key: throttle[key] for key in ("at", "rate_before", "rate_after") if key in throttle}
                candidates.append((updated.timestamp(), public))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def read_build_discovery_policy() -> dict[str, Any]:
    """The baseline documents a collector recovery, not a fixed report limit."""
    path = Path(__file__).resolve().parents[1] / "data/build-style-discovery.json"
    policy = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite_constant)
    if policy.get("schema_version") != 1 or policy.get("policy") != "current_collector":
        raise ValueError("Invalid build discovery policy")
    policy["min_total_decks"] = validate_minimum(policy.get("min_total_decks"))
    cutoff = datetime.fromisoformat(policy["collection_started_after"].replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("Build discovery cutoff must include a timezone")
    return policy


def current_build_discovery_slugs(directory: Path, reports: dict[str, dict[str, Any]],
                                  policy: dict[str, Any]) -> set[str]:
    """Require a completed current input without discarding older commander data."""
    try:
        state = json.loads((directory / "_collection/state.json").read_text(encoding="utf-8"),
                           parse_constant=reject_nonfinite_constant)
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or not isinstance(state.get("commanders"), dict)):
            return set()
    except (OSError, ValueError, TypeError):
        return set()
    cutoff = datetime.fromisoformat(policy["collection_started_after"].replace("Z", "+00:00"))
    eligible = set()
    for slug, report in reports.items():
        if not commander_is_eligible(report.get("total_edhrec_decks"), policy["min_total_decks"]):
            continue
        record, collection = state["commanders"].get(slug), report.get("collection")
        if not isinstance(record, dict) or not isinstance(collection, dict):
            continue
        try:
            collected_at = datetime.fromisoformat(record["last_collection_at"].replace("Z", "+00:00"))
            if collected_at.tzinfo is None or collected_at < cutoff:
                continue
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        version, dataset = record.get("analysis_version"), collection.get("dataset_sha256")
        if (isinstance(version, str) and version.strip()
                and collection.get("complete") is True and collection.get("target_mode") == "all"
                and collection.get("analysis_current") is True
                and isinstance(dataset, str) and dataset.strip()
                and ("collection_dataset_sha256" not in record or record["collection_dataset_sha256"] == dataset)
                and collection.get("analysis_dataset_sha256") == dataset):
            eligible.add(slug)
    return eligible


def summarize_build_style_packages(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose the selected model's package labels and bracket evidence only.

    Discovery must not fetch every full report just to display these details.
    Keep the rating timestamps so the frontend can age external verification in
    exactly the same way as it does on the commander page.
    """
    if analysis.get("status") != "fitted":
        return []
    packages = []
    for package in analysis["packages"]:
        suggestions = package.get("tag_suggestions")
        summary = {"id": package["id"], "tag_suggestions": [
            {key: tag[key] for key in ("tag_name", "tag_slug", "similarity", "support_tier", "deck_count",
                                       "commander_deck_count", "deck_fraction") if key in tag}
            for tag in (suggestions if isinstance(suggestions, list) else [])
            if isinstance(tag, dict) and isinstance(tag.get("tag_name"), str) and tag["tag_name"].strip()
        ]}
        if isinstance(package.get("name"), str):
            summary["name"] = package["name"]
        deck = package.get("generated_deck")
        if isinstance(deck, dict) and isinstance(deck.get("status"), str):
            generated = {"status": deck["status"]}
            validation = deck.get("validation", {})
            if isinstance(validation, dict):
                compact_validation = {}
                bracket = validation.get("bracket")
                if isinstance(bracket, dict) and "recommended" in bracket:
                    compact_validation["bracket"] = {"recommended": bracket["recommended"]}
                external = validation.get("external_rating")
                if isinstance(external, dict):
                    compact_validation["external_rating"] = {key: external[key] for key in (
                        "status", "checked_at", "playability_pct", "bracket",
                    ) if key in external}
                if compact_validation:
                    generated["validation"] = compact_validation
            statistics = deck.get("statistics")
            if isinstance(statistics, dict) and "recommended_bracket" in statistics:
                generated["statistics"] = {"recommended_bracket": statistics["recommended_bracket"]}
            summary["generated_deck"] = generated
        packages.append(summary)
    return packages


def export_build_style_files(
    df: pd.DataFrame,
    processed_dir: Path,
    output_dir: Path,
    previous_reports: dict[str, dict[str, Any]] | None = None,
    seed_dir: Path | None = None,
) -> dict[str, Any]:
    """Publish only actual, dated samples; no synthetic entries for unsampled commanders."""
    from edhrec_affinity.build_styles.deck_tags import normalize_report_deck_tags
    seed_dir = seed_dir if seed_dir is not None else Path(__file__).resolve().parents[1] / "data/build-styles"
    policy = read_build_discovery_policy()
    current_reports = read_build_style_reports(processed_dir / "build-styles")
    discovery_slugs = current_build_discovery_slugs(processed_dir / "build-styles", current_reports, policy)
    reports = read_build_style_reports(seed_dir)
    for incoming in (previous_reports or {}, current_reports):
        for slug, report in incoming.items():
            if incoming is current_reports and slug in discovery_slugs:
                # Freshly completed insufficient-data outcomes must also replace
                # an older fit; archived reports remain available for all others.
                reports[slug] = report
                continue
            old = reports.get(slug)
            if old:
                if old["deck_analysis"]["status"] == "fitted" and report["deck_analysis"]["status"] != "fitted":
                    continue
                newer = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00")).timestamp()
                previous = datetime.fromisoformat(old["generated_at"].replace("Z", "+00:00")).timestamp()
                if newer < previous and old["deck_analysis"]["status"] == report["deck_analysis"]["status"]:
                    continue
            reports[slug] = report
    known_slugs = set(df["commander_slug"].dropna().astype(str)) if "commander_slug" in df else set()
    catalog_counts = {str(row["commander_slug"]): valid_deck_count(row.get("total_decks"))
                      for row in df.to_dict("records") if row.get("commander_slug") is not None}
    catalog_cards = {str(row["commander_slug"]): {key: row[key] for key in (
        "commander_cards", "card_image_url", "partner_card_image_urls", "scryfall_uri",
        "partner_scryfall_uris", "scryfall_card_names",
    ) if key in row and has_value(row[key])}
        for row in build_commander_index(df).to_dict("records")} if "commander_slug" in df else {}
    eligible_catalog = {slug for slug in known_slugs
                        if commander_is_eligible(catalog_counts.get(slug), policy["min_total_decks"])}
    if (output_dir / "build-styles").is_dir():
        for path in (output_dir / "build-styles").glob("*.json"):
            path.unlink()
    entries = []
    for slug, report in sorted(reports.items()):
        if slug not in known_slugs:
            continue
        # An archived fit cannot override a newer population check. Keep its
        # evidence available, while making its ineligibility explicit to direct
        # commander-page readers as well as the discovery index.
        current_count = valid_deck_count(current_reports.get(slug, {}).get("total_edhrec_decks"))
        if slug not in eligible_catalog:
            report["total_edhrec_decks"] = catalog_counts.get(slug)
        elif current_count is not None and current_count < policy["min_total_decks"]:
            report["total_edhrec_decks"] = current_count
            report.setdefault("collection", {})["analysis_current"] = False
        filename = f"build-styles/{slug}.json"
        normalize_report_deck_tags(report)
        write_json(output_dir / filename, report, compact=True)
        if slug not in eligible_catalog or not commander_is_eligible(
                report.get("total_edhrec_decks"), policy["min_total_decks"]):
            continue
        analysis = report["deck_analysis"]
        entry = {
            "commander_slug": slug, "commander_name": report["commander_name"],
            "total_edhrec_decks": valid_deck_count(report.get("total_edhrec_decks")),
            "file": filename, "generated_at": report["generated_at"],
            "profile_count": len(report["profiles"]["profiles"]),
            "package_count": len(analysis["packages"]),
            "packages": summarize_build_style_packages(analysis),
            "unique_decks": analysis["unique_decks"], "status": analysis["status"],
            **catalog_cards.get(slug, {}),
        }
        collection = report.get("collection", {})
        if isinstance(collection, dict):
            # Keep source observations distinct from generated_at. The catalog
            # can show partial coverage and age without fetching every report.
            summary = {key: collection[key] for key in (
                "target_mode", "complete", "frame_decks", "previews_attempted", "previews_loaded", "analysis_current", "unavailable_previews",
            ) if key in collection}
            if isinstance(collection.get("freshness"), dict):
                summary["freshness"] = {key: value for key, value in collection["freshness"].items()
                    if key in {"target_hours", "max_age_hours", "oldest_source_at", "newest_source_at",
                               "stale_previews", "dated_previews", "undated_previews", "observed_previews",
                               "index_source_at", "index_stale", "source_complete", "basis", "oldest_verified_at",
                               "newest_verified_at", "source_dated_previews", "source_undated_previews", "unchanged_verified"}}
            if summary:
                entry["collection"] = summary
        entries.append(entry)
    index = {
        "schema_version": 1, "coverage": "sampled_commanders", "total_available": len(entries),
        "total_catalog": len(eligible_catalog), "total_pending": len(eligible_catalog) - len(entries),
        "min_total_decks": policy["min_total_decks"],
        "total_fitted": sum(entry["status"] == "fitted" for entry in entries),
        "total_insufficient": sum(entry["status"] == "insufficient_data" for entry in entries),
        "commanders": entries,
    }
    visible_slugs = sorted(entry["commander_slug"] for entry in entries if entry["commander_slug"] in discovery_slugs)
    index["discovery"] = {key: policy[key] for key in (
        "policy", "min_total_decks", "collection_started_after", "baseline_run_id", "baseline_previews", "baseline_report_count")}
    index["discovery"].update(commander_slugs=visible_slugs, total_available=len(visible_slugs),
                              total_hidden=len(entries) - len(visible_slugs))
    if any("freshness" in entry.get("collection", {}) for entry in entries):
        index["freshness"] = summarize_build_style_freshness(entries)
    progress = read_build_collection_progress([processed_dir / "build-styles", seed_dir])
    if progress is not None:
        index["collection_progress"] = progress
    write_json(output_dir / "build-styles/index.json", index)
    return {"index_file": "build-styles/index.json", "file_pattern": "build-styles/<commander_slug>.json",
            "commander_count": len(entries), "coverage": "sampled_commanders"}


def summarize_build_style_freshness(entries: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Export a dated summary; clients recompute age as the static site gets older.

    Partial, overdue and unknown counts can overlap. A recent analysis timestamp
    does not establish source freshness or full-index coverage.
    """
    now = now or datetime.now(timezone.utc)
    result = {"target_hours": 96, "evaluated_at": now.isoformat(), "target_met": 0,
              "partial": 0, "overdue": 0, "unknown": 0}

    def age_hours(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            age = (now - parsed).total_seconds() / 3600
            return age if age >= -5 / 60 else None
        except (AttributeError, TypeError, ValueError):
            return None

    for entry in entries:
        collection = entry.get("collection", {})
        evidence = collection.get("freshness", {})
        frame, attempted = collection.get("frame_decks"), collection.get("previews_attempted")
        complete = collection.get("target_mode") == "all" and collection.get("complete") is True \
            and type(frame) is int and type(attempted) is int and frame >= 0 and attempted == frame
        result["partial"] += not complete
        fields = ("oldest_verified_at", "newest_verified_at") if evidence.get("basis") == "current_index_versions" \
            else ("oldest_source_at", "newest_source_at")
        oldest, newest = (age_hours(evidence.get(field)) for field in fields)
        dated, undated, observed, stale = (evidence.get(key) for key in (
            "dated_previews", "undated_previews", "observed_previews", "stale_previews"))
        counts_known = all(type(value) is int and value >= 0 for value in (dated, undated, observed, stale))
        known = counts_known and observed > 0 and dated > 0 and dated + undated == observed \
            and stale <= observed and oldest is not None and newest is not None and oldest >= newest
        empty = frame == 0 and observed == 0 and undated == 0 and stale == 0
        unknown = not empty and (not known or undated > 0)
        result["unknown"] += unknown
        result["overdue"] += oldest is not None and oldest >= 96
        index_age = age_hours(evidence.get("index_source_at"))
        result["target_met"] += complete and collection.get("analysis_current") is not False \
            and collection.get("unavailable_previews") == 0 \
            and (empty or known and undated == 0 and stale == 0 and oldest < 96) \
            and index_age is not None and index_age < 96 and evidence.get("index_stale") is False
    return result


def export_full_site_data(
    processed_dir: Path,
    output_dir: Path,
    page_size: int,
    clean_output: bool,
    build_style_seed_dir: Path | None = None,
) -> dict[str, Any]:
    # Build samples have their own collection dates and can outlive an affinity
    # snapshot. Read them before cleaning, then retain only current commanders.
    previous_build_styles = read_build_style_reports(output_dir / "build-styles")
    if clean_output and output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    affinity_path = processed_dir / "affinity_rows_with_trends.json"
    df = read_json_records(affinity_path)
    df = normalize_columns(df)
    df = ensure_commander_card_metadata(df, processed_dir / "scryfall_lookup_cache.json")
    df = ensure_origin_set_metadata(df)

    tag_summary_path = processed_dir / "tag_summary.json"
    tag_summary_df = None

    if tag_summary_path.exists():
        tag_summary_df = pd.read_json(tag_summary_path)

    summary_files = copy_summary_files(processed_dir, output_dir)
    commander_export = export_commander_files(df, output_dir)
    set_export = export_set_files(df, output_dir)
    tag_export = export_tag_files(df, tag_summary_df, output_dir)
    theme_bracket_export = export_theme_bracket_files(
        df,
        tag_summary_df,
        output_dir,
    )
    theme_report_export = export_theme_report(df, tag_summary_df, output_dir)
    quirky_builds_export = export_quirky_builds_report(df, tag_summary_df, output_dir)
    leaderboard_export = export_leaderboard_pages(df, output_dir, page_size)
    build_style_export = export_build_style_files(
        df, processed_dir, output_dir, previous_build_styles, build_style_seed_dir,
    )

    manifest = {
        "dataset_type": "full_sharded_static_export",
        "processed_dir": str(processed_dir),
        "output_dir": str(output_dir),
        "total_rows": int(len(df)),
        "unique_commanders": int(df["commander_slug"].nunique())
        if "commander_slug" in df.columns
        else None,
        "unique_tags": int(df["tag_slug"].nunique()) if "tag_slug" in df.columns else None,
        "summary_files": summary_files,
        "commander_export": commander_export,
        "set_export": set_export,
        "tag_export": tag_export,
        "theme_bracket_export": theme_bracket_export,
        "theme_report_export": theme_report_export,
        "quirky_builds_export": quirky_builds_export,
        "leaderboard_export": leaderboard_export,
        "build_style_export": build_style_export,
    }
    if "affinity_model_version" in df.columns:
        versions = df["affinity_model_version"].dropna().unique().tolist()
        manifest["affinity_model_version"] = versions[0] if len(versions) == 1 else None

    write_json(output_dir / "site_manifest.json", manifest)

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a complete sharded static website dataset."
    )

    parser.add_argument(
        "--processed-dir",
        required=True,
        type=Path,
        help="Processed snapshot directory, such as data/processed/2026-05-07.",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Output directory, such as frontend/public/data/latest.",
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=LEADERBOARD_PAGE_SIZE,
        help="Number of rows per leaderboard page shard.",
    )

    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not remove the output directory before exporting.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    manifest = export_full_site_data(
        processed_dir=args.processed_dir,
        output_dir=args.output_dir,
        page_size=args.page_size,
        clean_output=not args.no_clean,
    )

    print(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
