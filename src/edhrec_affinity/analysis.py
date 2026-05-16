"""
analysis.py

Chat 7 - Statistical Analysis Pipeline

This file takes the cleaned commander-tag table from Chat 6 and adds the
statistical fields needed for the EDHREC Commander Tag Affinity project.

Input:
    data/processed/<date>/commander_tags_clean.json

Main outputs:
    affinity_rows.json
        Full commander-tag table with calculated statistical fields.

    tag_rankings.json
        Same analysis rows, sorted for tag-explorer usage.

    global_leaderboard.json
        Default-filtered leaderboard rows, sorted by strongest z-score.

    tag_summary.json
        One summary row per tag.

    analysis_summary.json
        Run-level audit information.

Important design decision:
    This script does not scrape data and does not clean raw data.
    It assumes Chat 6 already produced a valid clean commander-tag table.

Core formula:
    tag_affinity_pct = tag_decks / total_decks

Z-score formula:
    z = (tag_affinity_pct - tag_mean_pct) / tag_std_pct

Where tag_mean_pct and tag_std_pct are calculated separately inside each tag.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# Default filters for the first version of the leaderboard.
#
# These are not used to delete data from affinity_rows.json.
# They only decide which rows appear in the default global leaderboard.
DEFAULT_MIN_TOTAL_DECKS = 200
DEFAULT_MIN_TAG_DECKS = 5


# These are the columns this analysis step expects from Chat 6.
# If one of these is missing, the pipeline should fail clearly instead of
# silently producing incorrect statistics.
REQUIRED_INPUT_COLUMNS = {
    "commander_name",
    "commander_slug",
    "total_decks",
    "tag_name",
    "tag_slug",
    "tag_decks",
    "source_type",
    "scrape_timestamp",
}


# Output filenames are constants so tests and future automation can rely on
# stable file names.
AFFINITY_ROWS_FILENAME = "affinity_rows.json"
TAG_RANKINGS_FILENAME = "tag_rankings.json"
GLOBAL_LEADERBOARD_FILENAME = "global_leaderboard.json"
TAG_SUMMARY_FILENAME = "tag_summary.json"
ANALYSIS_SUMMARY_FILENAME = "analysis_summary.json"


# This preferred column order keeps the most important website fields near
# the front of each JSON record. Any extra columns from the clean input are
# preserved after these known columns.
PREFERRED_COLUMN_ORDER = [
    "commander_name",
    "commander_slug",
    "tag_name",
    "tag_slug",
    "source_type",
    "scrape_timestamp",
    "total_decks",
    "tag_decks",
    "tag_affinity_pct",
    "tag_affinity_pct_display",
    "tag_mean_pct",
    "tag_std_pct",
    "tag_row_count",
    "z",
    "rank_within_tag_by_z",
    "rank_within_tag_by_pct",
    "rank_within_tag_by_tag_decks",
    "percentile_within_tag",
    "passes_default_filters",
    "analysis_eligible",
]


def load_clean_tags(input_path: str | Path) -> pd.DataFrame:
    """
    Load the Chat 6 clean commander-tag table.

    Parameters
    ----------
    input_path:
        Path to commander_tags_clean.json.

    Returns
    -------
    pandas.DataFrame
        Raw DataFrame loaded from the clean JSON file.

    Why this is separate:
        Keeping file loading separate from statistical calculation makes the
        calculation functions easier to unit test with small fake DataFrames.
    """
    path = Path(input_path)

    if not path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")

    df = pd.read_json(path)

    if df.empty:
        raise ValueError(f"Input file exists but contains no rows: {path}")

    return df


def validate_analysis_input(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate that the clean input has the columns and count relationships
    needed for statistical analysis.

    This is not a replacement for Chat 6 cleaning. It is a final safety check
    so Chat 7 does not produce misleading statistics from malformed input.

    Returns a copy of the input DataFrame with numeric columns coerced to
    numeric types.
    """
    missing_columns = REQUIRED_INPUT_COLUMNS - set(df.columns)
    if missing_columns:
        raise ValueError(
            "Missing required columns for analysis: "
            f"{sorted(missing_columns)}"
        )

    if df.empty:
        raise ValueError("Cannot analyze an empty DataFrame.")

    validated = df.copy()

    # Validate text-like identifier columns.
    text_columns = [
        "commander_name",
        "commander_slug",
        "tag_name",
        "tag_slug",
        "source_type",
        "scrape_timestamp",
    ]

    for column in text_columns:
        empty_mask = (
            validated[column].isna()
            | (validated[column].astype(str).str.strip() == "")
        )
        if empty_mask.any():
            bad_count = int(empty_mask.sum())
            raise ValueError(
                f"Column {column!r} contains {bad_count} blank or missing values."
            )

    # Convert deck-count columns to numeric values.
    # If there is a bad value such as "unknown", pandas will raise and we turn
    # that into a clearer project-specific error.
    for column in ["total_decks", "tag_decks"]:
        try:
            validated[column] = pd.to_numeric(validated[column], errors="raise")
        except Exception as exc:  # pragma: no cover - exact pandas error may vary
            raise ValueError(f"Column {column!r} must contain numeric values.") from exc

    # total_decks must be greater than zero because it is used as a denominator.
    non_positive_total_mask = validated["total_decks"] <= 0
    if non_positive_total_mask.any():
        bad_count = int(non_positive_total_mask.sum())
        raise ValueError(
            f"Found {bad_count} rows with total_decks <= 0. "
            "Cannot compute tag_affinity_pct safely."
        )

    # tag_decks cannot be negative.
    negative_tag_decks_mask = validated["tag_decks"] < 0
    if negative_tag_decks_mask.any():
        bad_count = int(negative_tag_decks_mask.sum())
        raise ValueError(f"Found {bad_count} rows with negative tag_decks.")

    # For this project, a tag-specific deck count should not exceed the
    # commander's total deck count.
    impossible_count_mask = validated["tag_decks"] > validated["total_decks"]
    if impossible_count_mask.any():
        bad_count = int(impossible_count_mask.sum())
        raise ValueError(
            f"Found {bad_count} rows where tag_decks > total_decks."
        )

    return validated


