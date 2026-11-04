#!/usr/bin/env python3
"""Select and restore only the immutable artifacts needed by a distributed merge.

Uses the runner's ordinary GitHub CLI authentication. No source requests are made.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

RESERVE_BYTES = 2 * 1024 ** 3
EXTRACTION_ALLOWANCE_BYTES = 64 * 1024 ** 2
WORKFLOW = ".github/workflows/collect-distributed-build-styles.yml"


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.repository = repository

    def run(self, args):
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=1800)
        if result.returncode:
            # Do not dump authentication or signed-storage URL diagnostics.
            raise RuntimeError(f"GitHub artifact operation failed: {' '.join(args[:3])}")
        return result.stdout

    def api(self, endpoint):
        path = f"repos/{self.repository}" + (f"/{endpoint}" if endpoint else "")
        return json.loads(self.run(["api", path]))

    def artifacts(self, run_id):
        pages = json.loads(self.run(["api", "--paginate", "--slurp",
            f"repos/{self.repository}/actions/runs/{run_id}/artifacts?per_page=100"]))
        return [artifact for page in pages for artifact in page["artifacts"]]

    def download(self, run_id, name, destination):
        self.run(["run", "download", str(run_id), "--repo", self.repository,
                  "--name", name, "--dir", str(destination)])


def run_id(value):
    if not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Invalid source run ID")
    return str(value)


def available_artifacts(artifacts):
    result = {}
    for artifact in artifacts:
        if artifact.get("expired") is not False:
            continue
        name = artifact.get("name")
        if not isinstance(name, str) or name in result:
            raise ValueError("Invalid or ambiguous artifact identity")
        size = artifact.get("size_in_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("Invalid artifact size")
        result[name] = artifact
    return result


def merge_artifact_plan(manifest, artifacts, source_run_id):
    source_run_id = run_id(source_run_id)
    if not isinstance(manifest, dict) or str(manifest.get("generation")) != source_run_id or manifest.get("schema_version") != 1:
        raise ValueError("Manifest does not match the requested generation")
    workers = manifest.get("workers")
    if not isinstance(workers, list) or not 2 <= len(workers) <= 16:
        raise ValueError("Invalid worker count")
    available = available_artifacts(artifacts)
    chosen = []
    for index, worker in enumerate(workers):
        if not isinstance(worker, dict) or worker.get("id") != str(index):
            raise ValueError("Invalid worker identity")
        result = f"distributed-result-{source_run_id}-{index}"
        fallback = f"distributed-input-{source_run_id}-{index}"
        name, kind = (result, "result") if result in available else (fallback, "fallback")
        if name not in available:
            raise ValueError(f"No saved result or original fallback for worker {index}")
        chosen.append({"worker_id": str(index), "name": name, "kind": kind,
                       "size_in_bytes": available[name]["size_in_bytes"]})
    return chosen


def check_recovery(client, source_run_id, current_run_id):
    source_run_id, current_run_id = run_id(source_run_id), run_id(current_run_id)
    if int(source_run_id) >= int(current_run_id):
        raise ValueError("Recovery must reference an earlier generation")
    default_branch = client.api("")["default_branch"]
    source = client.api(f"actions/runs/{source_run_id}")
    if (source.get("path", "").split("@", 1)[0] != WORKFLOW or source.get("head_branch") != default_branch
            or source.get("event") != "workflow_dispatch" or source.get("status") != "completed"
            or source.get("conclusion") not in {"failure", "cancelled", "timed_out"}):
        raise ValueError("Recovery requires a completed interrupted distributed generation on the default branch")
    jobs = client.api(f"actions/runs/{source_run_id}/jobs?per_page=100")["jobs"]
    plans = [job for job in jobs if job.get("name") == "Restore one canonical checkpoint and partition owned work"]
    if len(plans) != 1 or plans[0].get("conclusion") != "success":
        raise ValueError("The interrupted generation has no successful immutable plan")
    artifacts = available_artifacts(client.artifacts(source_run_id))
    for name in [f"distributed-base-{source_run_id}", f"distributed-shared-{source_run_id}"]:
        if name not in artifacts:
            raise ValueError(f"Recovery artifact expired or missing: {name}")
    if not any(name.startswith(f"distributed-result-{source_run_id}-") for name in artifacts):
        raise ValueError("No completed worker results to recover")
    # Never roll a newer canonical checkpoint back to an older interrupted run.
    recent = []
    for workflow in ("collect-distributed-build-styles.yml", "build-commander-styles.yml"):
        page = 1
        while True:
            runs = client.api(f"actions/workflows/{workflow}/runs?per_page=100&page={page}")["workflow_runs"]
            recent.extend(runs)
            # Runs are returned newest first. Scan through the requested
            # generation even if many newer failed/manual runs exist.
            if len(runs) < 100 or any(int(run["id"]) <= int(source_run_id) for run in runs):
                break
            page += 1
    for other in recent:
        if (other.get("head_branch") == default_branch and not str(other.get("event", "")).startswith("pull_request")
                and int(other["id"]) > int(source_run_id)
                and str(other["id"]) != current_run_id and other.get("status") == "completed"):
            if any(a.get("expired") is False and str(a.get("name", "")).startswith("build-styles-")
                   for a in client.artifacts(other["id"])):
                raise ValueError("A newer canonical checkpoint already exists; do not recover an older baseline")
    with tempfile.TemporaryDirectory(prefix="merge-recovery-manifest-") as temporary:
        client.download(source_run_id, f"distributed-shared-{source_run_id}", Path(temporary))
        manifest = json.loads((Path(temporary) / "manifest.json").read_text())
        chosen = merge_artifact_plan(manifest, list(artifacts.values()), source_run_id)
        if manifest.get("runners") != len(chosen) or not isinstance(manifest.get("pilot"), bool):
            raise ValueError("Recovery manifest has invalid runner or pilot configuration")
    return {"source_run_id": source_run_id, "runners": str(len(chosen)),
            "pilot": str(manifest["pilot"]).lower()}


def restore_merge_artifacts(client, manifest, source_run_id, results, inputs):
    chosen = merge_artifact_plan(manifest, client.artifacts(source_run_id), source_run_id)
    results, inputs = Path(results), Path(inputs)
    results.mkdir(parents=True, exist_ok=True)
    inputs.mkdir(parents=True, exist_ok=True)
    required = sum(item["size_in_bytes"] + EXTRACTION_ALLOWANCE_BYTES for item in chosen) + RESERVE_BYTES
    free = shutil.disk_usage(results).free
    if free < required:
        raise ValueError(f"Insufficient disk for selected merge artifacts: need {required:,} bytes including reserve; {free:,} free. Saved artifacts remain unchanged.")
    for item in chosen:
        destination = results / item["name"] if item["kind"] == "result" else inputs / item["worker_id"]
        destination.mkdir(parents=True, exist_ok=False)
        client.download(source_run_id, item["name"], destination)
        print(f"Restored {item['kind']} for worker {item['worker_id']}; {shutil.disk_usage(results).free:,} bytes free", flush=True)
    return {"result_workers": [item["worker_id"] for item in chosen if item["kind"] == "result"],
            "fallback_workers": [item["worker_id"] for item in chosen if item["kind"] == "fallback"],
            "selected_archive_bytes": sum(item["size_in_bytes"] for item in chosen)}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("command", choices=["check-recovery", "restore"])
    cli.add_argument("--source-run-id", required=True, type=run_id)
    cli.add_argument("--manifest", type=Path)
    cli.add_argument("--results", type=Path, default=Path("data/distributed-results"))
    cli.add_argument("--inputs", type=Path, default=Path("data/distributed-inputs"))
    args = cli.parse_args()
    client = GitHub(os.environ["GITHUB_REPOSITORY"])
    if args.command == "check-recovery":
        config = check_recovery(client, args.source_run_id, os.environ["GITHUB_RUN_ID"])
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("".join(f"{key}={value}\n" for key, value in config.items()))
        print(f"Validated merge-only recovery for {config['source_run_id']}; source request controls will be retained")
    else:
        if not args.manifest:
            raise ValueError("--manifest is required for restoring a merge")
        manifest = json.loads(args.manifest.read_text())
        print(json.dumps(restore_merge_artifacts(client, manifest, args.source_run_id, args.results, args.inputs)))


if __name__ == "__main__":
    main()
