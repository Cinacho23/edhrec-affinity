from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import threading

import httpx
import pytest

from edhrec_affinity.build_styles.browser_transport import BrowserHTMLTransport, extract_gallery_data
from edhrec_affinity.build_styles.sources import (
    BudgetExpired, Cache, ReducedRateRetry, RequestLimiter, SourceBlocked, SourceFormatError,
)


PREVIEW = "https://edhrec.com/deckpreview/Abc_123-X"
GALLERY = "https://edhrec.com/commanders/test-commander/decks"


class Clock:
    def __init__(self):
        self.value = 100

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def limiter(clock=None, **kwargs):
    clock = clock or Clock()
    return RequestLimiter(delay=1, monotonic=clock.monotonic, sleep=clock.sleep, **kwargs)


class FakeRequest:
    def __init__(self, url, *, main=True, document=True, redirect=None):
        self.url = url
        self.frame = SimpleNamespace(parent_frame=None if main else object())
        self.resource_type = "document" if document else "script"
        self.redirected_from = redirect

    def is_navigation_request(self):
        return self.resource_type == "document"


class FakeRoute:
    def __init__(self, request):
        self.request = request
        self.aborted = self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class FakePage:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def goto(self, url, **kwargs):
        self.owner.record("goto")
        self.owner.goto_calls.append((url, kwargs))
        request = FakeRequest(url)
        route = FakeRoute(request)
        self.owner.routes.append(route)
        self.owner.gate(route)
        if route.aborted:
            raise RuntimeError("Navigation aborted")
        for extra in self.owner.subresources:
            extra_route = FakeRoute(extra)
            self.owner.routes.append(extra_route)
            self.owner.gate(extra_route)
        payload = self.owner.responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        response = SimpleNamespace(
            status=payload.get("status", 200), headers=payload.get("headers", {}),
            url=url, request=request, body=lambda: payload.get("body", b"<html>public deck</html>"),
            all_headers=lambda: payload.get("headers", {}),
        )
        self.owner.events["response"](response)
        if payload.get("redirect"):
            self.owner.events["request"](FakeRequest(payload["redirect"], redirect=request))
        return response

    def close(self):
        self.owner.record("page.close")
        self.closed = True

    def is_closed(self):
        return self.closed


class FakeRuntime:
    def __init__(self, responses=None):
        self.responses = list(responses or [{}, {}])
        self.events, self.routes, self.goto_calls = {}, [], []
        self.subresources = []
        self.actions = []
        self.webkit = SimpleNamespace(launch=self.launch)
        self.factory_calls = 0
        self.context_options = self.launch_options = None

    def factory(self):
        self.factory_calls += 1
        return self

    def record(self, action):
        self.actions.append((action, threading.get_ident()))

    def start(self):
        self.record("start")
        return self

    def launch(self, **kwargs):
        self.record("launch")
        self.launch_options = kwargs
        return SimpleNamespace(new_context=self.new_context, close=lambda: self.record("browser.close"))

    def new_context(self, **kwargs):
        self.record("new_context")
        self.context_options = kwargs
        return SimpleNamespace(route=self.route, on=self.on, new_page=self.new_page,
                               close=lambda: self.record("context.close"))

    def route(self, pattern, handler):
        assert pattern == "**/*"
        self.gate = handler

    def on(self, event, callback):
        self.events[event] = callback

    def new_page(self):
        self.record("new_page")
        return FakePage(self)

    def stop(self):
        self.record("stop")


def test_standard_browser_is_owned_by_one_thread_and_reuses_fresh_context():
    clock, runtime = Clock(), FakeRuntime()
    budget = limiter(clock)
    transport = BrowserHTMLTransport(budget, playwright_factory=runtime.factory)
    main_thread = threading.get_ident()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(transport.get_response, [PREVIEW, GALLERY]))
        assert [result.status_code for result in results] == [200, 200]
        assert results[0].request.url == PREVIEW
        assert results[0].extensions["edhrec_browser"] == "webkit"
        assert runtime.factory_calls == 1
        assert runtime.launch_options == {"headless": True}
        assert runtime.context_options == {"accept_downloads": False, "service_workers": "block"}
        assert budget.stats["requests_started"] == 2
        assert clock.value == 101
        assert transport.stats["document_requests"] == 2
    finally:
        transport.close()
    owned_threads = {thread for _, thread in runtime.actions}
    assert len(owned_threads) == 1
    assert main_thread not in owned_threads
    assert [action for action, _ in runtime.actions].count("browser.close") == 1
    assert [action for action, _ in runtime.actions].count("stop") == 1
    transport.close()
    with pytest.raises(RuntimeError, match="closed"):
        transport.get_response(PREVIEW)


