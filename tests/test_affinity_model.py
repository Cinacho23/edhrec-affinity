"""Model and pipeline checks for reliability-adjusted affinity."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import beta

from edhrec_affinity import affinity_model
from edhrec_affinity.affinity_model import fit_tag_prior
from edhrec_affinity.analysis import (
    add_affinity_percentage,
    add_tag_baselines,
    add_z_scores,
    prepare_analysis_table,
)


def make_rows(counts, tag_slug="tokens"):
    return pd.DataFrame([
        {
            "commander_name": f"Commander {i}",
            "commander_slug": f"commander-{i}",
            "tag_name": tag_slug.title(),
            "tag_slug": tag_slug,
            "tag_decks": successes,
            "total_decks": trials,
            "source_type": "commander_json",
            "scrape_timestamp": "2026-10-08T00:00:00Z",
        }
        for i, (successes, trials) in enumerate(counts)
    ])


def varied_rows():
    return make_rows([
        (20, 200), (2000, 20000), (5, 250), (20, 500),
        (50, 500), (100, 500), (200, 500), (1, 10),
    ])


def test_fitted_affinity_preserves_raw_and_exact_legacy_fields():
    rows = varied_rows()
    legacy = add_z_scores(add_tag_baselines(add_affinity_percentage(rows)))
    analyzed = prepare_analysis_table(rows)

    assert set(analyzed.affinity_model_status) == {"fitted"}
    assert set(analyzed.affinity_model_version) == {"beta_binomial_v1"}
    for raw_column in ["tag_affinity_pct", "tag_affinity_pct_display", "tag_mean_pct", "tag_std_pct"]:
        pd.testing.assert_series_equal(analyzed[raw_column], legacy[raw_column])
    pd.testing.assert_series_equal(analyzed.legacy_z, legacy.z, check_names=False)

    prior_mean = analyzed.tag_prior_mean_pct.iloc[0]
    strength = analyzed.tag_prior_strength.iloc[0]
    expected_std = np.sqrt(prior_mean * (1 - prior_mean) / (strength + 1))
    assert (analyzed.tag_reference_row_count == 7).all()
    np.testing.assert_allclose(analyzed.tag_prior_std_pct, expected_std)
    expected_adjusted = (rows.tag_decks + strength * prior_mean) / (rows.total_decks + strength)
    np.testing.assert_allclose(analyzed.tag_affinity_adjusted_pct, expected_adjusted)
    np.testing.assert_allclose(analyzed.z, (expected_adjusted - prior_mean) / expected_std)


def test_equal_raw_percentages_shrink_by_precision_and_rank_by_adjusted_score():
    analyzed = prepare_analysis_table(varied_rows())
    low, high = analyzed.iloc[0], analyzed.iloc[1]
    assert low.tag_affinity_pct == high.tag_affinity_pct
    assert low.legacy_z == high.legacy_z
    assert abs(low.tag_affinity_adjusted_pct - low.tag_prior_mean_pct) < abs(
        high.tag_affinity_adjusted_pct - high.tag_prior_mean_pct
    )
    assert low.tag_affinity_upper_pct - low.tag_affinity_lower_pct > (
        high.tag_affinity_upper_pct - high.tag_affinity_lower_pct
    )
    # The raw percentages tie; the learned score and its percentile do not.
    assert low.rank_within_tag_by_pct == high.rank_within_tag_by_pct
    assert low.rank_within_tag_by_z != high.rank_within_tag_by_z
    expected_percentiles = analyzed.groupby("tag_slug").z.rank(method="average", pct=True)
    np.testing.assert_allclose(analyzed.percentile_within_tag, expected_percentiles)


def test_posterior_intervals_match_conditional_beta_quantiles():
    analyzed = prepare_analysis_table(varied_rows())
    for row in analyzed.itertuples():
        alpha = row.tag_decks + row.tag_prior_mean_pct * row.tag_prior_strength
        beta_param = row.total_decks - row.tag_decks + (1 - row.tag_prior_mean_pct) * row.tag_prior_strength
        assert row.tag_affinity_lower_pct == pytest.approx(beta.ppf(.025, alpha, beta_param))
        assert row.tag_affinity_upper_pct == pytest.approx(beta.ppf(.975, alpha, beta_param))
        assert 0 <= row.tag_affinity_lower_pct <= row.tag_affinity_adjusted_pct <= row.tag_affinity_upper_pct <= 1


def test_tiny_rows_do_not_train_prior_but_get_adjusted_estimates():
    rows = varied_rows().iloc[:-1]
    before = prepare_analysis_table(rows)
    tiny = make_rows([(199, 199)])
    tiny["commander_slug"] = "tiny-extreme"
    after = prepare_analysis_table(pd.concat([rows, tiny], ignore_index=True))
    for column in ["tag_prior_mean_pct", "tag_prior_strength", "tag_prior_std_pct"]:
        assert after[column].iloc[0] == before[column].iloc[0]
    assert after.tag_reference_row_count.iloc[-1] == 7
    assert after.affinity_model_status.iloc[-1] == "fitted"
    assert after.tag_affinity_adjusted_pct.iloc[-1] < 1
    assert not after.analysis_eligible.iloc[-1]


def test_reference_has_no_tag_deck_count_floor_and_is_not_display_filter_dependent():
    rows = make_rows([(0, 300), (1, 300), (2, 300), (20, 300), (50, 300), (100, 300)])
    default = prepare_analysis_table(rows)
    filtered = prepare_analysis_table(rows, min_total_decks=10000, min_tag_decks=100)
    assert set(default.affinity_model_status) == {"fitted"}
    assert (default.tag_reference_row_count == 6).all()
    np.testing.assert_allclose(default.z, filtered.z)
    assert not filtered.analysis_eligible.any()


@pytest.mark.parametrize("counts,status", [
    ([(10, 200)], "fallback_invariant"),
    ([(10, 200), (20, 200), (30, 200)], "fallback_sparse"),
    ([(10, 100), (20, 200), (30, 300), (40, 400), (50, 500)], "fallback_invariant"),
    ([(20, 200)] * 5 + [(1, 10)], "fallback_invariant"),
])
def test_fallbacks_retain_legacy_and_do_not_fabricate_intervals(counts, status):
    analyzed = prepare_analysis_table(make_rows(counts))
    assert set(analyzed.affinity_model_status) == {status}
    np.testing.assert_allclose(analyzed.z, analyzed.legacy_z, equal_nan=True)
    np.testing.assert_allclose(analyzed.tag_affinity_adjusted_pct, analyzed.tag_affinity_pct)
    for column in ["tag_affinity_lower_pct", "tag_affinity_upper_pct", "tag_prior_mean_pct", "tag_prior_strength", "tag_prior_std_pct"]:
        assert analyzed[column].isna().all()


def test_failed_fit_is_explicit_fallback(monkeypatch):
    monkeypatch.setattr(affinity_model, "fit_tag_prior", lambda *args: None)
    analyzed = prepare_analysis_table(varied_rows())
    assert set(analyzed.affinity_model_status) == {"fallback_fit_failed"}
    np.testing.assert_allclose(analyzed.z, analyzed.legacy_z)
    assert analyzed.tag_affinity_lower_pct.isna().all()


def test_noise_without_identifiable_population_spread_falls_back():
    analyzed = prepare_analysis_table(make_rows([(19, 200), (20, 200), (21, 200), (20, 200), (20, 200)]))
    assert set(analyzed.affinity_model_status) == {"fallback_fit_failed"}
    np.testing.assert_allclose(analyzed.z, analyzed.legacy_z)
    assert analyzed.tag_prior_strength.isna().all()


def test_one_popular_commander_does_not_define_deck_weighted_baseline():
    counts = np.array([10, 20, 30, 40, 50, 900000])
    totals = np.array([200, 200, 200, 200, 200, 1000000])
    prior = fit_tag_prior(counts, totals)
    assert prior is not None
    assert prior.mean < .4
    assert counts.sum() / totals.sum() > .89


@pytest.mark.parametrize("counts", [
    [(0, 1000000), (1, 1000000), (10, 1000000), (100, 1000000), (10000, 1000000)],
    [(0, 1000000), (1000000, 1000000), (1, 1000000), (999999, 1000000), (500000, 1000000)],
])
def test_extreme_counts_zero_and_one_have_finite_fits(counts):
    analyzed = prepare_analysis_table(make_rows(counts))
    assert set(analyzed.affinity_model_status) == {"fitted"}
    assert np.isfinite(analyzed.z).all()
    assert np.isfinite(analyzed.tag_affinity_lower_pct).all()
    assert np.isfinite(analyzed.tag_affinity_upper_pct).all()
    assert analyzed.tag_affinity_adjusted_pct.between(0, 1, inclusive="neither").all()


def test_learned_prior_recovers_known_population_and_reduces_estimation_error():
    rng = np.random.default_rng(816)
    true_affinity = rng.beta(4, 36, size=1000)
    totals = rng.integers(200, 401, size=1000)
    counts = rng.binomial(totals, true_affinity)
    prior = fit_tag_prior(counts, totals)
    assert prior is not None
    assert prior.mean == pytest.approx(.1, abs=.01)
    assert prior.strength == pytest.approx(40, rel=.25)
    adjusted = (counts + prior.alpha) / (totals + prior.strength)
    raw_error = np.mean((counts / totals - true_affinity) ** 2)
    adjusted_error = np.mean((adjusted - true_affinity) ** 2)
    assert adjusted_error < raw_error


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 1.5])
@pytest.mark.parametrize("column", ["total_decks", "tag_decks"])
def test_binomial_counts_must_be_finite_integers(value, column):
    rows = varied_rows()
    rows[column] = rows[column].astype(float)
    rows.loc[0, column] = value
    with pytest.raises(ValueError, match="finite integer deck counts"):
        prepare_analysis_table(rows)


def test_direct_analysis_cli_smoke(tmp_path):
    input_path = tmp_path / "clean.json"
    output_path = tmp_path / "analysis"
    input_path.write_text(varied_rows().to_json(orient="records"))
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(project_root / "src/edhrec_affinity/analysis.py"),
         "--input", str(input_path), "--output-dir", str(output_path)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(completed.stdout)["affinity_model_status_tag_counts"] == {"fitted": 1}
    written = json.loads((output_path / "affinity_rows.json").read_text())
    assert written[0]["affinity_model_version"] == "beta_binomial_v1"
