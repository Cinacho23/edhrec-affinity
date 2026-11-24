import json
from pathlib import Path

import pytest

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.synergy_prototype.budgets import (
    DEFAULT_BUDGET_REPORT,
    TIERS,
    _cheapest_copies,
    playability_status,
    price_tier,
)


def test_price_includes_both_commanders_and_each_basic_up_to_the_cap():
    commanders = [{"name": "Doctor", "quantity": 1}, {"name": "Rose", "quantity": 1}]
    basics = {"name": "Plains", "quantity": 98}
    report = {"deck": {"cards": [*commanders, basics], "commanders": commanders}, "external_rating": {}}
    quotes = {"doctor": {"price_cents": 101}, "rose": {"price_cents": 99},
              "plains": {"price_cents": 100, "stock": 98}}

    result = price_tier(TIERS[0], report, quotes)

    assert result["total_usd"] == result["cap_usd"] == 100
    assert basics["price_usd"] == 1
    assert result["unpriced_cards"] == []
    with pytest.raises(ValueError, match="USD cap"):
        price_tier(TIERS[0], report, {**quotes, "rose": {"price_cents": 100}})
    with pytest.raises(ValueError, match="USD cap"):
        price_tier(TIERS[0], report, {**quotes, "plains": {"price_cents": 100, "stock": 97}})


def test_repeated_cards_use_the_cheapest_stock_across_printings():
    plains = {"name": "Plains", "quantity": 3}
    report = {"deck": {"cards": [plains], "commanders": []}, "external_rating": {}}
    quote = {"price_cents": 34, "offers": [
        {"price_cents": 34, "stock": 38, "printing": "Second set"},
        {"price_cents": 25, "stock": 2, "printing": "First set"},
    ]}

    priced = price_tier(TIERS[0], report, {"plains": quote})

    assert priced["total_usd"] == plains["price_total_usd"] == .84
    assert "price_usd" not in plains
    assert [(row["printing"], row["quantity"], row["price_usd"]) for row in plains["purchase_options"]] == [
        ("First set", 2, .25), ("Second set", 1, .34),
    ]
    assert _cheapest_copies(quote, 41) == []


@pytest.mark.parametrize(("value", "expected"), [(72.4, "below"), (72.5, "within"),
                                                  (77.5, "within"), (77.6, "above")])
def test_playability_target_includes_both_endpoints(value, expected):
    assert playability_status({"status": "verified", "playability_pct": value}) == expected


def test_built_tiers_have_exact_card_totals_prices_and_observations():
    path = Path(__file__).resolve().parents[1] / DEFAULT_BUDGET_REPORT
    bundle = json.loads(path.read_text(encoding="utf-8"))
    assert [tier["id"] for tier in bundle["tiers"]] == [tier.id for tier in TIERS]
    assert [tier["cap_usd"] for tier in bundle["tiers"]] == [100, 500, 1000, None]
    for tier in bundle["tiers"]:
        report = tier["report"]
        deck, rating = report["deck"], report["external_rating"]
        assert sum(card["quantity"] for card in deck["cards"]) == 100
        assert all(deck["validation"]["checks"].values())
        assert cardlist_hash(deck["cards"]) == deck["deck_sha256"] == rating["deck_sha256"]
        assert tier["playability_target_status"] == playability_status(rating)
        if tier["cap_usd"] is not None:
            assert tier["unpriced_cards"] == []
            assert sum(round(card["price_total_usd"] * 100) for card in deck["cards"]) == round(tier["total_usd"] * 100)
            assert tier["total_usd"] <= tier["cap_usd"]
        for card in deck["cards"]:
            if card["name"] in tier["unpriced_cards"]:
                assert not card["purchase_options"]
                continue
            options = card["purchase_options"]
            assert sum(option["quantity"] for option in options) == card["quantity"]
            assert all(0 < option["quantity"] <= option["stock"] for option in options)
            assert sum(option["price_cents"] * option["quantity"] for option in options) == round(card["price_total_usd"] * 100)
