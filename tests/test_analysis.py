"""
test_analysis.py

Tests for Chat 7 - Statistical Analysis Pipeline.

These tests use tiny fake data instead of the real EDHREC dataset.

Why:
    - Unit tests should be fast.
    - Unit tests should not depend on live EDHREC data.
    - Small fake rows make it easier to check exact formulas.

Run:
    pytest tests/test_analysis.py
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from edhrec_affinity.analysis import (
    AFFINITY_ROWS_FILENAME,
    ANALYSIS_SUMMARY_FILENAME,
    GLOBAL_LEADERBOARD_FILENAME,
    TAG_RANKINGS_FILENAME,
    TAG_SUMMARY_FILENAME,
    build_global_leaderboard,
    build_tag_rankings,
    build_tag_summary,
    prepare_analysis_table,
    write_analysis_outputs,
)


def fake_clean_rows() -> list[dict]:
    """
    Build a small fake version of commander_tags_clean.json.

    Tokens rows are chosen so the math is easy:
        Alpha: 100 / 1000 = 0.10
        Beta:  200 / 1000 = 0.20
        Gamma: 300 / 1000 = 0.30

    For Tokens:
        mean = 0.20
        sample standard deviation = 0.10
        z values are -1, 0, and 1

    Artifacts rows are chosen with identical percentages so standard deviation
    is zero. This tests that z-score becomes missing instead of crashing.

    Lifegain rows are chosen to test default sample-size filters.
    """
    timestamp = "2026-05-07T00:00:00+00:00"

    return [
        {
            "commander_name": "Alpha Commander",
            "commander_slug": "alpha",
            "total_decks": 1000,
            "tag_name": "Tokens",
            "tag_slug": "tokens",
            "tag_decks": 100,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
        {
            "commander_name": "Beta Commander",
            "commander_slug": "beta",
            "total_decks": 1000,
            "tag_name": "Tokens",
            "tag_slug": "tokens",
            "tag_decks": 200,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
        {
            "commander_name": "Gamma Commander",
            "commander_slug": "gamma",
            "total_decks": 1000,
            "tag_name": "Tokens",
            "tag_slug": "tokens",
            "tag_decks": 300,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
        {
            "commander_name": "Delta Commander",
            "commander_slug": "delta",
            "total_decks": 1000,
            "tag_name": "Artifacts",
            "tag_slug": "artifacts",
            "tag_decks": 400,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
        {
            "commander_name": "Epsilon Commander",
            "commander_slug": "epsilon",
            "total_decks": 1000,
            "tag_name": "Artifacts",
            "tag_slug": "artifacts",
            "tag_decks": 400,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
        {
            "commander_name": "Tiny Commander",
            "commander_slug": "tiny",
            "total_decks": 100,
            "tag_name": "Lifegain",
            "tag_slug": "lifegain",
            "tag_decks": 10,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
        {
            "commander_name": "Tiny Tag Commander",
            "commander_slug": "tiny-tag",
            "total_decks": 1000,
            "tag_name": "Lifegain",
            "tag_slug": "lifegain",
            "tag_decks": 2,
            "source_type": "commander_json",
            "scrape_timestamp": timestamp,
        },
    ]


def fake_clean_df() -> pd.DataFrame:
    """
    Return the fake clean rows as a DataFrame.
    """
    return pd.DataFrame(fake_clean_rows())


def row_for(
    df: pd.DataFrame,
    commander_slug: str,
    tag_slug: str,
) -> pd.Series:
    """
    Helper for selecting one commander-tag row from an analyzed table.
    """
    matches = df[
        (df["commander_slug"] == commander_slug)
        & (df["tag_slug"] == tag_slug)
    ]

    assert len(matches) == 1

    return matches.iloc[0]


def test_prepare_analysis_table_calculates_affinity_mean_std_and_z() -> None:
    """
    The core math should be correct for a simple three-row tag group.
    """
    analyzed = prepare_analysis_table(fake_clean_df())

    alpha = row_for(analyzed, "alpha", "tokens")
    beta = row_for(analyzed, "beta", "tokens")
    gamma = row_for(analyzed, "gamma", "tokens")

    assert alpha["tag_affinity_pct"] == pytest.approx(0.10)
    assert beta["tag_affinity_pct"] == pytest.approx(0.20)
    assert gamma["tag_affinity_pct"] == pytest.approx(0.30)

    # For the tokens group: [0.10, 0.20, 0.30]
    assert beta["tag_mean_pct"] == pytest.approx(0.20)

    # pandas uses sample standard deviation by default, so this is 0.10.
    assert beta["tag_std_pct"] == pytest.approx(0.10)

    assert alpha["z"] == pytest.approx(-1.0)
    assert beta["z"] == pytest.approx(0.0)
    assert gamma["z"] == pytest.approx(1.0)


def test_ranks_reset_inside_each_tag() -> None:
    """
    Rankings must be calculated within each tag, not globally.
    """
    analyzed = prepare_analysis_table(fake_clean_df())

    alpha = row_for(analyzed, "alpha", "tokens")
    beta = row_for(analyzed, "beta", "tokens")
    gamma = row_for(analyzed, "gamma", "tokens")

    assert int(gamma["rank_within_tag_by_z"]) == 1
    assert int(beta["rank_within_tag_by_z"]) == 2
    assert int(alpha["rank_within_tag_by_z"]) == 3

    # The Artifacts rows have the same tag_decks value, so method="min"
    # gives both tied rows rank 1 for tag deck count.
    delta = row_for(analyzed, "delta", "artifacts")
    epsilon = row_for(analyzed, "epsilon", "artifacts")

    assert int(delta["rank_within_tag_by_tag_decks"]) == 1
    assert int(epsilon["rank_within_tag_by_tag_decks"]) == 1


def test_percentile_within_tag_uses_affinity_percentage() -> None:
    """
    Percentile should be low for the lowest affinity row and 1.0 for the
    highest affinity row inside a tag.
    """
    analyzed = prepare_analysis_table(fake_clean_df())

    alpha = row_for(analyzed, "alpha", "tokens")
    beta = row_for(analyzed, "beta", "tokens")
    gamma = row_for(analyzed, "gamma", "tokens")

    assert alpha["percentile_within_tag"] == pytest.approx(1 / 3)
    assert beta["percentile_within_tag"] == pytest.approx(2 / 3)
    assert gamma["percentile_within_tag"] == pytest.approx(1.0)


def test_sample_size_flags_identify_default_filter_failures() -> None:
    """
    Rows should remain in the full analysis table, but flags should show
    whether they pass the default leaderboard filters.
    """
    analyzed = prepare_analysis_table(
        fake_clean_df(),
        min_total_decks=200,
        min_tag_decks=5,
    )

    alpha = row_for(analyzed, "alpha", "tokens")
    tiny = row_for(analyzed, "tiny", "lifegain")
    tiny_tag = row_for(analyzed, "tiny-tag", "lifegain")

    assert bool(alpha["passes_default_filters"]) is True

    # total_decks is only 100, so this fails min_total_decks.
    assert bool(tiny["passes_default_filters"]) is False

    # tag_decks is only 2, so this fails min_tag_decks.
    assert bool(tiny_tag["passes_default_filters"]) is False


def test_zero_standard_deviation_produces_missing_z() -> None:
    """
    If every row in a tag has the same affinity percentage, the standard
    deviation is zero and z-score should be missing.
    """
    analyzed = prepare_analysis_table(fake_clean_df())

    delta = row_for(analyzed, "delta", "artifacts")
    epsilon = row_for(analyzed, "epsilon", "artifacts")

    assert delta["tag_std_pct"] == pytest.approx(0.0)
    assert epsilon["tag_std_pct"] == pytest.approx(0.0)

    assert pd.isna(delta["z"])
    assert pd.isna(epsilon["z"])

    assert bool(delta["analysis_eligible"]) is False
    assert bool(epsilon["analysis_eligible"]) is False


def test_global_leaderboard_filters_and_sorts_by_z() -> None:
    """
    The global leaderboard should include only eligible rows and sort strongest
    z-score first.
    """
    analyzed = prepare_analysis_table(
        fake_clean_df(),
        min_total_decks=200,
        min_tag_decks=5,
    )

    leaderboard = build_global_leaderboard(analyzed)

    # Only the three Tokens rows should survive:
    # - Artifacts has zero standard deviation, so no meaningful z-score.
    # - Tiny fails min_total_decks.
    # - Tiny Tag fails min_tag_decks.
    assert list(leaderboard["commander_slug"]) == ["gamma", "beta", "alpha"]

    assert leaderboard.iloc[0]["z"] == pytest.approx(1.0)
    assert leaderboard.iloc[1]["z"] == pytest.approx(0.0)
    assert leaderboard.iloc[2]["z"] == pytest.approx(-1.0)


def test_tag_rankings_keeps_all_rows() -> None:
    """
    The tag rankings output should keep all analyzed rows. The website can
    filter later using passes_default_filters or analysis_eligible.
    """
    analyzed = prepare_analysis_table(fake_clean_df())
    rankings = build_tag_rankings(analyzed)

    assert len(rankings) == len(analyzed)
    assert set(rankings["tag_slug"]) == {"artifacts", "lifegain", "tokens"}


def test_tag_summary_has_one_row_per_tag() -> None:
    """
    Tag summary should collapse the analysis table down to one row per tag.
    """
    analyzed = prepare_analysis_table(fake_clean_df())
    summary = build_tag_summary(analyzed)

    assert set(summary["tag_slug"]) == {"artifacts", "lifegain", "tokens"}

    tokens_summary = summary[summary["tag_slug"] == "tokens"].iloc[0]

    assert int(tokens_summary["tag_row_count"]) == 3
    assert int(tokens_summary["eligible_row_count"]) == 3
    assert tokens_summary["tag_mean_pct"] == pytest.approx(0.20)
    assert tokens_summary["tag_std_pct"] == pytest.approx(0.10)
    assert tokens_summary["max_z"] == pytest.approx(1.0)


def test_write_analysis_outputs_creates_expected_files(tmp_path) -> None:
    """
    File-output test.

    tmp_path gives this test a temporary directory, so it does not write into
    the real project data folder.
    """
    input_path = tmp_path / "commander_tags_clean.json"
    output_dir = tmp_path / "processed"

    with input_path.open("w", encoding="utf-8") as file:
        json.dump(fake_clean_rows(), file)

    summary = write_analysis_outputs(
        input_path=input_path,
        output_dir=output_dir,
        min_total_decks=200,
        min_tag_decks=5,
    )

    expected_files = [
        AFFINITY_ROWS_FILENAME,
        TAG_RANKINGS_FILENAME,
        GLOBAL_LEADERBOARD_FILENAME,
        TAG_SUMMARY_FILENAME,
        ANALYSIS_SUMMARY_FILENAME,
    ]

    for filename in expected_files:
        assert (output_dir / filename).exists()

    assert summary["input_row_count"] == len(fake_clean_rows())
    assert summary["unique_commander_count"] == 7
    assert summary["unique_tag_count"] == 3
    assert summary["global_leaderboard_row_count"] == 3

    with (output_dir / GLOBAL_LEADERBOARD_FILENAME).open(
        "r",
        encoding="utf-8",
    ) as file:
        leaderboard_rows = json.load(file)

    assert leaderboard_rows[0]["commander_slug"] == "gamma"


def test_missing_required_columns_raise_clear_error() -> None:
    """
    The analysis pipeline should fail clearly if Chat 6 output is malformed.
    """
    bad_df = pd.DataFrame(
        [
            {
                "commander_name": "Incomplete Commander",
            }
        ]
    )

    with pytest.raises(ValueError, match="Missing required columns"):
        prepare_analysis_table(bad_df)


def test_tag_decks_cannot_exceed_total_decks() -> None:
    """
    A row with tag_decks greater than total_decks should fail validation.
    """
    bad_rows = fake_clean_rows()
    bad_rows[0]["tag_decks"] = 9999

    bad_df = pd.DataFrame(bad_rows)

    with pytest.raises(ValueError, match="tag_decks > total_decks"):
        prepare_analysis_table(bad_df)