"""Offline integration checks for durable full-catalog coordination."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import json
import multiprocessing
from pathlib import Path
import threading
import time

import httpx
import pytest

from edhrec_affinity.build_styles.pipeline import (
    COMMANDER_IDENTITY_VERSION, atomic_json, collection_options, evidence_version, model_policy, run_pipeline,
    save_evidence,
)
from edhrec_affinity.build_styles.analysis import tag_label_policy
from edhrec_affinity.build_styles.sources import BudgetExpired, SourceBlocked
from edhrec_affinity.build_styles.eligibility import CommanderBelowMinimum


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def options(tmp_path, **changes):
    defaults = dict(output_dir=tmp_path / "build-styles", snapshot_date="2026-10-21", commanders=None,
        min_total_decks=200, refresh_after_days=3, refresh=False, max_commanders=0, max_runtime_seconds=30,
        requests_per_second=40, workers=1, analysis_workers=1, preview_workers=1, max_preview_seconds=10,
        card_metadata_cache=tmp_path / "cards.json", fetch=False, sample_decks=None, seed=42,
        max_components=None, max_tags=12, min_tag_decks=5, main_tag_decks=15, request_delay=1, source_max_age_hours=72,
        index_max_age_hours=24, target_completion_hours=96, dry_run=False, min_free_disk_gb=0,
        prune_completed_cache=False)
    return argparse.Namespace(**(defaults | changes))


def rows(*slugs):
    return [{"commander_slug": slug, "total_decks": 200} for slug in slugs]


def evidence(slug, *, frame=5, loaded=5, attempted=None, new_attempts=None, complete=True,
             mode="all", dataset="stable-dataset", checked=NOW):
    attempted = loaded if attempted is None else attempted
    return {"slug": slug, "card": {"name": slug},
        "base": {"commander_names": [slug], "commander_name_groups": [[slug]], "deck_count": 200},
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
            "generated_at": NOW.isoformat(), "total_edhrec_decks": source["base"]["deck_count"],
            "collection": {**source["collection"], "provenance": source.get("provenance", [])},
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


@pytest.mark.parametrize("count", [1, 5, 6])
def test_two_workers_claim_opposite_ends_and_meet_without_duplicates(tmp_path, count):
    args = options(tmp_path, workers=2, analysis_workers=2)
    inventory = rows(*[f"commander-{index:02}" for index in range(count)])
    barrier = threading.Barrier(min(2, count))
    active, calls, limiters = set(), [], []
    lock = threading.Lock()

    def collect(settings, **dependencies):
        slug = settings.commander
        with lock:
            assert slug not in active
            active.add(slug)
            calls.append(slug)
            limiters.append(dependencies["limiter"])
            first_claim = len(calls) <= min(2, count)
        if first_claim:
            barrier.wait(timeout=5)
        with lock:
            active.remove(slug)
        return evidence(slug)

    result = execute(args, inventory, collect, saved_analyzer([]))
    assignments = result["worker_assignments"]
    assert assignments[0] == {"worker": 0, "direction": "top", "commander_slug": "commander-00"}
    if count > 1:
        assert assignments[1] == {"worker": 1, "direction": "bottom",
                                  "commander_slug": f"commander-{count - 1:02}"}
    assert len(calls) == len(set(calls)) == count
    assert set(result["completed"]) == {row["commander_slug"] for row in inventory}
    assert len({id(limiter) for limiter in limiters}) == 1
    assert result["collection_strategy"] == "opposite_ends"
    assert checkpoint(args)["pipeline"]["collection_workers"] == {}


def test_opposite_workers_complete_their_partial_frame_and_analysis_before_moving_on(tmp_path):
    args = options(tmp_path, workers=2, analysis_workers=2)
    visits, worker_current = {}, {}
    lock = threading.Lock()

    def collect(settings, **_):
        slug, worker = settings.commander, settings.collection_worker
        with lock:
            previous = worker_current.get(worker)
            if previous and previous != slug:
                # Publication, rather than merely a completed preview visit,
                # releases the claim for this worker's next commander.
                assert (args.output_dir / f"{previous}.json").exists()
            worker_current[worker] = slug
            visits[slug] = visits.get(slug, 0) + 1
            loaded = min(200, 50 * visits[slug])
        return evidence(slug, frame=200, loaded=loaded, new_attempts=50,
                        complete=loaded == 200, dataset=f"{slug}-{loaded}")

    result = execute(args, rows("a", "b", "c", "d", "e"), collect, saved_analyzer([]))
    assert set(result["completed"]) == {"a", "b", "c", "d", "e"}
    assert visits == {slug: 4 for slug in visits}
    assert len(result["worker_assignments"]) == 5


def test_opposite_worker_claims_resume_saved_partial_decks_after_a_run_budget(tmp_path):
    args = options(tmp_path, workers=2, analysis_workers=2, max_runtime_seconds=2)
    barrier, lock = threading.Barrier(2), threading.Lock()
    ticks = [0]

    def partial(settings, **_):
        settings.collection_state.parent.mkdir(parents=True, exist_ok=True)
        settings.collection_state.write_bytes(settings.commander.encode())
        barrier.wait(timeout=5)
        with lock:
            ticks[0] += 1
        return evidence(settings.commander, frame=200, loaded=50, new_attempts=50, complete=False)

    first = execute(args, rows("a", "b", "c", "z"), partial, saved_analyzer([]),
                    monotonic=lambda: ticks[0])
    assert first["time_budget_reached"]
    assert checkpoint(args)["pipeline"]["collection_workers"] == {"0": "a", "1": "z"}
    calls = []

    def resumed(settings, **_):
        calls.append((settings.collection_worker, settings.commander))
        if settings.commander in {"a", "z"}:
            assert settings.collection_state.read_bytes() == settings.commander.encode()
        return evidence(settings.commander, frame=200, loaded=200, new_attempts=150)

    second = execute(options(tmp_path, workers=2, analysis_workers=2), rows("a", "b", "c", "z"),
                     resumed, saved_analyzer([]))
    assert set(calls[:2]) == {(0, "a"), (1, "z")}
    assert set(second["completed"]) == {"a", "b", "c", "z"}
    assert checkpoint(args)["pipeline"]["collection_workers"] == {}


def test_bottom_worker_uses_the_unfinished_priority_phase_before_completed_refreshes(tmp_path):
    args = options(tmp_path, workers=2, analysis_workers=2)
    old = NOW - timedelta(days=2)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": {
        "z-refresh": {"status": "fitted", "coverage_complete": True, "frame_decks": 200,
                      "tag_label_policy": tag_label_policy(), "model_policy": model_policy(args),
                      "current_previews": 200, "verified_at": old.isoformat()}}})
    atomic_json(args.output_dir / "z-refresh.json", {"commander_slug": "z-refresh"})
    result = execute(args, rows("a", "b", "c", "z-refresh"),
                     lambda settings, **_: evidence(settings.commander), saved_analyzer([]))
    assert [assignment["commander_slug"] for assignment in result["worker_assignments"][:2]] == ["a", "c"]


def test_saved_claims_outside_a_scoped_batch_cannot_reserve_all_worker_slots(tmp_path):
    args = options(tmp_path, workers=2, max_commanders=1)
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1,
        "pipeline": {"collection_workers": {"0": "y", "1": "z"}}, "commanders": {
            "y": {"status": "collecting"}, "z": {"status": "collecting"}}})
    calls = []

    def collect(settings, **_):
        calls.append(settings.commander)
        return evidence(settings.commander)

    result = execute(args, rows("a", "y", "z"), collect, saved_analyzer([]))
    assert result["completed"] == calls == ["a"]


def test_rate_block_from_one_end_stops_both_and_preserves_claims_for_cooldown(tmp_path):
    args = options(tmp_path, workers=2)
    barrier, blocked_event = threading.Barrier(2), threading.Event()
    calls = []

    def blocked(settings, **dependencies):
        calls.append(settings.commander)
        limiter = dependencies["limiter"]
        barrier.wait(timeout=5)
        if settings.collection_worker == 0:
            limiter.record_throttle(now=NOW)
            limiter.block("HTTP 429 rate limit", kind="rate_limit")
            blocked_event.set()
        else:
            assert blocked_event.wait(timeout=5)
        limiter.wait()
        pytest.fail("A blocked shared limiter cannot issue another source request")

    first = execute(args, rows("a", "b", "c", "z"), blocked, saved_analyzer([]))
    assert set(calls) == {"a", "z"}
    assert first["blocked"] and not first["continuation_needed"]
    assert first["source_rate"]["throttles"] == 1
    assert checkpoint(args)["pipeline"]["collection_workers"] == {"0": "a", "1": "z"}
    execute(args, rows("a", "b", "c", "z"),
            lambda *_, **__: pytest.fail("The shared cooldown must stop both workers"),
            saved_analyzer([]), checked=NOW + timedelta(minutes=30))
    assert checkpoint(args)["pipeline"]["collection_workers"] == {"0": "a", "1": "z"}


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
    assert second["completed"] == []
    assert second["deferred_analyses"] == ["partial"]
    assert "a-new" not in calls
    assert checkpoint(args)["commanders"]["partial"]["coverage_complete"] is True
    ticks[0] = 0
    third = execute(args, rows("partial"), lambda *_: pytest.fail("Saved evidence should resume without collection"),
                    saved_analyzer(analyzed), monotonic=lambda: ticks[0])
    assert third["completed"] == ["partial"]


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
                      "model_policy": model_policy(args),
                      "current_previews": 200, "verified_at": old.isoformat()},
    }})
    atomic_json(args.output_dir / "a-refresh.json", {"commander_slug": "a-refresh"})
    ticks, progress, calls, analyzed = [0], {}, [], []
    result = execute(args, rows("a-refresh", "new"), chunked_collector(ticks, progress, calls),
                     saved_analyzer(analyzed), monotonic=lambda: ticks[0])
    assert result["completed"] == []
    assert result["deferred_analyses"] == ["new"]
    assert "a-refresh" not in calls


@pytest.mark.parametrize("workers", [1, 2])
def test_pipeline_collects_only_commanders_with_at_least_200_total_decks(tmp_path, workers):
    args, collected = options(tmp_path, workers=workers), []
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
    assert checkpoint(args)["commanders"]["a"]["analysis_version"] == evidence_version(
        source, collection_options(args, "a", args.max_preview_seconds))


def seed_pending_inputs(args, slugs):
    records = {}
    for slug in slugs:
        source = evidence(slug, new_attempts=0)
        source["analysis_version"] = evidence_version(source)
        save_evidence(args.output_dir / f"_collection/evidence/{slug}.json.gz", source)
        records[slug] = {"status": "analysis_pending", "coverage_complete": True, "frame_decks": 5,
                         "current_previews": 5, "verified_at": NOW.isoformat()}
    atomic_json(args.output_dir / "_collection/state.json", {"schema_version": 1, "commanders": records})


def test_pending_model_backlog_stops_at_budget_and_resumes_without_collection(tmp_path):
    args = options(tmp_path, max_runtime_seconds=3)
    slugs = [f"queued-{index:02}" for index in range(12)]
    seed_pending_inputs(args, slugs)
    ticks, calls = [0], []

    def analyze(path, settings):
        ticks[0] += 1
        return saved_analyzer(calls)(path, settings)

    def no_collection(*_, **__):
        pytest.fail("Pending analysis must reuse saved inputs")

    first = execute(args, rows(*slugs), no_collection, analyze, monotonic=lambda: ticks[0])
    assert calls == slugs[:3]
    assert first["completed"] == slugs[:3]
    assert first["deferred_analyses"] == slugs[3:]
    assert first["time_budget_reached"] and first["continuation_needed"]
    assert first["failures"] == [] and not first["blocked"]
    for slug in slugs[3:]:
        assert (args.output_dir / f"_collection/evidence/{slug}.json.gz").exists()
        assert "retry_after" not in checkpoint(args)["commanders"][slug]
    args.max_runtime_seconds = 30
    second = execute(args, rows(*slugs), no_collection, saved_analyzer(calls))
    assert second["completed"] == slugs[3:]
    assert second["deferred_analyses"] == []


def slow_and_fast_process_analyzer(path, settings):
    """Spawnable test worker, with one deliberately stuck CPU process."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        source = json.load(handle)
    if source["slug"] in {"b-slow", "c-finished-receipt"}:
        marker = Path(settings["output_dir"]) / "worker-started"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(multiprocessing.current_process().pid))
        if source["slug"] == "c-finished-receipt":
            result = saved_analyzer([])(path, settings)
            atomic_json(Path(settings["output_dir"]) / "result.json", result)
        time.sleep(60)
    return saved_analyzer([])(path, settings)


