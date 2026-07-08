#!/usr/bin/env python3
"""
use_previous_processed_if_empty.py

GitHub Actions helper for scheduled data builds.

If the current cleaning step produces zero clean commander-tag rows, the site
should not deploy an empty dataset. This script replaces the current processed
snapshot with the most recent older non-empty processed snapshot, then writes a
small report explaining that fallback.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


COMMANDER_TAGS_CLEAN_FILENAME = "commander_tags_clean.json"
DATA_VALIDATION_REPORT_FILENAME = "data_validation_report.json"
FALLBACK_REPORT_FILENAME = "snapshot_fallback_report.json"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def parse_snapshot_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.name)
    except ValueError:
        return None


def clean_tag_row_count(snapshot_dir: Path, *, required: bool) -> int | None:
    """
    Return the clean commander-tag row count for a processed snapshot.

    Prefer data_validation_report.json because it is the cleaning step's own
    audit output. Fall back to counting commander_tags_clean.json records.
    """
    validation_report_path = snapshot_dir / DATA_VALIDATION_REPORT_FILENAME

    if validation_report_path.exists():
        report = read_json(validation_report_path)

        if not isinstance(report, dict):
            raise ValueError(f"Expected JSON object in {validation_report_path}")

        value = report.get("clean_tag_row_count")

        if isinstance(value, int):
            return value

        if isinstance(value, str) and value.strip().isdigit():
            return int(value)

    clean_tags_path = snapshot_dir / COMMANDER_TAGS_CLEAN_FILENAME

    if not clean_tags_path.exists():
        if required:
            raise FileNotFoundError(
                f"Missing required clean data file: {clean_tags_path}"
            )
        return None

    records = read_json(clean_tags_path)

    if not isinstance(records, list):
        raise ValueError(f"Expected JSON list in {clean_tags_path}")

    return len(records)


def find_latest_non_empty_snapshot(
    *,
    processed_root: Path,
    current_dir: Path,
) -> tuple[Path, int] | None:
    current_date = parse_snapshot_date(current_dir)
    current_resolved = current_dir.resolve()

    candidates: list[tuple[date, Path, int]] = []

    if not processed_root.exists():
        return None

    for child in processed_root.iterdir():
        if not child.is_dir():
            continue

        if child.resolve() == current_resolved:
            continue

        snapshot_date = parse_snapshot_date(child)

        if snapshot_date is None:
            continue

        if current_date is not None and snapshot_date >= current_date:
            continue

        row_count = clean_tag_row_count(child, required=False)

        if row_count is not None and row_count > 0:
            candidates.append((snapshot_date, child, row_count))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, snapshot_dir, row_count = candidates[0]

    return snapshot_dir, row_count


def replace_snapshot_dir(*, source_dir: Path, current_dir: Path) -> None:
    if source_dir.resolve() == current_dir.resolve():
        raise ValueError("Fallback source and current directory are the same.")

    if current_dir.exists():
        shutil.rmtree(current_dir)

    shutil.copytree(source_dir, current_dir)


def use_previous_processed_if_empty(
    *,
    current_dir: Path,
    processed_root: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    current_count = clean_tag_row_count(current_dir, required=True)

    summary: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "current_dir": str(current_dir),
        "processed_root": str(processed_root),
        "current_clean_tag_row_count": int(current_count),
        "fallback_used": False,
        "fallback_source_dir": None,
        "fallback_clean_tag_row_count": None,
    }

    if current_count > 0:
        return summary

    fallback = find_latest_non_empty_snapshot(
        processed_root=processed_root,
        current_dir=current_dir,
    )

    if fallback is None:
        raise RuntimeError(
            "Current snapshot has zero clean commander-tag rows, and no older "
            "non-empty processed snapshot was found to use as a fallback."
        )

    fallback_dir, fallback_count = fallback

    replace_snapshot_dir(source_dir=fallback_dir, current_dir=current_dir)

    summary["fallback_used"] = True
    summary["fallback_source_dir"] = str(fallback_dir)
    summary["fallback_clean_tag_row_count"] = int(fallback_count)
    summary["reason"] = "current_clean_tag_row_count_is_zero"

    destination_report_path = report_path or current_dir / FALLBACK_REPORT_FILENAME
    write_json(destination_report_path, summary)

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace an empty current processed snapshot with the latest older "
            "non-empty processed snapshot."
        )
    )

    parser.add_argument(
        "--current-dir",
        required=True,
        type=Path,
        help="Current processed snapshot directory, such as data/processed/2026-07-06.",
    )

    parser.add_argument(
        "--processed-root",
        required=True,
        type=Path,
        help="Root directory containing dated processed snapshots.",
    )

    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional path for the fallback report JSON.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    summary = use_previous_processed_if_empty(
        current_dir=args.current_dir,
        processed_root=args.processed_root,
        report_path=args.report_path,
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