def add_affinity_percentage(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add the core affinity percentage.

    tag_affinity_pct is stored as a decimal:
        0.25 means 25%

    tag_affinity_pct_display is stored as a percent-scale number:
        25.0 means 25%

    Keeping both is useful because:
        - decimal values are better for formulas
        - percent-scale values are easier for website display
    """
    result = df.copy()

    result["tag_affinity_pct"] = result["tag_decks"] / result["total_decks"]
    result["tag_affinity_pct_display"] = result["tag_affinity_pct"] * 100.0

    return result


def add_tag_baselines(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add per-tag baseline statistics.

    For every tag_slug, calculate:
        - tag_mean_pct
        - tag_std_pct
        - tag_row_count

    These values are then merged back onto every commander-tag row.

    Important:
        The mean and standard deviation must be calculated within each tag.
        Do not calculate one global mean across all tags.
    """
    result = df.copy()

    tag_stats = (
        result.groupby("tag_slug", as_index=False)["tag_affinity_pct"]
        .agg(
            tag_mean_pct="mean",
            tag_std_pct="std",
            tag_row_count="size",
        )
    )

    result = result.merge(tag_stats, on="tag_slug", how="left")

    return result


def add_z_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add z-score values.

    z = (tag_affinity_pct - tag_mean_pct) / tag_std_pct

    If a tag has only one row, pandas will usually produce NaN for standard
    deviation. If all commanders in a tag have the exact same percentage,
    the standard deviation is 0. In both cases, z-score is not meaningful,
    so this function leaves z as NaN for those rows.
    """
    result = df.copy()

    result["z"] = (
        (result["tag_affinity_pct"] - result["tag_mean_pct"])
        / result["tag_std_pct"]
    )

    # Replace infinite values with NaN. Infinite z-scores can happen if the
    # denominator is zero.
    result["z"] = result["z"].replace([np.inf, -np.inf], np.nan)

    # If standard deviation is missing or zero, the z-score should be missing.
    invalid_std_mask = result["tag_std_pct"].isna() | (result["tag_std_pct"] <= 0)
    result.loc[invalid_std_mask, "z"] = np.nan

    return result


def add_ranks_and_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add rank and percentile fields inside each tag.

    rank_within_tag_by_z:
        Highest z-score gets rank 1.

    rank_within_tag_by_pct:
        Highest raw affinity percentage gets rank 1.

    rank_within_tag_by_tag_decks:
        Highest tag deck count gets rank 1.

    percentile_within_tag:
        A decimal percentile from 0 to 1.
        A value near 1 means the row is near the top of its tag.
    """
    result = df.copy()

    result["rank_within_tag_by_z"] = (
        result.groupby("tag_slug")["z"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )

    result["rank_within_tag_by_pct"] = (
        result.groupby("tag_slug")["tag_affinity_pct"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )

    result["rank_within_tag_by_tag_decks"] = (
        result.groupby("tag_slug")["tag_decks"]
        .rank(method="min", ascending=False)
        .astype("Int64")
    )

    # Percentile is calculated ascending so the highest affinity percentage
    # receives the largest percentile value.
    result["percentile_within_tag"] = (
        result.groupby("tag_slug")["tag_affinity_pct"]
        .rank(method="average", pct=True, ascending=True)
    )

    return result


def add_sample_size_flags(
    df: pd.DataFrame,
    min_total_decks: int = DEFAULT_MIN_TOTAL_DECKS,
    min_tag_decks: int = DEFAULT_MIN_TAG_DECKS,
) -> pd.DataFrame:
    """
    Add filter flags.

    passes_default_filters:
        True if the row passes the simple default sample-size filters.

    analysis_eligible:
        True if the row passes the default filters and also has a meaningful
        z-score.

    The pipeline keeps all rows. These flags are used to decide what appears
    by default in global_leaderboard.json.
    """
    result = df.copy()

    result["passes_default_filters"] = (
        (result["total_decks"] >= min_total_decks)
        & (result["tag_decks"] >= min_tag_decks)
    )

    result["analysis_eligible"] = (
        result["passes_default_filters"]
        & result["z"].notna()
        & result["tag_std_pct"].notna()
        & (result["tag_std_pct"] > 0)
        & (result["tag_row_count"] > 1)
    )

    return result


def reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Move the most important project columns to the front.

    This keeps output JSON easier to inspect by hand while preserving any
    extra columns that may appear in future clean tables.
    """
    known_columns = [column for column in PREFERRED_COLUMN_ORDER if column in df.columns]
    extra_columns = [column for column in df.columns if column not in known_columns]

    return df[known_columns + extra_columns]


def prepare_analysis_table(
    clean_df: pd.DataFrame,
    min_total_decks: int = DEFAULT_MIN_TOTAL_DECKS,
    min_tag_decks: int = DEFAULT_MIN_TAG_DECKS,
) -> pd.DataFrame:
    """
    Run the complete in-memory analysis pipeline.

    This is the best function to test because it applies the same sequence
    that the CLI uses:
        1. validate input
        2. calculate affinity percentage
        3. calculate tag baselines
        4. calculate z-scores
        5. calculate ranks and percentiles
        6. add sample-size flags
    """
    analyzed = validate_analysis_input(clean_df)
    analyzed = add_affinity_percentage(analyzed)
    analyzed = add_tag_baselines(analyzed)
    analyzed = add_z_scores(analyzed)
    analyzed = add_ranks_and_percentiles(analyzed)
    analyzed = add_sample_size_flags(
        analyzed,
        min_total_decks=min_total_decks,
        min_tag_decks=min_tag_decks,
    )
    analyzed = reorder_columns(analyzed)

    return analyzed


def build_global_leaderboard(analysis_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the default global leaderboard.

    This table is filtered. It contains only rows that:
        - pass the default sample-size filters
        - have a meaningful z-score

    Sorting priority:
        1. highest z-score
        2. highest tag deck count
        3. highest total deck count
        4. commander name alphabetically
    """
    leaderboard = analysis_df[analysis_df["analysis_eligible"]].copy()

    leaderboard = leaderboard.sort_values(
        by=["z", "tag_decks", "total_decks", "commander_name"],
        ascending=[False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)

    return leaderboard


def build_tag_rankings(analysis_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a tag-explorer-friendly ranking table.

    Unlike the global leaderboard, this keeps all rows. The website can still
    use passes_default_filters and analysis_eligible to filter by default.
    """
    rankings = analysis_df.copy()

    rankings = rankings.sort_values(
        by=[
            "tag_slug",
            "rank_within_tag_by_z",
            "rank_within_tag_by_pct",
            "rank_within_tag_by_tag_decks",
            "commander_name",
        ],
        ascending=[True, True, True, True, True],
        na_position="last",
    ).reset_index(drop=True)

    return rankings


def build_tag_summary(analysis_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one summary row per tag.

    This is useful for the future tag explorer page because it can show:
        - how many commander rows the tag has
        - how many rows pass default filters
        - the tag-wide mean and standard deviation
        - the strongest z-score in that tag
    """
    summary = (
        analysis_df.groupby(["tag_slug", "tag_name"], as_index=False)
        .agg(
            tag_row_count=("tag_slug", "size"),
            eligible_row_count=("analysis_eligible", "sum"),
            tag_mean_pct=("tag_affinity_pct", "mean"),
            tag_std_pct=("tag_affinity_pct", "std"),
            max_z=("z", "max"),
            max_tag_decks=("tag_decks", "max"),
            max_total_decks=("total_decks", "max"),
        )
        .sort_values(by=["tag_slug", "tag_name"])
        .reset_index(drop=True)
    )

    # Convert boolean-sum output to a normal integer-looking column.
    summary["eligible_row_count"] = summary["eligible_row_count"].astype(int)

    return summary


def write_json_records(df: pd.DataFrame, output_path: str | Path) -> None:
    """
    Write a DataFrame as a JSON array of records.

    orient="records" produces:
        [
          {"column": "value"},
          {"column": "value"}
        ]

    That shape is easy for a React/Vite frontend to load later.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # pandas writes NaN values as null in JSON, which is what we want for
    # rows where z-score is not meaningful.
    df.to_json(path, orient="records", indent=2)


def write_json_object(data: dict[str, Any], output_path: str | Path) -> None:
    """
    Write a plain dictionary as formatted JSON.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def build_analysis_summary(
    input_path: str | Path,
    output_dir: str | Path,
    analysis_df: pd.DataFrame,
    global_leaderboard: pd.DataFrame,
    tag_summary: pd.DataFrame,
    min_total_decks: int,
    min_tag_decks: int,
) -> dict[str, Any]:
    """
    Build a small audit summary for the analysis run.

    This is similar in spirit to earlier validation reports. It makes it
    easier to confirm that the script ran against the expected file and
    produced the expected output files.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    summary = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "input_row_count": int(len(analysis_df)),
        "affinity_row_count": int(len(analysis_df)),
        "global_leaderboard_row_count": int(len(global_leaderboard)),
        "tag_summary_row_count": int(len(tag_summary)),
        "unique_commander_count": int(analysis_df["commander_slug"].nunique()),
        "unique_tag_count": int(analysis_df["tag_slug"].nunique()),
        "analysis_eligible_row_count": int(analysis_df["analysis_eligible"].sum()),
        "rows_with_missing_z": int(analysis_df["z"].isna().sum()),
        "min_total_decks_default": int(min_total_decks),
        "min_tag_decks_default": int(min_tag_decks),
        "output_files": {
            "affinity_rows": AFFINITY_ROWS_FILENAME,
            "tag_rankings": TAG_RANKINGS_FILENAME,
            "global_leaderboard": GLOBAL_LEADERBOARD_FILENAME,
            "tag_summary": TAG_SUMMARY_FILENAME,
            "analysis_summary": ANALYSIS_SUMMARY_FILENAME,
        },
    }

    return summary


def write_analysis_outputs(
    input_path: str | Path,
    output_dir: str | Path,
    min_total_decks: int = DEFAULT_MIN_TOTAL_DECKS,
    min_tag_decks: int = DEFAULT_MIN_TAG_DECKS,
) -> dict[str, Any]:
    """
    Full file-based analysis pipeline.

    This is the main function used by the CLI and by the file-output test.
    """
    output_dir = Path(output_dir)

    clean_df = load_clean_tags(input_path)

    analysis_df = prepare_analysis_table(
        clean_df,
        min_total_decks=min_total_decks,
        min_tag_decks=min_tag_decks,
    )

    tag_rankings = build_tag_rankings(analysis_df)
    global_leaderboard = build_global_leaderboard(analysis_df)
    tag_summary = build_tag_summary(analysis_df)

    write_json_records(analysis_df, output_dir / AFFINITY_ROWS_FILENAME)
    write_json_records(tag_rankings, output_dir / TAG_RANKINGS_FILENAME)
    write_json_records(global_leaderboard, output_dir / GLOBAL_LEADERBOARD_FILENAME)
    write_json_records(tag_summary, output_dir / TAG_SUMMARY_FILENAME)

    summary = build_analysis_summary(
        input_path=input_path,
        output_dir=output_dir,
        analysis_df=analysis_df,
        global_leaderboard=global_leaderboard,
        tag_summary=tag_summary,
        min_total_decks=min_total_decks,
        min_tag_decks=min_tag_decks,
    )

    write_json_object(summary, output_dir / ANALYSIS_SUMMARY_FILENAME)

    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the command-line interface.

    Example:
        python3 src/edhrec_affinity/analysis.py \\
          --input data/processed/2026-05-07/commander_tags_clean.json \\
          --output-dir data/processed/2026-05-07
    """
    parser = argparse.ArgumentParser(
        description=(
            "Compute EDHREC commander-tag affinity statistics, ranks, "
            "percentiles, and leaderboard-ready JSON files."
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to commander_tags_clean.json from Chat 6.",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where analysis output JSON files should be written.",
    )

    parser.add_argument(
        "--min-total-decks",
        type=int,
        default=DEFAULT_MIN_TOTAL_DECKS,
        help=(
            "Minimum total commander decks for the default leaderboard. "
            f"Default: {DEFAULT_MIN_TOTAL_DECKS}"
        ),
    )

    parser.add_argument(
        "--min-tag-decks",
        type=int,
        default=DEFAULT_MIN_TAG_DECKS,
        help=(
            "Minimum commander-tag decks for the default leaderboard. "
            f"Default: {DEFAULT_MIN_TAG_DECKS}"
        ),
    )

    return parser


def main() -> None:
    """
    CLI entry point.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    summary = write_analysis_outputs(
        input_path=args.input,
        output_dir=args.output_dir,
        min_total_decks=args.min_total_decks,
        min_tag_decks=args.min_tag_decks,
    )

    # Print a compact run summary so manual runs give immediate feedback.
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()