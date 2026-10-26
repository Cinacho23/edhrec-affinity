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
from edhrec_affinity.build_styles.analysis import tag_label_policy
from edhrec_affinity.build_styles.sources import SourceBlocked
from edhrec_affinity.build_styles.eligibility import CommanderBelowMinimum


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def options(tmp_path, **changes):
    defaults = dict(output_dir=tmp_path / "build-styles", snapshot_date="2026-10-21", commanders=None,
        min_total_decks=200, refresh_after_days=3, refresh=False, max_commanders=0, max_runtime_seconds=30,
        requests_per_second=40, workers=1, analysis_workers=1, preview_workers=1, max_preview_seconds=10,
        card_metadata_cache=tmp_path / "cards.json", fetch=False, sample_decks=None, seed=42,
        max_components=8, max_tags=12, min_tag_decks=5, main_tag_decks=15, request_delay=1, source_max_age_hours=72,
        index_max_age_hours=24, target_completion_hours=96, dry_run=False, min_free_disk_gb=0,
        prune_completed_cache=False)
    return argparse.Namespace(**(defaults | changes))


def rows(*slugs):
    return [{"commander_slug": slug, "total_decks": 200} for slug in slugs]


def evidence(slug, *, frame=5, loaded=5, attempted=None, new_attempts=None, complete=True,
             mode="all", dataset="stable-dataset", checked=NOW):
    attempted = loaded if attempted is None else attempted
    return {"slug": slug, "card": {"name": slug}, "base": {"commander_names": [slug], "deck_count": 200},
        "profiles": [], "ranking_profiles": [], "decks": [], "metadata": {}, "metadata_summary": {},
        "tag_label_policy": tag_label_policy(),
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
            "generated_at": NOW.isoformat(), "total_edhrec_decks": source["base"]["deck_count"], "collection": source["collection"],
            "tag_label_policy": source.get("tag_label_policy"),
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
    assert checkpoint(args)["commanders"]["a"]["collection_dataset_sha256"] == "stable-dataset"
    assert not list((args.output_dir / "_collection/evidence").glob("*.gz"))
    assert json.loads((args.output_dir / "a.json").read_text())["collection"]["analysis_current"] is True


def chunked_collector(ticks, progress, calls, *, frames=None):
    """One virtual second represents one finite preview-collection visit."""
    def collect(settings, **_):
        slug = settings.commander
        frame = (frames or {}).get(slug, 200)
        before = progress.get(slug, 0)
        progress[slug] = min(frame, before + 50)
        calls.append(slug)
        ticks[0] += 1
        return evidence(slug, frame=frame, loaded=progress[slug],
                        new_attempts=progress[slug] - before, complete=progress[slug] == frame,
                        dataset=f"{slug}-{progress[slug]}")
    return collect


def test_time_sliced_full_frames_finish_before_visiting_the_entire_catalog(tmp_path):
    args = options(tmp_path, max_runtime_seconds=8, max_preview_seconds=1)
    ticks, progress, calls, analyzed = [0], {}, [], []
    inventory = rows(*[f"commander-{index:02d}" for index in range(20)])
    result = execute(args, inventory, chunked_collector(ticks, progress, calls), saved_analyzer(analyzed),
                     monotonic=lambda: ticks[0])
    assert result["completed"]
    assert result["time_budget_reached"]
    assert len(set(calls)) < len(inventory)
    for slug in result["completed"]:
        report = json.loads((args.output_dir / f"{slug}.json").read_text())
        assert report["total_edhrec_decks"] == 200
        assert report["collection"]["frame_decks"] == report["collection"]["previews_loaded"] == 200
        assert report["collection"]["complete"] is True
        assert report["collection"]["target_mode"] == "all"


def test_saved_partial_finishes_before_a_new_equal_sized_frame_on_the_next_job(tmp_path):
    args = options(tmp_path, max_runtime_seconds=2, max_preview_seconds=1)
    ticks, progress, calls, analyzed = [0], {}, [], []
    collect = chunked_collector(ticks, progress, calls)
    first = execute(args, rows("partial"), collect, saved_analyzer(analyzed), monotonic=lambda: ticks[0])
    assert first["completed"] == []
    assert checkpoint(args)["commanders"]["partial"]["current_previews"] == 100
    calls.clear()
    ticks[0] = 0
    second = execute(args, rows("a-new", "partial"), collect, saved_analyzer(analyzed),
                     checked=NOW + timedelta(minutes=31), monotonic=lambda: ticks[0])
    assert second["completed"] == ["partial"]
    assert "a-new" not in calls
    assert checkpoint(args)["commanders"]["partial"]["coverage_complete"] is True


def test_two_large_frames_do_not_monopolize_a_small_queued_commander(tmp_path):
    args = options(tmp_path, max_runtime_seconds=8, max_preview_seconds=1)
    ticks, progress, calls, analyzed = [0], {}, [], []
    # Catalog counts may initially underestimate the actual available index.
    # Once each large frame is discovered, its continuation must yield to the
    # smaller remaining frame instead of trading priority with the other giant.
    frames = {"a-giant": 100_000, "b-giant": 100_000, "c-small": 200}
    result = execute(args, rows(*frames), chunked_collector(ticks, progress, calls, frames=frames),
                     saved_analyzer(analyzed), monotonic=lambda: ticks[0])
    assert "c-small" in result["completed"]
    assert set(calls[:2]) == {"a-giant", "b-giant"}
    assert progress["a-giant"] < frames["a-giant"] and progress["b-giant"] < frames["b-giant"]
    report = json.loads((args.output_dir / "c-small.json").read_text())
    assert report["collection"]["complete"] is True
    assert report["collection"]["previews_loaded"] == 200


def test_completed_report_refresh_waits_for_the_unfinished_backlog(tmp_path):
    args = options(tmp_path, max_runtime_seconds=4, max_preview_seconds=1)
    old = NOW - timedelta(days=2)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {
        "a-refresh": {"status": "fitted", "coverage_complete": True, "frame_decks": 200,
                      "tag_label_policy": tag_label_policy(),
                      "current_previews": 200, "verified_at": old.isoformat()},
    }})
    atomic_json(args.output_dir / "a-refresh.json", {"commander_slug": "a-refresh"})
    ticks, progress, calls, analyzed = [0], {}, [], []
    result = execute(args, rows("a-refresh", "new"), chunked_collector(ticks, progress, calls),
                     saved_analyzer(analyzed), monotonic=lambda: ticks[0])
    assert result["completed"] == ["new"]
    assert "a-refresh" not in calls


