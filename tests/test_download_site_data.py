import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download_site_data.py"
spec = importlib.util.spec_from_file_location("download_site_data", SCRIPT_PATH)
download_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(download_module)


def remote_files(manifest):
    return {
        "site_manifest.json": manifest,
        "leaderboard/index.json": {"pages": []},
        "commanders/index.json": [],
        "tags/index.json": [],
        "theme-brackets/index.json": [],
        "sets/index.json": [],
    }


@pytest.mark.parametrize("include_quirky", [False, True])
def test_download_list_includes_complete_reports_advertised_by_the_manifest(
    monkeypatch, tmp_path, include_quirky
):
    manifest = {
        "summary_files": {"analysis": "summaries/analysis_summary.json"},
        "theme_report_export": {"file": "theme-report.json"},
    }
    if include_quirky:
        # Follow the advertised path rather than requiring a hard-coded name.
        manifest["quirky_builds_export"] = {"file": "reports/quirky-builds.json"}
    files = remote_files(manifest)
    monkeypatch.setattr(download_module, "fetch_json", lambda base_url, path: files[path])

    paths = download_module.build_download_list("https://example.test/", tmp_path)

    expected = {"summaries/analysis_summary.json", "theme-report.json"}
    if include_quirky:
        expected.add("reports/quirky-builds.json")
    assert set(paths) == expected
    assert json.loads((tmp_path / "site_manifest.json").read_text()) == manifest


def test_site_download_writes_quirky_dataset_for_a_fresh_local_checkout(monkeypatch, tmp_path):
    manifest = {"quirky_builds_export": {"file": "quirky-builds.json"}}
    report = {
        "schema_version": 1,
        "coverage": "all_observed_commander_tags",
        "row_fields": ["commander_slug", "theme_affinity_adjusted_pct"],
        "commanders": [],
        "themes": [],
    }
    files = {**remote_files(manifest), "quirky-builds.json": report}
    monkeypatch.setattr(download_module, "fetch_json", lambda base_url, path: files[path])

    download_module.download_site_data("https://example.test", tmp_path)

    assert json.loads((tmp_path / "quirky-builds.json").read_text()) == report
