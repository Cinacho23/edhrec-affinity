"""Train, compare, price, and check commander-specific Synergy Decks."""

import argparse
from contextlib import nullcontext
import gzip
import json
from pathlib import Path
import pickle

import numpy as np

from ..build_styles.collection import deck_dataset_sha256
from ..build_styles.official_checker import OfficialDeckChecker
from ..build_styles.pipeline import atomic_json
from ..synergy_decks.typed_ranker import TypedDeckRanker
from . import pantlaza, yshtola
from .__main__ import report_for_deck, verify_report
from .budgets import load_snapshot, playability_status
from .catalog import load_catalog
from .refinement import price_deck


_COMMANDERS = {policy.COMMANDER_SLUG: policy for policy in (pantlaza, yshtola)}


def _read_checkpoint(path, commander_slug):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("commander_slug") != commander_slug:
        raise ValueError(f"The checkpoint is for a different commander: {path}")
    return data


def freeze_target(latest, historical, output, *, commander_slug):
    """Freeze the observed previews and keep their public index coverage explicit."""
    current = _read_checkpoint(latest, commander_slug)
    current_ids = {row["urlhash"] for row in current["frame"]}
    frames = {}
    records = {}
    for source in (current, *(_read_checkpoint(path, commander_slug) for path in historical)):
        for row in source["frame"]:
            key = row["urlhash"]
            result = source["results"].get(key)
            if not result:
                continue
            if key not in records or not records[key].get("deck") and result.get("deck"):
                frames[key] = row
                records[key] = result
    if not records:
        raise ValueError(f"No observed deck previews were available for {commander_slug}")
    snapshot = {**current, "frame": list(frames.values()), "results": records}
    with gzip.open(output, "wt", encoding="utf-8", compresslevel=1) as handle:
        json.dump(snapshot, handle, ensure_ascii=False, separators=(",", ":"))
    return {
        "public_index_decks": len(current_ids),
        "current_previews_attempted": len(current["results"]),
        "current_previews_loaded": sum(bool(row.get("deck")) for row in current["results"].values()),
        "historical_previews_added": sum(bool(row.get("deck")) for key, row in records.items() if key not in current["results"]),
        "complete_current_index": current_ids <= current["results"].keys(),
        "frozen_preview_count": len(records),
        "target_dataset_sha256": deck_dataset_sha256([row["deck"] for row in records.values() if row.get("deck")]),
    }


def train(args, commander):
    args.output_directory.mkdir(parents=True, exist_ok=True)
    frozen = args.output_directory / "target-training-previews.json.gz"
    coverage = freeze_target(args.current_previews, args.historical_previews, frozen, commander_slug=commander.COMMANDER_SLUG)
    catalog, aliases = load_catalog(args.oracle_jsonl, args.catalog_updated_at, args.alias_cache)
    included = TypedDeckRanker(catalog, aliases, seed=args.seed)
    training, test, counts = included.load_decks(args.previews, (commander.COMMANDER_KEY,), target_previews=frozen)
    exact = (included.index[commander.COMMANDER_KEY],)
    other_decks = [deck for deck in training if deck.commanders != exact]
    target_count = len(training) - len(other_decks)
    assert target_count == counts["target_pair_training_decks"]
    print(f"Frozen {target_count} distinct lists for {commander.COMMANDER}. Current index has {coverage['public_index_decks']} entries.",
          flush=True)
    results = {}
    for arm, examples in (("with", training), ("without", other_decks)):
        label = f"{arm}-{commander.COHORT}"
        ranker = included if arm == "with" else TypedDeckRanker(catalog, aliases, seed=args.seed)
        if ranker is not included:
            ranker.holdout = included.holdout.copy()
            if ranker.keys != included.keys or not np.array_equal(ranker.features, included.features):
                raise ValueError("The two training arms must use the same card features")
        print(f"Training {label} on {len(examples):,} decks", flush=True)
        ranker.fit(examples, epochs=args.epochs)
        ranker.rng = np.random.default_rng(197)
        evaluation = ranker.evaluate(test, limit=1200)
        params = sum(tensor.size for tensor in (*ranker.model.coefs_, *ranker.model.intercepts_))
        sample = {
            **coverage, "training_decks": len(examples), "heldout_decks": len(test),
            "target_commander_training_decks": target_count if arm == "with" else 0,
            "target_commander_eligible_decks": target_count,
            "target_duplicate_decks": counts.get("target_duplicate_decks", 0),
            "target_unavailable_previews": counts.get("target_unavailable_previews", 0),
            "target_ineligible_decks": coverage["frozen_preview_count"] - target_count
            - counts.get("target_duplicate_decks", 0) - counts.get("target_unavailable_previews", 0),
            "target_index_source_at": counts.get("target_index_source_at"),
            "parameters": params, "typed_feature_version": ranker.typed_feature_version,
        }
        results[label] = {"sample": sample, "evaluation": evaluation, "epochs": args.epochs,
                          "catalog_updated_at": args.catalog_updated_at, "seed": args.seed}
        with (args.output_directory / f"{label}.pkl").open("wb") as handle:
            pickle.dump(ranker, handle, protocol=pickle.HIGHEST_PROTOCOL)
        print(label, evaluation, flush=True)
        atomic_json(args.output_directory / "training.json", results)
    return 0


def preference(report):
    mana = report["deck"]["mana_diagnostics"]
    price = report["pricing"]
    ratio = price["lands_cents"] / price["nonlands_cents"]
    general = mana["early_colored_land_sources_adjusted"]
    preferred = mana.get("dinosaur_cast_sources_adjusted", general)
    return (report["deck"]["synergy_score"] + .010 * min(general.values())
            + .004 * min(preferred.values()) - .16 * abs(1 - ratio))


