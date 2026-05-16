#!/usr/bin/env python3
"""
export_compact_site_data.py

Creates smaller website-ready JSON files from the large processed outputs.

This avoids committing or deploying massive full-table JSON files like:
- affinity_rows_with_trends.json
- tag_rankings.json
- trend_rows.json

The frontend can use these compact files for Version 1 deployment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_TOP_GLOBAL_ROWS = 5000
DEFAULT_TOP_ROWS_PER_TAG = 100
DEFAULT_TOP_TAGS_PER_COMMANDER = 30


def read_json_records(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")

    return pd.read_json(path)


def write_json_records(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(path, orient="records", indent=2)


def copy_json(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Missing input file: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    with source.open("r", encoding="utf-8") as file:
        data = json.load(file)

    with destination.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize common Chat 8 trend-suffixed fields into stable frontend names.
    This is defensive because trend outputs may include fields like z_current,
    total_decks_current, tag_decks_current, etc.
    """
    rename_map = {
        "total_decks_current": "total_decks",
        "tag_decks_current": "tag_decks",
        "affinity_pct_current": "tag_affinity_pct",
        "z_current": "z",
        "rank_within_tag_by_z_current": "rank_within_tag_by_z",
    }

    for source, target in rename_map.items():
        if source in df.columns:
            df[target] = df[source]

    return df


def select_existing_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    existing = [column for column in columns if column in df.columns]
    return df[existing].copy()


def build_global_leaderboard(
    df: pd.DataFrame,
    top_n: int,
) -> pd.DataFrame:
    columns = [
        "commander_name",
        "commander_slug",
        "tag_name",
        "tag_slug",
        "color_identity",
        "total_decks",
        "tag_decks",
        "tag_affinity_pct",
        "tag_affinity_pct_display",
        "z",
        "percentile_within_tag",
        "rank_within_tag_by_z",
        "rank_delta",
        "snapshot_status",
        "card_image_url",
        "partner_card_image_urls",
        "scryfall_uri",
        "partner_scryfall_uris",
    ]

    compact = select_existing_columns(df, columns)

    if "z" in compact.columns:
        compact = compact.sort_values("z", ascending=False, na_position="last")

    return compact.head(top_n)


def build_tag_rankings_top_by_tag(
    df: pd.DataFrame,
    top_per_tag: int,
) -> pd.DataFrame:
    columns = [
        "commander_name",
        "commander_slug",
        "tag_name",
        "tag_slug",
        "color_identity",
        "total_decks",
        "tag_decks",
        "tag_affinity_pct",
        "tag_affinity_pct_display",
        "z",
        "percentile_within_tag",
        "rank_within_tag_by_z",
        "rank_delta",
        "snapshot_status",
        "card_image_url",
        "partner_card_image_urls",
    ]

    compact = select_existing_columns(df, columns)

    if "tag_slug" not in compact.columns:
        return compact.head(0)

    if "rank_within_tag_by_z" in compact.columns:
        compact = compact.sort_values(
            ["tag_slug", "rank_within_tag_by_z"],
            ascending=[True, True],
            na_position="last",
        )

    return compact.groupby("tag_slug", group_keys=False).head(top_per_tag)


def build_commander_search_index(
    df: pd.DataFrame,
    top_tags_per_commander: int,
) -> pd.DataFrame:
    columns = [
        "commander_name",
        "commander_slug",
        "color_identity",
        "total_decks",
        "card_image_url",
        "partner_card_image_urls",
        "scryfall_uri",
        "partner_scryfall_uris",
    ]

    base = select_existing_columns(df, columns).drop_duplicates("commander_slug")

    tag_columns = [
        "commander_slug",
        "tag_name",
        "tag_slug",
        "tag_decks",
        "tag_affinity_pct",
        "tag_affinity_pct_display",
        "z",
        "rank_within_tag_by_z",
        "percentile_within_tag",
    ]

    tags = select_existing_columns(df, tag_columns)

    if "z" in tags.columns:
        tags = tags.sort_values(
            ["commander_slug", "z"],
            ascending=[True, False],
            na_position="last",
        )

    tags = tags.groupby("commander_slug", group_keys=False).head(top_tags_per_commander)

    tag_map = (
        tags.groupby("commander_slug")
        .apply(lambda group: group.drop(columns=["commander_slug"]).to_dict("records"))
        .to_dict()
    )

    base["top_tags"] = base["commander_slug"].map(tag_map).apply(
        lambda value: value if isinstance(value, list) else []
    )

    return base


