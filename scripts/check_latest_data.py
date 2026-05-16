#!/usr/bin/env python3
"""
check_latest_data.py

Validates that frontend/public/data/latest contains the JSON files expected
by the React frontend.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_FILES = [
    "analysis_summary.json",
    "trend_summary.json",
    "tag_summary.json",
    "global_leaderboard.json",
    "tag_rankings.json",
    "affinity_rows_with_trends.json",
]


def validate_json_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")

    with path.open("r", encoding="utf-8") as file:
        json.load(file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate frontend/public/data/latest JSON files."
    )

    parser.add_argument(
        "--frontend-data-dir",
        default=Path("frontend/public/data/latest"),
        type=Path,
        help="Frontend static data directory.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    for filename in REQUIRED_FILES:
        validate_json_file(args.frontend_data_dir / filename)

    print(f"Validated {len(REQUIRED_FILES)} required frontend data files.")


if __name__ == "__main__":
    main()