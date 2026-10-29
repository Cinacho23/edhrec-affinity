#!/usr/bin/env python3
"""Rebuild every saved package deck without collecting decks or refitting NMF.

The input is a restored collector artifact's ``build-styles`` directory, not
the smaller website export. Completed collectors retain parsed previews and
source profiles after discarding their temporary analysis evidence. This tool
uses either representation, checks that it matches the published model's
dataset, and leaves source freshness and learned package membership unchanged.
It is offline by default; ``--fetch-metadata`` refreshes Scryfall rules and power inputs in
one paced stream before starting deck-generation workers. A finite runtime
also bounds in-flight CPU and metadata work, retaining interrupted reports
for the next run.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, TimeoutError, wait
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
from pathlib import Path
import tempfile
import time

from edhrec_affinity.build_styles.card_metadata import (
    CACHE_MAX_AGE_DAYS, COLLECTION_BATCH_SIZE, COLLECTION_URL, MetadataRepository, metadata_aliases,
)
from edhrec_affinity.build_styles.collection import deck_dataset_sha256
from edhrec_affinity.build_styles.deckbuilding import DECK_GENERATION_VERSION, generate_package_decks
from edhrec_affinity.build_styles.eligibility import commander_is_eligible
from edhrec_affinity.build_styles.external_validation import (
    attach_external_validation, cardlist_hash, external_validation_directory,
)
from edhrec_affinity.build_styles.power_brackets import (
    POWER_SCORE_BRACKET_POLICY_VERSION, bracket_for_power_score, verified_score_bracket,
)
from edhrec_affinity.build_styles.power_scoring import ALGORITHM_SHA256, POWER_FORMULA_VERSION
from edhrec_affinity.build_styles.pipeline import shutdown_executor
from edhrec_affinity.build_styles.sources import (
    CommanderAliasCatalog, card_key, checked_identifier, commander_card_alias_groups,
    commander_printing_ids, enrich_commander_name_groups, parse_profile,
)


GENERATION_VERSION = DECK_GENERATION_VERSION
_worker_repository = None
_worker_aliases = None


class RebuildBlocked(ValueError):
    """Saved inputs cannot safely regenerate this learned model's decks."""


