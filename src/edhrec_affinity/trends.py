"""
trends.py

Chat 8 - Historical Trends and Weekly Change Tracking

This module compares one analyzed EDHREC snapshot against a previous analyzed
snapshot.

Input:
    data/processed/<date>/affinity_rows.json

Outputs:
    data/processed/<date>/trend_rows.json
    data/processed/<date>/affinity_rows_with_trends.json
    data/processed/<date>/trend_summary.json

Important first-run behavior:
    If there is no previous snapshot yet, this script does NOT fail.
    It writes trend-shaped output with:
        snapshot_status = "no_previous_snapshot"
    and previous/delta fields set to null.

That means you can run this now with only one dataset to validate the pipeline.
The first meaningful trend comparison happens once you have a second dated
processed snapshot.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------

AFFINITY_ROWS_FILENAME = "affinity_rows.json"
TREND_ROWS_FILENAME = "trend_rows.json"
AFFINITY_ROWS_WITH_TRENDS_FILENAME = "affinity_rows_with_trends.json"
TREND_SUMMARY_FILENAME = "trend_summary.json"


# ---------------------------------------------------------------------------
# Comparison rules
# ---------------------------------------------------------------------------

# This is the stable identity of one commander-tag row across snapshots.
# Example:
#   jasmine-boreal-of-the-seven + vanilla
#   the-tenth-doctor-rose-tyler + exile
KEY_COLUMNS = ["commander_slug", "tag_slug"]


# These columns make trend_rows.json easier to inspect manually.
# They are not all required because the website/data schema may evolve.
IDENTITY_COLUMNS = [
    "commander_name",
    "commander_slug",
    "tag_name",
    "tag_slug",
    "source_type",
]


# Numeric columns to compare across snapshots.
#
# Keys are the column names expected from Chat 7 affinity_rows.json.
# Values are the shorter base names used in trend output fields.
#
# Example:
#   source column: tag_affinity_pct
#   trend fields:
#       affinity_pct_current
#       affinity_pct_previous
#       affinity_pct_delta
NUMERIC_TREND_SPECS = {
    "total_decks": "total_decks",
    "tag_decks": "tag_decks",
    "tag_affinity_pct": "affinity_pct",
    "z": "z",
    "rank_within_tag_by_z": "rank_within_tag_by_z",
}


# Percent-change fields are only useful for count-style metrics.
# We do not calculate z_pct_change or rank_pct_change because those are not
# intuitive for the website.
PCT_CHANGE_BASES = ["total_decks", "tag_decks"]


@dataclass(frozen=True)
class TrendRunResult:
    """
    Small return object for run_trend_pipeline().

    This makes tests cleaner because they can inspect output paths and the
    summary dictionary directly.
    """

    trend_rows_path: Path
    affinity_rows_with_trends_path: Path
    trend_summary_path: Path
    summary: dict


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def read_json_records(path: Path) -> list[dict]:
    """
    Read a JSON file that stores a list of objects.

    Chat 7 writes affinity_rows.json as a JSON records array:
        [
          {"commander_slug": "...", "tag_slug": "...", ...},
          ...
        ]

    This function intentionally validates that shape so bad input fails early.
    """

    if not path.exists():
        raise FileNotFoundError(f"Expected JSON file does not exist: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected {path} to contain a JSON list of objects.")

    return data


def write_json_records(df: pd.DataFrame, path: Path) -> None:
    """
    Write a DataFrame as a JSON records array.

    pandas writes NaN/NA values as JSON null, which is what the website should
    receive for unavailable previous values or unavailable deltas.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    json_text = df.to_json(orient="records", indent=2, force_ascii=False)
    path.write_text(json_text, encoding="utf-8")