def test_hung_process_is_reaped_after_grace_while_finished_peer_is_published(tmp_path):
    args = options(tmp_path, max_runtime_seconds=4, worker_grace_seconds=.15, analysis_workers=3)
    seed_pending_inputs(args, ["a-fast", "b-slow", "c-finished-receipt"])
    before_children = {process.pid for process in multiprocessing.active_children()}
    started = time.monotonic()
    first = run_pipeline(args, rows("a-fast", "b-slow", "c-finished-receipt"),
        collector=lambda *_: pytest.fail("Pending evidence must not be recollected"),
        analyzer=slow_and_fast_process_analyzer, now=lambda: NOW)
    assert time.monotonic() - started < 10
    assert (args.output_dir / "_collection/analyzed/b-slow/worker-started").exists()
    assert first["completed"] == ["a-fast", "c-finished-receipt"]
    assert first["deferred_analyses"] == ["b-slow"]
    assert first["analysis_workers_stopped"] is True
    assert first["failures"] == [] and not first["blocked"]
    assert (args.output_dir / "a-fast.json").exists()
    assert (args.output_dir / "c-finished-receipt.json").exists()
    assert not (args.output_dir / "b-slow.json").exists()
    assert (args.output_dir / "_collection/evidence/b-slow.json.gz").exists()
    assert "retry_after" not in checkpoint(args)["commanders"]["b-slow"]
    assert {process.pid for process in multiprocessing.active_children()} <= before_children
    second = execute(args, rows("b-slow"), lambda *_: pytest.fail("Resume must use saved input"), saved_analyzer([]))
    assert second["completed"] == ["b-slow"]


