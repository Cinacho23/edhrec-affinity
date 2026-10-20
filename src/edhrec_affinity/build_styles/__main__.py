"""Run with python -m edhrec_affinity.build_styles --commander <slug>."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random

import httpx

from .analysis import analyze_decks, compare_profiles
from .sources import Cache, SourceBlocked, checked_identifier, import_decks, parse_preview, parse_profile


def run(args: argparse.Namespace) -> dict:
    slug = checked_identifier(args.commander)
    root = args.cache_dir or Path("data/raw/build-style-prototype") / slug
    cache = Cache(root, fetch=args.fetch, delay=args.request_delay)
    failures = []
    try:
        url = f"https://json.edhrec.com/pages/commanders/{slug}"
        payload = cache.get("base.json", url + ".json")
        card = payload["container"]["json_dict"]["card"]
        if card.get("url") and card["url"] != f"/commanders/{slug}":
            raise ValueError("The cache contains a different commander; select a separate cache directory")
        # A valid commander page can contain no mature card measurements. Deck
        # analysis can still proceed, or record an honest insufficient sample.
        base = parse_profile(payload, "all", "All builds", allow_empty=True)
        base["commander_names"] = card.get("names") or [card["name"]]
        tags = sorted((tag for tag in payload["panels"]["taglinks"]
                       if tag["count"] >= args.min_tag_decks), key=lambda tag: (-tag["count"], tag["slug"]))
        if args.tags:
            requested = {checked_identifier(tag) for tag in args.tags}
            tags = [tag for tag in tags if tag["slug"] in requested]
            missing = requested - {tag["slug"] for tag in tags}
            if missing:
                raise ValueError(f"Requested tags not reported or below --min-tag-decks: {sorted(missing)}")
        tags = tags[:args.max_tags]
        profiles = []
        for tag in tags:
            tag_slug = checked_identifier(tag["slug"])
            try:
                page = cache.get(tag_slug + ".json", f"{url}/{tag_slug}.json")
                profiles.append(parse_profile(page, tag_slug, tag["value"]))
            except (FileNotFoundError, ValueError, KeyError, httpx.HTTPError, SourceBlocked) as error:
                failures.append({"tag": tag_slug, "error": str(error)})
                if cache.blocked:
                    break
        decks, frame_count, attempted = [], None, 0
        if args.sample_decks and not cache.blocked:
            try:
                table = cache.get("decks.json", f"https://json.edhrec.com/pages/decks/{slug}.json")["table"]
                # Sampling occurs before inspecting card lists or deck tags.
                rows = {row["urlhash"]: row for row in table}
                frame_count = len(rows)
                sample = random.Random(args.seed).sample(list(rows.values()), min(args.sample_decks, len(rows)))
                for row in sample:
                    identifier = checked_identifier(row["urlhash"], deck=True)
                    attempted += 1
                    try:
                        data = cache.get(f"previews/{identifier}.html", f"https://edhrec.com/deckpreview/{identifier}", html=True)
                        deck = parse_preview(data, identifier)
                        deck["source_bracket"] = row.get("bracket")
                        decks.append(deck)
                    except (FileNotFoundError, ValueError, KeyError, TypeError, httpx.HTTPError, SourceBlocked) as error:
                        failures.append({"deck": identifier, "error": str(error)})
                        if cache.blocked:
                            break
                    if args.fetch and attempted % 10 == 0:
                        print(f"Read {attempted}/{len(sample)} sampled deck previews", flush=True)
            except (FileNotFoundError, KeyError, ValueError, httpx.HTTPError, SourceBlocked) as error:
                failures.append({"deck_index": slug, "error": str(error)})
        imported_count = 0
        if args.deck_import:
            imported, import_failures = import_decks(args.deck_import, base["commander_names"])
            imported_count = len(imported)
            decks.extend(imported)
            failures.extend(import_failures)
        analysis = analyze_decks(decks, base, profiles, components=args.components,
                                 max_components=args.max_components,
                                 seed=args.seed, min_cards=args.min_deck_cards,
                                 min_support=args.min_card_support,
                                 membership_threshold=args.membership)
        deck_generation = {"version": 1, "status": "insufficient_data", "metadata": {}, "ranking_pages": []}
        if analysis["status"] == "fitted":
            from .card_metadata import load_card_metadata
            from .deckbuilding import generate_package_decks
            from .external_validation import attach_external_validation

            # Bracket pages contribute only to deck ranking, never NMF fitting or
            # theme discovery. The unfiltered commander page is not a ranking page.
            bracket_paths = {1: "exhibition", 2: "core", 3: "upgraded", 4: "optimized", 5: "cedh"}
            brackets = {deck.get("source_bracket") for deck in decks
                        if type(deck.get("source_bracket")) is int and deck["source_bracket"] in bracket_paths}
            brackets.add(3)  # Explicit default guidance for samples with no reported bracket.
            ranking_profiles = list(profiles)
            for bracket in sorted(brackets):
                path = bracket_paths[bracket]
                try:
                    page = cache.get(f"bracket-{bracket}.json", f"{url}/{path}.json")
                    profile = parse_profile(page, f"bracket-{bracket}", f"Bracket {bracket}", allow_empty=True)
                    profile.update(bracket=bracket, source_url=f"https://edhrec.com/commanders/{slug}/{path}")
                    ranking_profiles.append(profile)
                except (FileNotFoundError, ValueError, KeyError, httpx.HTTPError, SourceBlocked) as error:
                    failures.append({"ranking_bracket": bracket, "error": str(error)})
                    if cache.blocked:
                        break
            names = [*base["commander_names"], *[card["name"] for deck in decks for card in deck["cards"]]]
            metadata, metadata_summary = load_card_metadata(names, args.card_metadata_cache, fetch=args.fetch)
            generate_package_decks(analysis, decks, base, ranking_profiles, metadata, prefer_playability=True)
            packages = [*analysis["packages"], *[pkg for model in analysis.get("alternative_models", []) for pkg in model["packages"]]]
            processed_decks = set()
            for package in packages:
                generated = package.get("generated_deck", {})
                if generated.get("status") != "generated" or id(generated) in processed_decks:
                    continue
                processed_decks.add(id(generated))
                validation = generated["validation"]
                validation["metadata"] = {key: metadata_summary.get(key) for key in
                                          ("checked_at", "oldest_checked_at", "stale_cached", "cache_max_age_days")}
                validation["checks"]["metadata_fresh"] = not bool(metadata_summary.get("stale_cached"))
                if metadata_summary.get("stale_cached"):
                    validation["valid"] = False
                    validation.setdefault("errors", []).append("Some card rules use an older cache; refresh metadata before relying on current legality.")
                rating = attach_external_validation(generated)
                if rating.get("status") in {"verified", "stale"}:
                    generated["primer"] += (
                        f"\n\nExternal observation\nEDHPowerLevel measured {rating['playability_pct']:.1f}% playability "
                        f"and bracket {rating['bracket']} on {rating['checked_at']}. "
                        "This observation belongs to this exact card list; it is separate from the source-based bracket estimate.\n"
                    )
            generation_status = "complete"
            if any(pkg.get("generated_deck", {}).get("status") != "generated" for pkg in packages):
                generation_status = "partial" if metadata_summary.get("missing") or metadata_summary.get("stale_cached") else "unavailable"
            elif metadata_summary.get("stale_cached"):
                generation_status = "partial"
            deck_generation.update(
                status=generation_status,
                metadata=metadata_summary,
                ranking_pages=[{"tag_slug": p["tag_slug"], "tag_name": p["tag_name"],
                                "source_url": p.get("source_url", f"https://edhrec.com/commanders/{slug}/{p['tag_slug']}")}
                               for p in ranking_profiles],
            )
        report = {
            "schema_version": 1, "prototype_version": 3,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "commander_name": card["name"], "commander_slug": slug,
            "total_edhrec_decks": base["deck_count"],
            "collection": {"network_enabled": args.fetch,
                           "blocked": cache.blocked or bool(deck_generation.get("metadata", {}).get("blocked")),
                           "sampling": "Seeded simple random sample of unique EDHREC deck-index IDs",
                           "seed": args.seed, "frame_decks": frame_count, "previews_attempted": attempted,
                           "previews_loaded": len(decks) - imported_count, "local_imports": imported_count,
                           "failures": failures, "provenance": cache.provenance},
            "profiles": compare_profiles(base, profiles, threshold=args.similarity),
            "deck_analysis": analysis, "deck_generation": deck_generation,
            "limits": [
                "Tag profile similarity does not measure shared decks or imply compatible strategies.",
                "EDHREC card lists are truncated: omitted cards are unknown, never observed zeros.",
                "Only cards with eligible-deck denominators at least 90% of the page deck count are compared.",
                "Profiles use a fixed 20-deck regularizer and 1 percentage point enrichment floor; these are pilot heuristics.",
                "NMF learns cards without source tags. Automatic package counts are selected conservatively from data-supported candidates; explicit counts are exploratory alternatives.",
                "Package memberships and suggested tag labels are exploratory scores, not probabilities.",
                "Incomplete lists, exact copies and wrong commanders are excluded; near copies can remain.",
                "Sample composition, budget, bracket and deck age may explain packages alongside strategy.",
                "Imported text may contain unrecognized nonbasic lands unless EDHREC lists them as lands.",
                "Results describe the successfully loaded sample, not every deck on EDHREC or its source sites.",
                "Generated decks use NMF-weighted sample consensus and rule-based structural constraints; functional buckets are Oracle-text heuristics, not learned labels.",
                "Deck ranking averages only selected theme/bracket observations, with missing cards contributing zero as requested. Unlisted cards are still unknown observations in the statistical profile model.",
                "Generated deck bracket guidance is an estimate; combo speed and external EDHPowerLevel playability are not certified by structural legality checks.",
            ],
        }
    finally:
        cache.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / "report.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if not args.no_html:
        from .report import render_report

        render_report(report, args.output_dir / "index.html")
    return report


def parse_components(value: str) -> int | None:
    if value.casefold() == "auto":
        return None
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use auto or a positive integer package count") from error
    if number < 1:
        raise argparse.ArgumentTypeError("Package count must be at least 1")
    return number


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Experimental EDHREC tag-profile and unlabeled-deck build discovery")
    cli.add_argument("--commander", required=True, help="EDHREC commander slug")
    cli.add_argument("--fetch", action="store_true", help="Collect missing public EDHREC pages; default is cache only")
    cli.add_argument("--cache-dir", type=Path)
    cli.add_argument("--card-metadata-cache", type=Path, default=Path("data/raw/build-style-card-metadata.json"),
                     help="Shared Scryfall metadata cache for generated package decks")
    cli.add_argument("--output-dir", type=Path, default=Path("data/prototypes/build-styles"))
    cli.add_argument("--no-html", action="store_true", help="Write JSON only for static-site data generation")
    cli.add_argument("--deck-import", type=Path, help="Folder of local .txt or .json exports; source tags not required")
    cli.add_argument("--min-tag-decks", type=int, default=25)
    cli.add_argument("--max-tags", type=int, default=12)
    cli.add_argument("--tags", nargs="+", help="Optional explicit tag slugs, still subject to the deck minimum")
    cli.add_argument("--sample-decks", type=int, default=60, help="At most this many previews attempted; 0 uses imports only")
    cli.add_argument("--components", type=parse_components, default=None,
                     help="auto (default) selects a supported package count; or specify a positive integer")
    cli.add_argument("--max-components", type=int, default=8,
                     help="Upper search bound for automatic package selection; the data can impose a smaller limit")
    cli.add_argument("--seed", type=int, default=42)
    cli.add_argument("--min-deck-cards", type=int, default=90)
    cli.add_argument("--min-card-support", type=int, default=2)
    cli.add_argument("--membership", type=float, default=0.25)
    cli.add_argument("--similarity", type=float, default=0.6)
    cli.add_argument("--request-delay", type=float, default=1.0, help="Seconds between request starts; minimum 1")
    return cli


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    if not 1 <= args.max_tags <= 16 or not 0 <= args.sample_decks <= 200:
        cli.error("--max-tags must be 1–16 and --sample-decks must be 0–200 for this pilot")
    if (not 1 <= args.max_components <= 32 or
        args.components is not None and not 1 <= args.components <= 32 or
        args.min_tag_decks < 1 or args.min_card_support < 2):
        cli.error("Use 1–32 maximum/components, a positive tag minimum, and card support of at least 2")
    if not 0 < args.membership <= 1 or not 0 <= args.similarity <= 1 or not 1 <= args.min_deck_cards <= 100:
        cli.error("Invalid similarity, membership, or minimum deck size")
    try:
        report = run(args)
    except (ValueError, KeyError, OSError, RuntimeError, httpx.HTTPError) as error:
        cli.exit(1, f"Prototype failed: {error}\n")
    print(json.dumps({"report": str((args.output_dir / ("report.json" if args.no_html else "index.html")).resolve()),
                      "profiles": len(report["profiles"]["profiles"]),
                      "deck_status": report["deck_analysis"]["status"],
                      "package_count": report["deck_analysis"].get("package_count", 0),
                      "unique_decks": report["deck_analysis"]["unique_decks"],
                      "failures": len(report["collection"]["failures"])}, indent=2))


if __name__ == "__main__":
    main()