def write_json_object(data: dict, path: Path) -> None:
    """Write a normal JSON object with stable indentation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------

def load_affinity_rows(path: Path) -> pd.DataFrame:
    """
    Load one Chat 7 affinity_rows.json file into a DataFrame.

    This function validates the minimum trend requirements:
    - commander_slug exists
    - tag_slug exists
    - commander_slug + tag_slug is unique
    """

    records = read_json_records(path)
    df = pd.DataFrame(records)

    validate_required_key_columns(df, source_name=str(path))
    validate_unique_keys(df, source_name=str(path))
    normalize_numeric_columns(df)

    return df


def validate_required_key_columns(df: pd.DataFrame, source_name: str = "DataFrame") -> None:
    """
    Ensure the columns needed to match rows across snapshots exist.

    Without these columns, there is no safe way to know which current row
    corresponds to which previous row.
    """

    missing = [column for column in KEY_COLUMNS if column not in df.columns]

    if missing:
        raise ValueError(
            f"{source_name} is missing required key columns: {missing}. "
            f"Expected columns: {KEY_COLUMNS}."
        )


def validate_unique_keys(df: pd.DataFrame, source_name: str = "DataFrame") -> None:
    """
    Fail if one snapshot has duplicate commander/tag keys.

    Trend comparison assumes this rule:
        one commander_slug + tag_slug = one row per snapshot

    If this fails, fix the upstream cleaning/analysis step before trusting
    trend output.
    """

    if df.empty:
        return

    duplicated_mask = df.duplicated(KEY_COLUMNS, keep=False)

    if duplicated_mask.any():
        sample_columns = [column for column in IDENTITY_COLUMNS if column in df.columns]
        sample = df.loc[duplicated_mask, sample_columns].head(10).to_dict(orient="records")

        raise ValueError(
            f"{source_name} contains duplicate commander/tag keys. "
            f"Trend comparison requires one row per {KEY_COLUMNS}. "
            f"Sample duplicates: {sample}"
        )


def normalize_numeric_columns(df: pd.DataFrame) -> None:
    """
    Convert trend metric columns to numeric values when present.

    If a value cannot be converted, pandas turns it into NaN.
    That is safer than crashing later during subtraction.
    """

    for column in NUMERIC_TREND_SPECS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")


# ---------------------------------------------------------------------------
# Snapshot discovery
# ---------------------------------------------------------------------------

def parse_snapshot_date(snapshot_dir: Path) -> pd.Timestamp | None:
    """
    Parse a directory name like 2026-05-07 into a pandas Timestamp.

    Non-date folders, such as latest, return None and are ignored by snapshot
    discovery.
    """

    try:
        return pd.Timestamp(snapshot_dir.name)
    except ValueError:
        return None


def find_previous_snapshot(current_dir: Path, processed_root: Path | None = None) -> Path | None:
    """
    Find the most recent older processed snapshot containing affinity_rows.json.

    Example:
        current_dir = data/processed/2026-05-21

        If these folders exist:
            data/processed/2026-05-07/affinity_rows.json
            data/processed/2026-05-14/affinity_rows.json
            data/processed/latest/

        This returns:
            data/processed/2026-05-14
    """

    current_dir = current_dir.resolve()
    root = processed_root.resolve() if processed_root else current_dir.parent.resolve()

    current_date = parse_snapshot_date(current_dir)
    if current_date is None:
        raise ValueError(
            f"Current snapshot directory must be named like YYYY-MM-DD. "
            f"Got: {current_dir.name}"
        )

    candidates: list[tuple[pd.Timestamp, Path]] = []

    for child in root.iterdir():
        if not child.is_dir():
            continue

        child_date = parse_snapshot_date(child)
        if child_date is None:
            continue

        if child_date < current_date and (child / AFFINITY_ROWS_FILENAME).exists():
            candidates.append((child_date, child))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


# ---------------------------------------------------------------------------
# Trend calculations
# ---------------------------------------------------------------------------

def safe_pct_change(current: pd.Series, previous: pd.Series) -> pd.Series:
    """
    Calculate fractional percent change safely.

    Formula:
        (current - previous) / previous

    Important:
        A return value of 0.20 means +20%.
        The frontend can multiply by 100 for display.

    If previous is missing or zero, the result is NA rather than infinity or
    a misleading number.
    """

    current_numeric = pd.to_numeric(current, errors="coerce")
    previous_numeric = pd.to_numeric(previous, errors="coerce")

    valid_previous = previous_numeric.notna() & (previous_numeric != 0)
    delta = current_numeric - previous_numeric

    return delta.where(valid_previous) / previous_numeric.where(valid_previous)


def _available_columns(df: pd.DataFrame, requested_columns: Iterable[str]) -> list[str]:
    """
    Return requested columns that actually exist in df, preserving order.

    This keeps the trend pipeline flexible if later website fields are added
    or removed.
    """

    return [column for column in requested_columns if column in df.columns]


def _comparison_subset(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only fields needed for trend comparison.

    We keep optional identity columns when available so trend_rows.json is
    useful to read directly during debugging.
    """

    requested_columns = list(
        dict.fromkeys(IDENTITY_COLUMNS + list(NUMERIC_TREND_SPECS.keys()))
    )
    columns = _available_columns(df, requested_columns)

    return df[columns].copy()