@pytest.mark.parametrize("url", [
    "http://edhrec.com/deckpreview/abc", "https://evil.example/deckpreview/abc",
    "https://edhrec.com.evil.example/deckpreview/abc", "https://user:secret@edhrec.com/deckpreview/abc",
    "https://edhrec.com:443/deckpreview/abc", "https://json.edhrec.com/pages/decks/a.json",
    "https://edhrec.com/deckpreview/abc?x=1", "https://edhrec.com/deckpreview/abc#x",
    "https://edhrec.com/deckpreview/../private", "https://edhrec.com/commanders/Bad/decks",
    "https://edhrec.com/commanders/test-commander", "https://edhrec.com/deckpreview/abc/",
])
def test_only_exact_public_document_routes_are_allowed(url):
    runtime = FakeRuntime()
    transport = BrowserHTMLTransport(limiter(), playwright_factory=runtime.factory)
    try:
        assert not transport.supports_url(url)
        with pytest.raises(ValueError, match="ordinary public"):
            transport.get_response(url)
        assert not runtime.actions
    finally:
        transport.close()


def test_only_the_intended_main_document_is_admitted():
    runtime = FakeRuntime()
    runtime.subresources = [FakeRequest("https://edhrec.com/script.js", document=False),
                            FakeRequest(PREVIEW, main=False), FakeRequest(GALLERY), FakeRequest(PREVIEW)]
    budget = limiter()
    transport = BrowserHTMLTransport(budget, playwright_factory=runtime.factory)
    try:
        transport.get_response(PREVIEW)
        assert [route.continued for route in runtime.routes] == [True, False, False, False, False]
        assert all(route.aborted for route in runtime.routes[1:])
        assert budget.stats["requests_started"] == 1
        assert transport.stats["aborted_resources"] == 4
    finally:
        transport.close()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_denial_stops_shared_budget_and_queued_navigation_without_retry(status):
    runtime = FakeRuntime([{"status": status, "headers": {"retry-after": "120"}}])
    budget = limiter()
    transport = BrowserHTMLTransport(budget, playwright_factory=runtime.factory)
    try:
        assert transport.get_response(PREVIEW).status_code == status
        assert budget.blocked
        stop = budget.stats["source_stop"]
        assert stop["status_code"] == status
        assert stop["retry_after_seconds"] == 120
        assert stop["reason"] == ("rate_limit" if status == 429 else "access_restriction")
        with pytest.raises(SourceBlocked):
            transport.get_response(GALLERY)
        assert len(runtime.goto_calls) == 1
    finally:
        transport.close()


@pytest.mark.parametrize("payload", [
    {"body": b'<html><title>Just a moment</title></html>'},
    {"body": b'<html><script src="/cdn-cgi/challenge-platform/x"></script></html>'},
    {"body": b"<html><p>Banned - Too many requests</p></html>"},
    {"headers": {"cf-mitigated": "challenge"}},
])
def test_success_status_challenge_still_stops_the_session(payload):
    runtime, budget = FakeRuntime([payload]), limiter()
    transport = BrowserHTMLTransport(budget, playwright_factory=runtime.factory)
    try:
        assert transport.get_response(PREVIEW).status_code == 200
        assert budget.blocked
        assert budget.stats["source_stop"]["reason"] == "access_restriction"
        with pytest.raises(SourceBlocked):
            transport.get_response(GALLERY)
        assert len(runtime.goto_calls) == 1
    finally:
        transport.close()


def test_existing_access_stop_and_expired_budget_do_not_launch_browser():
    for blocked in [False, True]:
        runtime, clock = FakeRuntime(), Clock()
        budget = limiter(clock, deadline=99 if not blocked else None)
        if blocked:
            budget.block("existing stop")
        transport = BrowserHTMLTransport(budget, playwright_factory=runtime.factory)
        try:
            with pytest.raises(SourceBlocked if blocked else BudgetExpired):
                transport.get_response(PREVIEW)
            assert runtime.factory_calls == 0
        finally:
            transport.close()


