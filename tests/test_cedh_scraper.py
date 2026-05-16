"""
test_cedh_scraper.py

Tests for the cEDH special scraper.

These tests do not contact EDHREC.

Why:
- Live EDHREC counts change.
- Network requests can fail for reasons unrelated to our parser.
- Unit tests should verify our logic with stable fake data.

The most important behavior:
- cEDH payload["num_decks_avg"] becomes tag_decks.
- We ignore panels["taglinks"] for cEDH.
- cEDH rows use tag_slug = "cedh".
- cEDH rows use source_type = "cedh_filtered_json".
"""

import json
from pathlib import Path

import pytest

from edhrec_affinity import cedh_scraper


def write_jsonl(path: Path, records: list[dict]) -> None:
    """
    Small test helper to write JSONL files.
    """
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            json.dump(record, file)
            file.write("\n")


def read_json(path: Path) -> list[dict]:
    """
    Small test helper to read a JSON array file.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_cedh_json_url() -> None:
    """
    The cEDH JSON URL should put /cedh.json after the commander slug.
    """
    url = cedh_scraper.build_cedh_json_url("the-tenth-doctor-rose-tyler")

    assert (
        url
        == "https://json.edhrec.com/pages/commanders/the-tenth-doctor-rose-tyler/cedh.json"
    )


def test_parse_cedh_payload_uses_num_decks_avg_as_tag_decks() -> None:
    """
    cEDH parsing should use the cEDH JSON's num_decks_avg as tag_decks.

    This test deliberately includes fake taglinks with a huge count to prove
    that cEDH does not use panels["taglinks"].
    """
    payload = {
        "num_decks_avg": 3,
        "panels": {
            "taglinks": [
                {"count": 999, "slug": "combo", "value": "Combo"},
            ]
        },
    }

    row = cedh_scraper.parse_cedh_payload(
        payload,
        commander_slug="the-tenth-doctor-rose-tyler",
        commander_name="The Tenth Doctor // Rose Tyler",
        normal_total_decks=3429,
        scrape_timestamp="2026-05-07T00:00:00+00:00",
    )

    assert row is not None
    assert row["commander_name"] == "The Tenth Doctor // Rose Tyler"
    assert row["commander_slug"] == "the-tenth-doctor-rose-tyler"
    assert row["total_decks"] == 3429

    # This is the key cEDH-specific assertion.
    assert row["tag_decks"] == 3

    # These fields make cEDH fit the same commander-tag table shape.
    assert row["tag_name"] == "cEDH"
    assert row["tag_slug"] == "cedh"
    assert row["source_type"] == "cedh_filtered_json"
    assert row["scrape_timestamp"] == "2026-05-07T00:00:00+00:00"


def test_parse_cedh_payload_returns_none_for_zero_cedh_decks() -> None:
    """
    A zero cEDH count should not produce a commander-tag row.

    The caller should log this as a no_cedh_decks status.
    """
    payload = {"num_decks_avg": 0}

    row = cedh_scraper.parse_cedh_payload(
        payload,
        commander_slug="example-commander",
        commander_name="Example Commander",
        normal_total_decks=100,
        scrape_timestamp="2026-05-07T00:00:00+00:00",
    )

    assert row is None


def test_parse_cedh_payload_rejects_cedh_count_above_total_decks() -> None:
    """
    cEDH decks should not exceed the commander's normal total deck count.
    """
    payload = {"num_decks_avg": 101}

    with pytest.raises(ValueError, match="cannot exceed normal total decks"):
        cedh_scraper.parse_cedh_payload(
            payload,
            commander_slug="example-commander",
            commander_name="Example Commander",
            normal_total_decks=100,
            scrape_timestamp="2026-05-07T00:00:00+00:00",
        )


def test_load_normal_commander_metadata_from_jsonl(tmp_path: Path) -> None:
    """
    The cEDH scraper should get commander_name and total_decks from the
    normal tag scrape output.
    """
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"

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
                "commander_name": "Jasmine Boreal of the Seven",
                "commander_slug": "jasmine-boreal-of-the-seven",
                "total_decks": 5736,
                "tag_name": "Power",
                "tag_slug": "power",
                "tag_decks": 437,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            },
        ],
    )

    metadata = cedh_scraper.load_normal_commander_metadata(normal_tags_path)

    assert metadata == {
        "jasmine-boreal-of-the-seven": {
            "commander_name": "Jasmine Boreal of the Seven",
            "total_decks": 5736,
        }
    }


def test_run_cedh_scrape_writes_row_and_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    End-to-end test with fake input files and a fake network response.

    This verifies:
    - commander_index.json is read
    - normal tag metadata is read
    - fake cEDH payload is parsed
    - cEDH row file is written
    - cEDH status file is written
    - summary file is written
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "raw_output"

    commander_index_path.write_text(
        json.dumps(
            [
                {
                    "commander_slug": "the-tenth-doctor-rose-tyler",
                    "commander_url": "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
                    "source_type": "commanders_sitemap",
                    "discovered_at": "2026-05-07T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    write_jsonl(
        normal_tags_path,
        [
            {
                "commander_name": "The Tenth Doctor // Rose Tyler",
                "commander_slug": "the-tenth-doctor-rose-tyler",
                "total_decks": 3429,
                "tag_name": "Time Counters",
                "tag_slug": "time-counters",
                "tag_decks": 100,
                "source_type": "commander_json",
                "scrape_timestamp": "2026-05-07T00:00:00+00:00",
            }
        ],
    )

    def fake_fetch_json(client, url):
        assert url.endswith("/the-tenth-doctor-rose-tyler/cedh.json")
        return {
            "num_decks_avg": 3,
            "panels": {
                "taglinks": [
                    {"count": 999, "slug": "ignored", "value": "Ignored"}
                ]
            },
        }

    monkeypatch.setattr(cedh_scraper, "fetch_json", fake_fetch_json)

    summary = cedh_scraper.run_cedh_scrape(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
        request_delay=0,
    )

    rows = read_json(output_dir / cedh_scraper.CEDH_ROWS_JSON)
    statuses = read_json(output_dir / cedh_scraper.CEDH_STATUS_JSON)

    assert summary["attempted_commander_count_this_run"] == 1
    assert summary["cedh_row_count_this_run"] == 1
    assert summary["no_cedh_count_this_run"] == 0
    assert summary["error_count_this_run"] == 0

    assert len(rows) == 1
    assert rows[0]["commander_slug"] == "the-tenth-doctor-rose-tyler"
    assert rows[0]["tag_slug"] == "cedh"
    assert rows[0]["tag_name"] == "cEDH"
    assert rows[0]["tag_decks"] == 3
    assert rows[0]["total_decks"] == 3429
    assert rows[0]["source_type"] == "cedh_filtered_json"

    assert len(statuses) == 1
    assert statuses[0]["status_type"] == "cedh_row_written"


def test_run_cedh_scrape_logs_missing_normal_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If a commander is in commander_index.json but not in the normal tag rows,
    we cannot create a cEDH row because total_decks is unknown.

    That should become a status record, not a crash.
    """
    commander_index_path = tmp_path / "commander_index.json"
    normal_tags_path = tmp_path / "commander_tags_raw.jsonl"
    output_dir = tmp_path / "raw_output"

    commander_index_path.write_text(
        json.dumps(
            [
                {
                    "commander_slug": "missing-normal-metadata",
                    "commander_url": "https://edhrec.com/commanders/missing-normal-metadata",
                    "source_type": "commanders_sitemap",
                    "discovered_at": "2026-05-07T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    # Empty normal tag metadata file.
    normal_tags_path.write_text("", encoding="utf-8")

    def fake_fetch_json(client, url):
        raise AssertionError("fetch_json should not be called without normal metadata")

    monkeypatch.setattr(cedh_scraper, "fetch_json", fake_fetch_json)

    summary = cedh_scraper.run_cedh_scrape(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        output_dir=output_dir,
        request_delay=0,
    )

    rows = read_json(output_dir / cedh_scraper.CEDH_ROWS_JSON)
    statuses = read_json(output_dir / cedh_scraper.CEDH_STATUS_JSON)

    assert summary["attempted_commander_count_this_run"] == 1
    assert summary["cedh_row_count_this_run"] == 0
    assert summary["missing_normal_metadata_count_this_run"] == 1

    assert rows == []
    assert len(statuses) == 1
    assert statuses[0]["commander_slug"] == "missing-normal-metadata"
    assert statuses[0]["status_type"] == "missing_normal_metadata"