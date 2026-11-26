"""Generate and externally check the static Tenth Doctor + Rose Tyler prototype."""

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import pickle

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.build_styles.official_checker import check_deck, deck_url
from edhrec_affinity.build_styles.pipeline import atomic_json
from edhrec_affinity.build_styles.sources import card_key

from .catalog import load_catalog
from .construction import COMMANDER_NAMES, ORACLE_POLICY, build_deck


DEFAULT_REPORT = Path("frontend/src/generated/synergy-deck.json")
ORACLE_MODEL_DESCRIPTION = (
    "A three-layer neural ranker predicts card fit from the commanders and partial deck. "
    "Oracle-text rules give separate credit for repeatable suspend, countdown acceleration, "
    "and effects that reward casting from exile."
)
REFINED_MODEL_DESCRIPTION = (
    "The neural ranker scores legal cards using the commanders and partial deck. The optimizer checks "
    "when each effect works, scores the suspend engine across the full list, and sizes the land allocation "
    "from the Card Kingdom price of the nonlands."
)


def commander_keys():
    return tuple(map(card_key, COMMANDER_NAMES))


def _game_plan(deck):
    names = {row["name"] for row in deck["cards"]}
    details = [
        ("Attack to suspend spells",
         "The Doctor triggers once when you declare one or more attackers, even if he stays back. "
         "Start with cheap attackers and use repeatable suspend effects to keep spells waiting in exile.",
         ("Jhoira of the Ghitu", "The Eleventh Doctor", "Inspiring Refrain", "Rory Williams")),
        ("Make time counters do two jobs",
         "Keep suspended cards and other permanents with time counters to grow Rose on attack. "
         "When both attack triggers fire, resolve the Doctor's first so Rose can count the newly suspended card. "
         "Use time travel or a counter-removal effect to cast a suspended spell sooner.",
         ("Time Beetle", "Clockspinning", "Kate Stewart", "All of History, All at Once", "Rotating Fireplace")),
        ("Get more from spells cast out of exile",
         "Once the final time counter comes off, cast the suspended spell and collect any cast-from-exile payoffs the list has in play.",
         ("Passionate Archaeologist", "The Twelfth Doctor", "Sage of the Beyond", "Nalfeshnee", "Wild-Magic Sorcerer")),
    ]
    result = []
    for title, description, examples in details:
        used = [name for name in examples if name in names]
        if used:
            description += " The list includes " + ", ".join(used[:3]) + "."
        result.append({"title": title, "description": description})
    return result


def generate_report(catalog, ranker, evaluation, sample, *, snapshot_at, seed, epochs, policy=ORACLE_POLICY):
    deck = build_deck(catalog, ranker.for_commanders(commander_keys()), policy=policy)
    return report_for_deck(deck, catalog, evaluation, sample, snapshot_at=snapshot_at, seed=seed, epochs=epochs)


def report_for_deck(deck, catalog, evaluation, sample, *, snapshot_at, seed, epochs):
    for row in deck["cards"]:
        card = catalog[card_key(row["name"])]
        row["scryfall_uri"] = card.get("scryfall_uri")
    for row in deck["commanders"]:
        row["image_url"] = catalog[card_key(row["name"])].get("image_url")
    deck["deck_sha256"] = deck.pop("cardlist_sha256")
    external_url = deck["validation"].pop("external_url")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deck": deck,
        "synergy": {
            "summary": "Suspend spells on attack, turn waiting cards into power for Rose, and use time travel to choose when those spells come back.",
            "highlights": _game_plan(deck),
            "model": ORACLE_MODEL_DESCRIPTION,
            "objective_score": deck["synergy_score"],
            "evaluated_decks": len(deck["construction"]["candidate_fit_scores"]),
            "catalog_cards": len(catalog),
            "catalog_updated_at": snapshot_at,
            "seed": seed,
            "epochs": epochs,
            "evaluation": evaluation,
            **sample,
        },
        "external_rating": {"status": "unverified", "deck_sha256": deck["deck_sha256"]},
        "external_url": external_url,
    }


def verify_report(report, *, validation_directory, timeout_seconds=120, checker=None):
    deck = report["deck"]
    exact_hash = cardlist_hash(deck["cards"])
    if deck.get("deck_sha256") != exact_hash:
        raise ValueError("The report's deck hash does not match its card list")
    report["external_url"] = deck_url(deck)
    options = {"validation_directory": validation_directory, "timeout_seconds": timeout_seconds}
    if checker is not None:
        options["checker"] = checker
    result = check_deck(deck, **options)
    if result["status"] == "verified":
        observation = result["observation"]
        if observation["deck_sha256"] != exact_hash or observation["status"] not in {"verified", "stale"}:
            raise ValueError("EDHPowerLevel returned an unverifiable observation")
        report["external_rating"] = observation
    else:
        previous = report.get("external_rating", {})
        if previous.get("deck_sha256") != exact_hash or previous.get("status") not in {"verified", "stale"}:
            report["external_rating"] = {key: result[key] for key in ("status", "checked_at", "reason") if key in result}
            report["external_rating"]["deck_sha256"] = exact_hash
    return result


