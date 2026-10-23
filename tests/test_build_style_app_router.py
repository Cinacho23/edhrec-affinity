"""Public deck preview formats across the EDHREC Next.js migration."""

import json

import httpx
import pytest

from edhrec_affinity.build_styles.sources import (
    Cache, PreviewTransport, extract_preview_data, extract_preview_stream, parse_preview,
)


def flight_stream(identifier="one", *, referenced=False):
    # Matches the public page/route/decklist model; gallery cards lack counts
    # and must never be used as the actual deck.
    recs = [
        {"category": "type-commander", "role": "commander", "name": "Timin, Youthful Geist", "count": 1},
        {"category": "type-commander", "role": "partner", "name": "Rhoda, Geist Avenger", "count": 1},
        {"category": "type-land", "name": "Plains", "count": 98},
        {"category": "sideboard", "role": "sideboard", "name": "Ignore", "count": 1},
    ]
    categories = [{"slug": "type-land", "label": "Land"}]
    deck = {"recs": "$2" if referenced else recs,
            "categories": "$3" if referenced else categories,
            "tags": [], "url": "https://moxfield.com/decks/example", "savedate": "2025-10-15"}
    page = {"route": {"section": "deckpreview", "deckpreviewSlug": identifier},
            "decklist": "$4" if referenced else deck,
            "gallery": {"recs": [{"name": "Wrong gallery card"}]}}
    rows = ["1:\"$Sreact.fragment\"", "a:I[123,[],\"default\"]",
            "1d:" + json.dumps(["$", "$L1c", None, {"page": page}])]
    if referenced:
        rows.extend(f"{key}:" + json.dumps(value) for key, value in [("2", recs), ("3", categories), ("4", deck)])
    return "\n".join(rows) + "\n"


def flight_html(identifier="one"):
    stream = flight_stream(identifier)
    # Framework script boundaries can split a JSON model in the middle.
    split = len(stream) // 2
    return "<html><script>self.__next_f.push([0])</script>" + "".join(
        "<script>self.__next_f.push(" + json.dumps([1, chunk]) + ")</script>"
        for chunk in [stream[:split], stream[split:]]
    ) + "</html>"


@pytest.mark.parametrize("referenced", [False, True])
def test_flight_deck_uses_counts_commanders_and_excludes_gallery(referenced):
    deck = parse_preview(extract_preview_stream(flight_stream(referenced=referenced)), "one")
    assert deck["commanders"] == ["Timin, Youthful Geist", "Rhoda, Geist Avenger"]
    assert deck["cards"] == [{"name": "Plains", "quantity": 98, "type_line": "Land"}]
    assert deck["savedate"] == "2025-10-15"
    assert deck["source_url"] == "https://moxfield.com/decks/example"


def test_flight_html_chunks_and_rsc_html_fallback_read_identically():
    expected = extract_preview_stream(flight_stream())
    assert extract_preview_data(flight_html()) == expected
    assert extract_preview_stream(flight_html()) == expected


def test_app_router_transport_bootstraps_once_then_uses_smaller_public_render(tmp_path):
    cache = Cache(tmp_path, fetch=True, delay=0, preview_transport=PreviewTransport())
    requests = []

    def handler(request):
        requests.append(request)
        identifier = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=flight_stream(identifier) if request.headers.get("RSC") else flight_html(identifier))

    cache.client.close()
    cache.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        for identifier in ["one", "two", "three"]:
            assert cache.get_preview(identifier)["urlhash"] == identifier
        assert [request.url.path for request in requests] == ["/deckpreview/one", "/deckpreview/two", "/deckpreview/three"]
        assert [request.headers.get("RSC") for request in requests] == [None, "1", "1"]
        fetched = cache.provenance_for("https://edhrec.com/deckpreview/two")[0]["fetched_at"]
        cache.fetch = False
        assert cache.get_preview("two")["urlhash"] == "two"
        assert cache.provenance[-1]["fetched_at"] == fetched
        assert len(requests) == 3
    finally:
        cache.close()


@pytest.mark.parametrize("changed_response", [404, 200])
def test_deployment_from_legacy_to_app_router_updates_shared_transport(tmp_path, changed_response):
    cache = Cache(tmp_path, fetch=True, delay=0, preview_transport=PreviewTransport())
    requests = []

    def handler(request):
        requests.append(request)
        identifier = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        if request.url.path.startswith("/_next/"):
            return httpx.Response(changed_response, json={"notFound": True})
        if identifier == "one":
            data = {"buildId": "old-build", "props": {"pageProps": {"data": {
                "deck": {"commander": ["Timin"], "cards": {"Land": [["Plains", 99]]}},
            }}}}
            return httpx.Response(200, text='<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>")
        return httpx.Response(200, text=flight_stream(identifier) if request.headers.get("RSC") else flight_html(identifier))

    cache.client.close()
    cache.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        for identifier in ["one", "two", "three"]:
            cache.get_preview(identifier)
        assert [request.url.path for request in requests] == [
            "/deckpreview/one", "/_next/data/old-build/deckpreview/two.json", "/deckpreview/two", "/deckpreview/three",
        ]
        assert requests[-1].headers["RSC"] == "1"
        assert not cache.blocked
    finally:
        cache.close()


def test_flight_schema_change_is_explicit():
    with pytest.raises(ValueError, match="schema may have changed"):
        extract_preview_stream('1d:{"gallery":{"recs":[]}}\n')


def test_flight_rejects_invalid_category_metadata():
    stream = flight_stream().replace('"label": "Land"', '"label": 4')
    with pytest.raises(ValueError, match="invalid category label"):
        extract_preview_stream(stream)
