"""Offline deck upgrades preserve learned models and report honest blockers."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import httpx

from edhrec_affinity.build_styles.card_metadata import MetadataRepository
from edhrec_affinity.build_styles.collection import deck_dataset_sha256
from edhrec_affinity.build_styles.external_validation import cardlist_hash, record_external_validation
from edhrec_affinity.build_styles.sources import CommanderAliasCatalog, card_key


ROOT = Path(__file__).resolve().parents[1]
# Finite runs use spawn even for one CPU worker. Make the script importable in
# those real child interpreters, just as it is when launched through its CLI.
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("rebuild_generated_decks", ROOT / "scripts/rebuild_generated_decks.py")
rebuild = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rebuild
spec.loader.exec_module(rebuild)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8") as handle:
        json.dump(value, handle)


def fixture(tmp_path, slug="commander"):
    now = datetime.now(timezone.utc).isoformat()
    names = [f"Core {index}" for index in range(62)] + ["Weak Card", "Consensus Card", "Forest", "Commander"]
    metadata = {}
    for name in names:
        metadata[card_key(name)] = {
            "name": name, "type_line": "Basic Land — Forest" if name == "Forest" else
            "Legendary Creature — Fungus" if name == "Commander" else "Creature — Plant",
            "oracle_text": "{T}: Add {G}." if name == "Forest" else "", "color_identity": ["G"],
            "legalities": {"commander": "legal"}, "cmc": 0 if name == "Forest" else 2,
            "game_changer": False, "keywords": [], "layout": "normal", "mana_cost": "",
            "card_faces": [], "produced_mana": ["G"] if name == "Forest" else [],
            "oracle_id": f"oracle-{name}", "checked_at": now,
            "colors": ["G"], "edhrec_rank": 1000, "reserved": False,
            "prices": {"usd": ".15", "usd_foil": None, "usd_etched": None},
        }
    decks = []
    for identifier, choice in [("a", "Weak Card"), ("b", "Consensus Card"), ("c", "Consensus Card")]:
        decks.append({"id": identifier, "commanders": ["Commander"], "source_bracket": 3,
                      "cards": [{"name": f"Core {index}", "quantity": 1} for index in range(62)]
                      + [{"name": choice, "quantity": 1}, {"name": "Forest", "quantity": 36}]})
    package = {"id": 0, "name": "Package 1", "top_cards": [{"name": "Consensus Card"}],
               "tag_suggestions": [{"tag_slug": "plants", "tag_name": "Plants"}],
               "generated_deck": {"status": "generated", "card_count": 100,
                                  "cards": [{"name": "Commander", "quantity": 1}, *deepcopy(decks[0]["cards"])],
                                  "primer": "Old primer", "validation": {"external_rating": {"status": "verified", "playability_pct": 99}}}}
    memberships = [{"id": deck["id"], "weights": [1.0 if deck["id"] == "a" else .8]} for deck in decks]
    report = {"schema_version": 1, "commander_slug": slug, "commander_name": "Commander",
              "generated_at": "2026-10-20T00:00:00Z", "total_edhrec_decks": 200,
              "collection": {"complete": True, "analysis_current": True, "source_verified_at": "2026-10-19T00:00:00Z",
                             "dataset_sha256": deck_dataset_sha256(decks), "target_mode": "all", "previews_loaded": 3},
              "profiles": {"kept": "as learned"},
              "deck_analysis": {"status": "fitted", "input_decks": 3, "unique_decks": 3, "seed_consistency": .99,
                                "decks": [{"id": row["id"], "memberships": row["weights"]} for row in memberships],
                                "packages": [package], "alternative_models": [{"components": 1,
                                    "packages": [deepcopy(package)], "memberships": memberships}]},
              "deck_generation": {"version": 1, "status": "complete", "ranking_pages": [
                  {"tag_slug": "plants", "tag_name": "Plants"}, {"tag_slug": "bracket-3", "tag_name": "Bracket 3"}]}}
    profiles = [{"tag_slug": "plants", "tag_name": "Plants", "cards": {"consensus card": {"rate": .8, "lift": 2}}},
                {"tag_slug": "bracket-3", "tag_name": "Bracket 3", "bracket": 3, "cards": {}}]
    inputs = {"base": {"commander_names": ["Commander"], "commander_name_groups": [["Commander"]], "deck_count": 200},
              "decks": decks, "profiles": profiles, "metadata": {}, "dataset_sha256": deck_dataset_sha256(decks),
              "source": "saved_analysis_evidence"}
    cache = tmp_path / "metadata.json"
    write(cache, {"schema_version": 1, "updated_at": now, "cards": metadata})
    return report, inputs, cache


def save_evidence(root, report, inputs):
    write(root / f"{report['commander_slug']}.json", report)
    write(root / "_collection/evidence" / f"{report['commander_slug']}.json.gz", {
        "slug": report["commander_slug"], "base": inputs["base"], "ranking_profiles": inputs["profiles"],
        "decks": inputs["decks"], "metadata": inputs["metadata"]})


def hanging_report(job):
    """An actual child that never finishes its CPU work within the budget."""
    root = Path(job["root"])
    (root / f"{Path(job['path']).stem}.started").write_text(str(os.getpid()))
    time.sleep(60)
    raise AssertionError("Hung child should have been stopped")


def hanging_prefetch(job):
    """Represent a stalled HTTP library in the sole metadata process."""
    (Path(job["root"]) / "metadata.started").write_text(str(os.getpid()))
    time.sleep(60)
    raise AssertionError("Hung metadata child should have been stopped")


def test_rebuild_all_models_keeps_learned_inputs_and_invalidates_changed_list_rating(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    monkeypatch.setattr("httpx.Client", lambda **_: pytest.fail("Offline rebuild tried to start HTTP"))
    result, summary = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False,
                                            validation_directory=tmp_path / "ratings")
    assert result["generated_at"] == report["generated_at"]
    assert result["collection"] == report["collection"]
    assert result["profiles"] == report["profiles"]
    assert rebuild.learned_analysis(result["deck_analysis"]) == rebuild.learned_analysis(report["deck_analysis"])
    assert result["deck_generation"]["version"] == rebuild.GENERATION_VERSION
    assert result["deck_generation"]["refitted"] is False
    assert len(summary["packages"]) == 2
    assert all(package["before"]["omitted_count"] == 1 for package in summary["packages"])
    for _, package in rebuild.packages(result):
        generated = package["generated_deck"]
        assert generated["status"] == "generated"
        assert generated["card_count"] == 100
        assert generated["validation"]["external_rating"]["status"] == "unverified"
        assert {card["name"] for card in generated["cards"]} >= {"Consensus Card", "Commander"}


def test_saved_evidence_dataset_must_match_learned_report(tmp_path):
    report, inputs, _ = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    loaded = rebuild.load_inputs(root, report, CommanderAliasCatalog(root / "_collection/sources"))
    assert loaded["decks"] == inputs["decks"]
    report["collection"]["dataset_sha256"] = "different-snapshot"
    with pytest.raises(rebuild.RebuildBlocked, match="different dataset"):
        rebuild.load_inputs(root, report, CommanderAliasCatalog(root / "_collection/sources"))


def test_analysis_dataset_hash_takes_precedence_during_partial_refresh(tmp_path):
    report, inputs, _ = fixture(tmp_path)
    report["collection"]["analysis_dataset_sha256"] = report["collection"]["dataset_sha256"]
    report["collection"]["dataset_sha256"] = "new-partial-collection"
    assert rebuild._check_decks(report, inputs["decks"]) == inputs["dataset_sha256"]


def test_recover_completed_inputs_from_parsed_previews_and_exact_selected_ranking_pages(tmp_path):
    report, inputs, _ = fixture(tmp_path)
    root = tmp_path / "build-styles"
    write(root / "_collection/previews/commander.json.gz", {"schema_version": 1, "commander_slug": "commander",
        "frame": [{"urlhash": deck["id"]} for deck in inputs["decks"]],
        "results": {deck["id"]: {"deck": deck} for deck in inputs["decks"]}})
    payload = {"container": {"json_dict": {"card": {"name": "Commander", "num_decks": 200,
                "url": "/commanders/commander"}, "cardlists": [{"tag": "creatures", "cardviews": [
                    {"name": "Consensus Card", "num_decks": 100, "potential_decks": 200, "lift": 2}]}]}}}
    for name in ("base", "plants", "bracket-3", "incidental-unselected-page"):
        write(root / "_collection/sources/commander" / f"{name}.json", payload)
    loaded = rebuild.load_inputs(root, report, CommanderAliasCatalog(root / "_collection/sources"))
    assert loaded["dataset_sha256"] == inputs["dataset_sha256"]
    assert [profile["tag_slug"] for profile in loaded["profiles"]] == ["plants", "bracket-3"]
    assert loaded["profiles"][1]["bracket"] == 3
    assert loaded["base"]["commander_names"] == ["Commander"]
    assert loaded["source"] == "saved_source_profiles_and_parsed_previews"
    (root / "_collection/sources/commander/plants.json").unlink()
    with pytest.raises(rebuild.RebuildBlocked, match="ranking page is missing"):
        rebuild.load_inputs(root, report, CommanderAliasCatalog(root / "_collection/sources"))


def test_rebuild_writes_atomic_report_backup_and_complete_audit_summary(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    original = (root / "commander.json").read_bytes()
    summary = rebuild.rebuild_all(root, cache, workers=1, prefer_playability=False,
                                  validation_directory=tmp_path / "ratings")
    assert summary["rebuilt"] == summary["regenerated"] == 1
    assert summary["blocked"] == summary["pending"] == 0
    assert summary["changed_deck_lists"] == 2
    assert summary["distinctive_omissions_before"] == 2
    assert summary["distinctive_omissions_after"] == 0
    assert Path(summary["reports"][0]["backup"]).read_bytes() == original
    assert rebuild.read_json(root / "commander.json")["deck_generation"]["version"] == rebuild.GENERATION_VERSION
    assert rebuild.read_json(root / "_collection/deck-rebuild-summary.json")["completed_at"]
    assert not list(root.rglob("*.tmp"))
    again = rebuild.rebuild_all(root, cache, only_outdated=True)
    assert again["rebuilt"] == 0 and again["skipped"] == 1
    assert again["reports"][0]["reason"] == "already_current_generation"


def test_missing_required_metadata_preserves_original_and_records_pending_blocker(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    original = (root / "commander.json").read_bytes()
    records = rebuild.read_json(cache)
    del records["cards"]["commander"]
    write(cache, records)
    summary = rebuild.rebuild_all(root, cache, prefer_playability=False)
    assert summary["blocked"] == summary["pending"] == 1
    assert "commander/existing-deck cards" in summary["blockers"][0]["reason"]
    assert (root / "commander.json").read_bytes() == original
    assert not (Path(summary["backup_dir"]) / "commander.json").exists()


def test_missing_unused_pool_metadata_does_not_prevent_generation(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    inputs["decks"].append({"id": "unused", "commanders": ["Commander"],
                            "cards": [{"name": "Unknown card", "quantity": 99}]})
    result, summary = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    assert result["deck_generation"]["metadata"]["missing"] == ["Unknown card"]
    assert all(package["status_after"] == "generated" for package in summary["packages"])


def test_dry_run_leaves_report_bytes_and_backups_untouched(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    original = (root / "commander.json").read_bytes()
    summary = rebuild.rebuild_all(root, cache, dry_run=True, prefer_playability=False)
    assert summary["rebuilt"] == summary["pending"] == 1
    assert summary["reports"][0]["dry_run"]
    assert (root / "commander.json").read_bytes() == original
    assert not Path(summary["backup_dir"]).exists()


def test_generated_regression_is_blocked_and_old_report_is_preserved(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    def remove(analysis, *_, **__):
        analysis["packages"][0]["generated_deck"] = {"status": "unavailable", "reason": "No safe anchor"}
    monkeypatch.setattr(rebuild, "generate_package_decks", remove)
    with pytest.raises(rebuild.RebuildBlocked, match="would remove an existing deck"):
        rebuild.rebuild_report(report, inputs, MetadataRepository(cache))
    assert report["deck_analysis"]["packages"][0]["generated_deck"]["status"] == "generated"


def test_unavailable_export_without_inputs_is_reported_as_blocked(tmp_path):
    report, _, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    write(root / "commander.json", report)
    result = rebuild.rebuild_all(root, cache)
    assert result["blocked"] == 1
    assert "website reports alone cannot regenerate" in result["blockers"][0]["reason"]


def test_runtime_limit_leaves_unscheduled_eligible_reports_pending(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    calls = iter([0, 100])
    monkeypatch.setattr(rebuild.time, "monotonic", lambda: next(calls))
    result = rebuild.rebuild_all(root, cache, max_runtime_seconds=1)
    assert result["rebuilt"] == 0
    assert result["time_budget_reached"]
    assert result["pending"] == 1


@pytest.mark.parametrize("workers", [1, 2])
def test_runtime_stops_real_hung_cpu_children_preserves_reports_and_resumes(tmp_path, monkeypatch, workers):
    report, inputs, cache = fixture(tmp_path, "commander-a")
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    report["commander_slug"] = "commander-b"
    save_evidence(root, report, inputs)
    originals = {path.name: path.read_bytes() for path in root.glob("*.json")}
    process = rebuild.process_report
    monkeypatch.setattr(rebuild, "process_report", hanging_report)
    started = time.monotonic()
    result = rebuild.rebuild_all(root, cache, workers=workers, max_runtime_seconds=3,
                                 only_outdated=True, prefer_playability=False)
    assert time.monotonic() - started < 8
    assert result["time_budget_reached"] and result["pending"] == 2
    assert result["blocked"] == result["regenerated"] == 0
    assert {path.name: path.read_bytes() for path in root.glob("*.json")} == originals
    assert not Path(result["backup_dir"]).exists()
    markers = list(root.glob("*.started"))
    assert markers, "The deadline must stop running CPU work, not just skip submission"
    for marker in markers:
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    monkeypatch.setattr(rebuild, "process_report", process)
    resumed = rebuild.rebuild_all(root, cache, only_outdated=True, prefer_playability=False)
    assert resumed["regenerated"] == 2
    assert resumed["pending"] == resumed["blocked"] == 0
    assert all(rebuild.read_json(root / name)["collection"] == report["collection"] for name in originals)


def test_runtime_stops_real_hung_metadata_stream_without_starting_cpu_work(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    original, original_cache = (root / "commander.json").read_bytes(), cache.read_bytes()
    monkeypatch.setattr(rebuild, "prefetch_metadata_job", hanging_prefetch)
    started = time.monotonic()
    result = rebuild.rebuild_all(root, cache, workers=2, fetch_metadata=True,
                                 max_runtime_seconds=3, only_outdated=True)
    assert time.monotonic() - started < 8
    assert result["time_budget_reached"] and result["pending"] == 1
    assert result["metadata_prefetch"]["status"] == "time_budget_reached"
    assert result["blocked"] == result["regenerated"] == 0
    assert (root / "commander.json").read_bytes() == original
    assert cache.read_bytes() == original_cache
    marker = root / "metadata.started"
    assert marker.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_bounded_metadata_prefetch_completes_then_commits_offline_worker_output(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    original_cache = cache.read_bytes()
    result = rebuild.rebuild_all(root, cache, workers=1, fetch_metadata=True,
                                 max_runtime_seconds=15, only_outdated=True, prefer_playability=False)
    assert not result["time_budget_reached"]
    assert result["pending"] == result["blocked"] == 0
    assert result["regenerated"] == 1
    assert result["metadata_prefetch"]["status"] == "complete"
    assert result["metadata_prefetch"]["requests"] == 0
    assert cache.read_bytes() == original_cache
    generated = rebuild.read_json(root / "commander.json")
    assert generated["collection"] == report["collection"]
    assert rebuild.learned_analysis(generated["deck_analysis"]) == rebuild.learned_analysis(report["deck_analysis"])
    assert generated["deck_generation"]["version"] == rebuild.GENERATION_VERSION


def test_below_threshold_and_insufficient_reports_are_not_pending(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    report["total_edhrec_decks"] = 199
    write(root / "commander.json", report)
    report = deepcopy(report)
    report["commander_slug"] = "insufficient"
    report["total_edhrec_decks"] = 200
    report["deck_analysis"]["status"] = "insufficient_data"
    write(root / "insufficient.json", report)
    result = rebuild.rebuild_all(root, cache)
    assert result["skipped"] == 2 and result["pending"] == 0


def test_stale_observations_are_not_made_fresh_by_an_offline_rebuild(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    records = rebuild.read_json(cache)
    observed = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    for card in records["cards"].values():
        card["checked_at"] = observed
    write(cache, records)
    original = deepcopy(report)
    with pytest.raises(rebuild.RebuildBlocked, match="Current verified metadata is required"):
        rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    assert report == original


def test_cli_process_workers_complete_and_persist_reports(tmp_path):
    report, inputs, cache = fixture(tmp_path, "commander-a")
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    report["commander_slug"] = "commander-b"
    save_evidence(root, report, inputs)
    completed = subprocess.run([sys.executable, str(ROOT / "scripts/rebuild_generated_decks.py"),
        "--build-styles-dir", str(root), "--card-metadata-cache", str(cache), "--workers", "2", "--single-anchor"],
        cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = rebuild.read_json(root / "_collection/deck-rebuild-summary.json")
    assert summary["rebuilt"] == 2 and summary["pending"] == summary["blocked"] == 0
    assert {entry["commander_slug"] for entry in summary["reports"]} == {"commander-a", "commander-b"}


def test_parent_prefetch_refreshes_stale_metadata_before_offline_workers(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    records = rebuild.read_json(cache)
    for card in records["cards"].values():
        card["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    write(cache, records)
    requests = []
    parent_pid = os.getpid()
    client = httpx.Client
    workers_started = False

    def handler(request):
        assert os.getpid() == parent_pid and not workers_started
        assert request.method == "POST" and str(request.url) == rebuild.COLLECTION_URL
        names = [row["name"] for row in json.loads(request.content)["identifiers"]]
        requests.append(names)
        return httpx.Response(200, json={"object": "list", "has_more": False,
            "data": [records["cards"][card_key(name)] for name in names], "not_found": []})

    monkeypatch.setattr("httpx.Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr("edhrec_affinity.build_styles.card_metadata.time.sleep", lambda _: None)
    initialize = rebuild.initialize_worker
    def start(*args):
        nonlocal workers_started
        workers_started = True
        initialize(*args)
    monkeypatch.setattr(rebuild, "initialize_worker", start)
    result = rebuild.rebuild_all(root, cache, fetch_metadata=True, prefer_playability=False)
    assert len(requests) == 1
    assert result["regenerated"] == 1 and result["pending"] == 0
    assert result["network_enabled"]
    assert result["metadata_prefetch"]["requests"] == 1
    assert result["metadata_prefetch"]["status"] == "complete"
    assert result["metadata_prefetch"]["edhrec_requests"] == 0
    generated = rebuild.read_json(root / "commander.json")["deck_analysis"]["packages"][0]["generated_deck"]
    assert generated["validation"]["checks"]["metadata_fresh"]


def test_dry_run_fetch_flag_does_not_start_http_or_change_metadata_cache(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    original = cache.read_bytes()
    monkeypatch.setattr("httpx.Client", lambda **_: pytest.fail("Dry run started HTTP"))
    result = rebuild.rebuild_all(root, cache, dry_run=True, fetch_metadata=True, prefer_playability=False)
    assert not result["network_enabled"]
    assert result["metadata_prefetch"]["status"] == "skipped_dry_run"
    assert cache.read_bytes() == original


def test_prefetch_deadline_is_checked_between_small_paced_batches(tmp_path, monkeypatch):
    report, inputs, _ = fixture(tmp_path)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    monkeypatch.setattr(rebuild, "requested_metadata_names", lambda *_: [f"Card {index}" for index in range(151)])
    calls = []
    class Repository:
        def load(self, names, **options):
            assert options["fetch"]
            calls.append(names)
            return {}, {"requests": 1, "fetched": len(names), "resolved": len(names)}
    time_checks = iter([True, True, False])
    summary = {"metadata_prefetch": {}}
    rebuild.prefetch_metadata(root, [root / "commander.json"], Repository(), summary, only_outdated=True,
                              time_remains=lambda: next(time_checks), checkpoint=lambda: None)
    assert len(calls) == 1 and len(calls[0]) == 75
    assert summary["metadata_prefetch"]["status"] == "time_budget_reached"
    assert summary["metadata_prefetch"]["processed"] == 75
    assert summary["metadata_prefetch"]["requested"] == 151


def test_current_version_with_failed_freshness_remains_retryable(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    root = tmp_path / "build-styles"
    report["deck_generation"]["version"] = rebuild.GENERATION_VERSION
    for _, package in rebuild.packages(report):
        package["generated_deck"]["validation"]["checks"] = {"metadata_fresh": False}
    save_evidence(root, report, inputs)
    assert rebuild.needs_rebuild(report)
    result = rebuild.rebuild_all(root, cache, only_outdated=True, prefer_playability=False)
    assert result["regenerated"] == 1 and result["pending"] == 0
    assert not rebuild.needs_rebuild(rebuild.read_json(root / "commander.json"))


@pytest.mark.parametrize("mode", ["missing", "formula", "hash", "algorithm", "score_bracket", "input_hash"])
def test_current_generation_retries_missing_or_invalid_local_power_proof(tmp_path, mode):
    report, inputs, cache = fixture(tmp_path)
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    assert not rebuild.needs_rebuild(report)
    validation = report["deck_analysis"]["packages"][0]["generated_deck"]["validation"]
    if mode == "missing":
        validation.pop("calculated_power")
    else:
        power = validation["calculated_power"]
        if mode == "formula":
            power["power_formula_version"] -= 1
        elif mode == "hash":
            power["deck_sha256"] = "0" * 64
        elif mode == "algorithm":
            power["algorithm_sha256"] = "0" * 64
        elif mode == "score_bracket":
            power["score_bracket"] = 5
        else:
            power["input_sha256"] = "incomplete"
    assert rebuild.needs_rebuild(report)


def test_current_generation_expires_metadata_dates_and_independent_power_dates(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    assert not rebuild.needs_rebuild(report)
    validation = report["deck_analysis"]["packages"][0]["generated_deck"]["validation"]
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    validation["metadata"]["oldest_checked_at"] = old
    assert validation["checks"]["metadata_fresh"]  # A historical true flag is insufficient.
    assert rebuild.needs_rebuild(report)
    assert not rebuild.needs_rebuild(report, max_age_days=5)
    validation["metadata"]["oldest_checked_at"] = datetime.now(timezone.utc).isoformat()
    validation["calculated_power"]["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert rebuild.needs_rebuild(report, max_age_days=10)


def test_explicit_unavailable_local_power_does_not_busy_loop_while_metadata_is_fresh(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    validation = report["deck_analysis"]["packages"][0]["generated_deck"]["validation"]
    power = validation["calculated_power"]
    power["status"] = "unavailable"
    for key in ("power_level", "score_bracket", "checked_at", "metadata_checked_at", "input_sha256"):
        power.pop(key)
    power["reason"] = "No paper printing with a usable USD price was found."
    assert not rebuild.needs_rebuild(report)
    validation["metadata"]["oldest_checked_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    assert rebuild.needs_rebuild(report)


def test_metadata_age_option_reaches_selection_and_worker_retry_for_current_generation(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    for _, package in rebuild.packages(report):
        package["generated_deck"]["validation"]["metadata"]["oldest_checked_at"] = (
            datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    default_age = rebuild.rebuild_all(root, cache, only_outdated=True, prefer_playability=False)
    assert default_age["skipped"] == 1 and default_age["regenerated"] == 0
    shorter_age = rebuild.rebuild_all(root, cache, max_age_days=1, only_outdated=True, prefer_playability=False)
    assert shorter_age["regenerated"] == 1 and shorter_age["pending"] == shorter_age["blocked"] == 0
    assert not rebuild.needs_rebuild(rebuild.read_json(root / "commander.json"), max_age_days=1)


def checked_score(report, directory, **changes):
    deck = report["deck_analysis"]["packages"][0]["generated_deck"]
    values = {"source": "edhpowerlevel.com", "checked_at": datetime.now(timezone.utc).isoformat(),
              "card_count": 100, "source_url": "https://edhpowerlevel.com/?d=observed-deck",
              "power_level": 6.09, "playability_pct": 70, "bracket": 3, **changes}
    return record_external_validation(deck, values, directory)


def test_current_generation_retries_new_exact_score_once_and_rechecks_changed_evidence(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    ratings = tmp_path / "ratings"
    root = tmp_path / "build-styles"
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache),
                                        validation_directory=ratings, prefer_playability=False)
    original = deepcopy(report)
    assert not rebuild.needs_rebuild(report, validation_directory=ratings)
    observation = checked_score(report, ratings)
    # A freshly recorded score must wake an otherwise current v3 report. The
    # canonical record, not a stale embedded external_rating, drives selection.
    assert rebuild.needs_rebuild(original, validation_directory=ratings)
    assert not rebuild.needs_rebuild(original, validation_directory=tmp_path / "empty")
    save_evidence(root, original, inputs)
    first = rebuild.rebuild_all(root, cache, only_outdated=True, prefer_playability=False,
                                 validation_directory=ratings)
    assert first["regenerated"] == 1 and first["pending"] == 0
    reviewed = rebuild.read_json(root / "commander.json")
    deck = reviewed["deck_analysis"]["packages"][0]["generated_deck"]
    assert deck["statistics"]["score_bracket_repair"]["status"] == "blocked"
    assert cardlist_hash(deck["cards"]) == observation["deck_sha256"]
    assert not rebuild.needs_rebuild(reviewed, validation_directory=ratings)
    second = rebuild.rebuild_all(root, cache, only_outdated=True, prefer_playability=False,
                                  validation_directory=ratings)
    assert second["regenerated"] == 0 and second["skipped"] == 1 and second["pending"] == 0

    # The same numeric score with new observed card restrictions is actionable.
    # The configured directory must reach generation itself, not just scheduling.
    checked_score(reviewed, ratings, checked_at=observation["checked_at"],
                  bracket_restrictions={"mass_land_denial": ["Consensus Card"]})
    assert rebuild.needs_rebuild(reviewed, validation_directory=ratings)
    third = rebuild.rebuild_all(root, cache, only_outdated=True, prefer_playability=False,
                                 validation_directory=ratings)
    assert third["regenerated"] == 1
    repaired = rebuild.read_json(root / "commander.json")
    deck = repaired["deck_analysis"]["packages"][0]["generated_deck"]
    assert deck["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert "Consensus Card" not in {row["name"] for row in deck["cards"]}
    assert deck["validation"]["external_rating"]["status"] == "unverified"
    assert not rebuild.needs_rebuild(repaired, validation_directory=ratings)
    assert len(repaired["deck_generation"]["score_repair_observations"]) <= 2 * len(list(rebuild.packages(repaired)))


@pytest.mark.parametrize("change", [
    {"power_level": "6–7"}, {"power_level": "10+"}, {"power_level": -1},
    {"power_level": 6.7}, {"power_level": 7.0},
    {"checked_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()},
])
def test_non_actionable_or_stale_canonical_scores_do_not_retry_current_generation(tmp_path, change):
    report, inputs, cache = fixture(tmp_path)
    ratings = tmp_path / "ratings"
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache),
                                        validation_directory=ratings, prefer_playability=False)
    checked_score(report, ratings, **change)
    assert not rebuild.needs_rebuild(report, validation_directory=ratings)


def test_retry_fingerprint_tracks_rechecked_score_policy_and_current_card_hash(tmp_path, monkeypatch):
    report, inputs, cache = fixture(tmp_path)
    ratings = tmp_path / "ratings"
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache),
                                        validation_directory=ratings, prefer_playability=False)
    observation = checked_score(report, ratings)
    report["deck_generation"]["score_repair_observations"] = rebuild.score_repair_observations(report, ratings)
    assert not rebuild.needs_rebuild(report, validation_directory=ratings)
    checked_score(report, ratings, checked_at=observation["checked_at"], power_level=6.1)
    assert rebuild.needs_rebuild(report, validation_directory=ratings)
    report["deck_generation"]["score_repair_observations"] = rebuild.score_repair_observations(report, ratings)
    checked_score(report, ratings, checked_at=(datetime.fromisoformat(observation["checked_at"]) + timedelta(seconds=1)).isoformat(),
                  power_level=6.1)
    assert rebuild.needs_rebuild(report, validation_directory=ratings)
    report["deck_generation"]["score_repair_observations"] = rebuild.score_repair_observations(report, ratings)
    monkeypatch.setattr(rebuild, "POWER_SCORE_BRACKET_POLICY_VERSION", rebuild.POWER_SCORE_BRACKET_POLICY_VERSION + 1)
    assert rebuild.needs_rebuild(report, validation_directory=ratings)
    # A record for the former list cannot schedule repairs for a changed list.
    for _, package in rebuild.packages(report):
        package["generated_deck"]["cards"][-1]["name"] = "Different Exact Card"
    assert not rebuild.score_repair_observations(report, ratings)
    # The former external observation cannot transfer, but the changed list
    # independently requires its own hash-bound local power calculation.
    assert rebuild.needs_rebuild(report, validation_directory=ratings)


def test_native_bracket_mismatch_schedules_current_report_and_metadata_prefetch(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    ratings = tmp_path / "ratings"
    report, _ = rebuild.rebuild_report(report, inputs, MetadataRepository(cache),
                                        validation_directory=ratings, prefer_playability=False)
    for _, package in rebuild.packages(report):
        package["generated_deck"]["validation"]["bracket"]["recommended"] = 3
    # The external bracket already agrees with its score, but current local
    # restrictions can still reveal a conflicting higher construction target.
    checked_score(report, ratings, bracket=2)
    assert rebuild.needs_rebuild(report, validation_directory=ratings)
    root = tmp_path / "build-styles"
    save_evidence(root, report, inputs)
    calls = []

    class Repository:
        def load(self, names, **options):
            assert options["fetch"]
            calls.append(names)
            return {}, {"requests": 1, "fetched": len(names), "resolved": len(names)}

    summary = {"metadata_prefetch": {}}
    rebuild.prefetch_metadata(root, [root / "commander.json"], Repository(), summary,
                              only_outdated=True, validation_directory=ratings,
                              time_remains=lambda: True, checkpoint=lambda: None)
    assert calls and summary["metadata_prefetch"]["status"] == "complete"
    # Persisted verified labels are not trusted after the canonical file goes.
    (ratings / f"{cardlist_hash(report['deck_analysis']['packages'][0]['generated_deck']['cards'])}.json").unlink()
    assert report["deck_analysis"]["packages"][0]["generated_deck"]["validation"]["external_rating"]["status"] == "verified"
    assert not rebuild.needs_rebuild(report, validation_directory=ratings)


def test_stale_unused_pool_card_does_not_block_current_generated_lists(tmp_path):
    report, inputs, cache = fixture(tmp_path)
    records = rebuild.read_json(cache)
    records["cards"]["unused"] = {**records["cards"]["weak card"], "name": "Unused", "oracle_id": "unused",
        "checked_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()}
    write(cache, records)
    inputs["decks"].append({"id": "unused", "commanders": ["Commander"],
                            "cards": [{"name": "Unused", "quantity": 99}]})
    result, summary = rebuild.rebuild_report(report, inputs, MetadataRepository(cache), prefer_playability=False)
    assert summary["metadata_stale"] == 1
    assert result["deck_generation"]["status"] == "complete"
    for _, package in rebuild.packages(result):
        assert package["generated_deck"]["validation"]["checks"]["metadata_fresh"]
        assert package["generated_deck"]["validation"]["metadata"]["stale_cached"] == 0


def test_current_discovery_scope_excludes_archival_reports_from_pending(tmp_path):
    report, inputs, cache = fixture(tmp_path, "current")
    root = tmp_path / "build-styles"
    report["collection"]["analysis_dataset_sha256"] = report["collection"]["dataset_sha256"]
    save_evidence(root, report, inputs)
    archival = deepcopy(report)
    archival["commander_slug"] = "archival"
    save_evidence(root, archival, inputs)
    write(root / "_collection/state.json", {"schema_version": 1, "commanders": {
        "current": {"last_collection_at": "2026-10-25T00:00:00Z", "analysis_version": "learned-current",
                    "collection_dataset_sha256": report["collection"]["dataset_sha256"]},
        "archival": {"last_collection_at": "2026-10-20T00:00:00Z", "analysis_version": "old"}}})
    result = rebuild.rebuild_all(root, cache, current_discovery_only=True, prefer_playability=False)
    assert result["scope"] == "current_discovery"
    assert result["outside_scope"] == ["archival"]
    assert result["report_count"] == result["regenerated"] == 1
    assert result["pending"] == result["blocked"] == 0
    assert rebuild.read_json(root / "archival.json")["deck_generation"]["version"] == 1