def export_compact_site_data(
    processed_dir: Path,
    output_dir: Path,
    top_global_rows: int,
    top_rows_per_tag: int,
    top_tags_per_commander: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    affinity_path = processed_dir / "affinity_rows_with_trends.json"
    affinity_df = read_json_records(affinity_path)
    affinity_df = normalize_columns(affinity_df)

    copy_json(
        processed_dir / "analysis_summary.json",
        output_dir / "analysis_summary.json",
    )
    copy_json(
        processed_dir / "trend_summary.json",
        output_dir / "trend_summary.json",
    )
    copy_json(
        processed_dir / "tag_summary.json",
        output_dir / "tag_summary.json",
    )

    global_leaderboard = build_global_leaderboard(
        affinity_df,
        top_n=top_global_rows,
    )
    write_json_records(
        global_leaderboard,
        output_dir / "global_leaderboard_top.json",
    )

    tag_rankings = build_tag_rankings_top_by_tag(
        affinity_df,
        top_per_tag=top_rows_per_tag,
    )
    write_json_records(
        tag_rankings,
        output_dir / "tag_rankings_top_by_tag.json",
    )

    commander_index = build_commander_search_index(
        affinity_df,
        top_tags_per_commander=top_tags_per_commander,
    )
    write_json_records(
        commander_index,
        output_dir / "commander_search_index.json",
    )

    manifest = {
        "processed_dir": str(processed_dir),
        "output_dir": str(output_dir),
        "top_global_rows": top_global_rows,
        "top_rows_per_tag": top_rows_per_tag,
        "top_tags_per_commander": top_tags_per_commander,
        "input_affinity_rows": int(len(affinity_df)),
        "global_leaderboard_top_rows": int(len(global_leaderboard)),
        "tag_rankings_top_rows": int(len(tag_rankings)),
        "commander_search_index_rows": int(len(commander_index)),
        "files": {
            "analysis_summary": "analysis_summary.json",
            "trend_summary": "trend_summary.json",
            "tag_summary": "tag_summary.json",
            "global_leaderboard_top": "global_leaderboard_top.json",
            "tag_rankings_top_by_tag": "tag_rankings_top_by_tag.json",
            "commander_search_index": "commander_search_index.json",
        },
    }

    with (output_dir / "site_data_manifest.json").open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export compact website-ready JSON files."
    )

    parser.add_argument(
        "--processed-dir",
        required=True,
        type=Path,
        help="Processed snapshot directory.",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Output directory for compact frontend JSON files.",
    )

    parser.add_argument(
        "--top-global-rows",
        default=DEFAULT_TOP_GLOBAL_ROWS,
        type=int,
    )

    parser.add_argument(
        "--top-rows-per-tag",
        default=DEFAULT_TOP_ROWS_PER_TAG,
        type=int,
    )

    parser.add_argument(
        "--top-tags-per-commander",
        default=DEFAULT_TOP_TAGS_PER_COMMANDER,
        type=int,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    manifest = export_compact_site_data(
        processed_dir=args.processed_dir,
        output_dir=args.output_dir,
        top_global_rows=args.top_global_rows,
        top_rows_per_tag=args.top_rows_per_tag,
        top_tags_per_commander=args.top_tags_per_commander,
    )

    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()