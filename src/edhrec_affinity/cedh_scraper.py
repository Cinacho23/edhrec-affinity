"""
cedh_scraper.py

This file handles the special cEDH scrape phase.

Why this file exists:
- Normal commander tags come from the normal commander JSON payload:
    https://json.edhrec.com/pages/commanders/<commander-slug>.json

- Normal tags are found under:
    payload["panels"]["taglinks"]

- cEDH is different.
  We are treating cEDH as its own tag row, but it does not come from
  panels["taglinks"].

- For cEDH, the cEDH-specific JSON page has its own num_decks_avg value.
  That cEDH num_decks_avg becomes the tag_decks value for the synthetic
  cEDH tag row.

Important:
- total_decks should still represent the commander's normal total deck count.
- tag_decks should represent the number of cEDH decks from the cEDH JSON.
- tag_slug should always be "cedh".
- tag_name should always be "cEDH".
- source_type should be "cedh_filtered_json".

Recommended command:

python3 src/edhrec_affinity/cedh_scraper.py \
  --commander-index data/raw/2026-05-07/commander_index.json \
  --normal-tags data/raw/2026-05-07/commander_tags_raw.jsonl \
  --output-dir data/raw/2026-05-07 \
  --request-delay 0.1
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


# This is the route pattern based on the cEDH special JSON route.
#
# If browser/network inspection later shows EDHREC changed the cEDH JSON path,
# this is the only constant that should need to change.
CEDH_JSON_URL_TEMPLATE = "https://json.edhrec.com/pages/commanders/{commander_slug}/cedh.json"


# Keep a clear user-agent instead of looking like a random anonymous client.
# You can customize the contact/project text later if desired.
USER_AGENT = "edhrec-affinity-analysis/0.1 educational-personal-project"


# Output files for this special scrape phase.
CEDH_ROWS_JSONL = "commander_tags_cedh_raw.jsonl"
CEDH_ROWS_JSON = "commander_tags_cedh_raw.json"
CEDH_STATUS_JSONL = "commander_cedh_status.jsonl"
CEDH_STATUS_JSON = "commander_cedh_status.json"
CEDH_SUMMARY_JSON = "cedh_scrape_summary.json"


JsonDict = dict[str, Any]


def utc_now_iso() -> str:
    """
    Return the current UTC time as an ISO-8601 string.

    Example:
        2026-05-07T18:30:00.000000+00:00
    """
    return datetime.now(timezone.utc).isoformat()


def build_cedh_json_url(commander_slug: str) -> str:
    """
    Build the cEDH JSON URL for a commander slug.

    Example:
        commander_slug = "the-tenth-doctor-rose-tyler"

        returns:
        https://json.edhrec.com/pages/commanders/the-tenth-doctor-rose-tyler/cedh.json
    """
    return CEDH_JSON_URL_TEMPLATE.format(commander_slug=commander_slug)


def coerce_int(value: Any, field_name: str) -> int:
    """
    Convert EDHREC count-like values into integers.

    This supports:
    - int values
    - float values like 3.0
    - string values like "3"
    - string values like "1,234"

    We do not allow booleans because bool is technically a subclass of int
    in Python, but True/False would be invalid deck counts.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer-like value, not bool")

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            raise ValueError(f"{field_name} cannot be blank")
        return int(cleaned)

    raise ValueError(f"{field_name} must be integer-like, got {type(value).__name__}")


def fetch_json(client: httpx.Client, url: str) -> JsonDict:
    """
    Fetch JSON using an existing HTTPX client.

    The reusable client is useful for a large scrape because it can reuse
    connections. raise_for_status() is important because otherwise a 404/500
    response could accidentally be parsed as if it were good data.
    """
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}, got {type(payload).__name__}")

    return payload


def read_json_records(path: Path) -> list[JsonDict]:
    """
    Read a JSON or JSONL file and return a list of dictionaries.

    Supported shapes:
    - .jsonl file with one JSON object per line
    - .json file containing a list of objects
    - .json file containing {"records": [...]}
    - .json file containing {"commanders": [...]}

    The extra dict shapes make this function tolerant if a previous scraper
    saved wrapped JSON instead of a raw list.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix == ".jsonl":
        records: list[JsonDict] = []

        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue

                value = json.loads(line)

                if not isinstance(value, dict):
                    raise ValueError(f"Expected JSON object per line in {path}")

                records.append(value)

        return records

    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("records", "commanders"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise ValueError(f"Unsupported JSON shape in {path}")


def append_jsonl(path: Path, record: JsonDict) -> None:
    """
    Append one JSON object to a JSONL file.

    JSONL is useful for scrapers because each line is a complete record.
    If the scrape stops partway through, the previous complete lines are
    still readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as file:
        json.dump(record, file, ensure_ascii=False)
        file.write("\n")


