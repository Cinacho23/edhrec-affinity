"""Join in-stock Card Kingdom offers to legal paper printings by Scryfall ID."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import json
from pathlib import Path
import re

from edhrec_affinity.build_styles.pipeline import atomic_json

from .catalog import load_catalog


CARD_KINGDOM_FEED = "https://api.cardkingdom.com/api/v2/pricelist"
CARD_KINGDOM_STORE = "https://www.cardkingdom.com/"
CONDITIONS = {"nm": "Near Mint", "ex": "Excellent", "vg": "Very Good", "g": "Good"}
CONDITION_PRIORITY = {condition: index for index, condition in enumerate(CONDITIONS.values())}
MIN_BASIC_STOCK = 38
MAX_NEEDED_COPIES = 100
ETCHED_LABEL = re.compile(r"\b(?:foil[ -]+etched|etched[ -]+foil)\b", re.IGNORECASE)
# Scryfall's format legalities describe the rules identity of these printings.
# A nonstandard physical card still cannot stand in for the actual card.
NONPLAYABLE_PRINTING_SETS = {"30a", "ced", "cei"}


def _price_cents(value):
    if type(value) not in {str, int}:
        return None
    try:
        cents = Decimal(value) * 100
    except InvalidOperation:
        return None
    if not cents.is_finite() or cents <= 0 or cents != cents.to_integral_value():
        return None
    return int(cents)


def _in_stock_products(feed):
    products = {}
    for product in feed["data"]:
        finish = product.get("is_foil")
        if (type(finish) is not bool and finish not in ("true", "false")) or not product.get("scryfall_id"):
            continue
        offers = []
        values = product.get("condition_values") or {}
        for grade, condition in CONDITIONS.items():
            stock = values.get(f"{grade}_qty")
            if type(stock) is not int or stock <= 0:
                continue
            cents = _price_cents(values.get(f"{grade}_price"))
            if cents is not None:
                offers.append({"price_cents": cents, "condition": condition, "stock": stock})
        if offers:
            products.setdefault(product["scryfall_id"], []).append((product, offers))
    return products


def _legal_paper_printing(printing):
    return (printing.get("lang") == "en" and "paper" in printing.get("games", ())
            and printing.get("legalities", {}).get("commander") == "legal"
            and printing.get("set") not in NONPLAYABLE_PRINTING_SETS and printing.get("set_type") != "memorabilia"
            and printing.get("border_color") not in {"gold", "silver"}
            and printing.get("security_stamp") != "acorn" and not printing.get("oversized"))


def _printing_finish(product, printing):
    # Card Kingdom labels some etched products with a false foil flag.
    # Use the listing's explicit finish and confirm it against the printing.
    finishes = set(printing.get("finishes", ()))
    variation = product.get("variation") or ""
    if variation.casefold() == "etched" or ETCHED_LABEL.search(variation) or ETCHED_LABEL.search(product["url"]):
        finish = "etched"
    elif product["is_foil"] is True or product["is_foil"] == "true":
        finish = "etched" if finishes == {"etched"} else "foil"
    else:
        finish = "nonfoil"
    return finish if finish in finishes else None


def _offer_priority(offer):
    return (offer["price_cents"], CONDITION_PRIORITY[offer["condition"]], offer["finish"] != "nonfoil",
            -offer["stock"], offer["scryfall_id"], offer["source_url"])


def _priced_card(offers, *, basic):
    selected, stock = [], 0
    estimate = None
    quantity = MIN_BASIC_STOCK if basic else 1
    for offer in sorted(offers, key=_offer_priority):
        selected.append(offer)
        stock += offer["stock"]
        if estimate is None and stock >= quantity:
            estimate = offer
        if stock >= MAX_NEEDED_COPIES:
            break
    # Budget construction uses this marginal unit price as a conservative estimate.
    # Shopping totals allocate the cheapest units from all relevant offers.
    return {**(estimate or selected[-1]), "offers": selected}


def build_price_snapshot(catalog, feed, printings_path, *, checked_at):
    """Return the cheapest eligible Card Kingdom stock for each priced Oracle identity."""
    by_card = {}
    products = _in_stock_products(feed)
    oracle_keys = {card["oracle_id"]: key for key, card in catalog.items()}
    with gzip.open(printings_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            printing = json.loads(line)
            printing_id = printing.get("id")
            key = oracle_keys.get(printing.get("oracle_id"))
            if key is None or printing_id not in products or not _legal_paper_printing(printing):
                continue
            for product, offers in products[printing_id]:
                url_path = product.get("url", "")
                if (not isinstance(url_path, str) or not url_path.startswith("mtg/")
                        or "not-tournament-legal" in url_path.casefold()):
                    continue
                finish = _printing_finish(product, printing)
                if finish is None:
                    continue
                for offer in offers:
                    quote = {**offer, "printing": printing["set_name"], "set_code": printing["set"],
                             "collector_number": printing["collector_number"], "finish": finish,
                             "scryfall_id": printing_id, "source_url": CARD_KINGDOM_STORE + url_path}
                    by_card.setdefault(key, []).append(quote)
    cards = {key: _priced_card(offers, basic="Basic" in catalog[key]["type_line"].split("—", 1)[0].split())
             for key, offers in sorted(by_card.items())}
    if not cards:
        raise ValueError("No in-stock Card Kingdom quotes matched legal Scryfall printings")
    return {
        "source": "Card Kingdom",
        "source_url": CARD_KINGDOM_FEED,
        "checked_at": checked_at,
        "source_created_at": feed["meta"].get("created_at"),
        "currency": "USD",
        "methodology": "Cheapest in-stock legal English copies across Card Kingdom editions, finishes, and conditions. "
                       "Quantities can draw from multiple offers. Prices exclude tax and shipping.",
        "cards": cards,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--price-feed", type=Path, required=True, help=f"Saved JSON from {CARD_KINGDOM_FEED}")
    parser.add_argument("--printing-jsonl", type=Path, required=True, help="Scryfall Default Cards gzipped JSONL")
    parser.add_argument("--oracle-jsonl", type=Path, required=True, help="Scryfall Oracle Cards gzipped JSONL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checked_at = datetime.fromtimestamp(args.price_feed.stat().st_mtime, timezone.utc).isoformat()
    catalog, _ = load_catalog(args.oracle_jsonl, checked_at)
    with args.price_feed.open(encoding="utf-8") as handle:
        feed = json.load(handle)
    snapshot = build_price_snapshot(catalog, feed, args.printing_jsonl, checked_at=checked_at)
    atomic_json(args.output, snapshot)
    print(f"Wrote {len(snapshot['cards']):,} Card Kingdom quotes to {args.output}.")


if __name__ == "__main__":
    main()
