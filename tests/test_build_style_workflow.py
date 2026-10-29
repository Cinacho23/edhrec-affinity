"""Regression checks for source-paced collection and honest workflow outcomes."""

import json
import glob
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / ".github/workflows/build-commander-styles.yml"
DEPLOY = ROOT / ".github/workflows/deploy-frontend.yml"
pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="Requires bash")


def step(path, name):
    lines = []
    for line in path.read_text().split(f"      - name: {name}\n", 1)[1].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) < 8:
            break
        lines.append(line)
    body = "\n".join(lines)
    script = (textwrap.dedent(body.split("        run: |\n", 1)[1]) if "        run: |\n" in body
              else body.split("        run: ", 1)[1].splitlines()[0])
    return body, script


def stub(tmp_path, name, program):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(program))
    path.chmod(0o755)


def stub_timeout(tmp_path):
    # macOS has no GNU timeout. Exercise the enclosed command while the
    # watchdog's signal, duration and step ceilings are checked separately.
    stub(tmp_path, "timeout", """\
        import os, sys
        args = sys.argv[1:]
        assert args[:2] == ["--signal=INT", "--kill-after=30s"]
        assert args[2] in {"35m", "21m", "32m"}
        os.execvp(args[3], args[3:])
    """)


def test_scheduled_collection_uses_source_freshness_without_a_completion_deadline(tmp_path):
    _, script = step(COLLECTOR, "Continue the resumable catalog queue")
    stub_timeout(tmp_path)
    stub(tmp_path, "python", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "STYLES_DIR": "data/styles", "SAMPLE_DATE": "2026-10-22", "MAX_COMMANDERS": "0",
             "SAMPLE_DECKS": "all", "MAX_COMPONENTS": "all", "REQUESTS_PER_SECOND": "40",
             "COMMANDER_SLUGS": "", "REFRESH_REPORTS": "false"})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[args.index("--freshness-target-hours") + 1] == "96"
    assert args[args.index("--max-runtime-seconds") + 1] == "1800"
    assert args[args.index("--worker-grace-seconds") + 1] == "120"
    assert args[args.index("--workers") + 1] == "1"
    assert args[args.index("--preview-workers") + 1] == "16"
    assert args[args.index("--min-total-decks") + 1] == "200"
    assert args[args.index("--max-components") + 1] == "all"
    assert args[args.index("--main-tag-decks") + 1] == "15"
    assert args[args.index("--min-tag-decks") + 1] == "5"
    assert args[args.index("--main-tag-fraction") + 1] == "0.03"
    assert args[args.index("--min-tag-fraction") + 1] == "0.01"
    assert "--target-completion-hours" not in args
    assert "reset_target" not in COLLECTOR.read_text()
    config = COLLECTOR.read_text().split("      max_components:\n", 1)[1].split("      refresh:\n", 1)[0]
    assert "type: string" in config and 'default: "all"' in config
    assert "MAX_COMPONENTS: ${{ inputs.max_components || 'all' }}" in COLLECTOR.read_text()


def test_failed_collection_fails_workflow_after_uploading_checkpoints():
    body, script = step(COLLECTOR, "Fail the run after preserving collection progress")
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True)
    assert result.returncode == 1
    assert "steps.collect_styles.outcome == 'failure'" in body
    assert "steps.collection_status.outputs.status != 'rate_limit_paused'" in body
    workflow = COLLECTOR.read_text()
    assert workflow.index("      - name: Upload collection summary") < workflow.index(
        "      - name: Fail the run after preserving collection progress")
    assert workflow.index("      - name: Upload cumulative build-style samples") < workflow.index(
        "      - name: Fail the run after preserving collection progress")


