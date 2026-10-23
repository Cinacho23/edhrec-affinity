#!/usr/bin/env python3
"""Work through every indexed commander with checkpointed build-style collection.

Runs are bounded by time, not catalog size. Reports and collection checkpoints
are reused by the next run, including attempts with insufficient deck evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from edhrec_affinity.build_styles.collection import parse_sample_decks
from edhrec_affinity.build_styles.eligibility import (
    MIN_COMMANDER_DECKS, commander_is_eligible, valid_deck_count, validate_minimum,
)


def read_index(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Commander index must be a list")
    by_slug = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Commander index contains an invalid entry")
        slug = row.get("commander_slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError("Commander index contains an invalid slug")
        by_slug[slug] = row
    return list(by_slug.values())


def has_completed_report(path: Path, slug: str) -> bool:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        return (report["commander_slug"] == slug and report["deck_analysis"]["status"] == "fitted"
                and bool(report["deck_analysis"]["packages"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def sample_date(path: Path, slug: str) -> datetime | None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report["commander_slug"] != slug:
            return None
        if report.get("deck_analysis", {}).get("status") not in {"fitted", "insufficient_data"}:
            return None
        # Reports from the initial package-only prototype need a one-time deck
        # generation upgrade, regardless of their usual sample refresh interval.
        if report.get("prototype_version") == 2:
            return None
        if report.get("deck_generation", {}).get("status") == "partial":
            return None
        parsed = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def checkpoint_path(args: argparse.Namespace) -> Path:
    # Stored inside the cumulative artifact but outside per-commander JSON files.
    return args.output_dir / "_collection" / "state.json"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def read_checkpoint(args: argparse.Namespace) -> dict:
    path = checkpoint_path(args)
    if not path.exists():
        return {"schema_version": 1, "commanders": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("commanders"), dict)):
        raise ValueError(f"Invalid collection checkpoint: {path}")
    return value


def future_date(value: str | None) -> bool:
    if not value:
        return False
    return datetime.fromisoformat(value) > datetime.now(timezone.utc)


def needs_more_previews(path: Path, slug: str, limit: int | None) -> bool:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        collection = report.get("collection", {})
        frame = collection.get("frame_decks")
        if report.get("commander_slug") != slug or not isinstance(frame, int):
            return False
        target = frame if limit is None else min(frame, limit)
        return collection.get("previews_attempted", 0) < target or collection.get("complete") is False
    except (OSError, ValueError, TypeError):
        return False


def preview_expansion_needed(args: argparse.Namespace, slug: str) -> bool:
    # The cumulative artifact supersedes the seed; an old seed must never cause
    # a completed larger collection to be scheduled again on every run.
    for directory in [args.output_dir, args.seed_dir]:
        path = directory / f"{slug}.json"
        if sample_date(path, slug) is not None:
            return needs_more_previews(path, slug, args.sample_decks)
    return False


def select_commanders(args: argparse.Namespace) -> list[dict]:
    minimum = validate_minimum(args.min_total_decks)
    rows = read_index(args.commander_index)
    requested = set(args.commanders or [])
    known = {row["commander_slug"] for row in rows}
    if requested - known:
        raise ValueError(f"Commanders not in the index: {', '.join(sorted(requested - known))}")
    state = read_checkpoint(args)
    selected, expanding, refreshable = [], [], []
    snapshot = datetime.strptime(args.snapshot_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    for row in sorted(rows, key=lambda row: (-(valid_deck_count(row.get("total_decks")) or 0), row["commander_slug"])):
        slug = row["commander_slug"]
        if requested and slug not in requested:
            continue
        if not commander_is_eligible(row.get("total_decks"), minimum):
            continue
        attempt = state["commanders"].get(slug, {})
        if not args.refresh and future_date(attempt.get("retry_after")):
            continue
        if not args.refresh and (attempt.get("status") == "collecting" or preview_expansion_needed(args, slug)):
            expanding.append(row)
            continue
        existing_dates = [date for directory in [args.output_dir, args.seed_dir]
                          if (date := sample_date(directory / f"{slug}.json", slug)) is not None]
        if not args.refresh and existing_dates:
            most_recent = max(existing_dates)
            if (snapshot - most_recent).days >= args.refresh_after_days:
                refreshable.append((most_recent, row))
            continue
        selected.append(row)
    # Alternate new coverage and expansion; rotate unfinished commanders so one
    # very large index cannot monopolize every scheduled run.
    expanding.sort(key=lambda row: (state["commanders"].get(row["commander_slug"], {}).get("attempted_at", ""),
                                   -float(row.get("total_decks") or 0), row["commander_slug"]))
    queue = []
    for index in range(max(len(selected), len(expanding))):
        if index < len(expanding):
            queue.append(expanding[index])
        if index < len(selected):
            queue.append(selected[index])
    selected = queue
    selected.extend(row for _, row in sorted(refreshable, key=lambda item: (item[0], item[1]["commander_slug"])))
    return selected[:args.max_commanders] if args.max_commanders else selected


def run_batch(args: argparse.Namespace, *, runner: Callable | None = None, monotonic: Callable = time.monotonic) -> dict:
    if args.pipeline:
        from edhrec_affinity.build_styles.pipeline import run_pipeline

        return run_pipeline(args, read_index(args.commander_index), monotonic=monotonic)
    from edhrec_affinity.build_styles.__main__ import parser as build_parser, run
    from edhrec_affinity.build_styles.sources import SourceBlocked

    started = monotonic()
    state = read_checkpoint(args)
    selected = select_commanders(args)
    summary = {
        "snapshot_date": args.snapshot_date, "selected": [row["commander_slug"] for row in selected],
        "max_preview_attempts": None if args.sample_decks is None else len(selected) * args.sample_decks,
        "max_page_requests": None if args.sample_decks is None else len(selected) * (7 + args.max_tags + args.sample_decks),
        "catalog_size": len(read_index(args.commander_index)),
        "completed": [], "failures": [], "blocked": False, "dry_run": args.dry_run,
        "time_budget_seconds": args.max_runtime_seconds, "time_budget_reached": False,
    }
    if args.dry_run:
        return summary
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir.parent / "build_style_batch.json"
    if future_date(state.get("blocked_until")):
        summary.update(blocked=True, blocked_until=state["blocked_until"])
        atomic_json(summary_path, summary)
        return summary
    runner = runner or run
    atomic_json(summary_path, summary)
    for row in selected:
        elapsed = monotonic() - started
        if args.max_runtime_seconds and elapsed >= args.max_runtime_seconds:
            summary["time_budget_reached"] = True
            break
        slug = row["commander_slug"]
        print(f"Building {slug} ({'all available' if args.sample_decks is None else args.sample_decks} deck previews)", flush=True)
        previous_attempt = state["commanders"].get(slug, {})
        # An interrupted commander resumes the same raw snapshot. A later retry
        # after an actual failure starts a new snapshot to allow fresh evidence.
        cache_snapshot = (previous_attempt.get("cache_snapshot", args.snapshot_date)
                          if not args.refresh and previous_attempt.get("status") in
                          {"running", "blocked", "failed", "generation_partial", "collecting"} else args.snapshot_date)
        if datetime.strptime(cache_snapshot, "%Y-%m-%d").date().isoformat() != cache_snapshot:
            raise ValueError("Invalid cache snapshot in collection checkpoint")
        state["commanders"][slug] = {
            "status": "running", "attempted_at": datetime.now(timezone.utc).isoformat(),
            "cache_snapshot": cache_snapshot,
        }
        atomic_json(checkpoint_path(args), state)
        preview_state = args.output_dir / "_collection" / "previews" / f"{slug}.json.gz"
        if args.refresh or (previous_attempt.get("cache_snapshot") not in {None, cache_snapshot}
                            and previous_attempt.get("status") not in
                            {"running", "blocked", "failed", "generation_partial", "collecting"}):
            preview_state.unlink(missing_ok=True)
        argv = ["--commander", slug, "--components", "auto", "--max-components", str(args.max_components),
                "--min-total-decks", str(args.min_total_decks),
                "--max-tags", str(args.max_tags), "--min-tag-decks", str(args.min_tag_decks),
                "--sample-decks", "all" if args.sample_decks is None else str(args.sample_decks), "--seed", str(args.seed),
                "--collection-state", str(preview_state),
                "--max-preview-seconds", str(min(args.max_preview_seconds, max(1, args.max_runtime_seconds - elapsed))
                                            if args.max_runtime_seconds else args.max_preview_seconds),
                "--request-delay", str(args.request_delay), "--no-html",
                "--cache-dir", str(args.cache_root / cache_snapshot / slug),
                "--output-dir", str(args.cache_root / cache_snapshot / slug / "analysis")]
        if args.fetch:
            argv.append("--fetch")
        try:
            report = runner(build_parser().parse_args(argv))
            if report.get("collection", {}).get("blocked"):
                raise SourceBlocked("EDHREC collection blocked; batch stopped")
            if report.get("commander_slug") != slug:
                raise ValueError("Report belongs to another commander")
            analysis = report.get("deck_analysis", {})
            if analysis.get("status") not in {"fitted", "insufficient_data"}:
                raise ValueError("Report has no recognized deck analysis status")
            destination = args.output_dir / f"{slug}.json"
            collecting = report.get("collection", {}).get("complete") is False
            # Keep a previous successful fit if a later sample is too small.
            if analysis.get("status") != "fitted" and has_completed_report(destination, slug):
                state["commanders"][slug].update(
                    status="collecting" if collecting else "insufficient_data", retained_previous_fit=True,
                )
                if not collecting:
                    state["commanders"][slug]["retry_after"] = (datetime.now(timezone.utc) + timedelta(days=args.refresh_after_days)).isoformat()
                summary["completed"].append(slug)
                continue
            # Local cache paths are machine specific; URLs and collection dates
            # remain in provenance for auditing the published sample.
            for provenance in report.get("collection", {}).get("provenance", []):
                provenance.pop("cache_file", None)
            atomic_json(destination, report)
            state["commanders"][slug].update(status=analysis["status"])
            if collecting:
                state["commanders"][slug].update(status="collecting", analysis_status=analysis["status"])
            elif report.get("deck_generation", {}).get("status") == "partial":
                state["commanders"][slug].update(
                    status="generation_partial", analysis_status=analysis["status"],
                    retry_after=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                )
            summary["completed"].append(slug)
        except SourceBlocked as error:
            summary["blocked"] = True
            summary["failures"].append({"commander_slug": slug, "error": str(error)})
            state["blocked_until"] = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            state["commanders"][slug].update(status="blocked", error=str(error), retry_after=state["blocked_until"])
            break
        except (ValueError, KeyError, OSError, RuntimeError, httpx.HTTPError) as error:
            summary["failures"].append({"commander_slug": slug, "error": str(error)})
            state["commanders"][slug].update(
                status="failed", error=str(error),
                retry_after=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            )
        finally:
            atomic_json(checkpoint_path(args), state)
            atomic_json(summary_path, summary)
            # Parsed lists and source hashes are durable in the artifact. Avoid
            # retaining gigabytes of preview HTML during an uncapped backfill.
            if args.prune_completed_cache and slug in summary["completed"]:
                previews = args.cache_root / cache_snapshot / slug / "previews"
                if previews.is_dir() and not previews.is_symlink():
                    shutil.rmtree(previews)
            # CI retains completed reports and their source provenance as the
            # durable artifact; raw HTML is needed only while an attempt resumes.
            if (args.prune_completed_cache and slug in summary["completed"]
                    and state["commanders"][slug]["status"] not in {"generation_partial", "collecting"}):
                raw = args.cache_root / cache_snapshot / slug
                if raw.is_dir() and not raw.is_symlink():
                    shutil.rmtree(raw)
                (args.output_dir / "_collection" / "previews" / f"{slug}.json.gz").unlink(missing_ok=True)
    summary["remaining_in_batch"] = len(selected) - len(summary["completed"]) - len(summary["failures"])
    atomic_json(summary_path, summary)
    return summary


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--commander-index", type=Path, default=Path("frontend/public/data/latest/commanders/index.json"))
    cli.add_argument("--output-dir", type=Path, default=Path("data/processed/build-styles-current/build-styles"))
    cli.add_argument("--seed-dir", type=Path, default=Path("data/build-styles"))
    cli.add_argument("--commanders", nargs="+", help="Optional indexed slugs; otherwise choose unsampled commanders by deck count")
    cli.add_argument("--max-commanders", type=int, default=0, help="Optional count limit; 0 processes the entire pending catalog (default)")
    cli.add_argument("--max-runtime-seconds", type=float, default=2700,
                     help="Stop before starting another commander after this budget; 0 disables the budget (default 2700)")
    cli.add_argument("--min-total-decks", type=int, default=MIN_COMMANDER_DECKS,
                     help="Minimum commander deck count; at least 200 (default 200)")
    cli.add_argument("--sample-decks", type=parse_sample_decks, default=None,
                     help="all (default) collects the available index; optional nonnegative count, with no count ceiling")
    cli.add_argument("--max-preview-seconds", type=float, default=300,
                     help="Preview collection time slice per commander (default 300 seconds); progress resumes next run")
    cli.add_argument("--max-tags", type=int, default=12)
    cli.add_argument("--min-tag-decks", type=int, default=25)
    cli.add_argument("--max-components", type=int, default=8, help="Automatic package-count search: 1 through this ceiling")
    cli.add_argument("--seed", type=int, default=42)
    cli.add_argument("--request-delay", type=float, default=1)
    cli.add_argument("--cache-root", type=Path, default=Path("data/raw/build-styles"))
    cli.add_argument("--prune-completed-cache", action="store_true",
                     help="Remove completed raw snapshots after durable report/checkpoint writes; retain interrupted/partial attempts")
    cli.add_argument("--snapshot-date", default=datetime.now(ZoneInfo("America/Toronto")).date().isoformat(),
                     help="YYYY-MM-DD cache snapshot; reuse the date to resume without refetching")
    cli.add_argument("--fetch", action="store_true", help="Fetch missing public EDHREC pages; otherwise cache only")
    cli.add_argument("--pipeline", action="store_true", help="Overlap full-catalog collection and model workers with rate-based completion estimates")
    cli.add_argument("--workers", type=int, default=6, help="Concurrent commander collectors sharing one source request budget")
    cli.add_argument("--preview-workers", type=int, default=16, help="Concurrent preview downloads per commander")
    cli.add_argument("--analysis-workers", type=int, default=2, help="Independent CPU model processes")
    cli.add_argument("--requests-per-second", type=float, default=40,
                     help="Shared adaptive request ceiling, not a claimed sustained source capacity")
    cli.add_argument("--source-max-age-hours", type=float, default=72)
    cli.add_argument("--index-max-age-hours", type=float, default=24)
    cli.add_argument("--target-completion-hours", type=float,
                     help="Optional completion deadline; otherwise report a rate-based ETA without a deadline")
    cli.add_argument("--freshness-target-hours", type=float, default=96,
                     help="Source freshness window, independent of the completion estimate (default 96)")
    cli.add_argument("--upstream-snapshot-date", help="Weekly dataset YYYY-MM-DD; later snapshots start a new collection cycle")
    cli.add_argument("--min-free-disk-gb", type=float, default=2)
    cli.add_argument("--card-metadata-cache", type=Path, default=Path("data/raw/build-style-card-metadata.json"))
    cli.add_argument("--refresh", action="store_true", help="Include commanders already having a fitted report")
    cli.add_argument("--refresh-after-days", type=int, default=90,
                     help="After unsampled commanders, refresh samples this many days old (default 90)")
    cli.add_argument("--dry-run", action="store_true", help="Print selection and upper request bounds without collecting")
    return cli


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    try:
        validate_minimum(args.min_total_decks)
    except ValueError as error:
        cli.error(str(error))
    if args.max_commanders < 0 or not 1 <= args.max_tags <= 16:
        cli.error("Use a nonnegative commander count and 1–16 tag profiles")
    if (not 1 <= args.max_components <= 32 or args.min_tag_decks < 1 or args.min_total_decks < 0
            or not math.isfinite(args.request_delay) or args.request_delay < 1 or args.refresh_after_days < 1
            or not math.isfinite(args.max_runtime_seconds) or args.max_runtime_seconds < 0
            or not math.isfinite(args.max_preview_seconds) or args.max_preview_seconds <= 0):
        cli.error("Invalid package ceiling, deck minimum, or request delay (minimum one second)")
    if (not 1 <= args.workers <= 32 or not 1 <= args.preview_workers <= 64 or not 1 <= args.analysis_workers <= 16
            or any(not math.isfinite(value) or value <= 0 for value in
                   (args.requests_per_second, args.source_max_age_hours, args.index_max_age_hours, args.freshness_target_hours))
            or (args.target_completion_hours is not None
                and (not math.isfinite(args.target_completion_hours) or args.target_completion_hours <= 0))
            or not math.isfinite(args.min_free_disk_gb) or args.min_free_disk_gb < 0):
        cli.error("Invalid worker count, source request ceiling, freshness window, or free-disk minimum")
    try:
        if datetime.strptime(args.snapshot_date, "%Y-%m-%d").date().isoformat() != args.snapshot_date:
            raise ValueError("Snapshot date must be YYYY-MM-DD")
        if (args.upstream_snapshot_date and datetime.strptime(args.upstream_snapshot_date, "%Y-%m-%d").date().isoformat()
                != args.upstream_snapshot_date):
            raise ValueError("Upstream snapshot date must be YYYY-MM-DD")
        summary = run_batch(args)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    if args.pipeline:
        return 1 if summary["blocked"] or (summary["failures"] and summary["no_progress"]) else 0
    return 1 if summary["blocked"] or (summary["failures"] and not summary["completed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
