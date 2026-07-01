"""
tag_scraper.py

Chat 5 - Complete Tag Scraper

Purpose:
- Read the commander_index.json file created by Chat 4.
- Loop through every discovered commander slug.
- Fetch each commander's EDHREC JSON payload.
- Parse total deck count and complete normal tag data from panels["taglinks"].
- Write raw commander-tag rows.
- Write a failure log.
- Support basic resume behavior.

Important project decision:
Normal commander tags should be collected from EDHREC's JSON payloads,
not from visible HTML. The visible page can omit tags, while the JSON
payload contains the complete normal tag list for the commander.

This module does NOT compute z-scores, ranks, percentiles, or trends.
That belongs to later analysis pipeline stages.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

from edhrec_affinity.models import CommanderTagRow, utc_now_iso


# EDHREC commander JSON files follow this pattern, based on the commander slug.
# Example:
# https://json.edhrec.com/pages/commanders/jasmine-boreal-of-the-seven.json
COMMANDER_JSON_BASE_URL = "https://json.edhrec.com/pages/commanders"


# A clear User-Agent is more responsible than looking like an anonymous bot.
# Later, you can update this with a GitHub repo URL or contact email if desired.
DEFAULT_USER_AGENT = (
    "edhrec-affinity-analysis/0.1 "
    "(personal educational research project; respectful rate-limited scraper)"
)


# A small delay helps avoid hitting EDHREC too aggressively.
# You can tune this later after small test runs.
DEFAULT_REQUEST_DELAY_SECONDS = 0.1


# Default output filenames for Chat 5.
TAGS_JSONL_FILENAME = "commander_tags_raw.jsonl"
FAILURES_JSONL_FILENAME = "commander_tag_failures.jsonl"
TAGS_JSON_FILENAME = "commander_tags_raw.json"
FAILURES_JSON_FILENAME = "commander_tag_failures.json"
SUMMARY_JSON_FILENAME = "tag_scrape_summary.json"


def build_commander_json_url(commander_slug: str) -> str:
    """
    Build the EDHREC JSON URL for a commander slug.

    Keeping this in one function is useful because the URL pattern is an
    external assumption. If EDHREC changes the JSON path later, this is the
    one place to update.

    Example:
        build_commander_json_url("jasmine-boreal-of-the-seven")
        -> "https://json.edhrec.com/pages/commanders/jasmine-boreal-of-the-seven.json"
    """
    clean_slug = commander_slug.strip().strip("/")
    return f"{COMMANDER_JSON_BASE_URL}/{clean_slug}.json"


def make_client() -> httpx.Client:
    """
    Create a reusable HTTP client.

    For a full scrape, this is better than calling httpx.get() repeatedly
    because the client can reuse connections.
    """
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=20.0,
        follow_redirects=True,
    )


def fetch_json(client: httpx.Client, url: str) -> dict[str, Any]:
    """
    Fetch a JSON URL and return the decoded payload.

    Important:
    response.raise_for_status() must be called with parentheses.
    Without parentheses, HTTP errors are not actually raised.
    """
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise TypeError(f"Expected JSON object from {url}, got {type(payload).__name__}")

    return payload


def load_commander_index(commander_index_path: Path) -> list[dict[str, Any]]:
    """
    Load the commander_index.json file created by Chat 4.

    Expected rough shape:
    [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00Z"
        },
        ...
    ]

    The only required field for this scraper is commander_slug.
    """
    with commander_index_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise TypeError(
            f"Expected {commander_index_path} to contain a JSON list, "
            f"got {type(data).__name__}"
        )

    records: list[dict[str, Any]] = []

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise TypeError(
                f"Expected commander index item #{index} to be an object, "
                f"got {type(item).__name__}"
            )

        if "commander_slug" not in item:
            raise KeyError(f"Commander index item #{index} is missing 'commander_slug'")

        records.append(item)

    return records


def slug_to_fallback_name(commander_slug: str) -> str:
    """
    Convert a slug into a readable fallback name.

    This is only a fallback. The ideal commander name should come from the
    commander JSON payload or from future cleaned commander metadata.
    """
    return commander_slug.replace("-", " ").title()


def extract_commander_name(
    payload: dict[str, Any],
    commander_slug: str,
    commander_record: dict[str, Any] | None = None,
) -> str:
    """
    Try to extract a commander name from available sources.

    Chat 4's commander index intentionally focuses on slugs and URLs, so it
    may not include a commander_name field yet. EDHREC JSON structures can
    also change, so this function is deliberately defensive.

    Priority:
    1. commander_record["commander_name"], if present
    2. payload["name"], if present
    3. payload["header"]["name"] or payload["header"]["title"], if present
    4. payload["header"], if it is a string
    5. slug-derived fallback name
    """
    if commander_record is not None:
        record_name = commander_record.get("commander_name")
        if isinstance(record_name, str) and record_name.strip():
            return record_name.strip()

    direct_name = payload.get("name")
    if isinstance(direct_name, str) and direct_name.strip():
        return direct_name.strip()

    header = payload.get("header")

    if isinstance(header, dict):
        for key in ("name", "title", "commander_name"):
            header_name = header.get(key)
            if isinstance(header_name, str) and header_name.strip():
                return header_name.strip()

    if isinstance(header, str) and header.strip():
        return header.strip()

    return slug_to_fallback_name(commander_slug)


def parse_commander_payload(
    payload: dict[str, Any],
    commander_slug: str,
    scrape_timestamp: str,
    commander_record: dict[str, Any] | None = None,
) -> list[CommanderTagRow]:
    """
    Parse one commander's JSON payload into normalized commander-tag rows.

    Confirmed normal tag path:
        payload["panels"]["taglinks"]

    Each tag object should contain:
        count -> tag_decks
        slug  -> tag_slug
        value -> tag_name

    Returns:
        A list of CommanderTagRow Pydantic models.

    Raises:
        KeyError, TypeError, ValueError, or Pydantic validation errors when
        the payload does not match expectations.
    """
    total_decks = int(payload["num_decks_avg"])

    panels = payload["panels"]
    if not isinstance(panels, dict):
        raise TypeError("Expected payload['panels'] to be a dictionary")

    taglinks = panels["taglinks"]
    if not isinstance(taglinks, list):
        raise TypeError("Expected payload['panels']['taglinks'] to be a list")

    commander_name = extract_commander_name(
        payload=payload,
        commander_slug=commander_slug,
        commander_record=commander_record,
    )

    rows: list[CommanderTagRow] = []

    for tag in taglinks:
        if not isinstance(tag, dict):
            raise TypeError(
                f"Expected each taglinks item to be a dictionary, got {type(tag).__name__}"
            )

        row = CommanderTagRow(
            commander_name=commander_name,
            commander_slug=commander_slug,
            total_decks=total_decks,
            tag_name=tag["value"],
            tag_slug=tag["slug"],
            tag_decks=int(tag["count"]),
            source_type="commander_json",
            scrape_timestamp=scrape_timestamp,
        )

        rows.append(row)

    return rows


def model_to_dict(model: Any) -> dict[str, Any]:
    """
    Convert a Pydantic model to a normal dictionary.

    Pydantic v2 uses model_dump().
    Pydantic v1 uses dict().

    This helper makes the scraper more tolerant if your environment changes.
    """
    if hasattr(model, "model_dump"):
        return model.model_dump()

    if hasattr(model, "dict"):
        return model.dict()

    raise TypeError(f"Object is not a supported Pydantic model: {type(model).__name__}")


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    """
    Append one JSON object as one line to a JSONL file.

    JSONL is useful for scraping because progress is written incrementally.
    If the scrape crashes halfway through, previous successful rows remain
    on disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as file:
        json.dump(item, file, ensure_ascii=False)
        file.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """
    Read a JSONL file into a list of dictionaries.

    Missing files return an empty list. This makes resume behavior simpler.
    """
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()

            if not stripped:
                continue

            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL line in {path} at line {line_number}"
                ) from exc

            if not isinstance(item, dict):
                raise TypeError(
                    f"Expected JSONL line in {path} at line {line_number} "
                    f"to decode to an object"
                )

            rows.append(item)

    return rows


