#!/usr/bin/env python3
"""Build a bounded, resumable cohort of real commander build-style samples.

The weekly update collects at most five commanders; larger or targeted cohorts
use an explicit run or the dedicated build-styles workflow. Every indexed
commander is supported, and successful samples are reused between batches.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Callable
from zoneinfo import ZoneInfo

import httpx


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
        if not report.get("profiles", {}).get("profiles") and not report.get("deck_analysis", {}).get("packages"):
            return None
        parsed = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def select_commanders(args: argparse.Namespace) -> list[dict]:
    rows = read_index(args.commander_index)
    requested = set(args.commanders or [])
    known = {row["commander_slug"] for row in rows}
    if requested - known:
        raise ValueError(f"Commanders not in the index: {', '.join(sorted(requested - known))}")
    selected, refreshable = [], []
    snapshot = datetime.strptime(args.snapshot_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    for row in sorted(rows, key=lambda row: (-float(row.get("total_decks") or 0), row["commander_slug"])):
        slug = row["commander_slug"]
        if requested and slug not in requested:
            continue
        if not requested and float(row.get("total_decks") or 0) < args.min_total_decks:
            continue
        existing_dates = [date for directory in [args.output_dir, args.seed_dir]
                          if (date := sample_date(directory / f"{slug}.json", slug)) is not None]
        if not args.refresh and existing_dates:
            most_recent = max(existing_dates)
            if (snapshot - most_recent).days >= args.refresh_after_days:
                refreshable.append((most_recent, row))
            continue
        selected.append(row)
    # Grow coverage before spending the bounded batch refreshing dated samples.
    selected.extend(row for _, row in sorted(refreshable, key=lambda item: (item[0], item[1]["commander_slug"])))
    return selected[:args.max_commanders]


def run_batch(args: argparse.Namespace, *, runner: Callable | None = None) -> dict:
    from edhrec_affinity.build_styles.__main__ import parser as build_parser, run
    from edhrec_affinity.build_styles.sources import SourceBlocked

    selected = select_commanders(args)
    summary = {
        "snapshot_date": args.snapshot_date, "selected": [row["commander_slug"] for row in selected],
        "max_preview_attempts": len(selected) * args.sample_decks,
        "max_page_requests": len(selected) * (2 + args.max_tags + args.sample_decks),
        "completed": [], "failures": [], "blocked": False, "dry_run": args.dry_run,
    }
    if args.dry_run:
        return summary
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runner = runner or run
    for row in selected:
        slug = row["commander_slug"]
        print(f"Building {slug} (at most {args.sample_decks} deck previews)", flush=True)
        argv = ["--commander", slug, "--components", "auto", "--max-components", str(args.max_components),
                "--max-tags", str(args.max_tags), "--min-tag-decks", str(args.min_tag_decks),
                "--sample-decks", str(args.sample_decks), "--seed", str(args.seed),
                "--request-delay", str(args.request_delay), "--no-html",
                "--cache-dir", str(args.cache_root / args.snapshot_date / slug),
                "--output-dir", str(args.cache_root / args.snapshot_date / slug / "analysis")]
        if args.fetch:
            argv.append("--fetch")
        try:
            report = runner(build_parser().parse_args(argv))
            if report.get("collection", {}).get("blocked"):
                summary["blocked"] = True
                summary["failures"].append({"commander_slug": slug, "error": "EDHREC collection blocked; batch stopped"})
                break
            if report.get("commander_slug") != slug:
                raise ValueError("Report belongs to another commander")
            analysis = report.get("deck_analysis", {})
            profiles = report.get("profiles", {}).get("profiles", [])
            if analysis.get("status") != "fitted" and not profiles:
                raise ValueError(analysis.get("reason", "No usable profiles or deck packages"))
            destination = args.output_dir / f"{slug}.json"
            # Keep a previous successful fit if a later sample is too small.
            if analysis.get("status") != "fitted" and has_completed_report(destination, slug):
                summary["failures"].append({"commander_slug": slug, "error": "Kept previous fitted report; new sample insufficient"})
                continue
            # Local cache paths are machine specific; URLs and collection dates
            # remain in provenance for auditing the published sample.
            for provenance in report.get("collection", {}).get("provenance", []):
                provenance.pop("cache_file", None)
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(destination)
            summary["completed"].append(slug)
        except SourceBlocked as error:
            summary["blocked"] = True
            summary["failures"].append({"commander_slug": slug, "error": str(error)})
            break
        except (ValueError, KeyError, OSError, RuntimeError, httpx.HTTPError) as error:
            summary["failures"].append({"commander_slug": slug, "error": str(error)})
    (args.output_dir.parent / "build_style_batch.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
    )
    return summary


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--commander-index", type=Path, default=Path("frontend/public/data/latest/commanders/index.json"))
    cli.add_argument("--output-dir", type=Path, default=Path("data/processed/build-styles-current/build-styles"))
    cli.add_argument("--seed-dir", type=Path, default=Path("data/build-styles"))
    cli.add_argument("--commanders", nargs="+", help="Optional indexed slugs; otherwise choose unsampled commanders by deck count")
    cli.add_argument("--max-commanders", type=int, default=5, help="Hard batch limit, 1–20 (default 5)")
    cli.add_argument("--min-total-decks", type=int, default=50)
    cli.add_argument("--sample-decks", type=int, default=60, help="Maximum attempted previews per commander, 0–200")
    cli.add_argument("--max-tags", type=int, default=12)
    cli.add_argument("--min-tag-decks", type=int, default=25)
    cli.add_argument("--max-components", type=int, default=8, help="Automatic package-count search: 1 through this ceiling")
    cli.add_argument("--seed", type=int, default=42)
    cli.add_argument("--request-delay", type=float, default=1)
    cli.add_argument("--cache-root", type=Path, default=Path("data/raw/build-styles"))
    cli.add_argument("--snapshot-date", default=datetime.now(ZoneInfo("America/Toronto")).date().isoformat(),
                     help="YYYY-MM-DD cache snapshot; reuse the date to resume without refetching")
    cli.add_argument("--fetch", action="store_true", help="Fetch missing public EDHREC pages; otherwise cache only")
    cli.add_argument("--refresh", action="store_true", help="Include commanders already having a fitted report")
    cli.add_argument("--refresh-after-days", type=int, default=90,
                     help="After unsampled commanders, refresh samples this many days old (default 90)")
    cli.add_argument("--dry-run", action="store_true", help="Print selection and upper request bounds without collecting")
    return cli


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    if not 1 <= args.max_commanders <= 20 or not 0 <= args.sample_decks <= 200 or not 1 <= args.max_tags <= 16:
        cli.error("Use 1–20 commanders, 0–200 deck previews, and 1–16 tag profiles per batch")
    if (not 1 <= args.max_components <= 32 or args.min_tag_decks < 1 or args.min_total_decks < 0
            or args.request_delay < 1 or args.refresh_after_days < 1):
        cli.error("Invalid package ceiling, deck minimum, or request delay (minimum one second)")
    try:
        if datetime.strptime(args.snapshot_date, "%Y-%m-%d").date().isoformat() != args.snapshot_date:
            raise ValueError("Snapshot date must be YYYY-MM-DD")
        summary = run_batch(args)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if summary["blocked"] or (summary["failures"] and not summary["completed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
