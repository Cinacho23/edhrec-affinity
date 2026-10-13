#!/usr/bin/env python3
"""Upgrade a restored processed snapshot without scraping EDHREC again.

Reuses the original counts, preserves enrichment and count/raw-affinity trends,
and suppresses score movement across incompatible algorithms. Only the supplied
snapshot directory is written; earlier historical snapshots remain untouched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from edhrec_affinity.affinity_model import AFFINITY_MODEL_VERSION
from edhrec_affinity.analysis import (
    AFFINITY_ROWS_FILENAME,
    ANALYSIS_SUMMARY_FILENAME,
    DEFAULT_MIN_TAG_DECKS,
    DEFAULT_MIN_TOTAL_DECKS,
    GLOBAL_LEADERBOARD_FILENAME,
    REQUIRED_INPUT_COLUMNS,
    TAG_RANKINGS_FILENAME,
    TAG_SUMMARY_FILENAME,
    build_analysis_summary,
    build_global_leaderboard,
    build_tag_rankings,
    build_tag_summary,
    prepare_analysis_table,
    write_json_object,
    write_json_records,
)
from edhrec_affinity.trends import (
    AFFINITY_ROWS_WITH_TRENDS_FILENAME,
    KEY_COLUMNS,
    LEGACY_MODEL_VERSION,
    TREND_ROWS_FILENAME,
    TREND_SUMMARY_FILENAME,
    validate_required_key_columns,
    validate_unique_keys,
)


CLEAN_FILENAME = "commander_tags_clean.json"
SCORE_PREVIOUS_AND_DELTA_COLUMNS = [
    "z_previous", "z_delta", "rank_within_tag_by_z_previous",
    "rank_within_tag_by_z_delta", "rank_delta",
]
SNAPSHOT_COLUMNS = {
    "current_snapshot", "previous_snapshot", "snapshot_status", "trend_status",
    "score_trend_status",
}


def read_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"Expected a non-empty JSON list of objects in {path}.")
    return data


def read_object_if_present(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return data


def canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Use the same canonical precedence as the existing static exporter."""
    result = df.copy()
    for column in ["total_decks", "tag_decks", "z", "rank_within_tag_by_z"]:
        if column not in result:
            for source in [f"{column}_current", f"{column}_x"]:
                if source in result:
                    result[column] = result[source]
                    break
    return result


def is_trend_column(column: str) -> bool:
    return column in SNAPSHOT_COLUMNS or column.endswith(
        ("_current", "_previous", "_delta", "_pct_change")
    )


def suppress_old_score_trends(rows: pd.DataFrame) -> pd.DataFrame:
    result = rows.copy()
    result["z_current"] = result["z"]
    result["rank_within_tag_by_z_current"] = result["rank_within_tag_by_z"]
    for column in SCORE_PREVIOUS_AND_DELTA_COLUMNS:
        result[column] = pd.NA
    result["affinity_model_version_current"] = AFFINITY_MODEL_VERSION
    if "affinity_model_version_previous" not in result:
        result["affinity_model_version_previous"] = LEGACY_MODEL_VERSION
    result["affinity_model_status_current"] = result["affinity_model_status"]
    if "affinity_model_status_previous" not in result:
        result["affinity_model_status_previous"] = "legacy"
    result["score_trend_status"] = "algorithm_changed"
    if "snapshot_status" in result:
        no_previous = result["snapshot_status"].eq("no_previous_snapshot")
        no_pair = result["snapshot_status"].isin(["new_pair", "removed_pair"])
        result.loc[no_previous, "score_trend_status"] = "no_previous_snapshot"
        result.loc[no_pair, "score_trend_status"] = "no_previous_pair"
        result.loc[no_previous | no_pair, [
            "affinity_model_version_previous", "affinity_model_status_previous",
        ]] = pd.NA
    return result


