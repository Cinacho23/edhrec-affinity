import base64
import gzip
import json
from types import SimpleNamespace

import numpy as np

from edhrec_affinity.synergy_decks.assets import (
    FEATURE_SCALE, _aliases_by_card, _commander_eligible, export_browser_assets, refresh_browser_assets,
)


def test_browser_assets_use_color_shards_and_little_endian_model_features(tmp_path):
    shared = {"oracle_text": "", "mana_cost": "", "cmc": 0, "keywords": [], "layout": "normal", "card_faces": []}
    catalog = {
        "plains": {**shared, "name": "Plains", "type_line": "Basic Land — Plains", "color_identity": [],
                   "produced_mana": ["W"], "oracle_id": "land"},
        "saruman": {**shared, "name": "Saruman", "type_line": "Legendary Creature — Wizard", "color_identity": ["U"],
                    "mana_cost": "{U}", "cmc": 1, "oracle_id": "commander"},
    }
    ranker = SimpleNamespace(index={"plains": 1, "saruman": 0},
                             features=np.asarray([[.25, -.5], [1, 2]], dtype=np.float32),
                             model=SimpleNamespace(coefs_=[np.arange(10, dtype=np.float32).reshape((10, 1))],
                                                   intercepts_=[np.asarray([.5])]))
    quotes = {"source": "Card Kingdom", "source_url": "https://www.cardkingdom.com/",
              "checked_at": "2026-11-23T22:51:00Z", "cards": {"plains": {"offers": [
                  {"price_cents": 20, "stock": 3}, {"price_cents": 40, "stock": 99},
              ]}}}
    output = tmp_path / "public"
    manifest = export_browser_assets(output, catalog, {"white wizard": "saruman", "plains": "saruman"}, ranker,
                                     quotes, snapshot_at="2026-11-21T22:01:57Z")

    assert manifest == json.loads((output / "manifest.json").read_text())
    assert [shard["color_mask"] for shard in manifest["shards"]] == [0, 2]
    model = json.loads((output / manifest["model_path"]).read_text())
    assert model["shapes"] == [[10, 1]] and model["weights"] == [list(range(10))]
    assert model["feature_count"] == 2 and model["feature_scale"] == FEATURE_SCALE
    payloads = [json.loads(gzip.decompress((output / shard["path"]).read_bytes())) for shard in manifest["shards"]]
    basic, commander = [payload["cards"][0] for payload in payloads]
    assert basic["produced_mask"] == 1 and basic["copies"] == 100
    assert [offer["price_cents"] for offer in basic["offers"]] == [20, 40]
    assert commander["aliases"] == ["white wizard"] and commander["can_command"] is True
    for payload, expected in zip(payloads, ([1, 2], [.25, -.5]), strict=True):
        features = np.frombuffer(base64.b64decode(payload["features"]), dtype="<i2") / FEATURE_SCALE
        np.testing.assert_array_equal(features, expected)

    catalog["new card"] = {**catalog["saruman"], "name": "New Card", "oracle_id": "new"}
    refreshed = refresh_browser_assets(output, output, catalog, {}, quotes, snapshot_at="2026-11-23T22:01:57Z")
    assert refreshed["model_catalog_updated_at"] == manifest["oracle_updated_at"]
    assert refreshed["oracle_only_cards"] == 1
    shard = json.loads(gzip.decompress((output / refreshed["shards"][1]["path"]).read_bytes()))
    new_card, saruman = shard["cards"]
    assert new_card["has_model_features"] is False and "has_model_features" not in saruman
    assert saruman["aliases"] == ["white wizard"]
    features = np.frombuffer(base64.b64decode(shard["features"]), dtype="<i2") / FEATURE_SCALE
    np.testing.assert_array_equal(features, [0, 0, .25, -.5])


def test_alias_for_reversible_printing_requires_one_proven_oracle_identity(tmp_path):
    printings = tmp_path / "printings.jsonl.gz"
    shared = {"layout": "reversible_card", "lang": "en", "games": ["paper"], "legalities": {"commander": "legal"}}
    with gzip.open(printings, "wt") as handle:
        for identity, name in (("commander", "Repeated // Repeated"), ("other", "Mistaken // Repeated")):
            handle.write(json.dumps({**shared, "name": name, "card_faces": [
                {"oracle_id": identity}, {"oracle_id": "commander"},
            ]}) + "\n")
    aliases = _aliases_by_card({"commander": {"oracle_id": "commander"}}, {}, printings)
    assert aliases == {"commander": ["repeated // repeated"]}


def test_grist_is_commander_eligible_through_its_command_zone_ability():
    assert _commander_eligible({
        "type_line": "Legendary Planeswalker — Grist",
        "oracle_text": "As long as Grist, the Hunger Tide isn't on the battlefield, it's a 1/1 Insect creature in addition to its other types.",
    })
    assert not _commander_eligible({
        "type_line": "Legendary Planeswalker — Chandra",
        "oracle_text": "Whenever you cast your first red spell each turn, draw a card.",
    })