def _finalists(reports):
    ordered = sorted(reports, key=preference, reverse=True)
    covered = set()
    for report in ordered:
        key = report["deck"]["mana_diagnostics"]["land_count"]
        if key not in covered:
            covered.add(key)
            yield report
    yield from ordered


def generate(args, commander):
    arms = json.loads((args.model_directory / "training.json").read_text(encoding="utf-8"))
    if any(label not in {f"with-{commander.COHORT}", f"without-{commander.COHORT}"} for label in arms):
        raise ValueError("The saved models belong to a different commander")
    if args.arm:
        arms = {args.arm: arms[args.arm]}
    profiles = [profile for profile in commander.PROFILES if not args.land_count or profile[0] in args.land_count]
    if not profiles:
        raise ValueError("The requested land counts are outside the commander's construction profiles")
    snapshot = load_snapshot(args.prices)
    prices = {key: quote["price_cents"] for key, quote in snapshot["cards"].items()}
    args.output_directory.mkdir(parents=True, exist_ok=True)
    observed = {}
    missing_verified = []
    with (OfficialDeckChecker() if args.check else nullcontext()) as checker:
        for label, training in arms.items():
            output = args.output_directory / label
            output.mkdir(exist_ok=True)
            catalog, _ = load_catalog(args.oracle_jsonl, training["catalog_updated_at"])
            # These local weights were produced by the train command above.
            with (args.model_directory / f"{label}.pkl").open("rb") as handle:
                ranker = pickle.load(handle)
            if ranker.keys != list(catalog):
                raise ValueError("The Oracle catalog does not match the model's stored features")
            reports = {}
            for deck in commander.build_candidates(catalog, ranker.for_commanders((commander.COMMANDER_KEY,)),
                                                   prices, profiles=profiles):
                digest = deck["cardlist_sha256"]
                if digest in reports:
                    continue
                report = report_for_deck(deck, catalog, training["evaluation"], training["sample"],
                                         snapshot_at=training["catalog_updated_at"], seed=training["seed"],
                                         epochs=training["epochs"])
                report["synergy"].update(model=commander.MODEL_DESCRIPTION, highlights=commander.game_plan(deck),
                                         summary=commander.SUMMARY)
                report["pricing"], shopping = price_deck(deck, snapshot)
                if report["pricing"]["total_cents"] is not None:
                    reports[digest] = report
                    atomic_json(output / f"{digest}-shopping.json", shopping)
            eligible = []
            checked = set()
            print(f"{label}: built {len(reports)} unique, fully priced lists", flush=True)
            for digest, report in reports.items():
                report["synergy"]["evaluated_decks"] = len(reports)
                atomic_json(output / f"{digest}.json", report)
            for report in _finalists(reports.values()) if args.check else ():
                digest = report["deck"]["deck_sha256"]
                if digest in checked:
                    continue
                checked.add(digest)
                if digest in observed:
                    report["external_rating"] = observed[digest]
                else:
                    verify_report(report, validation_directory=args.output_directory / "edhpowerlevel",
                                  timeout_seconds=45, checker=checker)
                    observed[digest] = report["external_rating"]
                atomic_json(output / f"{digest}.json", report)
                rating = report["external_rating"]
                print(label, digest[:12], report["pricing"]["lands_cents"], report["pricing"]["nonlands_cents"],
                      report["deck"]["mana_diagnostics"]["land_count"], rating.get("playability_pct"),
                      rating.get("bracket"), flush=True)
                if playability_status(rating) == "within":
                    eligible.append(report)
                if len(checked) >= 5 and eligible:
                    break
            if eligible:
                winner = max(eligible, key=preference)
                atomic_json(output / "selected.json", winner)
                (output / "selected.txt").write_text(winner["deck"]["export_text"], encoding="utf-8")
                print(f"{label}: selected {winner['deck']['deck_sha256']}", flush=True)
            elif args.check:
                missing_verified.append(label)
    if missing_verified:
        print("No finalist in the playability window for " + ", ".join(missing_verified), flush=True)
    return int(bool(missing_verified))


def main(*, default_commander=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commander", choices=tuple(_COMMANDERS), default=default_commander, required=default_commander is None)
    commands = parser.add_subparsers(dest="command", required=True)
    learner = commands.add_parser("train", help="Train matched models with and without the commander's lists")
    learner.add_argument("--oracle-jsonl", type=Path, required=True)
    learner.add_argument("--catalog-updated-at", required=True)
    learner.add_argument("--alias-cache", type=Path, required=True)
    learner.add_argument("--previews", type=Path, required=True)
    learner.add_argument("--current-previews", type=Path, required=True)
    learner.add_argument("--historical-previews", type=Path, action="append", default=[])
    learner.add_argument("--output-directory", type=Path, required=True)
    learner.add_argument("--epochs", type=int, default=2)
    learner.add_argument("--seed", type=int, default=42)
    builder = commands.add_parser("generate", help="Build and compare legal priced lists for both saved rankers")
    builder.add_argument("--oracle-jsonl", type=Path, required=True)
    builder.add_argument("--prices", type=Path, required=True)
    builder.add_argument("--model-directory", type=Path, required=True)
    builder.add_argument("--output-directory", type=Path, required=True)
    builder.add_argument("--arm", help="Limit the search to one of the named training arms")
    builder.add_argument("--land-count", type=int, action="append", help="Compare these land counts with the same model")
    builder.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.command == "train" and args.epochs < 1:
        parser.error("--epochs must be positive")
    commander = _COMMANDERS[args.commander]
    return train(args, commander) if args.command == "train" else generate(args, commander)


if __name__ == "__main__":
    raise SystemExit(main())
