import gzip
import json

from edhrec_affinity.synergy_prototype.prices import CARD_KINGDOM_FEED, build_price_snapshot


def test_card_kingdom_quotes_use_legal_printings_and_stock(tmp_path):
    catalog = {
        "tundra": {"oracle_id": "tundra", "type_line": "Land — Plains Island"},
        "island": {"oracle_id": "island", "type_line": "Basic Land — Island"},
        "plains": {"oracle_id": "plains", "type_line": "Basic Land — Plains"},
        "sol ring": {"oracle_id": "sol-ring", "type_line": "Artifact"},
    }
    standard = {"lang": "en", "games": ["paper"], "finishes": ["nonfoil"],
                "legalities": {"commander": "legal"}, "set_name": "Test edition", "set": "tst",
                "collector_number": "1"}
    printings = [
        {**standard, "id": "tundra-normal", "oracle_id": "tundra"},
        {**standard, "id": "tundra-foil", "oracle_id": "tundra", "finishes": ["foil"], "collector_number": "2"},
        {**standard, "id": "tundra-gold", "oracle_id": "tundra", "border_color": "gold"},
        {**standard, "id": "tundra-foreign", "oracle_id": "tundra", "lang": "ja"},
        {**standard, "id": "tundra-30a", "oracle_id": "tundra", "set": "30a"},
        {**standard, "id": "tundra-memorabilia", "oracle_id": "tundra", "set_type": "memorabilia"},
        {**standard, "id": "tundra-not-tournament-legal", "oracle_id": "tundra"},
        {**standard, "id": "island-low-stock", "oracle_id": "island"},
        {**standard, "id": "island-high-stock", "oracle_id": "island"},
        {**standard, "id": "sol-ring-etched", "oracle_id": "sol-ring", "finishes": ["nonfoil", "etched"]},
    ]
    path = tmp_path / "printings.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in printings:
            print(json.dumps(row), file=handle)

    def offer(printing, price, quantity, *, foil="false", **conditions):
        return {"scryfall_id": printing, "is_foil": foil, "url": f"mtg/test/{printing}",
                "condition_values": {"nm_price": price, "nm_qty": quantity, **conditions}}

    feed = {"meta": {"created_at": "2026-11-23 14:11:07"}, "data": [
        offer("tundra-normal", "199.99", 1, ex_price="170.00", ex_qty=1, g_price="89.99", g_qty=0),
        offer("tundra-normal", "20.00", 30, foil="true"),
        offer("tundra-foil", "160.00", 2, foil="true"),
        offer("tundra-gold", "40.00", 3),
        offer("tundra-foreign", "30.00", 4),
        offer("tundra-30a", "45.00", 12),
        offer("tundra-memorabilia", "50.00", 12),
        offer("tundra-not-tournament-legal", "55.00", 12),
        offer("island-low-stock", "0.12", 37),
        offer("island-high-stock", "0.22", 38),
        {**offer("sol-ring-etched", "1.99", 2), "variation": "Foil Etched"},
        offer("unknown-printing", "0.01", 200),
    ]}

    snapshot = build_price_snapshot(catalog, feed, path, checked_at="2026-11-23T22:00:00Z")

    assert snapshot["source"] == "Card Kingdom"
    assert snapshot["source_url"] == CARD_KINGDOM_FEED
    assert snapshot["currency"] == "USD"
    tundra = snapshot["cards"]["tundra"]
    assert tundra["price_cents"] == 16000
    assert tundra["finish"] == "foil"
    assert tundra["set_code"] == "tst" and tundra["collector_number"] == "2"
    assert [offer["price_cents"] for offer in tundra["offers"]] == [16000, 17000, 19999]
    assert tundra["offers"][1]["condition"] == "Excellent"
    island = snapshot["cards"]["island"]
    assert island["price_cents"] == 22
    assert [(offer["price_cents"], offer["stock"]) for offer in island["offers"]] == [(12, 37), (22, 38)]
    assert snapshot["cards"]["sol ring"]["finish"] == "etched"
    assert "plains" not in snapshot["cards"]