def test_browser_failure_is_retryable_transport_error_without_retry():
    runtime = FakeRuntime([RuntimeError("browser timed out")])
    transport = BrowserHTMLTransport(limiter(), playwright_factory=runtime.factory)
    try:
        with pytest.raises(httpx.TransportError, match="timed out"):
            transport.get_response(PREVIEW)
        assert len(runtime.goto_calls) == 1
        assert any(action == "page.close" for action, _ in runtime.actions)
    finally:
        transport.close()


@pytest.mark.parametrize("payload", [
    {"status": 302, "headers": {"location": "https://elsewhere.example/private"}},
    {"redirect": "https://edhrec.com/deckpreview/another"},
])
def test_unexpected_redirect_terminates_transport_without_next_request(payload):
    runtime = FakeRuntime([payload])
    transport = BrowserHTMLTransport(limiter(), playwright_factory=runtime.factory)
    try:
        with pytest.raises(SourceFormatError, match="redirect"):
            transport.get_response(PREVIEW)
        with pytest.raises(SourceBlocked):
            transport.get_response(GALLERY)
        assert len(runtime.goto_calls) == 1
        assert transport.stats["redirects"]
        assert transport.limiter.stats["source_stop"]["redirect_target"] == transport.stats["redirects"][0]["to"]
    finally:
        transport.close()


def deck_body():
    return ('<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"data": {
        "urlhash": "Abc_123-X", "deck": {"commander": ["Example"], "cards": {"Land": [["Island", 99]]}}
    }}}}) + '</script>').encode()


def browser_cache(tmp_path, responses, *, deadline=None):
    clock = Clock()
    budget = limiter(clock, max_rate=1.5, start_rate=1.5, learned_rate=1.5, deadline=deadline)
    runtime = FakeRuntime(responses)
    transport = BrowserHTMLTransport(budget, playwright_factory=runtime.factory)
    value = Cache(tmp_path, edhrec_transport="webkit", limiter=budget, fetch=True)
    value._browser_transport = transport
    value.client.close()
    value.client = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP fallback")))
    return clock, budget, runtime, transport, value


@pytest.mark.parametrize("target", ["/rate-limit", "https://edhrec.com/rate-limit/", "/rate-limit?source=preview"])
def test_rate_redirect_retries_original_once_and_caps_whole_job_without_changing_next_job(tmp_path, target):
    clock, budget, runtime, transport, value = browser_cache(tmp_path, [
        {"status": 302, "headers": {"location": target, "retry-after": "75"},
         "redirect": "https://edhrec.com/rate-limit"},
        {"body": deck_body()}, {"body": deck_body()},
    ])
    try:
        assert value.get_preview("Abc_123-X")["urlhash"] == "Abc_123-X"
        assert clock.value == 175
        assert not budget.blocked
        assert budget.stats["current_requests_per_second"] == 1
        assert budget.stats["maximum_requests_per_second"] == 1
        assert budget.stats["configured_maximum_requests_per_second"] == 1.5
        assert budget.learning_state == {"learned_rate": 1.5, "throttles": 0, "last_throttle": None}
        for _ in range(300):
            budget.record_success()
        value.get_preview("Abc_123-X", force_refresh=True)
        assert clock.value == 176
        assert [url for url, _ in runtime.goto_calls] == [PREVIEW] * 3
        assert transport.stats["document_requests"] == 3
        assert len(budget.stats["redirect_rate_events"]) == 1  # Duplicate browser events are one incident.
        assert budget.stats["redirect_rate_events"][0]["redirect_target"] == "https://edhrec.com/rate-limit"
        assert "rate-limit" not in (tmp_path / "previews/Abc_123-X.html").read_text()
        assert RequestLimiter(max_rate=1.5, learned_rate=budget.learning_state["learned_rate"]).stats[
            "current_requests_per_second"] == 1.5
    finally:
        value.close()