def test_pipeline_collects_only_commanders_with_at_least_200_total_decks(tmp_path):
    args, collected = options(tmp_path), []
    inventory = [{"commander_slug": slug, "total_decks": count}
                 for slug, count in [("rare", 6), ("low", 199), ("eligible", 200), ("unknown", None)]]

    def collect(settings, **_):
        collected.append(settings.commander)
        assert settings.min_total_decks == 200
        return evidence(settings.commander)

    result = execute(args, inventory, collect, saved_analyzer([]))
    assert collected == ["eligible"]
    assert result["catalog_size"] == 1
    assert result["min_total_decks"] == 200
    args.commanders = ["low"]
    with pytest.raises(ValueError, match="at least 200"):
        execute(args, inventory, collect, saved_analyzer([]))
    args.commanders, args.min_total_decks = None, 0
    with pytest.raises(ValueError, match="at least 200"):
        execute(args, inventory, collect, saved_analyzer([]))


def test_fresh_source_below_minimum_is_skipped_without_analysis_or_repeat_work(tmp_path):
    args, analyzed = options(tmp_path), []
    atomic_json(args.output_dir / "a.json", {"total_edhrec_decks": 200, "collection": {},
        "deck_analysis": {"status": "fitted", "packages": []}})

    def collect(*_, **__):
        raise CommanderBelowMinimum(199)

    result = execute(args, rows("a"), collect, saved_analyzer(analyzed))
    assert analyzed == [] and result["failures"] == []
    assert result["skipped"][0]["total_decks"] == 199
    assert checkpoint(args)["commanders"]["a"]["status"] == "ineligible"
    assert result["capacity"]["remaining_previews"] == 0
    archived = json.loads((args.output_dir / "a.json").read_text())
    assert archived["total_edhrec_decks"] == 199
    assert archived["collection"]["analysis_current"] is False
    retry = execute(args, rows("a"), lambda *_: pytest.fail("Population check must respect its retry interval"),
                    saved_analyzer(analyzed), checked=NOW + timedelta(hours=1))
    assert retry["selected"] == []


