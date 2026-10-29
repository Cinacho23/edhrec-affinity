#!/usr/bin/env python3
"""Coordinate collection through GitHub metadata; never request EDHREC pages.

Uses only Python's standard library and the runner's GitHub CLI, so watchdogs
and cooldown checks do not install analysis dependencies or restore deck data.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from itertools import tee
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile


COLLECTOR = "build-commander-styles.yml"
COOLDOWN = "resume-build-styles.yml"
FRONTEND = "deploy-frontend.yml"
ENVIRONMENT = "build-style-cooldown"
MAX_WAIT_ATTEMPTS = 48
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def reject_nonfinite(value):
    raise ValueError(f"Invalid nonfinite JSON value: {value}")


@dataclass
class Snapshot:
    run_id: int
    summary: dict
    durable: bool = True


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid GitHub repository")
        self.repository = repository
        self.root = f"repos/{repository}"
        self.default_branch = self.api(self.root)["default_branch"]
        self._runs = None

    def command(self, args, *, body=None):
        result = subprocess.run(["gh", *args], input=body, text=True, capture_output=True, timeout=90)
        if result.returncode:
            raise RuntimeError(f"GitHub command failed: {' '.join(args[:3])}")
        return result.stdout

    def api(self, endpoint, *, body=None):
        args = ["api", endpoint]
        if body is not None:
            args.extend(["--method", "POST", "--input", "-"])
        text = self.command(args, body=json.dumps(body) if body is not None else None)
        return json.loads(text, parse_constant=reject_nonfinite) if text.strip() else None

    def runs(self):
        if self._runs is None:
            self._runs = self.api(f"{self.root}/actions/workflows/{COLLECTOR}/runs?per_page=30")["workflow_runs"]
        return self._runs

    def trusted(self, run, workflow=COLLECTOR):
        return (run.get("head_branch") == self.default_branch and not str(run.get("event", "")).startswith("pull_request")
                and str(run.get("path", "")).split("@", 1)[0].endswith(f"/{workflow}"))

    def active(self, excluding=None):
        return any(self.trusted(run) and run.get("status") in ACTIVE and run["id"] != excluding
                   for run in self.runs())

    def snapshot(self, run, *, allow_uploaded=False):
        statuses = {"completed", "in_progress"} if allow_uploaded else {"completed"}
        if not self.trusted(run) or run.get("status") not in statuses:
            return None
        run_id = run["id"]
        artifacts = self.api(f"{self.root}/actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
        available = [artifact for artifact in artifacts if artifact.get("expired") is False]
        summary_name = f"build-style-summary-{run_id}"
        if not any(artifact["name"] == summary_name for artifact in available):
            return None
        durable = any(artifact["name"].startswith("build-styles-") for artifact in available)
        with tempfile.TemporaryDirectory(prefix="build-style-summary-") as directory:
            self.command(["run", "download", str(run_id), "--repo", self.repository,
                          "--name", summary_name, "--dir", directory])
            path = Path(directory) / "build_style_batch.json"
            value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite)
            if not isinstance(value, dict):
                raise ValueError("Invalid collector summary")
        if value.get("preflight_skipped"):
            return None
        return Snapshot(run_id, value, durable)

    def snapshots(self):
        for run in self.runs():
            if self.trusted(run) and run.get("status") == "completed":
                value = self.snapshot(run)
                if value is not None:
                    yield value

    def source(self, run_id, *, allow_uploaded=False):
        return self.snapshot(self.api(f"{self.root}/actions/runs/{run_id}"), allow_uploaded=allow_uploaded)

    def publication_exists(self, source_run_id):
        runs = self.api(f"{self.root}/actions/workflows/{FRONTEND}/runs?per_page=30")["workflow_runs"]
        for run in runs:
            match = re.fullmatch(r"Publish collector #(\d+)", str(run.get("display_title", "")))
            if (self.trusted(run, FRONTEND) and match and int(match[1]) >= source_run_id
                    and (run.get("status") in ACTIVE or run.get("conclusion") == "success")):
                return True
        return False

    def cooldown_exists(self, source_run_id):
        runs = self.api(f"{self.root}/actions/workflows/{COOLDOWN}/runs?per_page=30")["workflow_runs"]
        title = rf"Resume collector #{source_run_id} after cooldown \(attempt \d+\)"
        for run in sorted(runs, key=lambda value: value["id"], reverse=True):
            if self.trusted(run, COOLDOWN) and re.fullmatch(title, str(run.get("display_title", ""))):
                # A successful earlier attempt may only have queued a longer
                # wait. A later failed attempt must remain repairable.
                return run.get("status") in ACTIVE or run.get("conclusion") == "success"
        return False

    def verify_timer(self):
        environment = self.api(f"{self.root}/environments/{ENVIRONMENT}")
        rules = environment.get("protection_rules", [])
        timers = [rule.get("wait_timer", 0) for rule in rules if rule.get("type") == "wait_timer"]
        if not timers or not all(isinstance(value, int) and not isinstance(value, bool) and value >= 60 for value in timers):
            raise ValueError(f"Environment {ENVIRONMENT} must have a wait timer of at least 60 minutes")
        if any(rule.get("type") == "required_reviewers" and rule.get("reviewers") for rule in rules):
            raise ValueError(f"Environment {ENVIRONMENT} must not require manual reviewers")

    def dispatch(self, workflow, inputs=None):
        self.api(f"{self.root}/actions/workflows/{workflow}/dispatches",
                 body={"ref": self.default_branch, "inputs": inputs or {}})


def actual_rate_limit(summary):
    stats = summary.get("measured_request_rate") or {}
    return (summary.get("blocked") is True and not summary.get("preflight_skipped")
            and summary.get("block_reason") != "access_restriction"
            and type(stats.get("throttles")) is int and stats["throttles"] > 0)


def automatic_continuation_allowed(summary):
    if summary.get("automatic_continuation_allowed") is False:
        return False
    scope = summary.get("scope")
    if scope is None:
        return True  # Older full-catalog pipeline summaries predate this field.
    return (isinstance(scope, dict) and not scope.get("commanders") and not scope.get("max_commanders")
            and not scope.get("refresh") and scope.get("sample_decks") is None)


def block_reason(snapshot, older):
    summary = snapshot.summary
    explicit = summary.get("block_reason")
    if explicit in {"rate_limit", "access_restriction"}:
        return explicit
    if actual_rate_limit(summary):
        return "rate_limit"
    if (summary.get("measured_request_rate") or {}).get("requests_started", 0):
        return "access_restriction"
    # Older cooldown-only jobs did not retain the reason. Recover it from the
    # immediately preceding real collection attempt, never from a stored cap.
    for previous in older:
        if previous.run_id == snapshot.run_id:
            continue
        if not previous.summary.get("blocked"):
            return "unknown"
        if actual_rate_limit(previous.summary):
            return "rate_limit"
        if (previous.summary.get("measured_request_rate") or {}).get("requests_started", 0):
            return "access_restriction"
    return "unknown"


def decision(snapshot, *, now, older=(), allow_bounded=False):
    if snapshot is None:
        return {"run": True, "reason": "No previous durable checkpoint"}
    if not snapshot.durable:
        return {"run": False, "reason": "Latest checkpoint upload is missing; inspect the failed artifact upload"}
    summary = snapshot.summary
    if not allow_bounded and not automatic_continuation_allowed(summary):
        return {"run": False, "reason": "The previous manual or finite batch does not permit automatic continuation"}
    if summary.get("blocked"):
        expiry = timestamp(summary.get("blocked_until"))
        if expiry is None:
            return {"run": False, "reason": "Blocked source has no verified cooldown expiry"}
        if expiry > now:
            return {"run": False, "reason": "Source cooldown is still active", "retry_at": expiry.isoformat()}
        if block_reason(snapshot, older) != "rate_limit":
            return {"run": False, "reason": "Source access restriction requires review"}
        return {"run": True, "reason": "Verified rate-limit cooldown expired"}
    if summary.get("resource_limit"):
        return {"run": False, "reason": "Previous collection reached a resource limit"}
    if allow_bounded and not automatic_continuation_allowed(summary):
        return {"run": True, "reason": "An explicit collector request follows the previous bounded batch"}
    rebuild = summary.get("deck_rebuild")
    if isinstance(rebuild, dict):
        pending = rebuild.get("pending")
        metadata = rebuild.get("metadata_prefetch") or {}
        metadata_blocked = (rebuild.get("metadata_source_blocked") is True
                            or isinstance(metadata, dict) and (metadata.get("blocked") is True
                                or metadata.get("status") == "source_access_stopped"))
        progress = (any(type(rebuild.get(key)) is int and rebuild[key] > 0
                        for key in ("regenerated", "scored_reports", "power_refreshed_reports"))
                    or rebuild.get("durable_metadata_progress") is True)
        if type(pending) is int and pending > 0 and progress and not metadata_blocked:
            return {"run": True, "reason": "Saved package-deck rebuild work remains"}
    checked = timestamp((summary.get("capacity") or {}).get("evaluated_at"))
    if checked is not None:
        retry_delay = 1800 if summary.get("failures") and summary.get("no_progress") else 86400
        if summary.get("remaining_in_batch") == 0 and checked + timedelta(seconds=retry_delay) > now:
            return {"run": False, "reason": "No eligible collection work is due yet"}
    return {"run": True, "reason": "Collection work remains"}


def power_only_readiness(snapshot):
    if snapshot is None or not snapshot.durable:
        return {"run": False, "reason": "Power backfill requires a durable saved collector checkpoint"}
    if snapshot.summary.get("blocked"):
        return {"run": False, "reason": "Saved source controls remain blocked; power backfill will not replace them"}
    if snapshot.summary.get("resource_limit"):
        return {"run": False, "reason": "Previous collection reached a resource limit"}
    rebuild = snapshot.summary.get("deck_rebuild")
    if isinstance(rebuild, dict):
        metadata = rebuild.get("metadata_prefetch")
        if (rebuild.get("metadata_source_blocked") is True or isinstance(metadata, dict)
                and (metadata.get("blocked") is True or metadata.get("status") == "source_access_stopped")):
            return {"run": False, "reason": "Saved card-metadata access controls require review before backfill"}
    return {"run": True, "restore_run_id": snapshot.run_id,
            "reason": "Offline power backfill uses the saved collector checkpoint"}


def prepare_power_only_summary(snapshot, *, now):
    ready = power_only_readiness(snapshot)
    if not ready["run"] or snapshot.summary.get("preflight_skipped"):
        raise ValueError(ready["reason"] if not ready["run"] else "Skipped preflight is not a source checkpoint")
    summary = deepcopy(snapshot.summary)
    summary.update(publish_needed=False, continuation_needed=False, no_progress=True,
                   power_only=True, power_only_source_run_id=snapshot.run_id,
                   power_only_started_at=now.isoformat())
    summary.pop("deck_rebuild", None)
    return summary


def classify_outcome(summary):
    if summary.get("blocked"):
        if timestamp(summary.get("blocked_until")) is not None and (
                summary.get("block_reason") == "rate_limit" or actual_rate_limit(summary)):
            return "rate_limit_paused"
        return "failed"
    return "failed" if summary.get("failures") and summary.get("no_progress") else "success"


def publish_source(client, source, *, pending_changes=False):
    if source is None or not source.durable:
        raise ValueError("Frontend publication requires an uploaded collector checkpoint and summary")
    if not pending_changes and source.summary.get("publish_needed", bool(source.summary.get("completed"))) is not True:
        return {"publication_dispatched": False}
    if client.publication_exists(source.run_id):
        return {"publication_dispatched": False, "publication_current": True}
    client.dispatch(FRONTEND, {"source_run_id": str(source.run_id)})
    return {"publication_dispatched": True, "publication_source_run_id": source.run_id}


def repair_publication(client, source, older):
    if source.summary.get("publish_needed", bool(source.summary.get("completed"))) is True:
        return publish_source(client, source)
    # An unchanged batch still includes previous cumulative reports. If their
    # dispatch failed, publish the newest checkpoint rather than losing that
    # retry opportunity or rolling the website back to an older checkpoint.
    pending = next((previous for previous in older if previous.durable and
                    previous.summary.get("publish_needed", bool(previous.summary.get("completed"))) is True), None)
    if pending is None or client.publication_exists(pending.run_id):
        return {"publication_dispatched": False}
    return publish_source(client, source, pending_changes=True)


def queue_cooldown(client, source, *, now):
    if source is None or not source.durable:
        raise ValueError("Cooldown scheduling requires an uploaded collector checkpoint and summary")
    expiry = timestamp(source.summary.get("blocked_until"))
    if (not actual_rate_limit(source.summary) or not automatic_continuation_allowed(source.summary)
            or expiry is None or expiry <= now):
        return {"cooldown_dispatched": False}
    if client.cooldown_exists(source.run_id):
        return {"cooldown_dispatched": False, "cooldown_current": True}
    client.verify_timer()
    client.dispatch(COOLDOWN, {"source_run_id": str(source.run_id), "attempt": "0"})
    return {"cooldown_dispatched": True, "cooldown_source_run_id": source.run_id}


def recovery_plan(latest, older, *, requested_run_id, now):
    if latest is None or latest.run_id != requested_run_id or latest.durable:
        return {"run": False, "reason": "Recovery must name the latest incomplete trusted collector run"}
    if latest.summary.get("block_reason") == "access_restriction":
        return {"run": False, "reason": "Recovery cannot bypass a source access restriction"}
    known_expiry = timestamp(latest.summary.get("blocked_until"))
    if latest.summary.get("blocked_until") is not None and known_expiry is None:
        return {"run": False, "reason": "Recovery source has an invalid cooldown expiry"}
    if known_expiry is not None and known_expiry > now:
        return {"run": False, "reason": "Source cooldown is still active", "retry_at": known_expiry.isoformat()}
    if latest.summary.get("blocked"):
        expiry = timestamp(latest.summary.get("blocked_until"))
        if expiry is None or block_reason(latest, iter(older)) != "rate_limit":
            return {"run": False, "reason": "Recovery cannot bypass a source access restriction"}
        if expiry > now:
            return {"run": False, "reason": "Source cooldown is still active", "retry_at": expiry.isoformat()}
    checkpoint = next((snapshot for snapshot in older if snapshot.durable), None)
    if checkpoint is None:
        return {"run": False, "reason": "No older durable checkpoint is available for recovery"}
    if checkpoint.summary.get("block_reason") == "access_restriction":
        return {"run": False, "reason": "The restored checkpoint retains a source access restriction"}
    following = older[older.index(checkpoint) + 1:]
    ready = decision(checkpoint, now=now, older=iter(following), allow_bounded=True)
    if not ready["run"]:
        return {"run": False, "reason": f"Restored checkpoint is not ready: {ready['reason']}"}
    validated_rate_control(latest.summary.get("source_rate", {}))
    return {"run": True, "recovery_run_id": latest.run_id, "restore_run_id": checkpoint.run_id,
            "reason": "Explicit recovery from an older checkpoint with newer source controls retained"}


def validated_rate_control(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid recovery source rate controls")
    for field in ("learned_rate", "observed_requests_per_second"):
        rate = value.get(field)
        if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float))
                                 or not math.isfinite(rate) or rate <= 0):
            raise ValueError(f"Invalid recovery {field}")
    count = value.get("throttles", 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("Invalid recovery throttle count")
    return value


def merge_recovery_controls(state, summary, *, source_run_id, checkpoint_run_id, now):
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or not isinstance(state.get("commanders"), dict)):
        raise ValueError("Invalid restored collection checkpoint")
    if state.get("block_reason") == "access_restriction" or summary.get("block_reason") == "access_restriction":
        raise ValueError("Recovery cannot replace a source access restriction with a rate-limit pause")
    for checkpoint in (state, summary):
        expiry = timestamp(checkpoint.get("blocked_until"))
        if checkpoint.get("blocked_until") is not None and expiry is None:
            raise ValueError("Recovery source has an invalid cooldown expiry")
        if expiry is not None and expiry > now:
            raise ValueError("Recovery must wait for the source cooldown to expire")
    previous = validated_rate_control(state.get("source_rate", {}))
    newer = validated_rate_control(summary.get("source_rate", {}))
    merged = dict(previous)
    ceilings = [control["learned_rate"] for control in (previous, newer) if control.get("learned_rate") is not None]
    if ceilings:
        merged["learned_rate"] = min(ceilings)
    observations = [(timestamp(control.get("observed_rate_at")), control) for control in (previous, newer)
                    if control.get("observed_requests_per_second") is not None]
    observations = [(observed, control) for observed, control in observations if observed is not None]
    if observations:
        _, control = max(observations, key=lambda candidate: candidate[0])
        merged.update(observed_requests_per_second=control["observed_requests_per_second"],
                      observed_rate_at=control["observed_rate_at"])
    merged["throttles"] = max(previous.get("throttles", 0), newer.get("throttles", 0))
    throttles = [control.get("last_throttle") for control in (previous, newer)]
    throttles = [item for item in throttles if isinstance(item, dict) and timestamp(item.get("at")) is not None]
    if throttles:
        merged["last_throttle"] = max(throttles, key=lambda item: timestamp(item["at"]))
    state["source_rate"] = merged
    expiries = [value for value in (timestamp(state.get("blocked_until")), timestamp(summary.get("blocked_until")))
                if value is not None]
    if expiries:
        state["blocked_until"] = max(expiries).isoformat()
    if summary.get("blocked"):
        state["block_reason"] = "rate_limit"  # The recovery plan rejects all other block reasons.
    state["recovery"] = {"source_run_id": source_run_id, "checkpoint_run_id": checkpoint_run_id,
                         "recovered_at": now.isoformat()}


def coordinate(client, mode, *, now, current_run_id=None, source_run_id=None, attempt=0,
               recovery_run_id=None, power_only=False):
    if power_only and (mode != "preflight" or recovery_run_id):
        raise ValueError("Power-only readiness is available only for preflight without checkpoint recovery")
    if mode == "prepare-cooldown":
        # The collector dispatches immediately after immutable artifact upload,
        # before its own final bookkeeping steps finish.
        source = client.source(source_run_id, allow_uploaded=True)
        if (source is None or not source.durable or not actual_rate_limit(source.summary)
                or not automatic_continuation_allowed(source.summary)):
            return {"wait": False, "reason": "No durable rate-limit pause to resume"}
        if timestamp(source.summary.get("blocked_until")) is None:
            return {"wait": False, "reason": "No verified rate-limit expiry"}
        if attempt >= MAX_WAIT_ATTEMPTS:
            return {"wait": False, "reason": "Cooldown waiter retry bound reached; watchdog remains available"}
        if timestamp(source.summary.get("blocked_until")) <= now:
            return {"wait": False, **coordinate(client, "watchdog", now=now, current_run_id=current_run_id)}
        client.verify_timer()
        return {"wait": True, "source_run_id": source.run_id, "attempt": attempt,
                "reason": "Waiting through the configured GitHub environment timer"}
    downstream = {}
    prefetched = None
    if mode == "watchdog":
        prefetched = iter(client.snapshots())
        candidate = next(prefetched, None)
        if candidate is not None and candidate.durable:
            history, prefetched = tee(prefetched)
            for name, dispatch in (("publication", lambda: repair_publication(client, candidate, history)),
                                   ("cooldown", lambda: queue_cooldown(client, candidate, now=now))):
                try:
                    downstream.update(dispatch())
                except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                    # A broken downstream route must not stop collection or
                    # prevent the independent publication/cooldown repair.
                    downstream[f"{name}_error"] = str(error)
    if client.active(excluding=current_run_id):
        return {"run": False, "reason": "A collector is already running or queued", **downstream}
    if prefetched is not None:
        latest, snapshots = candidate, prefetched
    else:
        snapshots = iter(client.snapshots())
        latest = next(snapshots, None)
    if mode == "preflight" and power_only:
        result = power_only_readiness(latest)
    elif mode == "preflight" and recovery_run_id:
        result = recovery_plan(latest, list(snapshots), requested_run_id=recovery_run_id, now=now)
    else:
        result = decision(latest, now=now, older=snapshots, allow_bounded=mode == "preflight")
    if mode == "resume" and result.get("retry_at"):
        if latest is not None and block_reason(latest, client.snapshots()) == "rate_limit" and attempt + 1 < MAX_WAIT_ATTEMPTS:
            client.verify_timer()
            origin = next((snapshot for snapshot in client.snapshots() if actual_rate_limit(snapshot.summary)), None)
            if origin is None:
                return result
            client.dispatch(COOLDOWN, {"source_run_id": str(origin.run_id), "attempt": str(attempt + 1)})
            result["requeued"] = True
        return result
    if mode in {"watchdog", "resume"} and result["run"]:
        client.dispatch(COLLECTOR)
        result["dispatched"] = True
    if latest is not None:
        result["snapshot"] = latest.summary
    return {**result, **downstream}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "watchdog", "prepare-cooldown", "resume", "outcome", "recover-control", "publish", "publication", "queue-cooldown", "prepare-power-only"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--current-run-id", type=int, default=int(os.environ.get("GITHUB_RUN_ID", "0")))
    parser.add_argument("--source-run-id", type=int)
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--recovery-run-id", type=int)
    parser.add_argument("--restored-run-id", type=int)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--power-only", action="store_true")
    args = parser.parse_args()
    try:
        if not 0 <= args.attempt <= MAX_WAIT_ATTEMPTS:
            raise ValueError("Invalid cooldown attempt")
        if args.mode in {"prepare-cooldown", "resume"} and not args.source_run_id:
            raise ValueError("A source collector run is required")
        if args.recovery_run_id and args.mode not in {"preflight", "recover-control"}:
            raise ValueError("Checkpoint recovery is available only for an explicit collector request")
        if args.recovery_run_id and os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
            raise ValueError("Checkpoint recovery requires an explicit workflow_dispatch request")
        if args.power_only and (args.mode != "preflight" or args.recovery_run_id):
            raise ValueError("Power-only readiness is available only for preflight without checkpoint recovery")
        if args.mode == "prepare-power-only":
            if not args.source_run_id or args.source_run_id <= 0 or args.summary_file is None:
                raise ValueError("Power backfill requires the exact restored source run and a summary file")
            source = GitHub(args.repository).source(args.source_run_id)
            summary = prepare_power_only_summary(source, now=datetime.now(timezone.utc))
            args.summary_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.summary_file.with_suffix(".power.tmp")
            temporary.write_text(json.dumps(summary, allow_nan=False), encoding="utf-8")
            temporary.replace(args.summary_file)
            result = {"prepared": True, "source_run_id": source.run_id, "power_only": True}
        elif args.mode == "outcome":
            summary = json.loads(args.summary_file.read_text(), parse_constant=reject_nonfinite)
            result = {"status": classify_outcome(summary)}
            if result["status"] == "rate_limit_paused":
                message = ("the separate cooldown timer resumes collection" if automatic_continuation_allowed(summary)
                           else "this bounded run does not restart collection automatically")
                print(f"::notice::Rate-limit cooldown is active. Progress is saved; {message}.")
        elif args.mode in {"publish", "publication", "queue-cooldown"}:
            if args.mode == "publication" and args.source_run_id is None:
                result = {"publish": True}
            else:
                if args.source_run_id is None or args.source_run_id <= 0:
                    raise ValueError("A valid collector source run is required")
                client = GitHub(args.repository)
                source = client.source(args.source_run_id, allow_uploaded=True)
                if args.mode == "publish":
                    result = publish_source(client, source)
                elif args.mode == "queue-cooldown":
                    result = queue_cooldown(client, source, now=datetime.now(timezone.utc))
                elif source is None or not source.durable:
                    raise ValueError("Frontend publication source is not a trusted uploaded collector checkpoint")
                else:
                    # An explicit validated dispatch may repair a missed prior
                    # publication using this newer cumulative checkpoint.
                    result = {"publish": True, "source_run_id": source.run_id}
        elif args.mode == "recover-control":
            if not args.recovery_run_id or not args.restored_run_id or args.state_file is None:
                raise ValueError("Recovery requires the interrupted run, restored run and state file")
            client = GitHub(args.repository)
            snapshots = iter(client.snapshots())
            latest = next(snapshots, None)
            now = datetime.now(timezone.utc)
            plan = recovery_plan(latest, list(snapshots), requested_run_id=args.recovery_run_id, now=now)
            if not plan.get("run") or plan["restore_run_id"] != args.restored_run_id:
                raise ValueError("Restored checkpoint does not match the reviewed recovery plan")
            state = json.loads(args.state_file.read_text(encoding="utf-8"), parse_constant=reject_nonfinite)
            merge_recovery_controls(state, latest.summary, source_run_id=latest.run_id,
                                    checkpoint_run_id=args.restored_run_id, now=now)
            temporary = args.state_file.with_suffix(".recovery.tmp")
            temporary.write_text(json.dumps(state, allow_nan=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(args.state_file)
            result = {"recovered": True, "source_run_id": latest.run_id, "checkpoint_run_id": args.restored_run_id}
        else:
            result = coordinate(GitHub(args.repository), args.mode, now=datetime.now(timezone.utc),
                current_run_id=args.current_run_id, source_run_id=args.source_run_id, attempt=args.attempt,
                recovery_run_id=args.recovery_run_id, power_only=args.power_only)
        snapshot = result.pop("snapshot", {})
        if args.mode == "preflight" and not result["run"] and args.summary_file is not None:
            snapshot.update(publish_needed=False, continuation_needed=False, preflight_skipped=True,
                            measured_request_rate=None, preflight_reason=result["reason"])
            args.summary_file.parent.mkdir(parents=True, exist_ok=True)
            args.summary_file.write_text(json.dumps(snapshot, allow_nan=False), encoding="utf-8")
        output_path = os.environ.get("GITHUB_OUTPUT")
        if output_path:
            with open(output_path, "a", encoding="utf-8") as output:
                for key, value in result.items():
                    output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
        print(json.dumps(result, allow_nan=False))
        return 1 if result.get("publication_error") or result.get("cooldown_error") else 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"::error::{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
