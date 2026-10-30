"""Static build-style coverage and collection bounds; no live EDHREC requests."""

import importlib.util
import csv
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = load_script("export_full_site_data")
batch = load_script("build_site_build_styles")
downloader = load_script("download_site_data")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def report(slug="commander-a", *, date="2026-10-01T00:00:00+00:00", packages=3, total_decks=200):
    return {
        "schema_version": 1, "commander_slug": slug, "commander_name": slug.title(),
        "generated_at": date, "total_edhrec_decks": total_decks,
        "tag_label_policy": batch.tag_label_policy(),
        "profiles": {"profiles": [{"tag_slug": "tokens", "tag_name": "Tokens"}]},
        "deck_analysis": {
            "status": "fitted" if packages else "insufficient_data", "unique_decks": 60,
            "packages": [{"id": index, "top_cards": []} for index in range(packages)],
        },
        "collection": {"blocked": False, "provenance": [{"url": "https://edhrec.com/", "cache_file": "/local/cache"}]},
    }


def current_report(slug="commander-a", **options):
    value = report(slug, **options)
    value["collection"].update(complete=True, target_mode="all", analysis_current=True,
                               dataset_sha256="current-dataset", analysis_dataset_sha256="current-dataset")
    return value


def current_record(**changes):
    return {"last_collection_at": "2026-10-23T03:14:00Z", "analysis_version": "current-model", **changes}


def test_missing_samples_export_explicit_empty_coverage(tmp_path):
    result = exporter.export_build_style_files(
        pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": 200}), tmp_path / "processed", tmp_path / "site",
        seed_dir=tmp_path / "missing-seeds",
    )
    index = json.loads((tmp_path / "site/build-styles/index.json").read_text())
    assert result["commander_count"] == 0
    assert index == {"schema_version": 1, "coverage": "sampled_commanders", "total_available": 0,
                     "total_catalog": 1, "min_total_decks": 200, "total_pending": 1, "total_fitted": 0,
                     "total_insufficient": 0, "commanders": [], "discovery": {
                         "policy": "current_collector", "min_total_decks": 200, "collection_started_after": "2026-10-23T03:13:45Z",
                         "baseline_run_id": 39893813350, "baseline_previews": 151, "baseline_report_count": 25,
                         "commander_slugs": [], "total_available": 0, "total_hidden": 0}}


def test_discovery_selects_current_reports_before_legacy_merge_and_keeps_archived_commander_data(tmp_path):
    processed, site, seeds = tmp_path / "processed", tmp_path / "site", tmp_path / "seeds"
    write_json(seeds / "commander-a.json", report(date="2026-10-25T00:00:00Z", packages=7))
    write_json(seeds / "commander-b.json", report("commander-b", packages=2))
    approved = current_report(packages=0)
    write_json(processed / "build-styles/commander-a.json", approved)
    write_json(processed / "build-styles/_collection/state.json", {"schema_version": 1,
        "commanders": {"commander-a": current_record(status="blocked", collection_dataset_sha256="current-dataset")}})
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a", "commander-b", "pending"], "total_decks": 200}),
        processed, site, previous_reports={"commander-a": report(date="2026-10-26T00:00:00Z", packages=6)}, seed_dir=seeds)
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["total_available"] == 2
    assert {entry["commander_slug"] for entry in index["commanders"]} == {"commander-a", "commander-b"}
    assert index["discovery"]["commander_slugs"] == ["commander-a"]
    assert (index["discovery"]["total_available"], index["discovery"]["total_hidden"]) == (1, 1)
    saved = json.loads((site / "build-styles/commander-a.json").read_text())
    assert saved["deck_analysis"]["status"] == "insufficient_data"
    assert saved["generated_at"] == approved["generated_at"]
    assert (site / "build-styles/commander-b.json").is_file()
    assert json.loads((site / "build-styles/commander-b.json").read_text())["deck_analysis"]["packages"] == [
        {"id": 0, "top_cards": []}, {"id": 1, "top_cards": []}]


def test_discovery_grows_with_future_verified_reports_even_when_model_generation_date_is_older(tmp_path):
    processed, site = tmp_path / "processed", tmp_path / "site"
    records = {"commander-a": current_record()}
    for slug in ("commander-a", "commander-b"):
        write_json(processed / f"build-styles/{slug}.json", current_report(slug))
    state_path = processed / "build-styles/_collection/state.json"
    write_json(state_path, {"schema_version": 1, "commanders": records})
    frame = pd.DataFrame({"commander_slug": ["commander-a", "commander-b"], "total_decks": 200})
    exporter.export_build_style_files(frame, processed, site, seed_dir=tmp_path / "none")
    assert json.loads((site / "build-styles/index.json").read_text())["discovery"]["commander_slugs"] == ["commander-a"]
    records["commander-b"] = current_record(last_collection_at="2026-11-01T00:00:00Z", status="failed")
    write_json(state_path, {"schema_version": 1, "commanders": records})
    exporter.export_build_style_files(frame, processed, site, seed_dir=tmp_path / "none")
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["discovery"]["commander_slugs"] == ["commander-a", "commander-b"]
    assert index["discovery"]["total_available"] == 2
    assert index["discovery"]["baseline_report_count"] == 25
    assert all(entry["generated_at"] == "2026-10-01T00:00:00+00:00" for entry in index["commanders"])


