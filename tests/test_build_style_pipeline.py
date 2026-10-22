"""Offline integration checks for durable full-catalog coordination."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import threading

import httpx
import pytest

from edhrec_affinity.build_styles.pipeline import atomic_json, evidence_version, run_pipeline, save_evidence
from edhrec_affinity.build_styles.sources import SourceBlocked


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def options(tmp_path, **changes):
    defaults = dict(output_dir=tmp_path / "build-styles", snapshot_date="2026-10-21", commanders=None,
        min_total_decks=0, refresh_after_days=3, refresh=False, max_commanders=0, max_runtime_seconds=30,
        requests_per_second=40, workers=1, analysis_workers=1, preview_workers=1, max_preview_seconds=10,
        card_metadata_cache=tmp_path / "cards.json", fetch=False, sample_decks=None, seed=42,
        max_components=8, max_tags=12, min_tag_decks=25, request_delay=1, source_max_age_hours=72,
        index_max_age_hours=24, target_completion_hours=96, dry_run=False, min_free_disk_gb=0,
        prune_completed_cache=False)
    return argparse.Namespace(**(defaults | changes))


def rows(*slugs):
    return [{"commander_slug": slug, "total_decks": 5} for slug in slugs]


def evidence(slug, *, frame=5, loaded=5, attempted=None, new_attempts=None, complete=True,
             mode="all", dataset="stable-dataset", checked=NOW):
    attempted = loaded if attempted is None else attempted
    return {"slug": slug, "card": {"name": slug}, "base": {"commander_names": [slug], "deck_count": frame},
        "profiles": [], "ranking_profiles": [], "decks": [], "metadata": {}, "metadata_summary": {},
        "failures": [], "imported_count": 0, "blocked": False, "provenance": [],
        "collection": {"target_mode": mode, "frame_decks": frame, "previews_attempted": attempted,
            "previews_loaded": loaded, "fresh_previews": loaded, "unavailable_previews": 0,
            "new_previews_attempted": attempted if new_attempts is None else new_attempts,
            "complete": complete, "dataset_sha256": dataset,
            "freshness": {"oldest_source_at": checked.isoformat(), "oldest_verified_at": checked.isoformat(),
                "newest_source_at": checked.isoformat(), "newest_verified_at": checked.isoformat(),
                "observed_previews": loaded, "dated_previews": loaded, "undated_previews": 0,
                "stale_previews": 0, "index_source_at": checked.isoformat(), "index_stale": False,
                "source_complete": complete and mode == "all"}}}


def saved_analyzer(calls, on_start=None):
    def analyze(path, settings):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            source = json.load(handle)
        slug = source["slug"]
        calls.append(slug)
        if on_start:
            on_start(slug)
        destination = Path(settings["output_dir"]) / "report.json"
        report = {"schema_version": 1, "commander_slug": slug, "commander_name": slug,
            "generated_at": NOW.isoformat(), "collection": source["collection"],
            "deck_analysis": {"status": "fitted", "unique_decks": source["collection"]["previews_loaded"],
                "packages": [{"id": 0, "top_cards": []}]}}
        atomic_json(destination, report)
        return {"slug": slug, "report": str(destination), "status": "fitted", "version": source["analysis_version"]}
    return analyze


def execute(args, inventory, collector, analyzer, *, checked=NOW, **kwargs):
    return run_pipeline(args, inventory, collector=collector, analyzer=analyzer,
        analysis_executor_factory=lambda: ThreadPoolExecutor(max_workers=args.analysis_workers),
        now=lambda: checked, **kwargs)


def checkpoint(args):
    return json.loads((args.output_dir / "_collection/state.json").read_text())


def test_complete_collection_is_saved_analyzed_and_published_separately(tmp_path):
    args = options(tmp_path)
    collected, analyzed = [], []

    def collect(settings, **dependencies):
        collected.append(settings.commander)
        assert dependencies["limiter"] is not None
        return evidence(settings.commander)

    result = execute(args, rows("a", "b"), collect, saved_analyzer(analyzed))
    assert sorted(collected) == sorted(analyzed) == ["a", "b"]
    assert sorted(result["completed"]) == ["a", "b"]
    assert result["deadline_target_met"] is True
    assert result["publish_needed"] is True
    assert result["continuation_needed"] is False
    assert checkpoint(args)["commanders"]["a"]["coverage_complete"] is True
    assert not list((args.output_dir / "_collection/evidence").glob("*.gz"))
    assert json.loads((args.output_dir / "a.json").read_text())["collection"]["analysis_current"] is True


def test_finite_sample_cannot_be_marked_full_catalog_coverage(tmp_path):
    args = options(tmp_path, sample_decks=2)
    calls, analyzed = [], []

    def collect(settings, **_):
        calls.append(settings.commander)
        return evidence(settings.commander, loaded=2, complete=True, mode="sample", new_attempts=2 if len(calls) == 1 else 0)

    result = execute(args, rows("a"), collect, saved_analyzer(analyzed))
    assert result["deadline_target_met"] is False
    assert checkpoint(args)["commanders"]["a"]["coverage_complete"] is False


def test_analysis_pending_evidence_resumes_without_another_network_collection(tmp_path):
    args = options(tmp_path)
    source = evidence("a", new_attempts=0)
    source["analysis_version"] = evidence_version(source)
    source["collection"]["analysis_current"] = True
    save_evidence(args.output_dir / "_collection/evidence/a.json.gz", source)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {"a": {
        "status": "analysis_pending", "coverage_complete": True, "frame_decks": 5,
        "current_previews": 5, "verified_at": NOW.isoformat()}}})
    analyzed = []

    def no_collection(*_, **__):
        pytest.fail("A durable analysis input must not be downloaded again")

    result = execute(args, rows("a"), no_collection, saved_analyzer(analyzed))
    assert analyzed == ["a"]
    assert result["collected"] == []
    assert result["completed"] == ["a"]
    assert checkpoint(args)["commanders"]["a"]["status"] == "fitted"


def test_partial_failure_keeps_the_old_model_and_retries_after_backoff(tmp_path):
    args = options(tmp_path)
    atomic_json(args.output_dir / "a.json", {"commander_slug": "a", "collection": {},
        "deck_analysis": {"status": "fitted", "unique_decks": 1, "packages": [{"id": 0}]}})
    calls, analyzed = [], []

    def partial(settings, **_):
        calls.append(settings.commander)
        return evidence("a", loaded=1, complete=False, new_attempts=1 if len(calls) == 1 else 0)

    first = execute(args, rows("a"), partial, saved_analyzer(analyzed))
    assert calls == ["a", "a"]
    assert analyzed == []
    assert first["deadline_target_met"] is False
    assert first["preview_processing_rate"] > 0
    assert first["capacity"]["observed_requests_per_second"] is None
    assert first["capacity"]["projected_finish_at"] is None
    assert checkpoint(args)["commanders"]["a"]["status"] == "failed"
    old = json.loads((args.output_dir / "a.json").read_text())
    assert old["collection"]["analysis_current"] is False
    assert old["deck_analysis"]["unique_decks"] == 1

    retry = execute(args, rows("a"), lambda settings, **_: evidence("a", new_attempts=4),
                    saved_analyzer(analyzed), checked=NOW + timedelta(minutes=31))
    assert retry["completed"] == ["a"]
    assert analyzed == ["a"]
    assert checkpoint(args)["commanders"]["a"]["coverage_complete"] is True


def test_analyzer_failure_retains_evidence_for_the_next_attempt(tmp_path):
    args = options(tmp_path)

    def fail_analysis(*_):
        raise RuntimeError("worker interrupted")

    first = execute(args, rows("a"), lambda settings, **_: evidence("a"), fail_analysis)
    assert first["failures"][0]["error"] == "worker interrupted"
    assert (args.output_dir / "_collection/evidence/a.json.gz").exists()
    assert checkpoint(args)["commanders"]["a"]["status"] == "analysis_pending"
    analyzed = []

    def no_collection(*_, **__):
        pytest.fail("The completed source collection should survive an analysis failure")

    retry = execute(args, rows("a"), no_collection, saved_analyzer(analyzed), checked=NOW + timedelta(minutes=31))
    assert retry["completed"] == ["a"]
    assert analyzed == ["a"]


def test_unchanged_dataset_reuses_the_model_but_updates_source_observations(tmp_path):
    args = options(tmp_path)
    analyzed = []
    execute(args, rows("a"), lambda settings, **_: evidence("a"), saved_analyzer(analyzed))
    args.refresh = True
    updated_at = NOW + timedelta(days=1)
    result = execute(args, rows("a"), lambda settings, **_: evidence("a", new_attempts=0, checked=updated_at),
                     saved_analyzer(analyzed), checked=updated_at)
    assert analyzed == ["a"]
    assert result["completed"] == ["a"]
    report = json.loads((args.output_dir / "a.json").read_text())
    assert report["collection"]["freshness"]["oldest_verified_at"] == updated_at.isoformat()


def test_model_configuration_change_reanalyzes_even_when_source_content_is_identical(tmp_path):
    args = options(tmp_path)
    analyzed = []
    collect = lambda settings, **_: evidence("a")
    execute(args, rows("a"), collect, saved_analyzer(analyzed))
    args.refresh = True
    args.max_components = 4
    execute(args, rows("a"), collect, saved_analyzer(analyzed))
    assert analyzed == ["a", "a"]


def test_shared_access_block_stops_new_collection_and_automatic_continuation(tmp_path):
    args = options(tmp_path)
    calls = []

    def blocked(settings, **dependencies):
        calls.append(settings.commander)
        dependencies["limiter"].block("access challenge")
        raise SourceBlocked("access challenge")

    result = execute(args, rows("a", "b", "c"), blocked, saved_analyzer([]))
    assert calls == ["a"]
    assert result["blocked"] is True
    assert result["continuation_needed"] is False
    second = execute(args, rows("a", "b", "c"), blocked, saved_analyzer([]))
    assert calls == ["a"]
    assert second["blocked"] is True


def test_network_collection_and_analysis_overlap_without_a_sleep(tmp_path):
    args = options(tmp_path)
    second_collection = threading.Event()
    first_analysis = threading.Event()
    overlap = []

    def collect(settings, **_):
        if settings.commander == "b":
            second_collection.set()
            assert first_analysis.wait(2), "Analysis should run while the next commander is collected"
            overlap.append(True)
        return evidence(settings.commander)

    def analyze_started(slug):
        if slug == "a":
            first_analysis.set()
            assert second_collection.wait(2), "Collection should continue while CPU analysis is running"

    result = execute(args, rows("a", "b"), collect, saved_analyzer([], analyze_started))
    assert overlap == [True]
    assert sorted(result["completed"]) == ["a", "b"]


def test_expired_deadline_reports_a_capacity_miss_without_inventing_a_measured_rate(tmp_path):
    args = options(tmp_path, dry_run=True)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {},
        "pipeline": {"version": 1, "cycle_started_at": (NOW - timedelta(hours=97)).isoformat()}})
    result = execute(args, [{"commander_slug": "a", "total_decks": 12_000_000}],
                     lambda *_: pytest.fail("dry run collected sources"), saved_analyzer([]))
    assert result["capacity"]["status"] == "behind_target"
    assert result["capacity"]["configured_requests_per_second"] == 40
    assert result["capacity"]["observed_requests_per_second"] is None
    assert result["capacity"]["projected_finish_at"] is None
    assert result["deadline_target_met"] is False


def test_source_http_failure_is_checkpointed_without_losing_other_commanders(tmp_path):
    args = options(tmp_path)

    def collect(settings, **_):
        if settings.commander == "a":
            raise httpx.ConnectError("temporary network outage")
        return evidence(settings.commander)

    result = execute(args, rows("a", "b"), collect, saved_analyzer([]))
    assert result["completed"] == ["b"]
    assert checkpoint(args)["commanders"]["a"]["status"] == "failed"
    assert result["failures"][0]["commander_slug"] == "a"


def test_removed_commander_failures_do_not_block_the_current_catalog_target(tmp_path):
    args = options(tmp_path)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {
        "removed": {"status": "failed", "unavailable_previews": 4}}})
    result = execute(args, rows("a"), lambda settings, **_: evidence("a"), saved_analyzer([]))
    assert result["deadline_target_met"] is True
    assert result["capacity"]["unavailable_previews"] == 0


def test_missing_published_report_is_rebuilt_even_if_a_checkpoint_says_fitted(tmp_path):
    args = options(tmp_path)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {"a": {
        "status": "fitted", "coverage_complete": True, "frame_decks": 5,
        "current_previews": 5, "verified_at": NOW.isoformat()}}})
    analyzed = []
    result = execute(args, rows("a"), lambda settings, **_: evidence("a"), saved_analyzer(analyzed))
    assert result["completed"] == ["a"]
    assert analyzed == ["a"]
    assert (args.output_dir / "a.json").exists()
