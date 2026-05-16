"""
test_tag_scraper.py

Tests for Chat 5 - Complete Tag Scraper.

Testing strategy:
- Do NOT call live EDHREC in unit tests.
- Use fake payloads with stable counts.
- Use tmp_path for temporary files.
- Use monkeypatch to replace network-fetching behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edhrec_affinity import tag_scraper


def test_build_commander_json_url() -> None:
    """
    The scraper should build the expected EDHREC JSON URL from a commander slug.
    """
    url = tag_scraper.build_commander_json_url("jasmine-boreal-of-the-seven")

    assert url == (
        "https://json.edhrec.com/pages/commanders/"
        "jasmine-boreal-of-the-seven.json"
    )


def test_build_commander_json_url_strips_extra_slashes_and_spaces() -> None:
    """
    Small cleanup helps if a slug accidentally arrives with whitespace or slashes.
    """
    url = tag_scraper.build_commander_json_url(" /jasmine-boreal-of-the-seven/ ")

    assert url == (
        "https://json.edhrec.com/pages/commanders/"
        "jasmine-boreal-of-the-seven.json"
    )


def test_load_commander_index_reads_valid_index(tmp_path: Path) -> None:
    """
    load_commander_index should read Chat 4's commander index shape.
    """
    index_path = tmp_path / "commander_index.json"

    index_data = [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00Z",
        }
    ]

    index_path.write_text(json.dumps(index_data), encoding="utf-8")

    records = tag_scraper.load_commander_index(index_path)

    assert len(records) == 1
    assert records[0]["commander_slug"] == "jasmine-boreal-of-the-seven"


def test_load_commander_index_rejects_non_list_json(tmp_path: Path) -> None:
    """
    The index file should be a list, not a dictionary.
    """
    index_path = tmp_path / "commander_index.json"
    index_path.write_text(json.dumps({"commander_slug": "bad-shape"}), encoding="utf-8")

    with pytest.raises(TypeError):
        tag_scraper.load_commander_index(index_path)


def test_load_commander_index_rejects_missing_slug(tmp_path: Path) -> None:
    """
    Each commander record must include commander_slug.
    """
    index_path = tmp_path / "commander_index.json"
    index_path.write_text(json.dumps([{"commander_url": "https://example.com"}]), encoding="utf-8")

    with pytest.raises(KeyError):
        tag_scraper.load_commander_index(index_path)


def test_slug_to_fallback_name() -> None:
    """
    The fallback name is not perfect, but it is readable enough for raw data.
    """
    name = tag_scraper.slug_to_fallback_name("jasmine-boreal-of-the-seven")

    assert name == "Jasmine Boreal Of The Seven"


def test_extract_commander_name_prefers_commander_record() -> None:
    """
    If the commander index or later metadata has a commander_name, prefer it.
    """
    payload = {
        "name": "Payload Name",
        "header": {"name": "Header Name"},
    }

    record = {
        "commander_slug": "test-commander",
        "commander_name": "Record Name",
    }

    name = tag_scraper.extract_commander_name(
        payload=payload,
        commander_slug="test-commander",
        commander_record=record,
    )

    assert name == "Record Name"


def test_extract_commander_name_uses_payload_name_when_record_name_missing() -> None:
    """
    payload["name"] is the next cleanest source when commander_record has no name.
    """
    payload = {
        "name": "Payload Name",
        "header": {"name": "Header Name"},
    }

    record = {
        "commander_slug": "test-commander",
    }

    name = tag_scraper.extract_commander_name(
        payload=payload,
        commander_slug="test-commander",
        commander_record=record,
    )

    assert name == "Payload Name"


def test_extract_commander_name_uses_header_dict() -> None:
    """
    Some payloads may store a display name in a header object.
    """
    payload = {
        "header": {"name": "Header Name"},
    }

    name = tag_scraper.extract_commander_name(
        payload=payload,
        commander_slug="test-commander",
    )

    assert name == "Header Name"


def test_extract_commander_name_falls_back_to_slug() -> None:
    """
    If no name is found, use a slug-derived fallback instead of crashing.
    """
    payload = {}

    name = tag_scraper.extract_commander_name(
        payload=payload,
        commander_slug="test-commander",
    )

    assert name == "Test Commander"


def test_parse_commander_payload_returns_tag_rows() -> None:
    """
    The parser should convert panels["taglinks"] into one row per tag.

    This uses fake fixture data. Do not use live EDHREC counts in unit tests.
    """
    payload = {
        "num_decks_avg": 1000,
        "panels": {
            "taglinks": [
                {"count": 100, "slug": "power", "value": "Power"},
                {"count": 25, "slug": "vanilla", "value": "Vanilla"},
            ]
        },
    }

    commander_record = {
        "commander_slug": "test-commander",
        "commander_name": "Test Commander",
    }

    rows = tag_scraper.parse_commander_payload(
        payload=payload,
        commander_slug="test-commander",
        scrape_timestamp="2026-05-07T00:00:00Z",
        commander_record=commander_record,
    )

    assert len(rows) == 2

    first = tag_scraper.model_to_dict(rows[0])
    second = tag_scraper.model_to_dict(rows[1])

    assert first["commander_name"] == "Test Commander"
    assert first["commander_slug"] == "test-commander"
    assert first["total_decks"] == 1000
    assert first["tag_name"] == "Power"
    assert first["tag_slug"] == "power"
    assert first["tag_decks"] == 100
    assert first["source_type"] == "commander_json"
    assert first["scrape_timestamp"] == "2026-05-07T00:00:00Z"

    assert second["tag_name"] == "Vanilla"
    assert second["tag_slug"] == "vanilla"
    assert second["tag_decks"] == 25


def test_parse_commander_payload_rejects_missing_num_decks() -> None:
    """
    num_decks_avg is required because later analysis needs total_decks.
    """
    payload = {
        "panels": {
            "taglinks": [
                {"count": 100, "slug": "power", "value": "Power"},
            ]
        }
    }

    with pytest.raises(KeyError):
        tag_scraper.parse_commander_payload(
            payload=payload,
            commander_slug="test-commander",
            scrape_timestamp="2026-05-07T00:00:00Z",
        )


def test_parse_commander_payload_rejects_missing_taglinks() -> None:
    """
    panels["taglinks"] is required for normal tag extraction.
    """
    payload = {
        "num_decks_avg": 1000,
        "panels": {},
    }

    with pytest.raises(KeyError):
        tag_scraper.parse_commander_payload(
            payload=payload,
            commander_slug="test-commander",
            scrape_timestamp="2026-05-07T00:00:00Z",
        )


def test_parse_commander_payload_rejects_non_list_taglinks() -> None:
    """
    taglinks should be a list of tag objects.
    """
    payload = {
        "num_decks_avg": 1000,
        "panels": {
            "taglinks": {"count": 100, "slug": "power", "value": "Power"},
        },
    }

    with pytest.raises(TypeError):
        tag_scraper.parse_commander_payload(
            payload=payload,
            commander_slug="test-commander",
            scrape_timestamp="2026-05-07T00:00:00Z",
        )


def test_append_and_read_jsonl(tmp_path: Path) -> None:
    """
    JSONL output should append one dictionary per line and read it back.
    """
    path = tmp_path / "rows.jsonl"

    tag_scraper.append_jsonl(path, {"commander_slug": "one", "tag_slug": "power"})
    tag_scraper.append_jsonl(path, {"commander_slug": "two", "tag_slug": "tokens"})

    rows = tag_scraper.read_jsonl(path)

    assert rows == [
        {"commander_slug": "one", "tag_slug": "power"},
        {"commander_slug": "two", "tag_slug": "tokens"},
    ]


def test_read_jsonl_missing_file_returns_empty_list(tmp_path: Path) -> None:
    """
    Missing JSONL files should behave like empty files.
    """
    path = tmp_path / "does_not_exist.jsonl"

    assert tag_scraper.read_jsonl(path) == []


def test_load_completed_slugs_reads_successful_tag_rows(tmp_path: Path) -> None:
    """
    Resume behavior should skip commanders that already have successful tag rows.
    """
    path = tmp_path / "commander_tags_raw.jsonl"

    tag_scraper.append_jsonl(path, {"commander_slug": "already-done", "tag_slug": "power"})
    tag_scraper.append_jsonl(path, {"commander_slug": "already-done", "tag_slug": "tokens"})
    tag_scraper.append_jsonl(path, {"commander_slug": "other-done", "tag_slug": "vanilla"})

    completed = tag_scraper.load_completed_slugs(path)

    assert completed == {"already-done", "other-done"}


def test_build_failure_record() -> None:
    """
    Failure records should contain enough information to debug later.
    """
    error = ValueError("fake problem")

    failure = tag_scraper.build_failure_record(
        commander_slug="bad-commander",
        url="https://example.com/bad-commander.json",
        error=error,
        scrape_timestamp="2026-05-07T00:00:00Z",
    )

    assert failure["commander_slug"] == "bad-commander"
    assert failure["url"] == "https://example.com/bad-commander.json"
    assert failure["error_type"] == "ValueError"
    assert failure["error_message"] == "fake problem"
    assert failure["scrape_timestamp"] == "2026-05-07T00:00:00Z"


class DummyClient:
    """
    Minimal context-manager client for tests.

    scrape_all_commander_tags expects client_factory() to return something
    usable in a with-statement. Since fetch_json is monkeypatched in the
    high-level test, this dummy client does not need real HTTP behavior.
    """

    def __enter__(self) -> "DummyClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_scrape_all_commander_tags_writes_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    High-level workflow test:
    - one commander succeeds
    - one commander fails
    - scraper writes tag JSONL, failure JSONL, JSON arrays, and summary
    - no live network call is made
    """
    commander_index_path = tmp_path / "commander_index.json"
    output_dir = tmp_path / "output"

    commander_index = [
        {
            "commander_slug": "good-commander",
            "commander_name": "Good Commander",
            "commander_url": "https://edhrec.com/commanders/good-commander",
        },
        {
            "commander_slug": "bad-commander",
            "commander_name": "Bad Commander",
            "commander_url": "https://edhrec.com/commanders/bad-commander",
        },
    ]

    commander_index_path.write_text(json.dumps(commander_index), encoding="utf-8")

    def fake_fetch_json(client, url: str):
        if url.endswith("/good-commander.json"):
            return {
                "num_decks_avg": 500,
                "panels": {
                    "taglinks": [
                        {"count": 50, "slug": "tokens", "value": "Tokens"},
                        {"count": 20, "slug": "aggro", "value": "Aggro"},
                    ]
                },
            }

        raise ValueError("fake fetch failure")

    monkeypatch.setattr(tag_scraper, "fetch_json", fake_fetch_json)

    summary = tag_scraper.scrape_all_commander_tags(
        commander_index_path=commander_index_path,
        output_dir=output_dir,
        request_delay_seconds=0,
        resume=True,
        client_factory=DummyClient,
    )

    assert summary["total_commander_records_in_index"] == 2
    assert summary["attempted_commander_count"] == 2
    assert summary["successful_commander_count_this_run"] == 1
    assert summary["failed_commander_count_this_run"] == 1
    assert summary["new_tag_row_count_this_run"] == 2
    assert summary["total_tag_rows_in_output"] == 2
    assert summary["total_failures_in_output"] == 1

    tags_jsonl_path = output_dir / tag_scraper.TAGS_JSONL_FILENAME
    failures_jsonl_path = output_dir / tag_scraper.FAILURES_JSONL_FILENAME
    tags_json_path = output_dir / tag_scraper.TAGS_JSON_FILENAME
    failures_json_path = output_dir / tag_scraper.FAILURES_JSON_FILENAME
    summary_json_path = output_dir / tag_scraper.SUMMARY_JSON_FILENAME

    assert tags_jsonl_path.exists()
    assert failures_jsonl_path.exists()
    assert tags_json_path.exists()
    assert failures_json_path.exists()
    assert summary_json_path.exists()

    tag_rows = json.loads(tags_json_path.read_text(encoding="utf-8"))
    failures = json.loads(failures_json_path.read_text(encoding="utf-8"))
    saved_summary = json.loads(summary_json_path.read_text(encoding="utf-8"))

    assert len(tag_rows) == 2
    assert tag_rows[0]["commander_slug"] == "good-commander"
    assert tag_rows[0]["commander_name"] == "Good Commander"
    assert tag_rows[0]["tag_slug"] == "tokens"
    assert tag_rows[0]["tag_decks"] == 50

    assert len(failures) == 1
    assert failures[0]["commander_slug"] == "bad-commander"
    assert failures[0]["error_type"] == "ValueError"
    assert failures[0]["error_message"] == "fake fetch failure"

    assert saved_summary["total_tag_rows_in_output"] == 2