def _coalesce_identity_columns(merged: pd.DataFrame) -> pd.DataFrame:
    """
    Create unsuffixed identity columns from current/previous versions.

    For an existing or new row, the current name/tag is used.
    For a removed row, the previous name/tag is used.
    """

    output = pd.DataFrame()

    for key in KEY_COLUMNS:
        output[key] = merged[key]

    for column in IDENTITY_COLUMNS:
        if column in KEY_COLUMNS:
            continue

        current_column = f"{column}_current"
        previous_column = f"{column}_previous"

        if current_column in merged.columns and previous_column in merged.columns:
            output[column] = merged[current_column].combine_first(merged[previous_column])
        elif current_column in merged.columns:
            output[column] = merged[current_column]
        elif previous_column in merged.columns:
            output[column] = merged[previous_column]

    return output


def compute_trends(
    current_df: pd.DataFrame,
    previous_df: pd.DataFrame | None,
    *,
    current_snapshot: str,
    previous_snapshot: str | None,
) -> pd.DataFrame:
    """
    Compare current and previous affinity rows.

    Returns one trend row per commander/tag pair that exists in either snapshot.

    Status values:
        existing
            The commander-tag pair exists in both snapshots.

        new_pair
            The commander-tag pair exists only in the current snapshot.

        removed_pair
            The commander-tag pair exists only in the previous snapshot.

        no_previous_snapshot
            Used only when this is the first processed snapshot and no earlier
            snapshot exists yet.
    """

    validate_required_key_columns(current_df, source_name="current_df")
    validate_unique_keys(current_df, source_name="current_df")
    normalize_numeric_columns(current_df)

    if previous_df is None:
        return compute_first_run_trends(current_df, current_snapshot=current_snapshot)

    validate_required_key_columns(previous_df, source_name="previous_df")
    validate_unique_keys(previous_df, source_name="previous_df")
    normalize_numeric_columns(previous_df)

    current_subset = _comparison_subset(current_df)
    previous_subset = _comparison_subset(previous_df)

    # Full outer join is important:
    # - rows in both snapshots become "existing"
    # - current-only rows become "new_pair"
    # - previous-only rows become "removed_pair"
    merged = current_subset.merge(
        previous_subset,
        on=KEY_COLUMNS,
        how="outer",
        suffixes=("_current", "_previous"),
        indicator=True,
        validate="one_to_one",
    )

    trend_df = _coalesce_identity_columns(merged)

    trend_df["current_snapshot"] = current_snapshot
    trend_df["previous_snapshot"] = previous_snapshot
    trend_df["snapshot_status"] = merged["_merge"].map(
        {
            "both": "existing",
            "left_only": "new_pair",
            "right_only": "removed_pair",
        }
    )

    # Standard numeric deltas:
    #   current - previous
    for source_column, output_base in NUMERIC_TREND_SPECS.items():
        current_column = f"{source_column}_current"
        previous_column = f"{source_column}_previous"

        trend_df[f"{output_base}_current"] = (
            pd.to_numeric(merged[current_column], errors="coerce")
            if current_column in merged.columns
            else pd.NA
        )
        trend_df[f"{output_base}_previous"] = (
            pd.to_numeric(merged[previous_column], errors="coerce")
            if previous_column in merged.columns
            else pd.NA
        )
        trend_df[f"{output_base}_delta"] = (
            trend_df[f"{output_base}_current"] - trend_df[f"{output_base}_previous"]
        )

    # Percent-change fields for count metrics.
    for base in PCT_CHANGE_BASES:
        trend_df[f"{base}_pct_change"] = safe_pct_change(
            trend_df[f"{base}_current"],
            trend_df[f"{base}_previous"],
        )

    # Rank movement uses the opposite direction from normal numeric deltas.
    #
    # Example:
    #   Previous rank 12 -> current rank 7
    #   12 - 7 = +5
    #
    # Positive means the commander improved in rank.
    trend_df["rank_delta"] = (
        trend_df["rank_within_tag_by_z_previous"]
        - trend_df["rank_within_tag_by_z_current"]
    )
    trend_df["rank_within_tag_by_z_delta"] = trend_df["rank_delta"]

    return trend_df