@pytest.mark.parametrize("maximum", [None, "all", "0", "64"])
def test_pipeline_cli_has_no_default_completion_deadline(tmp_path, maximum):
    index = tmp_path / "index.json"
    index.write_text("[]")
    ceiling = [] if maximum is None else ["--max-components", maximum]
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_site_build_styles.py"),
        "--pipeline", "--dry-run", "--commander-index", str(index), "--output-dir", str(tmp_path / "styles"), *ceiling],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["target_completion_hours"] is None
    assert summary["capacity"]["deadline_at"] is None
    assert summary["deadline_target_met"] is None
    assert summary["freshness_target_hours"] == 96
    assert summary["model_policy"]["max_components"] == (64 if maximum == "64" else None)


def test_watchdog_and_free_cooldown_waiter_are_separate_from_the_collector():
    watchdog = (ROOT / ".github/workflows/watch-build-styles.yml").read_text()
    waiter = (ROOT / ".github/workflows/resume-build-styles.yml").read_text()
    collector = COLLECTOR.read_text()
    assert 'cron: "7,17,27,37,47,57 * * * *"' in watchdog
    assert "restore-build-styles" not in watchdog and "pip install" not in watchdog
    assert "schedule:" not in collector
    assert "name: build-style-cooldown" in waiter and "deployment: false" in waiter
    assert "prepare-cooldown" in waiter and " resume " in waiter
    assert "sleep" not in collector and "sleep" not in waiter


def test_batch_cli_rejects_a_lower_minimum_before_reading_the_catalog():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_site_build_styles.py"),
                             "--min-total-decks", "0", "--dry-run"], text=True, capture_output=True)
    assert result.returncode == 2
    assert "at least 200" in result.stderr


def test_checkpoint_upload_keeps_durable_inputs_and_excludes_mutable_scratch(tmp_path):
    root = tmp_path / "styles"
    retained = {"commander-a.json", "_collection/state.json", "_collection/previews/a.json.gz",
                "_collection/evidence/a.json.gz", "_collection/sources/a/base.json"}
    scratch = {"_collection/sources/a/previews/deck.rsc", "_collection/sources/a/previews/deck.rsc.meta.json",
               "_collection/sources/a/previews/nested/deck.json", "_collection/analyzed/a/report.json",
               "_collection/state.json.tmp", "_collection/evidence/a.tmp"}
    for relative in retained | scratch:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    body = COLLECTOR.read_text().split("      - name: Upload cumulative build-style samples\n", 1)[1]
    patterns = body.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
    included = set()
    for pattern in textwrap.dedent(patterns).splitlines():
        exclude = pattern.startswith("!")
        pattern = pattern.lstrip("!").replace("${{ env.STYLES_DIR }}", str(root))
        matches = [Path(path) for path in glob.glob(pattern, recursive=True)]
        files = {child for path in matches for child in ([path] if path.is_file() else path.rglob("*")) if child.is_file()}
        included = included - files if exclude else included | files
    assert {str(path.relative_to(root)) for path in included} == retained


def test_explicit_recovery_pins_restore_and_merges_source_controls_before_collection():
    workflow = COLLECTOR.read_text()
    assert "      recovery_run_id:" in workflow
    assert "run-id: ${{ needs.preflight.outputs.restore_run_id }}" in workflow
    restore = workflow.index("      - name: Restore accumulated build-style samples")
    merge = workflow.index("      - name: Preserve newer source limits during explicit checkpoint recovery")
    collect = workflow.index("      - name: Continue the resumable catalog queue")
    assert restore < merge < collect
    _, script = step(COLLECTOR, "Preserve newer source limits during explicit checkpoint recovery")
    assert "recover-control" in script and "--restored-run-id" in script


