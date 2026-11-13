"""Ordinary public EDHREC HTML through an isolated, standard WebKit browser.

Only document navigation is permitted. There is no login, copied session,
identity override, challenge handling, or HTTP fallback. A single owned thread
keeps Playwright's synchronous API safe when collection uses a thread pool.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import urlsplit

import httpx

from .sources import (
    BudgetExpired, RequestLimiter, SourceBlocked, SourceFormatError,
    _NextDataParser, checked_identifier, source_stop_details,
)


_CHALLENGE_MARKERS = (
    "/cdn-cgi/challenge-platform/", "cf_chl_opt", 'id="challenge-form"',
    "id='challenge-form'", "<title>just a moment",
    "<title>attention required! | cloudflare", "banned - too many requests",
)
_DOCUMENT_PATH = re.compile(
    r"/(?:deckpreview/[A-Za-z0-9_-]+|commanders/[a-z0-9-]+/decks)"
)


def _challenge(headers: dict, body: bytes) -> bool:
    text = body.decode("utf-8", errors="replace").lower()
    return headers.get("cf-mitigated", "").lower() == "challenge" or any(
        marker in text for marker in _CHALLENGE_MARKERS
    )


class BrowserHTMLTransport:
    """One reusable browser/context, one paced main document at a time.

    Redirects are unexpected source changes and stop this transport. Public
    Playwright routing does not promise interception of every redirect hop;
    redirect request/response events are therefore recorded and terminate the
    navigation, never used to discover or retry another endpoint.
    """

    def __init__(self, limiter: RequestLimiter, *, timeout_seconds: float = 25,
                 playwright_factory=None):
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds < float("inf"):
            raise ValueError("Browser timeout must be finite and positive")
        self.limiter = limiter
        self.timeout_seconds = timeout_seconds
        self._playwright_factory = playwright_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edhrec-webkit")
        self._submission_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._closed = False
        self._runtime = self._browser = self._context = None
        self._active_url = None
        self._active_page = None
        self._navigation_error = None
        self._stopped_error = None
        self._admitted_document = False
        self._requests = []
        self._redirects = []
        self._aborted_resources = 0

    @staticmethod
    def supports_url(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and parsed.netloc == "edhrec.com"
                    and not parsed.query and not parsed.fragment
                    and _DOCUMENT_PATH.fullmatch(parsed.path) is not None)
        except (TypeError, ValueError):
            return False

    @property
    def stats(self) -> dict:
        with self._stats_lock:
            return {"engine": "webkit", "headless": True,
                    "identity_overrides": False, "copied_session_credentials": False,
                    "document_requests": len(self._requests),
                    "request_starts": [dict(row) for row in self._requests],
                    "redirects": [dict(row) for row in self._redirects],
                    "aborted_resources": self._aborted_resources}

    def get_response(self, url: str) -> httpx.Response:
        if not self.supports_url(url):
            raise ValueError("WebKit only accepts ordinary public EDHREC preview/gallery HTTPS URLs")
        # Waiting callers cannot start another navigation before the caller that
        # receives a denial has observed it. The limiter also carries the stop.
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("Browser HTML transport is closed")
            if self.limiter.blocked:
                raise SourceBlocked("Collection stopped after an access challenge or rate limit")
            if self._stopped_error is not None:
                raise self._stopped_error
            self.limiter.check_budget()
            return self._executor.submit(self._get_on_worker, url).result()

    def _start(self):
        if self._context is not None:
            return
        if self._playwright_factory is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as error:
                raise RuntimeError("WebKit collection requires Playwright and its WebKit browser installation") from error
            factory = sync_playwright
        else:
            factory = self._playwright_factory
        self._runtime = factory().start()
        self._browser = self._runtime.webkit.launch(headless=True)
        # No user agent, proxy, storage state, headers, or browser arguments.
        self._context = self._browser.new_context(accept_downloads=False, service_workers="block")
        self._context.route("**/*", self._gate)
        self._context.on("request", self._observe_request)
        self._context.on("response", self._observe_response)

    def _gate(self, route):
        request = route.request
        if self.limiter.blocked and self._navigation_error is None:
            self._navigation_error = SourceBlocked("Collection stopped after an access challenge or rate limit")
        if (self.limiter.blocked or self._navigation_error is not None
                or not request.is_navigation_request()
                or request.resource_type != "document"
                or request.frame.parent_frame is not None
                or request.url != self._active_url
                or self._admitted_document or request.redirected_from is not None):
            with self._stats_lock:
                self._aborted_resources += 1
            route.abort()
            return
        try:
            self.limiter.wait()
        except (BudgetExpired, SourceBlocked) as error:
            self._navigation_error = error
            route.abort()
            return
        self._admitted_document = True
        with self._stats_lock:
            self._requests.append({"url": request.url,
                                   "at": datetime.now(timezone.utc).isoformat(),
                                   "monotonic": time.monotonic()})
        route.continue_()

    def _observe_request(self, request):
        if request.is_navigation_request() and request.redirected_from is not None:
            self._stop_redirect(request.url, request.redirected_from.url)

    def _stop_redirect(self, target, origin):
        with self._stats_lock:
            self._redirects.append({"from": origin, "to": target,
                                    "at": datetime.now(timezone.utc).isoformat()})
        self._navigation_error = SourceFormatError(
            "Unexpected EDHREC browser redirect; collection stopped without retry"
        )
        self._stopped_error = self._navigation_error
        self._block(origin, 302, {}, challenge=True)
        # Closing the page is the public API's immediate cancellation mechanism.
        # A browser-generated redirect may already have begun when its event is
        # delivered, so the evidence explicitly retains that limitation.
        if self._active_page is not None and not self._active_page.is_closed():
            self._active_page.close()

    def _observe_response(self, response):
        if not response.request.is_navigation_request():
            return
        if 300 <= response.status < 400 and response.status != 304:
            self._stop_redirect(response.headers.get("location", ""), response.url)
        elif response.status in {401, 403, 429}:
            self._block(response.url, response.status, response.headers)

    def _block(self, url, status, headers, *, challenge=False):
        details = source_stop_details(
            "edhrec", url, status, datetime.now(timezone.utc),
            retry_after=headers.get("retry-after"),
            reason="access_restriction" if challenge else None,
        )
        self.limiter.block(f"{'Access challenge' if challenge else 'HTTP ' + str(status)}: {url}",
                           kind=details["reason"], details=details)

    def _get_on_worker(self, url):
        page = None
        try:
            self._start()
            self._active_url = url
            self._navigation_error = None
            self._admitted_document = False
            page = self._context.new_page()
            self._active_page = page
            response = page.goto(url, wait_until="commit", timeout=self.timeout_seconds * 1000)
            if self._navigation_error is not None:
                raise self._navigation_error
            if response is None:
                raise SourceFormatError("Browser navigation did not return an HTML document")
            body = response.body()
            headers = response.all_headers()
            if response.url != url or response.request.redirected_from is not None:
                raise SourceFormatError("Unexpected EDHREC browser redirect; no retry attempted")
            if _challenge(headers, body):
                self._block(url, response.status, headers, challenge=True)
            # Browser response.body() has already decoded the HTTP content
            # encoding. Passing that header into httpx would decode it twice.
            decoded_headers = {key: value for key, value in headers.items()
                               if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
            return httpx.Response(response.status, headers=decoded_headers, content=body,
                                  request=httpx.Request("GET", url),
                                  extensions={"edhrec_browser": "webkit", "edhrec_original_headers": headers})
        except (BudgetExpired, SourceBlocked, SourceFormatError):
            raise
        except Exception as error:
            if self._navigation_error is not None:
                raise self._navigation_error from error
            raise httpx.TransportError(f"WebKit document request failed: {error}", request=httpx.Request("GET", url)) from error
        finally:
            if page is not None and not page.is_closed():
                page.close()
            self._active_page = None
            self._active_url = None

    def _close_on_worker(self):
        try:
            if self._context is not None:
                self._context.close()
        finally:
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                if self._runtime is not None:
                    self._runtime.stop()
                self._context = self._browser = self._runtime = None

    def close(self):
        with self._submission_lock:
            if not self._closed:
                self._closed = True
                try:
                    self._executor.submit(self._close_on_worker).result()
                finally:
                    self._executor.shutdown(wait=True)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extract_gallery_data(html: str, slug: str) -> dict:
    """Extract a complete, identity-checked public gallery index, without JS."""
    checked_identifier(slug)
    parser = _NextDataParser()
    parser.feed(html)
    records = {}
    if parser.fragments:
        try:
            payload = json.loads("".join(parser.fragments))
            page = payload.get("page", "")
            query = payload.get("query", {})
            identity = next((query[key] for key in ("slug", "commanderSlug", "cardSlug", "commander") if key in query), None)
            if (identity != slug or not re.fullmatch(r"/commanders/\[[A-Za-z]+\]/decks", page)):
                raise SourceFormatError("Legacy gallery commander identity does not match the requested page")
            rows = payload["props"]["pageProps"]["data"]["table"]
        except SourceFormatError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise SourceFormatError("Unsupported legacy commander gallery data") from error
    else:
        decoder, fragments = json.JSONDecoder(), []
        for script in parser.scripts:
            for match in re.finditer(r"(?:^|;)\s*self\.__next_f\.push\(", script):
                try:
                    chunk, _ = decoder.raw_decode(script[match.end():])
                except ValueError:
                    continue
                if isinstance(chunk, list) and len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):
                    fragments.append(chunk[1])
        for line in "".join(fragments).splitlines():
            identifier, separator, value = line.partition(":")
            if separator and re.fullmatch(r"[0-9a-f]+", identifier):
                try:
                    records[identifier] = json.loads(value)
                except ValueError:
                    continue

        def resolve(value):
            seen = set()
            while isinstance(value, str) and re.fullmatch(r"\$[0-9a-f]+", value):
                key = value[1:]
                if key in seen or key not in records:
                    break
                seen.add(key)
                value = records[key]
            return value

        pending, visited, candidates = list(records.values()), set(), []
        while pending:
            value = resolve(pending.pop())
            if isinstance(value, (dict, list)):
                if id(value) in visited:
                    continue
                visited.add(id(value))
            if isinstance(value, dict):
                route = resolve(value.get("route"))
                if isinstance(route, dict) and route.get("tab") == "decks":
                    if (route.get("cardSlug") != slug or route.get("section") not in {"commanders", "commandersFeatured"}):
                        raise SourceFormatError("Gallery source route does not match the requested commander")
                    candidates.append(resolve(value.get("decks")))
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        if len(candidates) != 1 or not isinstance(candidates[0], list):
            raise SourceFormatError("Gallery is missing one identity-checked deck index")
        rows = [resolve(row) for row in candidates[0]]
    if not isinstance(rows, list):
        raise SourceFormatError("Commander gallery deck index is not a list")
    identifiers = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("urlhash"), str)
                or "savedate" not in row
                or not ({"land", "lands"} & row.keys())
                or not ({"creature", "creatures"} & row.keys())):
            raise SourceFormatError("Commander gallery contains an unsupported deck row")
        try:
            identifier = checked_identifier(row["urlhash"], deck=True)
        except ValueError as error:
            raise SourceFormatError("Commander gallery contains an invalid deck identifier") from error
        if identifier in identifiers:
            raise SourceFormatError("Commander gallery contains duplicate deck identifiers")
        identifiers.add(identifier)
    visible = _VisibleText()
    visible.feed(html)
    text = " ".join(" ".join(visible.parts).split())
    match = re.search(r"Showing\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+)\s+decks", text, re.I)
    if match is None:
        raise SourceFormatError("Gallery does not expose its displayed deck population")
    start, end, total = [int(value.replace(",", "")) for value in match.groups()]
    if total != len(rows) or not 0 <= start <= end <= total:
        raise SourceFormatError("Gallery embedded deck index does not match its displayed population")
    return {"table": rows, "commander_slug": slug, "displayed_total": total, "index_complete": True}
