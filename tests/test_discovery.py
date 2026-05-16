"""
test_discovery.py

Tests for Chat 4 commander discovery logic.

These tests intentionally avoid live EDHREC requests.

Why?
Live website data can change. Network requests can fail. Rate limits can happen.
A unit test should verify our parsing and validation logic using stable fake
input instead of depending on a real website.

The live sitemap can still be tested manually by running:

    python -m edhrec_affinity.discovery

But pytest should stay fast and reliable.
"""

import json

import pytest

from edhrec_affinity.discovery import (
    build_commander_index_records,
    deduplicate_commander_records,
    discover_commander_index,
    extract_commander_slug,
    parse_sitemap_locations,
    save_json,
    validate_commander_index,
    write_discovery_outputs,
)


def test_parse_sitemap_locations_without_namespace():
    """
    Basic XML sitemap parsing test.

    This fake sitemap does not use XML namespaces.
    """

    xml_text = """
    <urlset>
      <url>
        <loc>https://edhrec.com/commanders/jasmine-boreal-of-the-seven</loc>
      </url>
      <url>
        <loc>https://edhrec.com/commanders/the-tenth-doctor-rose-tyler</loc>
      </url>
    </urlset>
    """

    locations = parse_sitemap_locations(xml_text)

    assert locations == [
        "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
        "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
    ]