def test_collection_budget_expiration_is_neither_failure_nor_source_cooldown(tmp_path):
    args = options(tmp_path)

    def expired(*_, **__):
        raise BudgetExpired("run budget")

    result = execute(args, rows("a"), expired, saved_analyzer([]))
    assert result["time_budget_reached"]
    assert result["failures"] == [] and not result["blocked"]
    record = checkpoint(args)["commanders"]["a"]
    assert record["status"] == "collecting"
    assert "retry_after" not in record
    assert "blocked_until" not in checkpoint(args)


def test_empty_partial_collection_at_budget_is_resumable_without_backoff(tmp_path):
    args = options(tmp_path, max_runtime_seconds=1)
    ticks = [0]

    def expired_partial(*_, **__):
        ticks[0] = 1
        return evidence("a", loaded=0, new_attempts=0, complete=False)

    result = execute(args, rows("a"), expired_partial, saved_analyzer([]), monotonic=lambda: ticks[0])
    assert result["time_budget_reached"]
    assert result["failures"] == [] and not result["blocked"]
    record = checkpoint(args)["commanders"]["a"]
    assert record["status"] == "collecting"
    assert "retry_after" not in record


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


def test_uncapped_upgrade_reuses_saved_sample_and_preserves_source_times(tmp_path):
    args = options(tmp_path, max_components=8, fetch=True)
    analyzed = []
    source_date = NOW - timedelta(hours=4)
    source = evidence("existing", checked=source_date)
    source["provenance"] = [{"url": "https://json.edhrec.com/pages/commanders/existing.json",
                             "fetched_at": source_date.isoformat(), "sha256": "original-source"}]
    execute(args, rows("existing"), lambda *_, **__: source, saved_analyzer(analyzed))
    before = checkpoint(args)["commanders"]["existing"].copy()
    preview = args.output_dir / "_collection/previews/existing.json.gz"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"retained parsed deck checkpoint")
    args.max_components = None
    args.max_commanders = 1
    calls = []

    def cached_inputs(settings, **_):
        calls.append(settings.commander)
        assert settings.fetch is False
        assert settings.source_max_age_hours is settings.index_max_age_hours is None
        assert settings.collection_state.read_bytes() == b"retained parsed deck checkpoint"
        return {**source, "collection": {**source["collection"], "new_previews_attempted": 0}}

    result = execute(args, rows("untouched", "existing"), cached_inputs, saved_analyzer(analyzed),
                     checked=NOW + timedelta(hours=1))
    record = checkpoint(args)["commanders"]["existing"]
    assert calls == result["reanalyzed_from_cache"] == result["completed"] == ["existing"]
    assert result["collected"] == [] and result["new_previews_attempted"] == 0
    assert analyzed == ["existing", "existing"]
    assert record["analysis_version"] != before["analysis_version"]
    assert record["model_policy"] == model_policy(args)
    assert record["verified_at"] == before["verified_at"]
    assert record["last_collection_at"] == before["last_collection_at"]
    assert record["collection_dataset_sha256"] == before["collection_dataset_sha256"]
    saved = json.loads((args.output_dir / "existing.json").read_text())
    assert saved["collection"]["dataset_sha256"] == "stable-dataset"
    assert saved["collection"]["freshness"]["oldest_verified_at"] == source_date.isoformat()
    assert saved["collection"]["provenance"] == source["provenance"]
    assert preview.read_bytes() == b"retained parsed deck checkpoint"


