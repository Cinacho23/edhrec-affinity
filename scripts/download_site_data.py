#!/usr/bin/env python3
"""
Download the deployed sharded site data into frontend/public/data/latest.

This is intended for local frontend development. It avoids running the full
scrape pipeline when you only need a current JSON dataset for Vite.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://cinacho23.github.io/edhrec-affinity/data/latest/"
DEFAULT_OUTPUT_DIR = Path("frontend/public/data/latest")
MAX_WORKERS = 8
MAX_ATTEMPTS = 4


def safe_json_filename(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip("-")
    return value or "unknown"


def fetch_json(base_url: str, relative_path: str) -> object:
    url = urljoin(base_url, relative_path)
    request = Request(url, headers={"User-Agent": "edhrec-affinity-local-dev"})

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read()
            break
        except HTTPError as error:
            if attempt == MAX_ATTEMPTS or error.code < 500:
                raise RuntimeError(f"HTTP {error.code} while downloading {url}") from error

            time.sleep(attempt * 1.5)
        except URLError as error:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"Could not download {url}: {error.reason}") from error

            time.sleep(attempt * 1.5)

    text = raw.decode("utf-8")

    if text.lstrip().startswith("<"):
        raise RuntimeError(f"Expected JSON at {url}, but received HTML.")

    return json.loads(text)


def is_not_found_error(error: Exception) -> bool:
    return "HTTP 404" in str(error)


def has_valid_local_json(output_dir: Path, relative_path: str) -> bool:
    destination = output_dir / relative_path

    if not destination.exists():
        return False

    try:
        json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    return True


def write_json(output_dir: Path, relative_path: str, data: object) -> None:
    destination = output_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def download_one(base_url: str, output_dir: Path, relative_path: str) -> str:
    if has_valid_local_json(output_dir, relative_path):
        return relative_path

    data = fetch_json(base_url, relative_path)
    write_json(output_dir, relative_path, data)
    return relative_path


def build_download_list(base_url: str, output_dir: Path) -> list[str]:
    manifest = fetch_json(base_url, "site_manifest.json")
    write_json(output_dir, "site_manifest.json", manifest)

    paths = set(manifest.get("summary_files", {}).values())

    leaderboard_index = fetch_json(base_url, "leaderboard/index.json")
    write_json(output_dir, "leaderboard/index.json", leaderboard_index)

    for page in leaderboard_index.get("pages", []):
        page_file = page.get("file")

        if page_file:
            paths.add(page_file)

    commander_index = fetch_json(base_url, "commanders/index.json")
    write_json(output_dir, "commanders/index.json", commander_index)

    for commander in commander_index:
        commander_slug = commander.get("commander_slug")

        if commander_slug:
            paths.add(f"commanders/{safe_json_filename(commander_slug)}.json")

    tag_index = fetch_json(base_url, "tags/index.json")
    write_json(output_dir, "tags/index.json", tag_index)

    for tag in tag_index:
        tag_slug = tag.get("tag_slug") or tag.get("slug")

        if tag_slug:
            paths.add(f"tags/{safe_json_filename(tag_slug)}.json")

    try:
        set_index = fetch_json(base_url, "sets/index.json")
    except RuntimeError as error:
        if not is_not_found_error(error):
            raise
        set_index = []
    else:
        write_json(output_dir, "sets/index.json", set_index)

    if not isinstance(set_index, list):
        set_index = []

    for set_info in set_index:
        if not isinstance(set_info, dict):
            continue

        set_file = set_info.get("file")
        set_code = set_info.get("set_code") or set_info.get("code")

        if set_file:
            paths.add(set_file)
        elif set_code:
            paths.add(f"sets/{safe_json_filename(set_code)}.json")

    return sorted(paths)


def download_site_data(base_url: str, output_dir: Path) -> None:
    if not base_url.endswith("/"):
        base_url = f"{base_url}/"

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = build_download_list(base_url, output_dir)

    print(f"Downloading {len(paths)} JSON files into {output_dir}...")

    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(download_one, base_url, output_dir, path): path
            for path in paths
        }

        for future in as_completed(futures):
            path = futures[future]

            try:
                future.result()
            except Exception as error:
                raise RuntimeError(f"Failed while downloading {path}: {error}") from error

            completed += 1

            if completed % 250 == 0 or completed == len(paths):
                print(f"Downloaded {completed}/{len(paths)} files")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download deployed sharded site JSON for local Vite dev."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        download_site_data(args.base_url, args.output_dir)
    except Exception as error:
        print(error, file=sys.stderr)
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
