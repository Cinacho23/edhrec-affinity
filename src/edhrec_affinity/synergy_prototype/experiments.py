"""Generate and check controlled Tenth Doctor + Rose Tyler deck variants."""

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.build_styles.official_checker import OfficialDeckChecker
from edhrec_affinity.build_styles.pipeline import atomic_json
from edhrec_affinity.build_styles.sources import card_key

from .__main__ import DEFAULT_REPORT, commander_keys, generate_report, verify_report
from .catalog import load_catalog
from .construction import BASELINE_POLICY, ORACLE_POLICY


DEFAULT_EXPERIMENTS = DEFAULT_REPORT.with_name("synergy-deck-experiments.json")
DEFAULT_BASELINE = DEFAULT_REPORT.with_name("synergy-deck-experiments-baseline.json")


def _balanced_policy(policy):
    def fit(spell, model_score, coverage, *, emphasis=1.0):
        score = policy.fit(spell, model_score, coverage, emphasis=emphasis)
        if "Mana acceleration" in spell.roles:
            score -= .28 * max(0, coverage["Mana acceleration"] - 8)
        return score

    limits = {role: limit for role, limit in policy.limits.items() if role != "Mana acceleration"}
    return replace(policy, limits=limits, fit=fit,
                   score_explanation=policy.score_explanation + " Extra acceleration has diminishing value after eight sources.")


def _card_counts(deck):
    return Counter({row["name"]: row["quantity"] for row in deck["cards"]})


def _diff_summary(counts):
    return [name if quantity == 1 else f"{quantity} × {name}" for name, quantity in sorted(counts.items())]


def compare_deck(deck, baseline, catalog):
    counts, prior_counts = _card_counts(deck), _card_counts(baseline)
    roles = Counter()
    acceleration = 0
    for row in deck["cards"]:
        if row["primary_role"] not in {"Commander", "Lands"}:
            card_roles = ORACLE_POLICY.classify(catalog[card_key(row["name"])]).roles
            roles.update({role: row["quantity"] for role in card_roles})
            if card_roles & {"Suspend acceleration", "Time travel", "Extra upkeep"}:
                acceleration += row["quantity"]
    return {
        "added": _diff_summary(counts - prior_counts),
        "removed": _diff_summary(prior_counts - counts),
        "cards_changed": sum((counts - prior_counts).values()),
        "ramp": roles["Mana acceleration"],
        "direct_synergy": roles["Direct synergy"],
        "early_attackers": roles["Early attackers"],
        "interaction": roles["Interaction"],
        "protection": roles["Commander protection"],
        "repeatable_suspend": roles["Repeatable suspend"],
        "suspend_acceleration": acceleration,
        "exile_payoffs": roles["Exile payoffs"],
    }


def build_experiments(baseline, catalog, ranker, interaction_ranker, sample, *, snapshot_at, seed, epochs,
                      model_benchmark):
    baseline_evaluation = model_benchmark["baseline"]
    interaction_evaluation = model_benchmark["interaction"]
    mean_model = "The original neural ranker compares each candidate with the commanders and the average features of the partial deck."
    interaction_model = "The experimental ranker also evaluates how each candidate relates to individual cards in the partial deck."
    variants = [
        ("mechanics", "Oracle mechanics", "Recognizes cards that grant suspend, accelerate suspended spells, or reward casting from exile. It gives overlapping free-casting effects less credit.",
         ranker, ORACLE_POLICY, mean_model, baseline_evaluation),
        ("balanced", "Diminishing returns", "Replaces the hard ramp ceiling with a rising penalty after eight accelerators. This tests whether the search chooses distinct roles when extra ramp earns less credit.",
         ranker, _balanced_policy(BASELINE_POLICY), mean_model, baseline_evaluation),
        ("interactions", "Card-to-card model", "Uses a separately trained neural ranker that compares each candidate with individual cards in the partial deck. The original construction rules make the model change easier to see.",
         interaction_ranker, BASELINE_POLICY, interaction_model, interaction_evaluation),
        ("combined", "Combined experiment", "Applies the revised Oracle mechanics, diminishing returns for ramp, and the card-to-card neural ranker together.",
         interaction_ranker, _balanced_policy(ORACLE_POLICY), interaction_model, interaction_evaluation),
    ]
    reports = []
    for identifier, title, change, model, policy, description, evaluation in variants:
        report = generate_report(catalog, model, evaluation, sample, snapshot_at=snapshot_at, seed=seed,
                                 epochs=epochs, policy=policy)
        report["synergy"]["model"] = description
        report.update(id=identifier, title=title, change=change,
                      comparison=compare_deck(report["deck"], baseline["deck"], catalog))
        print(f"{title}: {report['deck']['deck_sha256']}", flush=True)
        reports.append(report)
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline_deck_sha256": baseline["deck"]["deck_sha256"],
            "baseline_comparison": compare_deck(baseline["deck"], baseline["deck"], catalog),
            "model_benchmark": model_benchmark,
            "variants": reports}


