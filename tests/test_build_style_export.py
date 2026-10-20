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
    assert index == {"schema_version": 1, "coverage": "sampled_commanders", "total_available": 0, "commanders": []}


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
    assert [row["commander_slug"] for row in batch.select_commanders(args)] == ["commander-a"]


def test_batch_supports_explicit_small_commander_and_offline_dry_run(tmp_path):
    args = batch_args(tmp_path, "--commanders", "commander-c", "--dry-run", "--sample-decks", "20")
    summary = batch.run_batch(args, runner=lambda _: pytest.fail("Dry run must not collect"))
    assert summary["selected"] == ["commander-c"]
    assert summary["max_preview_attempts"] == 20
    assert summary["max_page_requests"] == 34
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