def test_discovery_starts_empty_for_low_population_reports_and_admits_200_total_decks(tmp_path):
    processed, site = tmp_path / "processed", tmp_path / "site"
    populations = {"rare": 6, "boundary-low": 199, "eligible": 200}
    records = {}
    for slug, count in populations.items():
        value = current_report(slug, total_decks=count)
        value["deck_analysis"]["unique_decks"] = 8
        write_json(processed / f"build-styles/{slug}.json", value)
        records[slug] = current_record()
    write_json(processed / "build-styles/_collection/state.json", {"schema_version": 1, "commanders": records})
    frame = pd.DataFrame([{"commander_slug": slug, "total_decks": count} for slug, count in populations.items()])
    exporter.export_build_style_files(frame[frame.commander_slug != "eligible"], processed, site,
                                     seed_dir=tmp_path / "none")
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["commanders"] == []
    assert index["discovery"]["commander_slugs"] == []
    assert index["min_total_decks"] == 200
    assert (site / "build-styles/rare.json").is_file()
    exporter.export_build_style_files(frame, processed, site, seed_dir=tmp_path / "none")
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["total_catalog"] == 1
    assert index["discovery"]["commander_slugs"] == ["eligible"]
    assert index["commanders"][0]["unique_decks"] == 8
    assert index["commanders"][0]["total_edhrec_decks"] == 200


@pytest.mark.parametrize("catalog_count,current_count", [(199, 500), (None, 500), (500, 199)])
def test_archived_fit_cannot_override_a_low_or_unknown_population_check(tmp_path, catalog_count, current_count):
    processed, site, seeds = tmp_path / "processed", tmp_path / "site", tmp_path / "seeds"
    write_json(seeds / "commander-a.json", report(date="2026-10-25T00:00:00Z", total_decks=500))
    write_json(processed / "build-styles/commander-a.json", current_report(packages=0, total_decks=current_count))
    write_json(processed / "build-styles/_collection/state.json", {"schema_version": 1,
        "commanders": {"commander-a": current_record()}})
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": catalog_count}),
                                     processed, site, seed_dir=seeds)
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["commanders"] == []
    assert index["discovery"]["commander_slugs"] == []
    archived = json.loads((site / "build-styles/commander-a.json").read_text())
    assert archived["total_edhrec_decks"] in (None, 199)


@pytest.mark.parametrize("record_changes,collection_changes", [
    ({"last_collection_at": "2026-10-23T03:13:44Z"}, {}),
    ({"last_collection_at": "2026-10-24T00:00:00"}, {}),
    ({"analysis_version": ""}, {}),
    ({"status": "analysis_pending", "collection_dataset_sha256": "new-input"}, {}),
    ({}, {"complete": False}),
    ({}, {"target_mode": "sample"}),
    ({}, {"analysis_current": False}),
    ({}, {"dataset_sha256": ""}),
    ({}, {"analysis_dataset_sha256": "older-input"}),
])
def test_discovery_hides_unverified_or_partial_reports_without_removing_their_files(tmp_path, record_changes, collection_changes):
    processed, site = tmp_path / "processed", tmp_path / "site"
    source = current_report()
    source["collection"].update(collection_changes)
    write_json(processed / "build-styles/commander-a.json", source)
    write_json(processed / "build-styles/_collection/state.json", {"schema_version": 1,
        "commanders": {"commander-a": current_record(**record_changes)}})
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": 200}), processed, site,
                                     seed_dir=tmp_path / "none")
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["total_available"] == 1
    assert index["discovery"]["commander_slugs"] == []
    assert index["discovery"]["total_hidden"] == 1
    assert (site / "build-styles/commander-a.json").is_file()


@pytest.mark.parametrize("state", [None, [], {"schema_version": 2, "commanders": {}},
                                  {"schema_version": 1, "commanders": []}])
def test_discovery_requires_a_valid_processed_checkpoint_and_does_not_admit_seeds(tmp_path, state):
    processed, site, seeds = tmp_path / "processed", tmp_path / "site", tmp_path / "seeds"
    write_json(seeds / "commander-a.json", current_report())
    write_json(processed / "build-styles/commander-b.json", current_report("commander-b"))
    if state is not None:
        write_json(processed / "build-styles/_collection/state.json", state)
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a", "commander-b"], "total_decks": 200}),
                                     processed, site, seed_dir=seeds)
    index = json.loads((site / "build-styles/index.json").read_text())
    assert index["total_available"] == 2
    assert index["discovery"]["commander_slugs"] == []
    assert index["discovery"]["total_hidden"] == 2


@pytest.mark.parametrize("packages", [1, 3, 7])
def test_export_counts_flexible_packages_and_filters_other_commanders(tmp_path, packages):
    processed, site = tmp_path / "processed", tmp_path / "site"
    write_json(processed / "build-styles/commander-a.json", report(packages=packages))
    write_json(processed / "build-styles/removed-commander.json", report("removed-commander"))
    result = exporter.export_build_style_files(
        pd.DataFrame({"commander_slug": ["commander-a", "not-sampled"], "total_decks": 200}), processed, site,
        seed_dir=tmp_path / "missing-seeds",
    )
    index = json.loads((site / "build-styles/index.json").read_text())
    assert result["commander_count"] == 1
    assert index["commanders"][0]["package_count"] == packages
    assert index["commanders"][0]["file"] == "build-styles/commander-a.json"
    assert not (site / "build-styles/not-sampled.json").exists()
    assert not (site / "build-styles/removed-commander.json").exists()


