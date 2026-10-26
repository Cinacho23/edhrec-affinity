"""Exact metadata resolution must not manufacture legal or identified cards."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from edhrec_affinity.build_styles import card_metadata as metadata

HTTPX_CLIENT = httpx.Client


def card(name="Forest", **changes):
    return {"object": "card", "name": name, "type_line": "Basic Land — Forest",
            "oracle_text": "({T}: Add {G}.)", "color_identity": ["G"],
            "legalities": {"commander": "legal"}, "keywords": [], "cmc": 0,
            "mana_cost": "", "layout": "normal", "game_changer": False,
            "produced_mana": ["G"], "oracle_id": f"oracle-{name}",
            "scryfall_uri": "https://scryfall.com/card/test/1/forest", **changes}


def collection(cards, not_found=None):
    return {"object": "list", "has_more": False, "data": cards,
            "not_found": [{"name": name} for name in (not_found or [])]}


def client(monkeypatch, handler):
    monkeypatch.setattr(metadata.httpx, "Client", lambda **kwargs: HTTPX_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))
    delays = []
    monkeypatch.setattr(metadata.time, "sleep", delays.append)
    return delays


def test_collection_batches_exact_names_at_75_with_headers_and_pacing(tmp_path, monkeypatch):
    batches = []

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == metadata.COLLECTION_URL
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"] == metadata.DEFAULT_USER_AGENT
        payload = json.loads(request.content)
        assert all(set(identifier) == {"name"} for identifier in payload["identifiers"])
        names = [identifier["name"] for identifier in payload["identifiers"]]
        batches.append(names)
        return httpx.Response(200, json=collection([card(name) for name in names]))

    delays = client(monkeypatch, handler)
    names = [f"Card {i}" for i in range(151)]
    found, summary = metadata.load_card_metadata(names + [names[0]], tmp_path / "cache.json", fetch=True)
    assert [len(batch) for batch in batches] == [75, 75, 1]
    assert delays == [.1, .1, .1]
    assert len(found) == 151
    assert summary["requested"] == summary["resolved"] == summary["fetched"] == 151
    assert summary["cached"] == 0
    assert summary["requests"] == 3
    assert not summary["missing"]
    assert not list(tmp_path.glob("*.tmp"))


def test_offline_cache_reuses_checked_metadata_without_starting_http_client(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card()])))
    online, _ = metadata.load_card_metadata(["Forest"], cache, fetch=True)
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Offline lookup started a client"))
    offline, summary = metadata.load_card_metadata([" FOREST ", "Missing"], cache)
    assert offline == online
    assert summary["cached"] == summary["resolved"] == 1
    assert summary["fetched"] == summary["requests"] == 0
    assert summary["missing"] == ["Missing"]
    assert offline["forest"]["checked_at"] == online["forest"]["checked_at"]
    assert offline["forest"]["legalities"]["commander"] == "legal"
    assert offline["forest"]["produced_mana"] == ["G"]
    assert offline["forest"]["game_changer"] is False
    assert offline["forest"]["oracle_id"] == "oracle-Forest"


def test_only_uncached_cards_are_fetched_and_negative_results_are_retried(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        if request.method == "GET":
            assert request.url.params["exact"] == "Unknown"
            return httpx.Response(404, json={"object": "error"})
        names = [identifier["name"] for identifier in json.loads(request.content)["identifiers"]]
        requests.append(names)
        return httpx.Response(200, json=collection([card(name) for name in names if name != "Unknown"],
                                                 ["Unknown"] if "Unknown" in names else []))

    client(monkeypatch, handler)
    cache = tmp_path / "cache.json"
    metadata.load_card_metadata(["Forest", "Unknown"], cache, fetch=True)
    _, summary = metadata.load_card_metadata(["Forest", "Unknown", "Island"], cache, fetch=True)
    assert requests == [["Forest", "Unknown"], ["Unknown", "Island"]]
    assert summary["not_found"] == summary["missing"] == ["Unknown"]
    assert summary["cached"] == 1
    assert set(json.loads(cache.read_text())["cards"]) == {"forest", "island"}


def test_front_face_alias_uses_full_color_identity_and_legality(tmp_path, monkeypatch):
    mdfc = card("Valki, God of Lies // Tibalt, Cosmic Impostor", type_line="Legendary Creature — God // Legendary Planeswalker — Tibalt",
                color_identity=["B", "R"], layout="modal_dfc", cmc=2,
                card_faces=[{"name": "Valki, God of Lies", "type_line": "Legendary Creature — God",
                             "mana_cost": "{1}{B}", "power": "2", "toughness": "1"},
                            {"name": "Tibalt, Cosmic Impostor", "type_line": "Legendary Planeswalker — Tibalt"}])
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([mdfc])))
    cache = tmp_path / "cache.json"
    found, summary = metadata.load_card_metadata(["Valki, God of Lies"], cache, fetch=True)
    assert found["valki, god of lies"] is found["valki, god of lies // tibalt, cosmic impostor"]
    assert found["valki, god of lies"]["color_identity"] == ["B", "R"]
    assert found["valki, god of lies"]["card_faces"][0]["power"] == "2"
    assert "tibalt, cosmic impostor" not in found
    assert summary["resolved"] == 1
    _, summary = metadata.load_card_metadata(["Valki, God of Lies"], cache, fetch=True)
    assert summary["requests"] == 0


def test_split_halves_are_not_aliases_or_silently_substituted(tmp_path, monkeypatch):
    split = card("Fire // Ice", layout="split", card_faces=[
        {"name": "Fire", "type_line": "Instant"}, {"name": "Ice", "type_line": "Instant"}])
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([split])))
    cache = tmp_path / "cache.json"
    found, _ = metadata.load_card_metadata(["Fire // Ice"], cache, fetch=True)
    assert set(found) == {"fire // ice"}
    original = cache.read_text()
    found, summary = metadata.load_card_metadata(["Fire"], cache, fetch=True)
    assert not found
    assert summary["missing"] == ["Fire"]
    assert "does not exactly match" in summary["failures"][0]["error"]
    assert cache.read_text() == original


def test_shared_alias_is_omitted_and_exact_canonical_name_takes_precedence(tmp_path, monkeypatch):
    variants = [card("Front // Back One", layout="transform", card_faces=[{"name": "Front"}]),
                card("Front // Back Two", layout="transform", card_faces=[{"name": "Front"}])]
    client(monkeypatch, lambda _: httpx.Response(200, json=collection(variants)))
    cache = tmp_path / "cache.json"
    found, _ = metadata.load_card_metadata([variant["name"] for variant in variants], cache, fetch=True)
    assert "front" not in found
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card("Front")])))
    found, summary = metadata.load_card_metadata(["Front"], cache, fetch=True)
    assert found["front"]["name"] == "Front"
    assert summary["fetched"] == 1


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_access_or_rate_limit_stops_without_retry_and_preserves_successful_batches(tmp_path, monkeypatch, status_code):
    attempts = []

    def handler(request):
        names = [identifier["name"] for identifier in json.loads(request.content)["identifiers"]]
        attempts.append(names)
        if len(attempts) == 1:
            return httpx.Response(200, json=collection([card(name) for name in names]))
        return httpx.Response(status_code, json={"object": "error"})

    client(monkeypatch, handler)
    cache = tmp_path / "cache.json"
    names = [f"Card {i}" for i in range(151)]
    found, summary = metadata.load_card_metadata(names, cache, fetch=True)
    assert len(attempts) == 2
    assert len(found) == 75
    assert summary["blocked"]
    assert summary["failures"][0]["status_code"] == status_code
    assert summary["missing"] == names[75:]
    offline, _ = metadata.load_card_metadata(names, cache)
    assert len(offline) == 75


@pytest.mark.parametrize("response", [
    '{"object":"list","data":[],"not_found":[],"value":NaN}',
    '{"object":"list","data":[],"not_found":[],"value":1e999}',
    '{"object":"list","data":[],"not_found":[]}',
    '<html>challenge</html>',
])
def test_invalid_or_incomplete_response_never_replaces_valid_cache(tmp_path, monkeypatch, response):
    cache = tmp_path / "cache.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card()])))
    metadata.load_card_metadata(["Forest"], cache, fetch=True)
    before = cache.read_bytes()
    client(monkeypatch, lambda _: httpx.Response(200, text=response))
    found, summary = metadata.load_card_metadata(["Forest", "Island"], cache, fetch=True)
    assert set(found) == {"forest"}
    assert summary["failures"]
    assert cache.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"color_identity": ["P"]}, {"legalities": {}}, {"cmc": True},
    {"game_changer": "yes"}, {"produced_mana": ["invalid"]},
])
def test_invalid_card_metadata_is_not_cached(tmp_path, monkeypatch, changes):
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card(**changes)])))
    cache = tmp_path / "cache.json"
    found, summary = metadata.load_card_metadata(["Forest"], cache, fetch=True)
    assert not found
    assert summary["failures"]
    assert not cache.exists()


def test_corrupted_cache_is_reported_without_network_when_offline(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text('{"schema_version":1,"updated_at":"2026-01-01T00:00:00+00:00","cards":{},"bad":1e999}')
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Offline lookup started a client"))
    found, summary = metadata.load_card_metadata(["Forest"], cache)
    assert not found
    assert summary["cache_error"]
    assert summary["missing"] == ["Forest"]


def test_unrelated_exact_names_are_not_fuzzily_accepted(tmp_path, monkeypatch):
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card("Sol Ring")])))
    cache = tmp_path / "cache.json"
    found, summary = metadata.load_card_metadata(["Sol Rign"], cache, fetch=True)
    assert not found
    assert summary["missing"] == ["Sol Rign"]
    assert not cache.exists()


def test_empty_request_never_creates_cache_or_client(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Empty lookup started a client"))
    found, summary = metadata.load_card_metadata([], cache, fetch=True)
    assert not found
    assert summary["requested"] == summary["requests"] == 0
    assert not cache.exists()


@pytest.mark.parametrize("failure", ["transport", "server"])
def test_network_failure_keeps_existing_positive_cache(tmp_path, monkeypatch, failure):
    cache = tmp_path / "cache.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card()])))
    metadata.load_card_metadata(["Forest"], cache, fetch=True)
    before = cache.read_bytes()

    def handler(request):
        if failure == "transport":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503, json={"object": "error"})

    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["Forest", "Island"], cache, fetch=True)
    assert set(found) == {"forest"}
    assert summary["failures"]
    assert summary["missing"] == ["Island"]
    assert cache.read_bytes() == before


def make_cache_stale(cache):
    payload = json.loads(cache.read_text())
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    for cached in payload["cards"].values():
        cached["checked_at"] = old_timestamp
    cache.write_text(json.dumps(payload))
    return old_timestamp


def test_online_refreshes_stale_legality_and_game_changer_metadata(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card()])))
    metadata.load_card_metadata(["Forest"], cache, fetch=True)
    old_timestamp = make_cache_stale(cache)
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([
        card(legalities={"commander": "banned"}, game_changer=True)])))
    found, summary = metadata.load_card_metadata(["Forest"], cache, fetch=True)
    assert found["forest"]["legalities"]["commander"] == "banned"
    assert found["forest"]["game_changer"] is True
    assert found["forest"]["checked_at"] != old_timestamp
    assert summary["requests"] == summary["fetched"] == 1
    assert summary["cached"] == summary["stale_cached"] == 0
    assert not summary["stale_names"]


def test_offline_retains_stale_metadata_and_exposes_its_age(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card()])))
    metadata.load_card_metadata(["Forest"], cache, fetch=True)
    old_timestamp = make_cache_stale(cache)
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Offline lookup started a client"))
    found, summary = metadata.load_card_metadata(["Forest"], cache)
    assert found["forest"]["checked_at"] == old_timestamp
    assert summary["oldest_checked_at"] == old_timestamp
    assert summary["cached"] == summary["stale_cached"] == 1
    assert summary["stale_names"] == ["Forest"]
    assert summary["cache_max_age_days"] == 7
    assert summary["requests"] == 0


def test_failed_refresh_discloses_stale_cached_cards(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card()])))
    metadata.load_card_metadata(["Forest"], cache, fetch=True)
    old_timestamp = make_cache_stale(cache)
    client(monkeypatch, lambda _: httpx.Response(429, json={"object": "error"}))
    found, summary = metadata.load_card_metadata(["Forest"], cache, fetch=True)
    assert found["forest"]["checked_at"] == old_timestamp
    assert summary["blocked"]
    assert summary["fetched"] == 0
    assert summary["stale_cached"] == 1
    assert summary["stale_names"] == ["Forest"]


def test_prepare_front_name_identifies_whole_card_without_aliasing_its_spell(tmp_path, monkeypatch):
    prepared = card("Grave Researcher // Reanimate", layout="prepare", card_faces=[
        {"name": "Grave Researcher", "type_line": "Creature — Troll Warlock"},
        {"name": "Reanimate", "type_line": "Sorcery"}])
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([prepared, card("Reanimate")])))
    found, summary = metadata.load_card_metadata(["Grave Researcher", "Reanimate"], tmp_path / "cache.json", fetch=True)
    assert found["grave researcher"]["name"] == "Grave Researcher // Reanimate"
    assert found["reanimate"]["name"] == "Reanimate"
    assert summary["resolved"] == 2
    assert not summary["failures"]


def test_unrecognized_card_preserves_other_exact_matches_and_continues_batches(tmp_path, monkeypatch):
    requested_batches = []

    def handler(request):
        names = [identifier["name"] for identifier in json.loads(request.content)["identifiers"]]
        requested_batches.append(names)
        returned = [card(name) for name in names if name != "Card 0"]
        if "Card 0" in names:
            returned.append(card("Card 0 // New Spell", layout="future_unknown_layout",
                                 card_faces=[{"name": "Card 0"}, {"name": "New Spell"}]))
        return httpx.Response(200, json=collection(returned))

    client(monkeypatch, handler)
    cache = tmp_path / "cache.json"
    found, summary = metadata.load_card_metadata([f"Card {i}" for i in range(76)], cache, fetch=True)
    assert [len(batch) for batch in requested_batches] == [75, 1]
    assert len(found) == 75
    assert summary["missing"] == ["Card 0"]
    assert summary["failures"][0]["names"] == ["Card 0"]
    assert "Card 0 // New Spell" in summary["failures"][0]["error"]
    assert summary["failures"][0]["returned_cards"][0]["aliases"] == ["card 0 // new spell"]
    assert "card 0 // new spell" not in json.loads(cache.read_text())["cards"]
    offline, offline_summary = metadata.load_card_metadata([f"Card {i}" for i in range(76)], cache)
    assert len(offline) == 75
    assert offline_summary["missing"] == ["Card 0"]


def test_full_split_and_room_identifiers_use_first_name_but_require_full_returned_identity(tmp_path, monkeypatch):
    names = ["Dusk // Dawn", "Find // Finality", "Funeral Room // Awakening Hall"]

    def handler(request):
        assert json.loads(request.content)["identifiers"] == [
            {"name": "Dusk"}, {"name": "Find"}, {"name": "Funeral Room"}]
        return httpx.Response(200, json=collection([card(name, layout="split", card_faces=[
            {"name": name.split(" // ")[0]}, {"name": name.split(" // ")[1]}]) for name in names]))

    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(names, tmp_path / "cache.json", fetch=True)
    assert set(found) == {name.casefold() for name in names}
    assert summary["resolved"] == 3
    assert not summary["failures"]
    assert "dusk" not in found


def test_split_first_name_resolution_cannot_substitute_another_full_card(tmp_path, monkeypatch):
    def handler(request):
        assert json.loads(request.content)["identifiers"] == [{"name": "Fire"}]
        return httpx.Response(200, json=collection([card("Fire // Ice", layout="split")]))

    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["Fire // Blade"], tmp_path / "cache.json", fetch=True)
    assert not found
    assert summary["missing"] == ["Fire // Blade"]
    assert summary["failures"][0]["names"] == ["Fire // Blade"]
    assert "Fire // Ice" in summary["failures"][0]["error"]


def test_collection_not_found_first_name_is_reported_as_original_full_name(tmp_path, monkeypatch):
    client(monkeypatch, lambda request: httpx.Response(404, json={"object": "error"})
           if request.method == "GET" else httpx.Response(200, json=collection([], ["Dusk"])))
    found, summary = metadata.load_card_metadata(["Dusk // Dawn"], tmp_path / "cache.json", fetch=True)
    assert not found
    assert summary["missing"] == summary["not_found"] == ["Dusk // Dawn"]
    assert not summary["failures"]


def alternate(name, display, *, field="flavor_name", oracle_id="oracle-shared", **changes):
    return card(name, **{"id": f"printing-{display}", "lang": "en", "oracle_id": oracle_id,
                         field: display, **changes})


def test_collection_accepts_verified_secret_lair_flavor_name_and_preserves_provenance(tmp_path, monkeypatch):
    raw = alternate("Yoshimaru, Ever Faithful", "Dogmeat, Constant Companion")
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([raw])))
    cache = tmp_path / "cards.json"
    found, summary = metadata.load_card_metadata(["Dogmeat, Constant Companion"], cache, fetch=True)
    dogmeat = found["dogmeat, constant companion"]
    assert dogmeat is found["yoshimaru, ever faithful"]
    assert dogmeat["flavor_name"] == "Dogmeat, Constant Companion"
    assert dogmeat["id"] == "printing-Dogmeat, Constant Companion"
    assert dogmeat["lang"] == "en"
    assert dogmeat["name_aliases"] == [{
        "name": "Dogmeat, Constant Companion", "source_field": "flavor_name",
        "printing_id": "printing-Dogmeat, Constant Companion", "lang": "en",
        "oracle_id": "oracle-shared", "canonical_name": "Yoshimaru, Ever Faithful",
    }]
    assert summary["requested"] == summary["resolved"] == summary["fetched"] == 1
    assert summary["alias_resolutions"] == [{
        "requested_name": "Dogmeat, Constant Companion", "canonical_name": "Yoshimaru, Ever Faithful",
        "oracle_id": "oracle-shared", "source": "scryfall",
    }]
    offline, summary = metadata.load_card_metadata(["Dogmeat, Constant Companion"], cache)
    assert offline == found
    assert summary["cached"] == 1


def test_collection_not_found_uses_paced_exact_named_fallback_without_fuzzy_lookup(tmp_path, monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=collection([], ["Cecil Harvey"]))
        assert str(request.url.copy_with(query=None)) == metadata.NAMED_URL
        assert dict(request.url.params) == {"exact": "Cecil Harvey"}
        return httpx.Response(200, json=alternate("Tymna the Weaver", "Cecil Harvey"))

    delays = client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["Cecil Harvey"], tmp_path / "cards.json", fetch=True)
    assert found["cecil harvey"]["name"] == "Tymna the Weaver"
    assert summary["fetched"] == summary["resolved"] == 1
    assert summary["requests"] == 2
    assert not summary["missing"] and not summary["not_found"] and not summary["failures"]
    assert delays == [.1, .1]


def test_canonical_collection_response_without_alias_evidence_is_reverified_by_exact_named(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            # Collection knows the alternate name but returns a normal printing
            # without the display-name evidence needed for strict matching.
            return httpx.Response(200, json=collection([card("Tymna the Weaver")]))
        assert dict(request.url.params) == {"exact": "Cecil Harvey"}
        return httpx.Response(200, json=alternate("Tymna the Weaver", "Cecil Harvey"))

    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["Cecil Harvey"], tmp_path / "cards.json", fetch=True)
    assert found["cecil harvey"]["name"] == "Tymna the Weaver"
    assert summary["requested"] == summary["resolved"] == summary["fetched"] == 1
    assert summary["requests"] == 2
    assert not summary["missing"] and not summary["failures"]
    assert "does not exactly match" in summary["collection_rejections"][0]["error"]


def test_verified_printed_name_survives_regular_printing_refresh_and_cache_reload(tmp_path, monkeypatch):
    cache = tmp_path / "cards.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([
        alternate("Elmar, Ulvenwald Informant", "Max, the Daredevil", field="printed_name")
    ])))
    metadata.load_card_metadata(["Max, the Daredevil"], cache, fetch=True)
    make_cache_stale(cache)
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([
        card("Elmar, Ulvenwald Informant", oracle_id="oracle-shared", id="regular-printing", lang="en")
    ])))
    metadata.load_card_metadata(["Elmar, Ulvenwald Informant"], cache, fetch=True)
    found, summary = metadata.load_card_metadata(["Max, the Daredevil"], cache)
    assert found["max, the daredevil"]["id"] == "regular-printing"
    assert found["max, the daredevil"]["name_aliases"][0]["printing_id"] == "printing-Max, the Daredevil"
    assert summary["cached"] == 1
    assert not summary["stale_names"]


def test_named_fallback_cannot_accept_unrelated_card_or_cache_fuzzy_substitution(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=collection([], ["Sol Rign"]))
        assert dict(request.url.params) == {"exact": "Sol Rign"}
        return httpx.Response(200, json=card("Sol Ring"))

    client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    found, summary = metadata.load_card_metadata(["Sol Rign"], cache, fetch=True)
    assert not found and not cache.exists()
    assert summary["missing"] == ["Sol Rign"]
    assert "does not exactly match" in summary["failures"][0]["error"]


@pytest.mark.parametrize("changes", [{"lang": "ja"}, {"id": None}, {"oracle_id": None}])
def test_unverified_or_non_english_printed_names_do_not_create_aliases(tmp_path, monkeypatch, changes):
    raw = alternate("Actual Card", "Other Name")
    for key, value in changes.items():
        if value is None:
            raw.pop(key)
        else:
            raw[key] = value
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([raw])))
    found, summary = metadata.load_card_metadata(["Other Name"], tmp_path / "cards.json", fetch=True)
    assert not found
    assert summary["missing"] == ["Other Name"]


def test_conflicting_printed_aliases_are_omitted_and_canonical_names_win(tmp_path, monkeypatch):
    cards = [alternate("Card One", "Shared", oracle_id="one"),
             alternate("Card Two", "Shared", oracle_id="two")]
    client(monkeypatch, lambda _: httpx.Response(200, json=collection(cards)))
    cache = tmp_path / "cards.json"
    found, _ = metadata.load_card_metadata(["Card One", "Card Two"], cache, fetch=True)
    assert "shared" not in found
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card("Shared")])))
    found, _ = metadata.load_card_metadata(["Shared"], cache, fetch=True)
    assert found["shared"]["name"] == "Shared"


def test_old_alias_proof_is_not_retained_when_oracle_identity_changes(tmp_path, monkeypatch):
    cache = tmp_path / "cards.json"
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([alternate("Actual", "Alternate")])))
    metadata.load_card_metadata(["Alternate"], cache, fetch=True)
    make_cache_stale(cache)
    client(monkeypatch, lambda _: httpx.Response(200, json=collection([card("Actual", oracle_id="changed")])))
    metadata.load_card_metadata(["Actual"], cache, fetch=True)
    found, summary = metadata.load_card_metadata(["Alternate"], cache)
    assert not found
    assert summary["missing"] == ["Alternate"]


def test_cached_alias_proof_with_conflicting_oracle_id_is_rejected(tmp_path):
    cache = tmp_path / "cards.json"
    checked = datetime.now(timezone.utc).isoformat()
    cached = metadata._metadata(alternate("Actual", "Alternate"), checked)
    cached["name_aliases"][0]["oracle_id"] = "different"
    metadata._write_cache(cache, {"actual": cached})
    found, summary = metadata.load_card_metadata(["Alternate"], cache)
    assert not found
    assert "different card identity" in summary["cache_error"]


def test_cold_secret_lair_printing_lookup_resolves_unindexed_display_name_without_alias_groups(tmp_path, monkeypatch):
    printing_id = "abb04ab1-2a74-432f-b3d3-d4f5b2534066"
    attempts = []

    def handler(request):
        attempts.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=collection([], ["Max, the Daredevil"]))
        assert str(request.url) == f"{metadata.CARD_URL}/{printing_id}"
        return httpx.Response(200, json=alternate(
            "Elmar, Ulvenwald Informant", "Max, the Daredevil", field="printed_name", id=printing_id,
        ))

    delays = client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    found, summary = metadata.load_card_metadata(
        ["Max, the Daredevil"], cache, fetch=True, printing_ids={"Max, the Daredevil": printing_id},
    )
    assert found["max, the daredevil"]["name"] == "Elmar, Ulvenwald Informant"
    assert found["max, the daredevil"]["name_aliases"][0]["printing_id"] == printing_id
    assert summary["requested"] == summary["resolved"] == summary["fetched"] == 1
    assert summary["requests"] == 2 and summary["printing_requests"] == 1
    assert not summary["missing"] and not summary["failures"]
    assert delays == [.1, .1]
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Verified printing alias should persist offline"))
    offline, summary = metadata.load_card_metadata(["Max, the Daredevil"], cache)
    assert offline == found
    assert summary["cached"] == 1 and summary["requests"] == 0


@pytest.mark.parametrize("mismatch", ["id", "name", "proof"])
def test_printing_lookup_requires_both_exact_uuid_and_original_name_evidence(tmp_path, monkeypatch, mismatch):
    printing_id = "abb04ab1-2a74-432f-b3d3-d4f5b2534066"

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=collection([], ["Max, the Daredevil"]))
        if str(request.url).startswith(metadata.NAMED_URL):
            return httpx.Response(404, json={"object": "error"})
        raw = alternate("Elmar, Ulvenwald Informant", "Max, the Daredevil", field="printed_name", id=printing_id)
        if mismatch == "id":
            raw["id"] = "114addfb-6fcb-47fc-a617-9e029d3e1ae5"
        elif mismatch == "name":
            raw["printed_name"] = "Different Name"
        else:
            raw.pop("printed_name")
        return httpx.Response(200, json=raw)

    client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    found, summary = metadata.load_card_metadata(
        ["Max, the Daredevil"], cache, fetch=True, printing_ids={"Max, the Daredevil": printing_id},
    )
    assert not found and not cache.exists()
    assert summary["missing"] == ["Max, the Daredevil"]
    assert summary["failures"]
    assert summary["requests"] == 3


def test_unknown_printing_can_still_resolve_via_independent_exact_name_fallback(tmp_path, monkeypatch):
    printing_id = "abb04ab1-2a74-432f-b3d3-d4f5b2534066"

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=collection([], ["Cecil Harvey"]))
        if str(request.url).startswith(metadata.NAMED_URL):
            return httpx.Response(200, json=alternate("Tymna the Weaver", "Cecil Harvey"))
        return httpx.Response(404, json={"object": "error"})

    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(
        ["Cecil Harvey"], tmp_path / "cards.json", fetch=True, printing_ids={"Cecil Harvey": printing_id},
    )
    assert found["cecil harvey"]["name"] == "Tymna the Weaver"
    assert summary["requests"] == 3 and not summary["failures"]


NO_WAY_OUT_ORACLE = "8cbbb1f4-4654-4865-a14c-0c94cd5d8b19"
OTHER_ORACLE = "8cbbb1f4-4654-4865-a14c-0c94cd5d8b10"


def no_way_out(*, layout="normal", **changes):
    return card("No Way Out", type_line="Sorcery" if layout == "normal" else "Plane",
                oracle_id=NO_WAY_OUT_ORACLE if layout == "normal" else OTHER_ORACLE,
                layout=layout, **changes)


def search_result(cards, **changes):
    return {"object": "list", "has_more": False, "total_cards": len(cards), "data": cards, **changes}


@pytest.mark.parametrize("layout", sorted(metadata._NON_DECK_LAYOUTS))
def test_non_deck_namesake_uses_unique_exact_physical_search_with_same_pacing(tmp_path, monkeypatch, layout):
    plane = no_way_out(layout=layout, legalities={"commander": "not_legal"})
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=collection([plane]))
        if str(request.url.copy_with(query=None)) == metadata.NAMED_URL:
            assert dict(request.url.params) == {"exact": "No Way Out"}
            return httpx.Response(200, json=plane)
        assert str(request.url.copy_with(query=None)) == metadata.SEARCH_URL
        assert request.url.params["q"].startswith('!"No Way Out" ')
        assert all(f"-layout:{value}" in request.url.params["q"] for value in metadata._NON_DECK_LAYOUTS)
        assert request.url.params["unique"] == "cards"
        assert request.url.params["include_extras"] == "true"
        assert "legal:" not in request.url.params["q"] and "fuzzy" not in request.url.params
        return httpx.Response(200, json=search_result([no_way_out()]))
    delays = client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    found, summary = metadata.load_card_metadata(["No Way Out"], cache, fetch=True)
    assert found["no way out"]["layout"] == "normal"
    assert found["no way out"]["oracle_id"] == NO_WAY_OUT_ORACLE
    assert summary["requested"] == summary["resolved"] == summary["fetched"] == 1
    assert summary["requests"] == len(requests) == 3
    assert summary["physical_name_searches"] == 1
    assert summary["non_deck_resolutions"][0]["excluded_layout"] == layout
    assert not summary["failures"] and not summary["missing"]
    assert delays == [.1, .1, .1]
    assert json.loads(cache.read_text())["cards"]["no way out"]["oracle_id"] == NO_WAY_OUT_ORACLE


def test_fresh_cached_plane_is_not_returned_offline_and_is_repaired_online(tmp_path, monkeypatch):
    cache = tmp_path / "cards.json"
    plane = no_way_out(layout="planar", legalities={"commander": "not_legal"})
    metadata._write_cache(cache, {"no way out": metadata._metadata(plane, metadata._now())})
    before = cache.read_bytes()
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Offline lookup started HTTP"))
    found, summary = metadata.load_card_metadata(["No Way Out"], cache)
    assert not found and summary["missing"] == ["No Way Out"]
    assert cache.read_bytes() == before
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=collection([plane]))
        return httpx.Response(200, json=search_result([no_way_out()])) if "/search" in request.url.path else httpx.Response(200, json=plane)
    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["No Way Out"], cache, fetch=True)
    assert found["no way out"]["oracle_id"] == NO_WAY_OUT_ORACLE
    assert summary["requests"] == 3
    monkeypatch.setattr(metadata.httpx, "Client", lambda **_: pytest.fail("Corrected cache started HTTP"))
    found, summary = metadata.load_card_metadata(["No Way Out"], cache, fetch=True)
    assert summary["cached"] == 1 and summary["requests"] == 0
    assert found["no way out"]["layout"] == "normal"


@pytest.mark.parametrize("legality", ["banned", "not_legal", "restricted"])
def test_normal_illegal_cards_are_retained_without_seeking_a_legal_namesake(tmp_path, monkeypatch, legality):
    def handler(request):
        assert request.method == "POST", "A normal illegal card must not cause a namesake search"
        return httpx.Response(200, json=collection([no_way_out(legalities={"commander": legality})]))
    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["No Way Out"], tmp_path / "cards.json", fetch=True)
    assert found["no way out"]["legalities"]["commander"] == legality
    assert summary["requests"] == 1 and summary["physical_name_searches"] == 0


@pytest.mark.parametrize("failure", ["wrong_name", "multiple_oracles", "paginated", "missing_oracle", "non_deck", "incomplete_total"])
def test_non_deck_search_fails_closed_without_unique_complete_strict_identity(tmp_path, monkeypatch, failure):
    plane = no_way_out(layout="planar", legalities={"commander": "not_legal"})
    rows = [no_way_out()]
    payload = search_result(rows)
    if failure == "wrong_name":
        rows[0]["name"] = "Other Card"
    elif failure == "multiple_oracles":
        # Even a normal/playtest namesake must not be hidden by choosing the
        # Commander-legal result or sorting by a preferred edition.
        rows.append({**no_way_out(), "oracle_id": OTHER_ORACLE, "legalities": {"commander": "not_legal"}})
        payload["total_cards"] = len(rows)
    elif failure == "paginated":
        payload["has_more"] = True
    elif failure == "missing_oracle":
        rows[0].pop("oracle_id")
    elif failure == "non_deck":
        rows[0] = plane
    else:
        payload["total_cards"] = 2
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=collection([plane]))
        return httpx.Response(200, json=payload if "/search" in request.url.path else plane)
    client(monkeypatch, handler)
    cache = tmp_path / "cards.json"
    found, summary = metadata.load_card_metadata(["No Way Out"], cache, fetch=True)
    assert not found and not cache.exists()
    assert summary["missing"] == ["No Way Out"]
    assert summary["failures"] and summary["physical_name_searches"] == 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_non_deck_search_uses_repository_wide_access_stop(tmp_path, monkeypatch, status):
    plane = no_way_out(layout="planar", legalities={"commander": "not_legal"})
    attempts = []
    def handler(request):
        attempts.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=collection([plane]))
        return httpx.Response(status, json={"object": "error"}) if "/search" in request.url.path else httpx.Response(200, json=plane)
    delays = client(monkeypatch, handler)
    repository = metadata.MetadataRepository(tmp_path / "cards.json")
    found, summary = repository.load(["No Way Out"], fetch=True)
    assert not found and summary["blocked"]
    assert summary["requests"] == len(attempts) == 3
    assert delays == [.1, .1, .1]
    _, again = repository.load(["Other Card"], fetch=True)
    assert again["blocked"] and again["requests"] == 0
    assert len(attempts) == 3


def test_collection_non_deck_evidence_allows_exact_search_when_named_endpoint_misses(tmp_path, monkeypatch):
    plane = no_way_out(layout="planar", legalities={"commander": "not_legal"})
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=collection([plane]))
        if "/search" in request.url.path:
            return httpx.Response(200, json=search_result([no_way_out()]))
        return httpx.Response(404, json={"object": "error"})
    client(monkeypatch, handler)
    found, summary = metadata.load_card_metadata(["No Way Out"], tmp_path / "cards.json", fetch=True)
    assert found["no way out"]["oracle_id"] == NO_WAY_OUT_ORACLE
    assert summary["requests"] == 3 and not summary["failures"]
