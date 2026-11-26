"""Build and check the Oracle deck's four Card Kingdom budget tiers."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.build_styles.official_checker import OfficialDeckChecker
from edhrec_affinity.build_styles.pipeline import atomic_json
from edhrec_affinity.build_styles.sources import card_key

from .__main__ import DEFAULT_REPORT, commander_keys, report_for_deck, verify_report
from .catalog import load_catalog
from .prices import cheapest_copies


DEFAULT_BUDGET_REPORT = DEFAULT_REPORT.with_name("synergy-deck-budgets.json")
PLAYABILITY_MIN = 72.5
PLAYABILITY_MAX = 77.5


@dataclass(frozen=True)
class Tier:
    id: str
    label: str
    cap_cents: int | None
    land_count: int = 37
    ramp_goal: int | None = None
    ramp_limit: int | None = None
    land_share: float = .35
    mana_value_bias: float = 0


TIERS = (
    Tier("100", "$", 10_000), Tier("500", "$$", 50_000),
    Tier("1000", "$$$", 100_000), Tier("uncapped", "$$$$", None),
)


def load_snapshot(path):
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    for field in ("source", "source_url", "checked_at"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise ValueError(f"The price snapshot is missing {field}")
    if not isinstance(snapshot.get("cards"), dict) or not snapshot["cards"]:
        raise ValueError("The price snapshot has no card quotes")
    for key, quote in snapshot["cards"].items():
        price = quote.get("price_cents") if isinstance(quote, dict) else None
        if type(price) is not int or price <= 0:
            raise ValueError(f"Invalid USD cents for {key}")
    return snapshot


def _cheapest_copies(quote, quantity):
    return [{**offer, "price_usd": offer["price_cents"] / 100} for offer in cheapest_copies(quote, quantity)]


def _line_total_cents(options):
    return sum(option["quantity"] * option["price_cents"] for option in options)


def playability_status(rating):
    percentage = rating.get("playability_pct")
    if rating.get("status") != "verified" or type(percentage) not in {int, float} or not isfinite(percentage):
        return "unverified"
    if percentage < PLAYABILITY_MIN:
        return "below"
    if percentage > PLAYABILITY_MAX:
        return "above"
    return "within"


def price_tier(tier, report, quotes):
    deck = report["deck"]
    purchases = {row["name"]: _cheapest_copies(quotes.get(card_key(row["name"])), row["quantity"])
                 for row in deck["cards"]}
    line_totals = {name: _line_total_cents(options) for name, options in purchases.items()}
    total = sum(line_totals.values())
    missing = sorted(name for name, options in purchases.items() if not options)
    if tier.cap_cents is not None and (missing or total > tier.cap_cents):
        raise ValueError(f"The {tier.label} deck cannot be verified within its USD cap")
    for row in [*deck["cards"], *deck["commanders"]]:
        name = row["name"]
        options = purchases[name]
        for field in ("price_cents", "price_usd", "price_printing", "price_condition", "price_source_url"):
            row.pop(field, None)
        row["purchase_options"] = options
        row["price_total_usd"] = line_totals[name] / 100 if options else None
        if options and len({option["price_cents"] for option in options}) == 1:
            row["price_usd"] = options[0]["price_usd"]
    if budget := deck.get("construction", {}).get("budget"):
        land_total = sum(line_totals[row["name"]] for row in deck["cards"] if row["primary_role"] == "Lands")
        commander_total = sum(line_totals[row["name"]] for row in deck["commanders"])
        budget.update(total_cents=total, land_cents=land_total, commander_cents=commander_total,
                      spell_cents=total - land_total - commander_total)
    return {
        "id": tier.id,
        "label": tier.label,
        "cap_usd": tier.cap_cents / 100 if tier.cap_cents is not None else None,
        "total_usd": total / 100 if not missing else None,
        "priced_subtotal_usd": total / 100,
        "unpriced_cards": missing,
        "playability_target_status": playability_status(report["external_rating"]),
        "report": report,
    }


def generate_budget_tier(tier, catalog, rank, snapshot, evaluation, sample, *, snapshot_at, seed, epochs):
    from .budget_construction import build_budget_deck

    quotes = snapshot["cards"]
    prices_cents = {key: quote["price_cents"] for key, quote in quotes.items()}
    deck = build_budget_deck(catalog, rank, prices_cents, tier.cap_cents,
                             land_count=tier.land_count, ramp_goal=tier.ramp_goal, ramp_limit=tier.ramp_limit,
                             land_share=tier.land_share, mana_value_bias=tier.mana_value_bias)
    report = report_for_deck(deck, catalog, evaluation, sample, snapshot_at=snapshot_at, seed=seed, epochs=epochs)
    return price_tier(tier, report, quotes)


def generate_budget_report(catalog, ranker, uncapped, snapshot, evaluation, sample, *, snapshot_at, seed, epochs):
    quotes = snapshot["cards"]
    rank = ranker.for_commanders(commander_keys())
    tiers = []
    for tier in TIERS:
        if tier.cap_cents is None:
            result = price_tier(tier, uncapped, quotes)
        else:
            result = generate_budget_tier(tier, catalog, rank, snapshot, evaluation, sample,
                                          snapshot_at=snapshot_at, seed=seed, epochs=epochs)
        print(f"{tier.label}: {result['total_usd']} USD", flush=True)
        tiers.append(result)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pricing": _pricing_provenance(snapshot),
        "playability_target": {"minimum": PLAYABILITY_MIN, "maximum": PLAYABILITY_MAX},
        "tiers": tiers,
    }


def _pricing_provenance(snapshot):
    result = {key: snapshot[key] for key in ("source", "source_url", "checked_at")}
    result["description"] = snapshot.get("methodology") or "USD singles estimates include every copy and exclude tax and shipping."
    return result


def reprice_budget_report(path, snapshot):
    bundle = json.loads(path.read_text(encoding="utf-8"))
    tier_defs = {tier.id: tier for tier in TIERS}
    repriced = []
    for row in bundle["tiers"]:
        report = row["report"]
        deck = report["deck"]
        if cardlist_hash(deck["cards"]) != deck["deck_sha256"]:
            raise ValueError(f"The {row['label']} deck has a mismatched card list hash")
        repriced.append({**row, **price_tier(tier_defs[row["id"]], report, snapshot["cards"])})
    bundle.update(pricing=_pricing_provenance(snapshot), tiers=repriced)
    atomic_json(path, bundle)
    return bundle


def check_budget_report(path, validation_directory, *, timeout_seconds=120):
    bundle = json.loads(path.read_text(encoding="utf-8"))
    pending = []
    verified = {}
    for tier in bundle["tiers"]:
        report = tier["report"]
        exact_hash = cardlist_hash(report["deck"]["cards"])
        if exact_hash != report["deck"]["deck_sha256"]:
            raise ValueError(f"The {tier['label']} deck has a mismatched card list hash")
        rating = report["external_rating"]
        if rating.get("deck_sha256") == exact_hash and rating.get("status") == "verified":
            verified[exact_hash] = rating
        else:
            pending.append((tier, exact_hash))
    if not pending:
        return 0

    with OfficialDeckChecker() as checker:
        for tier, exact_hash in pending:
            report = tier["report"]
            if exact_hash in verified:
                report["external_rating"] = verified[exact_hash]
                result = {"status": "verified"}
            else:
                result = verify_report(report, validation_directory=validation_directory,
                                       timeout_seconds=timeout_seconds, checker=checker)
            rating = report["external_rating"]
            tier["playability_target_status"] = playability_status(rating)
            atomic_json(path, bundle)
            if result["status"] == "verified":
                verified[exact_hash] = rating
                print(f"{tier['label']}: bracket {rating['bracket']}, {rating['playability_pct']}% playability", flush=True)
            else:
                print(f"{tier['label']}: {result.get('reason', result['status'])}", flush=True)
                if result.get("source_blocked"):
                    break
    return 0 if all(tier["report"]["external_rating"].get("status") == "verified" for tier in bundle["tiers"]) else 1


def write_budget_exports(bundle, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for tier in bundle["tiers"]:
        destination = directory / f"{tier['id']}.txt"
        destination.write_text(tier["report"]["deck"]["export_text"], encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Train the Oracle ranker and build price-constrained tiers")
    generate.add_argument("--oracle-jsonl", type=Path, required=True)
    generate.add_argument("--catalog-updated-at", required=True)
    generate.add_argument("--previews", type=Path, required=True)
    generate.add_argument("--alias-cache", type=Path)
    generate.add_argument("--prices", type=Path, required=True)
    generate.add_argument("--uncapped", type=Path, default=DEFAULT_REPORT)
    generate.add_argument("--output", type=Path, default=DEFAULT_BUDGET_REPORT)
    generate.add_argument("--exports-directory", type=Path)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--epochs", type=int, default=2)
    check = commands.add_parser("check", help="Verify the exact tier lists with EDHPowerLevel")
    check.add_argument("--report", type=Path, default=DEFAULT_BUDGET_REPORT)
    check.add_argument("--validation-directory", type=Path, default=Path("data/prototypes/synergy-deck-validation"))
    check.add_argument("--timeout-seconds", type=float, default=120)
    reprice = commands.add_parser("reprice", help="Refresh the exact printings for existing checked deck lists")
    reprice.add_argument("--report", type=Path, default=DEFAULT_BUDGET_REPORT)
    reprice.add_argument("--prices", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "check":
        return check_budget_report(args.report, args.validation_directory, timeout_seconds=args.timeout_seconds)
    if args.command == "reprice":
        bundle = reprice_budget_report(args.report, load_snapshot(args.prices))
        for tier in bundle["tiers"]:
            print(f"{tier['label']}: {tier['total_usd']} USD")
        return 0
    if args.epochs < 1:
        parser.error("--epochs must be positive")

    from .ranker import DeckRanker

    snapshot = load_snapshot(args.prices)
    catalog, aliases = load_catalog(args.oracle_jsonl, args.catalog_updated_at, args.alias_cache)
    uncapped = json.loads(args.uncapped.read_text(encoding="utf-8"))
    ranker = DeckRanker(catalog, aliases, seed=args.seed)
    train, test, sample = ranker.load_decks(args.previews, commander_keys())
    sample.update(training_decks=len(train), heldout_decks=len(test), target_pair_decks=sample.get("target_pair_decks", 0))
    ranker.fit(train, epochs=args.epochs)
    bundle = generate_budget_report(catalog, ranker, uncapped, snapshot, ranker.evaluate(test), sample,
                                    snapshot_at=args.catalog_updated_at, seed=args.seed, epochs=args.epochs)
    atomic_json(args.output, bundle)
    if args.exports_directory:
        write_budget_exports(bundle, args.exports_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