def test_empty_insufficient_report_is_counted_without_inventing_packages(tmp_path):
    processed, site = tmp_path / "processed", tmp_path / "site"
    insufficient = report(packages=0)
    insufficient["profiles"]["profiles"] = []
    insufficient["deck_analysis"].update(unique_decks=2, reason="Need at least 8 unique usable decks")
    write_json(processed / "build-styles/commander-a.json", insufficient)
    exporter.export_build_style_files(
        pd.DataFrame({"commander_slug": ["commander-a", "pending", "pending"], "total_decks": 200}), processed, site,
        seed_dir=tmp_path / "missing-seeds",
    )
    index = json.loads((site / "build-styles/index.json").read_text())
    assert (index["total_catalog"], index["total_pending"], index["total_insufficient"], index["total_fitted"]) == (2, 1, 1, 0)
    assert index["commanders"][0]["package_count"] == 0
    assert index["commanders"][0]["packages"] == []
    assert not (site / "build-styles/pending.json").exists()


def test_discovery_index_summarizes_selected_packages_without_deck_payloads(tmp_path):
    source = report(packages=4)
    packages = source["deck_analysis"]["packages"]
    rating = {"status": "verified", "checked_at": "2026-10-24T10:00:00Z",
              "playability_pct": 75.2, "bracket": 4, "power_level": 6.71, "deck_sha256": "large-rating-detail"}
    calculated = {"status": "calculated", "checked_at": "2026-10-24T10:00:00Z", "power_level": 6.72,
                  "score_bracket": 3, "deck_sha256": "current-list", "source": "local_public_formula",
                  "power_formula_version": 1, "card_details": ["large internal calculation detail"]}
    packages[0].update(name="Package 1", tag_suggestions=[
        {"tag_slug": "tokens", "tag_name": "Tokens", "similarity": 0.8, "compared_cards": 81,
         "support_tier": "main", "deck_count": 15, "commander_deck_count": 300, "deck_fraction": 0.05},
        {"tag_slug": "counters", "tag_name": "Counters", "similarity": 0.6,
         "support_tier": "supporting", "deck_count": 5, "commander_deck_count": 300, "deck_fraction": 5 / 300},
        {"tag_slug": "legacy", "tag_name": "Legacy", "similarity": 0.5},
    ], generated_deck={"status": "generated", "cards": [{"name": "Do not embed full decks", "quantity": 100}],
        "primer": "Long primer content", "export_text": "100 Do not embed full decks",
        "validation": {"valid": True, "bracket": {"recommended": 3, "rule_checks": {"expensive": True}},
                       "external_rating": rating},
        "statistics": {"recommended_bracket": 3, "swap_details": [{"in": "Large card detail"}]}})
    packages[1].update(generated_deck={"status": "generated", "validation": {
        "deck_sha256": "current-list", "calculated_power": calculated},
                                      "statistics": {"recommended_bracket": 2}})
    packages[2].update(tag_suggestions=[], generated_deck={"status": "unavailable", "reason": "Metadata missing"})
    # Older packages can lack generation and labels; alternative model packages
    # are deliberately excluded from this summary of the selected model.
    packages[3]["tag_suggestions"] = None
    source["deck_analysis"]["alternative_models"] = [{"components": 8, "packages": [
        {"id": 9, "name": "Alternative only", "tag_suggestions": [{"tag_name": "Alternative tag"}]}]}]
    write_json(tmp_path / "processed/build-styles/commander-a.json", source)
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": 200}),
                                     tmp_path / "processed", tmp_path / "site", seed_dir=tmp_path / "none")
    index = json.loads((tmp_path / "site/build-styles/index.json").read_text())
    assert index["commanders"][0]["packages"] == [
        {"id": 0, "name": "Package 1", "tag_suggestions": [
            {"tag_slug": "tokens", "tag_name": "Tokens", "similarity": 0.8,
             "support_tier": "main", "deck_count": 15, "commander_deck_count": 300, "deck_fraction": 0.05},
            {"tag_slug": "counters", "tag_name": "Counters", "similarity": 0.6,
             "support_tier": "supporting", "deck_count": 5, "commander_deck_count": 300, "deck_fraction": 5 / 300},
            {"tag_slug": "legacy", "tag_name": "Legacy", "similarity": 0.5}],
         "generated_deck": {"status": "generated", "validation": {
             "bracket": {"recommended": 3}, "external_rating": rating},
             "statistics": {"recommended_bracket": 3}}},
        {"id": 1, "tag_suggestions": [], "generated_deck": {
            "status": "generated", "validation": {"deck_sha256": "current-list", "calculated_power": {
                key: value for key, value in calculated.items() if key != "card_details"}},
            "statistics": {"recommended_bracket": 2}}},
        {"id": 2, "tag_suggestions": [], "generated_deck": {"status": "unavailable"}},
        {"id": 3, "tag_suggestions": []},
    ]
    assert "Long primer content" not in json.dumps(index)
    assert "Do not embed full decks" not in json.dumps(index)
    assert "Alternative tag" not in json.dumps(index)
    assert "large internal calculation detail" not in json.dumps(index)


