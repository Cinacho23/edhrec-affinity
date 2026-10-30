#!/usr/bin/env python3
"""Check saved package decks on EDHPowerLevel, repair conflicts, and recheck.

The queue uses complete exact-list site observations rather than local card
text guesses. It retains every successful check and report write immediately,
deduplicates lists with the same commander roles, and never recollects EDHREC
decks or refits package models. A source access block stops all site checks.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from edhrec_affinity.build_styles.eligibility import commander_is_eligible
from edhrec_affinity.build_styles.external_validation import (
    attach_external_validation, cardlist_hash, external_restriction_evidence,
    validation_record_key,
)
from edhrec_affinity.build_styles.power_brackets import verified_score_bracket
from edhrec_affinity.build_styles.sources import checked_identifier

from rebuild_generated_decks import (
    GENERATION_VERSION, atomic_json, current_discovery_slugs, packages, read_json, rebuild_all,
)


QUEUE_SCHEMA_VERSION = 1
MIN_CHECK_INTERVAL_SECONDS = 5.0
MAX_REPAIR_ATTEMPTS = 2
MAX_CHECK_ATTEMPTS = 2
SOURCE_RETRY_SECONDS = 86400
CHECK_RETRY_SECONDS = 86400
REPAIR_RETRY_SECONDS = 3600


def checked_number(value, label, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label} must be a finite number of at least {minimum}")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _due(value):
    parsed = _timestamp(value)
    return parsed is not None and parsed <= datetime.now(timezone.utc)


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _identity(deck):
    return validation_record_key(deck)


def _official(deck, directory):
    rating = attach_external_validation(deck, directory)
    evidence = external_restriction_evidence(rating, expected_hash=cardlist_hash(deck["cards"]))
    return rating, evidence


def _conflict(rating, evidence):
    target = verified_score_bracket(rating)
    return evidence is not None and target is not None and rating.get("bracket", 0) > target


def _proof(deck, rating, evidence):
    digest = cardlist_hash(deck["cards"])
    result = {"status": "verified" if evidence is not None else "pending",
              "source": "edhpowerlevel.com", "deck_sha256": digest,
              "categories": sorted(evidence) if evidence is not None else []}
    if evidence is not None:
        result.update(checked_at=rating["checked_at"],
                      source_script_sha256=rating["restriction_check"]["source_script_sha256"])
    return result


def report_paths(root, commanders=None, *, current_discovery_only=True):
    wanted = {checked_identifier(slug) for slug in commanders or []}
    paths = sorted(path for path in root.glob("*.json")
                   if path.stem not in {"index", "build_style_batch"} and (not wanted or path.stem in wanted))
    missing = wanted - {path.stem for path in paths}
    if missing:
        raise ValueError(f"Requested reports do not exist: {sorted(missing)}")
    reports = {}
    for path in paths:
        try:
            report = read_json(path)
            if (path.stem == checked_identifier(report.get("commander_slug"))
                    and commander_is_eligible(report.get("total_edhrec_decks"))
                    and report.get("deck_analysis", {}).get("status") == "fitted"):
                reports[path.stem] = {"total_edhrec_decks": report.get("total_edhrec_decks"),
                                      "collection": report.get("collection")}
        except (OSError, ValueError, TypeError):
            continue
    if current_discovery_only:
        allowed = current_discovery_slugs(root, reports)
        reports = {slug: report for slug, report in reports.items() if slug in allowed}
    return [root / f"{slug}.json" for slug in sorted(reports)]


def catalog(root, paths, directory):
    """Read current lists, never retaining obsolete lists as work after repair."""
    tasks, by_report, invalid = {}, {}, []
    for path in paths:
        try:
            report = read_json(path)
            entries = []
            for model, package in packages(report):
                deck = package.get("generated_deck", {})
                if deck.get("status") != "generated":
                    continue
                try:
                    key = _identity(deck)
                    rating, evidence = _official(deck, directory)
                except (ValueError, TypeError) as error:
                    invalid.append({"commander_slug": path.stem, "model": model,
                                    "package_id": package.get("id"), "reason": str(error)})
                    continue
                entries.append(key)
                if key not in tasks:
                    tasks[key] = {"key": key, "deck": deck, "rating": rating,
                                  "evidence": evidence, "commanders": set(), "auto": model == "auto"}
                tasks[key]["commanders"].add(path.stem)
                tasks[key]["auto"] |= model == "auto"
            if entries:
                by_report[path.stem] = set(entries)
        except (OSError, ValueError, TypeError) as error:
            invalid.append({"commander_slug": path.stem, "reason": str(error)})
    return tasks, by_report, invalid


def attach_report(path, directory, *, attempts=None, backup_dir=None):
    """Publish proof only onto the exact current deck, preserving every list."""
    before = path.read_bytes()
    report = read_json(path)
    changed = False
    for _, package in packages(report):
        deck = package.get("generated_deck", {})
        if deck.get("status") != "generated":
            continue
        validation = deck.setdefault("validation", {})
        previous = deepcopy(validation)
        try:
            key = _identity(deck)
            rating, evidence = _official(deck, directory)
            validation["restriction_validation"] = _proof(deck, rating, evidence)
            attempt = (attempts or {}).get(key)
            if evidence is not None:
                validation.pop("external_validation_attempt", None)
                # An exact site check supersedes an unchanged-list guess.
                if not _conflict(rating, evidence):
                    validation.pop("power_alignment", None)
                else:
                    validation["power_alignment"] = {
                        "status": "known_mismatch", "deck_sha256": cardlist_hash(deck["cards"]),
                        "score_bracket": verified_score_bracket(rating),
                        "minimum_checked_bracket": rating["bracket"], "rating_kind": "verified",
                        "note": "EDHPowerLevel found restrictions above this exact list's numeric power bracket. Card replacements and a new site check are pending.",
                    }
            elif attempt:
                validation["external_validation_attempt"] = {key: attempt[key] for key in
                    ("status", "deck_sha256", "checked_at", "reason") if key in attempt}
            changed |= previous != validation
        except (ValueError, TypeError):
            continue
    if not changed:
        return False
    if path.read_bytes() != before:
        raise ValueError(f"Report changed concurrently: {path.name}")
    if backup_dir is not None:
        backup = backup_dir / "official-validation" / path.name
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(before)
    atomic_json(path, report)
    return True


def _fingerprint(tasks):
    evidence = [[task["key"], task["rating"].get("checked_at"),
                 task["rating"].get("power_level"), task["rating"].get("bracket"),
                 task["rating"].get("bracket_restrictions"), task["rating"].get("restriction_check")]
                for task in sorted(tasks, key=lambda task: task["key"])]
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _risk(task):
    validation = task["deck"].get("validation", {})
    native = validation.get("bracket", {})
    target = verified_score_bracket(task["rating"])
    if target is None:
        target = validation.get("calculated_power", {}).get("score_bracket")
    return (_conflict(task["rating"], task["evidence"])
        or validation.get("power_alignment", {}).get("status") == "known_mismatch"
        or type(target) is int and type(native.get("recommended")) is int and native["recommended"] > target
        or any(type(native.get(key)) is int and native[key] > 0
               for key in ("mass_land_denial_count", "chainable_extra_turn_count")))


def _repair_count(entry):
    """Cap the whole repair lineage, including lists changed by prior repairs."""
    try:
        started = datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - started).total_seconds()
        if started.tzinfo is None or not 0 <= age < 7 * 86400 or entry.get("generation_version") != GENERATION_VERSION:
            return 0
        count = entry.get("attempts", 0)
        return count if type(count) is int and count >= 0 else 0
    except (KeyError, ValueError, TypeError, AttributeError):
        return 0


def validate_all(root: Path, cache: Path, *, validation_directory: Path | None = None,
                 commanders=None, max_runtime_seconds=1800, max_decks=300,
                 check_interval_seconds=MIN_CHECK_INTERVAL_SECONDS,
                 summary_path=None, backup_dir=None, current_discovery_only=True,
                 check_fn=None, checker_factory=None, rebuild_fn=None,
                 monotonic=time.monotonic, sleep=time.sleep, workers=1):
    checked_number(max_runtime_seconds, "Runtime", minimum=1)
    checked_number(check_interval_seconds, "Check interval", minimum=MIN_CHECK_INTERVAL_SECONDS)
    if type(workers) is not int or not 1 <= workers <= 2:
        raise ValueError("Validation workers must be either 1 or 2")
    if type(max_decks) is not int or max_decks < 1:
        raise ValueError("Maximum site checks must be a positive integer")
    if not root.is_dir():
        raise ValueError("Build-style directory does not exist")
    directory = validation_directory or root / "_collection" / "external-validations"
    queue_path = root / "_collection" / "official-validation-queue.json"
    summary_path = summary_path or root / "_collection" / "official-validation-summary.json"
    backup_dir = backup_dir or root / "_collection" / "official-validation-backups"
    paths = report_paths(root, commanders, current_discovery_only=current_discovery_only)
    state = read_json(queue_path) if queue_path.exists() else {"schema_version": QUEUE_SCHEMA_VERSION,
        "check_attempts": {}, "repair_attempts": {}, "source_blocked": False}
    if state.get("schema_version") != QUEUE_SCHEMA_VERSION:
        raise ValueError("Unknown official validation queue version")
    state.setdefault("check_attempts", {})
    state.setdefault("check_retry_at", {})
    state.setdefault("repair_attempts", {})
    state.setdefault("repair_retry_at", {})
    if state.get("source_blocked") and not _timestamp(state.get("source_retry_at")):
        state["source_retry_at"] = _later(SOURCE_RETRY_SECONDS)
    if state.get("source_blocked") and _due(state.get("source_retry_at")):
        state.update(source_blocked=False, source_block_reason=None)
        state.pop("source_retry_at", None)
    for key, retry_at in list(state["check_retry_at"].items()):
        if _due(retry_at):
            state["check_attempts"].pop(key, None)
            del state["check_retry_at"][key]
    if _due(state.get("session_retry_at")):
        state.pop("session_retry_at", None)
    started, end = monotonic(), monotonic() + max_runtime_seconds
    summary = {"schema_version": 1, "started_at": _now(), "completed_at": None,
        "source": "edhpowerlevel.com", "checked": 0, "records_written": 0,
        "repaired_reports": 0, "changed_reports": 0, "changed_deck_lists": 0,
        "pending": 0, "verified": 0, "conflicts": 0, "unresolved": [], "invalid": [],
        "source_blocked": state.get("source_blocked") is True,
        "source_block_reason": state.get("source_block_reason"),
        "time_budget_reached": False, "check_limit_reached": False,
        "continuation_needed": False, "max_checks": max_decks,
        "retry_at": state.get("source_retry_at"), "deferred_checks": 0, "errors": [],
        "check_interval_seconds": check_interval_seconds, "edhrec_requests": 0,
        "workers": workers, "commander_claims": [], "stale_results": 0, "peak_in_flight": 0,
        "reports": len(paths), "scope": "current_discovery" if current_discovery_only else "all_saved_reports"}
    changed_reports, attempts, deferred_repairs = set(), {}, set()
    tasks, by_report, invalid = catalog(root, paths, directory)

    def remaining():
        return max(0, end - monotonic())

    def save():
        state["updated_at"] = _now()
        atomic_json(queue_path, state)
        summary["changed_reports"] = len(changed_reports)
        summary["active_claims"] = deepcopy(state.get("worker_claims", {}))
        atomic_json(summary_path, summary)
        retained_summary = root / "_collection" / "official-validation-summary.json"
        if summary_path != retained_summary:
            atomic_json(retained_summary, summary)

    def attach(slugs):
        for slug in sorted(slugs):
            if attach_report(root / f"{slug}.json", directory, attempts=attempts, backup_dir=backup_dir):
                changed_reports.add(slug)

    def refresh(slugs):
        """Reload only rewritten commander families, not the whole catalog."""
        refreshed, families, errors = catalog(root, [root / f"{slug}.json" for slug in sorted(slugs)], directory)
        for slug in slugs:
            for key in by_report.pop(slug, set()):
                task = tasks[key]
                task["commanders"].discard(slug)
                if not task["commanders"]:
                    del tasks[key]
        for key, task in refreshed.items():
            if key in tasks:
                task["commanders"].update(tasks[key]["commanders"])
                task["auto"] |= tasks[key]["auto"]
            tasks[key] = task
        by_report.update(families)
        return errors

    from edhrec_affinity.build_styles.official_checker import SharedCheckAdmission
    admission = SharedCheckAdmission(min_interval_seconds=check_interval_seconds,
                                     monotonic=monotonic, sleep=sleep)
    if check_fn is None:
        from edhrec_affinity.build_styles.official_checker import OfficialDeckChecker, check_deck
        check_fn = check_deck
        checker_factory = checker_factory or (lambda: OfficialDeckChecker(
            admission=admission, admission_managed=True))
    rebuild_fn = rebuild_fn or rebuild_all

    class Lane:
        """Each Playwright context lives and closes on one dedicated thread."""
        def __init__(self, number):
            self.number, self.slug, self.future, self.task = number, None, None, None
            self.context, self.checker = None, None
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"official-{number}")

        def check(self, deck, timeout):
            if self.checker is None and checker_factory is not None:
                self.context = checker_factory()
                self.checker = self.context.__enter__()
            budget = min(timeout, max(0, remaining() - 2))
            if budget <= 0 or not admission.acquire(timeout_seconds=budget):
                return {"status": "blocked" if admission.blocked else "deferred",
                        "source_blocked": admission.blocked, "reason": admission.reason,
                        "retry_after_seconds": admission.retry_after_seconds,
                        "attempted": False}
            budget = min(timeout, max(0, remaining() - 2))
            if budget <= 0:
                return {"status": "deferred", "source_blocked": False, "attempted": False}
            result = check_fn(deck, validation_directory=directory,
                             timeout_seconds=max(1, budget), checker=self.checker)
            if result.get("source_blocked"):
                admission.stop(result.get("reason") or "EDHPowerLevel access stopped.",
                               retry_after_seconds=result.get("retry_after_seconds") or 0)
            return result

        def close(self):
            if self.context is not None:
                self.context.__exit__(None, None, None)

    lanes = [Lane(index) for index in range(workers)]
    inflight_keys = set()
    finished_families = set()
    family_order = sorted(by_report, key=lambda slug: (
        min(0 if _conflict(tasks[key]["rating"], tasks[key]["evidence"]) else
            1 if _risk(tasks[key]) else 2 for key in by_report[slug]),
        all(tasks[key]["rating"].get("restriction_check", {}).get("status") == "complete"
            for key in by_report[slug]), slug))
    # A run has one stable ordering, but each new run reprioritizes current
    # evidence. Only active ownership carries over; an old whole-catalog order
    # must not strand newly discovered official conflicts behind stale work.
    state["family_order"] = family_order
    previous_claims = state.get("worker_claims", {})
    previous_claims = previous_claims if isinstance(previous_claims, dict) else {}
    state["worker_claims"] = {}

    def related(slug):
        return [tasks[key] for key in by_report.get(slug, ()) if key in tasks]

    def can_repair(slug, family):
        return (family and all(task["evidence"] is not None for task in family)
            and any(_conflict(task["rating"], task["evidence"]) for task in family)
            and _repair_count(state["repair_attempts"].get(slug, {})) < MAX_REPAIR_ATTEMPTS
            and slug not in deferred_repairs
            and (slug not in state["repair_retry_at"] or _due(state["repair_retry_at"][slug])))

    def actionable(family):
        return [task for task in family if task["evidence"] is None
                and state["check_attempts"].get(task["key"], 0) < MAX_CHECK_ATTEMPTS]

    def recount():
        state["check_attempts"] = {key: count for key, count in state["check_attempts"].items() if key in tasks}
        state["check_retry_at"] = {key: value for key, value in state["check_retry_at"].items() if key in tasks}
        unresolved = []
        missing = [task for task in tasks.values() if task["evidence"] is None]
        for task in missing:
            if state["check_attempts"].get(task["key"], 0) >= MAX_CHECK_ATTEMPTS:
                unresolved.append({"deck_key": task["key"], "retry_at": state["check_retry_at"].get(task["key"]),
                    "reason": "Site check failed twice; saved for a paced retry after the backoff."})
        for slug in by_report:
            family = related(slug)
            complete = family and all(task["evidence"] is not None for task in family)
            conflicting = any(_conflict(task["rating"], task["evidence"]) for task in family)
            if complete and not conflicting:
                # A completed, aligned final family ends its repair lineage.
                # Later independent evidence/list changes get a fresh budget.
                state["repair_attempts"].pop(slug, None)
                state["repair_retry_at"].pop(slug, None)
            if (complete and conflicting
                and _repair_count(state["repair_attempts"].get(slug, {})) >= MAX_REPAIR_ATTEMPTS):
                unresolved.append({"commander_slug": slug,
                    "reason": "Restrictions still conflict after two repair attempts for this exact site evidence."})
        summary.update(unresolved=unresolved, invalid=invalid, verified=len(tasks) - len(missing),
            conflicts=sum(_conflict(task["rating"], task["evidence"]) for task in tasks.values()),
            pending=len(missing) + sum(bool(can_repair(slug, related(slug))) for slug in by_report))

    def record_claim(lane, *, resumed=False):
        state["worker_claims"][str(lane.number + 1)] = lane.slug
        summary["commander_claims"].append({"worker": lane.number + 1, "commander_slug": lane.slug,
            "direction": "bottom_to_top" if lane.number % 2 else "top_to_bottom", "resumed": resumed})
        print(json.dumps({"event": "edhpowerlevel_commander_claimed", **summary["commander_claims"][-1]},
                         separators=(",", ":")), flush=True)
        save()

    def claim(lane):
        # Opposite ends of one stable ordering, with coordinator-only ownership.
        owned = {worker.slug for worker in lanes if worker.slug is not None}
        candidates = [slug for slug in family_order if slug not in owned and slug not in finished_families
                      and (actionable(related(slug)) or can_repair(slug, related(slug)))]
        if not candidates:
            return False
        lane.slug = candidates[-1] if lane.number % 2 else candidates[0]
        record_claim(lane)
        return True

    # A bounded job can end midway through a package family. Restore ownership
    # before selecting new endpoints, even if new evidence changed its risk.
    restored = set()
    for owner, slug in sorted(previous_claims.items(), key=lambda item: str(item[0])):
        if (not isinstance(slug, str) or slug not in by_report or slug in restored
                or not (actionable(related(slug)) or can_repair(slug, related(slug)))):
            continue
        preferred = next((lane for lane in lanes if str(lane.number + 1) == owner and lane.slug is None), None)
        lane = preferred or next((lane for lane in lanes if lane.slug is None), None)
        if lane is not None:
            lane.slug = slug
            restored.add(slug)
            record_claim(lane, resumed=True)

    def repair(slug):
        family = related(slug)
        count = _repair_count(state["repair_attempts"].get(slug, {}))
        fingerprint = _fingerprint(family)
        print(json.dumps({"event": "edhpowerlevel_repair_started", "commander_slug": slug,
            "completed_attempts": count}, separators=(",", ":")), flush=True)
        save()
        before = set(by_report[slug])
        result = rebuild_fn(root, cache, commanders=[slug], validation_directory=directory,
            workers=1, only_outdated=True, fetch_metadata=True,
            max_runtime_seconds=min(180, max(1, remaining() - 2)),
            summary_path=summary_path.parent / "official-repair-summary.json", backup_dir=backup_dir / "repairs")
        if result.get("regenerated", 0):
            changed_reports.add(slug)
            summary["repaired_reports"] += result["regenerated"]
            summary["changed_deck_lists"] += result.get("changed_deck_lists", 0)
        print(json.dumps({"event": "edhpowerlevel_repair_finished", "commander_slug": slug,
            "regenerated": result.get("regenerated", 0), "changed_deck_lists": result.get("changed_deck_lists", 0),
            "pending": result.get("pending", 0)}, separators=(",", ":")), flush=True)
        if result.get("metadata_prefetch", {}).get("blocked") is True:
            summary["metadata_source_blocked"] = True
            summary["metadata_block_reason"] = "Scryfall access stopped during saved-pool repair."
            deferred_repairs.add(slug)
            state["repair_retry_at"][slug] = _later(REPAIR_RETRY_SECONDS)
            return
        invalid.extend(refresh({slug}))
        attach({slug})
        completed = (type(result.get("regenerated")) is int and result["regenerated"] > 0
            and not result.get("pending") and not result.get("time_budget_reached"))
        if completed:
            previous = state["repair_attempts"].get(slug, {})
            state["repair_attempts"][slug] = {"fingerprint": fingerprint, "attempts": count + 1,
                "started_at": previous.get("started_at") if count else _now(),
                "generation_version": GENERATION_VERSION, "checked_at": _now()}
            state["repair_retry_at"].pop(slug, None)
            if before == by_report.get(slug, set()):
                state["repair_attempts"][slug]["attempts"] = MAX_REPAIR_ATTEMPTS
        else:
            deferred_repairs.add(slug)
            state["repair_retry_at"][slug] = _later(REPAIR_RETRY_SECONDS)
        save()

    def receive(lane):
        task, future = lane.task, lane.future
        lane.task, lane.future = None, None
        inflight_keys.discard(task["key"])
        result = future.result()
        if result.get("source_blocked"):
            reason = result.get("reason") or "EDHPowerLevel access stopped."
            retry_after = result.get("retry_after_seconds")
            retry_after = retry_after if type(retry_after) in {int, float} and math.isfinite(retry_after) else 0
            admission.stop(reason, retry_after_seconds=retry_after)
            summary.update(source_blocked=True, source_block_reason=reason)
            state.update(source_blocked=True, source_block_reason=reason, source_blocked_at=_now(),
                source_retry_at=_later(max(SOURCE_RETRY_SECONDS, retry_after, admission.retry_after_seconds)))
        if result.get("attempted") is False:
            if not result.get("source_blocked"):
                summary["time_budget_reached"] = True
            return
        summary["checked"] += 1
        state["check_attempts"][task["key"]] = state["check_attempts"].get(task["key"], 0) + 1
        if state["check_attempts"][task["key"]] >= MAX_CHECK_ATTEMPTS:
            state["check_retry_at"][task["key"]] = _later(CHECK_RETRY_SECONDS)
        if result.get("status") == "verified":
            rating, evidence = _official(task["deck"], directory)
            if evidence is not None:
                summary["records_written"] += 1
                state["check_attempts"].pop(task["key"], None)
                state["check_retry_at"].pop(task["key"], None)
        else:
            attempts[task["key"]] = {"status": "blocked" if result.get("source_blocked") else "failed",
                "deck_sha256": cardlist_hash(task["deck"]["cards"]), "checked_at": result.get("checked_at") or _now(),
                "reason": str(result.get("reason") or "EDHPowerLevel did not return a complete analysis.")[:500]}
        observation = result.get("observation") or {}
        print(json.dumps({"event": "edhpowerlevel_check", "checked": summary["checked"],
            "records_written": summary["records_written"], "commanders": sorted(task["commanders"]),
            "worker": lane.number + 1, "deck_key": task["key"], "status": result.get("status"),
            "power_level": observation.get("power_level"), "score_bracket": observation.get("score_bracket"),
            "reported_bracket": observation.get("bracket"), "source_blocked": bool(result.get("source_blocked"))},
            separators=(",", ":")), flush=True)
        # Reload before publishing. A result for a changed list remains in the
        # exact-list observation store, and cannot verify the replacement list.
        # A different family's repair may have joined this exact identity while
        # its check was in flight. Publish to its current owners as well as the
        # original snapshot, always reloading their exact lists first.
        affected = set(task["commanders"])
        if task["key"] in tasks:
            affected.update(tasks[task["key"]]["commanders"])
        invalid.extend(refresh(affected))
        if task["key"] not in tasks or not tasks[task["key"]]["commanders"].intersection(task["commanders"]):
            summary["stale_results"] += 1
        attach(affected)
        save()

    try:
        attach(by_report)
        first_cycle = True
        while True:
            received = False
            for lane in lanes:
                if lane.future is not None and lane.future.done():
                    receive(lane)
                    received = True
            recount()
            if received or first_cycle:
                save()
                first_cycle = False
            session_blocked = state.get("session_retry_at") and not _due(state["session_retry_at"])
            can_start = (remaining() > 2 and not summary["source_blocked"]
                and not summary.get("metadata_source_blocked") and not session_blocked
                and not summary["time_budget_reached"])
            if can_start:
                for lane in lanes:
                    if summary.get("metadata_source_blocked") or remaining() <= 2:
                        break
                    if lane.future is not None:
                        continue
                    while lane.slug is not None or claim(lane):
                        family = related(lane.slug)
                        if can_repair(lane.slug, family):
                            repair(lane.slug)
                            family = related(lane.slug)
                            if summary.get("metadata_source_blocked") or remaining() <= 2:
                                break
                        waiting = actionable(family)
                        available = sorted((task for task in waiting if task["key"] not in inflight_keys),
                            key=lambda task: (not task["auto"], task["key"]))
                        if not waiting and not can_repair(lane.slug, family):
                            finished_families.add(lane.slug)
                            state["worker_claims"].pop(str(lane.number + 1), None)
                            lane.slug = None
                            continue
                        reserved = summary["checked"] + sum(worker.future is not None for worker in lanes)
                        if available and reserved < max_decks and remaining() >= 10:
                            lane.task = deepcopy(available[0])
                            inflight_keys.add(lane.task["key"])
                            lane.future = lane.executor.submit(lane.check, deepcopy(lane.task["deck"]),
                                                               min(120, max(1, remaining() - 2)))
                            summary["peak_in_flight"] = max(summary["peak_in_flight"],
                                sum(worker.future is not None for worker in lanes))
                        break
            futures = [lane.future for lane in lanes if lane.future is not None]
            if not futures:
                summary["check_limit_reached"] = (summary["checked"] >= max_decks
                    and any(actionable(related(slug)) for slug in by_report))
                break
            wait(futures, timeout=min(0.5, max(0.01, remaining())), return_when=FIRST_COMPLETED)
        summary["time_budget_reached"] |= remaining() <= 2
    except Exception as error:
        summary["errors"].append(f"{type(error).__name__}: {error}"[:1000])
        state["session_retry_at"] = _later(REPAIR_RETRY_SECONDS)
    finally:
        # Every started check is collected before its thread closes its own
        # browser; only this coordinator writes reports and queue state.
        for lane in lanes:
            if lane.future is not None:
                try:
                    receive(lane)
                except Exception as error:
                    summary["errors"].append(f"Worker {type(error).__name__}: {error}"[:1000])
                    state["session_retry_at"] = _later(REPAIR_RETRY_SECONDS)
            try:
                lane.executor.submit(lane.close).result()
            except Exception as error:
                summary["errors"].append(f"Browser cleanup {type(error).__name__}: {error}"[:1000])
                state["session_retry_at"] = _later(REPAIR_RETRY_SECONDS)
            lane.executor.shutdown(wait=True)
        recount()
        # Recount after the final site check/repair so the saved continuation
        # reflects current lists, including newly created unverified hashes.
        missing = [task for task in tasks.values() if task["evidence"] is None]
        ready_checks = sum(state["check_attempts"].get(task["key"], 0) < MAX_CHECK_ATTEMPTS for task in missing)
        summary.update(verified=sum(task["evidence"] is not None for task in tasks.values()),
                       invalid=invalid, unique_lists=len(tasks),
                       conflicts=sum(_conflict(task["rating"], task["evidence"]) for task in tasks.values()))
        pending_repairs, ready_repairs = 0, 0
        for slug, keys in by_report.items():
            related = [tasks[key] for key in keys]
            if all(task["evidence"] is not None for task in related) and any(_conflict(task["rating"], task["evidence"]) for task in related):
                entry = state["repair_attempts"].get(slug, {})
                if _repair_count(entry) < MAX_REPAIR_ATTEMPTS:
                    pending_repairs += 1
                    ready_repairs += int(slug not in state["repair_retry_at"] or _due(state["repair_retry_at"][slug]))
        summary["pending"] = len(missing) + pending_repairs
        summary["deferred_checks"] = len(missing) - ready_checks
        ready_pending = ready_checks + ready_repairs
        session_blocked = bool(state.get("session_retry_at") and not _due(state["session_retry_at"]))
        retry_times = [value for value in [state.get("source_retry_at"), state.get("session_retry_at"),
            *state["check_retry_at"].values(), *state["repair_retry_at"].values()] if _timestamp(value) and not _due(value)]
        summary["retry_at"] = min(retry_times, key=_timestamp) if retry_times and (
            summary["source_blocked"] or summary.get("metadata_source_blocked") or session_blocked or not ready_pending) else None
        summary["continuation_needed"] = (ready_pending > 0 and not summary["source_blocked"]
            and not summary.get("metadata_source_blocked")
            and not session_blocked
            and (summary["checked"] > 0 or summary["repaired_reports"] > 0))
        summary["completed_at"] = _now()
        save()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-styles-dir", required=True, type=Path)
    parser.add_argument("--card-metadata-cache", required=True, type=Path)
    parser.add_argument("--validation-directory", type=Path)
    parser.add_argument("--commander", action="append", dest="commanders")
    parser.add_argument("--max-runtime-seconds", type=float, default=1800)
    parser.add_argument("--max-decks", type=int, default=300)
    parser.add_argument("--workers", type=int, default=2, help="Opposite-end commander workers (1 or 2)")
    parser.add_argument("--check-interval-seconds", type=float, default=MIN_CHECK_INTERVAL_SECONDS)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--all-saved-reports", action="store_true")
    args = parser.parse_args()
    summary = validate_all(args.build_styles_dir, args.card_metadata_cache,
        validation_directory=args.validation_directory, commanders=args.commanders,
        max_runtime_seconds=args.max_runtime_seconds, max_decks=args.max_decks, workers=args.workers,
        check_interval_seconds=args.check_interval_seconds, summary_path=args.summary,
        backup_dir=args.backup_dir, current_discovery_only=not args.all_saved_reports)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    raise SystemExit(1 if summary["source_blocked"] or summary["errors"] else 0)


if __name__ == "__main__":
    main()
