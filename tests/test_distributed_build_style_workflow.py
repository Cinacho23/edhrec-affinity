"""Distributed workflow contracts: owned inputs, real VMs and one publisher."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/collect-distributed-build-styles.yml"
LEGACY = ROOT / ".github/workflows/build-commander-styles.yml"


def step(name):
    lines = []
    for line in WORKFLOW.read_text().split(f"      - name: {name}\n", 1)[1].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) < 8:
            break
        lines.append(line)
    body = "\n".join(lines)
    script = (textwrap.dedent(body.split("        run: |\n", 1)[1]) if "        run: |\n" in body
              else body.split("        run: ", 1)[1].splitlines()[0] if "        run: " in body else "")
    return body, script


def test_distributed_workflow_uses_real_vm_matrix_and_separate_parallel_power_job():
    workflow = WORKFLOW.read_text()
    assert "group: commander-build-style-samples" in workflow
    assert "cancel-in-progress: false" in workflow
    collect = workflow.split("  collect:\n", 1)[1].split("  power:\n", 1)[0]
    assert "runs-on: ubuntu-latest" in collect
    assert "max-parallel: 16" in collect and "fail-fast: false" in collect
    assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in collect
    power = workflow.split("  power:\n", 1)[1].split("  merge:\n", 1)[0]
    assert "needs: plan" in power and "needs: collect" not in power
    assert "inputs.pilot != true" in power
    assert "--workers 2" in power and "--max-runtime-seconds 1800" in power
    assert "needs: [plan, collect, power]" in workflow
    assert "if: always() && needs.plan.result == 'success'" in workflow


def test_metadata_pacing_is_divided_across_source_vms_and_power_vm():
    workflow = WORKFLOW.read_text()
    _, plan = step("Get the current index and partition commander ownership")
    assert 'metadata_delay_seconds={(runners + 1) / 5}' in plan
    assert "metadata_delay_seconds: ${{ steps.plan.outputs.metadata_delay_seconds }}" in workflow
    for job, following in (("collect", "power"), ("power", "merge")):
        body = workflow.split(f"  {job}:\n", 1)[1].split(f"  {following}:\n", 1)[0]
        assert "BUILD_STYLE_METADATA_REQUEST_DELAY_SECONDS: ${{ needs.plan.outputs.metadata_delay_seconds }}" in body


def test_each_vm_downloads_only_its_owned_shard_and_shared_metadata():
    workflow = WORKFLOW.read_text()
    collect = workflow.split("  collect:\n", 1)[1].split("  power:\n", 1)[0]
    assert "distributed-base-" not in collect
    assert "name: distributed-input-${{ github.run_id }}-${{ matrix.id }}" in collect
    assert "name: distributed-shared-${{ github.run_id }}" in collect
    assert "--commander-index data/distributed-shared/owned-commander-index.json" in collect
    assert 'owned = set(worker["commanders"])' in collect
    assert 'if row["commander_slug"] in owned' in collect
    assert "--manifest data/distributed-shared/manifest.json --worker" in collect
    assert "--exit-code" in collect


def test_only_merger_uploads_a_canonical_restore_candidate():
    workflow = WORKFLOW.read_text()
    names = re.findall(r"^          name: (.+)$", workflow, re.M)
    canonical = [name for name in names if name.startswith("build-styles-")]
    assert canonical == ["build-styles-distributed-${{ github.run_id }}"]
    for worker in range(16):
        assert f"name: distributed-input-${{{{ github.run_id }}}}-{worker}" in workflow
    merger = workflow.split("  merge:\n", 1)[1]
    assert "distributed-input-${{ github.run_id }}-*" in merger
    assert "--inputs data/distributed-inputs" in merger
    assert "--generation" in merger
    assert merger.index("Merge only each machine's assigned commander state") < merger.index(
        "Upload the single canonical cumulative checkpoint") < merger.index("Publish the immutable distributed checkpoint")
    upload = workflow.split("      - name: Upload the canonical summary and pilot evidence\n", 1)[1].split(
        "      - name:", 1)[0]
    paths = re.findall(r"^            (data/.+)$", upload, re.M)
    assert paths and all(Path(path).parent.as_posix() == "data/build-style-artifact" for path in paths)


def test_machine_evidence_and_checkpoint_upload_survive_collection_failure():
    body, _ = step("Collect and analyze this owned commander queue")
    assert "continue-on-error: true" in body
    body, script = step("Capture final public egress and checkpoint owned results")
    assert "if: always() && steps.extract.outcome == 'success'" in body
    assert "--phase after" in script and "--exit-code" in script
    body, _ = step("Upload only this VM's durable results and evidence")
    assert "if: always() && steps.extract.outcome == 'success'" in body
    assert "path: data/distributed-result/" in body


def test_initial_machine_evidence_is_small_available_early_and_cannot_block_source_work():
    workflow = WORKFLOW.read_text()
    capture, _ = step("Capture this machine's initial public egress")
    assert "continue-on-error: true" in capture
    upload, _ = step("Upload early machine identity evidence")
    assert "if: always() && steps.extract.outcome == 'success'" in upload
    assert "continue-on-error: true" in upload
    assert "name: distributed-machine-${{ github.run_id }}-${{ matrix.id }}" in upload
    assert "path: data/distributed-result/egress-before.json" in upload
    assert "if-no-files-found: warn" in upload
    assert workflow.index("Capture this machine's initial public egress") < workflow.index(
        "Upload early machine identity evidence") < workflow.index("Collect and analyze this owned commander queue")
    _, checkpoint = step("Capture final public egress and checkpoint owned results")
    assert '|| echo "::warning::Final egress capture failed' in checkpoint


def test_pilot_is_two_machines_bounded_and_requires_explicit_review_for_scale_up():
    workflow = WORKFLOW.read_text()
    _, script = step("Get the current index and partition commander ownership")
    assert "pilot and runners != 2" in script
    assert "900 if pilot else 5400" in script
    assert "pilot and seconds > 1800" in script
    body, script = step("Assess the two-machine pilot without automatically increasing runners")
    assert "inputs.pilot == true" in body
    assert "check_distributed_pilot.py assess" in script
    body, script = step("Continue approved full collection from this uploaded generation")
    assert "inputs.pilot != true" in body
    assert '--distributed-runners "${{ needs.plan.outputs.runners }}"' in script
    assert "actions/variables" not in workflow


@pytest.mark.skipif(not shutil.which("bash"), reason="Requires bash")
def test_worker_shell_invokes_owned_minimum_200_uncapped_pipeline_with_bounded_watchdog(tmp_path):
    _, script = step("Collect and analyze this owned commander queue")
    for name, program in {
        "python": 'import json,sys; print(json.dumps(sys.argv[1:]))',
        "timeout": 'import os,sys; assert sys.argv[1:4]==["--signal=INT","--kill-after=30s","1080s"]; os.execvp(sys.argv[4],sys.argv[4:])',
    }.items():
        executable = tmp_path / name
        executable.write_text(f"#!{sys.executable}\n{program}\n")
        executable.chmod(0o755)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "STYLES_DIR": "owned/styles",
             "COLLECT_SECONDS": "900", "REQUESTS_PER_SECOND": "1.318359375"})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    for flag, expected in {"--min-total-decks": "200", "--sample-decks": "all", "--max-components": "all",
                           "--workers": "2", "--preview-workers": "16", "--analysis-workers": "2",
                           "--max-runtime-seconds": "900", "--worker-grace-seconds": "120",
                           "--requests-per-second": "1.318359375",
                           "--commander-index": "data/distributed-shared/owned-commander-index.json"}.items():
        assert args[args.index(flag) + 1] == expected


@pytest.mark.skipif(not shutil.which("bash"), reason="Requires bash")
def test_all_embedded_distributed_shell_steps_have_valid_syntax():
    workflow = WORKFLOW.read_text()
    for block in workflow.split("        run: |\n")[1:]:
        lines = []
        for line in block.splitlines():
            if line.strip() and not line.startswith("          "):
                break
            lines.append(line[10:])
        script = re.sub(r"\$\{\{.*?\}\}", "test-value", "\n".join(lines))
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_legacy_debug_routes_and_watchdog_inherit_distributed_rollout_setting():
    workflow = LEGACY.read_text()
    preflight = workflow.split("      - name: Check active collectors and source cooldown\n", 1)[1].split(
        "      - name: Retain a skipped-run summary", 1)[0]
    assert "--route-distributed" in preflight
    assert '-z "${RECOVERY_RUN_ID}"' in preflight and '-z "${COMMANDER_SLUGS:-}"' in preflight
    assert '"${MAX_COMMANDERS:-0}" == "0"' in preflight
    assert '"${SAMPLE_DECKS:-all}" == "all"' in preflight
    assert "coordinate_build_styles.py continue --source-run-id" in workflow
    for name in ("build-commander-styles.yml", "watch-build-styles.yml", "resume-build-styles.yml"):
        assert "vars.BUILD_STYLE_DISTRIBUTED_RUNNERS" in (ROOT / ".github/workflows" / name).read_text()
