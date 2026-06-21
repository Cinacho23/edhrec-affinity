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
from pathlib import Path
from typing import Any

import pandas as pd


LEADERBOARD_PAGE_SIZE = 500

SUMMARY_FILES = [
    "analysis_summary.json",
    "trend_summary.json",
    "tag_summary.json",
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    sanitized = sanitize_for_json(data)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            sanitized,
            file,
            indent=2,
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
        return df.sort_values("z", ascending=False, na_position="last")

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
        "scryfall_uri": row.get("scryfall_uri"),
        "partner_scryfall_uris": row.get("partner_scryfall_uris"),
        "scryfall_card_names": row.get("scryfall_card_names"),
        "origin_set_code": origin_set.get("set_code"),
        "origin_set_name": origin_set.get("set_name"),
        "origin_released_at": origin_set.get("released_at"),
        "scryfall_set_uri": origin_set.get("scryfall_set_uri"),
        "set_uri": origin_set.get("set_uri"),
        "origin_sets": row.get("origin_sets"),
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
                row.get("z") is None,
                -as_sort_float(row.get("z")),
                str(row.get("commander_name") or ""),
                str(row.get("tag_name") or ""),
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


def export_full_site_data(
    processed_dir: Path,
    output_dir: Path,
    page_size: int,
    clean_output: bool,
) -> dict[str, Any]:
    if clean_output and output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    affinity_path = processed_dir / "affinity_rows_with_trends.json"
    df = read_json_records(affinity_path)
    df = normalize_columns(df)
    df = ensure_origin_set_metadata(df)

    tag_summary_path = processed_dir / "tag_summary.json"
    tag_summary_df = None

    if tag_summary_path.exists():
        tag_summary_df = pd.read_json(tag_summary_path)

    summary_files = copy_summary_files(processed_dir, output_dir)
    commander_export = export_commander_files(df, output_dir)
    set_export = export_set_files(df, output_dir)
    tag_export = export_tag_files(df, tag_summary_df, output_dir)
    leaderboard_export = export_leaderboard_pages(df, output_dir, page_size)

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
        "leaderboard_export": leaderboard_export,
    }

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
