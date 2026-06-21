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
    "site_manifest.json",
    "summaries/analysis_summary.json",
    "summaries/trend_summary.json",
    "summaries/tag_summary.json",
    "leaderboard/index.json",
    "leaderboard/page_0001.json",
    "tags/index.json",
    "commanders/index.json",
]


def read_json_file(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_json_file(path: Path) -> None:
    read_json_file(path)


def safe_json_filename(value: str) -> str:
    import re

    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip("-")
    return value or "unknown"


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

    tag_index = read_json_file(args.frontend_data_dir / "tags/index.json")
    commander_index = read_json_file(args.frontend_data_dir / "commanders/index.json")
    sets_index_path = args.frontend_data_dir / "sets/index.json"

    if tag_index:
        first_tag_slug = tag_index[0].get("tag_slug") or tag_index[0].get("slug")
        validate_json_file(
            args.frontend_data_dir / "tags" / f"{safe_json_filename(first_tag_slug)}.json"
        )

    if commander_index:
        first_commander_slug = commander_index[0].get("commander_slug")
        validate_json_file(
            args.frontend_data_dir
            / "commanders"
            / f"{safe_json_filename(first_commander_slug)}.json"
        )

    if sets_index_path.exists():
        set_index = read_json_file(sets_index_path)

        if isinstance(set_index, list) and set_index and isinstance(set_index[0], dict):
            first_set_file = set_index[0].get("file")
            first_set_code = set_index[0].get("set_code") or set_index[0].get("code")

            if first_set_file:
                validate_json_file(args.frontend_data_dir / first_set_file)
            elif first_set_code:
                validate_json_file(
                    args.frontend_data_dir
                    / "sets"
                    / f"{safe_json_filename(first_set_code)}.json"
                )

    print(f"Validated sharded frontend data in {args.frontend_data_dir}.")


if __name__ == "__main__":
    main()
