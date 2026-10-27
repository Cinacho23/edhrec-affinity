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
        assert args[2] in {"35m", "21m"}
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
             "SAMPLE_DECKS": "all", "MAX_COMPONENTS": "8", "REQUESTS_PER_SECOND": "40",
             "COMMANDER_SLUGS": "", "REFRESH_REPORTS": "false"})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[args.index("--freshness-target-hours") + 1] == "96"
    assert args[args.index("--max-runtime-seconds") + 1] == "1800"
    assert args[args.index("--worker-grace-seconds") + 1] == "120"
    assert args[args.index("--workers") + 1] == "1"
    assert args[args.index("--preview-workers") + 1] == "16"
    assert args[args.index("--min-total-decks") + 1] == "200"
    assert args[args.index("--main-tag-decks") + 1] == "15"
    assert args[args.index("--min-tag-decks") + 1] == "5"
    assert args[args.index("--main-tag-fraction") + 1] == "0.03"
    assert args[args.index("--min-tag-fraction") + 1] == "0.01"
    assert "--target-completion-hours" not in args
    assert "reset_target" not in COLLECTOR.read_text()


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


def test_pipeline_cli_has_no_default_completion_deadline(tmp_path):
    index = tmp_path / "index.json"
    index.write_text("[]")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_site_build_styles.py"),
        "--pipeline", "--dry-run", "--commander-index", str(index), "--output-dir", str(tmp_path / "styles")],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["target_completion_hours"] is None
    assert summary["capacity"]["deadline_at"] is None
    assert summary["deadline_target_met"] is None
    assert summary["freshness_target_hours"] == 96


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


def test_saved_deck_upgrade_is_bounded_to_current_reports_and_uploaded_before_publication(tmp_path):
    body, script = step(COLLECTOR, "Rebuild package decks from saved collections")
    stub_timeout(tmp_path)
    stub(tmp_path, "python", """\
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "STYLES_DIR": "saved/styles", "RUNNER_TEMP": "scratch"})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[0] == "scripts/rebuild_generated_decks.py"
    assert args[args.index("--build-styles-dir") + 1] == "saved/styles"
    assert args[args.index("--max-runtime-seconds") + 1] == "1200"
    assert args[args.index("--backup-dir") + 1].startswith("scratch/")
    assert {"--only-outdated", "--current-discovery-only", "--fetch-metadata"} <= set(args)
    assert "--fetch" not in args
    assert "continue-on-error: true" in body
    workflow = COLLECTOR.read_text()
    assert workflow.index("      - name: Continue the resumable catalog queue") < workflow.index(
        "      - name: Rebuild package decks from saved collections") < workflow.index(
        "      - name: Upload cumulative build-style samples")
    assert "data/build-style-artifact/deck-rebuild-summary.json" in workflow


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
