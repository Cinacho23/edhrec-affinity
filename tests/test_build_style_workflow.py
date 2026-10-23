"""Regression checks for source-paced collection and honest workflow outcomes."""

import json
import glob
import os
from pathlib import Path
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
    return body, textwrap.dedent(body.split("        run: |\n", 1)[1])


def stub(tmp_path, name, program):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(program))
    path.chmod(0o755)


def test_scheduled_collection_uses_source_freshness_without_a_completion_deadline(tmp_path):
    _, script = step(COLLECTOR, "Continue the resumable catalog queue")
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
    assert args[args.index("--min-total-decks") + 1] == "200"
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


@pytest.mark.parametrize("publish", [True, False])
def test_failed_collector_only_publishes_when_summary_reports_changes(tmp_path, publish):
    _, script = step(DEPLOY, "Inspect collection summary")
    (tmp_path / "python").symlink_to(sys.executable)
    stub(tmp_path, "gh", """\
        import os, pathlib, sys
        destination = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
        (destination / "build_style_batch.json").write_text(os.environ["TEST_SUMMARY"])
    """)
    output = tmp_path / "output"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "RUNNER_TEMP": str(tmp_path), "SOURCE_RUN_ID": "123", "GITHUB_EVENT_NAME": "workflow_run",
             "GITHUB_REPOSITORY": "owner/repository", "GITHUB_OUTPUT": str(output),
             "TEST_SUMMARY": json.dumps({"blocked": True, "publish_needed": publish})})
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f"publish={str(publish).lower()}\n"
    assert "contains(fromJSON('[\"success\", \"failure\"]'), github.event.workflow_run.conclusion)" in DEPLOY.read_text()