def test_frontend_routes_an_explicit_source_to_validated_publication_helper(tmp_path):
    _, script = step(DEPLOY, "Inspect collection summary")
    stub(tmp_path, "python3", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "SOURCE_RUN_ID": "123"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["scripts/coordinate_build_styles.py", "publication", "--source-run-id", "123"]
    assert "workflow_run:" not in DEPLOY.read_text()
    assert "workflow_dispatch:" in DEPLOY.read_text() and "  push:" in DEPLOY.read_text()


def test_publication_requires_both_uploads_but_failure_does_not_stop_source_continuation():
    body, script = step(COLLECTOR, "Publish the uploaded collector checkpoint")
    assert "steps.checkpoint.outcome == 'success'" in body
    assert "steps.collection_summary.outcome == 'success'" in body
    assert "continue-on-error: true" in body
    assert " publish --source-run-id " in script
    workflow = COLLECTOR.read_text()
    assert workflow.index("      - name: Continue from the uploaded checkpoint") < workflow.index(
        "      - name: Report a frontend publication failure")


def test_cooldown_dispatch_uses_the_token_exception_after_both_durable_uploads():
    body, script = step(COLLECTOR, "Schedule a saved rate-limit cooldown")
    assert "steps.checkpoint.outcome == 'success'" in body
    assert "steps.collection_summary.outcome == 'success'" in body
    assert "continue-on-error: true" in body
    assert "GH_TOKEN: ${{ github.token }}" in body
    assert 'queue-cooldown --source-run-id "${GITHUB_RUN_ID}"' in script
    waiter = (ROOT / ".github/workflows/resume-build-styles.yml").read_text()
    assert "workflow_run:" not in waiter and "workflow_dispatch:" in waiter
    assert "SOURCE_RUN_ID: ${{ inputs.source_run_id }}" in waiter
    assert "Resume collector #{0} after cooldown (attempt {1})" in waiter


@pytest.mark.parametrize("power_only", [False, True])
def test_saved_deck_upgrade_is_bounded_to_current_reports_and_uploaded_before_publication(tmp_path, power_only):
    body, script = step(COLLECTOR, "Rebuild package decks from saved collections")
    stub_timeout(tmp_path)
    stub(tmp_path, "python", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "STYLES_DIR": "saved/styles", "RUNNER_TEMP": "scratch", "POWER_ONLY": str(power_only).lower()})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[0] == "scripts/rebuild_generated_decks.py"
    assert args[args.index("--build-styles-dir") + 1] == "saved/styles"
    assert args[args.index("--max-runtime-seconds") + 1] == "1200"
    assert args[args.index("--backup-dir") + 1].startswith("scratch/")
    assert {"--only-outdated", "--current-discovery-only"} <= set(args)
    assert ("--scores-only" in args) is power_only
    assert ("--fetch-metadata" in args) is not power_only
    assert "--fetch" not in args
    assert "continue-on-error: true" in body
    workflow = COLLECTOR.read_text()
    assert workflow.index("      - name: Continue the resumable catalog queue") < workflow.index(
        "      - name: Rebuild package decks from saved collections") < workflow.index(
        "      - name: Upload cumulative build-style samples")
    assert "data/build-style-artifact/deck-rebuild-summary.json" in workflow


