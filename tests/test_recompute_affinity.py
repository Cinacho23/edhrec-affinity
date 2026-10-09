from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from edhrec_affinity.affinity_model import AFFINITY_MODEL_VERSION
from edhrec_affinity.analysis import prepare_analysis_table


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recompute_affinity.py"
spec = importlib.util.spec_from_file_location("recompute_affinity", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
recompute_affinity = module.recompute_affinity


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cached_rows() -> list[dict]:
    rows = [
        {
            "commander_slug": f"commander-{index}",
            "commander_name": f"Commander {index}",
            "tag_slug": "tokens",
            "tag_name": "Tokens",
            "total_decks": 1000,
            "tag_decks": count,
            "source_type": "commander_json",
            "scrape_timestamp": "2026-10-01T00:00:00+00:00",
            "card_image_url": f"https://example.org/{index}.jpg",
            "partner_card_image_urls": [f"https://example.org/{index}.jpg"],
            "origin_sets": [{"set_code": "tst", "set_name": "Test"}],
            "snapshot_status": "existing",
            "current_snapshot": "2026-10-01",
            "previous_snapshot": "2026-09-24",
            "total_decks_current": 1000,
            "total_decks_previous": 900,
            "total_decks_delta": 100,
            "total_decks_pct_change": 100 / 900,
            "tag_decks_current": count,
            "tag_decks_previous": count - 1,
            "tag_decks_delta": 1,
            "affinity_pct_current": count / 1000,
            "affinity_pct_previous": (count - 1) / 900,
            "affinity_pct_delta": count / 1000 - (count - 1) / 900,
        }
        for index, count in enumerate([50, 100, 200, 350, 500, 750])
    ]
    raw = pd.Series([row["tag_decks"] / row["total_decks"] for row in rows])
    for index, row in enumerate(rows):
        row.update({
            "tag_affinity_pct": raw.iloc[index],
            "tag_mean_pct": raw.mean(),
            "tag_std_pct": raw.std(),
            "tag_row_count": len(rows),
            "z": round((raw.iloc[index] - raw.mean()) / raw.std(), 10),
            "z_previous": 0.1,
            "z_delta": 0.2,
            "rank_within_tag_by_z": len(rows) - index,
            "rank_within_tag_by_z_current": len(rows) - index,
            "rank_within_tag_by_z_previous": len(rows) - index + 1,
            "rank_delta": 1,
            "rank_within_tag_by_z_delta": 1,
        })
        row["z_current"] = row["z"]
    return rows


def snapshot(tmp_path: Path, *, with_clean: bool = False) -> tuple[Path, list[dict]]:
    processed = tmp_path / "restored"
    rows = cached_rows()
    write_json(processed / "affinity_rows_with_trends.json", rows)
    write_json(processed / "analysis_summary.json", {
        "min_total_decks_default": 200, "min_tag_decks_default": 5,
    })
    write_json(processed / "trend_summary.json", {
        "current_snapshot": "2026-10-01", "rows_with_tag_decks_delta": 6,
        "rows_with_z_delta": 6, "rows_with_rank_delta": 6,
    })
    write_json(processed / "trend_rows.json", rows)
    if with_clean:
        # Existing derived fields must not collide with recomputed statistics.
        clean = [{**row, "tag_mean_pct": -999, "tag_std_pct": -999} for row in rows]
        write_json(processed / "commander_tags_clean.json", clean)
    return processed, rows


def test_cached_count_fallback_upgrades_all_outputs_and_retains_enrichment_and_raw_trends(tmp_path):
    processed, old_rows = snapshot(tmp_path)
    result = recompute_affinity(processed)
    assert result["recomputed"] is True
    assert result["input_path"].endswith("affinity_rows_with_trends.json")
    rows = read_json(processed / "affinity_rows_with_trends.json")
    expected = prepare_analysis_table(pd.DataFrame(old_rows)[sorted(module.REQUIRED_INPUT_COLUMNS)])
    for old, row, (_, wanted) in zip(old_rows, rows, expected.iterrows(), strict=True):
        assert row["affinity_model_version"] == AFFINITY_MODEL_VERSION
        assert row["affinity_model_status"] == "fitted"
        assert row["tag_affinity_adjusted_pct"] == pytest.approx(wanted["tag_affinity_adjusted_pct"])
        assert row["z"] == pytest.approx(wanted["z"])
        assert row["legacy_z"] == old["z"]
        assert row["card_image_url"] == old["card_image_url"]
        assert row["partner_card_image_urls"] == old["partner_card_image_urls"]
        assert row["origin_sets"] == old["origin_sets"]
        assert row["total_decks_delta"] == old["total_decks_delta"]
        assert row["tag_decks_delta"] == old["tag_decks_delta"]
        assert row["affinity_pct_delta"] == pytest.approx(old["affinity_pct_delta"])
        assert row["z_current"] == row["z"]
        assert row["rank_within_tag_by_z_current"] == row["rank_within_tag_by_z"]
        assert row["score_trend_status"] == "algorithm_changed"
        assert row["affinity_model_version_previous"] == "legacy_z_v1"
        for column in module.SCORE_PREVIOUS_AND_DELTA_COLUMNS:
            assert row[column] is None

    for filename in ["affinity_rows.json", "tag_rankings.json", "global_leaderboard.json"]:
        exported = read_json(processed / filename)
        assert len(exported) == 6
        assert all(row["affinity_model_version"] == AFFINITY_MODEL_VERSION for row in exported)
        assert all("z_current" not in row for row in exported)
    assert read_json(processed / "tag_summary.json")[0]["affinity_model_status"] == "fitted"
    assert read_json(processed / "analysis_summary.json")["recomputed_from_cached_counts"] is True
    trend_summary = read_json(processed / "trend_summary.json")
    assert trend_summary["rows_with_tag_decks_delta"] == 6
    assert trend_summary["rows_with_z_delta"] == 0
    assert trend_summary["rows_with_rank_delta"] == 0
    assert all(row["z_delta"] is None for row in read_json(processed / "trend_rows.json"))


def test_prefers_clean_counts_and_preserves_explicit_legacy_scores(tmp_path):
    processed, old_rows = snapshot(tmp_path, with_clean=True)
    old_rows[0]["legacy_z"] = 1.05
    write_json(processed / "affinity_rows_with_trends.json", old_rows)
    result = recompute_affinity(processed)
    assert result["input_path"].endswith("commander_tags_clean.json")
    rows = read_json(processed / "affinity_rows_with_trends.json")
    assert rows[0]["legacy_z"] == 1.05
    assert rows[0]["tag_mean_pct"] > 0
    assert rows[0]["tag_std_pct"] > 0


def test_current_model_is_idempotent_and_keeps_comparable_score_trends(tmp_path):
    processed, _ = snapshot(tmp_path)
    recompute_affinity(processed)
    rows = read_json(processed / "affinity_rows_with_trends.json")
    rows[0]["score_trend_status"] = "comparable"
    rows[0]["z_previous"] = rows[0]["z"] - 0.1
    rows[0]["z_delta"] = 0.1
    write_json(processed / "affinity_rows_with_trends.json", rows)
    before = {path.name: path.read_bytes() for path in processed.iterdir()}
    result = recompute_affinity(processed)
    assert result["recomputed"] is False
    assert result["reason"] == "already_current_model"
    assert {path.name: path.read_bytes() for path in processed.iterdir()} == before


@pytest.mark.parametrize("difference", ["keys", "counts", "source"])
def test_mismatched_clean_snapshot_fails_before_any_writes(tmp_path, difference):
    processed, _ = snapshot(tmp_path, with_clean=True)
    clean_path = processed / "commander_tags_clean.json"
    clean = read_json(clean_path)
    if difference == "keys":
        clean[0]["commander_slug"] = "other"
    elif difference == "counts":
        clean[0]["total_decks"] = 1001
    else:
        clean[0]["source_type"] = "cedh_filtered_json"
    write_json(clean_path, clean)
    before = {path.name: path.read_bytes() for path in processed.iterdir()}
    with pytest.raises(ValueError, match="different commander/tag keys|disagree"):
        recompute_affinity(processed)
    assert {path.name: path.read_bytes() for path in processed.iterdir()} == before


def test_alias_only_cached_file_replaces_stale_current_scores(tmp_path):
    processed, rows = snapshot(tmp_path)
    for row in rows:
        row["total_decks_x"] = row.pop("total_decks")
        row["tag_decks_x"] = row.pop("tag_decks")
        row["z_x"] = row.pop("z")
    write_json(processed / "affinity_rows_with_trends.json", rows)
    recompute_affinity(processed)
    migrated = read_json(processed / "affinity_rows_with_trends.json")
    assert migrated[0]["legacy_z"] == rows[0]["z_current"]
    assert migrated[0]["z_current"] == migrated[0]["z"]
    assert migrated[0]["z_current"] != rows[0]["z_current"]


def test_first_run_trend_audit_refreshes_canonical_scores_and_retains_removed_history(tmp_path):
    processed, rows = snapshot(tmp_path)
    for row in rows:
        row["snapshot_status"] = "no_previous_snapshot"
        row["previous_snapshot"] = None
    write_json(processed / "affinity_rows_with_trends.json", rows)
    trends = [{
        **row, "affinity_model_version": "legacy_z_v1",
        "affinity_model_status": "legacy",
    } for row in rows]
    removed = {
        **trends[0], "commander_slug": "removed-commander",
        "snapshot_status": "removed_pair", "z": 2.5, "z_current": None,
        "z_previous": 2.5, "rank_within_tag_by_z_previous": 2,
        "affinity_model_version_previous": "legacy_z_v1",
        "affinity_model_status_previous": "legacy",
    }
    write_json(processed / "trend_rows.json", trends + [removed])
    recompute_affinity(processed)
    audit = read_json(processed / "trend_rows.json")
    current = read_json(processed / "affinity_rows_with_trends.json")
    for row, wanted in zip(audit[:-1], current, strict=True):
        assert row["z"] == row["z_current"] == wanted["z"]
        assert row["rank_within_tag_by_z"] == wanted["rank_within_tag_by_z"]
        assert row["affinity_model_version"] == AFFINITY_MODEL_VERSION
        assert row["affinity_model_status"] == "fitted"
        assert row["score_trend_status"] == "no_previous_snapshot"
        assert row["affinity_model_version_previous"] is None
    assert audit[-1]["z"] == audit[-1]["z_previous"] == 2.5
    assert audit[-1]["z_current"] is None
    assert audit[-1]["rank_within_tag_by_z_previous"] == 2
    assert audit[-1]["affinity_model_version"] == "legacy_z_v1"
    assert audit[-1]["affinity_model_version_previous"] == "legacy_z_v1"
    assert audit[-1]["score_trend_status"] == "no_previous_pair"