def seed_legacy_commander_identity(args, slug, card):
    """A complete source sample whose old analysis mistook DFC faces for partners."""
    observed = NOW - timedelta(hours=4)
    source = evidence(slug, checked=observed, new_attempts=0)
    source["card"] = card
    source["base"]["commander_names"] = card["names"]
    source["base"]["commander_name_groups"] = [card["names"]]
    source["provenance"] = [{"url": f"https://json.edhrec.com/pages/commanders/{slug}.json",
                             "fetched_at": observed.isoformat(), "sha256": "original-source"}]
    state_path = args.output_dir / "_collection/state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"schema_version": 1, "commanders": {}}
    state["commanders"][slug] = {
        "status": "insufficient_data", "coverage_complete": True, "frame_decks": 5,
        "current_previews": 5, "verified_at": observed.isoformat(), "analyzed_at": observed.isoformat(),
        "last_collection_at": observed.isoformat(), "collection_dataset_sha256": "stable-dataset",
        "analysis_version": "legacy-identity-model", "tag_label_policy": tag_label_policy(),
        "model_policy": model_policy(args),
    }
    atomic_json(state_path, state)
    atomic_json(args.output_dir / f"{slug}.json", {
        "schema_version": 1, "commander_slug": slug, "commander_name": card["name"],
        "generated_at": observed.isoformat(), "total_edhrec_decks": 200,
        "collection": {**source["collection"], "analysis_dataset_sha256": "stable-dataset",
                       "provenance": source["provenance"]},
        "tag_label_policy": tag_label_policy(),
        "deck_analysis": {"status": "insufficient_data", "unique_decks": 0, "packages": []},
    })
    atomic_json(args.output_dir / f"_collection/sources/{slug}/base.json", {
        "container": {"json_dict": {"card": card}}})
    preview = args.output_dir / f"_collection/previews/{slug}.json.gz"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"retained parsed deck checkpoint")
    return source