def read_json(path: Path) -> dict:
    def reject(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        result = json.load(handle, parse_constant=reject)
    json.dumps(result, allow_nan=False)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return result


def atomic_json(path: Path, value: dict) -> None:
    """Replace only a complete, flushed JSON file; never truncate a report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def packages(report: dict):
    analysis = report.get("deck_analysis", {})
    for package in analysis.get("packages", []):
        yield "auto", package
    for model in analysis.get("alternative_models", []):
        for package in model.get("packages", []):
            yield f"{model.get('components')}-packages", package


def score_repair_observations(report: dict, validation_directory: Path | None = None) -> list[dict]:
    """Identify new repair work from canonical current exact-list observations.

    Saved reports can contain an old ``verified`` label. Reload each rating so
    an expired, replaced or invalid observation never triggers a fresh repair.
    Only a small identity record is retained, bounded by the report's packages.
    """
    observations = []
    for model, package in packages(report):
        deck = package.get("generated_deck", {})
        if deck.get("status") != "generated":
            continue
        checked_deck = {"cards": deck.get("cards")}
        if "card_count" in deck:
            checked_deck["card_count"] = deck["card_count"]
        rating = attach_external_validation(checked_deck, validation_directory) if validation_directory is not None else attach_external_validation(checked_deck)
        target = verified_score_bracket(rating)
        native_bracket = deck.get("validation", {}).get("bracket", {}).get("recommended", 0)
        native_bracket = native_bracket if type(native_bracket) is int and 1 <= native_bracket <= 5 else 0
        current_bracket = max(rating.get("bracket", 0), native_bracket)
        if target is None or target >= current_bracket:
            continue
        evidence = {key: rating[key] for key in
                    ("deck_sha256", "checked_at", "power_level", "bracket", "score_bracket")}
        evidence.update(bracket_restrictions=rating.get("bracket_restrictions", {}),
                        native_bracket=native_bracket, policy_version=POWER_SCORE_BRACKET_POLICY_VERSION)
        fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False,
                                                allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        observations.append({"model": model, "package_id": package.get("id"),
                             "deck_sha256": rating["deck_sha256"], "observation_sha256": fingerprint,
                             "policy_version": POWER_SCORE_BRACKET_POLICY_VERSION})
    return observations


def _expired_timestamp(value, now: datetime, max_age_days: float) -> bool:
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return checked.tzinfo is None or not 0 <= (now - checked).total_seconds() < max_age_days * 86400
    except (ValueError, AttributeError, TypeError):
        return True


def needs_rebuild(report: dict, *, validation_directory: Path | None = None,
                  max_age_days: float = 3) -> bool:
    """Retry old generations, expired inputs or a newly checked score mismatch.

    A saved ``metadata_fresh`` flag is only a fact about its build time. Check
    actual observation dates on every selection, including already-v4 reports.
    Explicit unavailable calculations remain honest completed attempts while
    their metadata is fresh; they are retried when that metadata expires.
    """
    analysis = report.get("deck_analysis", {})
    if not (commander_is_eligible(report.get("total_edhrec_decks"))
            and analysis.get("status") == "fitted" and analysis.get("packages")):
        return False
    if report.get("deck_generation", {}).get("version", 0) < GENERATION_VERSION:
        return True
    if any(package.get("generated_deck", {}).get("status") == "generated"
           and package["generated_deck"].get("validation", {}).get("checks", {}).get("metadata_fresh") is False
           for _, package in packages(report)):
        return True
    now = datetime.now(timezone.utc)
    for _, package in packages(report):
        deck = package.get("generated_deck", {})
        if deck.get("status") != "generated":
            continue
        validation = deck.get("validation", {})
        calculated = validation.get("calculated_power")
        if (not isinstance(calculated, dict) or calculated.get("status") not in {"calculated", "unavailable"}
                or calculated.get("source") != "local_public_formula"
                or calculated.get("power_formula_version") != POWER_FORMULA_VERSION
                or calculated.get("algorithm_sha256") != ALGORITHM_SHA256):
            return True
        try:
            if calculated.get("deck_sha256") != cardlist_hash(deck.get("cards", [])):
                return True
        except (ValueError, TypeError):
            return True
        metadata = validation.get("metadata", {})
        observed = metadata.get("oldest_checked_at") or calculated.get("metadata_checked_at") or calculated.get("checked_at")
        if _expired_timestamp(observed, now, max_age_days):
            return True
        if calculated["status"] == "calculated":
            score = calculated.get("power_level")
            digest = calculated.get("input_sha256")
            if (isinstance(score, bool) or not isinstance(score, (int, float))
                    or bracket_for_power_score(score) != calculated.get("score_bracket")
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                    or _expired_timestamp(calculated.get("checked_at"), now, CACHE_MAX_AGE_DAYS)):
                return True
    handled = report.get("deck_generation", {}).get("score_repair_observations", [])
    if not isinstance(handled, list):
        handled = []
    return any(observation not in handled for observation in score_repair_observations(report, validation_directory))


def current_discovery_slugs(root: Path, reports: dict[str, dict]) -> set[str]:
    """Use exactly the website export's existing current-collection policy."""
    source = Path(__file__).with_name("export_full_site_data.py")
    spec = importlib.util.spec_from_file_location("build_deck_discovery_policy", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.current_build_discovery_slugs(root, reports, module.read_build_discovery_policy())


def requested_metadata_names(report: dict, inputs: dict) -> list[str]:
    return [*inputs["base"].get("commander_names", []),
            *[card["name"] for _, package in packages(report)
              if package.get("generated_deck", {}).get("status") == "generated"
              for card in package["generated_deck"].get("cards", [])],
            *[row["name"] for _, package in packages(report) for row in package.get("top_cards", [])],
            *[card["name"] for deck in inputs["decks"] for card in deck["cards"]]]


def learned_analysis(analysis: dict) -> dict:
    """Fields which deck regeneration is not allowed to change."""
    result = deepcopy(analysis)
    result.pop("deck_generation", None)
    for package in result.get("packages", []):
        package.pop("generated_deck", None)
    for model in result.get("alternative_models", []):
        for package in model.get("packages", []):
            package.pop("generated_deck", None)
    return result


def _expected_dataset(report: dict) -> str | None:
    collection = report.get("collection", {})
    # During a refresh collection.dataset_sha256 can already describe new data
    # while the saved NMF still belongs to analysis_dataset_sha256.
    return collection.get("analysis_dataset_sha256") or collection.get("dataset_sha256")


def _check_decks(report: dict, decks: list[dict]) -> str:
    if not isinstance(decks, list) or not decks:
        raise RebuildBlocked("No saved parsed deck lists are available")
    identifiers = [deck.get("id") for deck in decks]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise RebuildBlocked("Saved parsed deck IDs are invalid or duplicated")
    known = set(identifiers)
    analysis = report["deck_analysis"]
    required = {row["id"] for row in analysis.get("decks", [])}
    required.update(row["id"] for model in analysis.get("alternative_models", []) for row in model.get("memberships", []))
    if not required or not required <= known:
        raise RebuildBlocked(f"Saved lists are missing {len(required - known)} learned deck memberships")
    actual = deck_dataset_sha256(decks)
    expected = _expected_dataset(report)
    if expected and actual != expected:
        raise RebuildBlocked("Saved previews belong to a different dataset than the learned packages")
    if not expected:
        # Legacy samples lack a dataset hash. Do not silently expand their
        # source pool with later collection work.
        count = report.get("collection", {}).get("previews_loaded", analysis.get("input_decks"))
        if type(count) is not int or count != len(decks):
            raise RebuildBlocked("Legacy report has no dataset hash and saved input count does not match")
    return actual


def load_inputs(root: Path, report: dict, aliases: CommanderAliasCatalog) -> dict:
    slug = checked_identifier(report["commander_slug"])
    evidence_path = root / "_collection" / "evidence" / f"{slug}.json.gz"
    if evidence_path.exists():
        evidence = read_json(evidence_path)
        if evidence.get("slug") != slug:
            raise RebuildBlocked("Saved evidence contains a different commander")
        digest = _check_decks(report, evidence.get("decks", []))
        return {"base": deepcopy(evidence["base"]), "profiles": evidence["ranking_profiles"],
                "decks": evidence["decks"], "metadata": evidence.get("metadata", {}),
                "dataset_sha256": digest, "source": "saved_analysis_evidence",
                "printing_ids": commander_printing_ids(evidence.get("card", {}))}

    preview_path = root / "_collection" / "previews" / f"{slug}.json.gz"
    source_root = root / "_collection" / "sources" / slug
    base_path = source_root / "base.json"
    if not preview_path.exists() or not base_path.exists():
        raise RebuildBlocked("Collector parsed previews and base profile are required; website reports alone cannot regenerate decks")
    state = read_json(preview_path)
    if state.get("schema_version") != 1 or state.get("commander_slug") != slug or not isinstance(state.get("results"), dict):
        raise RebuildBlocked("Parsed-preview checkpoint identity or schema mismatch")
    frame = state.get("frame")
    if not isinstance(frame, list):
        raise RebuildBlocked("Parsed-preview checkpoint has no source frame")
    collection = report.get("collection", {})
    limit = collection.get("sample_limit")
    if type(limit) is int:
        frame = frame[:limit]
    elif collection.get("target_mode") == "sample":
        count = collection.get("target_decks", collection.get("previews_attempted"))
        if type(count) is not int:
            raise RebuildBlocked("Sample report has no saved target size")
        frame = frame[:count]
    decks = [state["results"][row["urlhash"]]["deck"] for row in frame
             if "deck" in state["results"].get(row.get("urlhash"), {})]
    digest = _check_decks(report, decks)
    payload = read_json(base_path)
    card = payload["container"]["json_dict"]["card"]
    if card.get("url") and card["url"] != f"/commanders/{slug}":
        raise RebuildBlocked("Saved base profile contains a different commander")
    base = parse_profile(payload, "all", "All builds", allow_empty=True)
    base["commander_names"] = card.get("names") or [card["name"]]
    base["commander_name_groups"] = aliases.groups(card)
    # Reuse exactly the ranking pages selected for the old generated decks,
    # never every incidental JSON file in the source directory.
    selected = report.get("deck_generation", {}).get("ranking_pages")
    if not isinstance(selected, list):
        raise RebuildBlocked("Report does not identify its original theme/bracket ranking pages")
    profiles = []
    for page in selected:
        tag = checked_identifier(page["tag_slug"])
        path = source_root / f"{tag}.json"
        if not path.exists():
            raise RebuildBlocked(f"Saved ranking page is missing: {tag}")
        profile = parse_profile(read_json(path), tag, page.get("tag_name", tag), allow_empty=True)
        if tag.startswith("bracket-") and tag.removeprefix("bracket-") in {"1", "2", "3", "4", "5"}:
            profile["bracket"] = int(tag.removeprefix("bracket-"))
        if page.get("source_url"):
            profile["source_url"] = page["source_url"]
        profiles.append(profile)
    return {"base": base, "profiles": profiles, "decks": decks, "metadata": {},
            "dataset_sha256": digest, "source": "saved_source_profiles_and_parsed_previews",
            "printing_ids": commander_printing_ids(card)}


def _coverage(package: dict, metadata: dict) -> dict:
    deck = package.get("generated_deck", {})
    keys = {card_key(row["name"]) for row in deck.get("cards", [])}
    included, omitted = [], []
    for row in package.get("top_cards", []):
        name = row["name"]
        card = metadata.get(card_key(name))
        aliases = metadata_aliases(card) if card else {card_key(name)}
        (included if aliases & keys else omitted).append(name)
    result = {"distinctive_count": len(included) + len(omitted), "included_count": len(included),
              "omitted_count": len(omitted), "included": included, "omitted": omitted}
    if isinstance(deck.get("distinctive_cards"), dict):
        result["explanations"] = deck["distinctive_cards"].get("omitted", [])
    return result


def rebuild_report(report: dict, inputs: dict, repository: MetadataRepository, *,
                   validation_directory: Path | None = None, prefer_playability: bool = True,
                   rebuilt_at: str | None = None) -> tuple[dict, dict]:
    """Regenerate primary and exploratory decks, keeping NMF/source facts intact."""
    old_analysis = learned_analysis(report["deck_analysis"])
    base, decks = deepcopy(inputs["base"]), inputs["decks"]
    existing_names = [card["name"] for _, package in packages(report)
                      if package.get("generated_deck", {}).get("status") == "generated"
                      for card in package["generated_deck"].get("cards", [])]
    names = requested_metadata_names(report, inputs)
    options = {"commander_alias_groups": commander_card_alias_groups(base.get("commander_name_groups", []))}
    metadata, summary = repository.load(names, fetch=False, **options)
    # Durable analysis evidence already contains authoritative metadata. Cached
    # observations override it; absent cache entries can retain the observation
    # saved with that exact input, including its original checked_at.
    metadata = {**inputs.get("metadata", {}), **metadata}
    base["commander_name_groups"] = enrich_commander_name_groups(base, metadata)
    requested = {card_key(name): name for name in names}
    summary["missing"] = sorted(name for key, name in requested.items() if key not in metadata)
    summary["resolved"] = len(requested) - len(summary["missing"])
    required = {card_key(name): name for name in [*base.get("commander_names", []), *existing_names]}
    required_missing = sorted(name for key, name in required.items() if key not in metadata)
    if required_missing:
        raise RebuildBlocked(f"Verified metadata is missing for {len(required_missing)} commander/existing-deck cards: "
                             + ", ".join(required_missing[:12]))
    now = datetime.now(timezone.utc)
    ages = []
    for key in requested.keys() & metadata.keys():
        checked = metadata[key].get("checked_at")
        try:
            observed = datetime.fromisoformat(checked.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("Naive metadata time")
            ages.append((requested[key], observed))
        except (ValueError, AttributeError, TypeError):
            ages.append((requested[key], datetime.min.replace(tzinfo=timezone.utc)))
    summary["stale_names"] = sorted(name for name, observed in ages
                                    if not 0 <= (now - observed).total_seconds() < repository.max_age_days * 86400)
    summary["stale_cached"] = len(summary["stale_names"])
    summary["oldest_checked_at"] = min((observed for _, observed in ages), default=None)
    if summary["oldest_checked_at"] is not None:
        summary["oldest_checked_at"] = summary["oldest_checked_at"].isoformat()
    observed_by_name = {card_key(name): observed for name, observed in ages}
    observed_by_name.update({card_key(metadata[card_key(name)]["name"]): observed for name, observed in ages})
    output = deepcopy(report)
    attempted_observations = score_repair_observations(report, validation_directory)
    before = {(model, package["id"]): package for model, package in packages(report)}
    with external_validation_directory(validation_directory):
        generate_package_decks(output["deck_analysis"], decks, base, inputs["profiles"], metadata,
                               prefer_playability=prefer_playability)
    summaries = []
    seen = set()
    for model, package in packages(output):
        previous = before[model, package["id"]]
        generated = package.get("generated_deck", {})
        if previous.get("generated_deck", {}).get("status") == "generated" and generated.get("status") != "generated":
            raise RebuildBlocked(f"Regeneration would remove an existing deck: {model}, package {package['id'] + 1}: {generated.get('reason')}")
        if generated.get("status") == "generated" and id(generated) not in seen:
            seen.add(id(generated))
            used = {card_key(row["name"]): row["name"] for row in generated["cards"]}
            stale_used = sorted(name for key, name in used.items() if key not in observed_by_name
                                or not 0 <= (now - observed_by_name[key]).total_seconds() < repository.max_age_days * 86400)
            if stale_used:
                raise RebuildBlocked(f"Current verified metadata is required for {len(stale_used)} generated-deck cards: "
                                     + ", ".join(stale_used[:12]))
            validation = generated.setdefault("validation", {})
            validation["metadata"] = {key: summary.get(key) for key in
                                      ("checked_at", "oldest_checked_at", "stale_cached", "cache_max_age_days")}
            validation["metadata"]["stale_cached"] = 0
            validation["metadata"]["oldest_checked_at"] = min(observed_by_name[key] for key in used).isoformat()
            validation.setdefault("checks", {})["metadata_fresh"] = True
            rating = attach_external_validation(generated, validation_directory) if validation_directory is not None else attach_external_validation(generated)
            if rating.get("status") in {"verified", "stale"}:
                generated["primer"] += (
                    f"\n\nExternal observation\nEDHPowerLevel measured {rating['playability_pct']:.1f}% playability "
                    f"and bracket {rating['bracket']} on {rating['checked_at']}. "
                    "This observation belongs to this exact card list; it is separate from the source-based bracket estimate.\n"
                )
        previous_deck = previous.get("generated_deck", {})
        changed = (cardlist_hash(previous_deck["cards"]) if previous_deck.get("status") == "generated" else None) != (
            cardlist_hash(generated["cards"]) if generated.get("status") == "generated" else None)
        summaries.append({"model": model, "package_id": package["id"], "status_before": previous_deck.get("status"),
                          "status_after": generated.get("status"), "list_changed": changed,
                          "before": _coverage(previous, metadata), "after": _coverage(package, metadata)})
    if learned_analysis(output["deck_analysis"]) != old_analysis:
        raise RebuildBlocked("Deck generation unexpectedly changed learned package fields")
    unavailable = any(package.get("generated_deck", {}).get("status") != "generated" for _, package in packages(output))
    generation = output.setdefault("deck_generation", {})
    # A blocked attempt is a completed review of that exact observation. New
    # timestamps, scores, restrictions or policy still request another pass.
    # Replacing rather than accumulating this list keeps the audit bounded.
    for observation in score_repair_observations(output, validation_directory):
        if observation not in attempted_observations:
            attempted_observations.append(observation)
    generation.update(version=GENERATION_VERSION, status=("partial" if summary["missing"] else "unavailable")
                      if unavailable else "complete", metadata=summary,
                      rebuilt_at=rebuilt_at or now.isoformat(), refitted=False,
                      input_dataset_sha256=inputs["dataset_sha256"], input_source=inputs["source"],
                      score_repair_observations=attempted_observations)
    return output, {"commander_slug": report["commander_slug"], "status": "rebuilt", "packages": summaries,
                    "metadata_missing": summary["missing"], "metadata_stale": summary["stale_cached"]}


def initialize_worker(root: str, cache: str, max_age_days: float) -> None:
    global _worker_repository, _worker_aliases
    _worker_repository = MetadataRepository(cache, max_age_days=max_age_days)
    _worker_aliases = CommanderAliasCatalog(Path(root) / "_collection" / "sources")


def prefetch_metadata(root: Path, paths: list[Path], repository: MetadataRepository, summary: dict, *,
                      only_outdated: bool, time_remains, checkpoint,
                      validation_directory: Path | None = None) -> None:
    """Warm one shared cache in the parent, never competing worker HTTP streams.

    Names are deduplicated across reports, including unresolved names, so an
    unsupported unused card is attempted once in this run. Current reports with
    fresh inputs are skipped, without adding a negative metadata cache.
    The existing repository supplies exact lookup proofs, pacing, and its
    global 401/403/429 access stop.
    """
    progress = summary["metadata_prefetch"]
    progress.update(status="reading_saved_inputs", source_url=COLLECTION_URL,
                    requested=0, processed=0, requests=0, fetched=0, resolved=0,
                    missing=[], stale_names=[], failures=[], input_blockers=[], blocked=False,
                    alias_resolutions=[])
    aliases = CommanderAliasCatalog(root / "_collection/sources")
    names, groups, printing_ids = {}, [], {}
    for path in paths:
        if not time_remains():
            progress["status"] = "time_budget_reached"
            checkpoint()
            return
        try:
            report = read_json(path)
            if (not commander_is_eligible(report.get("total_edhrec_decks"))
                    or report.get("deck_analysis", {}).get("status") != "fitted"
                    or not report.get("deck_analysis", {}).get("packages")
                    or only_outdated and not needs_rebuild(report, validation_directory=validation_directory,
                                                          max_age_days=getattr(repository, "max_age_days", 3))):
                continue
            inputs = load_inputs(root, report, aliases)
            names.update({card_key(name): name for name in requested_metadata_names(report, inputs)})
            for group in commander_card_alias_groups(inputs["base"].get("commander_name_groups", [])):
                if group not in groups:
                    groups.append(group)
            for name, identifier in inputs.get("printing_ids", {}).items():
                printing_ids.setdefault(name, identifier)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            progress["input_blockers"].append({"commander_slug": path.stem, "reason": str(error)})
    requested = [names[key] for key in sorted(names)]
    progress.update(status="refreshing_scryfall_metadata", requested=len(requested))
    checkpoint()
    for start in range(0, len(requested), COLLECTION_BATCH_SIZE):
        if not time_remains():
            progress["status"] = "time_budget_reached"
            checkpoint()
            return
        batch = requested[start:start + COLLECTION_BATCH_SIZE]
        keys = {card_key(name) for name in batch}
        batch_groups = [group for group in groups if keys & {card_key(name) for name in group}]
        _, result = repository.load(batch, fetch=True, commander_alias_groups=batch_groups,
                                    printing_ids={name: identifier for name, identifier in printing_ids.items()
                                                  if card_key(name) in keys})
        for key in ("requests", "fetched", "resolved"):
            progress[key] += result[key]
        progress["processed"] += len(batch)
        for key in ("missing", "stale_names", "failures", "alias_resolutions"):
            progress[key].extend(item for item in result.get(key, []) if item not in progress[key])
        progress["blocked"] = bool(result.get("blocked"))
        checkpoint()
        if progress["blocked"]:
            progress["status"] = "source_access_stopped"
            checkpoint()
            return
    progress["status"] = "complete"
    checkpoint()


def commit_report(job: dict, result: dict) -> dict:
    """Commit completed CPU work in the parent, outside terminable workers."""
    rebuilt = result.pop("_rebuilt_report", None)
    contents = result.pop("_original_contents", None)
    if rebuilt is None:
        return result
    path = Path(job["path"])
    try:
        # A live collector must not have replaced the report while CPU work
        # was running. The operator should restore artifacts separately.
        if path.read_bytes() != contents:
            raise RebuildBlocked("Report changed during regeneration; refusing to overwrite concurrent work")
        backup = Path(job["backup_dir"]) / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("xb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        result["backup"] = str(backup.resolve())
        atomic_json(path, rebuilt)
        return result
    except (OSError, ValueError, TypeError) as error:
        return {"commander_slug": path.stem, "status": "blocked", "reason": str(error)}


def process_report(job: dict) -> dict:
    path, root = Path(job["path"]), Path(job["root"])
    try:
        contents = path.read_bytes()
        report = read_json(path)
        slug = checked_identifier(report["commander_slug"])
        if path.stem != slug:
            raise RebuildBlocked("Report filename does not match its commander")
        if not commander_is_eligible(report.get("total_edhrec_decks")):
            return {"commander_slug": slug, "status": "skipped", "reason": "below_200_total_decks"}
        if (report.get("deck_analysis", {}).get("status") != "fitted"
                or not report.get("deck_analysis", {}).get("packages")):
            return {"commander_slug": slug, "status": "skipped", "reason": "no_fitted_packages"}
        validation_directory = Path(job["validation_directory"]) if job.get("validation_directory") else None
        if job.get("only_outdated") and not needs_rebuild(report, validation_directory=validation_directory,
                                                        max_age_days=job.get("max_age_days", 3)):
            return {"commander_slug": slug, "status": "skipped", "reason": "already_current_generation"}
        inputs = load_inputs(root, report, _worker_aliases)
        rebuilt, result = rebuild_report(report, inputs, _worker_repository,
            validation_directory=validation_directory,
            prefer_playability=job["prefer_playability"], rebuilt_at=job["rebuilt_at"])
        result["input_dataset_sha256"] = inputs["dataset_sha256"]
        result["source_analysis_generated_at"] = report.get("generated_at")
        result["dry_run"] = job["dry_run"]
        if not job["dry_run"]:
            result.update(_rebuilt_report=rebuilt, _original_contents=contents)
            if not job.get("prepare_only"):
                return commit_report(job, result)
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return {"commander_slug": path.stem, "status": "blocked", "reason": str(error)}


def prefetch_metadata_job(job: dict) -> dict:
    """The sole HTTP stream is isolated so a stalled request can be stopped.

    CPU workers are started only after this process has exited. Cache saves
    and progress checkpoints are atomic, so a deadline retains completed
    batches and never makes a partially written cache authoritative.
    """
    summary = job["summary"]

    def checkpoint():
        atomic_json(Path(job["summary_path"]), summary)

    def time_remains():
        if time.monotonic() >= job["deadline"]:
            summary["time_budget_reached"] = True
            return False
        return True

    prefetch_metadata(Path(job["root"]), [Path(path) for path in job["paths"]],
                      MetadataRepository(job["cache"], max_age_days=job["max_age_days"]), summary,
                      only_outdated=job["only_outdated"], time_remains=time_remains, checkpoint=checkpoint,
                      validation_directory=Path(job["validation_directory"]) if job.get("validation_directory") else None)
    return summary["metadata_prefetch"]


def rebuild_all(root: Path, cache: Path, *, commanders: list[str] | None = None, workers: int = 1,
                dry_run: bool = False, summary_path: Path | None = None, backup_dir: Path | None = None,
                validation_directory: Path | None = None, max_age_days: float = 3,
                prefer_playability: bool = True, only_outdated: bool = False,
                max_runtime_seconds: float = 0, fetch_metadata: bool = False,
                current_discovery_only: bool = False) -> dict:
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("Workers must be an integer from 1 through 8")
    if not root.is_dir():
        raise ValueError(f"Build-style directory does not exist: {root}")
    if isinstance(max_runtime_seconds, bool) or not isinstance(max_runtime_seconds, (float, int)) or not math.isfinite(max_runtime_seconds) or max_runtime_seconds < 0:
        raise ValueError("Runtime must be a finite nonnegative number of seconds")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, (float, int)) or not math.isfinite(max_age_days) or max_age_days <= 0:
        raise ValueError("Metadata age must be a positive finite number of days")
    started = time.monotonic()
    requested = {checked_identifier(slug) for slug in (commanders or [])}
    paths = sorted(path for path in root.glob("*.json") if path.stem not in {"index", "build_style_batch"}
                   and (not requested or path.stem in requested))
    missing = requested - {path.stem for path in paths}
    if missing:
        raise ValueError(f"Requested reports do not exist: {sorted(missing)}")
    outside_scope = []
    if current_discovery_only:
        # The policy reads only coverage facts. Avoid retaining all full learned
        # models in memory merely to select the same visible commander scope.
        reports = {}
        for path in paths:
            try:
                report = read_json(path)
                reports[path.stem] = {"total_edhrec_decks": report.get("total_edhrec_decks"),
                                      "collection": report.get("collection")}
            except (OSError, ValueError, TypeError):
                continue
        allowed = current_discovery_slugs(root, reports)
        outside_scope = [path.stem for path in paths if path.stem not in allowed]
        paths = [path for path in paths if path.stem in allowed]
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = backup_dir or root / "_collection" / "deck-rebuild-backups" / run_id
    summary_path = summary_path or root / "_collection" / "deck-rebuild-summary.json"
    summary = {"schema_version": 1, "generation_version": GENERATION_VERSION, "started_at": now.isoformat(),
               "completed_at": None, "network_enabled": bool(fetch_metadata and not dry_run), "refitted": False, "dry_run": dry_run,
               "report_count": len(paths), "rebuilt": 0, "regenerated": 0, "blocked": 0, "skipped": 0,
               "changed_deck_lists": 0, "distinctive_omissions_before": 0, "distinctive_omissions_after": 0,
               "backup_dir": str(backup_dir.resolve()), "reports": [], "blockers": [],
               "max_runtime_seconds": max_runtime_seconds, "time_budget_reached": False,
               "only_outdated": only_outdated, "pending": 0, "pending_commanders": [],
               "scope": "current_discovery" if current_discovery_only else "all_saved_reports",
               "outside_scope": outside_scope,
               "metadata_prefetch": {"enabled": bool(fetch_metadata and not dry_run),
                                     "status": "skipped_dry_run" if fetch_metadata and dry_run else "not_requested",
                                     "network_enabled": bool(fetch_metadata and not dry_run), "edhrec_requests": 0}}
    pending = set()
    for path in paths:
        try:
            report = read_json(path)
            if (needs_rebuild(report, validation_directory=validation_directory, max_age_days=max_age_days) or not only_outdated and commander_is_eligible(report.get("total_edhrec_decks"))
                    and report.get("deck_analysis", {}).get("status") == "fitted"
                    and report.get("deck_analysis", {}).get("packages")):
                pending.add(path.stem)
        except (OSError, ValueError, TypeError):
            pending.add(path.stem)
    jobs = [{"root": str(root), "path": str(path), "backup_dir": str(backup_dir),
             "validation_directory": str(validation_directory) if validation_directory is not None else None,
             "prefer_playability": prefer_playability, "dry_run": dry_run, "rebuilt_at": now.isoformat(),
             "only_outdated": only_outdated, "max_age_days": max_age_days} for path in paths]

    def checkpoint():
        summary["pending_commanders"] = sorted(pending)
        summary["pending"] = len(pending)
        atomic_json(summary_path, summary)

    def time_remains():
        if max_runtime_seconds and time.monotonic() - started >= max_runtime_seconds:
            summary["time_budget_reached"] = True
            return False
        return True

    def remaining_seconds():
        return max(0, max_runtime_seconds - (time.monotonic() - started)) if max_runtime_seconds else None

    def record(result):
        summary["reports"].append(result)
        summary[result["status"]] += 1
        if result["status"] == "rebuilt":
            summary["regenerated"] += 1
            if not dry_run:
                pending.discard(result["commander_slug"])
        elif result["status"] == "blocked":
            summary["blockers"].append({key: result[key] for key in ("commander_slug", "reason")})
        for package in result.get("packages", []):
            summary["changed_deck_lists"] += int(package["list_changed"])
            summary["distinctive_omissions_before"] += package["before"]["omitted_count"]
            summary["distinctive_omissions_after"] += package["after"]["omitted_count"]
        checkpoint()
        print(f"{len(summary['reports'])}/{len(paths)} {result['commander_slug']}: {result['status']}"
              + (f" ({result['reason']})" if result.get("reason") else ""), flush=True)

    checkpoint()
    if fetch_metadata and not dry_run:
        if max_runtime_seconds and time_remains():
            # Even one batch can contain many exact-name fallbacks. An HTTP
            # timeout per request cannot bound that batch or a stuck library.
            # Keep one serialized HTTP stream in an independently stoppable
            # process, and never overlap it with CPU workers.
            pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
            expired = False
            try:
                future = pool.submit(prefetch_metadata_job, {
                    "root": str(root), "paths": [str(path) for path in paths], "cache": str(cache),
                    "max_age_days": max_age_days, "summary": summary, "summary_path": str(summary_path),
                    "validation_directory": str(validation_directory) if validation_directory is not None else None,
                    "only_outdated": only_outdated, "deadline": started + max_runtime_seconds})
                try:
                    summary["metadata_prefetch"] = future.result(timeout=remaining_seconds())
                except TimeoutError:
                    expired = True
                    summary["time_budget_reached"] = True
            finally:
                shutdown_executor(pool, terminate=expired)
            if expired:
                # Read only the last fully completed checkpoint after the
                # child has stopped. Successful cache batches remain usable.
                summary["metadata_prefetch"] = read_json(summary_path)["metadata_prefetch"]
                summary["metadata_prefetch"]["status"] = "time_budget_reached"
                checkpoint()
        elif not max_runtime_seconds:
            repository = MetadataRepository(cache, max_age_days=max_age_days)
            prefetch_metadata(root, paths, repository, summary, only_outdated=only_outdated,
                              time_remains=time_remains, checkpoint=checkpoint,
                              validation_directory=validation_directory)
    if workers == 1 and not max_runtime_seconds:
        initialize_worker(str(root), str(cache), max_age_days)
        for job in jobs:
            if not time_remains():
                break
            record(process_report(job))
    elif time_remains():
        pool = ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker,
                                   initargs=(str(root), str(cache), max_age_days),
                                   mp_context=multiprocessing.get_context("spawn"))
        expired = False
        running = {}
        try:
            unscheduled = iter(jobs)
            while True:
                while len(running) < workers and time_remains():
                    job = next(unscheduled, None)
                    if job is None:
                        break
                    running[pool.submit(process_report, {**job, "prepare_only": True})] = job
                if not running:
                    break
                if not time_remains():
                    expired = True
                    break
                done, _ = wait(running, timeout=remaining_seconds(), return_when=FIRST_COMPLETED)
                if not done:
                    summary["time_budget_reached"] = expired = True
                    break
                for completed in done:
                    job = running.pop(completed)
                    record(commit_report(job, completed.result()))
        finally:
            # A context manager performs shutdown(wait=True), which lets one
            # stuck analysis consume the entire GitHub Actions job timeout.
            # Timed-out children never own report or backup writes.
            shutdown_executor(pool, terminate=expired or bool(running))
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-styles-dir", type=Path, required=True)
    parser.add_argument("--card-metadata-cache", type=Path, required=True)
    parser.add_argument("--commander", action="append", dest="commanders")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--validation-directory", type=Path)
    parser.add_argument("--metadata-max-age-days", type=float, default=3)
    parser.add_argument("--single-anchor", action="store_true", help="Skip optional playability/mana candidate exploration")
    parser.add_argument("--only-outdated", action="store_true", help="Rebuild older generations, missing/expired power inputs, and new exact-list score mismatches")
    parser.add_argument("--max-runtime-seconds", type=float, default=0,
                        help="Stop work at this deadline, retaining interrupted reports for retry (0 means unlimited)")
    parser.add_argument("--fetch-metadata", action="store_true",
                        help="Refresh missing/stale Scryfall metadata in one paced stream before workers; disabled for --dry-run")
    parser.add_argument("--current-discovery-only", action="store_true",
                        help="Use the website export's current-collector discovery scope, excluding archival reports")
    args = parser.parse_args()
    summary = rebuild_all(args.build_styles_dir, args.card_metadata_cache, commanders=args.commanders,
        workers=args.workers, dry_run=args.dry_run, summary_path=args.summary, backup_dir=args.backup_dir,
        validation_directory=args.validation_directory, max_age_days=args.metadata_max_age_days,
        prefer_playability=not args.single_anchor, only_outdated=args.only_outdated,
        max_runtime_seconds=args.max_runtime_seconds, fetch_metadata=args.fetch_metadata,
        current_discovery_only=args.current_discovery_only)
    print(json.dumps({key: value for key, value in summary.items() if key != "reports"}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if summary["blocked"] else 0)


if __name__ == "__main__":
    main()
