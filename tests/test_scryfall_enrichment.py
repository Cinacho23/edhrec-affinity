import json

import httpx

from edhrec_affinity.scryfall_enrichment import (
    CommanderIdentity,
    build_commander_cards,
    build_metadata_row,
    enrich_commanders_with_scryfall,
    find_cards_for_commander,
    get_card_faces,
    merge_metadata_into_rows,
)


def double_faced_card():
    return {
        "name": "Front Commander // Back Commander",
        "color_identity": ["G", "U"],
        "scryfall_uri": "https://scryfall.com/card/test/1/front-commander",
        "card_faces": [
            {"name": "Front Commander", "image_uris": {"normal": "front.jpg"}},
            {"name": "Back Commander", "image_uris": {"normal": "back.jpg"}},
        ],
    }


def test_metadata_groups_partner_cards_and_preserves_both_faces_and_colors():
    background = {
        "name": "Background",
        "color_identity": ["W", "G"],
        "image_uris": {"normal": "background.jpg"},
        "scryfall_uri": "https://scryfall.com/card/test/2/background",
    }
    commander = CommanderIdentity("Front Commander // Background", "pair")
    metadata = build_metadata_row(
        commander, [double_faced_card(), background], match_strategy="exact_split_parts"
    )

    assert metadata["color_identity"] == ["W", "U", "G"]
    assert metadata["card_image_url"] == "front.jpg"
    assert metadata["partner_card_image_urls"] == ["front.jpg", "background.jpg"]
    assert metadata["commander_cards"] == [
        {
            "name": "Front Commander // Back Commander",
            "scryfall_uri": "https://scryfall.com/card/test/1/front-commander",
            "faces": [
                {"name": "Front Commander", "image_url": "front.jpg"},
                {"name": "Back Commander", "image_url": "back.jpg"},
            ],
        },
        {
            "name": "Background",
            "scryfall_uri": "https://scryfall.com/card/test/2/background",
            "faces": [{"name": "Background", "image_url": "background.jpg"}],
        },
    ]
    merged = merge_metadata_into_rows([{"commander_slug": "pair", "z": 2}], [metadata])
    assert merged[0]["commander_cards"] == metadata["commander_cards"]
    assert merged[0]["z"] == 2


def test_split_or_adventure_card_uses_one_combined_image():
    card = double_faced_card()
    card["image_uris"] = {"normal": "combined.jpg"}
    assert get_card_faces(card) == [
        {"name": "Front Commander // Back Commander", "image_url": "combined.jpg"}
    ]


def test_missing_face_image_does_not_hide_other_face():
    card = double_faced_card()
    card["card_faces"][0]["image_uris"] = {}
    assert get_card_faces(card) == [
        {"name": "Back Commander", "image_url": "back.jpg"}
    ]


def test_partner_lookup_resolves_missing_exact_part_with_fuzzy_lookup():
    requests = []

    def respond(request):
        mode, name = next(iter(request.url.params.items()))
        requests.append((mode, name))
        result = {
            ("exact", "Partner"): {"name": "Partner"},
            ("fuzzy", "Backgrund"): {"name": "Background"},
        }.get((mode, name))
        return httpx.Response(200 if result else 404, json=result or {})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        cards, strategy = find_cards_for_commander(
            client, "Partner // Backgrund (Commander)", delay_seconds=0, cache={}
        )

    assert [card["name"] for card in cards] == ["Partner", "Background"]
    assert strategy == "fuzzy_split_parts"
    assert ("fuzzy", "Partner // Backgrund") not in requests


def test_enrichment_rebuilds_both_faces_from_existing_cache_without_requests(tmp_path, monkeypatch):
    card = double_faced_card()
    cache_path = tmp_path / "scryfall_lookup_cache.json"
    cache_path.write_text(json.dumps({"exact:front commander": card}), encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("An existing raw card cache should not require a request")

    monkeypatch.setattr(httpx.Client, "get", no_network)
    metadata, failures = enrich_commanders_with_scryfall(
        [CommanderIdentity("Front Commander", "front")],
        user_agent="Test",
        delay_seconds=0,
        cache_path=cache_path,
        use_print_history_for_origin=False,
    )
    assert failures == []
    assert len(metadata[0]["commander_cards"][0]["faces"]) == 2


def meld_commander():
    return {
        "name": "Mishra, Claimed by Gix",
        "layout": "meld",
        "color_identity": ["B", "R"],
        "image_uris": {"normal": "mishra-front.jpg"},
        "scryfall_uri": "https://scryfall.com/card/bro/216/mishra-claimed-by-gix",
        "all_parts": [
            {"name": "Phyrexian Dragon Engine", "component": "meld_part", "uri": "https://api.scryfall.com/cards/engine"},
            {"name": "Mishra, Lost to Phyrexia", "component": "meld_result", "uri": "https://api.scryfall.com/cards/melded"},
        ],
    }


def test_meld_enrichment_fetches_only_result_then_reuses_cached_back(tmp_path, monkeypatch):
    card = meld_commander()
    result = {
        "name": "Mishra, Lost to Phyrexia",
        "layout": "meld",
        "image_uris": {"normal": "mishra-back.jpg"},
        "all_parts": card["all_parts"],
    }
    cache_path = tmp_path / "scryfall_lookup_cache.json"
    cache_path.write_text(json.dumps({"exact:mishra, claimed by gix": card}), encoding="utf-8")
    requests = []

    def fetch_result(self, uri):
        requests.append(uri)
        assert uri == "https://api.scryfall.com/cards/melded"
        return httpx.Response(200, json=result, request=httpx.Request("GET", uri))

    monkeypatch.setattr(httpx.Client, "get", fetch_result)
    for _ in range(2):
        metadata, failures = enrich_commanders_with_scryfall(
            [CommanderIdentity(card["name"], "mishra")],
            user_agent="Test",
            delay_seconds=0,
            cache_path=cache_path,
            use_print_history_for_origin=False,
        )
        assert failures == []
        assert metadata[0]["color_identity"] == ["B", "R"]
        assert metadata[0]["partner_card_image_urls"] == ["mishra-front.jpg"]
        assert metadata[0]["commander_cards"] == [{
            "name": card["name"],
            "scryfall_uri": card["scryfall_uri"],
            "faces": [
                {"name": card["name"], "image_url": "mishra-front.jpg"},
                {"name": result["name"], "image_url": "mishra-back.jpg"},
            ],
        }]
    assert requests == ["https://api.scryfall.com/cards/melded"]
    # The result object can reference itself without adding its image twice.
    assert len(build_commander_cards([result])[0]["faces"]) == 1


def test_missing_meld_result_keeps_legacy_front_for_runtime_fallback():
    card = meld_commander()
    metadata = build_metadata_row(
        CommanderIdentity(card["name"], "mishra"), [card], match_strategy="exact_full_name"
    )
    assert metadata["commander_cards"] == []
    assert metadata["card_image_url"] == "mishra-front.jpg"