def dfc_identity_card():
    return {"name": "Plargg, Dean of Chaos", "names": ["Plargg, Dean of Chaos", "Augusta, Dean of Order"],
            "layout": "modal_dfc"}


def corrected_identity_evidence(source):
    front = source["card"]["name"]
    return {**source, "base": {**source["base"], "commander_names": [front],
                               "commander_name_groups": [[front], [" // ".join(source["card"]["names"])]]}}


def test_identity_upgrade_repairs_only_split_faces_from_cache_once_without_changing_source_dates(tmp_path):
    args = options(tmp_path, fetch=True)
    source = seed_legacy_commander_identity(args, "dfc", dfc_identity_card())
    seed_legacy_commander_identity(args, "ordinary", {"name": "Ordinary Commander", "names": ["Ordinary Commander"]})
    seed_legacy_commander_identity(args, "partners", {
        "name": "First Commander // Second Commander", "names": ["First Commander", "Second Commander"],
        "layout": "normal", "cards": [{"name": "First Commander"}, {"name": "Second Commander"}]})
    previous = checkpoint(args)
    calls, analyzed = [], []

    def cached_inputs(settings, **dependencies):
        calls.append(settings.commander)
        assert settings.commander == "dfc"
        assert settings.fetch is False
        assert settings.source_max_age_hours is settings.index_max_age_hours is None
        assert settings.collection_state.read_bytes() == b"retained parsed deck checkpoint"
        assert dependencies["limiter"].stats["requests_started"] == 0
        return corrected_identity_evidence(source)

    result = execute(args, rows("dfc", "ordinary", "partners"), cached_inputs, saved_analyzer(analyzed))
    record = checkpoint(args)["commanders"]["dfc"]
    assert calls == analyzed == result["reanalyzed_from_cache"] == result["completed"] == ["dfc"]
    assert result["collected"] == [] and result["new_previews_attempted"] == 0
    assert result["measured_request_rate"]["requests_started"] == 0
    assert record["commander_identity_version"] == COMMANDER_IDENTITY_VERSION
    assert record["model_policy"] == previous["commanders"]["dfc"]["model_policy"] == model_policy(args)
    assert model_policy(args)["version"] == 8
    for key in ("verified_at", "last_collection_at", "collection_dataset_sha256"):
        assert record[key] == previous["commanders"]["dfc"][key]
    for slug in ("ordinary", "partners"):
        assert checkpoint(args)["commanders"][slug] == previous["commanders"][slug]
    saved = json.loads((args.output_dir / "dfc.json").read_text())
    assert saved["deck_analysis"]["status"] == "fitted"
    assert saved["collection"]["freshness"] == source["collection"]["freshness"]
    assert saved["collection"]["provenance"] == source["provenance"]
    second = execute(args, rows("dfc", "ordinary", "partners"),
                     lambda *_, **__: pytest.fail("The repaired identity must not reanalyze on every run"),
                     saved_analyzer(analyzed))
    assert second["selected"] == second["completed"] == []
    assert analyzed == ["dfc"]