def test_second_rate_redirect_at_reduced_rate_stops_without_a_third_request(tmp_path):
    response = {"status": 302, "headers": {"location": "/rate-limit"}}
    clock, budget, runtime, _, value = browser_cache(tmp_path, [response, response, {"body": deck_body()}])
    try:
        with pytest.raises(SourceBlocked, match="rate-limit"):
            value.get_preview("Abc_123-X")
        assert len(runtime.goto_calls) == 2 and clock.value == 160
        assert budget.blocked and budget.stats["block_kind"] == "rate_limit"
        assert budget.stats["source_stop"]["rate_limit_redirect"] is True
        assert budget.learning_state["learned_rate"] == 1.5
        assert not (tmp_path / "previews/Abc_123-X.html").exists()
        with pytest.raises(SourceBlocked):
            value.get_preview("Abc_123-X")
        assert len(runtime.goto_calls) == 2
    finally:
        value.close()


@pytest.mark.parametrize("target", ["https://evil.example/rate-limit", "//edhrec.com.evil.example/rate-limit",
    "http://edhrec.com/rate-limit", "https://user:secret@edhrec.com/rate-limit", "/login", ""])
def test_unrecognized_302_is_recorded_but_never_followed_or_retried(tmp_path, target):
    _, budget, runtime, _, value = browser_cache(tmp_path, [{"status": 302, "headers": {"location": target}}])
    try:
        with pytest.raises(SourceFormatError, match="redirect"):
            value.get_preview("Abc_123-X")
        assert budget.blocked and budget.stats["block_kind"] == "access_restriction"
        assert len(runtime.goto_calls) == 1 and budget.stats["maximum_requests_per_second"] == 1
        assert budget.stats["source_stop"]["redirect_target"]
    finally:
        value.close()


def test_retry_after_beyond_job_budget_becomes_a_saved_pause_without_navigation_retry(tmp_path):
    _, budget, runtime, _, value = browser_cache(tmp_path, [
        {"status": 302, "headers": {"location": "/rate-limit", "retry-after": "7200"}},
    ], deadline=200)
    try:
        with pytest.raises(SourceBlocked, match="rate-limit"):
            value.get_preview("Abc_123-X")
        assert len(runtime.goto_calls) == 1 and budget.blocked
        assert budget.stats["retry_after_seconds"] == 7200
        assert budget.stats["source_stop"]["retry_after_seconds"] == 7200
    finally:
        value.close()


def test_inflight_responses_share_one_fallback_but_later_redirects_cannot_reopen_it():
    clock = Clock()
    budget = limiter(clock, max_rate=1.5, start_rate=1.5, learned_rate=1.5)
    details = {"status_code": 302, "redirect_target": "https://edhrec.com/rate-limit"}
    assert budget.record_redirect(details, request_started=99, recover=True)
    assert budget.record_redirect(details, request_started=100, recover=True)
    # A navigation caught by a new pause is cancelled without spending a slot.
    with pytest.raises(ReducedRateRetry):
        budget.wait_document()
    assert budget.stats["requests_started"] == 0
    budget.wait_for_redirect_pause()
    assert clock.value == 160 and budget.stats["requests_started"] == 0
    budget.wait_document()
    assert not budget.record_redirect(details, request_started=160, recover=True)
    assert budget.stats["maximum_requests_per_second"] == 1


def test_fallback_interrupts_an_already_waiting_browser_without_blocking_response_callback():
    clock = Clock()
    waiting, release = threading.Event(), threading.Event()
    first = [True]
    def sleep(seconds):
        if seconds and first[0]:
            first[0] = False
            waiting.set()
            assert release.wait(2)
        clock.sleep(seconds)
    budget = RequestLimiter(max_rate=1.5, start_rate=1.5, monotonic=clock.monotonic, sleep=sleep)
    budget.wait_document()
    errors = []
    def navigate():
        try:
            budget.wait_document()
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=navigate)
    worker.start()
    assert waiting.wait(2)
    assert budget.record_redirect({"status_code": 302}, request_started=100, recover=True)
    release.set()
    worker.join(2)
    assert not worker.is_alive() and len(errors) == 1 and isinstance(errors[0], ReducedRateRetry)
    assert budget.stats["requests_started"] == 1
    assert budget.stats["maximum_requests_per_second"] == 1