def write_json_array_from_jsonl(jsonl_path: Path, json_path: Path) -> None:
    """
    Convert a JSONL file into a normal JSON array file.

    The scraper writes JSONL while running, then creates JSON arrays afterward
    because pandas and manual inspection are often easier with regular JSON.
    """
    if not jsonl_path.exists():
        records: list[JsonDict] = []
    else:
        records = read_json_records(jsonl_path)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fallback_commander_name_from_slug(commander_slug: str) -> str:
    """
    Create a simple fallback display name from a slug.

    This is not perfect for every card name, but it is better than a blank
    commander_name. In most cases, the normal tag scrape should already give
    us a better commander_name.
    """
    return commander_slug.replace("-", " ").title()


def load_normal_commander_metadata(normal_tags_path: Path) -> dict[str, JsonDict]:
    """
    Build commander metadata from the normal tag scrape.

    The cEDH row needs:
    - commander_name
    - commander_slug
    - total_decks

    commander_index.json has slugs and URLs, but not always total_decks.
    The normal tag rows already contain total_decks, so this function uses
    commander_tags_raw.jsonl or commander_tags_raw.json as the metadata source.

    Returns:
        {
          "jasmine-boreal-of-the-seven": {
              "commander_name": "Jasmine Boreal of the Seven",
              "total_decks": 5736
          },
          ...
        }
    """
    metadata_by_slug: dict[str, JsonDict] = {}

    for row in read_json_records(normal_tags_path):
        commander_slug = str(row.get("commander_slug", "")).strip()

        if not commander_slug:
            continue

        # Keep the first normal metadata record we see for a commander.
        # All normal tag rows for the same commander should have the same
        # commander_name and total_decks.
        if commander_slug in metadata_by_slug:
            continue

        commander_name = str(row.get("commander_name", "")).strip()
        if not commander_name:
            commander_name = fallback_commander_name_from_slug(commander_slug)

        total_decks = coerce_int(row.get("total_decks"), "total_decks")

        metadata_by_slug[commander_slug] = {
            "commander_name": commander_name,
            "total_decks": total_decks,
        }

    return metadata_by_slug


def parse_cedh_payload(
    payload: JsonDict,
    *,
    commander_slug: str,
    commander_name: str,
    normal_total_decks: int,
    scrape_timestamp: str | None = None,
) -> JsonDict | None:
    """
    Convert a cEDH JSON payload into one normalized commander-tag row.

    This is the key cEDH rule:

        cEDH payload["num_decks_avg"] -> tag_decks

    We intentionally do not read payload["panels"]["taglinks"] here.
    cEDH is treated as its own synthetic tag row.

    Args:
        payload:
            The cEDH-specific JSON payload.
        commander_slug:
            Stable EDHREC commander slug.
        commander_name:
            Display name from the normal tag scrape when available.
        normal_total_decks:
            The commander's normal total deck count from the normal commander
            data, not from the cEDH filtered page.
        scrape_timestamp:
            Optional fixed timestamp for tests. If omitted, use current UTC.

    Returns:
        A normalized commander-tag row, or None if the cEDH deck count is zero.

    Raises:
        ValueError:
            If counts are invalid.
        KeyError:
            If num_decks_avg is missing from the cEDH payload.
    """
    if scrape_timestamp is None:
        scrape_timestamp = utc_now_iso()

    normal_total_decks = coerce_int(normal_total_decks, "normal_total_decks")

    if normal_total_decks <= 0:
        raise ValueError("normal_total_decks must be greater than zero")

    # This is the special cEDH-specific count.
    cedh_decks = coerce_int(payload["num_decks_avg"], "num_decks_avg")

    # If EDHREC exposes the cEDH route but the count is zero, we do not create
    # a commander-tag row. The caller should write a status record instead.
    if cedh_decks <= 0:
        return None

    # A cEDH count should be a subset of total commander decks.
    # If this happens, it likely means the normal total_decks and cEDH payload
    # were scraped from incompatible source windows or the source meaning changed.
    if cedh_decks > normal_total_decks:
        raise ValueError(
            f"cEDH decks ({cedh_decks}) cannot exceed normal total decks ({normal_total_decks})"
        )

    return {
        "commander_name": commander_name,
        "commander_slug": commander_slug,
        "total_decks": normal_total_decks,
        "tag_name": "cEDH",
        "tag_slug": "cedh",
        "tag_decks": cedh_decks,
        "source_type": "cedh_filtered_json",
        "scrape_timestamp": scrape_timestamp,
    }