def test_identity_repair_marks_an_already_normalized_unchanged_model_without_refitting(tmp_path):
    args = options(tmp_path, fetch=True)
    source = corrected_identity_evidence(seed_legacy_commander_identity(args, "dfc", dfc_identity_card()))
    state = checkpoint(args)
    version = evidence_version(source, collection_options(args, "dfc", 30))
    state["commanders"]["dfc"]["analysis_version"] = version
    atomic_json(args.output_dir / "_collection/state.json", state)
    calls = []

    def cached_inputs(settings, **_):
        calls.append(settings.fetch)
        return source

    result = execute(args, rows("dfc"), cached_inputs,
                     lambda *_, **__: pytest.fail("An unchanged normalized model must reuse its saved result"))
    assert calls == [False]
    assert result["reanalyzed_from_cache"] == result["completed"] == ["dfc"]
    assert result["new_previews_attempted"] == 0
    assert checkpoint(args)["commanders"]["dfc"]["commander_identity_version"] == COMMANDER_IDENTITY_VERSION
    assert checkpoint(args)["commanders"]["dfc"]["analysis_version"] == version


def test_short_identity_repair_preserves_both_sticky_frames_and_their_parsed_decks(tmp_path):
    args = options(tmp_path, fetch=True, workers=2, analysis_workers=2, max_runtime_seconds=2)
    source = seed_legacy_commander_identity(args, "m-dfc", dfc_identity_card())
    state = checkpoint(args)
    state["pipeline"] = {"collection_workers": {"0": "a-partial", "1": "z-partial"}}
    for slug in ("a-partial", "z-partial"):
        state["commanders"][slug] = {"status": "collecting", "coverage_complete": False,
                                      "frame_decks": 200, "current_previews": 50}
        path = args.output_dir / f"_collection/previews/{slug}.json.gz"
        path.write_bytes(f"retained {slug} decks".encode())
    atomic_json(args.output_dir / "_collection/state.json", state)
    calls, analyzed, ticks = [], [], [0]
    lock, partials = threading.Lock(), threading.Barrier(2)

    def collect(settings, **_):
        slug = settings.commander
        with lock:
            calls.append((settings.collection_worker, slug))
        if slug == "m-dfc":
            assert settings.fetch is False
            assert checkpoint(args)["pipeline"]["collection_workers"] == {"0": "a-partial", "1": "z-partial"}
            return corrected_identity_evidence(source)
        assert settings.collection_state.read_bytes() == f"retained {slug} decks".encode()
        partials.wait(timeout=5)
        with lock:
            ticks[0] += 1
        return evidence(slug, frame=200, loaded=100, new_attempts=50, complete=False)

    inventory = rows("a-partial", "m-dfc", "z-partial")
    result = execute(args, inventory, collect, saved_analyzer(analyzed), monotonic=lambda: ticks[0])
    assert calls[0] == (0, "m-dfc")
    assert set(calls[1:]) == {(0, "a-partial"), (1, "z-partial")}
    assert result["reanalyzed_from_cache"] == analyzed == ["m-dfc"]
    assert checkpoint(args)["pipeline"]["collection_workers"] == {"0": "a-partial", "1": "z-partial"}
    resumed = []

    def finish(settings, **_):
        resumed.append((settings.collection_worker, settings.commander))
        assert settings.collection_state.read_bytes() == f"retained {settings.commander} decks".encode()
        return evidence(settings.commander, frame=200, loaded=200, new_attempts=100)

    second = execute(options(tmp_path, fetch=True, workers=2, analysis_workers=2), inventory,
                     finish, saved_analyzer([]))
    assert set(resumed) == {(0, "a-partial"), (1, "z-partial")}
    assert set(second["completed"]) == {"a-partial", "z-partial"}
    assert checkpoint(args)["pipeline"]["collection_workers"] == {}


