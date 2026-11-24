from dataclasses import replace

import pytest

from edhrec_affinity.build_styles.sources import card_key
from edhrec_affinity.synergy_prototype import budget_construction as builder
from edhrec_affinity.synergy_prototype.construction import ConstructionSearchError, ORACLE_POLICY


def _catalog():
    def card(name, type_line, colors=(), oracle_text="", cmc=0):
        return {"name": name, "type_line": type_line, "color_identity": list(colors), "oracle_text": oracle_text,
                "legalities": {"commander": "legal"}, "cmc": cmc, "layout": "normal", "card_faces": []}

    cards = [
        card("The Tenth Doctor", "Legendary Creature — Time Lord Doctor", "UR", cmc=5),
        card("Rose Tyler", "Legendary Creature — Human", "W", "Doctor's companion", cmc=2),
        *(card(name, f"Basic Land — {name}", color)
          for name, color in (("Plains", "W"), ("Island", "U"), ("Mountain", "R"))),
        *(card(f"Regular spell {number}", "Artifact", cmc=2) for number in range(61)),
        card("Premium spell", "Artifact", cmc=2),
        card("Unpriced spell", "Artifact", cmc=2),
    ]
    return {card_key(row["name"]): row for row in cards}


def test_whole_deck_budget_prices_commanders_and_basic_copies(monkeypatch):
    catalog = _catalog()
    prices = {key: 5 for key in catalog if key != "unpriced spell"}
    prices.update({"the tenth doctor": 50, "rose tyler": 50, "plains": 10, "island": 10, "mountain": 10,
                   "premium spell": 99})
    policy = replace(ORACLE_POLICY, goals={"Early ramp": 0, "Efficient ramp": 0}, limits={"Mana acceleration": 0})
    monkeypatch.setattr(builder, "ORACLE_POLICY", policy)

    def rank(_):
        return {**dict.fromkeys(catalog, 0), "unpriced spell": 100, "premium spell": 99}

    cap = 50 + 50 + 37 * 10 + 61 * 5
    deck = builder.build_budget_deck(catalog, rank, prices, cap, land_share=.6, starts=1)
    assert {row["name"] for row in deck["cards"]}.isdisjoint({"Premium spell", "Unpriced spell"})
    assert deck["validation"]["valid"]
    assert deck["validation"]["checks"]["within_price_cap"]
    assert deck["construction"]["budget"]["total_cents"] == cap
    assert sum(row["quantity"] * row["price_cents"] for row in deck["cards"]) == cap


def test_missing_commander_price_prevents_budget_certification():
    catalog = _catalog()
    prices = {key: 1 for key in catalog if key != "rose tyler"}

    with pytest.raises(ConstructionSearchError, match="No verified eligible price for commanders: Rose Tyler"):
        builder.build_budget_deck(catalog, lambda _: {}, prices, 10000)
