"""Browser collection preserves Cache identity, freshness and stop controls."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
import pytest

from edhrec_affinity.build_styles.sources import (
    Cache, RequestLimiter, SourceBlocked, SourceFormatError,
)


START = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
SLUG = "the-tenth-doctor-rose-tyler"
GALLERY = f"https://edhrec.com/commanders/{SLUG}/decks"


def preview_data(identifier="one"):
    return {"urlhash": identifier, "deck": {"commander": ["The Tenth Doctor", "Rose Tyler"],
            "cards": {"Land": [["Island", 98]]}}, "tags": [],
            "url": "https://moxfield.com/decks/source", "savedate": "2026-11-10"}


def preview_html(identifier="one"):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({
        "props": {"pageProps": {"data": preview_data(identifier)}}}) + "</script>"


def gallery_html(identifier="one"):
    page = {"route": {"section": "commandersFeatured", "cardSlug": SLUG, "tab": "decks"},
            "decks": [{"urlhash": identifier, "savedate": "2026-11-10", "lands": 35, "creatures": 20}]}
    return "<p>Showing 1 to 1 of 1 decks</p><script>self.__next_f.push(" + json.dumps([
        1, "1:" + json.dumps(page) + "\n"]) + ")</script>"


def test_offline_gallery_without_sidecar_remains_undated(tmp_path):
    (tmp_path / "decks.html").write_text(gallery_html())
    value = Cache(tmp_path, edhrec_transport="webkit", fetch=False)
    try:
        assert value.get_deck_index(SLUG)["table"][0]["urlhash"] == "one"
        metadata = json.loads((tmp_path / "decks.json.meta.json").read_text())
        assert metadata["fetched_at"] is None
        assert value._browser_transport is None
    finally:
        value.close()


def save_cache(root, relative, text, *, fetched_at=START, url="https://edhrec.com/old"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    metadata = {"url": url, "fetched_at": fetched_at.isoformat(),
                "sha256": hashlib.sha256(text.encode()).hexdigest()}
    path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def limiter():
    ticks = [0.0]
    def sleep(seconds):
        ticks[0] += seconds
    return RequestLimiter(delay=1, monotonic=lambda: ticks[0], sleep=sleep)


@pytest.fixture
def browser(monkeypatch):
    from edhrec_affinity.build_styles import browser_transport
    original = browser_transport.BrowserHTMLTransport
    state = {"instances": [], "requests": [], "http_requests": [], "closed": 0, "handler": None}

    class FakeBrowser:
        supports_url = staticmethod(original.supports_url)

        def __init__(self, request_limiter):
            self.limiter = request_limiter
            state["instances"].append(self)

        def get_response(self, url):
            self.limiter.wait()
            state["requests"].append(url)
            response = state["handler"](url)
            response.request = httpx.Request("GET", url)
            return response

        def close(self):
            state["closed"] += 1

    monkeypatch.setattr(browser_transport, "BrowserHTMLTransport", FakeBrowser)
    return state


def cache(root, browser, **kwargs):
    value = Cache(root, edhrec_transport="webkit", limiter=kwargs.pop("limiter", limiter()), **kwargs)
    value.client.close()
    def unexpected(request):
        browser["http_requests"].append(str(request.url))
        pytest.fail(f"Unexpected HTTP fallback: {request.url}")
    value.client = httpx.Client(transport=httpx.MockTransport(unexpected))
    return value


def test_transport_selection_is_explicit_or_environment_driven(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_STYLE_EDHREC_TRANSPORT", "webkit")
    with_environment = Cache(tmp_path / "env")
    explicit_http = Cache(tmp_path / "http", edhrec_transport="http")
    try:
        assert with_environment.edhrec_transport == "webkit"
        assert explicit_http.edhrec_transport == "http"
        assert with_environment._browser_transport is None
        assert explicit_http._browser_transport is None
    finally:
        with_environment.close()
        explicit_http.close()
    monkeypatch.setenv("BUILD_STYLE_EDHREC_TRANSPORT", "unsupported")
    with pytest.raises(ValueError, match="transport"):
        Cache(tmp_path / "invalid")


@pytest.mark.parametrize("fetch,age", [(True, timedelta(minutes=30)), (False, timedelta(days=5))])
def test_legacy_index_cache_is_read_without_browser_and_keeps_source_age(tmp_path, browser, fetch, age):
    text = json.dumps({"table": [{"urlhash": "one", "land": 35, "creature": 20}]})
    metadata = save_cache(tmp_path, "decks.json", text,
                          url=f"https://json.edhrec.com/pages/decks/{SLUG}.json")
    value = cache(tmp_path, browser, fetch=fetch, max_age_seconds=3600, now=lambda: START + age)
    try:
        assert value.get_deck_index(SLUG)["table"][0]["urlhash"] == "one"
        assert value.provenance[-1]["fetched_at"] == START.isoformat()
        assert value.provenance[-1]["cache_stale"] is (age > timedelta(hours=1))
        assert json.loads((tmp_path / "decks.json.meta.json").read_text()) == metadata
        assert browser["instances"] == browser["requests"] == []
    finally:
        value.close()


def test_offline_html_can_populate_compatible_index_without_freshening_it(tmp_path, browser):
    metadata = save_cache(tmp_path, "decks.html", gallery_html(), url=GALLERY)
    value = cache(tmp_path, browser, fetch=False, max_age_seconds=3600,
                  now=lambda: START + timedelta(days=3))
    try:
        result = value.get_deck_index(SLUG)
        assert result["table"][0]["urlhash"] == "one"
        normalized = json.loads((tmp_path / "decks.json.meta.json").read_text())
        assert normalized["fetched_at"] == metadata["fetched_at"]
        assert normalized["source_sha256"] == metadata["sha256"]
        assert normalized["sha256"] == hashlib.sha256((tmp_path / "decks.json").read_bytes()).hexdigest()
        assert value.provenance[-1]["cache_stale"]
        assert browser["instances"] == []
    finally:
        value.close()


def test_stale_legacy_index_refreshes_from_gallery_and_retains_html_provenance(tmp_path, browser):
    save_cache(tmp_path, "decks.json", json.dumps({"table": [{"urlhash": "old"}]}))
    body = gallery_html("new")
    browser["handler"] = lambda _url: httpx.Response(200, headers={"content-type": "text/html"}, text=body)
    observed = START + timedelta(days=2)
    value = cache(tmp_path, browser, fetch=True, max_age_seconds=3600, now=lambda: observed)
    try:
        assert value.get_deck_index(SLUG)["table"][0]["urlhash"] == "new"
        assert value.get_deck_index(SLUG)["table"][0]["urlhash"] == "new"
        assert browser["requests"] == [GALLERY]
        metadata = json.loads((tmp_path / "decks.json.meta.json").read_text())
        assert metadata["url"] == GALLERY
        assert metadata["fetched_at"] == observed.isoformat()
        assert metadata["source_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        assert (tmp_path / "decks.html").read_text() == body
    finally:
        value.close()
    assert browser["closed"] == 1


def test_each_missing_preview_uses_ordinary_html_without_compact_or_http_fallback(tmp_path, browser):
    browser["handler"] = lambda url: httpx.Response(200, text=preview_html(url.rsplit("/", 1)[-1]))
    class UnexpectedCompactTransport:
        def get(self, *_args, **_kwargs):
            pytest.fail("WebKit fetch must not switch to compact/RSC requests")
    value = cache(tmp_path, browser, fetch=True, preview_transport=UnexpectedCompactTransport())
    try:
        for identifier in ["one", "two", "one"]:
            assert value.get_preview(identifier)["urlhash"] == identifier
        assert browser["requests"] == ["https://edhrec.com/deckpreview/one", "https://edhrec.com/deckpreview/two"]
        assert len(browser["instances"]) == 1
        assert len(list((tmp_path / "previews").glob("*.html"))) == 2
    finally:
        value.close()


def test_fresh_preview_html_does_not_initialize_a_browser(tmp_path, browser):
    save_cache(tmp_path, "previews/one.html", preview_html())
    value = cache(tmp_path, browser, fetch=True, max_age_seconds=3600, now=lambda: START)
    try:
        assert value.get_preview("one")["urlhash"] == "one"
        assert browser["instances"] == []
    finally:
        value.close()


def test_legacy_compact_preview_is_read_offline_without_an_assigned_transport(tmp_path, browser):
    save_cache(tmp_path, "previews/one.json", json.dumps({"pageProps": {"data": preview_data()}}))
    value = cache(tmp_path, browser, fetch=False)
    try:
        assert value.get_preview("one")["urlhash"] == "one"
        assert value.provenance[-1]["fetched_at"] == START.isoformat()
        assert browser["instances"] == []
    finally:
        value.close()


@pytest.mark.parametrize("body", [preview_html("wrong"), "<html>Unrecognized source response</html>"])
def test_wrong_preview_identity_or_schema_cannot_replace_a_usable_cache(tmp_path, browser, body):
    save_cache(tmp_path, "previews/one.html", preview_html())
    before = {p.name: p.read_bytes() for p in (tmp_path / "previews").iterdir()}
    browser["handler"] = lambda _url: httpx.Response(200, text=body)
    value = cache(tmp_path, browser, fetch=True)
    try:
        with pytest.raises(SourceFormatError):
            value.get_preview("one", force_refresh=True)
        assert not value.blocked
        assert {p.name: p.read_bytes() for p in (tmp_path / "previews").iterdir()} == before
        assert len(browser["requests"]) == 1
    finally:
        value.close()


def test_wrong_gallery_route_does_not_replace_normalized_index(tmp_path, browser):
    save_cache(tmp_path, "decks.json", json.dumps({"table": [{"urlhash": "old"}]}))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    body = gallery_html().replace(SLUG, "different-commander")
    browser["handler"] = lambda _url: httpx.Response(200, text=body)
    value = cache(tmp_path, browser, fetch=True)
    try:
        with pytest.raises(SourceFormatError):
            value.get_deck_index(SLUG, force_refresh=True)
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
        assert not value.blocked
    finally:
        value.close()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_browser_denial_stops_shared_workers_without_retry_or_cache_changes(tmp_path, browser, status):
    shared = limiter()
    browser["handler"] = lambda _url: httpx.Response(status, headers={"retry-after": "120"}, text="Denied")
    first = cache(tmp_path / "first", browser, fetch=True, limiter=shared, now=lambda: START)
    second = cache(tmp_path / "second", browser, fetch=True, limiter=shared, now=lambda: START)
    try:
        with pytest.raises(SourceBlocked):
            first.get_preview("one")
        with pytest.raises(SourceBlocked):
            second.get_preview("two")
        assert first.blocked and second.blocked
        assert browser["requests"] == ["https://edhrec.com/deckpreview/one"]
        assert len(browser["instances"]) == 1
        assert not list(tmp_path.glob("**/*.html"))
        assert shared.stats["source_stop"]["status_code"] == status
        assert shared.stats["block_kind"] == ("rate_limit" if status == 429 else "access_restriction")
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("response", [
    httpx.Response(200, text='<html><title>Just a moment</title></html>'),
    httpx.Response(200, headers={"cf-mitigated": "challenge"}, text=preview_html()),
])
def test_http200_browser_challenge_retains_prior_cache_and_access_hold(tmp_path, browser, response):
    save_cache(tmp_path, "previews/one.html", preview_html())
    before = (tmp_path / "previews/one.html").read_bytes()
    browser["handler"] = lambda _url: response
    value = cache(tmp_path, browser, fetch=True)
    try:
        with pytest.raises(SourceBlocked):
            value.get_preview("one", force_refresh=True)
        assert value.blocked
        assert (tmp_path / "previews/one.html").read_bytes() == before
        assert value.limiter.stats["block_kind"] == "access_restriction"
        assert len(browser["requests"]) == 1
    finally:
        value.close()


def test_preview_caller_validation_still_runs_before_caching(tmp_path, browser):
    browser["handler"] = lambda _url: httpx.Response(200, text=preview_html())
    value = cache(tmp_path, browser, fetch=True)
    def reject(_data):
        raise ValueError("Application-specific invalid deck")
    try:
        with pytest.raises(SourceFormatError, match="Application-specific"):
            value.get_preview("one", validate=reject)
        assert not (tmp_path / "previews/one.html").exists()
    finally:
        value.close()


def test_webkit_mode_leaves_commander_json_on_paced_http_client(tmp_path, browser):
    value = cache(tmp_path, browser, fetch=True)
    value.client.close()
    requests = []
    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"commander": "verified"})
    value.client = httpx.Client(transport=httpx.MockTransport(response))
    url = f"https://json.edhrec.com/pages/commanders/{SLUG}.json"
    try:
        assert value.get("base.json", url) == {"commander": "verified"}
        assert requests == [url]
        assert value.limiter.stats["requests_started"] == 1
        assert browser["instances"] == []
    finally:
        value.close()