def write_json(path: Path, data: Any) -> None:
    """
    Write JSON data with readable formatting.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_completed_slugs(tags_jsonl_path: Path) -> set[str]:
    """
    Determine which commander slugs already have successful tag rows.

    This enables basic resume behavior:
    - If a commander already produced at least one tag row, skip it.
    - Failed commanders are not treated as completed, so rerunning can retry them.
    """
    completed: set[str] = set()

    for row in read_jsonl(tags_jsonl_path):
        slug = row.get("commander_slug")

        if isinstance(slug, str) and slug.strip():
            completed.add(slug)

    return completed


def build_failure_record(
    commander_slug: str,
    url: str,
    error: BaseException,
    scrape_timestamp: str,
) -> dict[str, Any]:
    """
    Build a structured failure record.

    Failure logs matter because full scrapes should not stop just because
    one commander fails. Chat 6 can later inspect these failures during
    validation.
    """
    return {
        "commander_slug": commander_slug,
        "url": url,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "scrape_timestamp": scrape_timestamp,
    }


def scrape_one_commander(
    client: httpx.Client,
    commander_record: dict[str, Any],
    scrape_timestamp: str,
) -> list[CommanderTagRow]:
    """
    Fetch and parse one commander.

    The commander_record comes from commander_index.json.
    """
    commander_slug = str(commander_record["commander_slug"])
    url = build_commander_json_url(commander_slug)

    payload = fetch_json(client, url)

    return parse_commander_payload(
        payload=payload,
        commander_slug=commander_slug,
        scrape_timestamp=scrape_timestamp,
        commander_record=commander_record,
    )


def scrape_all_commander_tags(
    commander_index_path: Path,
    output_dir: Path,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    resume: bool = True,
    client_factory: Callable[[], httpx.Client] = make_client,
) -> dict[str, Any]:
    """
    Main Chat 5 scraping workflow.

    Steps:
    1. Load commander_index.json from Chat 4.
    2. Open an HTTP client.
    3. For each commander:
       - skip if already completed and resume=True
       - fetch commander JSON
       - parse tag rows
       - append each row to commander_tags_raw.jsonl
       - log failures to commander_tag_failures.jsonl
    4. Export JSON array versions for easier later processing.
    5. Write a summary JSON file.

    Returns:
        The summary dictionary.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    tags_jsonl_path = output_dir / TAGS_JSONL_FILENAME
    failures_jsonl_path = output_dir / FAILURES_JSONL_FILENAME
    tags_json_path = output_dir / TAGS_JSON_FILENAME
    failures_json_path = output_dir / FAILURES_JSON_FILENAME
    summary_json_path = output_dir / SUMMARY_JSON_FILENAME
    tags_jsonl_path.touch(exist_ok=True)
    failures_jsonl_path.touch(exist_ok=True)

    started_at = utc_now_iso()
    scrape_timestamp = started_at

    commander_records = load_commander_index(commander_index_path)

    completed_slugs = load_completed_slugs(tags_jsonl_path) if resume else set()

    attempted_count = 0
    skipped_count = 0
    successful_commander_count = 0
    failed_commander_count = 0
    new_tag_row_count = 0

    with client_factory() as client:
        for commander_record in commander_records:
            commander_slug = str(commander_record["commander_slug"])

            if resume and commander_slug in completed_slugs:
                skipped_count += 1
                continue

            attempted_count += 1
            url = build_commander_json_url(commander_slug)

            try:
                rows = scrape_one_commander(
                    client=client,
                    commander_record=commander_record,
                    scrape_timestamp=scrape_timestamp,
                )

                for row in rows:
                    append_jsonl(tags_jsonl_path, model_to_dict(row))

                successful_commander_count += 1
                new_tag_row_count += len(rows)

            except Exception as error:
                failure = build_failure_record(
                    commander_slug=commander_slug,
                    url=url,
                    error=error,
                    scrape_timestamp=scrape_timestamp,
                )

                append_jsonl(failures_jsonl_path, failure)
                failed_commander_count += 1

            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)

    # Export normal JSON arrays after the scrape. The JSONL files are best for
    # resume safety, while the JSON arrays are easier for Chat 6 / pandas work.
    all_tag_rows = read_jsonl(tags_jsonl_path)
    all_failures = read_jsonl(failures_jsonl_path)

    write_json(tags_json_path, all_tag_rows)
    write_json(failures_json_path, all_failures)

    finished_at = utc_now_iso()

    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "commander_index_path": str(commander_index_path),
        "output_dir": str(output_dir),
        "total_commander_records_in_index": len(commander_records),
        "attempted_commander_count": attempted_count,
        "skipped_commander_count": skipped_count,
        "successful_commander_count_this_run": successful_commander_count,
        "failed_commander_count_this_run": failed_commander_count,
        "new_tag_row_count_this_run": new_tag_row_count,
        "total_tag_rows_in_output": len(all_tag_rows),
        "total_failures_in_output": len(all_failures),
        "resume_enabled": resume,
        "request_delay_seconds": request_delay_seconds,
        "normal_tag_source_type": "commander_json",
        "notes": (
            "This Chat 5 scraper collects normal commander tag data only. "
            "cEDH remains a special route to add separately."
        ),
    }

    write_json(summary_json_path, summary)

    return summary


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Example:
        python -m edhrec_affinity.tag_scraper \\
          --commander-index data/raw/2026-05-07/commander_index.json \\
          --output-dir data/raw/2026-05-07
    """
    parser = argparse.ArgumentParser(
        description="Scrape complete normal EDHREC commander tag data from commander JSON payloads."
    )

    parser.add_argument(
        "--commander-index",
        required=True,
        type=Path,
        help="Path to commander_index.json produced by Chat 4.",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory where raw tag rows, failures, and summary files will be written.",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help=f"Delay in seconds between commander requests. Default: {DEFAULT_REQUEST_DELAY_SECONDS}",
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Disable resume behavior and attempt every commander in the index.",
    )

    return parser.parse_args()


def main() -> None:
    """
    Command-line entry point.
    """
    args = parse_args()

    summary = scrape_all_commander_tags(
        commander_index_path=args.commander_index,
        output_dir=args.output_dir,
        request_delay_seconds=args.delay,
        resume=not args.no_resume,
    )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
