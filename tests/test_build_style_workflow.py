"""Regression checks for source-paced collection and honest workflow outcomes."""

import json
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
    assert "--target-completion-hours" not in args
    assert "reset_target" not in COLLECTOR.read_text()


def test_failed_collection_fails_workflow_after_uploading_checkpoints():
    body, script = step(COLLECTOR, "Fail the run after preserving collection progress")
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], text=True, capture_output=True)
    assert result.returncode == 1
    assert "steps.collect_styles.outcome == 'failure'" in body
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