def test_commander_requalifying_with_unchanged_dataset_restores_its_population(tmp_path):
    args, analyzed = options(tmp_path), []
    execute(args, rows("a"), lambda settings, **_: evidence("a"), saved_analyzer(analyzed))
    args.refresh = True

    def below_minimum(*_, **__):
        raise CommanderBelowMinimum(199)

    execute(args, rows("a"), below_minimum, saved_analyzer(analyzed))
    execute(args, rows("a"), lambda settings, **_: evidence("a"), saved_analyzer(analyzed))
    assert analyzed == ["a"]
    report = json.loads((args.output_dir / "a.json").read_text())
    assert report["total_edhrec_decks"] == 200
    assert report["collection"]["analysis_current"] is True


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


def test_new_preview_progress_publishes_global_status_before_a_model_exists(tmp_path):
    args = options(tmp_path, target_completion_hours=None)
    calls = []

    def partial(settings, **_):
        calls.append(settings.commander)
        return evidence("a", loaded=1, complete=False, new_attempts=1 if len(calls) == 1 else 0)

    result = execute(args, rows("a"), partial, saved_analyzer([]))
    assert result["completed"] == []
    assert not (args.output_dir / "a.json").exists()
    assert result["publish_needed"] is True
    assert checkpoint(args)["progress"]["capacity"]["remaining_previews"] == 4
    assert checkpoint(args)["progress"]["rate_control"]["status"] == "calibrating"


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


def test_label_policy_upgrade_is_collected_before_new_commanders_without_resetting_previews(tmp_path):
    args = options(tmp_path)
    analyzed = []
    execute(args, rows("existing"), lambda *_, **__: evidence("existing"), saved_analyzer(analyzed))
    source_state = checkpoint(args)
    source_state["commanders"]["existing"].pop("tag_label_policy")
    atomic_json(args.output_dir / "_collection/state.json", source_state)
    preview = args.output_dir / "_collection/previews/existing.json.gz"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"retained preview checkpoint")
    args.max_commanders = 1
    calls = []

    def collect(settings, **_):
        calls.append(settings.commander)
        assert settings.min_tag_decks == 5 and settings.main_tag_decks == 15
        assert settings.collection_state.read_bytes() == b"retained preview checkpoint"
        return evidence(settings.commander, new_attempts=0)

    result = execute(args, rows("untouched", "existing"), collect, saved_analyzer(analyzed))
    assert result["selected"] == calls == ["existing"]
    assert result["completed"] == ["existing"]
    assert checkpoint(args)["commanders"]["existing"]["tag_label_policy"] == tag_label_policy()
    assert checkpoint(args)["commanders"]["existing"]["verified_at"] == NOW.isoformat()


@pytest.mark.parametrize("pending", ["legacy", "corrupt"])
def test_old_or_broken_pending_evidence_is_recollected_before_analysis(tmp_path, pending):
    args = options(tmp_path)
    source = evidence("a", new_attempts=0)
    source.pop("tag_label_policy")
    source["analysis_version"] = evidence_version(source)
    pending_path = args.output_dir / "_collection/evidence/a.json.gz"
    save_evidence(pending_path, source)
    if pending == "corrupt":
        pending_path.write_bytes(b"truncated gzip")
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {"a": {
        "status": "analysis_pending", "coverage_complete": True, "frame_decks": 5,
        "current_previews": 5, "verified_at": NOW.isoformat()}}})
    calls, analyzed = [], []

    def collect(settings, **_):
        calls.append(settings.commander)
        return evidence(settings.commander, new_attempts=0)

    result = execute(args, rows("a"), collect, saved_analyzer(analyzed))
    assert result["completed"] == calls == analyzed == ["a"]
    assert json.loads((args.output_dir / "a.json").read_text())["tag_label_policy"] == tag_label_policy()


def test_label_threshold_options_invalidate_the_evidence_version(tmp_path):
    args = options(tmp_path)
    source = evidence("a")
    original = evidence_version(source, args)
    args.main_tag_decks = 20
    assert evidence_version(source, args) != original
    args.main_tag_decks, args.min_tag_decks = 15, 10
    assert evidence_version(source, args) != original
    args.min_tag_decks, args.main_tag_fraction = 5, .05
    assert evidence_version(source, args) != original
    args.main_tag_fraction, args.min_tag_fraction = .03, .02
    assert evidence_version(source, args) != original