@pytest.mark.parametrize("failure", ["missing", "different_dataset"])
def test_identity_upgrade_preserves_report_when_complete_saved_inputs_cannot_be_reproduced(tmp_path, failure):
    args = options(tmp_path, fetch=True)
    source = seed_legacy_commander_identity(args, "dfc", dfc_identity_card())
    path = args.output_dir / "dfc.json"
    previous = path.read_bytes()
    calls = []

    def unusable_cached_inputs(settings, **_):
        calls.append(settings.fetch)
        assert settings.fetch is False
        if failure == "missing":
            raise FileNotFoundError("No retained parsed preview inputs")
        result = corrected_identity_evidence(source)
        result["collection"] = {**result["collection"], "dataset_sha256": "different-dataset"}
        return result

    result = execute(args, rows("dfc"), unusable_cached_inputs, saved_analyzer([]))
    assert calls == [False]
    assert result["completed"] == [] and len(result["model_upgrades_deferred"]) == 1
    assert path.read_bytes() == previous
    assert checkpoint(args)["commanders"]["dfc"].get("commander_identity_version") != COMMANDER_IDENTITY_VERSION
    assert checkpoint(args)["commanders"]["dfc"]["model_upgrade_needs_collection"] is True


def test_pending_split_face_evidence_is_normalized_before_its_analysis_fingerprint(tmp_path):
    args = options(tmp_path, fetch=True)
    source = seed_legacy_commander_identity(args, "dfc", dfc_identity_card())
    source["analysis_version"] = "old-split-face-fingerprint"
    path = args.output_dir / "_collection/evidence/dfc.json.gz"
    save_evidence(path, source)
    state = checkpoint(args)
    state["commanders"]["dfc"]["status"] = "analysis_pending"
    atomic_json(args.output_dir / "_collection/state.json", state)
    observed, analyzed = [], []

    def inspect_saved_input(path, settings):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            pending = json.load(handle)
        assert pending["base"]["commander_names"] == ["Plargg, Dean of Chaos"]
        assert ["Plargg, Dean of Chaos"] in pending["base"]["commander_name_groups"]
        assert all(len(group) == 1 for group in pending["base"]["commander_name_groups"])
        assert pending["analysis_version"] == evidence_version(pending, argparse.Namespace(**settings))
        assert pending["collection"] == source["collection"]
        observed.append(pending)
        return saved_analyzer(analyzed)(path, settings)

    result = execute(args, rows("dfc"),
                     lambda *_, **__: pytest.fail("Durable model evidence must not trigger HTTP collection"),
                     inspect_saved_input)
    assert result["completed"] == analyzed == ["dfc"]
    assert len(observed) == 1
    assert checkpoint(args)["commanders"]["dfc"]["analysis_version"] == observed[0]["analysis_version"]
    assert checkpoint(args)["commanders"]["dfc"]["commander_identity_version"] == COMMANDER_IDENTITY_VERSION