@pytest.mark.parametrize("cards", [
    [{"name": "Commander", "scryfall_uri": "https://scryfall.com/card/tst/1/commander", "faces": [
        {"name": "Commander", "image_url": "commander.jpg"}]}],
    [{"name": "Front // Back", "scryfall_uri": "https://scryfall.com/card/tst/2/front", "faces": [
        {"name": "Front", "image_url": "front.jpg"}, {"name": "Back", "image_url": "back.jpg"}]},
     {"name": "Partner", "scryfall_uri": "https://scryfall.com/card/tst/3/partner", "faces": [
        {"name": "Partner", "image_url": "partner.jpg"}]}],
])
def test_discovery_index_includes_catalog_card_faces_and_legacy_images(tmp_path, cards):
    source = report()
    source["commander_name"] = "EDHREC display name"
    images = {"commander_cards": cards, "card_image_url": cards[0]["faces"][0]["image_url"],
              "partner_card_image_urls": [card["faces"][0]["image_url"] for card in cards],
              "scryfall_uri": cards[0]["scryfall_uri"],
              "partner_scryfall_uris": [card["scryfall_uri"] for card in cards],
              "scryfall_card_names": [card["name"] for card in cards]}
    row = {"commander_slug": "commander-a", "commander_name": "Canonical catalog name", "total_decks": 200,
           "unrelated_tag_detail": "not needed in discovery", **images}
    # Affinity data has one row per tag, while discovery has one per commander.
    frame = pd.DataFrame([row, {**row, "unrelated_tag_detail": "second tag"}])
    write_json(tmp_path / "processed/build-styles/commander-a.json", source)
    exporter.export_build_style_files(frame, tmp_path / "processed", tmp_path / "site", seed_dir=tmp_path / "none")
    index = json.loads((tmp_path / "site/build-styles/index.json").read_text())
    assert len(index["commanders"]) == 1
    entry = index["commanders"][0]
    assert entry["commander_name"] == "EDHREC display name"
    assert {key: entry[key] for key in images} == images
    assert "unrelated_tag_detail" not in entry