def refine_reports(args):
    from edhrec_affinity.build_styles.official_checker import OfficialDeckChecker
    from .budgets import load_snapshot, playability_status
    from .refinement import build_candidates, price_deck, selection_score

    baseline = json.loads(args.training_report.read_text(encoding="utf-8"))["synergy"]
    snapshot_at, seed, epochs = (baseline[key] for key in ("catalog_updated_at", "seed", "epochs"))
    catalog, _ = load_catalog(args.oracle_jsonl, snapshot_at)
    with args.trained_ranker.open("rb") as handle:
        ranker, evaluation, sample = pickle.load(handle)
    if list(catalog) != ranker.keys:
        raise ValueError("The Oracle catalog must match the saved ranker's feature index")
    snapshot = load_snapshot(args.prices)
    prices = {key: quote["price_cents"] for key, quote in snapshot["cards"].items()}
    args.output_directory.mkdir(parents=True, exist_ok=True)
    reports = {}
    for deck in build_candidates(catalog, ranker.for_commanders(commander_keys()), prices):
        digest = deck["cardlist_sha256"]
        if digest in reports:
            continue
        report = report_for_deck(deck, catalog, evaluation, sample, snapshot_at=snapshot_at, seed=seed, epochs=epochs)
        report["synergy"]["model"] = REFINED_MODEL_DESCRIPTION
        report["pricing"], shopping = price_deck(deck, snapshot)
        atomic_json(args.output_directory / f"{digest}-shopping.json", shopping)
        reports[digest] = report
    eligible = []
    with (OfficialDeckChecker() if args.check else nullcontext()) as checker:
        for digest, report in reports.items():
            report["synergy"]["evaluated_decks"] = len(reports)
            if args.check:
                verify_report(report, validation_directory=args.output_directory / "edhpowerlevel", checker=checker,
                              timeout_seconds=args.timeout_seconds)
            atomic_json(args.output_directory / f"{digest}.json", report)
            if playability_status(report["external_rating"]) == "within" and report["pricing"]["total_cents"] is not None:
                eligible.append(report)
    if eligible:
        selected = max(eligible, key=selection_score)
        atomic_json(args.output_directory / "selected.json", selected)
        (args.output_directory / "selected.txt").write_text(selected["deck"]["export_text"], encoding="utf-8")
        print(f"Selected {selected['deck']['deck_sha256']} from {len(eligible)} verified lists.")
    else:
        print(f"Wrote {len(reports)} candidates. No complete price and in-range external rating were verified.")
    return int(args.check and not eligible)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Train and generate the prototype report")
    generate.add_argument("--oracle-jsonl", type=Path, required=True)
    generate.add_argument("--catalog-updated-at", required=True)
    generate.add_argument("--previews", type=Path, required=True)
    generate.add_argument("--target-previews", type=Path,
                          help="Train on every usable distinct list from a completed target-pair checkpoint")
    generate.add_argument("--alias-cache", type=Path)
    generate.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    generate.add_argument("--epochs", type=int, default=2)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--save-ranker", type=Path, help="Write a trusted local cache for optimizer comparisons")
    refine = commands.add_parser("refine", help="Regenerate Doctor + Rose with a saved ranker and a proportional mana budget")
    refine.add_argument("--trained-ranker", type=Path, required=True, help="Trusted local cache from --save-ranker")
    refine.add_argument("--training-report", type=Path, default=DEFAULT_REPORT)
    refine.add_argument("--oracle-jsonl", type=Path, required=True)
    refine.add_argument("--prices", type=Path, required=True)
    refine.add_argument("--output-directory", type=Path, required=True)
    refine.add_argument("--check", action="store_true", help="Verify the exact lists and choose the best in-range candidate")
    refine.add_argument("--timeout-seconds", type=float, default=60)
    verify = commands.add_parser("check", help="Check the exact generated deck with EDHPowerLevel")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    verify.add_argument("--validation-directory", type=Path, default=Path("data/prototypes/synergy-deck-validation"))
    verify.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    if args.command == "refine":
        return refine_reports(args)
    if args.command == "check":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        result = verify_report(report, validation_directory=args.validation_directory, timeout_seconds=args.timeout_seconds)
        atomic_json(args.report, report)
        if result["status"] == "verified":
            print(f"EDHPowerLevel recommended bracket {result['observation']['bracket']} for {report['deck']['deck_sha256']}.")
            return 0
        print(result.get("reason", result["status"]))
        return 1
    if args.epochs < 1:
        parser.error("--epochs must be positive")

    from .ranker import DeckRanker

    catalog, aliases = load_catalog(args.oracle_jsonl, args.catalog_updated_at, args.alias_cache)
    ranker = DeckRanker(catalog, aliases, seed=args.seed)
    train, test, sample = ranker.load_decks(args.previews, commander_keys(), target_previews=args.target_previews)
    sample.update(training_decks=len(train), heldout_decks=len(test), target_pair_decks=sample.get("target_pair_decks", 0))
    print(f"Training on {len(train):,} decks and {len(catalog):,} cards.", flush=True)
    ranker.fit(train, epochs=args.epochs)
    evaluation = ranker.evaluate(test)
    if args.save_ranker:
        with args.save_ranker.open("wb") as handle:
            pickle.dump((ranker, evaluation, sample), handle, protocol=pickle.HIGHEST_PROTOCOL)
    report = generate_report(catalog, ranker, evaluation, sample, snapshot_at=args.catalog_updated_at,
                             seed=args.seed, epochs=args.epochs)
    atomic_json(args.output, report)
    print(f"Wrote {args.output} with deck hash {report['deck']['deck_sha256']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
