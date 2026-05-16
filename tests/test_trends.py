"""
test_trends.py

Tests for Chat 8 - Historical Trends and Weekly Change Tracking.

These tests do not contact EDHREC. They use small fake affinity_rows.json
datasets so the trend logic can be tested quickly and reliably.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from edhrec_affinity.trends import (
    AFFINITY_ROWS_FILENAME,
    AFFINITY_ROWS_WITH_TRENDS_FILENAME,
    TREND_ROWS_FILENAME,
    TREND_SUMMARY_FILENAME,
    compute_trends,
    find_previous_snapshot,
    run_trend_pipeline,
    safe_pct_change,
    validate_unique_keys,
)


def affinity_row(
    *,
    commander_slug: str,
    tag_slug: str,
    total_decks: int,
    tag_decks: int,
    affinity_pct: float,
    z: float | None,
    rank: int | None,
    commander_name: str | None = None,
    tag_name: str | None = None,
    source_type: str = "commander_json",
) -> dict:
    """
    Build one fake Chat 7 affinity row for tests.

    The real affinity_rows.json file has many more fields, but these are the
    fields trends.py needs for historical comparison.
    """

    return {
        "commander_name": commander_name or commander_slug.replace("-", " ").title(),
        "commander_slug": commander_slug,
        "tag_name": tag_name or tag_slug.replace("-", " ").title(),
        "tag_slug": tag_slug,
        "source_type": source_type,
        "total_decks": total_decks,
        "tag_decks": tag_decks,
        "tag_affinity_pct": affinity_pct,
        "z": z,
        "rank_within_tag_by_z": rank,
    }


def write_affinity_rows(snapshot_dir: Path, rows: list[dict]) -> Path:
    """Write fake affinity_rows.json into a temporary snapshot folder."""

    snapshot_dir.mkdir(parents=True, exist_ok=True)

    path = snapshot_dir / AFFINITY_ROWS_FILENAME
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    return path


def read_json(path: Path):
    """Read JSON from a test output file."""

    return json.loads(path.read_text(encoding="utf-8"))


def test_compute_trends_existing_new_removed_and_deltas():
    """
    This test covers the three main comparison cases:

    - existing pair: row exists in both snapshots
    - new_pair: row exists only in current snapshot
    - removed_pair: row exists only in previous snapshot
    """

    previous_df = pd.DataFrame(
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=100,
                tag_decks=20,
                affinity_pct=0.20,
                z=1.0,
                rank=10,
            ),
            affinity_row(
                commander_slug="bravo",
                tag_slug="artifacts",
                total_decks=200,
                tag_decks=50,
                affinity_pct=0.25,
                z=2.0,
                rank=1,
            ),
            affinity_row(
                commander_slug="removed",
                tag_slug="graveyard",
                total_decks=150,
                tag_decks=30,
                affinity_pct=0.20,
                z=0.5,
                rank=4,
            ),
        ]
    )

    current_df = pd.DataFrame(
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=120,
                tag_decks=30,
                affinity_pct=0.25,
                z=1.5,
                rank=7,
            ),
            affinity_row(
                commander_slug="bravo",
                tag_slug="artifacts",
                total_decks=180,
                tag_decks=45,
                affinity_pct=0.25,
                z=1.8,
                rank=2,
            ),
            affinity_row(
                commander_slug="newbie",
                tag_slug="enchantress",
                total_decks=300,
                tag_decks=15,
                affinity_pct=0.05,
                z=0.8,
                rank=11,
            ),
        ]
    )

    trend_df = compute_trends(
        current_df,
        previous_df,
        current_snapshot="2026-05-14",
        previous_snapshot="2026-05-07",
    )

    alpha = trend_df[
        (trend_df["commander_slug"] == "alpha")
        & (trend_df["tag_slug"] == "tokens")
    ].iloc[0]

    assert alpha["snapshot_status"] == "existing"
    assert alpha["total_decks_delta"] == 20
    assert alpha["tag_decks_delta"] == 10
    assert alpha["affinity_pct_delta"] == pytest.approx(0.05)
    assert alpha["z_delta"] == pytest.approx(0.5)

    # Rank movement uses previous - current.
    # Rank 10 -> rank 7 means the commander improved by 3 places.
    assert alpha["rank_delta"] == 3

    # Percent-change fields are fractional:
    # 0.20 means +20%.
    assert alpha["total_decks_pct_change"] == pytest.approx(0.20)
    assert alpha["tag_decks_pct_change"] == pytest.approx(0.50)

    bravo = trend_df[
        (trend_df["commander_slug"] == "bravo")
        & (trend_df["tag_slug"] == "artifacts")
    ].iloc[0]

    assert bravo["snapshot_status"] == "existing"

    # Rank 1 -> rank 2 means the commander fell by 1 place.
    assert bravo["rank_delta"] == -1

    newbie = trend_df[trend_df["commander_slug"] == "newbie"].iloc[0]

    assert newbie["snapshot_status"] == "new_pair"
    assert pd.isna(newbie["tag_decks_previous"])
    assert pd.isna(newbie["tag_decks_delta"])

    removed = trend_df[trend_df["commander_slug"] == "removed"].iloc[0]

    assert removed["snapshot_status"] == "removed_pair"
    assert pd.isna(removed["tag_decks_current"])
    assert pd.isna(removed["tag_decks_delta"])


def test_safe_pct_change_returns_na_for_zero_or_missing_previous():
    """
    Percent change should not produce infinity when the previous value is zero.

    Example:
        previous tag_decks = 0
        current tag_decks = 10

    That is a real increase, but the percentage-change denominator is zero,
    so the safest machine-readable value is null/NA.
    """

    current = pd.Series([120, 10, 5])
    previous = pd.Series([100, 0, pd.NA])

    result = safe_pct_change(current, previous)

    assert result.iloc[0] == pytest.approx(0.20)
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])


def test_first_run_trends_mark_no_previous_snapshot():
    """
    With only one dataset, trends.py should still produce valid output.

    It should not pretend every row is new. Instead, it should mark rows as:
        no_previous_snapshot
    """

    current_df = pd.DataFrame(
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=120,
                tag_decks=30,
                affinity_pct=0.25,
                z=1.5,
                rank=7,
            )
        ]
    )

    trend_df = compute_trends(
        current_df,
        previous_df=None,
        current_snapshot="2026-05-07",
        previous_snapshot=None,
    )

    row = trend_df.iloc[0]

    assert row["snapshot_status"] == "no_previous_snapshot"
    assert row["total_decks_current"] == 120
    assert pd.isna(row["total_decks_previous"])
    assert pd.isna(row["total_decks_delta"])
    assert pd.isna(row["rank_delta"])


def test_duplicate_keys_raise_clear_error():
    """
    Trend comparison requires one row per commander_slug + tag_slug.

    If duplicates appear here, it usually means something upstream went wrong
    in cleaning or analysis.
    """

    df = pd.DataFrame(
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=100,
                tag_decks=20,
                affinity_pct=0.20,
                z=1.0,
                rank=10,
            ),
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=100,
                tag_decks=20,
                affinity_pct=0.20,
                z=1.0,
                rank=10,
            ),
        ]
    )

    with pytest.raises(ValueError, match="duplicate commander/tag keys"):
        validate_unique_keys(df, source_name="fake snapshot")


def test_find_previous_snapshot_finds_most_recent_earlier_folder(tmp_path):
    """
    Snapshot discovery should ignore non-date folders and choose the most recent
    older dated folder.
    """

    processed_root = tmp_path / "processed"

    current_dir = processed_root / "2026-05-21"
    old_dir = processed_root / "2026-05-07"
    recent_previous_dir = processed_root / "2026-05-14"
    ignored_latest_dir = processed_root / "latest"

    write_affinity_rows(old_dir, [])
    write_affinity_rows(recent_previous_dir, [])
    write_affinity_rows(current_dir, [])
    ignored_latest_dir.mkdir(parents=True)

    found = find_previous_snapshot(current_dir, processed_root=processed_root)

    assert found == recent_previous_dir


def test_run_trend_pipeline_first_run_writes_outputs(tmp_path):
    """
    This tests your current real-world situation:
    one processed snapshot and no previous snapshot.
    """

    current_dir = tmp_path / "processed" / "2026-05-07"

    write_affinity_rows(
        current_dir,
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=120,
                tag_decks=30,
                affinity_pct=0.25,
                z=1.5,
                rank=7,
            )
        ],
    )

    result = run_trend_pipeline(current_dir=current_dir)

    assert result.trend_rows_path.exists()
    assert result.affinity_rows_with_trends_path.exists()
    assert result.trend_summary_path.exists()

    summary = read_json(current_dir / TREND_SUMMARY_FILENAME)

    assert summary["previous_snapshot_found"] is False
    assert summary["no_previous_snapshot_count"] == 1

    enriched_rows = read_json(current_dir / AFFINITY_ROWS_WITH_TRENDS_FILENAME)

    assert enriched_rows[0]["snapshot_status"] == "no_previous_snapshot"
    assert enriched_rows[0]["total_decks_previous"] is None


def test_run_trend_pipeline_with_previous_writes_expected_counts(tmp_path):
    """
    This tests the future normal case:
    current snapshot compared against a previous dated snapshot.
    """

    processed_root = tmp_path / "processed"
    previous_dir = processed_root / "2026-05-07"
    current_dir = processed_root / "2026-05-14"

    write_affinity_rows(
        previous_dir,
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=100,
                tag_decks=20,
                affinity_pct=0.20,
                z=1.0,
                rank=10,
            ),
            affinity_row(
                commander_slug="removed",
                tag_slug="graveyard",
                total_decks=150,
                tag_decks=30,
                affinity_pct=0.20,
                z=0.5,
                rank=4,
            ),
        ],
    )

    write_affinity_rows(
        current_dir,
        [
            affinity_row(
                commander_slug="alpha",
                tag_slug="tokens",
                total_decks=120,
                tag_decks=30,
                affinity_pct=0.25,
                z=1.5,
                rank=7,
            ),
            affinity_row(
                commander_slug="newbie",
                tag_slug="enchantress",
                total_decks=300,
                tag_decks=15,
                affinity_pct=0.05,
                z=0.8,
                rank=11,
            ),
        ],
    )

    result = run_trend_pipeline(
        current_dir=current_dir,
        processed_root=processed_root,
    )

    assert result.summary["previous_snapshot_found"] is True
    assert result.summary["previous_snapshot"] == "2026-05-07"
    assert result.summary["existing_pair_count"] == 1
    assert result.summary["new_pair_count"] == 1
    assert result.summary["removed_pair_count"] == 1

    trend_rows = read_json(current_dir / TREND_ROWS_FILENAME)

    # One existing row, one new row, one removed row.
    assert len(trend_rows) == 3

    enriched_rows = read_json(current_dir / AFFINITY_ROWS_WITH_TRENDS_FILENAME)

    # affinity_rows_with_trends.json represents the current website dataset,
    # so removed rows are not included there.
    assert len(enriched_rows) == 2
    assert {row["commander_slug"] for row in enriched_rows} == {
        "alpha",
        "newbie",
    }