"""
discovery.py

Chat 4 - Full Commander Discovery Scraper

This file is responsible for discovering commander pages from EDHREC.

At this stage, we are NOT scraping every commander's tags yet.
That comes in Chat 5.

For now, this module does the following:

1. Fetch the EDHREC commanders sitemap.
2. Parse the XML sitemap and extract commander page URLs.
3. Extract commander slugs from those URLs.
4. Build a clean commander index.
5. Validate the commander index for duplicates and malformed URLs.
6. Save the index and validation report as JSON files.

Expected output files:

data/raw/YYYY-MM-DD/commander_index.json
data/raw/YYYY-MM-DD/commander_index_validation.json

These files become the input for the full commander tag scraper in Chat 5.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx


# EDHREC's commander sitemap. This should be the main discovery source.
COMMANDERS_SITEMAP_URL = "https://edhrec.com/sitemaps/commanders.xml"

# This value tells later pipeline stages where a record came from.
SOURCE_TYPE = "commanders_sitemap"

# A clear user-agent is better than looking like an anonymous script.
# You can make this more specific later if needed.
USER_AGENT = (
    "edhrec-affinity-analysis-learning-project/0.1 "
    "(commander discovery; educational portfolio project)"
)

# A timeout prevents the script from hanging forever if the request stalls.
DEFAULT_TIMEOUT_SECONDS = 20.0


# Type alias for readability.
# Each commander index record is represented as a dictionary for now.
CommanderIndexRecord = dict[str, Any]


def utc_now_iso() -> str:
    """
    Return the current UTC time in a clean ISO-8601 string.

    Example:
    2026-05-07T12:34:56Z

    UTC is useful for automated pipelines because it avoids local timezone
    ambiguity when GitHub Actions or another service runs the scraper.
    """

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def utc_today_string() -> str:
    """
    Return today's UTC date as YYYY-MM-DD.

    This is used for dated raw data snapshot folders.
    """

    return datetime.now(timezone.utc).date().isoformat()


def fetch_text(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = USER_AGENT,
) -> str:
    """
    Fetch a URL and return the response body as text.

    Parameters:
        url:
            The URL to fetch.

        timeout_seconds:
            Maximum time to wait for network activity.

        user_agent:
            User-Agent header sent with the request.

    Returns:
        The response body as a string.

    Raises:
        httpx.HTTPStatusError:
            If the server returns an HTTP error status such as 404 or 500.

        httpx.RequestError:
            If a network-level problem occurs.

    Important:
        response.raise_for_status() must include parentheses.
        Without parentheses, the status check is not actually executed.
    """

    headers = {"User-Agent": user_agent}

    with httpx.Client(
        timeout=timeout_seconds,
        headers=headers,
        follow_redirects=True,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def parse_sitemap_locations(xml_text: str) -> list[str]:
    """
    Parse sitemap XML and return all <loc> values.

    Sitemaps are XML files. Many XML files use namespaces, which can make tags
    look like this internally:

        {http://www.sitemaps.org/schemas/sitemap/0.9}loc

    To keep this beginner-friendly, we strip off the namespace and only check
    whether the local tag name is "loc".

    Parameters:
        xml_text:
            The raw XML text from the sitemap.

    Returns:
        A list of URL strings found inside <loc> elements.

    Raises:
        ValueError:
            If the XML cannot be parsed.
    """

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("Could not parse sitemap XML.") from exc

    locations: list[str] = []

    for element in root.iter():
        # Remove XML namespace if present.
        # Example:
        # "{namespace}loc" becomes "loc"
        tag_name = element.tag.rsplit("}", maxsplit=1)[-1]

        if tag_name == "loc" and element.text:
            location = element.text.strip()

            if location:
                locations.append(location)

    return locations


def extract_commander_slug(url: str) -> str | None:
    """
    Extract the commander slug from an EDHREC commander URL.

    Example:
        https://edhrec.com/commanders/jasmine-boreal-of-the-seven

    Returns:
        jasmine-boreal-of-the-seven

    This function intentionally rejects filtered commander URLs such as:

        https://edhrec.com/commanders/example-commander/cedh

    because Chat 4 is only supposed to discover base commander pages.
    Filtered routes such as /cedh are handled separately later.
    """

    parsed = urlparse(url)

    # Accept the normal EDHREC host. This keeps unrelated URLs out of the index.
    if parsed.netloc not in {"edhrec.com", "www.edhrec.com"}:
        return None

    # Split the path into useful pieces.
    # "/commanders/jasmine-boreal-of-the-seven"
    # becomes ["commanders", "jasmine-boreal-of-the-seven"]
    path_parts = [part for part in parsed.path.split("/") if part]

    # A base commander page should have exactly two path parts:
    # ["commanders", "<commander-slug>"]
    if len(path_parts) != 2:
        return None

    if path_parts[0] != "commanders":
        return None

    slug = path_parts[1].strip()

    if not slug:
        return None

    return slug


def build_commander_index_records(
    urls: list[str],
    *,
    discovered_at: str | None = None,
) -> list[CommanderIndexRecord]:
    """
    Convert commander URLs into commander index records.

    Invalid URLs are skipped here. They are still reported separately by
    validate_commander_index(), which should receive the original URL list.

    Parameters:
        urls:
            URLs extracted from the sitemap.

        discovered_at:
            Optional timestamp. Tests can pass a fixed timestamp so expected
            outputs are stable.

    Returns:
        A list of commander index records.
    """

    timestamp = discovered_at or utc_now_iso()
    records: list[CommanderIndexRecord] = []

    for url in urls:
        slug = extract_commander_slug(url)

        if slug is None:
            continue

        records.append(
            {
                "commander_slug": slug,
                "commander_url": url,
                "source_type": SOURCE_TYPE,
                "discovered_at": timestamp,
            }
        )

    return records


def deduplicate_commander_records(
    records: list[CommanderIndexRecord],
) -> list[CommanderIndexRecord]:
    """
    Remove duplicate commander records by commander_slug.

    If the same slug appears more than once, we keep the first occurrence.

    Why keep the first?
    The sitemap order is already a reasonable source order. Keeping the first
    occurrence preserves that order and avoids unnecessary sorting surprises.
    """

    seen_slugs: set[str] = set()
    unique_records: list[CommanderIndexRecord] = []

    for record in records:
        slug = record["commander_slug"]

        if slug in seen_slugs:
            continue

        seen_slugs.add(slug)
        unique_records.append(record)

    return unique_records


def validate_commander_index(
    *,
    sitemap_urls: list[str],
    records_before_deduplication: list[CommanderIndexRecord],
    records_after_deduplication: list[CommanderIndexRecord] | None = None,
    sitemap_url: str = COMMANDERS_SITEMAP_URL,
) -> dict[str, Any]:
    """
    Build a validation report for the commander index.

    This report is useful because Chat 5 depends on this commander list.
    If the discovery stage has duplicates or malformed URLs, the full scraper
    would inherit those problems.

    Parameters:
        sitemap_urls:
            Every URL found in the sitemap.

        records_before_deduplication:
            Commander records built before duplicate removal.

        records_after_deduplication:
            Commander records after duplicate removal.

        sitemap_url:
            The sitemap source URL.

    Returns:
        A JSON-serializable validation report.
    """

    if records_after_deduplication is None:
        records_after_deduplication = deduplicate_commander_records(
            records_before_deduplication
        )

    slugs_before = [
        record["commander_slug"] for record in records_before_deduplication
    ]

    slug_counts = Counter(slugs_before)

    duplicate_slugs = sorted(
        slug for slug, count in slug_counts.items() if count > 1
    )

    duplicate_slug_details = [
        {"commander_slug": slug, "count": slug_counts[slug]}
        for slug in duplicate_slugs
    ]

    invalid_urls = [
        url for url in sitemap_urls if extract_commander_slug(url) is None
    ]

    return {
        "sitemap_url": sitemap_url,
        "source_type": SOURCE_TYPE,
        "validated_at": utc_now_iso(),
        "total_locations_found": len(sitemap_urls),
        "total_commander_records_before_deduplication": len(
            records_before_deduplication
        ),
        "total_commander_records_after_deduplication": len(
            records_after_deduplication
        ),
        "unique_slugs": len({record["commander_slug"] for record in records_before_deduplication}),
        "duplicate_slug_count": len(duplicate_slugs),
        "duplicate_slugs": duplicate_slugs,
        "duplicate_slug_details": duplicate_slug_details,
        "invalid_url_count": len(invalid_urls),
        "invalid_urls": invalid_urls,
        "missing_slug_count": sum(1 for slug in slugs_before if not slug),
    }


def save_json(data: Any, output_path: Path) -> None:
    """
    Save data as a pretty-printed JSON file.

    Parameters:
        data:
            Any JSON-serializable Python object.

        output_path:
            Where to write the JSON file.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def write_discovery_outputs(
    *,
    output_dir: Path,
    commander_records: list[CommanderIndexRecord],
    validation_report: dict[str, Any],
) -> dict[str, Path]:
    """
    Write the commander index and validation report to disk.

    Returns:
        A dictionary containing the output paths.
    """

    commander_index_path = output_dir / "commander_index.json"
    validation_report_path = output_dir / "commander_index_validation.json"

    save_json(commander_records, commander_index_path)
    save_json(validation_report, validation_report_path)

    return {
        "commander_index": commander_index_path,
        "validation_report": validation_report_path,
    }


def discover_commander_index(
    *,
    sitemap_url: str = COMMANDERS_SITEMAP_URL,
) -> tuple[list[CommanderIndexRecord], dict[str, Any]]:
    """
    Run the full discovery process in memory.

    This function:
    1. Fetches the sitemap.
    2. Parses URLs from the sitemap.
    3. Builds commander records.
    4. Deduplicates commander records.
    5. Builds a validation report.

    It does not write files. File output is handled separately by
    write_discovery_outputs().
    """

    xml_text = fetch_text(sitemap_url)
    sitemap_urls = parse_sitemap_locations(xml_text)

    records_before_deduplication = build_commander_index_records(sitemap_urls)
    records_after_deduplication = deduplicate_commander_records(
        records_before_deduplication
    )

    validation_report = validate_commander_index(
        sitemap_urls=sitemap_urls,
        records_before_deduplication=records_before_deduplication,
        records_after_deduplication=records_after_deduplication,
        sitemap_url=sitemap_url,
    )

    return records_after_deduplication, validation_report


def main() -> int:
    """
    Command-line entry point.

    Example usage from the project root:

        python -m edhrec_affinity.discovery

    Optional custom output directory:

        python -m edhrec_affinity.discovery --output-dir data/raw/test-run
    """

    parser = argparse.ArgumentParser(
        description="Discover EDHREC commander URLs/slugs from commanders.xml."
    )

    parser.add_argument(
        "--sitemap-url",
        default=COMMANDERS_SITEMAP_URL,
        help="Commander sitemap URL to fetch.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "raw" / utc_today_string(),
        help="Directory where discovery JSON files will be written.",
    )

    args = parser.parse_args()

    commander_records, validation_report = discover_commander_index(
        sitemap_url=args.sitemap_url
    )

    output_paths = write_discovery_outputs(
        output_dir=args.output_dir,
        commander_records=commander_records,
        validation_report=validation_report,
    )

    # Print a short machine-readable summary for terminal use or GitHub Actions logs.
    summary = {
        "commander_records_written": len(commander_records),
        "commander_index_path": str(output_paths["commander_index"]),
        "validation_report_path": str(output_paths["validation_report"]),
        "duplicate_slug_count": validation_report["duplicate_slug_count"],
        "invalid_url_count": validation_report["invalid_url_count"],
    }

    print(json.dumps(summary, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())