def test_power_only_mode_prepares_the_exact_restored_source_and_skips_collection(tmp_path):
    workflow = COLLECTOR.read_text()
    config = workflow.split("      power_only:\n", 1)[1].split("      recovery_run_id:\n", 1)[0]
    assert "type: boolean" in config and "default: false" in config
    for name in ("Get current commander index and weekly dataset date", "Continue the resumable catalog queue"):
        body, _ = step(COLLECTOR, name)
        assert "if: inputs.power_only != true" in body
    stub(tmp_path, "python3", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    body, script = step(COLLECTOR, "Prepare saved reports for a power-only pass")
    assert "if: inputs.power_only == true" in body
    assert "RESTORED_RUN_ID: ${{ steps.restored_styles.outputs['run-id'] }}" in body
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "RESTORED_RUN_ID": "123"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["scripts/coordinate_build_styles.py", "prepare-power-only",
        "--source-run-id", "123", "--summary-file", "data/build-style-artifact/build_style_batch.json"]
    assert workflow.index("      - name: Restore shared card rules cache") < workflow.index(
        "      - name: Prepare saved reports for a power-only pass") < workflow.index(
        "      - name: Rebuild package decks from saved collections")
    for name in ("Rebuild package decks from saved collections", "Include rebuilt decks in publication progress",
                 "Decide whether collection can continue immediately"):
        body, _ = step(COLLECTOR, name)
        assert "steps.prepare_power_only.outcome == 'success'" in body
    body = workflow.split("      - name: Save refreshed card rules cache\n", 1)[1].split(
        "      - name: Verify saved card rules cache", 1)[0]
    assert "inputs.power_only != true" in body
    _, dispatch = step(COLLECTOR, "Continue from the uploaded checkpoint")
    assert "--scores-only" not in dispatch and "power_only" not in dispatch


@pytest.mark.parametrize("power_only", [False, True])
def test_power_only_preflight_uses_its_explicit_offline_guard(tmp_path, power_only):
    _, script = step(COLLECTOR, "Check active collectors and source cooldown")
    stub(tmp_path, "python3", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "POWER_ONLY": str(power_only).lower(), "RECOVERY_RUN_ID": ""})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[:3] == ["scripts/coordinate_build_styles.py", "preflight", "--summary-file"]
    assert ("--power-only" in args) is power_only


def test_work_stages_leave_time_for_checkpoint_upload_before_the_job_deadline():
    workflow = COLLECTOR.read_text()
    build = workflow.split("  build:\n", 1)[1]
    job_minutes = int(re.search(r"^    timeout-minutes: (\d+)$", build, re.M)[1])
    collect, collect_script = step(COLLECTOR, "Continue the resumable catalog queue")
    rebuild, rebuild_script = step(COLLECTOR, "Rebuild package decks from saved collections")
    work_minutes = sum(int(re.search(r"^        timeout-minutes: (\d+)$", body, re.M)[1])
                       for body in (collect, rebuild))
    assert job_minutes - work_minutes >= 30
    assert "timeout --signal=INT --kill-after=30s 35m python" in collect_script
    assert "timeout --signal=INT --kill-after=30s 21m python" in rebuild_script
    assert all("continue-on-error: true" in body for body in (collect, rebuild))
    checkpoint = workflow.split("      - name: Upload cumulative build-style samples\n", 1)[1].split(
        "      - name: Upload collection summary", 1)[0]
    assert "if: always()" in checkpoint
    assert "timeout-minutes: 15" in checkpoint
    assert "compression-level: 1" in checkpoint