def test_fixed_count_policy_refreshes_existing_reports_without_discarding_decks(tmp_path):
    args = options(tmp_path)
    analyzed = []
    execute(args, rows("existing"), lambda *_, **__: evidence("existing"), saved_analyzer(analyzed))
    state = checkpoint(args)
    state["commanders"]["existing"]["tag_label_policy"] = {
        "version": 1, "supporting_minimum_decks": 5, "main_minimum_decks": 15, "max_profiles": 12}
    atomic_json(args.output_dir / "_collection/state.json", state)
    preview = args.output_dir / "_collection/previews/existing.json.gz"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"already collected decks")
    calls = []

    def collect(settings, **_):
        calls.append(settings.commander)
        assert settings.min_tag_fraction == .01 and settings.main_tag_fraction == .03
        assert settings.collection_state.read_bytes() == b"already collected decks"
        return evidence(settings.commander, new_attempts=0)

    result = execute(args, rows("existing"), collect, saved_analyzer(analyzed))
    assert result["completed"] == calls == ["existing"]
    assert checkpoint(args)["commanders"]["existing"]["tag_label_policy"]["version"] == 2


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


def test_rate_limit_learning_survives_cooldown_and_the_next_collection_batch(tmp_path):
    args = options(tmp_path)

    def throttled(settings, **dependencies):
        limiter = dependencies["limiter"]
        for _ in range(100):
            limiter.record_success()
        limiter.record_throttle(now=NOW)
        limiter.block("HTTP 429 rate limit", kind="rate_limit")
        raise SourceBlocked("HTTP 429 rate limit")

    first = execute(args, rows("a"), throttled, saved_analyzer([]))
    expected_expiry = (NOW + timedelta(hours=1)).isoformat()
    assert first["blocked_until"] == checkpoint(args)["blocked_until"] == expected_expiry
    assert checkpoint(args)["progress"]["blocked_until"] == expected_expiry
    saved_summary = json.loads((args.output_dir.parent / "build_style_batch.json").read_text())
    assert saved_summary["blocked_until"] == expected_expiry
    learned = checkpoint(args)["source_rate"]
    assert learned["learned_rate"] == pytest.approx(1.875)
    assert learned["throttles"] == 1
    assert learned["last_throttle"]["at"] == NOW.isoformat()
    assert first["capacity"]["configured_requests_per_second"] == 40
    assert first["capacity"]["effective_requests_per_second"] == learned["learned_rate"]
    assert first["publish_needed"] is True
    assert checkpoint(args)["progress"]["rate_control"]["status"] == "learned_below_observed_throttle"

    def no_collection(*_, **__):
        pytest.fail("The source cooldown must survive collection restarts")

    cooldown = execute(args, rows("a"), no_collection, saved_analyzer([]), checked=NOW + timedelta(minutes=30))
    assert cooldown["blocked"] is True
    assert checkpoint(args)["source_rate"] == learned
    assert cooldown["capacity"]["effective_requests_per_second"] == learned["learned_rate"]
    assert cooldown["capacity"]["cooldown_seconds"] == 1800
    assert cooldown["publish_needed"] is False
    assert cooldown["capacity"]["observed_requests_per_second"] == learned["observed_requests_per_second"]
    assert cooldown["capacity"]["observed_rate_at"] == NOW.isoformat()
    assert cooldown["capacity"]["measured_projection_seconds"] is not None
    assert checkpoint(args)["progress"]["rate_control"]["observed_rate_at"] == NOW.isoformat()

    resumed_at = NOW + timedelta(minutes=61)

    def resumed(settings, **dependencies):
        limiter = dependencies["limiter"]
        for _ in range(3000):
            limiter.record_success()
        assert limiter.stats["maximum_requests_per_second"] == learned["learned_rate"]
        assert limiter.stats["current_requests_per_second"] <= learned["learned_rate"]
        return evidence(settings.commander, checked=resumed_at)

    resumed_result = execute(args, rows("a"), resumed, saved_analyzer([]), checked=resumed_at)
    assert resumed_result["completed"] == ["a"]
    assert resumed_result["cycle_started_at"] == NOW.isoformat()
    assert {key: checkpoint(args)["source_rate"][key] for key in ("learned_rate", "last_throttle", "throttles")} == {
        key: learned[key] for key in ("learned_rate", "last_throttle", "throttles")}
    assert checkpoint(args)["source_rate"]["observed_rate_at"] == resumed_at.isoformat()


