"""
test_cleaning.py

Tests for Chat 6 data cleaning and validation.

These tests use fake local files only.
They do not contact EDHREC.

The purpose is to verify that:
- normal commander tag rows are accepted
- cEDH rows are accepted
- bad count relationships are rejected
- duplicate rows are handled
- unknown commander slugs are rejected
- expected output files are created
"""

from __future__ import annotations

import json
from pathlib import Path

from edhrec_affinity import cleaning


def write_json(path: Path, records: list[dict]) -> None:
    """
    Write records as a JSON array.
    """
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_jsonl(path: Path, records: list[dict]) -> None:
    """
    Write records as JSONL.
    """
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            json.dump(record, file, ensure_ascii=False)
            file.write("\n")


def read_json(path: Path):
    """
    Read JSON from a file.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def test_read_json_records_falls_back_to_json_for_missing_jsonl(tmp_path: Path) -> None:
    missing_jsonl_path = tmp_path / "commander_tags_raw.jsonl"
    json_path = tmp_path / "commander_tags_raw.json"
    records = [
        {
            "commander_name": "Jasmine Boreal of the Seven",
            "commander_slug": "jasmine-boreal-of-the-seven",
            "total_decks": 5736,
            "tag_name": "Vanilla",
            "tag_slug": "vanilla",
            "tag_decks": 214,
            "source_type": "commander_json",
            "scrape_timestamp": "2026-05-07T00:00:00+00:00",
        }
    ]

    write_json(json_path, records)

    assert cleaning.read_json_records(missing_jsonl_path) == records


def make_commander_index() -> list[dict]:
    """
    Fake commander index records.

    These mimic the shape produced by Chat 4 discovery.
    """
    return [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00+00:00",
        },
        {
            "commander_slug": "the-tenth-doctor-rose-tyler",
            "commander_url": "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00+00:00",
        },
    ]


def test_run_cleaning_combines_normal_and_cedh_rows(tmp_path: Path) -> None:
    """
    The cleaner should combine normal tag rows and cEDH tag rows into one
    commander_tags_clean.json output.
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    cedh_tags_path = tmp_path / "commander_tags_cedh_raw.jsonl"
    cedh_status_path = tmp_path / "commander_cedh_status.jsonl"
    output_dir = tmp_path / "processed"

    write_json(commander_index_path, make_commander_index())

    write_jsonl(
        normal_tags_path,
        [
            {
                "commander_name": "Jasmine Boreal of the Seven",
                "commander_slug": "jasmine-boreal-of-the-seven",
                "total_decks": 5736,
                "tag_name": "Vanilla",
                "tag_slug": "vanilla",
                "tag_decks": 214,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            },
            {
                "commander_name": "The Tenth Doctor // Rose Tyler",
                "commander_slug": "the-tenth-doctor-rose-tyler",
                "total_decks": 3429,
                "tag_name": "Time Counters",
                "tag_slug": "time-counters",
                "tag_decks": 100,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            },
        ],
    )

    write_jsonl(
        cedh_tags_path,
        [
            {
                "commander_name": "The Tenth Doctor // Rose Tyler",
                "commander_slug": "the-tenth-doctor-rose-tyler",
                "total_decks": 3429,
                "tag_name": "cEDH",
                "tag_slug": "cedh",
                "tag_decks": 3,
                "source_type": "cedh_filtered_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    write_jsonl(
        cedh_status_path,
        [
            {
                "commander_slug": "the-tenth-doctor-rose-tyler",
                "status_type": "cedh_row_written",
                "reason": "cEDH row was created",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    report = cleaning.run_cleaning(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        cedh_tags_path=cedh_tags_path,
        cedh_status_path=cedh_status_path,
        output_dir=output_dir,
    )

    clean_tags = read_json(output_dir / cleaning.COMMANDER_TAGS_CLEAN_JSON)
    commanders = read_json(output_dir / cleaning.COMMANDERS_CLEAN_JSON)
    tags = read_json(output_dir / cleaning.TAGS_CLEAN_JSON)

    assert report["normal_input_row_count"] == 2
    assert report["cedh_input_row_count"] == 1
    assert report["clean_tag_row_count"] == 3
    assert report["invalid_row_count"] == 0

    cedh_rows = [row for row in clean_tags if row["source_type"] == "cedh_filtered_json"]

    assert len(cedh_rows) == 1
    assert cedh_rows[0]["tag_slug"] == "cedh"
    assert cedh_rows[0]["tag_name"] == "cEDH"
    assert cedh_rows[0]["tag_decks"] == 3

    tenth_doctor = next(
        row for row in commanders if row["commander_slug"] == "the-tenth-doctor-rose-tyler"
    )

    assert tenth_doctor["has_cedh_tag"] is True
    assert tenth_doctor["cedh_decks"] == 3
    assert tenth_doctor["tag_count"] == 2

    tag_slugs = {row["tag_slug"] for row in tags}
    assert tag_slugs == {"vanilla", "time-counters", "cedh"}


def test_cleaning_rejects_tag_decks_greater_than_total_decks(tmp_path: Path) -> None:
    """
    A row where tag_decks > total_decks should be invalid.

    This matters because Chat 7 will calculate:
        tag_decks / total_decks
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "processed"

    write_json(
        commander_index_path,
        [
            {
                "commander_slug": "example-commander",
                "commander_url": "https://edhrec.com/commanders/example-commander",
                "source_type": "commanders_sitemap",
                "discovered_at": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    write_jsonl(
        normal_tags_path,
        [
            {
                "commander_name": "Example Commander",
                "commander_slug": "example-commander",
                "total_decks": 100,
                "tag_name": "Tokens",
                "tag_slug": "tokens",
                "tag_decks": 150,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    report = cleaning.run_cleaning(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
    )

    clean_tags = read_json(output_dir / cleaning.COMMANDER_TAGS_CLEAN_JSON)
    invalid_rows = read_json(output_dir / cleaning.INVALID_ROWS_JSON)

    assert report["clean_tag_row_count"] == 0
    assert report["invalid_row_count"] == 1
    assert clean_tags == []
    assert invalid_rows[0]["reason_type"] == "schema_validation_error"
    assert "cannot exceed total_decks" in invalid_rows[0]["reason"]


def test_cleaning_drops_exact_duplicate_rows(tmp_path: Path) -> None:
    """
    Exact duplicate rows should be dropped because they add no information.
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "processed"

    write_json(
        commander_index_path,
        [
            {
                "commander_slug": "example-commander",
                "commander_url": "https://edhrec.com/commanders/example-commander",
                "source_type": "commanders_sitemap",
                "discovered_at": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    duplicate_row = {
        "commander_name": "Example Commander",
        "commander_slug": "example-commander",
        "total_decks": 100,
        "tag_name": "Tokens",
        "tag_slug": "tokens",
        "tag_decks": 25,
        "source_type": "commander_json",
        "scrape_timestamp": "2026-05-07T00:00:00+00:00",
    }

    write_jsonl(normal_tags_path, [duplicate_row, duplicate_row])

    report = cleaning.run_cleaning(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
    )

    clean_tags = read_json(output_dir / cleaning.COMMANDER_TAGS_CLEAN_JSON)
    exact_duplicates = read_json(output_dir / cleaning.EXACT_DUPLICATE_ROWS_JSON)

    assert report["clean_tag_row_count"] == 1
    assert report["exact_duplicate_row_count"] == 1
    assert len(clean_tags) == 1
    assert len(exact_duplicates) == 1


def test_cleaning_reports_conflicting_duplicate_rows(tmp_path: Path) -> None:
    """
    If two rows have the same commander_slug + tag_slug + source_type but
    different values, the cleaner should report the conflict and keep the
    first row in the clean output.
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "processed"

    write_json(
        commander_index_path,
        [
            {
                "commander_slug": "example-commander",
                "commander_url": "https://edhrec.com/commanders/example-commander",
                "source_type": "commanders_sitemap",
                "discovered_at": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    write_jsonl(
        normal_tags_path,
        [
            {
                "commander_name": "Example Commander",
                "commander_slug": "example-commander",
                "total_decks": 100,
                "tag_name": "Tokens",
                "tag_slug": "tokens",
                "tag_decks": 25,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            },
            {
                "commander_name": "Example Commander",
                "commander_slug": "example-commander",
                "total_decks": 100,
                "tag_name": "Tokens",
                "tag_slug": "tokens",
                "tag_decks": 30,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            },
        ],
    )

    report = cleaning.run_cleaning(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
    )

    clean_tags = read_json(output_dir / cleaning.COMMANDER_TAGS_CLEAN_JSON)
    conflict_rows = read_json(output_dir / cleaning.DUPLICATE_CONFLICT_ROWS_JSON)

    assert report["clean_tag_row_count"] == 1
    assert report["conflicting_duplicate_row_count"] == 2
    assert report["cleaning_stats"]["conflicting_duplicate_key_count"] == 1

    assert len(conflict_rows) == 2

    # The first row should be kept.
    assert clean_tags[0]["tag_decks"] == 25


def test_cleaning_rejects_unknown_commander_slug(tmp_path: Path) -> None:
    """
    Every clean tag row must trace back to commander_index.json.

    This protects the analysis pipeline from rows that came from an unexpected
    or malformed source.
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "processed"

    write_json(
        commander_index_path,
        [
            {
                "commander_slug": "known-commander",
                "commander_url": "https://edhrec.com/commanders/known-commander",
                "source_type": "commanders_sitemap",
                "discovered_at": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    write_jsonl(
        normal_tags_path,
        [
            {
                "commander_name": "Unknown Commander",
                "commander_slug": "unknown-commander",
                "total_decks": 100,
                "tag_name": "Tokens",
                "tag_slug": "tokens",
                "tag_decks": 25,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    report = cleaning.run_cleaning(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
    )

    clean_tags = read_json(output_dir / cleaning.COMMANDER_TAGS_CLEAN_JSON)
    invalid_rows = read_json(output_dir / cleaning.INVALID_ROWS_JSON)

    assert report["clean_tag_row_count"] == 0
    assert report["invalid_row_count"] == 1
    assert report["cleaning_stats"]["unknown_commander_slug_count"] == 1
    assert clean_tags == []
    assert invalid_rows[0]["reason_type"] == "unknown_commander_slug"


def test_cleaning_works_without_optional_cedh_file(tmp_path: Path) -> None:
    """
    cEDH data is optional for the cleaner.

    This allows normal tag cleaning to work even before cEDH scraping has run.
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "processed"

    write_json(
        commander_index_path,
        [
            {
                "commander_slug": "example-commander",
                "commander_url": "https://edhrec.com/commanders/example-commander",
                "source_type": "commanders_sitemap",
                "discovered_at": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    write_jsonl(
        normal_tags_path,
        [
            {
                "commander_name": "Example Commander",
                "commander_slug": "example-commander",
                "total_decks": 100,
                "tag_name": "Tokens",
                "tag_slug": "tokens",
                "tag_decks": 25,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    report = cleaning.run_cleaning(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
    )

    assert report["normal_input_row_count"] == 1
    assert report["cedh_input_row_count"] == 0
    assert report["clean_tag_row_count"] == 1

    assert (output_dir / cleaning.COMMANDER_TAGS_CLEAN_JSON).exists()
    assert (output_dir / cleaning.COMMANDERS_CLEAN_JSON).exists()
    assert (output_dir / cleaning.TAGS_CLEAN_JSON).exists()
    assert (output_dir / cleaning.VALIDATION_REPORT_JSON).exists()
