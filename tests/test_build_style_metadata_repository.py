"""Catalog metadata lookups share work while retaining source freshness."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading

import httpx
import pytest

from edhrec_affinity.build_styles import card_metadata as metadata
from edhrec_affinity.build_styles.sources import BudgetExpired


HTTPX_CLIENT = httpx.Client


def card(name="Forest"):
    return {"object": "card", "name": name, "type_line": "Basic Land — Forest",
            "oracle_text": "({T}: Add {G}.)", "color_identity": ["G"],
            "legalities": {"commander": "legal"}, "keywords": [], "cmc": 0,
            "mana_cost": "", "layout": "normal", "produced_mana": ["G"],
            "colors": ["G"], "edhrec_rank": 1000, "reserved": False,
            "prices": {"usd": ".15", "usd_foil": None, "usd_etched": None}}


def mock_client(monkeypatch, handler):
    monkeypatch.setattr(metadata.httpx, "Client", lambda **kwargs: HTTPX_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))
    delays = []
    monkeypatch.setattr(metadata.time, "sleep", delays.append)
    return delays


def response(names):
    return httpx.Response(200, json={"object": "list", "has_more": False,
                                    "data": [card(name) for name in names], "not_found": []})


def seed_cache(path, names, age_days=0):
    checked = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    metadata._write_cache(path, {metadata.card_key(name): metadata._metadata(card(name), checked) for name in names})
    return checked


def test_metadata_budget_stops_fallback_requests_and_preserves_completed_batches(tmp_path, monkeypatch):
    ticks, attempts = [0.0], []

    def handler(request):
        names = [item["name"] for item in json.loads(request.content)["identifiers"]]
        attempts.append(names)
        ticks[0] = 2
        return httpx.Response(200, json={"object": "list", "has_more": False,
            "data": [card("Forest")], "not_found": [{"name": "Missing"}]})

    mock_client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    repository = metadata.MetadataRepository(cache, deadline=1, monotonic=lambda: ticks[0])
    with pytest.raises(BudgetExpired):
        repository.load(["Forest", "Missing"], fetch=True)
    assert attempts == [["Forest", "Missing"]]
    assert not repository.blocked
    found, summary = metadata.MetadataRepository(cache).load(["Forest"])
    assert found["forest"]["name"] == "Forest"
    assert summary["cached"] == 1


def test_repository_reads_and_indexes_the_shared_cache_once(tmp_path, monkeypatch):
    cache = tmp_path / "cards.json"
    seed_cache(cache, ["Forest", "Island"])
    repository = metadata.MetadataRepository(cache)
    first, _ = repository.load(["Forest"])
    cache.unlink()
    monkeypatch.setattr(metadata, "_read_cache", lambda _: pytest.fail("Shared cache was reread"))
    monkeypatch.setattr(metadata, "_index", lambda _: pytest.fail("Cached lookup rebuilt the complete index"))
    second, summary = repository.load(["Island"], fetch=True)
    assert set(first) == {"forest"}
    assert set(second) == {"island"}
    assert summary["cached"] == 1
    assert summary["requests"] == 0


def test_concurrent_overlapping_pools_fetch_a_card_only_once(tmp_path, monkeypatch):
    attempts = []

    def handler(request):
        names = [item["name"] for item in json.loads(request.content)["identifiers"]]
        attempts.append(names)
        return response(names)

    delays = mock_client(monkeypatch, handler)
    repository = metadata.MetadataRepository(tmp_path / "cards.json")
    barrier = threading.Barrier(8)

    def lookup(_):
        barrier.wait(timeout=5)
        return repository.load(["Forest"], fetch=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lookup, range(8)))
    assert attempts == [["Forest"]]
    assert delays == [.1]
    assert sum(summary["fetched"] for _, summary in results) == 1
    assert sum(summary["cached"] for _, summary in results) == 7
    assert all(found["forest"]["legalities"]["commander"] == "legal" for found, _ in results)


def test_separate_concurrent_pools_share_scryfall_pacing(tmp_path, monkeypatch):
    attempts = []

    def handler(request):
        names = [item["name"] for item in json.loads(request.content)["identifiers"]]
        attempts.extend(names)
        return response(names)

    delays = mock_client(monkeypatch, handler)
    repository = metadata.MetadataRepository(tmp_path / "cards.json")
    names = [f"Card {index}" for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda name: repository.load([name], fetch=True), names))
    assert sorted(attempts) == names
    assert delays == [.1] * 8


def test_returned_nested_metadata_cannot_pollute_the_repository(tmp_path):
    cache = tmp_path / "cards.json"
    original = seed_cache(cache, ["Forest"])
    repository = metadata.MetadataRepository(cache)
    found, _ = repository.load(["Forest"])
    found["forest"]["legalities"]["commander"] = "banned"
    found["forest"]["produced_mana"].clear()
    found["forest"]["checked_at"] = "2099-01-01T00:00:00+00:00"
    found["forest"]["_build_cache"] = {"tags": ["Wrong"]}
    again, summary = repository.load(["Forest"])
    assert again["forest"]["legalities"]["commander"] == "legal"
    assert again["forest"]["produced_mana"] == ["G"]
    assert again["forest"]["checked_at"] == original
    assert "_build_cache" not in again["forest"]
    assert summary["stale_names"] == []


def test_three_day_repository_policy_does_not_change_legacy_seven_days(tmp_path, monkeypatch):
    cache = tmp_path / "cards.json"
    old = seed_cache(cache, ["Forest"], age_days=4)
    mock_client(monkeypatch, lambda _: response(["Forest"]))
    _, legacy_summary = metadata.load_card_metadata(["Forest"], cache, fetch=True)
    assert legacy_summary["requests"] == 0
    repository = metadata.MetadataRepository(cache, max_age_days=3)
    offline, stale_summary = repository.load(["Forest"])
    assert offline["forest"]["checked_at"] == stale_summary["oldest_checked_at"] == old
    assert stale_summary["stale_names"] == ["Forest"]
    fresh, summary = repository.load(["Forest"], fetch=True)
    assert fresh["forest"]["checked_at"] != old
    assert summary["cache_max_age_days"] == 3
    assert summary["fetched"] == 1
    assert summary["stale_cached"] == 0


@pytest.mark.parametrize("status", [401, 403, 429])
def test_access_stop_is_shared_across_later_repository_calls(tmp_path, monkeypatch, status):
    cache = tmp_path / "cards.json"
    old = seed_cache(cache, ["Forest"], age_days=4)
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(status, json={"object": "error"})

    delays = mock_client(monkeypatch, handler)
    repository = metadata.MetadataRepository(cache)
    first, summary = repository.load(["Forest"], fetch=True)
    _, later = repository.load(["Forest", "Island"], fetch=True)
    assert repository.blocked
    assert summary["blocked"] and later["blocked"]
    assert len(attempts) == 1
    assert delays == [.1]
    assert later["requests"] == later["fetched"] == 0
    assert first["forest"]["checked_at"] == later["oldest_checked_at"] == old
    assert later["stale_names"] == ["Forest"]
    assert later["missing"] == ["Island"]
    assert json.loads(cache.read_text())["cards"]["forest"]["checked_at"] == old


@pytest.mark.parametrize("age", [0, -1, float("nan"), float("inf"), True, "3"])
def test_repository_rejects_invalid_freshness_policy(tmp_path, age):
    with pytest.raises(ValueError, match="positive finite"):
        metadata.MetadataRepository(tmp_path / "cards.json", max_age_days=age)


def test_explicit_commander_equivalent_reuses_cached_canonical_with_original_request_counts(tmp_path, monkeypatch):
    cache = tmp_path / "cards.json"
    seed_cache(cache, ["Elmar, Ulvenwald Informant"])
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Fresh equivalent should reuse cache"))
    found, summary = metadata.MetadataRepository(cache).load(
        ["Max, the Daredevil"], fetch=True,
        commander_alias_groups=[["Elmar, Ulvenwald Informant", "Max, the Daredevil"]],
    )
    assert found["max, the daredevil"]["name"] == "Elmar, Ulvenwald Informant"
    assert found["elmar, ulvenwald informant"] is found["max, the daredevil"]
    assert summary["requested"] == summary["resolved"] == summary["cached"] == 1
    assert summary["requests"] == summary["fetched"] == 0
    assert summary["alias_resolutions"][0]["source"] == "edhrec_commander_equivalence"
    # EDHREC equivalences remain scoped to their verified commander input;
    # they are not arbitrary permanent Scryfall aliases in the shared cache.
    found, summary = metadata.MetadataRepository(cache).load(["Max, the Daredevil"])
    assert not found and summary["missing"] == ["Max, the Daredevil"]


def test_exact_missing_commander_falls_back_to_verified_equivalent(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"object": "list", "data": [],
                                            "not_found": [{"name": "Max, the Daredevil"}]})
        assert set(request.url.params) == {"exact"}
        if request.url.params["exact"] == "Max, the Daredevil":
            return httpx.Response(404, json={"object": "error"})
        assert request.url.params["exact"] == "Elmar, Ulvenwald Informant"
        return httpx.Response(200, json={**card("Elmar, Ulvenwald Informant"), "oracle_id": "oracle-elmar"})

    delays = mock_client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    found, summary = metadata.MetadataRepository(cache).load(
        ["Max, the Daredevil"], fetch=True,
        commander_alias_groups=[["Elmar, Ulvenwald Informant", "Max, the Daredevil"]],
    )
    assert found["max, the daredevil"]["oracle_id"] == "oracle-elmar"
    assert summary["requested"] == summary["resolved"] == summary["fetched"] == 1
    assert not summary["missing"] and not summary["not_found"] and not summary["failures"]
    assert summary["requests"] == 3
    assert delays == [.1, .1, .1]
    assert list(json.loads(cache.read_text())["cards"]) == ["elmar, ulvenwald informant"]


def test_conflicting_commander_equivalences_do_not_alias_different_oracle_identities(tmp_path):
    cache = tmp_path / "cards.json"
    checked = datetime.now(timezone.utc).isoformat()
    metadata._write_cache(cache, {
        "actual one": metadata._metadata({**card("Actual One"), "oracle_id": "one"}, checked),
        "actual two": metadata._metadata({**card("Actual Two"), "oracle_id": "two"}, checked),
    })
    group = ["Actual One", "Actual Two", "Unresolved"]
    found, summary = metadata.MetadataRepository(cache).load(
        group, commander_alias_groups=[group],
    )
    assert found["actual one"]["oracle_id"] == "one"
    assert found["actual two"]["oracle_id"] == "two"
    assert "unresolved" not in found
    assert summary["requested"] == 3 and summary["resolved"] == 2
    assert summary["alias_conflicts"] == [group]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_named_fallback_access_stop_prevents_equivalent_and_later_requests(tmp_path, monkeypatch, status):
    attempts = []

    def handler(request):
        attempts.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"object": "list", "data": [],
                                            "not_found": [{"name": "Alternate"}]})
        return httpx.Response(status, json={"object": "error"})

    delays = mock_client(monkeypatch, handler)
    repository = metadata.MetadataRepository(tmp_path / "cards.json")
    _, summary = repository.load(["Alternate"], fetch=True,
                                 commander_alias_groups=[["Alternate", "Actual"]])
    _, later = repository.load(["Another"], fetch=True)
    assert repository.blocked and summary["blocked"] and later["blocked"]
    assert len(attempts) == 2
    assert summary["requests"] == 2 and later["requests"] == 0
    assert delays == [.1, .1]


@pytest.mark.parametrize("groups", ["Alternate", [["Only One"]], ["Not A List"], [["", "Actual"]]])
def test_commander_alias_group_shape_is_validated(tmp_path, groups):
    with pytest.raises(ValueError):
        metadata.MetadataRepository(tmp_path / "cards.json").load(["Actual"], commander_alias_groups=groups)


@pytest.mark.parametrize("identifiers", [
    "not-a-mapping", {"Max": "not-a-uuid"}, {"Max": "abb04ab12a74432fb3d3d4f5b2534066"},
    {"Max": "../cards/named?fuzzy=Max"}, {"": "abb04ab1-2a74-432f-b3d3-d4f5b2534066"},
    {"Max": 123}, {"Max": "abb04ab1-2a74-432f-b3d3-d4f5b2534066",
                  " MAX ": "114addfb-6fcb-47fc-a617-9e029d3e1ae5"},
])
def test_printing_hints_require_valid_uuid_and_unambiguous_name(tmp_path, identifiers):
    with pytest.raises(ValueError):
        metadata.MetadataRepository(tmp_path / "cards.json").load(["Max"], printing_ids=identifiers)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_printing_lookup_access_stop_prevents_named_and_later_requests(tmp_path, monkeypatch, status):
    attempts = []

    def handler(request):
        attempts.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"object": "list", "data": [],
                                            "not_found": [{"name": "Alternate"}]})
        assert request.url.path == "/cards/abb04ab1-2a74-432f-b3d3-d4f5b2534066"
        return httpx.Response(status, json={"object": "error"})

    delays = mock_client(monkeypatch, handler)
    repository = metadata.MetadataRepository(tmp_path / "cards.json")
    _, summary = repository.load(["Alternate"], fetch=True,
                                 printing_ids={"Alternate": "abb04ab1-2a74-432f-b3d3-d4f5b2534066"})
    _, later = repository.load(["Another"], fetch=True)
    assert repository.blocked and summary["blocked"] and later["blocked"]
    assert len(attempts) == 2 and delays == [.1, .1]
    assert summary["printing_requests"] == 1 and summary["requests"] == 2
    assert later["requests"] == 0