def test_scrape_all_commander_tags_resume_skips_completed_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Resume behavior:
    - If a commander already has successful tag rows in the JSONL file,
      it should be skipped on the next run.
    """
    commander_index_path = tmp_path / "commander_index.json"
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    commander_index = [
        {"commander_slug": "already-done", "commander_name": "Already Done"},
        {"commander_slug": "new-commander", "commander_name": "New Commander"},
    ]

    commander_index_path.write_text(json.dumps(commander_index), encoding="utf-8")

    existing_tags_jsonl = output_dir / tag_scraper.TAGS_JSONL_FILENAME
    tag_scraper.append_jsonl(
        existing_tags_jsonl,
        {
            "commander_name": "Already Done",
            "commander_slug": "already-done",
            "total_decks": 100,
            "tag_name": "Power",
            "tag_slug": "power",
            "tag_decks": 10,
            "source_type": "commander_json",
            "scrape_timestamp": "2026-05-07T00:00:00Z",
        },
    )

    fetched_urls: list[str] = []

    def fake_fetch_json(client, url: str):
        fetched_urls.append(url)
        return {
            "num_decks_avg": 300,
            "panels": {
                "taglinks": [
                    {"count": 30, "slug": "tokens", "value": "Tokens"},
                ]
            },
        }

    monkeypatch.setattr(tag_scraper, "fetch_json", fake_fetch_json)

    summary = tag_scraper.scrape_all_commander_tags(
        commander_index_path=commander_index_path,
        output_dir=output_dir,
        request_delay_seconds=0,
        resume=True,
        client_factory=DummyClient,
    )

    assert summary["skipped_commander_count"] == 1
    assert summary["attempted_commander_count"] == 1
    assert summary["successful_commander_count_this_run"] == 1

    assert len(fetched_urls) == 1
    assert fetched_urls[0].endswith("/new-commander.json")

    all_rows = tag_scraper.read_jsonl(existing_tags_jsonl)

    assert len(all_rows) == 2
    assert {row["commander_slug"] for row in all_rows} == {
        "already-done",
        "new-commander",
    }