@pytest.mark.parametrize("failure", ["missing", "different_dataset", "partial", "missing_ranking", "missing_metadata"])
def test_missing_upgrade_inputs_keep_report_and_retry_source_queue_in_later_run(tmp_path, failure):
    args = options(tmp_path, max_components=8, fetch=True)
    analyzed = []
    execute(args, rows("a"), lambda *_, **__: evidence("a"), saved_analyzer(analyzed))
    report_path = args.output_dir / "a.json"
    published = report_path.read_bytes()
    before = checkpoint(args)["commanders"]["a"].copy()
    args.max_components = None
    calls = []

    def unusable_cached_inputs(settings, **_):
        calls.append(settings.fetch)
        assert settings.fetch is False
        if failure == "missing":
            raise FileNotFoundError("No retained base profile")
        result = evidence("a", new_attempts=0,
                          dataset="changed-dataset" if failure == "different_dataset" else "stable-dataset",
                          complete=failure != "partial")
        if failure == "missing_ranking":
            result["failures"] = [{"ranking_bracket": 3, "error": "Not cached"}]
        if failure == "missing_metadata":
            result["metadata_summary"] = {"missing": 1}
        return result

    first = execute(args, rows("a"), unusable_cached_inputs, saved_analyzer(analyzed))
    assert calls == [False]  # No fallback request or repeated visit in this batch.
    assert first["completed"] == [] and len(first["model_upgrades_deferred"]) == 1
    assert first["continuation_needed"] is True
    assert report_path.read_bytes() == published
    pending = checkpoint(args)["commanders"]["a"]
    assert pending["model_upgrade_needs_collection"] is True
    assert pending["verified_at"] == before["verified_at"]
    assert pending["last_collection_at"] == before["last_collection_at"]

    def next_source_visit(settings, **_):
        calls.append(settings.fetch)
        assert settings.fetch is True
        assert settings.source_max_age_hours == 72 and settings.index_max_age_hours == 24
        return evidence("a", new_attempts=0)

    second = execute(args, rows("a"), next_source_visit, saved_analyzer(analyzed))
    assert calls == [False, True]
    assert second["completed"] == ["a"]
    assert analyzed == ["a", "a"]
    assert checkpoint(args)["commanders"]["a"]["model_policy"] == model_policy(args)
    assert "model_upgrade_needs_collection" not in checkpoint(args)["commanders"]["a"]


def test_uncapped_options_change_the_input_fingerprint(tmp_path):
    args = options(tmp_path, max_components=8)
    source = evidence("a")
    capped = evidence_version(source, args)
    args.max_components = None
    assert evidence_version(source, args) != capped


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
              "model_policy": model_policy(args),
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
