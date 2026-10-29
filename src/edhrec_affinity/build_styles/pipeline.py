"""Rolling full-catalog collection with source-paced completion estimates."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import shutil
import threading
import time
from zoneinfo import ZoneInfo

import httpx

from .capacity import plan_capacity
from .analysis import tag_label_policy
from .eligibility import CommanderBelowMinimum, commander_is_eligible, valid_deck_count, validate_minimum
from .sources import BudgetExpired, CommanderAliasCatalog, PreviewTransport, RequestLimiter, SourceBlocked, source_time


MODEL_VERSION = 8


def model_policy(options: argparse.Namespace) -> dict:
    """Analysis policy stored separately from observation freshness."""
    return {"version": MODEL_VERSION, "max_components": options.max_components}


class CachedModelUpgradeUnavailable(ValueError):
    """Saved inputs cannot safely reproduce a published model's source sample."""


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def save_evidence(path: Path, evidence: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as handle:
        json.dump(evidence, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    temporary.replace(path)


def evidence_version(evidence: dict, options: argparse.Namespace | None = None) -> str:
    # Observation times change during revalidation even when the input does not.
    # Include actual card rules and ranking measurements, not their check times.
    metadata = {key: {field: value for field, value in card.items() if field not in {"checked_at", "_build_cache"}}
                for key, card in evidence.get("metadata", {}).items()}
    contents = {"deck_dataset_sha256": evidence["collection"].get("dataset_sha256"),
                "base": evidence["base"], "profiles": evidence["ranking_profiles"], "metadata": metadata,
                # Version 8 removes the configured package-count ceiling.
                "model_version": MODEL_VERSION, "tag_label_policy": evidence.get("tag_label_policy"),
                "options": {key: getattr(options, key, None) for key in
                    ("seed", "components", "max_components", "min_deck_cards", "min_card_support", "membership", "similarity",
                     "min_tag_decks", "main_tag_decks", "min_tag_fraction", "main_tag_fraction", "max_tags")}}
    return hashlib.sha256(json.dumps(contents, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def published_collection(evidence: dict) -> dict:
    return {**evidence["collection"], "failures": evidence.get("failures", []),
            "provenance": [{key: value for key, value in item.items() if key != "cache_file"}
                           for item in evidence.get("provenance", [])]}


def refresh_unchanged_report_metadata(report: dict, metadata_summary: dict) -> None:
    """Refresh observations of unchanged rules without inventing a new rating."""
    if not metadata_summary.get("checked_at"):
        return
    analysis = report.get("deck_analysis", {})
    packages = [*analysis.get("packages", []),
                *[package for model in analysis.get("alternative_models", []) for package in model.get("packages", [])]]
    stale_error = "Some card rules use an older cache; refresh metadata before relying on current legality."
    for package in packages:
        generated = package.get("generated_deck", {})
        if generated.get("status") != "generated":
            continue
        validation = generated.setdefault("validation", {})
        validation["metadata"] = {key: metadata_summary.get(key) for key in
                                  ("checked_at", "oldest_checked_at", "stale_cached", "cache_max_age_days")}
        validation.setdefault("checks", {})["metadata_fresh"] = not bool(metadata_summary.get("stale_cached"))
        if validation["checks"]["metadata_fresh"]:
            validation["errors"] = [error for error in validation.get("errors", []) if error != stale_error]
            validation["valid"] = not validation["errors"] and all(validation["checks"].values())
    if report.get("deck_generation"):
        report["deck_generation"]["metadata"] = metadata_summary


def analyze_saved_evidence(path: Path, options: dict) -> dict:
    """Spawn-safe worker: reads a durable input and never fetches source data."""
    from threadpoolctl import threadpool_limits
    from .__main__ import analyze_evidence

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        evidence = json.load(handle)
    with threadpool_limits(limits=1):
        report = analyze_evidence(argparse.Namespace(**options), evidence)
    result = {"slug": report["commander_slug"], "report": str(Path(options["output_dir"]) / "report.json"),
              "status": report["deck_analysis"]["status"], "version": evidence["analysis_version"]}
    # A finished report can precede delivery of its Future result. This receipt
    # lets the parent publish that work even when it must stop the CPU pool.
    atomic_json(Path(options["output_dir"]) / "result.json", result)
    return result


def shutdown_executor(executor, *, terminate: bool = False) -> None:
    """Reap CPU children without letting executor shutdown outlive a run budget.

    Python 3.12 has no public ProcessPoolExecutor.terminate_workers(). Capture
    its children before shutdown clears the mapping, then use the public
    multiprocessing Process lifecycle methods. Ordinary completion still uses
    the executor's normal, waiting shutdown.
    """
    if not terminate:
        executor.shutdown(wait=True)
        return
    processes = list((getattr(executor, "_processes", None) or {}).values())
    for process in processes:
        if process.is_alive():
            process.terminate()
    executor.shutdown(wait=False, cancel_futures=True)
    join_until = time.monotonic() + 2
    for process in processes:
        process.join(timeout=max(0, join_until - time.monotonic()))
    for process in processes:
        if process.is_alive():
            process.kill()
    for process in processes:
        process.join(timeout=1)


def collection_options(args, slug: str, remaining_seconds: float):
    from .__main__ import parser

    options = parser().parse_args(["--commander", slug, "--no-html"])
    options.cache_dir = args.output_dir / "_collection" / "sources" / slug
    options.collection_state = args.output_dir / "_collection" / "previews" / f"{slug}.json.gz"
    options.output_dir = args.output_dir / "_collection" / "analyzed" / slug
    options.card_metadata_cache = args.card_metadata_cache
    options.fetch, options.sample_decks, options.seed = args.fetch, args.sample_decks, args.seed
    options.max_components, options.max_tags, options.min_tag_decks = args.max_components, args.max_tags, args.min_tag_decks
    options.min_tag_fraction = getattr(args, "min_tag_fraction", 0.01)
    options.main_tag_fraction = getattr(args, "main_tag_fraction", 0.03)
    options.main_tag_decks = tag_label_policy(options.min_tag_decks, getattr(args, "main_tag_decks", None),
                                             options.max_tags, min_tag_fraction=options.min_tag_fraction,
                                             main_tag_fraction=options.main_tag_fraction)["main_minimum_decks"]
    options.min_total_decks = validate_minimum(args.min_total_decks)
    options.request_delay, options.preview_workers = args.request_delay, args.preview_workers
    options.source_max_age_hours, options.index_max_age_hours = args.source_max_age_hours, args.index_max_age_hours
    options.target_completion_hours = args.target_completion_hours
    options.freshness_target_hours = getattr(args, "freshness_target_hours", 96)
    options.trust_index_versions, options.defer_partial_analysis = True, True
    options.max_preview_seconds = min(args.max_preview_seconds, max(1, remaining_seconds))
    return options


def run_pipeline(args, rows: list[dict], *, collector=None, analyzer=None, monotonic=time.monotonic,
                 analysis_executor_factory=None, now=None) -> dict:
    from .__main__ import collect_evidence
    from .card_metadata import MetadataRepository

    now = now or (lambda: datetime.now(timezone.utc))
    current_model_policy = model_policy(args)
    started, observed_at = monotonic(), now()
    root = args.output_dir / "_collection"
    root.mkdir(parents=True, exist_ok=True)
    state_path, summary_path = root / "state.json", args.output_dir.parent / "build_style_batch.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"schema_version": 1, "commanders": {}}
    if state.get("schema_version") != 1 or not isinstance(state.get("commanders"), dict):
        raise ValueError("Invalid collection checkpoint")
    source_rate = state.get("source_rate", {})
    if not isinstance(source_rate, dict):
        raise ValueError("Invalid source rate checkpoint")
    learned_rate = source_rate.get("learned_rate")
    if learned_rate is not None:
        if (isinstance(learned_rate, bool) or not isinstance(learned_rate, (float, int))
                or not math.isfinite(learned_rate) or learned_rate <= 0):
            raise ValueError("Learned source rate must be finite and positive")
    saved_observed_rate = source_rate.get("observed_requests_per_second")
    if saved_observed_rate is not None:
        if (isinstance(saved_observed_rate, bool) or not isinstance(saved_observed_rate, (float, int))
                or not math.isfinite(saved_observed_rate) or saved_observed_rate <= 0):
            raise ValueError("Observed source rate must be finite and positive")
    prior_throttles = source_rate.get("throttles", 0)
    if isinstance(prior_throttles, bool) or not isinstance(prior_throttles, int) or prior_throttles < 0:
        raise ValueError("Invalid source throttle count")
    requested = set(args.commanders or [])
    known = {row["commander_slug"] for row in rows}
    if requested - known:
        raise ValueError("Requested commanders not found in catalog")
    minimum = validate_minimum(args.min_total_decks)
    label_policy = tag_label_policy(args.min_tag_decks, getattr(args, "main_tag_decks", None), args.max_tags,
                                   min_tag_fraction=getattr(args, "min_tag_fraction", 0.01),
                                   main_tag_fraction=getattr(args, "main_tag_fraction", 0.03))
    if any(row["commander_slug"] in requested and not commander_is_eligible(row.get("total_decks"), minimum)
           for row in rows):
        raise ValueError(f"Requested commanders must each have at least {minimum} total decks")
    rows = [row for row in rows if (not requested or row["commander_slug"] in requested)
            and commander_is_eligible(row.get("total_decks"), minimum)]
    rows_by_slug = {row["commander_slug"]: row for row in rows}
    cycle = state.setdefault("pipeline", {"version": 1, "cycle_started_at": observed_at.isoformat()})
    cycle_start = source_time(cycle.get("cycle_started_at")) or observed_at
    upstream_date = getattr(args, "upstream_snapshot_date", None)
    if upstream_date and cycle.get("upstream_snapshot_date") not in {None, upstream_date}:
        cycle_start = datetime.strptime(upstream_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/Toronto")).astimezone(timezone.utc)
        cycle["cycle_started_at"] = cycle_start.isoformat()
    if upstream_date:
        cycle["upstream_snapshot_date"] = upstream_date
    cycle_complete = all(state["commanders"].get(slug, {}).get("status") in {"fitted", "insufficient_data"}
                         and state["commanders"][slug].get("tag_label_policy") == label_policy
                         and state["commanders"][slug].get("model_policy") == current_model_policy
                         and state["commanders"][slug].get("coverage_complete")
                         and not state["commanders"][slug].get("unavailable_previews")
                         and (verified := source_time(state["commanders"][slug].get("verified_at"))) is not None
                         and -300 <= (observed_at - verified).total_seconds() < getattr(args, "freshness_target_hours", 96) * 3600
                         and (args.output_dir / f"{slug}.json").exists() for slug in rows_by_slug)
    if cycle_complete and (observed_at - cycle_start).total_seconds() >= args.refresh_after_days * 86400:
        cycle_start = observed_at
        cycle["cycle_started_at"] = observed_at.isoformat()
    summary = {"snapshot_date": args.snapshot_date, "pipeline_version": 1, "catalog_size": len(rows),
               "cycle_started_at": cycle_start.isoformat(), "target_completion_hours": args.target_completion_hours,
               "freshness_target_hours": getattr(args, "freshness_target_hours", 96),
               "scope": {"commanders": args.commanders or [], "max_commanders": args.max_commanders,
                         "sample_decks": args.sample_decks, "refresh": args.refresh},
               "automatic_continuation_allowed": (not args.commanders and not args.max_commanders
                                                   and args.sample_decks is None and not args.refresh),
               "min_total_decks": minimum, "tag_label_policy": label_policy,
               "model_policy": current_model_policy, "model_upgrades_deferred": [],
               "reanalyzed_from_cache": [], "skipped": [],
               "completed": [], "collected": [], "failures": [], "blocked": False,
               "dry_run": args.dry_run, "publish_needed": False, "continuation_needed": False,
               "no_progress": True, "time_budget_reached": False, "new_previews_attempted": 0,
               "time_budget_seconds": args.max_runtime_seconds, "max_preview_attempts": None,
               "worker_grace_seconds": getattr(args, "worker_grace_seconds", 120),
               "deferred_analyses": [], "analysis_workers_stopped": False,
               "request_ceiling_per_second": args.requests_per_second}

    def eligible(slug):
        record = state["commanders"].get(slug, {})
        retry = source_time(record.get("retry_after"))
        if retry and retry > now() and not args.refresh:
            return False
        verified = source_time(record.get("verified_at"))
        return (args.refresh or record.get("status") not in {"fitted", "insufficient_data"}
                or record.get("tag_label_policy") != label_policy
                or record.get("model_policy") != current_model_policy
                or not record.get("coverage_complete") or verified is None
                or not (args.output_dir / f"{slug}.json").exists()
                or (verified - now()).total_seconds() > 300
                or (now() - verified).total_seconds() >= min(args.refresh_after_days * 86400, args.index_max_age_hours * 3600))

    def collection_priority(slug):
        record = state["commanders"].get(slug, {})
        published = (args.output_dir / f"{slug}.json").exists()
        # Finish queued models and upgrade existing reports before expanding
        # the catalog. Policy upgrades reuse their saved preview checkpoints.
        phase = (0 if record.get("status") == "analysis_pending" else
                 1 if record.get("coverage_complete") and published and
                     (record.get("tag_label_policy") != label_policy
                      or record.get("model_policy") != current_model_policy) else
                 3 if record.get("coverage_complete") and published else 2)
        frame = valid_deck_count(record.get("frame_decks")) or valid_deck_count(rows_by_slug[slug].get("total_decks"))
        loaded = valid_deck_count(record.get("current_previews")) or 0
        remaining = max(0, frame - loaded)
        return (phase, remaining, not bool(loaded), record.get("attempted_at", ""), slug)

    queue = sorted((slug for slug in rows_by_slug if eligible(slug)), key=collection_priority)
    if args.max_commanders:
        queue = queue[:args.max_commanders]
    summary["selected"] = list(queue)

    def checkpoint():
        elapsed = max(0.0, monotonic() - started)
        planning_rows = [row for row in rows if not (
            state["commanders"].get(row["commander_slug"], {}).get("status") == "ineligible"
            and not eligible(row["commander_slug"]))]
        planning_slugs = {row["commander_slug"] for row in planning_rows}
        remaining = sum(max(0, int(record.get("frame_decks", rows_by_slug[slug].get("total_decks") or 0))
                            - (int(record.get("current_previews", 0)) if source_time(record.get("verified_at"))
                               and (now() - source_time(record["verified_at"])).total_seconds() < getattr(args, "freshness_target_hours", 96) * 3600 else 0))
                        for slug in planning_slugs for record in [state["commanders"].get(slug, {})])
        preview_rate = summary["new_previews_attempted"] / elapsed if elapsed > 0 and summary["new_previews_attempted"] else None
        # ``limiter`` lives in the enclosing run after initialization; before
        # then (including dry runs) no throughput measurement exists.
        measured_limiter = limiter_holder[0]
        stats = measured_limiter.stats if measured_limiter is not None else None
        if measured_limiter is not None:
            learning = measured_limiter.learning_state
            if learning["learned_rate"] is not None:
                persisted = dict(state.get("source_rate", {}))
                persisted.update(learned_rate=learning["learned_rate"],
                                 throttles=prior_throttles + learning["throttles"])
                if learning["last_throttle"] is not None:
                    persisted["last_throttle"] = learning["last_throttle"]
                state["source_rate"] = persisted
        effective_ceiling = min(args.requests_per_second, state.get("source_rate", {}).get("learned_rate")
                                or args.requests_per_second)
        if stats is not None:
            effective_ceiling = min(effective_ceiling, stats["maximum_requests_per_second"])
        measured_rate = (stats.get("successful_responses", 0) / elapsed
                         if stats and elapsed > 0 and stats.get("successful_responses") else None)
        if measured_rate is not None:
            state.setdefault("source_rate", {}).update(observed_requests_per_second=measured_rate,
                                                       observed_rate_at=now().isoformat())
        rate = measured_rate or state.get("source_rate", {}).get("observed_requests_per_second")
        summary["effective_request_ceiling_per_second"] = effective_ceiling
        summary["source_rate"] = dict(state.get("source_rate", {}))
        if (summary["new_previews_attempted"] > 0
                or state.get("source_rate", {}).get("learned_rate") != learned_rate):
            summary["publish_needed"] = True
        summary["preview_processing_rate"] = preview_rate
        overhead = 19 * sum(not source_time(state["commanders"].get(slug, {}).get("last_collection_at"))
                            or (now() - source_time(state["commanders"][slug]["last_collection_at"])).total_seconds()
                            >= args.index_max_age_hours * 3600 for slug in planning_slugs)
        cooldown_until = source_time(state.get("blocked_until"))
        cooldown_seconds = max(0.0, (cooldown_until - now()).total_seconds()) if cooldown_until else 0.0
        summary["blocked_until"] = cooldown_until.isoformat() if summary["blocked"] and cooldown_until else None
        summary["block_reason"] = state.get("block_reason") if summary["blocked"] else None
        summary["capacity"] = plan_capacity(planning_rows, target_hours=args.target_completion_hours,
            max_requests_per_second=args.requests_per_second, effective_requests_per_second=effective_ceiling,
            observed_requests_per_second=rate, cooldown_seconds=cooldown_seconds,
            elapsed_seconds=max(0, (now() - cycle_start).total_seconds()), remaining_previews=remaining,
            remaining_overhead_requests=overhead, now=now())
        summary["capacity"]["rate_limit_learned"] = state.get("source_rate", {}).get("learned_rate") is not None
        summary["capacity"]["observed_rate_at"] = state.get("source_rate", {}).get("observed_rate_at")
        summary["capacity"]["unavailable_previews"] = sum(state["commanders"].get(slug, {}).get("unavailable_previews", 0)
                                                         for slug in rows_by_slug)
        summary["capacity"]["analysis_pending"] = sum(state["commanders"].get(slug, {}).get("status") == "analysis_pending"
                                                      for slug in rows_by_slug)
        summary["remaining_in_batch"] = sum(eligible(slug) for slug in rows_by_slug)
        summary["deadline_target_met"] = (None if args.target_completion_hours is None else
            remaining == 0 and summary["remaining_in_batch"] == 0 and not summary["capacity"]["unavailable_previews"])
        summary["capacity"]["disk_free_bytes"] = shutil.disk_usage(root).free
        summary["elapsed_seconds"] = round(elapsed, 3)
        state["progress"] = {"updated_at": now().isoformat(), "cycle_started_at": cycle_start.isoformat(),
            **{key: summary.get(key) for key in ("blocked", "blocked_until", "block_reason", "time_budget_reached", "no_progress", "resource_limit")},
            "capacity": summary["capacity"], "measured_request_rate": stats,
            "rate_control": {"learned_rate": state.get("source_rate", {}).get("learned_rate"),
                "last_throttle": state.get("source_rate", {}).get("last_throttle"),
                "throttles": state.get("source_rate", {}).get("throttles", 0),
                "observed_requests_per_second": rate,
                "observed_rate_at": state.get("source_rate", {}).get("observed_rate_at"),
                "configured_requests_per_second": args.requests_per_second,
                "effective_requests_per_second": effective_ceiling,
                "status": "learned_below_observed_throttle" if state.get("source_rate", {}).get("learned_rate") else "calibrating"}}
        if not args.dry_run:
            atomic_json(state_path, state)
            atomic_json(summary_path, summary)

    limiter_holder = [None]
    checkpoint()
    cooldown = source_time(state.get("blocked_until"))
    if cooldown and cooldown > now():
        summary.update(blocked=True, blocked_until=state["blocked_until"])
        checkpoint()
        return summary
    if args.dry_run:
        return summary
    deadline = started + args.max_runtime_seconds if args.max_runtime_seconds else None
    limiter = RequestLimiter(max_rate=args.requests_per_second, learned_rate=learned_rate,
                             start_rate=min(2, args.requests_per_second), ramp_after=100,
                             deadline=deadline, monotonic=monotonic)
    limiter_holder[0] = limiter
    transport, metadata_lock = PreviewTransport(), threading.Lock()
    repository = MetadataRepository(args.card_metadata_cache, max_age_days=args.source_max_age_hours / 24,
                                    deadline=deadline, monotonic=monotonic)
    commander_aliases = CommanderAliasCatalog(root / "sources")
    collector, analyzer = collector or collect_evidence, analyzer or analyze_saved_evidence
    analysis_executor_factory = analysis_executor_factory or (lambda: ProcessPoolExecutor(
        max_workers=args.analysis_workers, mp_context=multiprocessing.get_context("spawn")))
    network_jobs, model_jobs = {}, {}
    analysis_queue = []
    for slug in queue[:]:
        if args.max_runtime_seconds and monotonic() - started >= args.max_runtime_seconds:
            summary["time_budget_reached"] = True
            break
        record = state["commanders"].get(slug, {})
        evidence_path = root / "evidence" / f"{slug}.json.gz"
        if record.get("status") == "analysis_pending" and evidence_path.exists():
            try:
                with gzip.open(evidence_path, "rt", encoding="utf-8") as handle:
                    pending = json.load(handle)
                    pending_policy = pending.get("tag_label_policy")
            except (OSError, EOFError, ValueError, TypeError, AttributeError):
                pending_policy = None
            if pending_policy == label_policy:
                options = collection_options(args, slug, args.max_preview_seconds)
                pending["analysis_version"] = evidence_version(pending, options)
                save_evidence(evidence_path, pending)
                analysis_queue.append((slug, evidence_path, options))
                queue.remove(slug)
    visits, visited_count = {}, 0

    def publish_analysis(slug, evidence_path, result):
        report = json.loads(Path(result["report"]).read_text())
        for item in report.get("collection", {}).get("provenance", []):
            item.pop("cache_file", None)
        atomic_json(args.output_dir / f"{slug}.json", report)
        record = state["commanders"][slug]
        record.update(status=result["status"], analysis_version=result["version"],
                      model_policy=current_model_policy, analyzed_at=now().isoformat())
        record.pop("model_upgrade_needs_collection", None)
        record.pop("analysis_deferred_reason", None)
        record.pop("retry_after", None)
        record["tag_label_policy"] = report.get("tag_label_policy")
        summary["completed"].append(slug)
        summary.update(publish_needed=True, no_progress=False)
        evidence_path.unlink(missing_ok=True)
        shutil.rmtree(root / "analyzed" / slug, ignore_errors=True)

    model_pool = analysis_executor_factory()
    with ThreadPoolExecutor(max_workers=args.workers) as network_pool:
        try:
            while queue or network_jobs or analysis_queue or model_jobs:
                elapsed = monotonic() - started
                remaining_time = args.max_runtime_seconds - elapsed if args.max_runtime_seconds else float("inf")
                accepting = remaining_time > 0 and not summary["blocked"]
                if accepting and shutil.disk_usage(root).free < args.min_free_disk_gb * 1024 ** 3:
                    summary["resource_limit"] = "Insufficient local disk for another durable collection checkpoint"
                    accepting = False
                if remaining_time <= 0:
                    summary["time_budget_reached"] = True
                while (accepting and queue and len(network_jobs) < args.workers
                       and (deadline is None or monotonic() < deadline)):
                    slug = queue.pop(0)
                    if args.max_commanders and visited_count >= args.max_commanders and slug not in visits:
                        continue
                    if slug not in visits:
                        visited_count += 1
                    visits[slug] = visits.get(slug, 0) + 1
                    options = collection_options(args, slug, remaining_time)
                    previous = state["commanders"].get(slug, {})
                    options.cache_only_model_upgrade = bool(
                        not args.refresh and previous.get("coverage_complete")
                        and previous.get("tag_label_policy") == label_policy
                        and previous.get("model_policy") != current_model_policy
                        and not previous.get("model_upgrade_needs_collection")
                        and (args.output_dir / f"{slug}.json").exists())
                    if options.cache_only_model_upgrade:
                        # A model-policy change does not refresh its sources.
                        # Reuse parsed previews and source pages, even if old;
                        # their actual timestamps and freshness remain visible.
                        options.fetch = False
                        options.source_max_age_hours = options.index_max_age_hours = None
                    state["commanders"].setdefault(slug, {}).update(
                        status="collecting", attempted_at=now().isoformat(), cache_snapshot="rolling")
                    future = network_pool.submit(collector, options, limiter=limiter, preview_transport=transport,
                                                 metadata_lock=metadata_lock, metadata_provider=repository,
                                                 commander_alias_provider=commander_aliases)
                    network_jobs[future] = (slug, options)
                while (analysis_queue and len(model_jobs) < args.analysis_workers
                       and (deadline is None or monotonic() < deadline)):
                    slug, evidence_path, options = analysis_queue.pop(0)
                    (options.output_dir / "result.json").unlink(missing_ok=True)
                    model_jobs[model_pool.submit(analyzer, evidence_path, vars(options))] = (slug, evidence_path)
                futures = [*network_jobs, *model_jobs]
                if not futures:
                    break
                grace_left = remaining_time + summary["worker_grace_seconds"]
                timeout = min(1, max(0, grace_left)) if model_jobs else 1
                done, _ = wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
                for future in done:
                    slug, context = (network_jobs.pop(future) if future in network_jobs else model_jobs.pop(future))
                    is_collection = isinstance(context, argparse.Namespace)
                    record = state["commanders"][slug]
                    try:
                        result = future.result()
                        if is_collection:
                            collection = result["collection"]
                            if getattr(context, "cache_only_model_upgrade", False):
                                previous_report = json.loads((args.output_dir / f"{slug}.json").read_text())
                                previous_collection = previous_report.get("collection", {})
                                expected_dataset = (previous_collection.get("analysis_dataset_sha256")
                                                    or previous_collection.get("dataset_sha256"))
                                if (not collection.get("complete") or not expected_dataset
                                        or collection.get("dataset_sha256") != expected_dataset
                                        or result.get("metadata_summary", {}).get("missing")
                                        or any("tag" in failure or "ranking_bracket" in failure
                                               for failure in result.get("failures", []))):
                                    raise CachedModelUpgradeUnavailable("Saved inputs do not reproduce the complete published sample, profiles and card rules")
                            if result.get("metadata_summary", {}).get("blocked"):
                                limiter.block("Card metadata source access restriction", kind="access_restriction")
                            if result.get("blocked") or limiter.blocked:
                                raise SourceBlocked("Source access restriction or rate limit; shared workers stopped")
                            new_attempts = int(collection.get("new_previews_attempted", 0))
                            summary["new_previews_attempted"] += new_attempts
                            cache_only_upgrade = getattr(context, "cache_only_model_upgrade", False)
                            summary["reanalyzed_from_cache" if cache_only_upgrade else "collected"].append(slug)
                            record.update(frame_decks=collection.get("frame_decks") or 0,
                                collection_dataset_sha256=collection.get("dataset_sha256"),
                                current_previews=collection.get("fresh_previews", collection.get("previews_loaded", 0)),
                                unavailable_previews=collection.get("unavailable_previews", 0),
                                coverage_complete=collection.get("complete") is True and collection.get("target_mode") == "all",
                                tag_label_policy=result.get("tag_label_policy"))
                            if not cache_only_upgrade:
                                record["last_collection_at"] = now().isoformat()
                            freshness = collection.get("freshness", {})
                            if not cache_only_upgrade:
                                record["verified_at"] = (freshness.get("oldest_verified_at") or freshness.get("oldest_source_at")
                                                         or (freshness.get("index_source_at") if collection.get("complete") else None))
                            if new_attempts or collection.get("reconciliation", {}).get("refreshed_index"):
                                summary["no_progress"] = False
                            destination = args.output_dir / f"{slug}.json"
                            if collection.get("complete"):
                                version = evidence_version(result, context)
                                collection.update(analysis_current=True, analysis_dataset_sha256=collection.get("dataset_sha256"))
                                result["analysis_version"] = version
                                if record.get("analysis_version") == version and destination.exists():
                                    report = json.loads(destination.read_text())
                                    report["total_edhrec_decks"] = result["base"]["deck_count"]
                                    report["collection"] = {**report.get("collection", {}), **published_collection(result)}
                                    refresh_unchanged_report_metadata(report, result.get("metadata_summary", {}))
                                    atomic_json(destination, report)
                                    record["status"] = report["deck_analysis"]["status"]
                                    record["model_policy"] = current_model_policy
                                    summary["completed"].append(slug)
                                    summary.update(publish_needed=True, no_progress=False)
                                else:
                                    evidence_path = root / "evidence" / f"{slug}.json.gz"
                                    save_evidence(evidence_path, result)
                                    record["status"] = "analysis_pending"
                                    analysis_queue.append((slug, evidence_path, context))
                            else:
                                if destination.exists():
                                    report = json.loads(destination.read_text())
                                    report["collection"].update(published_collection(result), analysis_current=False)
                                    atomic_json(destination, report)
                                    summary["publish_needed"] = True
                                if new_attempts:
                                    # A finite visit must not send useful partial
                                    # work behind thousands of untouched frames.
                                    # Reapply the same remaining-work priority;
                                    # other small frames can still outrank a giant.
                                    queue.append(slug)
                                    queue.sort(key=collection_priority)
                                elif limiter.budget_expired:
                                    summary["time_budget_reached"] = True
                                    record["status"] = "collecting"
                                    record.pop("retry_after", None)
                                else:
                                    record.update(status="failed", retry_after=(now() + timedelta(minutes=30)).isoformat())
                            if args.prune_completed_cache:
                                previews = context.cache_dir / "previews"
                                if previews.is_dir() and not previews.is_symlink():
                                    shutil.rmtree(previews)
                        else:
                            publish_analysis(slug, context, result)
                    except CommanderBelowMinimum as error:
                        record.update(status="ineligible", observed_total_decks=error.count,
                                      eligibility_checked_at=now().isoformat(),
                                      retry_after=(now() + timedelta(hours=args.index_max_age_hours)).isoformat())
                        summary["skipped"].append({"commander_slug": slug, "reason": str(error),
                                                   "total_decks": error.count, "minimum": error.minimum})
                        destination = args.output_dir / f"{slug}.json"
                        if destination.exists():
                            report = json.loads(destination.read_text())
                            report["total_edhrec_decks"] = error.count
                            report.setdefault("collection", {})["analysis_current"] = False
                            atomic_json(destination, report)
                            summary["publish_needed"] = True
                        if not is_collection:
                            context.unlink(missing_ok=True)
                    except BudgetExpired:
                        summary["time_budget_reached"] = True
                        record.update(status="collecting" if is_collection else "analysis_pending")
                        record.pop("retry_after", None)
                    except SourceBlocked as error:
                        summary["blocked"] = True
                        limiter.block(str(error))
                        cooldown_seconds = max(3600, limiter.stats.get("retry_after_seconds", 0))
                        state["blocked_until"] = (now() + timedelta(seconds=cooldown_seconds)).isoformat()
                        state["block_reason"] = limiter.stats.get("block_kind") or "access_restriction"
                        record.update(status="blocked", retry_after=state["blocked_until"])
                        summary["failures"].append({"commander_slug": slug, "error": str(error)})
                    except (OSError, ValueError, KeyError, TypeError, RuntimeError, httpx.HTTPError) as error:
                        if is_collection and getattr(context, "cache_only_model_upgrade", False):
                            # Retry through the ordinary shared source queue on
                            # a later run, preserving the usable published report.
                            record.update(status="collecting", model_upgrade_needs_collection=True)
                            summary["model_upgrades_deferred"].append({"commander_slug": slug, "reason": str(error)})
                            summary["no_progress"] = False  # The durable retry route has changed.
                            checkpoint()
                            continue
                        record.update(status="analysis_pending" if not is_collection else "failed",
                                      retry_after=(now() + timedelta(minutes=30)).isoformat())
                        summary["failures"].append({"commander_slug": slug, "error": str(error)})
                    checkpoint()
                if (args.max_runtime_seconds and monotonic() - started
                        >= args.max_runtime_seconds + summary["worker_grace_seconds"] and model_jobs):
                    # Inputs were durably saved before submission. A deadline
                    # is scheduling, not a source failure or a retry cooldown.
                    for future in model_jobs:
                        future.cancel()
                    shutdown_executor(model_pool, terminate=True)
                    summary["analysis_workers_stopped"] = True
                    for future, (slug, evidence_path) in model_jobs.items():
                        receipt = root / "analyzed" / slug / "result.json"
                        try:
                            result = future.result() if future.done() and not future.cancelled() else None
                        except (OSError, ValueError, KeyError, TypeError, RuntimeError, httpx.HTTPError):
                            result = None  # The forced pool stop breaks unfinished Futures.
                        try:
                            result = result or (json.loads(receipt.read_text()) if receipt.exists() else None)
                            if result is not None:
                                publish_analysis(slug, evidence_path, result)
                                continue
                        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                            # A partial staging report is not a published result.
                            # Keep its original evidence for the next attempt.
                            pass
                        record = state["commanders"][slug]
                        record.update(status="analysis_pending", analysis_deferred_reason="run_time_budget")
                        record.pop("retry_after", None)
                    model_jobs.clear()
                    checkpoint()
                if not accepting and not network_jobs and not model_jobs:
                    break
        finally:
            if not summary["analysis_workers_stopped"]:
                shutdown_executor(model_pool, terminate=bool(model_jobs))
            summary["deferred_analyses"] = sorted(slug for slug in rows_by_slug
                if state["commanders"].get(slug, {}).get("status") == "analysis_pending")
            checkpoint()
    summary["continuation_needed"] = (not summary["blocked"] and not summary["no_progress"]
                                      and not summary.get("resource_limit") and summary["remaining_in_batch"] > 0
                                      and args.sample_decks is None and not args.max_commanders
                                      and not args.commanders and not args.refresh)
    summary["measured_request_rate"] = limiter.stats if hasattr(limiter, "stats") else None
    checkpoint()
    return summary
