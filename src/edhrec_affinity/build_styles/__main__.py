"""Run with python -m edhrec_affinity.build_styles --commander <slug>."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import math

import httpx

from .analysis import analyze_decks, compare_profiles, tag_label_policy
from .collection import collect_previews, parse_sample_decks
from .eligibility import MIN_COMMANDER_DECKS, require_eligible_commander, validate_minimum
from .sources import (
    Cache, CommanderAliasCatalog, RequestLimiter, SourceBlocked, checked_identifier,
    commander_card_alias_groups, commander_printing_ids, enrich_commander_name_groups,
    import_decks, parse_profile,
)


def collect_evidence(args: argparse.Namespace, *, limiter=None, preview_transport=None, metadata_lock=None,
                     metadata_provider=None, commander_alias_provider=None) -> dict:
    minimum = validate_minimum(getattr(args, "min_total_decks", MIN_COMMANDER_DECKS))
    label_policy = tag_label_policy(args.min_tag_decks, args.main_tag_decks, args.max_tags)
    slug = checked_identifier(args.commander)
    root = args.cache_dir or Path("data/raw/build-style-prototype") / slug
    if limiter is None and args.preview_workers > 1:
        limiter = RequestLimiter(delay=max(1, args.request_delay))
    cache = Cache(root, fetch=args.fetch, delay=args.request_delay, limiter=limiter,
                  max_age_seconds=args.source_max_age_hours * 3600 if args.source_max_age_hours is not None else None,
                  preview_transport=preview_transport)
    failures = []
    try:
        url = f"https://json.edhrec.com/pages/commanders/{slug}"
        payload = cache.get("base.json", url + ".json")
        card = payload["container"]["json_dict"]["card"]
        if card.get("url") and card["url"] != f"/commanders/{slug}":
            raise ValueError("The cache contains a different commander; select a separate cache directory")
        require_eligible_commander(card.get("num_decks", payload.get("num_decks_avg")), minimum)
        # A valid commander page can contain no mature card measurements. Deck
        # analysis can still proceed, or record an honest insufficient sample.
        base = parse_profile(payload, "all", "All builds", allow_empty=True)
        base["commander_names"] = card.get("names") or [card["name"]]
        aliases = commander_alias_provider or CommanderAliasCatalog(root.parent)
        base["commander_name_groups"] = aliases.groups(card)
        # Sparse partner pages can omit the tag panel entirely. Their deck
        # index remains usable even when no tag profiles are available.
        panels = payload.get("panels", {})
        taglinks = panels.get("taglinks", []) if isinstance(panels, dict) else None
        if not isinstance(taglinks, list):
            raise ValueError("Commander taglinks must be a list when present")
        tags = sorted((tag for tag in taglinks
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
                profile = parse_profile(page, tag_slug, tag["value"])
                profile.update(reported_tag_decks=tag["count"],
                               support_tier="main" if tag["count"] >= label_policy["main_minimum_decks"] else "supporting")
                profiles.append(profile)
            except (FileNotFoundError, ValueError, KeyError, httpx.HTTPError, SourceBlocked) as error:
                failures.append({"tag": tag_slug, "error": str(error)})
                if cache.blocked:
                    break
        decks = []
        collection = {"frame_decks": None, "previews_attempted": 0, "previews_loaded": 0,
                      "sample_limit": args.sample_decks, "target_mode": "all" if args.sample_decks is None else "sample",
                      "complete": args.sample_decks == 0}
        if args.sample_decks != 0 and not cache.blocked:
            try:
                decks, collection = collect_previews(
                    cache, slug, limit=args.sample_decks, seed=args.seed,
                    progress_path=args.collection_state or root / "collection.json.gz",
                    max_seconds=args.max_preview_seconds, preview_workers=args.preview_workers,
                    index_max_age_seconds=args.index_max_age_hours * 3600 if args.index_max_age_hours is not None else None,
                    preview_max_age_seconds=args.source_max_age_hours * 3600 if args.source_max_age_hours is not None else None,
                    trust_index_versions=args.trust_index_versions,
                    freshness_target_hours=getattr(args, "freshness_target_hours", 96),
                )
                failures.extend(collection.pop("failures"))
            except (FileNotFoundError, KeyError, ValueError, httpx.HTTPError, SourceBlocked) as error:
                failures.append({"deck_index": slug, "error": str(error)})
        imported_count = 0
        if args.deck_import:
            imported, import_failures = import_decks(args.deck_import, base["commander_names"])
            imported_count = len(imported)
            decks.extend(imported)
            failures.extend(import_failures)
        ranking_profiles, metadata, metadata_summary = list(profiles), {}, {}
        if len(decks) >= 8 and not cache.blocked and (collection.get("complete") or not args.defer_partial_analysis):
            from .card_metadata import load_card_metadata
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
            alias_groups = commander_card_alias_groups(base.get("commander_name_groups", []))
            alias_options = {"commander_alias_groups": alias_groups} if alias_groups else {}
            printing_ids = commander_printing_ids(card)
            if printing_ids:
                alias_options["printing_ids"] = printing_ids
            with metadata_lock or nullcontext():
                metadata, metadata_summary = (metadata_provider.load(names, fetch=args.fetch, **alias_options) if metadata_provider else
                                               load_card_metadata(names, args.card_metadata_cache, fetch=args.fetch, **alias_options))
            base["commander_name_groups"] = enrich_commander_name_groups(base, metadata)
        return {"slug": slug, "card": card, "base": base, "profiles": profiles, "tag_label_policy": label_policy,
                "ranking_profiles": ranking_profiles, "decks": decks, "collection": collection,
                "metadata": metadata, "metadata_summary": metadata_summary,
                "failures": failures, "imported_count": imported_count,
                "blocked": cache.blocked or bool(metadata_summary.get("blocked")), "provenance": cache.provenance}
    finally:
        cache.close()


def analyze_evidence(args: argparse.Namespace, evidence: dict) -> dict:
    """CPU work over a saved input; no EDHREC or Scryfall requests in this phase."""
    slug, card, base = evidence["slug"], evidence["card"], evidence["base"]
    require_eligible_commander(base.get("deck_count"), getattr(args, "min_total_decks", MIN_COMMANDER_DECKS))
    label_policy = tag_label_policy(args.min_tag_decks, args.main_tag_decks, args.max_tags)
    profiles, decks, collection = evidence["profiles"], evidence["decks"], evidence["collection"]
    failures, imported_count = list(evidence["failures"]), evidence["imported_count"]
    ranking_profiles, metadata = evidence["ranking_profiles"], evidence["metadata"]
    metadata_summary = evidence["metadata_summary"]
    analysis = analyze_decks(decks, base, profiles, components=args.components,
                             max_components=args.max_components,
                             seed=args.seed, min_cards=args.min_deck_cards,
                             min_support=args.min_card_support,
                             membership_threshold=args.membership, min_tag_decks=args.min_tag_decks,
                             main_tag_decks=label_policy["main_minimum_decks"])
    from .deckbuilding import DECK_GENERATION_VERSION

    deck_generation = {"version": DECK_GENERATION_VERSION, "status": "insufficient_data", "metadata": {}, "ranking_pages": []}
    if analysis["status"] == "fitted":
        from .deckbuilding import generate_package_decks
        from .external_validation import attach_external_validation

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
        # Preserve the collection policy, including its absence on legacy saved
        # evidence. New options alone cannot certify profiles never collected.
        "tag_label_policy": evidence.get("tag_label_policy"),
        "collection": {**collection, "network_enabled": args.fetch,
                       "blocked": evidence["blocked"] or bool(deck_generation.get("metadata", {}).get("blocked")),
                       "sampling": ("All unique IDs in the available EDHREC deck index, collected in seeded order across resumable runs"
                                    if args.sample_decks is None else "Seeded random sample of unique EDHREC deck-index IDs"),
                       "seed": args.seed, "local_imports": imported_count,
                       "failures": failures, "provenance": evidence["provenance"]},
        "profiles": compare_profiles(base, profiles, threshold=args.similarity,
                                     min_tag_decks=args.min_tag_decks, main_tag_decks=label_policy["main_minimum_decks"]),
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
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / "report.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if not args.no_html:
        from .report import render_report

        render_report(report, args.output_dir / "index.html")
    return report


def run(args: argparse.Namespace) -> dict:
    return analyze_evidence(args, collect_evidence(args))


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
    cli.add_argument("--min-total-decks", type=validate_minimum, default=MIN_COMMANDER_DECKS,
                     help=f"Minimum EDHREC commander deck count; must be at least {MIN_COMMANDER_DECKS}")
    cli.add_argument("--min-tag-decks", type=int, default=5,
                     help="Minimum reported tag decks for a supporting label profile (default 5)")
    cli.add_argument("--main-tag-decks", type=int,
                     help="Minimum reported tag decks for a main label profile (default 15, or a higher --min-tag-decks)")
    cli.add_argument("--max-tags", type=int, default=12)
    cli.add_argument("--tags", nargs="+", help="Optional explicit tag slugs, still subject to the deck minimum")
    cli.add_argument("--sample-decks", type=parse_sample_decks, default=None,
                     help="all (default) enumerates the available index; optional count; 0 uses imports only")
    cli.add_argument("--collection-state", type=Path, help="Durable compressed preview checkpoint; defaults inside cache-dir")
    cli.add_argument("--max-preview-seconds", type=float, default=0,
                     help="Pause preview collection after this many seconds, resuming next run; 0 disables the time slice")
    cli.add_argument("--source-max-age-hours", type=float, default=None,
                     help="Refresh source pages and preview observations after this age; default preserves legacy caches")
    cli.add_argument("--index-max-age-hours", type=float, default=None)
    cli.add_argument("--freshness-target-hours", "--target-completion-hours", dest="freshness_target_hours",
                     type=float, default=96,
                     help="Source verification age threshold; separate from any catalog completion estimate")
    cli.add_argument("--preview-workers", type=int, default=1)
    cli.add_argument("--defer-partial-analysis", action="store_true", help=argparse.SUPPRESS)
    cli.add_argument("--trust-index-versions", action="store_true",
                     help="Revalidate unchanged deck versions using the current EDHREC index; retain original observation dates")
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
    try:
        tag_label_policy(args.min_tag_decks, args.main_tag_decks, args.max_tags)
    except ValueError as error:
        cli.error(str(error))
    if not 1 <= args.max_tags <= 16:
        cli.error("--max-tags must be 1–16")
    if not math.isfinite(args.max_preview_seconds) or args.max_preview_seconds < 0:
        cli.error("--max-preview-seconds must be finite and nonnegative")
    if (not 1 <= args.preview_workers <= 64 or not math.isfinite(args.freshness_target_hours)
            or args.freshness_target_hours <= 0 or any(value is not None and
                (not math.isfinite(value) or value <= 0) for value in
                (args.source_max_age_hours, args.index_max_age_hours))):
        cli.error("Use 1–64 preview workers and positive finite source-age windows")
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