def test_metadata_access_block_cannot_be_misclassified_as_an_edhrec_rate_pause(tmp_path):
    args = options(tmp_path)

    def collect(settings, **dependencies):
        limiter = dependencies["limiter"]
        limiter.record_throttle(now=NOW)
        limiter.block("HTTP 429 EDHREC", kind="rate_limit")
        value = evidence(settings.commander)
        value.update(blocked=True, metadata_summary={"blocked": True})
        return value

    result = execute(args, rows("a"), collect, saved_analyzer([]))
    assert result["blocked"] is True
    assert result["block_reason"] == "access_restriction"
    assert checkpoint(args)["block_reason"] == "access_restriction"


@pytest.mark.parametrize("invalid", [0, -1, float("inf"), float("nan"), True, "2"])
def test_invalid_saved_rate_cannot_silently_restore_the_configured_ceiling(tmp_path, invalid):
    args = options(tmp_path, dry_run=True)
    state_path = args.output_dir / "_collection/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"schema_version": 1, "commanders": {},
                                     "source_rate": {"learned_rate": invalid}}))
    with pytest.raises(ValueError, match="Learned source rate"):
        execute(args, rows("a"), lambda *_: None, saved_analyzer([]))


@pytest.mark.parametrize("invalid", [0, -1, float("inf"), float("nan"), True, "2"])
def test_invalid_saved_observed_rate_is_not_used_for_eta(tmp_path, invalid):
    args = options(tmp_path, dry_run=True)
    state_path = args.output_dir / "_collection/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"schema_version": 1, "commanders": {},
                                     "source_rate": {"observed_requests_per_second": invalid}}))
    with pytest.raises(ValueError, match="Observed source rate"):
        execute(args, rows("a"), lambda *_: None, saved_analyzer([]))


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


def test_completion_estimates_preserve_fresh_evidence_without_a_deadline(tmp_path):
    args = options(tmp_path, target_completion_hours=None)
    observed = (NOW - timedelta(hours=4)).isoformat()
    record = {"status": "fitted", "coverage_complete": True, "frame_decks": 5,
              "tag_label_policy": tag_label_policy(),
              "current_previews": 5, "verified_at": observed, "analyzed_at": observed, "last_collection_at": observed}
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {"a": record},
        "pipeline": {"version": 1, "cycle_started_at": (NOW - timedelta(days=2)).isoformat(),
                     "upstream_snapshot_date": "2026-10-19"}})
    report_path = args.output_dir / "a.json"
    atomic_json(report_path, {"commander_slug": "a", "generated_at": observed,
                             "collection": {"freshness": {"oldest_verified_at": observed}}})
    previews_path = args.output_dir / "_collection/previews/a.json.gz"
    save_evidence(previews_path, evidence("a", checked=NOW - timedelta(days=2)))
    source_path = args.output_dir / "_collection/sources/a/base.json"
    atomic_json(source_path, {"checked_at": observed, "cards": {}})
    before = {path: path.read_bytes() for path in (report_path, previews_path, source_path)}

    def no_collection(*_, **__):
        pytest.fail("Fresh source observations must not be downloaded again")

    result = execute(args, rows("a"), no_collection, saved_analyzer([]))
    assert result["capacity"]["remaining_seconds"] is None
    assert result["capacity"]["deadline_at"] is None
    assert result["deadline_target_met"] is None
    assert result["capacity"]["status"] == "complete"
    assert checkpoint(args)["commanders"]["a"] == record
    assert all(path.read_bytes() == contents for path, contents in before.items())


def test_no_completion_deadline_preserves_source_cooldown_and_commander_retry(tmp_path):
    args = options(tmp_path, target_completion_hours=None)
    blocked_until = (NOW + timedelta(hours=1)).isoformat()
    record = {"status": "blocked", "retry_after": blocked_until, "current_previews": 3}
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1,
        "blocked_until": blocked_until, "commanders": {"a": record},
        "pipeline": {"version": 1, "cycle_started_at": (NOW - timedelta(days=5)).isoformat()}})
    result = execute(args, rows("a"), lambda *_: pytest.fail("Collection must respect source cooldown"), saved_analyzer([]))
    assert result["blocked"] is True
    assert result["capacity"]["deadline_at"] is None
    assert result["capacity"]["cooldown_seconds"] == 3600
    assert checkpoint(args)["blocked_until"] == blocked_until
    assert checkpoint(args)["commanders"]["a"] == record


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
