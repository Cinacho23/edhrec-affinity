#!/usr/bin/env python3
"""Promote browser collection only after immutable smoke, review and pilot proof.

Uses the runner's ordinary GitHub CLI authentication. This helper makes no
provider requests, changes no source controls and never writes repository
variables. It must run outside the collectors' shared concurrency group so its
child workflows can acquire that group.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import quote

from coordinate_build_styles import GitHub, ACTIVE, COLLECTOR, DISTRIBUTED_COLLECTOR, reject_nonfinite, timestamp

SMOKE = "test-browser-collection.yml"
POLL_SECONDS = 30
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
STAGE_TIMEOUTS = {"smoke": 25 * 60, "access_review": 100 * 60, "pilot": 180 * 60}


def utcnow():
    return datetime.now(timezone.utc)


def identifier(value):
    if not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Invalid source workflow run ID")
    return str(value)


def require(value, message):
    if not value:
        raise ValueError(message)


def cautious_rate(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 < value <= 0.1)


def unblocked(summary):
    require(isinstance(summary, dict), "Collector summary is invalid")
    require(not summary.get("preflight_skipped"), "Collector preflight skipped source work")
    require(summary.get("blocked") is False and not summary.get("block_reason")
            and not summary.get("source_stop"), "Collector retains a source stop")
    require(not summary.get("failures") and not summary.get("resource_limit"), "Collector reported failures or resource exhaustion")
    stats = summary.get("measured_request_rate")
    require(isinstance(stats, dict) and stats.get("blocked") is False and not stats.get("block_kind")
            and stats.get("throttles") == 0, "Measured source traffic was blocked or throttled")
    require(type(summary.get("new_previews_attempted")) is int and summary["new_previews_attempted"] > 0
            and type(stats.get("successful_responses")) is int and stats["successful_responses"] > 0,
            "Collector did not prove new preview progress")


def validate_smoke(proof, run_id, sha):
    require(isinstance(proof, dict) and proof.get("passed") is True and proof.get("transport") == "webkit",
            "Browser smoke proof did not pass")
    require(proof.get("configured_requests_per_second") == 0.1, "Smoke did not use the initial cautious browser rate")
    require(str(proof.get("github_run_id")) == str(run_id) and proof.get("github_commit") == sha,
            "Smoke proof belongs to another workflow run or commit")
    checks = proof.get("checks")
    require(isinstance(checks, list) and len(checks) == 2 and all(isinstance(row, dict) for row in checks),
            "Smoke proof does not contain exactly two source checks")
    gallery, preview = checks
    require(gallery.get("kind") == "gallery" and gallery.get("valid") is True
            and gallery.get("source_commander_verified") is True
            and gallery.get("complete_gallery_index_verified") is True
            and type(gallery.get("index_rows")) is int and gallery["index_rows"] > 0
            and gallery["index_rows"] == gallery.get("unique_deck_ids") == gallery.get("displayed_total"),
            "Smoke gallery identity or complete population was not verified")
    require(preview.get("kind") == "preview" and preview.get("valid") is True
            and preview.get("source_preview_id_verified") is True
            and preview.get("cards_including_commanders") == 100,
            "Smoke did not verify the known 100-card preview")
    require(proof.get("checkpoint_controls_changed") is False and proof.get("collector_dispatched") is False,
            "Smoke proof unexpectedly changed production controls")


def validate_review(summary, proof, source_run_id):
    unblocked(summary)
    require(cautious_rate(summary.get("request_ceiling_per_second")), "Bounded review did not retain the cautious browser rate")
    require(isinstance(proof, dict) and proof.get("passed") is True and proof.get("released") is True
            and proof.get("edhrec_transport") == "webkit" and str(proof.get("source_run_id")) == source_run_id,
            "Access review did not release the exact requested checkpoint through WebKit")
    checks = proof.get("checks")
    require(isinstance(checks, list) and len(checks) == 4
            and all(isinstance(check, dict) and check.get("valid") is True
                    and check.get("status_code") == 200 and check.get("challenge") is False for check in checks),
            "Access review lacks four unrestricted service checks")
    require(bool(summary.get("collected")), "The bounded review collected no commander previews")


def validate_pilot(summary, proof, run_id):
    unblocked(summary)
    distributed = summary.get("distributed")
    require(isinstance(distributed, dict) and distributed.get("runners") == 2
            and distributed.get("pilot") is True and str(distributed.get("generation")) == str(run_id)
            and distributed.get("systemic_block") is False
            and not any(distributed.get(key) for key in ("missing_workers", "fallback_workers", "failed_workers")),
            "Two-machine pilot lacks an intact unblocked generation")
    require(isinstance(proof, dict) and proof.get("status") == "pass"
            and proof.get("passed") is True and proof.get("allowed_scale_up") is True
            and proof.get("pilotpass") is True and proof.get("machine_count") == 2
            and str(proof.get("generation")) == str(run_id) and not proof.get("reasons"),
            "The two-machine pilot did not permit scale-up")
    require(distributed.get("pilot_gate") == proof, "Pilot summary and immutable gate artifact differ")
    require(cautious_rate(proof.get("configured_requests_per_second")) and proof.get("collect_seconds") == 900,
            "Pilot did not use the expected cautious rate and 900-second budget")
    workers = distributed.get("workers")
    require(isinstance(workers, list) and len(workers) == 2, "Pilot worker source summaries are missing")
    for worker in workers:
        require(isinstance(worker, dict), "Pilot worker summary is invalid")
        pipeline = worker.get("pipeline_summary")
        require(isinstance(pipeline, dict) and type(pipeline.get("new_previews_attempted")) is int
                and pipeline["new_previews_attempted"] > 0,
                "A pilot worker did not prove new individual-preview progress")
        stats = pipeline.get("measured_request_rate")
        require(isinstance(stats, dict) and cautious_rate(stats.get("configured_maximum_requests_per_second")),
                "A pilot worker did not retain the cautious browser ceiling")


class Rollout:
    def __init__(self, client, output, *, now=utcnow, monotonic=time.monotonic, sleep=time.sleep):
        self.client, self.output = client, Path(output)
        self.now, self.monotonic, self.sleep = now, monotonic, sleep
        self.output.mkdir(parents=True, exist_ok=True)
        self.receipt = {"schema_version": 1, "started_at": now().isoformat(), "status": "running",
                        "repository": client.repository, "stages": [], "provider_requests_made": False,
                        "repository_variables_changed": False, "source_controls_changed_by_helper": False}

    def save(self):
        path = self.output / "rollout.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def main_sha(self):
        branch = quote(self.client.default_branch, safe="")
        value = self.client.api(f"{self.client.root}/commits/{branch}").get("sha")
        require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value), "Default-branch commit is invalid")
        return value

    def workflow_runs(self, workflow):
        branch = quote(self.client.default_branch, safe="")
        payload = self.client.api(f"{self.client.root}/actions/workflows/{workflow}/runs?event=workflow_dispatch&branch={branch}&per_page=30")
        require(isinstance(payload, dict) and isinstance(payload.get("workflow_runs"), list), "Workflow run listing is invalid")
        return payload["workflow_runs"]

    def trusted(self, run, workflow):
        return (isinstance(run, dict) and self.client.trusted(run, workflow)
                and run.get("event") == "workflow_dispatch" and run.get("head_sha") == self.receipt["commit"]
                and run.get("head_repository", {}).get("full_name") == self.client.repository)

    def dispatch(self, stage_name, workflow, inputs):
        require(self.main_sha() == self.receipt["commit"], "Default branch changed during rollout; restart after reviewing the new commit")
        self.client._runs = None
        require(not self.client.active(), "Another collector is active; rollout did not dispatch competing source work")
        before = {run.get("id") for run in self.workflow_runs(workflow)}
        cutoff = self.now().replace(microsecond=0)
        stage = {"name": stage_name, "workflow": workflow, "inputs": inputs, "dispatched_at": cutoff.isoformat(),
                 "status": "dispatching"}
        self.receipt["stages"].append(stage)
        self.save()
        self.client.dispatch(workflow, inputs)
        deadline = self.monotonic() + 300
        while True:
            matches = [run for run in self.workflow_runs(workflow)
                       if run.get("id") not in before and self.trusted(run, workflow)
                       and (created := timestamp(run.get("created_at"))) is not None and created >= cutoff]
            require(len(matches) <= 1, "More than one matching dispatched workflow appeared; refusing ambiguous promotion")
            if matches:
                run = matches[0]
                stage.update(run_id=run["id"], url=f"https://github.com/{self.client.repository}/actions/runs/{run['id']}",
                             status=run["status"])
                self.save()
                print(f"{stage_name}: {stage['url']} ({stage['status']})", flush=True)
                return stage
            require(self.monotonic() < deadline, "Dispatched workflow could not be identified within five minutes")
            self.sleep(POLL_SECONDS)

    def wait(self, stage):
        deadline = self.monotonic() + STAGE_TIMEOUTS[stage["name"]]
        while True:
            run = self.client.api(f"{self.client.root}/actions/runs/{stage['run_id']}")
            require(self.trusted(run, stage["workflow"]), "Workflow identity or pinned commit changed")
            if stage.get("status") != run.get("status"):
                stage["status"] = run.get("status")
                self.save()
                print(f"{stage['name']}: {stage['status']}", flush=True)
            if run.get("status") == "completed":
                stage["conclusion"] = run.get("conclusion")
                self.save()
                require(run.get("conclusion") == "success", f"{stage['name']} workflow did not succeed; no later stage was dispatched")
                return
            require(run.get("status") in ACTIVE, "Workflow returned an unsupported execution state")
            require(self.monotonic() < deadline, f"{stage['name']} workflow exceeded its bounded wait; no later stage was dispatched")
            self.sleep(POLL_SECONDS)

    def artifact(self, stage, name, *, require_checkpoint=False):
        run_id = stage["run_id"]
        payload = self.client.api(f"{self.client.root}/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = payload.get("artifacts", [])
        require(isinstance(artifacts, list), "Workflow artifact listing is invalid")
        available = [row for row in artifacts if isinstance(row, dict) and row.get("expired") is False]
        matches = [row for row in available if row.get("name") == name]
        require(len(matches) == 1, "Required proof artifact is missing, expired or ambiguous")
        row = matches[0]
        size = row.get("size_in_bytes")
        require(type(size) is int and 0 < size <= MAX_ARTIFACT_BYTES, "Proof artifact is unexpectedly large or invalid")
        if require_checkpoint:
            require(any(str(row.get("name", "")).startswith("build-styles-") for row in available),
                    "Collector saved no durable cumulative checkpoint")
        destination = self.output / stage["name"]
        require(not destination.exists(), "Proof destination already exists; use a fresh rollout output directory")
        destination.mkdir()
        self.client.command(["run", "download", str(run_id), "--repo", self.client.repository,
                             "--name", name, "--dir", str(destination)])
        stage["artifact"] = {key: row[key] for key in ("id", "name", "size_in_bytes", "digest") if key in row}
        self.save()
        return destination

    def proof(self, stage, directory, filename):
        path = directory / filename
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ARTIFACT_BYTES,
                "Required proof file is missing or invalid")
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8"), parse_constant=reject_nonfinite)
        stage.setdefault("proof_files", {})[filename] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        self.save()
        return value

    def execute(self, source_run_id, runners):
        try:
            source_run_id = identifier(source_run_id)
            require(type(runners) is int and 2 <= runners <= 16, "Runner pool must contain 2–16 machines")
            self.receipt.update(source_run_id=source_run_id, target_runners=runners, commit=self.main_sha())
            self.save()
            smoke = self.dispatch("smoke", SMOKE, {})
            self.wait(smoke)
            directory = self.artifact(smoke, f"browser-collection-proof-{smoke['run_id']}")
            validate_smoke(self.proof(smoke, directory, "receipt.json"), smoke["run_id"], self.receipt["commit"])
            smoke["proof_verified"] = True
            self.save()
            print("smoke: source proof verified", flush=True)
            review = self.dispatch("access_review", COLLECTOR, {"access_review_run_id": source_run_id})
            self.wait(review)
            directory = self.artifact(review, f"build-style-summary-{review['run_id']}", require_checkpoint=True)
            validate_review(self.proof(review, directory, "build_style_batch.json"),
                            self.proof(review, directory, "access-review.json"), source_run_id)
            review["proof_verified"] = True
            self.save()
            print("access_review: release and new preview progress verified", flush=True)
            pilot = self.dispatch("pilot", DISTRIBUTED_COLLECTOR,
                                  {"runners": "2", "pilot": "true", "collect_seconds": "900"})
            self.wait(pilot)
            directory = self.artifact(pilot, f"build-style-summary-{pilot['run_id']}", require_checkpoint=True)
            validate_pilot(self.proof(pilot, directory, "build_style_batch.json"),
                           self.proof(pilot, directory, "distributed-pilot.json"), pilot["run_id"])
            pilot["proof_verified"] = True
            self.save()
            print("pilot: two-machine gate verified", flush=True)
            full = self.dispatch("full_collection", DISTRIBUTED_COLLECTOR, {"runners": str(runners), "pilot": "false"})
            self.receipt.update(status="collection_dispatched", full_collection_run_id=full["run_id"])
        except Exception as error:
            self.receipt.update(status="stopped", error_type=type(error).__name__, error=str(error)[:700])
        finally:
            self.receipt["completed_at"] = self.now().isoformat()
            self.save()
        return self.receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--runners", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    client = GitHub(os.environ.get("GITHUB_REPOSITORY", "Cinacho23/edhrec-affinity"))
    result = Rollout(client, args.output).execute(args.source_run_id, args.runners)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] == "collection_dispatched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
