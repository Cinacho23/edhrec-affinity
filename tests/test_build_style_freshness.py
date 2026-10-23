"""Observation age and index changes survive incremental collection checkpoints."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import gzip
import threading

import httpx
import pytest

from edhrec_affinity.build_styles.collection import collect_previews, deck_dataset_sha256
from edhrec_affinity.build_styles.sources import Cache, PreviewTransport, RequestLimiter, SourceBlocked, SourceFormatError


START = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def fake_limiter():
    ticks = [0.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        ticks[0] += seconds

    return RequestLimiter(.5, monotonic=lambda: ticks[0], sleep=sleep), ticks, waits


def attach_transport(cache, handler):
    cache.client.close()
    cache.client = httpx.Client(transport=httpx.MockTransport(handler))


def test_cache_ttl_rechecks_source_without_refreshing_offline_observations(tmp_path):
    now = [START]
    calls = []
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path, fetch=True, max_age_seconds=3600, limiter=limiter, now=lambda: now[0])

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"revision": len(calls)})

    attach_transport(cache, handler)
    try:
        assert cache.get("index.json", "https://json.edhrec.com/index.json")["revision"] == 1
        now[0] += timedelta(minutes=30)
        assert cache.get("index.json", "https://json.edhrec.com/index.json")["revision"] == 1
        now[0] += timedelta(hours=1)
        assert cache.get("index.json", "https://json.edhrec.com/index.json")["revision"] == 2
        observed = cache.provenance[-1]["fetched_at"]
        now[0] += timedelta(days=5)
        cache.fetch = False
        assert cache.get("index.json", "https://json.edhrec.com/index.json")["revision"] == 2
        assert cache.provenance[-1]["fetched_at"] == observed
        assert cache.provenance[-1]["cache_stale"]
        assert len(calls) == 2
    finally:
        cache.close()


def test_shared_limiter_spaces_workers_and_access_block_stops_all(tmp_path):
    limiter, ticks, waits = fake_limiter()
    first = Cache(tmp_path / "first", fetch=True, limiter=limiter)
    second = Cache(tmp_path / "second", fetch=True, limiter=limiter)
    starts = []

    def handler(request):
        starts.append(ticks[0])
        return httpx.Response(403 if request.url.path.endswith("blocked.json") else 200, json={})

    attach_transport(first, handler)
    attach_transport(second, handler)
    try:
        first.get("one.json", "https://json.edhrec.com/one.json")
        second.get("two.json", "https://json.edhrec.com/two.json")
        limiter.defer(2)
        with pytest.raises(SourceBlocked, match="403"):
            first.get("blocked.json", "https://json.edhrec.com/blocked.json")
        with pytest.raises(SourceBlocked, match="stopped"):
            second.get("three.json", "https://json.edhrec.com/three.json")
        assert starts == [0, .5, 2.5]
        assert waits == [0, .5, 2]
        assert first.blocked and second.blocked
        assert limiter.stats["requests_started"] == 3
        assert limiter.stats["successful_responses"] == 2
        assert limiter.stats["blocked"]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"pageProps": {"changed": True}}),
    httpx.Response(200, text="<html><title>Deck with Ghave | EDHREC</title></html>"),
])
def test_changed_response_format_does_not_block_other_workers_or_cache_bad_data(tmp_path, response):
    limiter, _, _ = fake_limiter()
    first = Cache(tmp_path / "first", fetch=True, limiter=limiter)
    second = Cache(tmp_path / "second", fetch=True, limiter=limiter)
    attach_transport(first, lambda request: response)
    attach_transport(second, lambda request: httpx.Response(200, json={"valid": True}))
    try:
        with pytest.raises(SourceFormatError, match="Unsupported or invalid response"):
            first.get("one.json", "https://json.edhrec.com/one.json", validate=lambda data: data["deck"])
        assert not first.blocked and not second.blocked
        assert not list((tmp_path / "first").glob("**/*"))
        assert second.get("two.json", "https://json.edhrec.com/two.json") == {"valid": True}
        assert limiter.stats["failures"] == 1
        assert not limiter.stats["blocked"]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("response", [
    httpx.Response(200, text='<html><title>Just a moment...</title><form id="challenge-form"></form></html>'),
    httpx.Response(200, text="Challenge", headers={"cf-mitigated": "challenge"}),
])
def test_http_success_with_explicit_challenge_still_blocks_shared_collection(tmp_path, response):
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path, fetch=True, limiter=limiter)
    calls = []

    def handler(request):
        calls.append(request)
        return response

    attach_transport(cache, handler)
    try:
        with pytest.raises(SourceBlocked, match="Access challenge"):
            cache.get("one.json", "https://json.edhrec.com/one.json")
        with pytest.raises(SourceBlocked, match="stopped"):
            cache.get("two.json", "https://json.edhrec.com/two.json")
        assert len(calls) == 1 and cache.blocked
        assert not list(tmp_path.iterdir())
    finally:
        cache.close()


def test_failed_format_refresh_preserves_the_last_usable_cached_observation(tmp_path):
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path, fetch=True, limiter=limiter, now=lambda: START)
    responses = iter([httpx.Response(200, json={"deck": {"cards": {}}}),
                      httpx.Response(200, json={"changed": True})])
    attach_transport(cache, lambda request: next(responses))
    try:
        expected = cache.get("one.json", "https://json.edhrec.com/one.json")
        before_metadata = (tmp_path / "one.json.meta.json").read_bytes()
        with pytest.raises(SourceFormatError):
            cache.get("one.json", "https://json.edhrec.com/one.json", force_refresh=True,
                      validate=lambda data: data["deck"])
        assert json.loads((tmp_path / "one.json").read_text()) == expected
        assert (tmp_path / "one.json.meta.json").read_bytes() == before_metadata
        assert not cache.blocked
    finally:
        cache.close()


class UpdatingSource:
    def __init__(self, now):
        self.now = now
        self.rows = [{"urlhash": name, "savedate": "2026-10-20", "bracket": 3,
                      "creature": 30, "price": 100, "salt": 10} for name in ("keep", "edit", "remove")]
        self.calls = []
        self.provenance = []
        self.blocked = False

    def get(self, relative, url, **kwargs):
        self.calls.append((relative, kwargs))
        self.provenance.append({"url": url, "fetched_at": self.now.isoformat(), "sha256": "hash"})
        if relative == "decks.json":
            return {"table": self.rows}
        return {"deck": {"commander": ["Ghave"], "cards": {"Land": [["Forest", 99]]}}}


def collect(source, path):
    return collect_previews(source, "ghave", limit=None, seed=42, progress_path=path,
                            index_max_age_seconds=3600, preview_max_age_seconds=72 * 3600, now=source.now)


def test_refresh_reconciles_new_removed_and_changed_rows_without_refetching_unchanged_lists(tmp_path):
    source = UpdatingSource(START)
    path = tmp_path / "state.gz"
    _, initial = collect(source, path)
    assert initial["freshness"]["source_complete"]
    source.calls.clear()
    source.now += timedelta(hours=2)
    source.rows = [
        {**source.rows[0], "price": 200, "salt": 20},  # Market changes do not imply a different list.
        {**source.rows[1], "savedate": "2026-10-21", "creature": 31},
        {**source.rows[2], "urlhash": "added"},
    ]
    decks, status = collect(source, path)
    assert {deck["id"] for deck in decks} == {"keep", "edit", "added"}
    assert [name for name, _ in source.calls] == ["decks.json", "previews/added.html", "previews/edit.html"]
    assert source.calls[-1][1]["force_refresh"]
    assert status["reconciliation"] == {"added": 1, "removed": 1, "changed": 1, "refreshed_index": True}
    assert status["revalidated_previews"] == 1
    assert status["freshness"]["oldest_source_at"] == START.isoformat()
    assert status["freshness"]["newest_source_at"] == source.now.isoformat()
    assert status["freshness"]["target_hours"] == 96
    assert status["freshness"]["max_age_hours"] == 72
    assert status["freshness"]["source_complete"]


def test_same_id_still_revalidates_after_preview_max_age(tmp_path):
    source = UpdatingSource(START)
    path = tmp_path / "state.gz"
    collect(source, path)
    source.now += timedelta(hours=73)
    source.calls.clear()
    _, status = collect(source, path)
    assert len(source.calls) == 4  # Current index and all three retained IDs.
    assert status["revalidated_previews"] == 3
    assert status["freshness"]["oldest_source_at"] == source.now.isoformat()


def test_current_immutable_index_versions_revalidate_without_redownloading_unchanged_lists(tmp_path):
    source = UpdatingSource(START)
    path = tmp_path / "state.gz"
    collect(source, path)
    source.now += timedelta(hours=73)
    source.calls.clear()
    _, status = collect_previews(source, "ghave", limit=None, seed=42, progress_path=path,
                                index_max_age_seconds=3600, preview_max_age_seconds=72 * 3600,
                                now=source.now, trust_index_versions=True)
    assert [name for name, _ in source.calls] == ["decks.json"]
    freshness = status["freshness"]
    assert freshness["basis"] == "current_index_versions"
    assert freshness["oldest_source_at"] == START.isoformat()
    assert freshness["oldest_verified_at"] == source.now.isoformat()
    assert freshness["unchanged_verified"] == 3
    assert freshness["stale_previews"] == 0 and freshness["source_complete"]


def preview_html(identifier, build_id="build-one"):
    data = {"deck": {"commander": ["Ghave"], "cards": {"Land": [[identifier, 99]]}}}
    return '<script id="__NEXT_DATA__">' + json.dumps({
        "buildId": build_id, "props": {"pageProps": {"data": data}},
    }) + "</script>"


def test_compact_transport_discovers_shared_build_and_refreshes_after_deployment(tmp_path):
    limiter, _, _ = fake_limiter()
    transport = PreviewTransport()
    cache = Cache(tmp_path, fetch=True, limiter=limiter, preview_transport=transport)
    paths = []
    deployed = [False]

    def handler(request):
        paths.append(request.url.path)
        identifier = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        if request.url.path.startswith("/_next/"):
            if deployed[0] and "/build-one/" in request.url.path:
                return httpx.Response(404)
            return httpx.Response(200, json={"pageProps": {"data": {
                "deck": {"commander": ["Ghave"], "cards": {"Land": [[identifier, 99]]}},
            }}})
        return httpx.Response(200, text=preview_html(identifier, "build-two" if deployed[0] else "build-one"))

    attach_transport(cache, handler)
    try:
        cache.get_preview("one")
        cache.get_preview("two")
        assert paths == ["/deckpreview/one", "/_next/data/build-one/deckpreview/two.json"]
        assert cache.provenance_for("https://edhrec.com/deckpreview/two")[0]["url"].endswith("two.json")
        deployed[0] = True
        cache.get_preview("three")
        cache.get_preview("four")
        assert paths[-3:] == ["/_next/data/build-one/deckpreview/three.json", "/deckpreview/three",
                              "/_next/data/build-two/deckpreview/four.json"]
        assert not (tmp_path / "previews/three.json").exists()  # The 404 was never cached.
        assert (tmp_path / "previews/four.json").exists()
    finally:
        cache.close()


def test_compact_access_block_never_falls_back_to_another_route(tmp_path):
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path, fetch=True, limiter=limiter, preview_transport=PreviewTransport())
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(403) if request.url.path.startswith("/_next/") else httpx.Response(200, text=preview_html("one"))

    attach_transport(cache, handler)
    try:
        cache.get_preview("one")
        with pytest.raises(SourceBlocked, match="403"):
            cache.get_preview("two")
        assert paths == ["/deckpreview/one", "/_next/data/build-one/deckpreview/two.json"]
        assert not (tmp_path / "previews/two.json").exists()
    finally:
        cache.close()


def test_parallel_previews_keep_each_list_bound_to_its_own_provenance(tmp_path):
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path / "raw", fetch=True, limiter=limiter, now=lambda: START)
    barrier = threading.Barrier(3)

    def handler(request):
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json={"table": [{"urlhash": f"deck-{i}"} for i in range(6)]})
        barrier.wait(timeout=3)
        return httpx.Response(200, text=preview_html(request.url.path.rsplit("/", 1)[-1]))

    attach_transport(cache, handler)
    path = tmp_path / "state.gz"
    try:
        decks, status = collect_previews(cache, "ghave", limit=None, seed=42, progress_path=path,
                                        preview_workers=3, now=START)
        assert len(decks) == 6 and status["complete"]
        with gzip.open(path, "rt") as handle:
            records = json.load(handle)["results"]
        for identifier, record in records.items():
            assert len(record["provenance"]) == 1
            assert record["provenance"][0]["url"].endswith("/" + identifier)
            assert record["deck"]["cards"][0]["name"] == identifier
    finally:
        cache.close()


def test_adaptive_rate_ramps_only_after_successes_and_slows_on_failure():
    ticks = [0.0]
    limiter = RequestLimiter(max_rate=40, start_rate=2, ramp_after=2,
                             monotonic=lambda: ticks[0], sleep=lambda seconds: None)
    assert limiter.delay == .5
    limiter.record_success()
    assert limiter.delay == .5
    limiter.record_success()
    assert limiter.delay == .4
    for _ in range(100):
        limiter.record_success()
    assert limiter.delay == pytest.approx(1 / 40)
    limiter.record_failure(cooldown=3)
    assert limiter.delay == pytest.approx(1 / 20)
    assert limiter._next_request == 3


@pytest.mark.parametrize("learned_rate", [True, False, 0, -1, float("inf"), float("nan"), "2"])
def test_learned_request_rate_rejects_invalid_persistent_values(learned_rate):
    with pytest.raises(ValueError, match="Learned request rate"):
        RequestLimiter(max_rate=40, learned_rate=learned_rate)


def test_persistent_learned_ceiling_limits_startup_and_future_ramping():
    limiter = RequestLimiter(max_rate=40, learned_rate=1.25, ramp_after=1)
    assert limiter.delay == .8
    for _ in range(100):
        limiter.record_success()
    assert limiter.delay == .8
    assert limiter.stats["configured_maximum_requests_per_second"] == 40
    assert limiter.stats["maximum_requests_per_second"] == 1.25
    assert limiter.stats["learned_maximum_requests_per_second"] == 1.25
    # A previously higher limit cannot override a lower configured maximum.
    lowered_configuration = RequestLimiter(max_rate=1, learned_rate=1.25)
    assert lowered_configuration.stats["maximum_requests_per_second"] == 1
    assert lowered_configuration.learning_state["learned_rate"] == 1


def test_throttle_learns_from_current_rate_once_per_job_and_survives_restart():
    limiter = RequestLimiter(max_rate=40, ramp_after=1, monotonic=lambda: 0)
    limiter.record_success()  # Current rate is 2.5, below the configured ceiling.
    limiter.record_throttle(now=START)
    assert limiter.learning_state == {
        "learned_rate": 1.875, "throttles": 1,
        "last_throttle": {"at": START.isoformat(), "rate_before": 2.5, "rate_after": 1.875},
    }
    workers = [threading.Thread(target=limiter.record_throttle) for _ in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert limiter.learning_state["throttles"] == 1
    assert limiter.learning_state["learned_rate"] == 1.875
    limiter.record_failure(cooldown=0)
    for _ in range(100):
        limiter.record_success()
    assert limiter.stats["current_requests_per_second"] <= 1.875
    restarted = RequestLimiter(max_rate=40, learned_rate=limiter.learning_state["learned_rate"])
    assert restarted.stats["current_requests_per_second"] == 1.875
    restarted.record_throttle(now=START + timedelta(hours=1))
    assert restarted.learning_state["learned_rate"] == 1.40625


def test_learning_can_reduce_rates_below_a_fixed_practical_floor():
    limiter = RequestLimiter(max_rate=.02, learned_rate=.01)
    limiter.record_throttle(now=START)
    assert limiter.stats["maximum_requests_per_second"] == pytest.approx(.0075)
    assert limiter.stats["current_requests_per_second"] == pytest.approx(.0075)


@pytest.mark.parametrize("status", [403, 429])
def test_only_http_429_lowers_the_ceiling_while_both_access_codes_stop_collection(tmp_path, status):
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path, fetch=True, limiter=limiter, now=lambda: START)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "7200"})

    attach_transport(cache, handler)
    try:
        with pytest.raises(SourceBlocked, match=str(status)):
            cache.get("one.json", "https://json.edhrec.com/one.json")
        assert limiter.learning_state["learned_rate"] == (1.5 if status == 429 else None)
        assert limiter.learning_state["throttles"] == (1 if status == 429 else 0)
        assert limiter.stats["block_kind"] == ("rate_limit" if status == 429 else "access_restriction")
        assert limiter.stats["retry_after_seconds"] == 7200
        with pytest.raises(SourceBlocked, match="stopped"):
            cache.get("two.json", "https://json.edhrec.com/two.json")
        assert len(calls) == 1
    finally:
        cache.close()


@pytest.mark.parametrize("kinds", [
    ["rate_limit", "access_restriction", "rate_limit", None],
    ["access_restriction", "rate_limit", None],
])
def test_access_restriction_stays_sticky_after_rate_and_generic_stops(kinds):
    limiter = RequestLimiter()
    for index, kind in enumerate(kinds):
        limiter.block(f"stop {index}", kind=kind)
    assert limiter.blocked
    assert limiter.stats["block_kind"] == "access_restriction"


def test_in_flight_403_after_429_cannot_become_a_rate_limit_auto_resume(tmp_path):
    limiter, _, _ = fake_limiter()
    rate_cache = Cache(tmp_path / "rate", fetch=True, limiter=limiter)
    access_cache = Cache(tmp_path / "access", fetch=True, limiter=limiter)
    in_flight = threading.Barrier(2)
    errors = []

    def handler(request):
        in_flight.wait(timeout=3)
        if request.url.path == "/rate.json":
            return httpx.Response(429)
        assert limiter._blocked_event.wait(timeout=3)  # The 429 was processed first.
        return httpx.Response(403)

    attach_transport(rate_cache, handler)
    attach_transport(access_cache, handler)

    def read(cache, name):
        try:
            cache.get(name + ".json", "https://json.edhrec.com/" + name + ".json")
        except SourceBlocked as error:
            errors.append(str(error))

    workers = [threading.Thread(target=read, args=(rate_cache, "rate")),
               threading.Thread(target=read, args=(access_cache, "access"))]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert len(errors) == 2
        assert limiter.stats["throttles"] == 1
        assert limiter.stats["block_kind"] == "access_restriction"
        limiter.block("Shared collection stopped")
        assert limiter.stats["block_kind"] == "access_restriction"
    finally:
        rate_cache.close()
        access_cache.close()


def test_access_block_interrupts_a_worker_already_waiting_for_its_request_slot():
    def sleep(seconds):
        if seconds:
            limiter.block("HTTP 429 from another worker")

    limiter = RequestLimiter(.5, monotonic=lambda: 0, sleep=sleep)
    limiter.wait()
    with pytest.raises(SourceBlocked, match="429"):
        limiter.wait()


@pytest.mark.parametrize("header,expected", [
    ("7200", 7200),
    (format_datetime(START + timedelta(hours=3), usegmt=True), 10800),
])
def test_rate_limit_retains_server_retry_after_across_shared_workers(tmp_path, header, expected):
    limiter, ticks, _ = fake_limiter()
    cache = Cache(tmp_path, fetch=True, limiter=limiter, now=lambda: START)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": header})

    attach_transport(cache, handler)
    try:
        with pytest.raises(SourceBlocked, match="429"):
            cache.get("one.json", "https://json.edhrec.com/one.json")
        assert limiter.stats["retry_after_seconds"] == expected
        ticks[0] += 60
        assert limiter.stats["retry_after_seconds"] == expected - 60
        with pytest.raises(SourceBlocked, match="stopped"):
            cache.get("two.json", "https://json.edhrec.com/two.json")
        assert len(calls) == 1  # Retry-After schedules no automatic retry.
        assert not list(tmp_path.iterdir())
    finally:
        cache.close()


def test_dataset_hash_ignores_observation_age_but_tracks_actual_model_inputs():
    deck = {"id": "one", "commanders": ["Ghave"], "source_bracket": 3,
            "cards": [{"name": "Forest", "quantity": 99, "type_line": "Land"}],
            "savedate": "2026-10-20", "source_at": START.isoformat()}
    digest = deck_dataset_sha256([deck])
    assert deck_dataset_sha256([{**deck, "savedate": "2026-10-21", "source_at": "later", "tags": ["new label"]}]) == digest
    assert deck_dataset_sha256([{**deck, "source_bracket": 4}]) != digest
    assert deck_dataset_sha256([{**deck, "cards": [{**deck["cards"][0], "quantity": 98}]}]) != digest


def test_offline_reanalysis_never_labels_old_saved_lists_as_a_fresh_full_source(tmp_path):
    now = [START]
    limiter, _, _ = fake_limiter()
    cache = Cache(tmp_path / "raw", fetch=True, limiter=limiter, now=lambda: now[0])

    def handler(request):
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json={"table": [{"urlhash": "one", "savedate": "2026-10-20"}]})
        payload = {"props": {"pageProps": {"data": {
            "deck": {"commander": ["Ghave"], "cards": {"Land": [["Forest", 99]]}},
        }}}}
        return httpx.Response(200, text='<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>")

    attach_transport(cache, handler)
    kwargs = {"limit": None, "seed": 42, "progress_path": tmp_path / "state.gz",
              "index_max_age_seconds": 3600, "preview_max_age_seconds": 72 * 3600}
    try:
        _, first = collect_previews(cache, "ghave", now=now[0], **kwargs)
        assert first["freshness"]["source_complete"]
        cache.fetch = False
        now[0] += timedelta(days=5)
        decks, later = collect_previews(cache, "ghave", now=now[0], **kwargs)
        assert len(decks) == 1  # Retained for an explicitly dated partial analysis.
        assert not later["complete"] and not later["freshness"]["source_complete"]
        assert later["freshness"]["oldest_source_at"] == START.isoformat()
        assert later["freshness"]["stale_previews"] == 1
        assert later["freshness"]["index_stale"]
    finally:
        cache.close()