def make_status_record(
    *,
    commander_slug: str,
    status_type: str,
    reason: str,
    url: str | None = None,
    scrape_timestamp: str | None = None,
    extra: JsonDict | None = None,
) -> JsonDict:
    """
    Create a status record for cEDH scrape outcomes.

    Status records are separate from commander-tag rows. This prevents
    no-cEDH cases from being mislabeled as successful cEDH tag rows.
    """
    if scrape_timestamp is None:
        scrape_timestamp = utc_now_iso()

    record: JsonDict = {
        "commander_slug": commander_slug,
        "url": url,
        "status_type": status_type,
        "reason": reason,
        "scrape_timestamp": scrape_timestamp,
    }

    if extra:
        record.update(extra)

    return record


def load_completed_slugs(output_dir: Path) -> set[str]:
    """
    Read existing cEDH row/status files and return slugs already handled.

    This supports a simple resume mode:
    - if a row was already written, skip it
    - if a no-cEDH or failure status was already written, skip it

    For a clean rerun, delete the cEDH output files or do not use --resume.
    """
    completed: set[str] = set()

    for filename in (CEDH_ROWS_JSONL, CEDH_STATUS_JSONL):
        path = output_dir / filename

        if not path.exists():
            continue

        for record in read_json_records(path):
            commander_slug = str(record.get("commander_slug", "")).strip()
            if commander_slug:
                completed.add(commander_slug)

    return completed