def compute_first_run_trends(
    current_df: pd.DataFrame,
    *,
    current_snapshot: str,
) -> pd.DataFrame:
    """
    Create trend-shaped rows when no previous snapshot exists yet.

    This is useful right now because you currently only have one dataset.
    It lets you test the pipeline and produce stable website-shaped files
    without pretending that every row is a "new trend."
    """

    trend_df = _comparison_subset(current_df)

    trend_df["current_snapshot"] = current_snapshot
    trend_df["previous_snapshot"] = pd.NA
    trend_df["snapshot_status"] = "no_previous_snapshot"

    for source_column, output_base in NUMERIC_TREND_SPECS.items():
        if source_column in current_df.columns:
            trend_df[f"{output_base}_current"] = pd.to_numeric(
                current_df[source_column],
                errors="coerce",
            )
        else:
            trend_df[f"{output_base}_current"] = pd.NA

        trend_df[f"{output_base}_previous"] = pd.NA
        trend_df[f"{output_base}_delta"] = pd.NA

    for base in PCT_CHANGE_BASES:
        trend_df[f"{base}_pct_change"] = pd.NA

    trend_df["rank_delta"] = pd.NA
    trend_df["rank_within_tag_by_z_delta"] = pd.NA

    return trend_df


def merge_trends_into_current_rows(
    current_df: pd.DataFrame,
    trend_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Attach trend fields to current affinity rows.

    Removed rows are intentionally excluded because this output is meant for
    the current website dataset.

    Full removed-row information remains available in trend_rows.json.
    """

    trend_columns = [
        column
        for column in trend_df.columns
        if column not in IDENTITY_COLUMNS or column in KEY_COLUMNS
    ]

    current_trends = trend_df[
        trend_df["snapshot_status"] != "removed_pair"
    ][trend_columns].copy()

    return current_df.merge(
        current_trends,
        on=KEY_COLUMNS,
        how="left",
        validate="one_to_one",
    )


# ---------------------------------------------------------------------------
# Summary and full pipeline
# ---------------------------------------------------------------------------

def build_trend_summary(
    *,
    current_dir: Path,
    previous_dir: Path | None,
    current_df: pd.DataFrame,
    previous_df: pd.DataFrame | None,
    trend_df: pd.DataFrame,
) -> dict:
    """Build a run-level audit summary for trend generation."""

    status_counts = trend_df["snapshot_status"].value_counts(dropna=False).to_dict()

    summary = {
        "current_snapshot": current_dir.name,
        "previous_snapshot": previous_dir.name if previous_dir else None,
        "previous_snapshot_found": previous_dir is not None,
        "current_row_count": int(len(current_df)),
        "previous_row_count": int(len(previous_df)) if previous_df is not None else 0,
        "trend_row_count": int(len(trend_df)),
        "existing_pair_count": int(status_counts.get("existing", 0)),
        "new_pair_count": int(status_counts.get("new_pair", 0)),
        "removed_pair_count": int(status_counts.get("removed_pair", 0)),
        "no_previous_snapshot_count": int(
            status_counts.get("no_previous_snapshot", 0)
        ),
        "rows_with_total_decks_delta": int(
            trend_df["total_decks_delta"].notna().sum()
        ),
        "rows_with_tag_decks_delta": int(
            trend_df["tag_decks_delta"].notna().sum()
        ),
        "rows_with_affinity_pct_delta": int(
            trend_df["affinity_pct_delta"].notna().sum()
        ),
        "rows_with_z_delta": int(trend_df["z_delta"].notna().sum()),
        "rows_with_rank_delta": int(trend_df["rank_delta"].notna().sum()),
        "output_files": {
            "trend_rows": TREND_ROWS_FILENAME,
            "affinity_rows_with_trends": AFFINITY_ROWS_WITH_TRENDS_FILENAME,
            "trend_summary": TREND_SUMMARY_FILENAME,
        },
    }

    return summary


def run_trend_pipeline(
    *,
    current_dir: Path,
    previous_dir: Path | None = None,
    processed_root: Path | None = None,
) -> TrendRunResult:
    """
    Run the full Chat 8 trend pipeline for one current snapshot directory.

    Typical current first run:
        python3 src/edhrec_affinity/trends.py \\
          --current-dir data/processed/2026-05-07

    Typical later run:
        python3 src/edhrec_affinity/trends.py \\
          --current-dir data/processed/2026-05-14 \\
          --processed-root data/processed

    You can also explicitly pass:
        --previous-dir data/processed/2026-05-07
    """

    current_dir = current_dir.resolve()

    if previous_dir is None:
        previous_dir = find_previous_snapshot(current_dir, processed_root=processed_root)
    else:
        previous_dir = previous_dir.resolve()

    current_path = current_dir / AFFINITY_ROWS_FILENAME
    current_df = load_affinity_rows(current_path)

    previous_df: pd.DataFrame | None = None
    if previous_dir is not None:
        previous_path = previous_dir / AFFINITY_ROWS_FILENAME
        previous_df = load_affinity_rows(previous_path)

    trend_df = compute_trends(
        current_df,
        previous_df,
        current_snapshot=current_dir.name,
        previous_snapshot=previous_dir.name if previous_dir else None,
    )

    affinity_rows_with_trends_df = merge_trends_into_current_rows(
        current_df,
        trend_df,
    )

    summary = build_trend_summary(
        current_dir=current_dir,
        previous_dir=previous_dir,
        current_df=current_df,
        previous_df=previous_df,
        trend_df=trend_df,
    )

    trend_rows_path = current_dir / TREND_ROWS_FILENAME
    affinity_rows_with_trends_path = current_dir / AFFINITY_ROWS_WITH_TRENDS_FILENAME
    trend_summary_path = current_dir / TREND_SUMMARY_FILENAME

    write_json_records(trend_df, trend_rows_path)
    write_json_records(affinity_rows_with_trends_df, affinity_rows_with_trends_path)
    write_json_object(summary, trend_summary_path)

    return TrendRunResult(
        trend_rows_path=trend_rows_path,
        affinity_rows_with_trends_path=affinity_rows_with_trends_path,
        trend_summary_path=trend_summary_path,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """Create the command-line interface for trends.py."""

    parser = argparse.ArgumentParser(
        description=(
            "Compare EDHREC affinity_rows.json snapshots and compute trend fields."
        )
    )

    parser.add_argument(
        "--current-dir",
        required=True,
        type=Path,
        help=(
            "Current processed snapshot folder, "
            "e.g. data/processed/2026-05-14."
        ),
    )

    parser.add_argument(
        "--previous-dir",
        type=Path,
        default=None,
        help=(
            "Optional previous processed snapshot folder. If omitted, the script "
            "uses the most recent earlier date folder under --processed-root or "
            "the current folder's parent."
        ),
    )

    parser.add_argument(
        "--processed-root",
        type=Path,
        default=None,
        help=(
            "Optional root containing dated processed folders, "
            "e.g. data/processed."
        ),
    )

    return parser


def main() -> None:
    """CLI entry point."""

    parser = build_arg_parser()
    args = parser.parse_args()

    result = run_trend_pipeline(
        current_dir=args.current_dir,
        previous_dir=args.previous_dir,
        processed_root=args.processed_root,
    )

    print(json.dumps(result.summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()