def test_export_preserves_source_age_and_partial_coverage_without_using_analysis_date(tmp_path):
    processed, site = tmp_path / "processed", tmp_path / "site"
    source = report(date="2026-10-21T12:00:00+00:00")
    evidence = {"target_hours": 96, "max_age_hours": 72,
                "oldest_source_at": "2026-10-01T12:00:00+00:00",
                "newest_source_at": "2026-10-20T12:00:00+00:00",
                "dated_previews": 60, "undated_previews": 0, "observed_previews": 60, "stale_previews": 20,
                "index_source_at": "2026-10-20T12:00:00+00:00", "index_stale": False,
                "source_complete": False}
    source["collection"].update(target_mode="all", complete=False, frame_decks=1000,
                                previews_attempted=60, previews_loaded=60, freshness=evidence)
    write_json(processed / "build-styles/commander-a.json", source)
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": 200}), processed, site,
                                     seed_dir=tmp_path / "missing-seeds")
    index = json.loads((site / "build-styles/index.json").read_text())
    entry = index["commanders"][0]
    assert entry["generated_at"] == "2026-10-21T12:00:00+00:00"
    assert entry["collection"]["freshness"] == evidence
    assert entry["collection"]["previews_attempted"] == 60
    assert "provenance" not in entry["collection"]
    from datetime import datetime, timezone
    summary = exporter.summarize_build_style_freshness(index["commanders"], datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
    assert (summary["target_met"], summary["partial"], summary["overdue"], summary["unknown"]) == (0, 1, 1, 0)


def test_freshness_summary_counts_full_current_sources_and_unknown_legacy_reports():
    from datetime import datetime, timezone
    evidence = {"oldest_source_at": "2026-10-20T12:00:00Z", "newest_source_at": "2026-10-20T13:00:00Z",
                "dated_previews": 98, "undated_previews": 0, "observed_previews": 98, "stale_previews": 0,
                "index_source_at": "2026-10-20T14:00:00Z", "index_stale": False}
    full = {"collection": {"target_mode": "all", "complete": True, "frame_decks": 100,
                            "previews_attempted": 100, "unavailable_previews": 0, "freshness": evidence}}
    legacy = {"generated_at": "2026-10-21T12:00:00Z"}
    summary = exporter.summarize_build_style_freshness([full, legacy], datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
    assert (summary["target_met"], summary["partial"], summary["overdue"], summary["unknown"]) == (1, 1, 0, 1)
    evidence.update(basis="current_index_versions", oldest_verified_at="2026-10-20T14:00:00Z",
                    newest_verified_at="2026-10-20T14:00:00Z", oldest_source_at="2026-08-01T00:00:00Z")
    summary = exporter.summarize_build_style_freshness([full], datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
    assert (summary["target_met"], summary["overdue"]) == (1, 0)


def test_export_publishes_compact_checkpoint_rate_and_eta_without_internal_state(tmp_path):
    processed, site = tmp_path / "processed", tmp_path / "site"
    progress = {
        "updated_at": "2026-10-23T02:47:17Z", "blocked": True, "blocked_until": "2026-10-23T03:47:17Z",
        "block_reason": "rate_limit",
        "capacity": {"remaining_requests": 12_000_000, "effective_requests_per_second": 1.5,
                     "configured_requests_per_second": 40, "rate_limit_learned": True,
                     "estimated_minimum_seconds": 8_000_000, "measured_projection_seconds": 9_000_000,
                     "projected_finish_at": "2027-02-04T06:47:17Z", "internal_cache_file": "/private/cache"},
        "rate_control": {"learned_rate": 1.5, "status": "learned_below_observed_throttle", "throttles": 1,
                         "last_throttle": {"at": "2026-10-23T02:47:17Z", "rate_before": 2, "rate_after": 1.5,
                                           "raw_response": "private debug content"}},
        "measured_request_rate": {"successful_responses": 128, "elapsed_seconds": 65.4},
        "distributed": {"runners": 16, "active_workers": 15, "blocked_workers": 1, "pilot": False,
                        "public_ips": ["private-network-receipt"]},
        "selected": ["private-queue-entry"],
    }
    write_json(processed / "build-styles/_collection/state.json", {
        "progress": progress, "commanders": {"private-queue-entry": {"cache_file": "/private/state"}}})
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": 200}), processed, site,
                                     seed_dir=tmp_path / "missing-seeds")
    index = json.loads((site / "build-styles/index.json").read_text())
    public = index["collection_progress"]
    assert public["updated_at"] == progress["updated_at"]
    assert public["capacity"]["estimated_minimum_seconds"] == 8_000_000
    assert public["rate_control"]["learned_rate"] == 1.5
    assert public["blocked"] is True
    assert public["block_reason"] == "rate_limit"
    assert "/private" not in json.dumps(public)
    assert "selected" not in public and "commanders" not in public
    assert "raw_response" not in public["rate_control"]["last_throttle"]
    assert public["distributed"] == {"runners": 16, "active_workers": 15, "blocked_workers": 1, "pilot": False}


def test_progress_export_chooses_latest_valid_snapshot_and_tolerates_bad_optional_state(tmp_path):
    processed, seed = tmp_path / "processed/build-styles", tmp_path / "seeds"
    write_json(seed / "_collection/state.json", {"progress": {
        "updated_at": "2026-10-20T00:00:00Z", "capacity": {"remaining_requests": 100}}})
    write_json(processed / "_collection/state.json", {"progress": {
        "updated_at": "2026-10-21T00:00:00Z", "capacity": {"remaining_requests": 90}}})
    write_json(processed.parent / "build_style_batch.json", {
        "capacity": {"evaluated_at": "2026-10-22T00:00:00Z", "remaining_requests": 80}})
    progress = exporter.read_build_collection_progress([processed, seed])
    assert progress["capacity"]["remaining_requests"] == 80
    assert progress["updated_at"] == "2026-10-22T00:00:00Z"
    (processed.parent / "build_style_batch.json").write_text('{"capacity":NaN}')
    assert exporter.read_build_collection_progress([processed, seed])["capacity"]["remaining_requests"] == 90
    write_json(processed / "_collection/state.json", {"progress": {
        "updated_at": "2026-10-23T00:00:00", "capacity": {"remaining_requests": 70}}})
    assert exporter.read_build_collection_progress([processed, seed])["capacity"]["remaining_requests"] == 100


def test_malformed_samples_do_not_prevent_other_exports(tmp_path):
    samples = tmp_path / "samples"
    write_json(samples / "commander-a.json", report())
    write_json(samples / "wrong-filename.json", report("commander-b"))
    write_json(samples / "missing-analysis.json", {"commander_slug": "missing-analysis"})
    write_json(samples / "bad-packages.json", {**report("bad-packages"), "deck_analysis": {"status": "fitted", "packages": [1], "unique_decks": 60}})
    (samples / "nonfinite.json").write_text('{"value":NaN}')
    (samples / "overflow.json").write_text(json.dumps(report("overflow")).replace('"collection":', '"overflow":1e999,"collection":'))
    (samples / "invalid.json").write_text("<html>challenge</html>")
    assert set(exporter.read_build_style_reports(samples)) == {"commander-a"}


def test_clean_export_keeps_newest_successful_samples_and_publishes_manifest(tmp_path):
    processed, site, seeds = tmp_path / "processed", tmp_path / "site", tmp_path / "seeds"
    write_json(processed / "affinity_rows_with_trends.json", [{
        "commander_slug": "commander-a", "commander_name": "Commander A", "tag_slug": "tokens",
        "tag_name": "Tokens", "total_decks": 200, "tag_decks": 50, "z": 2,
    }])
    for name, data in [("analysis_summary", {}), ("trend_summary", {}), ("tag_summary", [])]:
        write_json(processed / f"{name}.json", data)
    write_json(seeds / "commander-a.json", report(date="2026-09-01T00:00:00+00:00", packages=1))
    write_json(site / "build-styles/commander-a.json", report(date="2026-10-05T00:00:00+00:00", packages=5))
    write_json(site / "build-styles/removed-commander.json", report("removed-commander"))
    write_json(processed / "build-styles/commander-a.json", report(date="2026-10-10T00:00:00+00:00", packages=0))
    manifest = exporter.export_full_site_data(processed, site, 10, True, seeds)
    saved = json.loads((site / "build-styles/commander-a.json").read_text())
    assert len(saved["deck_analysis"]["packages"]) == 5
    assert saved["generated_at"] == "2026-10-05T00:00:00+00:00"
    assert manifest["build_style_export"]["commander_count"] == 1
    assert not (site / "build-styles/removed-commander.json").exists()


def test_seed_fills_first_deployment_and_newer_processed_sample_wins(tmp_path):
    processed, site, seeds = tmp_path / "processed", tmp_path / "site", tmp_path / "seeds"
    write_json(seeds / "commander-a.json", report(packages=1))
    frame = pd.DataFrame({"commander_slug": ["commander-a"], "total_decks": 200})
    exporter.export_build_style_files(frame, processed, site, seed_dir=seeds)
    assert json.loads((site / "build-styles/commander-a.json").read_text())["deck_analysis"]["packages"] == [{"id": 0, "top_cards": []}]
    write_json(processed / "build-styles/commander-a.json", report(date="2026-10-15T00:00:00+00:00", packages=4))
    exporter.export_build_style_files(frame, processed, site, seed_dir=seeds)
    assert len(json.loads((site / "build-styles/commander-a.json").read_text())["deck_analysis"]["packages"]) == 4


def test_downloader_follows_only_advertised_samples_and_keeps_old_deployments_compatible(monkeypatch, tmp_path):
    index = {"commanders": [{"commander_slug": "commander-a", "file": "build-styles/commander-a.json"}]}
    files = {
        "site_manifest.json": {"build_style_export": {"index_file": "build-styles/index.json"}},
        "build-styles/index.json": index, "build-styles/commander-a.json": report(),
        "leaderboard/index.json": {"pages": []}, "commanders/index.json": [],
        "tags/index.json": [], "theme-brackets/index.json": [], "sets/index.json": [],
    }
    monkeypatch.setattr(downloader, "fetch_json", lambda base, path: files[path])
    downloader.download_site_data("https://example.test/", tmp_path)
    assert json.loads((tmp_path / "build-styles/commander-a.json").read_text())["commander_slug"] == "commander-a"
    assert json.loads((tmp_path / "build-styles/index.json").read_text()) == index
    files["site_manifest.json"] = {}
    assert downloader.build_download_list("https://example.test/", tmp_path) == []


def batch_args(tmp_path, *extra):
    index = tmp_path / "commander_index.json"
    write_json(index, [
        {"commander_slug": "commander-a", "total_decks": 1000},
        {"commander_slug": "commander-b", "total_decks": 500},
        {"commander_slug": "commander-c", "total_decks": 200},
    ])
    return batch.parser().parse_args([
        "--commander-index", str(index), "--output-dir", str(tmp_path / "processed/build-styles"),
        "--seed-dir", str(tmp_path / "seeds"), "--cache-root", str(tmp_path / "cache"),
        "--snapshot-date", "2026-10-19", *extra,
    ])


def test_batch_prioritizes_unsampled_before_refresh_and_obeys_hard_limit(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1")
    write_json(args.output_dir / "commander-a.json", report(date="2026-01-01T00:00:00+00:00"))
    assert [row["commander_slug"] for row in batch.select_commanders(args)] == ["commander-b"]
    write_json(args.output_dir / "commander-b.json", report("commander-b", date="2026-10-18T00:00:00+00:00"))
    assert [row["commander_slug"] for row in batch.select_commanders(args)] == ["commander-c"]
    write_json(args.output_dir / "commander-c.json", report("commander-c", date="2026-10-18T00:00:00+00:00"))
    assert [row["commander_slug"] for row in batch.select_commanders(args)] == ["commander-a"]


def test_batch_supports_explicit_eligible_commander_and_offline_dry_run(tmp_path):
    args = batch_args(tmp_path, "--commanders", "commander-c", "--dry-run", "--sample-decks", "20")
    summary = batch.run_batch(args, runner=lambda _: pytest.fail("Dry run must not collect"))
    assert summary["selected"] == ["commander-c"]
    assert summary["max_preview_attempts"] == 20
    assert summary["max_page_requests"] == 39
    with pytest.raises(ValueError, match="not in the index"):
        batch.select_commanders(batch_args(tmp_path, "--commanders", "unknown"))


@pytest.mark.parametrize("maximum", [None, 6, 64])
def test_batch_passes_automatic_model_and_dated_cache_then_resumes(tmp_path, maximum):
    optional_ceiling = [] if maximum is None else ["--max-components", str(maximum)]
    args = batch_args(tmp_path, "--max-commanders", "1", "--sample-decks", "40", *optional_ceiling)
    calls = []

    def runner(parsed):
        calls.append(parsed)
        return report(parsed.commander, date="2026-10-19T00:00:00+00:00", packages=4)

    summary = batch.run_batch(args, runner=runner)
    assert summary["completed"] == ["commander-a"]
    assert calls[0].components is None
    assert calls[0].max_components == maximum
    assert calls[0].min_tag_decks == 5 and calls[0].main_tag_decks == 15
    assert calls[0].sample_decks == 40
    assert calls[0].no_html and not calls[0].fetch
    assert calls[0].cache_dir == args.cache_root / "2026-10-19/commander-a"
    saved = json.loads((args.output_dir / "commander-a.json").read_text())
    assert "cache_file" not in saved["collection"]["provenance"][0]
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-b"


def test_batch_upgrades_old_tag_policy_before_new_coverage_and_retains_preview_checkpoint(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1")
    old = report(date="2026-10-18T00:00:00+00:00")
    old.pop("tag_label_policy")
    write_json(args.output_dir / "commander-a.json", old)
    write_json(batch.checkpoint_path(args), {"schema_version": 1, "commanders": {"commander-a": {
        "status": "fitted", "cache_snapshot": "2026-10-18"}}})
    preview = args.output_dir / "_collection/previews/commander-a.json.gz"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"existing deck evidence")

    def runner(parsed):
        assert parsed.collection_state.read_bytes() == b"existing deck evidence"
        return report(parsed.commander, date="2026-10-19T00:00:00+00:00")

    assert batch.run_batch(args, runner=runner)["completed"] == ["commander-a"]
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-b"


def test_access_block_stops_entire_batch_and_retains_previous_fit(tmp_path):
    args = batch_args(tmp_path, "--fetch", "--refresh")
    old = report(packages=2)
    write_json(args.output_dir / "commander-a.json", old)
    calls = []

    def runner(parsed):
        calls.append(parsed.commander)
        return {**report(parsed.commander), "collection": {"blocked": True}}

    summary = batch.run_batch(args, runner=runner)
    assert summary["blocked"]
    assert calls == ["commander-a"]
    assert json.loads((args.output_dir / "commander-a.json").read_text()) == old
    # The next scheduled job honors the shared cooldown without making requests.
    subsequent = batch.run_batch(args, runner=lambda _: pytest.fail("Cooldown must stop collection"))
    assert subsequent["blocked"]
    assert subsequent["blocked_until"]


def test_default_queue_excludes_commanders_below_two_hundred_decks(tmp_path):
    args = batch_args(tmp_path, "--dry-run")
    write_json(args.commander_index, [
        {"commander_slug": f"commander-{i}", "total_decks": i} for i in range(300)
    ])
    summary = batch.run_batch(args, runner=lambda _: pytest.fail("Dry run must not collect"))
    assert len(summary["selected"]) == 100
    assert "commander-199" not in summary["selected"]
    assert "commander-200" in summary["selected"]
    assert args.max_commanders == 0 and args.min_total_decks == 200


def test_targeted_or_refresh_collection_cannot_bypass_two_hundred_deck_minimum(tmp_path):
    args = batch_args(tmp_path, "--commanders", "commander-c", "--refresh", "--dry-run")
    write_json(args.commander_index, [{"commander_slug": "commander-c", "total_decks": 199}])
    summary = batch.run_batch(args, runner=lambda _: pytest.fail("Below-minimum commander must not be collected"))
    assert summary["selected"] == []
    args.min_total_decks = 0
    with pytest.raises(ValueError, match="at least 200"):
        batch.select_commanders(args)


def test_deadline_saves_finished_work_and_next_run_continues(tmp_path):
    args = batch_args(tmp_path, "--max-runtime-seconds", "60")
    ticks = iter([0, 0, 61])
    summary = batch.run_batch(
        args, runner=lambda parsed: report(parsed.commander, date="2026-10-19T00:00:00+00:00"),
        monotonic=lambda: next(ticks),
    )
    assert summary["time_budget_reached"]
    assert summary["completed"] == ["commander-a"]
    assert summary["remaining_in_batch"] == 2
    assert batch.read_checkpoint(args)["commanders"]["commander-a"]["status"] == "fitted"
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-b"
    assert json.loads((args.output_dir.parent / "build_style_batch.json").read_text()) == summary


def test_insufficient_evidence_is_saved_and_not_repeated_each_hour(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1")

    def runner(parsed):
        value = report(parsed.commander, date="2026-10-19T00:00:00+00:00", packages=0)
        value["profiles"]["profiles"] = []
        return value

    assert batch.run_batch(args, runner=runner)["completed"] == ["commander-a"]
    assert batch.read_checkpoint(args)["commanders"]["commander-a"]["status"] == "insufficient_data"
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-b"


def test_failures_do_not_starve_other_commanders_and_retry_later(tmp_path):
    args = batch_args(tmp_path)
    calls = []

    def runner(parsed):
        calls.append(parsed.commander)
        if parsed.commander == "commander-a":
            raise ValueError("No usable base profile")
        return report(parsed.commander, date="2026-10-19T00:00:00+00:00")

    summary = batch.run_batch(args, runner=runner)
    assert calls == ["commander-a", "commander-b", "commander-c"]
    assert len(summary["failures"]) == 1
    assert not batch.select_commanders(args)
    state = batch.read_checkpoint(args)
    state["commanders"]["commander-a"]["retry_after"] = "2000-01-01T00:00:00+00:00"
    batch.atomic_json(batch.checkpoint_path(args), state)
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-a"


def test_interrupted_run_retains_checkpoint_and_resumes_raw_snapshot(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1")

    def interrupt(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(args, runner=interrupt)
    assert batch.read_checkpoint(args)["commanders"]["commander-a"]["status"] == "running"
    args.snapshot_date = "2026-10-20"
    caches = []

    def resume(parsed):
        caches.append(parsed.cache_dir)
        return report(parsed.commander, date="2026-10-20T00:00:00+00:00")

    batch.run_batch(args, runner=resume)
    assert caches == [args.cache_root / "2026-10-19/commander-a"]


def test_partial_deck_generation_retries_without_refetching_completed_raw_pages(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1")

    def partial(parsed):
        return {**report(parsed.commander, date="2026-10-19T00:00:00+00:00"),
                "prototype_version": 3, "deck_generation": {"status": "partial"}}

    batch.run_batch(args, runner=partial)
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-b"
    state = batch.read_checkpoint(args)
    state["commanders"]["commander-a"]["retry_after"] = "2000-01-01T00:00:00+00:00"
    batch.atomic_json(batch.checkpoint_path(args), state)
    args.snapshot_date = "2026-10-20"
    caches = []

    def complete(parsed):
        caches.append(parsed.cache_dir)
        return {**report(parsed.commander, date="2026-10-20T00:00:00+00:00"),
                "prototype_version": 3, "deck_generation": {"status": "complete"}}

    batch.run_batch(args, runner=complete)
    assert caches == [args.cache_root / "2026-10-19/commander-a"]


def test_ci_cache_pruning_keeps_durable_reports_and_partial_retry_data(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1", "--prune-completed-cache")
    raw = args.cache_root / args.snapshot_date / "commander-a"
    write_json(raw / "base.json", {"sample": "cached"})
    batch.run_batch(args, runner=lambda parsed: report(parsed.commander, date="2026-10-19T00:00:00+00:00"))
    assert not raw.exists()
    assert (args.output_dir / "commander-a.json").exists()
    assert batch.read_checkpoint(args)["commanders"]["commander-a"]["status"] == "fitted"
    args.refresh = True
    write_json(raw / "base.json", {"sample": "cached"})
    batch.run_batch(args, runner=lambda parsed: {
        **report(parsed.commander, date="2026-10-19T00:00:00+00:00"), "deck_generation": {"status": "partial"}})
    assert raw.exists()


def test_old_package_only_reports_receive_native_deck_upgrade(tmp_path):
    args = batch_args(tmp_path)
    write_json(args.output_dir / "commander-a.json", {
        **report(date="2026-10-19T00:00:00+00:00"), "prototype_version": 2})
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-a"


def test_capped_reports_expand_without_waiting_for_refresh_but_complete_report_supersedes_seed(tmp_path):
    args = batch_args(tmp_path)
    capped = {**report(date="2026-10-19T00:00:00+00:00"), "prototype_version": 3,
              "collection": {"frame_decks": 500, "previews_attempted": 60}}
    write_json(args.seed_dir / "commander-a.json", capped)
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-a"
    complete = {**capped, "collection": {"frame_decks": 500, "previews_attempted": 500, "complete": True}}
    write_json(args.output_dir / "commander-a.json", complete)
    assert batch.select_commanders(args)[0]["commander_slug"] == "commander-b"


def test_partial_collection_retains_durable_state_and_rotates_with_new_coverage(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1", "--prune-completed-cache")
    def partial(parsed):
        assert parsed.sample_decks is None
        assert parsed.max_preview_seconds == 300
        assert parsed.collection_state.parent == args.output_dir / "_collection/previews"
        parsed.collection_state.parent.mkdir(parents=True, exist_ok=True)
        parsed.collection_state.write_bytes(b"durable-parsed-decks")
        return {**report(parsed.commander, date="2026-10-19T00:00:00+00:00"),
                "collection": {"complete": False, "frame_decks": 500, "previews_attempted": 200}}
    batch.run_batch(args, runner=partial)
    assert batch.read_checkpoint(args)["commanders"]["commander-a"]["status"] == "collecting"
    assert (args.output_dir / "_collection/previews/commander-a.json.gz").exists()
    args.max_commanders = 0
    queue = [row["commander_slug"] for row in batch.select_commanders(args)]
    assert "commander-a" in queue and "commander-b" in queue


def test_export_removes_legacy_tags_from_all_models_without_changing_cards_primers_or_rating(tmp_path):
    source = report()
    deck = {"status": "generated", "commanders": [{"name": "Commander"}], "cards": [
        {"name": "Commander", "quantity": 1, "tags": ["Commander", "Tokens"], "average_lift": 100},
        {"name": "Hybrid", "quantity": 1, "primary_role": "Ramp", "tags": ["Removal", "Tokens"], "average_lift": 3},
        {"name": "Forest", "quantity": 98, "tags": ["Lands"]},
    ], "card_count": 100, "primer": "Plan\nDevelop mana with [[Hybrid]].",
        "validation": {"external_rating": {"deck_sha256": "same-card-list"}}, "export_text": "old multi-tag export"}
    source["deck_analysis"]["packages"][0]["generated_deck"] = deck
    source["deck_analysis"]["alternative_models"] = [{"components": 2, "packages": [{"generated_deck": deepcopy(deck)}]}]
    write_json(tmp_path / "processed/build-styles/commander-a.json", source)
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"]}),
                                     tmp_path / "processed", tmp_path / "site", seed_dir=tmp_path / "none")
    saved_report = json.loads((tmp_path / "site/build-styles/commander-a.json").read_text())
    analysis = saved_report["deck_analysis"]
    saved_decks = [analysis["packages"][0]["generated_deck"],
                   analysis["alternative_models"][0]["packages"][0]["generated_deck"]]
    for saved in saved_decks:
        assert all("tags" not in card for card in saved["cards"])
        assert [card["primary_role"] for card in saved["cards"]] == ["Commander", "Ramp", "Lands"]
        assert [(card["name"], card["quantity"]) for card in saved["cards"]] == [
            (card["name"], card["quantity"]) for card in deck["cards"]]
        assert saved["validation"] == deck["validation"]
        assert saved["primer"] == deck["primer"]
        assert saved["export_text"] == "1 Commander *CMDR*\n1 Hybrid\n98 Forest\n"

    # The downloadable files normalize legacy reports too, and commander lift
    # must remain excluded after public card tags are removed.
    summaries = load_script("export_package_decks").export_decks(source, tmp_path / "decks")
    assert len(summaries) == 2
    for summary in summaries:
        assert summary["highest_average_lift"] == {"name": "Hybrid", "lift": 3}
        assert Path(summary["deck_file"]).read_text() == "1 Commander *CMDR*\n1 Hybrid\n98 Forest\n"
        with Path(summary["ranking_file"]).open(newline="") as handle:
            ranking = csv.DictReader(handle)
            assert ranking.fieldnames == ["name", "quantity", "average_inclusion", "average_lift", "member_inclusion"]
            assert [(row["name"], row["quantity"]) for row in ranking] == [("Commander", "1"), ("Hybrid", "1"), ("Forest", "98")]
