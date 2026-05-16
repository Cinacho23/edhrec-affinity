#!/usr/bin/env python3
"""
copy_latest_data.py

Copies website-ready processed JSON files into:

    frontend/public/data/latest/

The React/Vite frontend can then load the files with paths like:

    /data/latest/analysis_summary.json
    /data/latest/affinity_rows_with_trends.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_WEBSITE_FILES = [
    "analysis_summary.json",
    "trend_summary.json",
    "tag_summary.json",
    "global_leaderboard.json",
    "tag_rankings.json",
    "affinity_rows_with_trends.json",
]

OPTIONAL_WEBSITE_FILES = [
    "commander_scryfall_metadata.json",
    "trend_rows.json",
    "affinity_rows.json",
]


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def copy_json_file(source: Path, destination: Path) -> None:
    load_json(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_manifest(
    processed_dir: Path,
    copied_files: list[str],
    missing_optional_files: list[str],
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processed_dir": str(processed_dir),
        "copied_files": copied_files,
        "missing_optional_files": missing_optional_files,
    }


def copy_latest_data(
    processed_dir: Path,
    frontend_data_dir: Path,
    clean_destination: bool,
) -> dict:
    if not processed_dir.exists():
        raise FileNotFoundError(f"Processed directory does not exist: {processed_dir}")

    if clean_destination and frontend_data_dir.exists():
        shutil.rmtree(frontend_data_dir)

    frontend_data_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []
    missing_optional_files: list[str] = []

    for filename in REQUIRED_WEBSITE_FILES:
        source = processed_dir / filename
        destination = frontend_data_dir / filename

        if not source.exists():
            raise FileNotFoundError(f"Required website data file is missing: {source}")

        copy_json_file(source, destination)
        copied_files.append(filename)

    for filename in OPTIONAL_WEBSITE_FILES:
        source = processed_dir / filename
        destination = frontend_data_dir / filename

        if source.exists():
            copy_json_file(source, destination)
            copied_files.append(filename)
        else:
            missing_optional_files.append(filename)

    manifest = build_manifest(
        processed_dir=processed_dir,
        copied_files=copied_files,
        missing_optional_files=missing_optional_files,
    )

    manifest_path = frontend_data_dir / "site_data_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)

    copied_files.append("site_data_manifest.json")
    manifest["copied_files"] = copied_files

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy processed website JSON files into frontend/public/data/latest."
    )

    parser.add_argument(
        "--processed-dir",
        required=True,
        type=Path,
        help="Processed snapshot directory, such as data/processed/2026-05-14.",
    )

    parser.add_argument(
        "--frontend-data-dir",
        default=Path("frontend/public/data/latest"),
        type=Path,
        help="Frontend static data directory.",
    )

    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete the destination folder before copying.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    manifest = copy_latest_data(
        processed_dir=args.processed_dir,
        frontend_data_dir=args.frontend_data_dir,
        clean_destination=not args.no_clean,
    )

    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()