def test_parse_sitemap_locations_with_namespace():
    """
    Real sitemaps often use XML namespaces.

    The parser should still find <loc> values even when the tag internally
    looks like "{namespace}loc".
    """

    xml_text = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://edhrec.com/commanders/jasmine-boreal-of-the-seven</loc>
      </url>
      <url>
        <loc>https://edhrec.com/commanders/the-tenth-doctor-rose-tyler</loc>
      </url>
    </urlset>
    """

    locations = parse_sitemap_locations(xml_text)

    assert locations == [
        "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
        "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
    ]


def test_parse_sitemap_locations_raises_value_error_for_invalid_xml():
    """
    Invalid XML should fail clearly.

    We raise ValueError from our function so callers do not have to know the
    exact ElementTree exception type.
    """

    invalid_xml = "<urlset><url><loc>https://edhrec.com/commanders/test"

    with pytest.raises(ValueError, match="Could not parse sitemap XML"):
        parse_sitemap_locations(invalid_xml)


def test_extract_commander_slug_from_normal_commander_url():
    """
    A normal commander URL should return the final slug.
    """

    url = "https://edhrec.com/commanders/jasmine-boreal-of-the-seven"

    slug = extract_commander_slug(url)

    assert slug == "jasmine-boreal-of-the-seven"


def test_extract_commander_slug_from_paired_commander_url():
    """
    Paired commanders can have combined slugs.

    We should parse the slug directly from the URL instead of trying to create
    it from the commander names.
    """

    url = "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler"

    slug = extract_commander_slug(url)

    assert slug == "the-tenth-doctor-rose-tyler"


def test_extract_commander_slug_rejects_filtered_route():
    """
    Chat 4 should only discover base commander pages.

    Filtered routes such as /cedh are handled separately later.
    """

    url = "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler/cedh"

    slug = extract_commander_slug(url)

    assert slug is None


def test_extract_commander_slug_rejects_non_commander_url():
    """
    URLs outside /commanders/<slug> should not become commander records.
    """

    url = "https://edhrec.com/tags/vanilla"

    slug = extract_commander_slug(url)

    assert slug is None


def test_extract_commander_slug_rejects_non_edhrec_url():
    """
    The discovery module should not accidentally index unrelated domains.
    """

    url = "https://example.com/commanders/jasmine-boreal-of-the-seven"

    slug = extract_commander_slug(url)

    assert slug is None


def test_build_commander_index_records_skips_invalid_urls():
    """
    build_commander_index_records() should create records only for valid base
    commander URLs.
    """

    urls = [
        "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
        "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
        "https://edhrec.com/tags/vanilla",
    ]

    records = build_commander_index_records(
        urls,
        discovered_at="2026-05-07T00:00:00Z",
    )

    assert records == [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00Z",
        },
        {
            "commander_slug": "the-tenth-doctor-rose-tyler",
            "commander_url": "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00Z",
        },
    ]


def test_deduplicate_commander_records_keeps_first_record():
    """
    If duplicate slugs appear, keep the first copy.

    This preserves the original sitemap order while preventing Chat 5 from
    scraping the same commander twice.
    """

    records = [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00Z",
        },
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://www.edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:01:00Z",
        },
    ]

    unique_records = deduplicate_commander_records(records)

    assert unique_records == [records[0]]


def test_validate_commander_index_reports_duplicates_and_invalid_urls():
    """
    Validation should tell us whether the sitemap produced duplicates or
    malformed/non-commander URLs.
    """

    urls = [
        "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
        "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
        "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler/cedh",
        "https://edhrec.com/tags/vanilla",
    ]

    records_before_deduplication = build_commander_index_records(
        urls,
        discovered_at="2026-05-07T00:00:00Z",
    )

    records_after_deduplication = deduplicate_commander_records(
        records_before_deduplication
    )

    report = validate_commander_index(
        sitemap_urls=urls,
        records_before_deduplication=records_before_deduplication,
        records_after_deduplication=records_after_deduplication,
        sitemap_url="https://edhrec.com/sitemaps/commanders.xml",
    )

    assert report["total_locations_found"] == 4
    assert report["total_commander_records_before_deduplication"] == 2
    assert report["total_commander_records_after_deduplication"] == 1
    assert report["unique_slugs"] == 1

    assert report["duplicate_slug_count"] == 1
    assert report["duplicate_slugs"] == ["jasmine-boreal-of-the-seven"]
    assert report["duplicate_slug_details"] == [
        {"commander_slug": "jasmine-boreal-of-the-seven", "count": 2}
    ]

    assert report["invalid_url_count"] == 2
    assert report["invalid_urls"] == [
        "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler/cedh",
        "https://edhrec.com/tags/vanilla",
    ]


def test_save_json_writes_readable_json_file(tmp_path):
    """
    save_json() should create parent directories and write valid JSON.

    tmp_path is a pytest-provided temporary folder. It keeps the test from
    writing into the real project data directory.
    """

    output_path = tmp_path / "nested" / "output.json"
    data = {"commander_slug": "jasmine-boreal-of-the-seven"}

    save_json(data, output_path)

    assert output_path.exists()

    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded == data


def test_write_discovery_outputs_writes_both_files(tmp_path):
    """
    write_discovery_outputs() should write:

    - commander_index.json
    - commander_index_validation.json
    """

    commander_records = [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": "2026-05-07T00:00:00Z",
        }
    ]

    validation_report = {
        "total_locations_found": 1,
        "duplicate_slug_count": 0,
        "invalid_url_count": 0,
    }

    output_paths = write_discovery_outputs(
        output_dir=tmp_path,
        commander_records=commander_records,
        validation_report=validation_report,
    )

    commander_index_path = output_paths["commander_index"]
    validation_report_path = output_paths["validation_report"]

    assert commander_index_path.exists()
    assert validation_report_path.exists()

    saved_records = json.loads(
        commander_index_path.read_text(encoding="utf-8")
    )

    saved_report = json.loads(
        validation_report_path.read_text(encoding="utf-8")
    )

    assert saved_records == commander_records
    assert saved_report == validation_report


def test_discover_commander_index_uses_fetch_text_without_live_network(monkeypatch):
    """
    Test the high-level discovery function without making a real HTTP request.

    monkeypatch replaces fetch_text() with a fake function that returns stable
    XML. This lets us test the workflow without depending on live EDHREC data.
    """

    import edhrec_affinity.discovery as discovery_module

    fake_xml = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://edhrec.com/commanders/jasmine-boreal-of-the-seven</loc>
      </url>
      <url>
        <loc>https://edhrec.com/commanders/the-tenth-doctor-rose-tyler</loc>
      </url>
      <url>
        <loc>https://edhrec.com/tags/vanilla</loc>
      </url>
    </urlset>
    """

    def fake_fetch_text(url: str) -> str:
        assert url == "https://edhrec.com/sitemaps/commanders.xml"
        return fake_xml

    monkeypatch.setattr(discovery_module, "fetch_text", fake_fetch_text)

    records, report = discover_commander_index(
        sitemap_url="https://edhrec.com/sitemaps/commanders.xml"
    )

    assert records == [
        {
            "commander_slug": "jasmine-boreal-of-the-seven",
            "commander_url": "https://edhrec.com/commanders/jasmine-boreal-of-the-seven",
            "source_type": "commanders_sitemap",
            "discovered_at": records[0]["discovered_at"],
        },
        {
            "commander_slug": "the-tenth-doctor-rose-tyler",
            "commander_url": "https://edhrec.com/commanders/the-tenth-doctor-rose-tyler",
            "source_type": "commanders_sitemap",
            "discovered_at": records[1]["discovered_at"],
        },
    ]

    assert report["total_locations_found"] == 3
    assert report["total_commander_records_before_deduplication"] == 2
    assert report["total_commander_records_after_deduplication"] == 2
    assert report["invalid_url_count"] == 1
    assert report["invalid_urls"] == ["https://edhrec.com/tags/vanilla"]