def check_experiments(path, validation_directory, *, timeout_seconds=120):
    report = json.loads(path.read_text(encoding="utf-8"))
    previous = {}
    with OfficialDeckChecker() as checker:
        for variant in report["variants"]:
            exact_hash = variant["deck"]["deck_sha256"]
            if cardlist_hash(variant["deck"]["cards"]) != exact_hash:
                raise ValueError(f"{variant['title']} has an incorrect card list hash")
            rating = variant["external_rating"]
            if rating.get("status") == "verified" and rating.get("deck_sha256") == exact_hash:
                previous[exact_hash] = rating
                continue
            if exact_hash in previous:
                variant["external_rating"] = previous[exact_hash]
                atomic_json(path, report)
                continue
            result = verify_report(variant, validation_directory=validation_directory,
                                   timeout_seconds=timeout_seconds, checker=checker)
            atomic_json(path, report)
            if result["status"] == "verified":
                previous[exact_hash] = variant["external_rating"]
                print(f"{variant['title']}: EDHPowerLevel bracket {variant['external_rating']['bracket']}", flush=True)
            else:
                print(f"{variant['title']}: {result.get('reason', result['status'])}", flush=True)
                if result.get("source_blocked"):
                    break
    return 0 if all(variant["external_rating"].get("status") == "verified" for variant in report["variants"]) else 1


def write_exports(baseline, report, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for variant in [{"id": "baseline", "deck": baseline["deck"]}, *report["variants"]]:
        path = directory / f"{variant['id']}.txt"
        path.write_text(variant["deck"]["export_text"], encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Train both rankers and generate the comparison decks")
    generate.add_argument("--oracle-jsonl", type=Path, required=True)
    generate.add_argument("--catalog-updated-at", required=True)
    generate.add_argument("--previews", type=Path, required=True)
    generate.add_argument("--alias-cache", type=Path)
    generate.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    generate.add_argument("--output", type=Path, default=DEFAULT_EXPERIMENTS)
    generate.add_argument("--exports-directory", type=Path)
    generate.add_argument("--epochs", type=int, default=2)
    generate.add_argument("--seed", type=int, default=42)
    check = commands.add_parser("check", help="Check each distinct exact list with EDHPowerLevel")
    check.add_argument("--report", type=Path, default=DEFAULT_EXPERIMENTS)
    check.add_argument("--validation-directory", type=Path, default=Path("data/prototypes/synergy-deck-validation"))
    check.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    if args.command == "check":
        return check_experiments(args.report, args.validation_directory, timeout_seconds=args.timeout_seconds)
    if args.epochs < 1:
        parser.error("--epochs must be positive")

    from .interaction_ranker import InteractionDeckRanker, paired_evaluation
    from .ranker import DeckRanker

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    catalog, aliases = load_catalog(args.oracle_jsonl, args.catalog_updated_at, args.alias_cache)
    ranker = DeckRanker(catalog, aliases, seed=args.seed)
    train, test, sample = ranker.load_decks(args.previews, commander_keys())
    sample.update(training_decks=len(train), heldout_decks=len(test), target_pair_decks=sample.get("target_pair_decks", 0))
    print(f"Training both rankers on {len(train):,} decks.", flush=True)
    ranker.fit(train, epochs=args.epochs)
    interaction_ranker = InteractionDeckRanker.from_ranker(ranker, seed=args.seed)
    interaction_ranker.fit(train, epochs=args.epochs)
    report = build_experiments(baseline, catalog, ranker, interaction_ranker, sample,
                               snapshot_at=args.catalog_updated_at, seed=args.seed, epochs=args.epochs,
                               model_benchmark=paired_evaluation(ranker, interaction_ranker, test, seed=args.seed))
    atomic_json(args.output, report)
    if args.exports_directory:
        write_exports(baseline, report, args.exports_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
