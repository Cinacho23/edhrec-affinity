"""Static build-style coverage and collection bounds; no live EDHREC requests."""

import importlib.util
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


def report(slug="commander-a", *, date="2026-10-01T00:00:00+00:00", packages=3):
    return {
        "schema_version": 1, "commander_slug": slug, "commander_name": slug.title(),
        "generated_at": date,
        "profiles": {"profiles": [{"tag_slug": "tokens", "tag_name": "Tokens"}]},
        "deck_analysis": {
            "status": "fitted" if packages else "insufficient_data", "unique_decks": 60,
            "packages": [{"id": index, "top_cards": []} for index in range(packages)],
        },
        "collection": {"blocked": False, "provenance": [{"url": "https://edhrec.com/", "cache_file": "/local/cache"}]},
    }


def test_missing_samples_export_explicit_empty_coverage(tmp_path):
    result = exporter.export_build_style_files(
        pd.DataFrame({"commander_slug": ["commander-a"]}), tmp_path / "processed", tmp_path / "site",
        seed_dir=tmp_path / "missing-seeds",
    )
    index = json.loads((tmp_path / "site/build-styles/index.json").read_text())
    assert result["commander_count"] == 0
    assert index == {"schema_version": 1, "coverage": "sampled_commanders", "total_available": 0,
                     "total_catalog": 1, "total_pending": 1, "total_fitted": 0,
                     "total_insufficient": 0, "commanders": []}


@pytest.mark.parametrize("packages", [1, 3, 7])
def test_export_counts_flexible_packages_and_filters_other_commanders(tmp_path, packages):
    processed, site = tmp_path / "processed", tmp_path / "site"
    write_json(processed / "build-styles/commander-a.json", report(packages=packages))
    write_json(processed / "build-styles/removed-commander.json", report("removed-commander"))
    result = exporter.export_build_style_files(
        pd.DataFrame({"commander_slug": ["commander-a", "not-sampled"]}), processed, site,
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
        pd.DataFrame({"commander_slug": ["commander-a", "pending", "pending"]}), processed, site,
        seed_dir=tmp_path / "missing-seeds",
    )
    index = json.loads((site / "build-styles/index.json").read_text())
    assert (index["total_catalog"], index["total_pending"], index["total_insufficient"], index["total_fitted"]) == (2, 1, 1, 0)
    assert index["commanders"][0]["package_count"] == 0
    assert not (site / "build-styles/pending.json").exists()


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
        "tag_name": "Tokens", "total_decks": 100, "tag_decks": 50, "z": 2,
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
    frame = pd.DataFrame({"commander_slug": ["commander-a"]})
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
        {"commander_slug": "commander-b", "total_decks": 100},
        {"commander_slug": "commander-c", "total_decks": 10},
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


def test_batch_supports_explicit_small_commander_and_offline_dry_run(tmp_path):
    args = batch_args(tmp_path, "--commanders", "commander-c", "--dry-run", "--sample-decks", "20")
    summary = batch.run_batch(args, runner=lambda _: pytest.fail("Dry run must not collect"))
    assert summary["selected"] == ["commander-c"]
    assert summary["max_preview_attempts"] == 20
    assert summary["max_page_requests"] == 39
    with pytest.raises(ValueError, match="not in the index"):
        batch.select_commanders(batch_args(tmp_path, "--commanders", "unknown"))


def test_batch_passes_automatic_model_and_dated_cache_then_resumes(tmp_path):
    args = batch_args(tmp_path, "--max-commanders", "1", "--sample-decks", "40", "--max-components", "6")
    calls = []

    def runner(parsed):
        calls.append(parsed)
        return report(parsed.commander, date="2026-10-19T00:00:00+00:00", packages=4)

    summary = batch.run_batch(args, runner=runner)
    assert summary["completed"] == ["commander-a"]
    assert calls[0].components is None
    assert calls[0].max_components == 6
    assert calls[0].sample_decks == 40
    assert calls[0].no_html and not calls[0].fetch
    assert calls[0].cache_dir == args.cache_root / "2026-10-19/commander-a"
    saved = json.loads((args.output_dir / "commander-a.json").read_text())
    assert "cache_file" not in saved["collection"]["provenance"][0]
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


def test_default_queue_includes_entire_catalog_without_deck_count_threshold(tmp_path):
    args = batch_args(tmp_path, "--dry-run")
    write_json(args.commander_index, [
        {"commander_slug": f"commander-{i}", "total_decks": i} for i in range(100)
    ])
    summary = batch.run_batch(args, runner=lambda _: pytest.fail("Dry run must not collect"))
    assert len(summary["selected"]) == summary["catalog_size"] == 100
    assert "commander-0" in summary["selected"]
    assert args.max_commanders == args.min_total_decks == 0


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


def test_export_converts_existing_decks_to_one_tag_without_changing_cards_or_rating(tmp_path):
    source = report()
    deck = {"status": "generated", "commanders": [{"name": "Commander"}], "cards": [
        {"name": "Commander", "quantity": 1, "primary_role": "Commander", "tags": ["Commander", "Tokens"]},
        {"name": "Hybrid", "quantity": 1, "primary_role": "Ramp", "tags": ["Ramp", "Removal", "Tokens"]},
    ], "validation": {"external_rating": {"deck_sha256": "same-card-list"}}, "export_text": "old multi-tag export"}
    source["deck_analysis"]["packages"][0]["generated_deck"] = deck
    write_json(tmp_path / "processed/build-styles/commander-a.json", source)
    exporter.export_build_style_files(pd.DataFrame({"commander_slug": ["commander-a"]}),
                                     tmp_path / "processed", tmp_path / "site", seed_dir=tmp_path / "none")
    saved = json.loads((tmp_path / "site/build-styles/commander-a.json").read_text())["deck_analysis"]["packages"][0]["generated_deck"]
    assert [card["tags"] for card in saved["cards"]] == [["Commander"], ["Ramp"]]
    assert saved["validation"] == deck["validation"]
    assert saved["export_text"] == "1 Commander *CMDR* #Commander\n1 Hybrid #Ramp\n"