def test_browser_decoded_body_is_not_decompressed_twice_by_httpx():
    body = b'<html>already decoded HTML</html>'
    original = {"content-encoding": "br", "content-length": "10", "content-type": "text/html"}
    runtime = FakeRuntime([{"body": body, "headers": original}])
    transport = BrowserHTMLTransport(limiter(), playwright_factory=runtime.factory)
    try:
        response = transport.get_response(PREVIEW)
        assert response.content == body
        assert "content-encoding" not in response.headers
        assert response.headers["content-type"] == "text/html"
        assert response.extensions["edhrec_original_headers"] == original
    finally:
        transport.close()


def gallery_html(rows=None, *, slug="test-commander", total=None, references=False, legacy=False):
    rows = rows if rows is not None else [{"urlhash": "Abc_123-X", "savedate": "2026-11-11", "lands": 36, "creatures": 25}]
    total = len(rows) if total is None else total
    visible = f"<p>Showing <span>{1 if total else 0}</span> to {min(50,total)} of {total:,} decks</p>"
    if legacy:
        data = {"page": "/commanders/[slug]/decks", "query": {"slug": slug},
                "props": {"pageProps": {"data": {"table": rows}}}}
        return visible + '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
    route = {"cardSlug": slug, "section": "commandersFeatured", "tab": "decks"}
    page = {"route": "$2" if references else route, "decks": "$3" if references else rows}
    stream = '1:' + json.dumps(page) + '\n'
    if references:
        stream += '2:' + json.dumps(route) + '\n3:' + json.dumps(rows) + '\n'
    return visible + '<script>self.__next_f.push(' + json.dumps([1, stream]) + ')</script>'


@pytest.mark.parametrize("legacy,references", [(False, False), (False, True), (True, False)])
def test_gallery_complete_index_is_extracted_with_source_identity(legacy, references):
    rows = [{"urlhash": f"deck-{number}", "savedate": "2026-11-11", "lands": 36, "creatures": 25}
            for number in range(113)]
    result = extract_gallery_data(gallery_html(rows, legacy=legacy, references=references), "test-commander")
    assert result == {"table": rows, "commander_slug": "test-commander", "displayed_total": 113, "index_complete": True}


def test_legacy_singular_row_fields_are_accepted_and_not_rewritten():
    rows = [{"urlhash": "deck", "savedate": "2026-11-11", "land": 36, "creature": 25}]
    assert extract_gallery_data(gallery_html(rows), "test-commander")["table"] == rows


def test_empty_gallery_can_be_verified():
    assert extract_gallery_data(gallery_html([]), "test-commander")["displayed_total"] == 0


@pytest.mark.parametrize("legacy", [True, False])
def test_wrong_commander_is_rejected_even_when_slug_is_in_unrelated_text(legacy):
    html = gallery_html(slug="different-commander", legacy=legacy) + '<p>test-commander</p>'
    with pytest.raises(SourceFormatError, match="identity|route"):
        extract_gallery_data(html, "test-commander")


@pytest.mark.parametrize("rows,total", [
    ([{"urlhash": "x", "savedate": "date", "lands": 1, "creatures": 1}], 100),
    ([{"urlhash": "x", "savedate": "date", "lands": 1, "creatures": 1}] * 2, 2),
    ([{"urlhash": "../x", "savedate": "date", "lands": 1, "creatures": 1}], 1),
    ([{"urlhash": "x", "savedate": "date", "lands": 1}], 1),
    (["not a deck row"], 1),
])
def test_incomplete_ambiguous_or_invalid_gallery_data_is_rejected(rows, total):
    with pytest.raises(SourceFormatError):
        extract_gallery_data(gallery_html(rows, total=total), "test-commander")


def test_missing_population_or_route_is_rejected():
    html = gallery_html()
    with pytest.raises(SourceFormatError, match="population"):
        extract_gallery_data(html[html.index('<script>'):], "test-commander")
    with pytest.raises(SourceFormatError, match="identity-checked"):
        extract_gallery_data('<p>Showing 1 to 1 of 1 decks</p>', "test-commander")


def test_flight_reference_cycle_does_not_hang_or_accept_unbound_rows():
    stream = '1:"$2"\n2:"$1"\n'
    html = '<p>Showing 0 to 0 of 0 decks</p><script>self.__next_f.push(' + json.dumps([1, stream]) + ')</script>'
    with pytest.raises(SourceFormatError):
        extract_gallery_data(html, "test-commander")
