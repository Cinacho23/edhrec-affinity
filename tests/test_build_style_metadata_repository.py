"""Catalog metadata lookups share work while retaining source freshness."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading

import httpx
import pytest

from edhrec_affinity.build_styles import card_metadata as metadata


HTTPX_CLIENT = httpx.Client


def card(name="Forest"):
    return {"object": "card", "name": name, "type_line": "Basic Land — Forest",
            "oracle_text": "({T}: Add {G}.)", "color_identity": ["G"],
            "legalities": {"commander": "legal"}, "keywords": [], "cmc": 0,
            "mana_cost": "", "layout": "normal", "produced_mana": ["G"]}


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
