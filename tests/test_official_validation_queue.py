"""The catalog queue checks exact site lists before and after bounded repair."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import quote_plus

import pytest

from edhrec_affinity.build_styles.external_validation import (
    RESTRICTION_CATEGORIES, attach_external_validation, cardlist_hash,
    record_external_validation, validation_record_key,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("validate_generated_decks", ROOT / "scripts/validate_generated_decks.py")
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path, slug="commander", card="Good Card"):
    deck = {"status": "generated", "card_count": 100,
        "cards": [{"name": "Commander", "quantity": 1}, {"name": "Forest", "quantity": 98},
                  {"name": card, "quantity": 1}],
        "commanders": [{"name": "Commander", "quantity": 1}],
        "primer": "Keep this exact primer", "moxfield_text": "Keep this exact export", "validation": {}}
    package = {"id": 0, "name": "Package 1", "top_cards": [{"name": card}], "generated_deck": deck}
    report = {"commander_slug": slug, "commander_name": "Commander", "total_edhrec_decks": 200,
        "generated_at": "2026-10-20T00:00:00Z",
        "deck_analysis": {"status": "fitted", "packages": [package],
            "alternative_models": [{"components": 1, "packages": [deepcopy(package)]}]},
        "deck_generation": {"version": 1}}
    root = tmp_path / "styles"
    write(root / f"{slug}.json", report)
    return root, report


def observation(deck, *, conflict=False):
    main = [row for row in deck["cards"] if row["name"] != "Commander"]
    encoded = "~".join(f"{row['quantity']} {row['name']}" for row in main) + "~~1 Commander~Z~"
    categories = {key: [] for key in RESTRICTION_CATEGORIES}
    if conflict:
        categories["mass_land_denial"] = ["Bad Card"]
    return {"source": "edhpowerlevel.com", "checked_at": datetime.now(timezone.utc).isoformat(),
        "card_count": 100, "playability_pct": 75, "power_level": 6.1,
        "bracket": 4 if conflict else 2, "source_url": "https://edhpowerlevel.com/?d=" + quote_plus(encoded),
        "bracket_restrictions": categories,
        "restriction_check": {"status": "complete", "method": "edhpowerlevel_page",
            "source_script_sha256": "a" * 64, "commander_names": ["Commander"]}}


def checker(calls, *, before=None):
    def check(deck, *, validation_directory, timeout_seconds, checker):
        calls.append(validation_record_key(deck))
        if before:
            before()
        conflict = any(row["name"] == "Bad Card" for row in deck["cards"])
        record_external_validation(deck, observation(deck, conflict=conflict), validation_directory)
        return {"status": "verified", "source_blocked": False}
    return check


def run(root, tmp_path, check_fn, **kwargs):
    if "monotonic" not in kwargs:
        kwargs.update(virtual_clock())
    return queue.validate_all(root, tmp_path / "metadata.json", check_fn=check_fn,
        current_discovery_only=False, max_runtime_seconds=60, **kwargs)


def test_duplicate_models_are_checked_once_and_publish_exact_proof(tmp_path):
    root, report = fixture(tmp_path)
    calls = []
    result = run(root, tmp_path, checker(calls))
    updated = queue.read_json(root / "commander.json")
    assert len(calls) == result["checked"] == result["unique_lists"] == result["verified"] == 1
    assert result["pending"] == result["conflicts"] == result["repaired_reports"] == 0
    assert result["changed_reports"] == 1
    for (_, old), (_, new) in zip(queue.packages(report), queue.packages(updated), strict=True):
        assert new["top_cards"] == old["top_cards"]
        assert new["generated_deck"]["cards"] == old["generated_deck"]["cards"]
        assert new["generated_deck"]["primer"] == old["generated_deck"]["primer"]
        assert new["generated_deck"]["moxfield_text"] == old["generated_deck"]["moxfield_text"]
        validation = new["generated_deck"]["validation"]
        assert validation["external_rating"]["status"] == "verified"
        assert validation["restriction_validation"]["status"] == "verified"
        assert validation["restriction_validation"]["categories"] == sorted(RESTRICTION_CATEGORIES)
    assert updated["generated_at"] == report["generated_at"]
    assert updated["deck_generation"] == report["deck_generation"]


def test_second_run_reuses_fresh_site_records_without_browser_or_writes(tmp_path):
    root, _ = fixture(tmp_path)
    first = run(root, tmp_path, checker([]))
    before = (root / "commander.json").read_bytes()
    second = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Repeated a fresh site check"),
                 checker_factory=lambda: pytest.fail("Started an unnecessary browser"))
    assert first["checked"] == 1
    assert second["checked"] == second["changed_reports"] == 0
    assert second["pending"] == 0
    assert (root / "commander.json").read_bytes() == before


def test_official_conflict_repairs_saved_pool_then_checks_final_hash(tmp_path):
    root, _ = fixture(tmp_path, card="Bad Card")
    calls, repairs = [], []

    def repair(saved_root, cache, **kwargs):
        repairs.append(kwargs)
        assert kwargs["commanders"] == ["commander"]
        assert kwargs["validation_directory"] == root / "_collection/external-validations"
        assert kwargs["fetch_metadata"] is True and kwargs["only_outdated"] is True
        report = queue.read_json(root / "commander.json")
        for _, package in queue.packages(report):
            for row in package["generated_deck"]["cards"]:
                if row["name"] == "Bad Card":
                    row["name"] = "Good Card"
        write(root / "commander.json", report)
        return {"regenerated": 1, "changed_deck_lists": 2}

    result = run(root, tmp_path, checker(calls), rebuild_fn=repair, sleep=lambda _: None)
    assert len(repairs) == result["repaired_reports"] == 1
    assert len(calls) == result["checked"] == 2
    assert calls[0] != calls[1]
    assert result["conflicts"] == result["pending"] == 0
    final = queue.read_json(root / "commander.json")
    assert all(package["generated_deck"]["validation"]["restriction_validation"]["status"] == "verified"
               for _, package in queue.packages(final))


def test_new_hash_stays_pending_when_check_limit_reached_after_repair(tmp_path):
    root, _ = fixture(tmp_path, card="Bad Card")

    def repair(*_args, **_kwargs):
        report = queue.read_json(root / "commander.json")
        for _, package in queue.packages(report):
            package["generated_deck"]["cards"][-1]["name"] = "Good Card"
        write(root / "commander.json", report)
        return {"regenerated": 1, "changed_deck_lists": 2}

    result = run(root, tmp_path, checker([]), rebuild_fn=repair, max_decks=1)
    final = queue.read_json(root / "commander.json")
    assert result["checked"] == 1 and result["pending"] == 1 and result["continuation_needed"]
    assert result["check_limit_reached"]
    deck = final["deck_analysis"]["packages"][0]["generated_deck"]
    assert deck["validation"]["external_rating"]["status"] == "unverified"
    assert deck["validation"]["restriction_validation"]["status"] == "pending"
    state = queue.read_json(root / "_collection/official-validation-queue.json")
    assert state["repair_attempts"]["commander"]["attempts"] == 1
    resumed = run(root, tmp_path, checker([]), rebuild_fn=lambda *_a, **_kw: pytest.fail("Repaired aligned deck"))
    assert resumed["checked"] == 1 and resumed["pending"] == 0
    assert queue.read_json(root / "_collection/official-validation-queue.json")["repair_attempts"] == {}


def test_site_access_block_stops_globally_and_survives_resume(tmp_path):
    root, report = fixture(tmp_path)
    other = deepcopy(report)
    other["commander_slug"] = "other"
    for _, package in queue.packages(other):
        package["generated_deck"]["cards"][-1]["name"] = "Other Card"
    write(root / "other.json", other)
    calls = []

    def blocked(deck, **_kwargs):
        calls.append(cardlist_hash(deck["cards"]))
        return {"status": "blocked", "source_blocked": True, "reason": "HTTP 429; no retry"}

    result = run(root, tmp_path, blocked)
    assert len(calls) == 1 and result["source_blocked"]
    assert result["pending"] == 2 and not result["continuation_needed"]
    resumed = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Retried a saved source block"))
    assert resumed["source_blocked"] and resumed["checked"] == 0
    attempt = queue.read_json(root / "commander.json")["deck_analysis"]["packages"][0]["generated_deck"]["validation"]["external_validation_attempt"]
    assert attempt["status"] == "blocked" and attempt["reason"].startswith("HTTP 429")


def test_repair_that_changes_nothing_is_unresolved_without_looping(tmp_path):
    root, _ = fixture(tmp_path, card="Bad Card")
    repairs = []

    def unchanged(*_args, **_kwargs):
        repairs.append(True)
        return {"regenerated": 1, "changed_deck_lists": 0}

    result = run(root, tmp_path, checker([]), rebuild_fn=unchanged)
    assert len(repairs) == 1 and result["conflicts"] == 1
    assert result["pending"] == 0 and result["unresolved"]
    assert not result["continuation_needed"]
    resumed = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Rechecked unchanged conflict"),
        rebuild_fn=lambda *_a, **_kw: pytest.fail("Retried exhausted repair"))
    assert resumed["pending"] == 0 and resumed["unresolved"]


def test_unavailable_site_result_retries_at_most_twice_and_keeps_lists(tmp_path):
    root, original = fixture(tmp_path)
    calls = []

    def failed(*_args, **_kwargs):
        calls.append(True)
        return {"status": "failed", "source_blocked": False, "reason": "Combo tracker incomplete"}

    result = run(root, tmp_path, failed, sleep=lambda _: None)
    assert len(calls) == 2 and not result["source_blocked"]
    assert result["pending"] == 1 and result["unresolved"] and result["retry_at"]
    actual = queue.read_json(root / "commander.json")
    assert actual["deck_analysis"]["packages"][0]["generated_deck"]["cards"] == original["deck_analysis"]["packages"][0]["generated_deck"]["cards"]


def test_queue_retains_records_and_summary_after_runtime_budget(tmp_path):
    root, _ = fixture(tmp_path)
    clock = [0]

    def elapsed():
        clock[0] += 20

    result = queue.validate_all(root, tmp_path / "metadata.json", check_fn=checker([], before=elapsed),
        current_discovery_only=False, max_runtime_seconds=15, monotonic=lambda: clock[0], sleep=lambda _: None)
    assert result["checked"] == 1 and result["time_budget_reached"]
    retained = queue.read_json(root / "_collection/official-validation-summary.json")
    assert retained["checked"] == result["checked"]
    assert list((root / "_collection/external-validations").glob("*.json"))


def test_interrupted_repair_keeps_its_lineage_and_resumes_after_backoff(tmp_path):
    root, _ = fixture(tmp_path, card="Bad Card")
    result = run(root, tmp_path, checker([]), rebuild_fn=lambda *_a, **_kw: {
        "regenerated": 0, "pending": 1, "time_budget_reached": True, "deferred": 1})
    state_file = root / "_collection/official-validation-queue.json"
    state = queue.read_json(state_file)
    assert state["repair_attempts"] == {}
    assert state["repair_retry_at"]["commander"]
    assert result["pending"] == 1 and result["retry_at"] and not result["continuation_needed"]
    state["repair_retry_at"]["commander"] = "2000-01-01T00:00:00Z"
    write(state_file, state)
    repairs = []

    def completed(*_args, **_kwargs):
        repairs.append(True)
        return {"regenerated": 1, "pending": 0}

    resumed = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Rechecked fresh exact list"), rebuild_fn=completed)
    assert repairs == [True] and resumed["pending"] == 0


def test_source_access_retry_waits_a_day_then_can_run_again(tmp_path):
    root, _ = fixture(tmp_path)
    blocked = run(root, tmp_path, lambda *_a, **_kw: {
        "status": "blocked", "source_blocked": True, "reason": "HTTP 403"})
    assert blocked["retry_at"] and blocked["source_blocked"]
    state_file = root / "_collection/official-validation-queue.json"
    state = queue.read_json(state_file)
    state["source_retry_at"] = "2000-01-01T00:00:00Z"
    write(state_file, state)
    result = run(root, tmp_path, checker([]))
    assert result["checked"] == 1 and not result["source_blocked"] and result["pending"] == 0


def test_failed_list_retries_after_its_saved_backoff(tmp_path):
    root, _ = fixture(tmp_path)
    run(root, tmp_path, lambda *_a, **_kw: {"status": "failed", "source_blocked": False}, sleep=lambda _: None)
    state_file = root / "_collection/official-validation-queue.json"
    state = queue.read_json(state_file)
    state["check_retry_at"] = {key: "2000-01-01T00:00:00Z" for key in state["check_retry_at"]}
    write(state_file, state)
    result = run(root, tmp_path, checker([]))
    assert result["checked"] == 1 and result["pending"] == 0


def test_never_checked_family_precedes_expired_alphabetic_family(tmp_path):
    root, old = fixture(tmp_path, slug="a-old", card="Old Card")
    _, new = fixture(tmp_path, slug="z-new", card="New Card")
    old_deck = old["deck_analysis"]["packages"][0]["generated_deck"]
    values = observation(old_deck)
    values["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    record_external_validation(old_deck, values, root / "_collection/external-validations")
    calls = []
    result = run(root, tmp_path, checker(calls), max_decks=1)
    assert calls == [validation_record_key(new["deck_analysis"]["packages"][0]["generated_deck"])]
    assert result["pending"] == 1


def test_known_restriction_conflict_family_gets_priority_over_alphabetic_order(tmp_path):
    root, _ = fixture(tmp_path, slug="a-safe")
    _, risky = fixture(tmp_path, slug="z-risky", card="Bad Card")
    for _, package in queue.packages(risky):
        package["generated_deck"]["validation"]["power_alignment"] = {"status": "known_mismatch"}
    write(root / "z-risky.json", risky)
    calls = []
    result = run(root, tmp_path, checker(calls), max_decks=1, rebuild_fn=lambda *_a, **_kw: {
        "regenerated": 0, "pending": 1, "time_budget_reached": True})
    assert calls[0] == validation_record_key(risky["deck_analysis"]["packages"][0]["generated_deck"])
    assert result["checked"] == 1


def test_below_minimum_commander_is_not_checked(tmp_path):
    root, report = fixture(tmp_path)
    report["total_edhrec_decks"] = 199
    write(root / "commander.json", report)
    result = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Checked ineligible commander"))
    assert result["reports"] == result["unique_lists"] == result["checked"] == 0


def test_validation_seed_preserves_newer_cached_observations(tmp_path):
    from seed_build_style_validations import seed_validations
    source, destination = tmp_path / "repo", tmp_path / "artifact"
    name = "a" * 64 + "." + "b" * 64 + ".json"
    write(source / name, {"checked_at": "2026-10-28T00:00:00Z", "value": "repo"})
    assert seed_validations(source, destination) == 1
    write(destination / name, {"checked_at": "2026-10-29T00:00:00Z", "value": "newer"})
    assert seed_validations(source, destination) == 0
    assert queue.read_json(destination / name)["value"] == "newer"
    write(source / name, {"checked_at": "2026-10-30T00:00:00Z", "value": "newest"})
    assert seed_validations(source, destination) == 1
    assert queue.read_json(destination / name)["value"] == "newest"


def test_browser_startup_failure_preserves_summary_and_defers_another_launch(tmp_path):
    root, _ = fixture(tmp_path)
    cleanup = []

    class MissingBrowser:
        def __enter__(self):
            raise RuntimeError("Browser executable missing")

        def __exit__(self, *_args):
            cleanup.append(True)

    result = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Called a missing browser"), checker_factory=MissingBrowser)
    assert result["errors"] and result["retry_at"] and result["pending"] == 1
    assert not result["continuation_needed"] and cleanup == [True]
    assert queue.read_json(root / "_collection/official-validation-summary.json")["errors"] == result["errors"]
    resumed = run(root, tmp_path, lambda *_a, **_kw: pytest.fail("Retried unavailable browser"),
        checker_factory=lambda: pytest.fail("Repeated startup before backoff"))
    assert resumed["checked"] == 0 and resumed["retry_at"]


def test_metadata_access_block_defers_repair_without_consuming_attempts(tmp_path):
    root, _ = fixture(tmp_path, card="Bad Card")
    result = run(root, tmp_path, checker([]), rebuild_fn=lambda *_a, **_kw: {
        "regenerated": 0, "pending": 1, "metadata_prefetch": {"blocked": True}})
    state = queue.read_json(root / "_collection/official-validation-queue.json")
    assert state["repair_attempts"] == {} and state["repair_retry_at"]["commander"]
    assert result["metadata_source_blocked"] and result["retry_at"]
    assert result["pending"] == 1 and not result["continuation_needed"]


@pytest.mark.parametrize("kwargs", [{"max_decks": 0}, {"max_decks": True},
    {"max_runtime_seconds": float("nan")}, {"check_interval_seconds": 4.9}])
def test_invalid_queue_bounds_are_rejected_before_network(tmp_path, kwargs):
    root, _ = fixture(tmp_path)
    with pytest.raises(ValueError):
        queue.validate_all(root, tmp_path / "metadata.json", check_fn=lambda *_a, **_kw: pytest.fail("Network"), **kwargs)


def virtual_clock():
    """Advance pacing time without delaying concurrency-focused queue tests."""
    import threading
    value, lock = [0.0], threading.Lock()

    def now():
        with lock:
            return value[0]

    def sleep(seconds):
        with lock:
            value[0] += seconds

    return {"monotonic": now, "sleep": sleep}


def test_two_workers_claim_opposite_ends_and_meet_without_overlap(tmp_path):
    import threading
    for slug in ("a-first", "b-middle", "c-middle", "z-last"):
        root, _ = fixture(tmp_path, slug=slug, card=slug)
    barrier, calls, lock = threading.Barrier(2), [], threading.Lock()
    normal_check = checker(calls)

    def check(deck, **kwargs):
        with lock:
            first_pair = len(calls) < 2
        if first_pair:
            barrier.wait(timeout=5)
        return normal_check(deck, **kwargs)

    result = run(root, tmp_path, check, workers=2, **virtual_clock())
    assert result["errors"] == []
    assert result["checked"] == result["verified"] == 4
    assert result["pending"] == 0 and result["peak_in_flight"] == 2
    claims = result["commander_claims"]
    assert [(item["worker"], item["commander_slug"]) for item in claims[:2]] == [(1, "a-first"), (2, "z-last")]
    assert sorted(item["commander_slug"] for item in claims) == ["a-first", "b-middle", "c-middle", "z-last"]
    assert len(calls) == len(set(calls))


def test_parallel_worker_keeps_commander_through_repair_and_recheck(tmp_path):
    import threading
    root, report = fixture(tmp_path, slug="a-conflict", card="Bad Card")
    sibling = deepcopy(report["deck_analysis"]["packages"][0])
    sibling["generated_deck"]["cards"][-1]["name"] = "Sibling Card"
    report["deck_analysis"]["alternative_models"][0]["packages"].append(sibling)
    write(root / "a-conflict.json", report)
    for slug in ("b-middle", "c-middle", "z-last"):
        fixture(tmp_path, slug=slug, card=slug)
    events, threads, lock = [], {}, threading.Lock()
    normal_check = checker([])
    main_thread = threading.get_ident()

    def check(deck, **kwargs):
        name = deck["cards"][-1]["name"]
        with lock:
            events.append(("check", name, threading.get_ident()))
            threads.setdefault(name, set()).add(threading.get_ident())
        return normal_check(deck, **kwargs)

    def repair(*_args, **_kwargs):
        assert threading.get_ident() == main_thread
        events.append(("repair", "a-conflict", main_thread))
        saved = queue.read_json(root / "a-conflict.json")
        for _, package in queue.packages(saved):
            if package["generated_deck"]["cards"][-1]["name"] == "Bad Card":
                package["generated_deck"]["cards"][-1]["name"] = "Good Card"
        write(root / "a-conflict.json", saved)
        return {"regenerated": 1, "changed_deck_lists": 2}

    result = run(root, tmp_path, check, workers=2, rebuild_fn=repair, **virtual_clock())
    assert result["errors"] == [] and result["pending"] == result["conflicts"] == 0
    assert result["repaired_reports"] == 1
    owner = next(iter(threads["Bad Card"]))
    assert threads["Sibling Card"] == threads["Good Card"] == {owner}
    owner_events = [(kind, name) for kind, name, worker in events if worker == owner]
    assert owner_events.index(("check", "Good Card")) > owner_events.index(("check", "Bad Card"))
    for index, (kind, name) in enumerate(owner_events):
        if name in {"b-middle", "c-middle"}:
            assert index > owner_events.index(("check", "Good Card"))


def test_parallel_contexts_start_check_and_close_on_their_own_thread(tmp_path):
    import threading
    for slug in ("a-first", "z-last"):
        root, _ = fixture(tmp_path, slug=slug, card=slug)
    barrier, contexts = threading.Barrier(2), []

    class Context:
        def __enter__(self):
            self.owner, self.closed = threading.get_ident(), False
            contexts.append(self)
            return self

        def __exit__(self, *_args):
            assert threading.get_ident() == self.owner
            self.closed = True

    normal_check = checker([])

    def check(deck, **kwargs):
        assert kwargs["checker"].owner == threading.get_ident()
        barrier.wait(timeout=5)
        return normal_check(deck, **kwargs)

    result = run(root, tmp_path, check, workers=2, checker_factory=Context, **virtual_clock())
    assert result["errors"] == [] and result["checked"] == 2
    assert len({context.owner for context in contexts}) == 2
    assert all(context.closed for context in contexts)


def test_parallel_duplicate_families_share_one_exact_role_check(tmp_path):
    root, report = fixture(tmp_path, slug="a-first")
    report["commander_slug"] = "z-last"
    write(root / "z-last.json", report)
    calls = []
    result = run(root, tmp_path, checker(calls), workers=2, **virtual_clock())
    assert result["errors"] == []
    assert result["checked"] == result["verified"] == len(calls) == 1
    assert result["pending"] == 0
    for slug in ("a-first", "z-last"):
        current = queue.read_json(root / f"{slug}.json")
        assert current["deck_analysis"]["packages"][0]["generated_deck"]["validation"]["restriction_validation"]["status"] == "verified"


def test_outdated_inflight_result_cannot_verify_a_changed_deck(tmp_path):
    root, _ = fixture(tmp_path)
    old = queue.read_json(root / "commander.json")
    old_hash = cardlist_hash(old["deck_analysis"]["packages"][0]["generated_deck"]["cards"])

    def replace_while_checking():
        current = queue.read_json(root / "commander.json")
        for _, package in queue.packages(current):
            package["generated_deck"]["cards"][-1]["name"] = "Replacement Card"
        write(root / "commander.json", current)

    result = run(root, tmp_path, checker([], before=replace_while_checking), max_decks=1)
    current = queue.read_json(root / "commander.json")
    deck = current["deck_analysis"]["packages"][0]["generated_deck"]
    assert result["stale_results"] == result["pending"] == 1
    assert result["verified"] == 0
    assert deck["validation"]["restriction_validation"]["status"] == "pending"
    assert deck["validation"]["restriction_validation"]["deck_sha256"] != old_hash


def test_parallel_source_block_cancels_other_worker_before_its_check(tmp_path, monkeypatch):
    import threading
    from edhrec_affinity.build_styles import official_checker
    for slug in ("a-first", "z-last"):
        root, _ = fixture(tmp_path, slug=slug, card=slug)
    stopped, first_admission, lock = threading.Event(), [True], threading.Lock()

    class Gate:
        blocked, reason, retry_after_seconds = False, None, 0

        def __init__(self, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            with lock:
                first = first_admission[0]
                first_admission[0] = False
            if first:
                return True
            assert stopped.wait(timeout=5)
            return False

        def stop(self, reason, retry_after_seconds=0):
            self.blocked, self.reason, self.retry_after_seconds = True, reason, retry_after_seconds
            stopped.set()

    monkeypatch.setattr(official_checker, "SharedCheckAdmission", Gate)
    calls = []

    def blocked(deck, **_kwargs):
        calls.append(cardlist_hash(deck["cards"]))
        return {"status": "blocked", "source_blocked": True, "reason": "HTTP 429", "retry_after_seconds": 120000}

    result = run(root, tmp_path, blocked, workers=2)
    assert result["errors"] == [] and result["checked"] == len(calls) == 1
    assert result["source_blocked"] and result["pending"] == 2
    state = queue.read_json(root / "_collection/official-validation-queue.json")
    retry_at = datetime.fromisoformat(state["source_retry_at"])
    assert (retry_at - datetime.now(timezone.utc)).total_seconds() > 119000
    resumed = run(root, tmp_path, lambda *_args, **_kwargs: pytest.fail("Retried blocked source"), workers=2)
    assert resumed["checked"] == 0 and resumed["source_blocked"]


@pytest.mark.parametrize("workers", [0, 3, True, 1.5])
def test_invalid_worker_count_is_rejected_before_network(tmp_path, workers):
    root, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="workers"):
        run(root, tmp_path, lambda *_args, **_kwargs: pytest.fail("Network"), workers=workers)


def test_two_inflight_source_blocks_retain_longest_server_cooldown(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ALL_COMPLETED, wait as real_wait
    from edhrec_affinity.build_styles import official_checker
    for slug in ("a-first", "z-last"):
        root, _ = fixture(tmp_path, slug=slug, card=slug)
    barrier = threading.Barrier(2)
    # Both requests were admitted before either backend response arrived.
    monkeypatch.setattr(official_checker.SharedCheckAdmission, "acquire", lambda *_a, **_kw: True)
    monkeypatch.setattr(queue, "wait", lambda futures, **_kw:
        real_wait(futures, timeout=5, return_when=ALL_COMPLETED))

    def blocked(deck, **_kwargs):
        barrier.wait(timeout=5)
        delay = 172800 if deck["cards"][-1]["name"] == "a-first" else 90000
        return {"status": "blocked", "source_blocked": True, "reason": "HTTP 429",
                "retry_after_seconds": delay}

    result = run(root, tmp_path, blocked, workers=2)
    assert result["checked"] == 2 and result["source_blocked"] and result["errors"] == []
    state = queue.read_json(root / "_collection/official-validation-queue.json")
    retry_at = datetime.fromisoformat(state["source_retry_at"])
    assert (retry_at - datetime.now(timezone.utc)).total_seconds() > 172000


def test_interrupted_middle_family_resumes_before_new_endpoints(tmp_path):
    root, middle = fixture(tmp_path, slug="b-middle", card="Middle Card")
    alternative = deepcopy(middle["deck_analysis"]["alternative_models"][0]["packages"][0])
    alternative["generated_deck"]["cards"][-1]["name"] = "Middle Alternative"
    middle["deck_analysis"]["alternative_models"][0]["packages"].append(alternative)
    write(root / "b-middle.json", middle)
    for slug in ("a-first", "z-last"):
        fixture(tmp_path, slug=slug, card=slug)
    state_path = root / "_collection/official-validation-queue.json"
    write(state_path, {"schema_version": 1, "source_blocked": False, "check_attempts": {},
        "repair_attempts": {}, "worker_claims": {"1": "b-middle"},
        "family_order": ["a-first", "b-middle", "z-last"]})
    first_calls = []
    first = run(root, tmp_path, checker(first_calls), max_decks=1)
    assert first["commander_claims"][0]["commander_slug"] == "b-middle"
    assert first["commander_claims"][0]["resumed"] is True
    assert first["active_claims"] == {"1": "b-middle"}
    state = queue.read_json(state_path)
    assert state["worker_claims"] == {"1": "b-middle"}
    assert state["family_order"] == ["a-first", "b-middle", "z-last"]
    second_calls = []
    second = run(root, tmp_path, checker(second_calls), max_decks=1)
    assert second["commander_claims"][0]["commander_slug"] == "b-middle"
    assert second["commander_claims"][0]["resumed"] is True
    assert second_calls and second_calls != first_calls
    expected = validation_record_key(alternative["generated_deck"])
    assert second_calls == [expected]
    assert "b-middle" not in second["active_claims"].values()


def test_stale_deferred_and_duplicate_saved_claims_release_lanes(tmp_path):
    root, report = fixture(tmp_path, slug="a-first", card="First Card")
    fixture(tmp_path, slug="z-last", card="Last Card")
    state_path = root / "_collection/official-validation-queue.json"
    write(state_path, {"schema_version": 1, "source_blocked": False, "check_attempts": {},
        "repair_attempts": {}, "worker_claims": {"1": "removed-commander", "2": "a-first", "3": "a-first"},
        "family_order": ["removed-commander", "a-first", "a-first", "z-last"]})
    result = run(root, tmp_path, checker([]), workers=2)
    assert result["errors"] == [] and result["pending"] == 0
    assert result["active_claims"] == {}
    assert sum(claim["commander_slug"] == "a-first" for claim in result["commander_claims"]) == 1
    assert queue.read_json(state_path)["family_order"] == ["a-first", "z-last"]


def test_fresh_conflict_priority_replaces_old_order_after_owned_family(tmp_path):
    root, _ = fixture(tmp_path, slug="a-safe", card="Safe Card")
    _, middle = fixture(tmp_path, slug="b-owned", card="Middle Card")
    _, conflict = fixture(tmp_path, slug="z-conflict", card="Bad Card")
    deck = conflict["deck_analysis"]["packages"][0]["generated_deck"]
    record_external_validation(deck, observation(deck, conflict=True), root / "_collection/external-validations")
    state_path = root / "_collection/official-validation-queue.json"
    write(state_path, {"schema_version": 1, "source_blocked": False, "check_attempts": {},
        "repair_attempts": {}, "worker_claims": {"1": "b-owned"},
        "family_order": ["a-safe", "b-owned", "z-conflict"]})
    repairs, calls = [], []

    def unchanged(*_args, **kwargs):
        repairs.extend(kwargs["commanders"])
        return {"regenerated": 1, "changed_deck_lists": 0}

    result = run(root, tmp_path, checker(calls), max_decks=1, rebuild_fn=unchanged)
    assert result["errors"] == []
    assert calls == [validation_record_key(middle["deck_analysis"]["packages"][0]["generated_deck"])]
    assert repairs == ["z-conflict"]
    claims = result["commander_claims"]
    assert [(item["commander_slug"], item["resumed"]) for item in claims[:2]] == [
        ("b-owned", True), ("z-conflict", False)]
    assert queue.read_json(state_path)["family_order"][0] == "z-conflict"


def test_repair_joining_an_inflight_identity_publishes_both_families(tmp_path):
    import threading
    root, _ = fixture(tmp_path, slug="a-repaired", card="Bad Card")
    fixture(tmp_path, slug="z-checking", card="Good Card")
    good_started, repaired = threading.Event(), threading.Event()
    calls = []
    normal_check = checker(calls)

    def check(deck, **kwargs):
        if deck["cards"][-1]["name"] == "Good Card":
            good_started.set()
            assert repaired.wait(timeout=5)
        return normal_check(deck, **kwargs)

    def repair(*_args, **kwargs):
        assert kwargs["commanders"] == ["a-repaired"]
        assert good_started.wait(timeout=5)
        report = queue.read_json(root / "a-repaired.json")
        for _, package in queue.packages(report):
            package["generated_deck"]["cards"][-1]["name"] = "Good Card"
        write(root / "a-repaired.json", report)
        repaired.set()
        return {"regenerated": 1, "changed_deck_lists": 2}

    result = run(root, tmp_path, check, workers=2, rebuild_fn=repair, **virtual_clock())
    assert result["errors"] == [] and result["checked"] == 2
    assert len(calls) == len(set(calls)) == 2
    assert result["pending"] == result["conflicts"] == 0
    for slug in ("a-repaired", "z-checking"):
        report = queue.read_json(root / f"{slug}.json")
        for _, package in queue.packages(report):
            validation = package["generated_deck"]["validation"]
            assert validation["restriction_validation"]["status"] == "verified"
            assert validation["external_rating"]["status"] == "verified"


def test_successful_final_check_resets_lineage_for_an_independent_conflict(tmp_path):
    root, _ = fixture(tmp_path, card="Bad Card")
    state_path = root / "_collection/official-validation-queue.json"
    write(state_path, {"schema_version": 1, "source_blocked": False, "check_attempts": {},
        "repair_attempts": {"commander": {"attempts": 1, "started_at": queue._now(),
            "generation_version": queue.GENERATION_VERSION}},
        "repair_retry_at": {"commander": "2000-01-01T00:00:00Z"}})
    repairs = []

    def repair(*_args, **kwargs):
        repairs.extend(kwargs["commanders"])
        report = queue.read_json(root / "commander.json")
        for _, package in queue.packages(report):
            package["generated_deck"]["cards"][-1]["name"] = "Good Card"
        write(root / "commander.json", report)
        return {"regenerated": 1, "changed_deck_lists": 2}

    first = run(root, tmp_path, checker([]), rebuild_fn=repair, max_decks=2)
    assert first["errors"] == [] and first["checked"] == 2
    assert first["pending"] == first["conflicts"] == 0
    state = queue.read_json(state_path)
    assert "commander" not in state["repair_attempts"]
    assert "commander" not in state["repair_retry_at"]
    # A later independent list change reintroduces a known conflict. Fresh
    # observations are reusable, but an old exhausted lineage cannot block it.
    report = queue.read_json(root / "commander.json")
    for _, package in queue.packages(report):
        package["generated_deck"]["cards"][-1]["name"] = "Bad Card"
    write(root / "commander.json", report)
    second = run(root, tmp_path, lambda *_args, **_kwargs: pytest.fail("Rechecked fresh exact evidence"),
                 rebuild_fn=repair)
    assert second["errors"] == [] and second["repaired_reports"] == 1
    assert second["pending"] == second["conflicts"] == 0
    assert repairs == ["commander", "commander"]
    assert queue.read_json(state_path)["repair_attempts"] == {}