def run_cedh_scrape(
    *,
    commander_index_path: Path,
    normal_tags_path: Path,
    output_dir: Path,
    request_delay: float = 0.1,
    limit: int | None = None,
    resume: bool = False,
) -> JsonDict:
    """
    Run the cEDH special scrape phase.

    Inputs:
    - commander_index_path:
        The commander index from Chat 4.
    - normal_tags_path:
        The normal tag rows from Chat 5. Used for commander_name and total_decks.
    - output_dir:
        Same dated raw snapshot directory.

    Outputs:
    - commander_tags_cedh_raw.jsonl
    - commander_tags_cedh_raw.json
    - commander_cedh_status.jsonl
    - commander_cedh_status.json
    - cedh_scrape_summary.json
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = utc_now_iso()

    commander_records = read_json_records(commander_index_path)
    metadata_by_slug = load_normal_commander_metadata(normal_tags_path)

    if limit is not None:
        commander_records = commander_records[:limit]

    rows_jsonl_path = output_dir / CEDH_ROWS_JSONL
    rows_json_path = output_dir / CEDH_ROWS_JSON
    status_jsonl_path = output_dir / CEDH_STATUS_JSONL
    status_json_path = output_dir / CEDH_STATUS_JSON
    summary_path = output_dir / CEDH_SUMMARY_JSON

    completed_slugs = load_completed_slugs(output_dir) if resume else set()

    attempted_count = 0
    skipped_count = 0
    cedh_row_count = 0
    no_cedh_count = 0
    missing_metadata_count = 0
    error_count = 0

    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True) as client:
        for commander_record in commander_records:
            commander_slug = str(commander_record.get("commander_slug", "")).strip()

            if not commander_slug:
                error_count += 1
                append_jsonl(
                    status_jsonl_path,
                    make_status_record(
                        commander_slug="",
                        status_type="invalid_commander_index_record",
                        reason="Commander index record is missing commander_slug",
                    ),
                )
                continue

            if commander_slug in completed_slugs:
                skipped_count += 1
                continue

            attempted_count += 1
            url = build_cedh_json_url(commander_slug)

            normal_metadata = metadata_by_slug.get(commander_slug)

            if normal_metadata is None:
                missing_metadata_count += 1
                append_jsonl(
                    status_jsonl_path,
                    make_status_record(
                        commander_slug=commander_slug,
                        url=url,
                        status_type="missing_normal_metadata",
                        reason=(
                            "Commander was in commander_index.json, but no normal tag row "
                            "was found for it. Cannot create cEDH row because total_decks "
                            "is unknown."
                        ),
                    ),
                )

                if request_delay > 0:
                    time.sleep(request_delay)

                continue

            try:
                payload = fetch_json(client, url)

                row = parse_cedh_payload(
                    payload,
                    commander_slug=commander_slug,
                    commander_name=str(normal_metadata["commander_name"]),
                    normal_total_decks=coerce_int(
                        normal_metadata["total_decks"],
                        "total_decks",
                    ),
                )

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code

                # Many commanders may simply have no cEDH filtered JSON route.
                # Treat 404 as "no cEDH data", not as a fatal scrape failure.
                if status_code == 404:
                    no_cedh_count += 1
                    append_jsonl(
                        status_jsonl_path,
                        make_status_record(
                            commander_slug=commander_slug,
                            url=url,
                            status_type="no_cedh_route",
                            reason="cEDH JSON route returned 404",
                            extra={"http_status_code": status_code},
                        ),
                    )
                else:
                    error_count += 1
                    append_jsonl(
                        status_jsonl_path,
                        make_status_record(
                            commander_slug=commander_slug,
                            url=url,
                            status_type="http_error",
                            reason=f"HTTP error while fetching cEDH JSON: {exc}",
                            extra={"http_status_code": status_code},
                        ),
                    )

            except httpx.HTTPError as exc:
                error_count += 1
                append_jsonl(
                    status_jsonl_path,
                    make_status_record(
                        commander_slug=commander_slug,
                        url=url,
                        status_type="request_error",
                        reason=f"Request error while fetching cEDH JSON: {exc}",
                    ),
                )

            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                error_count += 1
                append_jsonl(
                    status_jsonl_path,
                    make_status_record(
                        commander_slug=commander_slug,
                        url=url,
                        status_type="parse_error",
                        reason=f"Could not parse cEDH JSON into a cEDH tag row: {exc}",
                    ),
                )

            else:
                if row is None:
                    no_cedh_count += 1
                    append_jsonl(
                        status_jsonl_path,
                        make_status_record(
                            commander_slug=commander_slug,
                            url=url,
                            status_type="no_cedh_decks",
                            reason="cEDH JSON num_decks_avg was zero or less",
                        ),
                    )
                else:
                    cedh_row_count += 1
                    append_jsonl(rows_jsonl_path, row)

                    append_jsonl(
                        status_jsonl_path,
                        make_status_record(
                            commander_slug=commander_slug,
                            url=url,
                            status_type="cedh_row_written",
                            reason="cEDH row was created from cEDH JSON num_decks_avg",
                            extra={"tag_decks": row["tag_decks"]},
                        ),
                    )

            if request_delay > 0:
                time.sleep(request_delay)

    # Keep JSON array exports beside the JSONL files.
    write_json_array_from_jsonl(rows_jsonl_path, rows_json_path)
    write_json_array_from_jsonl(status_jsonl_path, status_json_path)

    finished_at = utc_now_iso()

    summary: JsonDict = {
        "started_at": started_at,
        "finished_at": finished_at,
        "commander_index_path": str(commander_index_path),
        "normal_tags_path": str(normal_tags_path),
        "output_dir": str(output_dir),
        "total_commander_records_in_index": len(read_json_records(commander_index_path)),
        "commander_records_considered_this_run": len(commander_records),
        "attempted_commander_count_this_run": attempted_count,
        "skipped_commander_count_this_run": skipped_count,
        "cedh_row_count_this_run": cedh_row_count,
        "no_cedh_count_this_run": no_cedh_count,
        "missing_normal_metadata_count_this_run": missing_metadata_count,
        "error_count_this_run": error_count,
        "request_delay_seconds": request_delay,
        "resume_enabled": resume,
        "cedh_source_type": "cedh_filtered_json",
        "cedh_count_source": "cedh_json.num_decks_avg",
        "rows_jsonl_path": str(rows_jsonl_path),
        "rows_json_path": str(rows_json_path),
        "status_jsonl_path": str(status_jsonl_path),
        "status_json_path": str(status_json_path),
    }

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return summary


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for manual runs and future automation.
    """
    parser = argparse.ArgumentParser(
        description="Scrape EDHREC cEDH counts as synthetic cEDH commander-tag rows."
    )

    parser.add_argument(
        "--commander-index",
        required=True,
        type=Path,
        help="Path to commander_index.json from Chat 4.",
    )

    parser.add_argument(
        "--normal-tags",
        required=True,
        type=Path,
        help=(
            "Path to commander_tags_raw.jsonl or commander_tags_raw.json from Chat 5. "
            "Used to get commander_name and normal total_decks."
        ),
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Dated raw snapshot output directory, e.g. data/raw/2026-05-07.",
    )

    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.1,
        help="Delay in seconds between cEDH JSON requests. Default: 0.1",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit for test runs, e.g. --limit 25.",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip commanders already present in cEDH row/status output files.",
    )

    return parser.parse_args()


def main() -> None:
    """
    CLI entry point.
    """
    args = parse_args()

    summary = run_cedh_scrape(
        commander_index_path=args.commander_index,
        normal_tags_path=args.normal_tags,
        output_dir=args.output_dir,
        request_delay=args.request_delay,
        limit=args.limit,
        resume=args.resume,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()