def recompute_affinity(processed_dir: Path) -> dict[str, Any]:
    processed_dir = Path(processed_dir)
    current_path = processed_dir / AFFINITY_ROWS_WITH_TRENDS_FILENAME
    source_records = read_records(current_path)
    current = canonical_columns(pd.DataFrame(source_records))
    validate_required_key_columns(current, source_name=str(current_path))
    validate_unique_keys(current, source_name=str(current_path))

    if (
        "affinity_model_version" in current
        and current["affinity_model_version"].eq(AFFINITY_MODEL_VERSION).all()
    ):
        return {
            "recomputed": False,
            "reason": "already_current_model",
            "affinity_model_version": AFFINITY_MODEL_VERSION,
            "row_count": len(current),
        }

    clean_path = processed_dir / CLEAN_FILENAME
    raw = pd.DataFrame(read_records(clean_path)) if clean_path.exists() else current
    input_path = clean_path if clean_path.exists() else current_path
    validate_required_key_columns(raw, source_name=str(input_path))
    validate_unique_keys(raw, source_name=str(input_path))
    missing = REQUIRED_INPUT_COLUMNS - set(raw.columns)
    if missing:
        raise ValueError(f"Cached source is missing required analysis columns: {sorted(missing)}")

    # Never attach trends/enrichment from a different count snapshot.
    raw_indexed = raw.set_index(KEY_COLUMNS)
    current_indexed = current.set_index(KEY_COLUMNS)
    if set(raw_indexed.index) != set(current_indexed.index):
        raise ValueError("Clean and cached affinity rows have different commander/tag keys.")
    raw_indexed = raw_indexed.reindex(current_indexed.index)
    for column in ["total_decks", "tag_decks"]:
        if column not in current_indexed or not pd.to_numeric(
            raw_indexed[column], errors="raise"
        ).equals(pd.to_numeric(current_indexed[column], errors="raise")):
            # Series.equals includes dtype; equal integer/float counts are safe.
            if column not in current_indexed or not (
                pd.to_numeric(raw_indexed[column], errors="raise")
                == pd.to_numeric(current_indexed[column], errors="raise")
            ).all():
                raise ValueError(f"Clean and cached affinity rows disagree on {column}.")
    if "source_type" not in current_indexed or not (
        raw_indexed["source_type"] == current_indexed["source_type"]
    ).all():
        raise ValueError("Clean and cached affinity rows disagree on source_type.")

    previous_summary = read_object_if_present(processed_dir / ANALYSIS_SUMMARY_FILENAME)
    min_total = int(previous_summary.get("min_total_decks_default", DEFAULT_MIN_TOTAL_DECKS))
    min_tag = int(previous_summary.get("min_tag_decks_default", DEFAULT_MIN_TAG_DECKS))
    analyzed = prepare_analysis_table(
        raw[sorted(REQUIRED_INPUT_COLUMNS)].copy(),
        min_total_decks=min_total,
        min_tag_decks=min_tag,
    )

    # Preserve the serialized descriptive score exactly at bracket boundaries.
    # Recomputing from counts can differ slightly from the old JSON's rounding.
    previous_legacy = {}
    for record in source_records:
        key = tuple(record[column] for column in KEY_COLUMNS)
        value = record.get("legacy_z") if "legacy_z" in record else record.get("z")
        if "legacy_z" not in record and "z" not in record:
            value = record.get("z_current", record.get("z_x"))
        previous_legacy[key] = value
    analyzed["legacy_z"] = [
        previous_legacy[tuple(row)]
        for row in analyzed[KEY_COLUMNS].itertuples(index=False, name=None)
    ]

    generated_columns = [column for column in analyzed if column not in KEY_COLUMNS]
    merged = current.drop(columns=generated_columns, errors="ignore").merge(
        analyzed, on=KEY_COLUMNS, how="left", validate="one_to_one",
    )
    merged = suppress_old_score_trends(merged)
    affinity = merged[[column for column in merged if not is_trend_column(column)]].copy()
    tag_rankings = build_tag_rankings(affinity)
    leaderboard = build_global_leaderboard(affinity)
    tag_summary = build_tag_summary(affinity)
    summary = build_analysis_summary(
        input_path=input_path, output_dir=processed_dir, analysis_df=affinity,
        global_leaderboard=leaderboard, tag_summary=tag_summary,
        min_total_decks=min_total, min_tag_decks=min_tag,
    )
    summary["recomputed_from_cached_counts"] = True

    # Keep the detailed trend audit in agreement with the migrated website rows.
    trend_path = processed_dir / TREND_ROWS_FILENAME
    migrated_trends = None
    if trend_path.exists():
        trends = pd.DataFrame(read_records(trend_path))
        validate_required_key_columns(trends, source_name=str(trend_path))
        validate_unique_keys(trends, source_name=str(trend_path))
        updated_columns = [
            "z_current", "rank_within_tag_by_z_current",
            *SCORE_PREVIOUS_AND_DELTA_COLUMNS,
            "affinity_model_version_current", "affinity_model_status_current",
            "affinity_model_version_previous", "affinity_model_status_previous",
            "score_trend_status",
        ]
        updates = merged[KEY_COLUMNS + updated_columns].copy()
        migrated_trends = trends.drop(columns=updated_columns, errors="ignore").merge(
            updates, on=KEY_COLUMNS, how="left", validate="one_to_one",
        )
        removed = migrated_trends["snapshot_status"].eq("removed_pair")
        migrated_trends.loc[removed, "score_trend_status"] = "no_previous_pair"
        migrated_keys = pd.MultiIndex.from_frame(migrated_trends[KEY_COLUMNS])
        current_keys = migrated_keys.isin(pd.MultiIndex.from_frame(merged[KEY_COLUMNS]))
        refreshed = merged.set_index(KEY_COLUMNS).reindex(migrated_keys)
        # First-run trend files also contain unsuffixed canonical metrics.
        # Refresh those for current rows while keeping removed-pair history.
        for column in [
            "z", "rank_within_tag_by_z", "legacy_z",
            "affinity_model_version", "affinity_model_status",
        ]:
            if column in migrated_trends:
                migrated_trends.loc[current_keys, column] = refreshed.loc[
                    current_keys, column
                ].to_numpy()
        original_trends = trends.set_index(KEY_COLUMNS).reindex(migrated_keys)
        for column in [
            "z_previous", "rank_within_tag_by_z_previous",
            "affinity_model_version_previous", "affinity_model_status_previous",
        ]:
            if column in original_trends:
                migrated_trends.loc[removed, column] = original_trends.loc[
                    removed.to_numpy(), column
                ].to_numpy()

    trend_summary = read_object_if_present(processed_dir / TREND_SUMMARY_FILENAME)
    if trend_summary:
        trend_summary["rows_with_z_delta"] = 0
        trend_summary["rows_with_rank_delta"] = 0
        trend_summary["affinity_model_version"] = AFFINITY_MODEL_VERSION
        trend_summary["recomputed_from_cached_counts"] = True
        status_rows = migrated_trends if migrated_trends is not None else merged
        trend_summary["score_trend_status_row_counts"] = {
            str(status): int(count)
            for status, count in status_rows["score_trend_status"].value_counts().items()
        }

    write_json_records(affinity, processed_dir / AFFINITY_ROWS_FILENAME)
    write_json_records(merged, current_path)
    write_json_records(tag_rankings, processed_dir / TAG_RANKINGS_FILENAME)
    write_json_records(leaderboard, processed_dir / GLOBAL_LEADERBOARD_FILENAME)
    write_json_records(tag_summary, processed_dir / TAG_SUMMARY_FILENAME)
    write_json_object(summary, processed_dir / ANALYSIS_SUMMARY_FILENAME)
    if migrated_trends is not None:
        write_json_records(migrated_trends, trend_path)
    if trend_summary:
        write_json_object(trend_summary, processed_dir / TREND_SUMMARY_FILENAME)
    return {
        "recomputed": True,
        "input_path": str(input_path),
        "affinity_model_version": AFFINITY_MODEL_VERSION,
        "row_count": len(merged),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(recompute_affinity(args.processed_dir), indent=2))


if __name__ == "__main__":
    main()