@pytest.mark.parametrize("blocked, automatic, pending, expected_continuation", [
    (False, True, 5, True), (True, True, 5, False),
    (False, False, 5, False), (False, True, 0, False),
])
def test_successful_deck_rebuilds_publish_without_erasing_collection_controls(
    tmp_path, blocked, automatic, pending, expected_continuation,
):
    _, script = step(COLLECTOR, "Include rebuilt decks in publication progress")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    source = {"blocked": blocked, "blocked_until": "2026-10-25T23:00:00Z", "no_progress": True,
              "automatic_continuation_allowed": automatic, "publish_needed": False,
              "continuation_needed": False, "measured_request_rate": {"successful_responses": 12}}
    path = root / "build_style_batch.json"
    path.write_text(json.dumps(source))
    rebuild = {"generation_version": 2, "regenerated": 3, "pending": pending, "blocked": 1,
               "changed_deck_lists": 30, "distinctive_omissions_before": 70,
               "distinctive_omissions_after": 2, "time_budget_reached": bool(pending)}
    (root / "deck-rebuild-summary.json").write_text(json.dumps(rebuild))
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True,
        env={**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    output = json.loads(path.read_text())
    assert output["publish_needed"] is True and output["no_progress"] is False
    assert output["continuation_needed"] is expected_continuation
    assert output["deck_rebuild"] == rebuild
    for field in ("blocked", "blocked_until", "automatic_continuation_allowed", "measured_request_rate"):
        assert output[field] == source[field]


def test_refreshed_metadata_cache_is_saved_before_handled_failures_and_continuation():
    workflow = COLLECTOR.read_text()
    restore = workflow.split("      - name: Restore shared card rules cache\n", 1)[1].split(
        "      - name: Get current commander index", 1)[0]
    save = workflow.split("      - name: Save refreshed card rules cache\n", 1)[1].split(
        "      - name: Verify saved card rules cache", 1)[0]
    assert "id: card_metadata_cache" in restore
    assert "uses: actions/cache/restore@v4" in restore
    assert "${{ github.run_attempt }}" in restore
    assert "uses: actions/cache/save@v4" in save
    assert "if: always()" in save
    assert "hashFiles('data/raw/build-style-card-metadata.json') != ''" in save
    assert "key: ${{ steps.card_metadata_cache.outputs.cache-primary-key }}" in save
    assert "continue-on-error: true" in save
    assert workflow.index("      - name: Rebuild package decks from saved collections") < workflow.index(
        "      - name: Save refreshed card rules cache") < workflow.index(
        "      - name: Verify saved card rules cache") < workflow.index(
        "      - name: Include rebuilt decks in publication progress")
    assert workflow.index("      - name: Save refreshed card rules cache") < workflow.index(
        "      - name: Fail the run after preserving collection progress")
    body, _ = step(COLLECTOR, "Include rebuilt decks in publication progress")
    assert "METADATA_CACHE_SAVED: ${{ steps.verify_card_metadata.outputs.verified == 'true' }}" in body


@pytest.mark.parametrize("progress", [
    {"scored_reports": 7, "scored_decks": 25, "calculated_decks": 25},
    {"power_refreshed_reports": 7},
])
@pytest.mark.parametrize("blocked, automatic, pending, expected_continuation", [
    (False, True, 5, True), (True, True, 5, False),
    (False, False, 5, False), (False, True, 0, False),
])
def test_saved_fast_power_updates_publish_before_full_regeneration_without_claiming_decks_rebuilt(
    tmp_path, progress, blocked, automatic, pending, expected_continuation,
):
    _, script = step(COLLECTOR, "Include rebuilt decks in publication progress")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    path = root / "build_style_batch.json"
    source = {"blocked": blocked, "automatic_continuation_allowed": automatic,
              "no_progress": True, "publish_needed": False, "continuation_needed": False}
    path.write_text(json.dumps(source))
    rebuilt = {"generation_version": 4, "regenerated": 0, "pending": pending, "blocked": 0,
               "changed_deck_lists": 0, "time_budget_reached": True, "scoring_pending": 3, **progress}
    (root / "deck-rebuild-summary.json").write_text(json.dumps(rebuilt))
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True,
        env={**os.environ, "METADATA_CACHE_SAVED": "false",
             "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    output = json.loads(path.read_text())
    assert output["publish_needed"] is True
    assert output["no_progress"] is False
    assert output["continuation_needed"] is expected_continuation
    assert output["deck_rebuild"] == rebuilt
    assert output["deck_rebuild"]["regenerated"] == output["deck_rebuild"]["changed_deck_lists"] == 0
    for key in ("blocked", "automatic_continuation_allowed"):
        assert output[key] == source[key]


def test_fast_power_attempts_without_a_committed_report_do_not_claim_publishable_progress(tmp_path):
    _, script = step(COLLECTOR, "Include rebuilt decks in publication progress")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    path = root / "build_style_batch.json"
    source = {"automatic_continuation_allowed": True, "no_progress": True,
              "publish_needed": False, "continuation_needed": False}
    path.write_text(json.dumps(source))
    (root / "deck-rebuild-summary.json").write_text(json.dumps({
        "regenerated": 0, "pending": 12, "scored_reports": 0, "scored_decks": 0,
        "power_refreshed_reports": 0, "calculated_decks": 0, "scoring_pending": 12,
    }))
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True, env={**os.environ, "METADATA_CACHE_SAVED": "false",
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    output = json.loads(path.read_text())
    for key in ("no_progress", "publish_needed", "continuation_needed"):
        assert output[key] == source[key]


@pytest.mark.parametrize("entries, expected", [
    ([{"key": "cards-123-1", "ref": "refs/heads/main"}], True),
    ([], False),
    ([{"key": "cards-123-1-longer", "ref": "refs/heads/main"}], False),
    ([{"key": "cards-123-1", "ref": "refs/heads/feature"}], False),
    ([{"key": "other", "ref": "refs/heads/main"}, {"key": "cards-123-1", "ref": "refs/heads/main"}], True),
])
def test_remote_metadata_cache_proof_requires_the_exact_key_and_ref(tmp_path, entries, expected):
    body, script = step(COLLECTOR, "Verify saved card rules cache")
    assert "if: always() && steps.save_card_metadata.outcome == 'success'" in body
    assert "continue-on-error: true" in body
    stub(tmp_path, "gh", """\
        import json, os, sys
        assert sys.argv[1:] == ["api", "--method", "GET", "repos/owner/repo/actions/caches",
                               "-f", "key=cards-123-1", "-f", "ref=refs/heads/main"]
        print(os.environ["CACHE_INVENTORY"])
    """)
    output = tmp_path / "outputs"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True, env={**os.environ,
            "PATH": str(tmp_path) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path), "GITHUB_REPOSITORY": "owner/repo", "GITHUB_REF": "refs/heads/main",
            "CARD_METADATA_CACHE_KEY": "cards-123-1", "GITHUB_OUTPUT": str(output),
            "CACHE_INVENTORY": json.dumps({"actions_caches": entries})})
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"verified={str(expected).lower()}"


def test_remote_cache_lookup_failure_cannot_claim_metadata_durability(tmp_path):
    _, script = step(COLLECTOR, "Verify saved card rules cache")
    stub(tmp_path, "gh", "raise SystemExit(1)\n")
    output = tmp_path / "outputs"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True, env={**os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REPOSITORY": "owner/repo", "GITHUB_REF": "refs/heads/main", "GITHUB_OUTPUT": str(output),
            "CARD_METADATA_CACHE_KEY": "cards-123-1"})
    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("fetched, durable, cached, pending, source_blocked, metadata_status, metadata_blocked, resource, automatic, expected_progress, expected_continue", [
    (3, False, True, 5, False, "partial", False, False, True, True, True),
    (0, True, True, 5, False, "time_budget_reached", False, False, True, True, True),
    (0, False, True, 5, False, "complete", False, False, True, False, False),
    (True, False, True, 5, False, "partial", False, False, True, False, False),
    (3, True, False, 5, False, "partial", False, False, True, False, False),
    (3, False, True, 0, False, "complete", False, False, True, True, False),
    (3, False, True, 5, True, "partial", False, False, True, True, False),
    (3, False, True, 5, False, "source_access_stopped", False, False, True, True, False),
    (3, False, True, 5, False, "partial", True, False, True, True, False),
    (3, False, True, 5, False, "partial", False, True, True, True, False),
    (3, False, True, 5, False, "partial", False, False, False, True, False),
])
def test_metadata_only_progress_continues_from_a_saved_remote_cache_without_claiming_report_publication(
    tmp_path, fetched, durable, cached, pending, source_blocked, metadata_status, metadata_blocked,
    resource, automatic, expected_progress, expected_continue,
):
    _, script = step(COLLECTOR, "Include rebuilt decks in publication progress")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    source = {"blocked": source_blocked, "block_reason": "access_restriction" if source_blocked else None,
              "resource_limit": resource, "automatic_continuation_allowed": automatic, "no_progress": True,
              "publish_needed": False, "continuation_needed": False,
              "measured_request_rate": {"successful_responses": 0}}
    metadata = {"status": metadata_status, "requested": 300, "processed": 150, "resolved": 150,
                "fetched": fetched, "requests": 2, "durable_cache_saved": durable, "blocked": metadata_blocked,
                "missing": ["Large card-level diagnostic"]}
    path = root / "build_style_batch.json"
    path.write_text(json.dumps(source))
    (root / "deck-rebuild-summary.json").write_text(json.dumps({
        "generation_version": 4, "regenerated": 0, "pending": pending, "blocked": 0,
        "time_budget_reached": True, "metadata_prefetch": metadata,
    }))
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True, env={**os.environ, "METADATA_CACHE_SAVED": str(cached).lower(),
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    output = json.loads(path.read_text())
    assert output["publish_needed"] is False
    assert output["no_progress"] is not expected_progress
    assert output["continuation_needed"] is expected_continue
    rebuilt = output["deck_rebuild"]
    assert rebuilt["metadata_cache_saved"] is cached
    assert rebuilt["durable_metadata_progress"] is expected_progress
    assert rebuilt["metadata_source_blocked"] is (metadata_blocked or metadata_status == "source_access_stopped")
    assert rebuilt["metadata_prefetch"] == {key: value for key, value in metadata.items() if key != "missing"}
    for field in ("blocked", "block_reason", "resource_limit", "automatic_continuation_allowed", "measured_request_rate"):
        assert output[field] == source[field]


@pytest.mark.parametrize("changes, env_changes, expected", [
    ({}, {}, True),
    ({"blocked": True}, {}, False),
    ({"resource_limit": True}, {}, False),
    ({"automatic_continuation_allowed": False}, {}, False),
    ({"no_progress": True}, {}, False),
    ({"deck_rebuild": {"metadata_source_blocked": True}}, {}, False),
    ({}, {"TARGETED_COMMANDERS": "the-indomitable"}, False),
    ({}, {"MAX_COMMANDERS": "1"}, False),
    ({}, {"SAMPLE_DECKS": "60"}, False),
    ({}, {"REFRESH_REPORTS": "true"}, False),
    ({}, {"GITHUB_REF_NAME": "feature"}, False),
])
def test_immediate_continuation_retains_source_resource_and_manual_scope_controls(tmp_path, changes, env_changes, expected):
    _, script = step(COLLECTOR, "Decide whether collection can continue immediately")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    (root / "build_style_batch.json").write_text(json.dumps({
        "continuation_needed": True, "no_progress": False, **changes,
    }))
    output = tmp_path / "outputs"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        text=True, capture_output=True, env={**os.environ, "TARGETED_COMMANDERS": "", "MAX_COMMANDERS": "0",
            "SAMPLE_DECKS": "all", "REFRESH_REPORTS": "false", "GITHUB_REF_NAME": "main", "DEFAULT_BRANCH": "main",
            "GITHUB_OUTPUT": str(output), "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            **env_changes})
    assert result.returncode == 0, result.stderr
    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {
        "needed": str(expected).lower(), "validate_power": "false"}


def test_official_validation_mode_is_paced_bounded_and_uses_saved_observations(tmp_path):
    workflow = COLLECTOR.read_text()
    assert "      validate_power:\n" in workflow
    for name in ("Get current commander index and weekly dataset date", "Continue the resumable catalog queue",
                 "Rebuild package decks from saved collections"):
        body, _ = step(COLLECTOR, name)
        assert "inputs.validate_power != true" in body
    body, script = step(COLLECTOR, "Check EDHPowerLevel restrictions and repair saved decks")
    assert "inputs.validate_power == true" in body
    assert "steps.prepare_power_only.outcome == 'success'" in body
    assert "continue-on-error: true" in body and "timeout-minutes: 33" in body
    stub_timeout(tmp_path)
    stub(tmp_path, "python", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "STYLES_DIR": "saved/styles",
             "RUNNER_TEMP": "scratch", "COMMANDER_SLUGS": "nemata-grove-guardian the-face-of-boe"})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[0] == "scripts/validate_generated_decks.py"
    assert args[args.index("--validation-directory") + 1] == "saved/styles/_collection/external-validations"
    assert args[args.index("--max-runtime-seconds") + 1] == "1800"
    assert args[args.index("--max-decks") + 1] == "300"
    assert args[args.index("--check-interval-seconds") + 1] == "5"
    assert args[-4:] == ["--commander", "nemata-grove-guardian", "--commander", "the-face-of-boe"]
    assert workflow.index("      - name: Retain checked repository examples") < workflow.index(
        "      - name: Check EDHPowerLevel restrictions") < workflow.index("      - name: Upload cumulative build-style samples")
    assert "official-validation-summary.json" in workflow


@pytest.mark.parametrize("current_check,blocked,expected_publish", [(True, False, True), (True, True, True), (False, False, False)])
def test_official_observations_publish_without_faking_collection_progress(tmp_path, current_check, blocked, expected_publish):
    _, script = step(COLLECTOR, "Include EDHPowerLevel checks in publication progress")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    styles = root / "build-styles"
    (styles / "_collection").mkdir(parents=True)
    source = {"blocked": False, "resource_limit": False, "publish_needed": False, "no_progress": True,
        "automatic_continuation_allowed": True, "continuation_needed": False,
        "source_rate": {"learned_rate": .12}, "scope": {"max_commanders": None}}
    path = root / "build_style_batch.json"
    path.write_text(json.dumps(source))
    observed = {"source": "edhpowerlevel.com", "checked": 10, "changed_reports": 4, "pending": 200,
        "source_blocked": blocked, "retry_at": "2099-01-01T00:00:00Z" if blocked else None,
        "continuation_needed": not blocked}
    target = root / "official-validation-summary.json" if current_check else styles / "_collection/official-validation-summary.json"
    target.write_text(json.dumps(observed))
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, "VALIDATE_POWER": str(current_check).lower(), "STYLES_DIR": str(styles),
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    actual = json.loads(path.read_text())
    assert actual["publish_needed"] is expected_publish
    assert actual["no_progress"] is (not current_check)
    assert actual["continuation_needed"] is (current_check and not blocked)
    assert actual["official_validation"]["pending"] == 200
    assert actual["source_rate"] == source["source_rate"]
    assert actual["scope"] == source["scope"] and actual["blocked"] is False


@pytest.mark.parametrize("current,official,completed,expected", [
    (False, {"pending": 10}, [], "true"),
    (True, {"pending": 10}, [], "false"),
    (False, {"pending": 0}, ["commander"], "true"),
    (False, {"pending": 10, "source_blocked": True, "retry_at": "2099-01-01T00:00:00Z"}, [], "false"),
    (False, {"pending": 10, "source_blocked": True, "retry_at": "2000-01-01T00:00:00Z"}, [], "true"),
    (False, {"pending": 10, "retry_at": "2099-01-01T00:00:00Z"}, [], "false"),
])
def test_continuation_alternates_source_and_official_work_and_honors_retry_time(tmp_path, current, official, completed, expected):
    _, script = step(COLLECTOR, "Decide whether collection can continue immediately")
    root = tmp_path / "data/build-style-artifact"
    root.mkdir(parents=True)
    (root / "build_style_batch.json").write_text(json.dumps({"continuation_needed": True, "no_progress": False,
        "official_validation": official, "completed": completed}))
    output = tmp_path / "outputs"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, "TARGETED_COMMANDERS": "", "MAX_COMMANDERS": "0", "SAMPLE_DECKS": "all",
            "REFRESH_REPORTS": "false", "CURRENT_VALIDATION": str(current).lower(),
            "GITHUB_REF_NAME": "main", "DEFAULT_BRANCH": "main", "GITHUB_OUTPUT": str(output),
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {"needed": "true", "validate_power": expected}
