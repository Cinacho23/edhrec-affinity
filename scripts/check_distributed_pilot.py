#!/usr/bin/env python3
"""Capture runner egress and conservatively assess a two-machine EDHREC pilot.

A pass proves only this observed pair of runners. It does not establish that
EDHREC's request limits are scoped to IP addresses or permit an unmonitored
sixteen-machine run. Missing evidence never authorizes scaling.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

SERVICES = {"ipify": "https://api.ipify.org", "aws_checkip": "https://checkip.amazonaws.com"}
MIN_OVERLAP_SECONDS = 600
MIN_SUCCESSFUL_REQUESTS = 600
MIN_WORKER_RPS = 0.8
MIN_AGGREGATE_RPS = 1.8
BASELINE_RPS = 1.13
MAX_FAILURE_FRACTION = 0.01


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    def invalid(value):
        raise ValueError("Nonfinite JSON number")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def public_ipv4(value):
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value.strip())
        return str(address) if address.version == 4 and address.is_global else None
    except ValueError:
        return None


def fetch_ip(url):
    request = Request(url, headers={"User-Agent": "edhrec-affinity-distributed-pilot/1.0", "Accept": "text/plain"})
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("Egress service did not return HTTP 200")
        return response.read(128).decode("ascii").strip()


def capture(*, phase, run_id, job_id, shard_id, runner_name=None, fetch=fetch_ip, now=utcnow):
    """Capture two independent HTTPS observations without storing headers/tokens."""
    if phase not in {"before", "after"}:
        raise ValueError("Capture phase must be before or after")
    values, errors, reasons = {}, {}, []
    for name, url in SERVICES.items():
        try:
            address = public_ipv4(fetch(url))
            if address is None:
                raise ValueError("Service returned no global IPv4 literal")
            values[name] = address
        except Exception as error:
            # Error messages can embed proxy credentials or request details.
            # Keep only a safe exception class, never repr(error) or headers.
            errors[name] = type(error).__name__
    agreed = len(values) == len(SERVICES) and len(set(values.values())) == 1
    identity = {"run_id": str(run_id or ""), "job_id": str(job_id or ""), "shard_id": str(shard_id or ""),
                "runner_name": str(runner_name if runner_name is not None else os.environ.get("RUNNER_NAME", ""))}
    if not agreed:
        reasons.append("The two independent HTTPS services did not verify the same global IPv4 address.")
    if not all(identity.values()):
        reasons.append("Runner, run, job, or shard identity is missing.")
    return {"schema_version": 1, "phase": phase, "captured_at": now(), **identity,
            "status": "verified" if agreed and not reasons else "inconclusive",
            "public_ipv4": next(iter(values.values())) if agreed else None,
            "observations": values, "service_errors": errors, "reasons": reasons}


def number(value, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or integer and type(value) is not int):
        return None
    return value


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def assess(manifest, summaries_dir, *, now=utcnow):
    """Assess trusted artifacts, failing closed on absent or contradictory proof."""
    failures, unknown, metrics, windows, addresses, jobs, runners, runs = [], [], [], [], [], [], [], []
    report = {"schema_version": 1, "assessed_at": now(), "status": "inconclusive", "passed": False,
        "allowed_scale_up": False, "pilotpass": False, "machine_count": 0, "proven_requests": 0,
        "aggregate_successful_rps": 0.0, "overlap_seconds": 0.0, "baseline_requests_per_second": BASELINE_RPS,
        "scope": "Observed two-runner EDHREC pilot only; source limit scope remains unknown.",
        "per_worker": metrics, "reasons": [], "thresholds": {"overlap_seconds": MIN_OVERLAP_SECONDS,
            "successful_requests_per_worker": MIN_SUCCESSFUL_REQUESTS, "successful_rps_per_worker": MIN_WORKER_RPS,
            "aggregate_successful_rps": MIN_AGGREGATE_RPS, "maximum_transient_failure_fraction": MAX_FAILURE_FRACTION}}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        failures.append("Manifest schema is invalid.")
        workers = []
    else:
        workers = manifest.get("workers")
        if not isinstance(workers, list):
            workers = []
    generation = manifest.get("generation") if isinstance(manifest, dict) else None
    report["generation"] = generation
    if not isinstance(generation, str) or not generation:
        failures.append("Manifest generation is missing.")
    if len(workers) != 2:
        failures.append("Pilot must contain exactly two worker jobs.")
    seen_ids, assigned = set(), set()
    for worker in workers:
        if not isinstance(worker, dict):
            failures.append("Worker assignment is invalid.")
            continue
        worker_id = worker.get("id")
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", worker_id) or worker_id in seen_ids:
            failures.append("Worker IDs must be unique safe strings.")
            continue
        seen_ids.add(worker_id)
        label = f"Worker {worker_id}"
        names = worker.get("commanders")
        valid_names = isinstance(names, list) and bool(names) and all(isinstance(name, str) and name for name in names)
        if not valid_names or len(set(names)) != len(names):
            failures.append(f"{label}: commander assignments are invalid or duplicated.")
            names = []
        if assigned.intersection(names):
            failures.append(f"{label}: commander assignments overlap another worker.")
        assigned.update(names)
        matches = list(Path(summaries_dir).rglob(f"shard-{worker_id}-summary.json"))
        if len(matches) != 1:
            (unknown if not matches else failures).append(f"{label}: expected exactly one worker summary; found {len(matches)}.")
            continue
        summary_path = matches[0]
        try:
            summary = read_json(summary_path)
        except (OSError, ValueError):
            unknown.append(f"{label}: worker summary is unreadable.")
            continue
        if not isinstance(summary, dict):
            failures.append(f"{label}: worker summary is invalid.")
            continue
        if summary.get("schema_version") != 1:
            failures.append(f"{label}: worker summary schema is invalid.")
        if summary.get("generation") != generation or summary.get("worker_id") != worker_id:
            failures.append(f"{label}: summary generation or worker identity does not match the manifest.")
        if not isinstance(summary.get("commanders"), list) or sorted(summary["commanders"]) != sorted(names):
            failures.append(f"{label}: summary commander assignments do not match the manifest.")
        if type(summary.get("exit_code")) is not int or summary["exit_code"] != 0:
            failures.append(f"{label}: collection did not exit successfully.")
        checkpoint = summary.get("checkpoint") or {}
        if not isinstance(checkpoint, dict) or checkpoint.get("valid") is not True or checkpoint.get("used_fallback") is not False:
            failures.append(f"{label}: checkpoint is missing, invalid, or a fallback.")
        else:
            archive, expected = checkpoint.get("archive"), checkpoint.get("sha256")
            if (not isinstance(archive, str) or Path(archive).name != archive
                    or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
                failures.append(f"{label}: checkpoint archive identity is invalid.")
            else:
                path = summary_path.parent / archive
                if not path.is_file() or digest(path) != expected:
                    failures.append(f"{label}: checkpoint archive is missing or its checksum does not match.")
        machine = summary.get("machine") or {}
        machine = machine if isinstance(machine, dict) else {}
        captures = []
        for phase in ("before", "after"):
            path = summary_path.parent / f"egress-{phase}.json"
            try:
                evidence = read_json(path) if path.is_file() else machine.get(phase)
            except (OSError, ValueError):
                evidence = None
            if not isinstance(evidence, dict):
                unknown.append(f"{label}: {phase} egress proof is missing or unreadable.")
                continue
            captures.append(evidence)
        if len(captures) == 2:
            before, after = captures
            verified_captures = True
            for evidence, phase in zip(captures, ("before", "after"), strict=True):
                ip = public_ipv4(evidence.get("public_ipv4"))
                observations = evidence.get("observations")
                services_agree = isinstance(observations, dict) and set(observations) == set(SERVICES) and all(
                    public_ipv4(value) == ip and ip is not None for value in observations.values())
                if (evidence.get("schema_version") != 1 or evidence.get("status") != "verified"
                        or evidence.get("phase") != phase or not services_agree or evidence.get("service_errors")):
                    unknown.append(f"{label}: {phase} egress was not independently verified by both HTTPS services.")
                    verified_captures = False
                if evidence.get("shard_id") != worker_id:
                    failures.append(f"{label}: {phase} egress proof belongs to another shard.")
            identity_fields = ("run_id", "job_id", "shard_id", "runner_name")
            if any(not isinstance(before.get(key), str) or not before[key] or before.get(key) != after.get(key)
                   for key in identity_fields):
                failures.append(f"{label}: runner/job identity is missing or changed during collection.")
            if public_ipv4(before.get("public_ipv4")) is None or before.get("public_ipv4") != after.get("public_ipv4"):
                unknown.append(f"{label}: the public IPv4 address was unverified or changed during collection.")
            elif verified_captures:
                addresses.append(before["public_ipv4"])
            jobs.append(before.get("job_id"))
            runners.append(before.get("runner_name"))
            runs.append(before.get("run_id"))
        pipeline = summary.get("pipeline_summary")
        if not isinstance(pipeline, dict):
            unknown.append(f"{label}: measured collection summary is missing.")
            continue
        for value in (summary.get("errors"), pipeline.get("errors"), pipeline.get("failures"),
                      pipeline.get("invalid"), summary.get("invalid")):
            if value:
                failures.append(f"{label}: collection reported errors, failures, or invalid data.")
                break
        stats = pipeline.get("measured_request_rate")
        if not isinstance(stats, dict):
            unknown.append(f"{label}: actual measured source request statistics are missing.")
            continue
        control = summary.get("source_control") or {}
        blocked_until = timestamp(control.get("blocked_until")) if isinstance(control, dict) else None
        if (pipeline.get("blocked") or stats.get("blocked") or stats.get("block_kind")
                or blocked_until is not None and blocked_until > timestamp(report["assessed_at"])):
            failures.append(f"{label}: source access is blocked or cooling down.")
        throttle = number(stats.get("throttles"), integer=True)
        if throttle is None:
            unknown.append(f"{label}: new source throttle count is missing.")
        elif throttle:
            failures.append(f"{label}: source returned a new throttle.")
        status_counts = stats.get("status_counts") or stats.get("http_status_counts") or {}
        if isinstance(status_counts, dict) and any(status_counts.get(str(code), status_counts.get(code, 0))
                                                   for code in (401, 403, 429)):
            failures.append(f"{label}: source returned HTTP 401, 403, or 429.")
        started, successes, failed = [number(stats.get(key), integer=True)
                                      for key in ("requests_started", "successful_responses", "failures")]
        elapsed = number(stats.get("elapsed_seconds"))
        beginning = timestamp(stats.get("started_at") or stats.get("source_started_at"))
        ending = timestamp(stats.get("completed_at") or stats.get("source_completed_at"))
        if None in (started, successes, failed, elapsed, beginning, ending):
            unknown.append(f"{label}: source counts or actual UTC collection window are missing.")
            continue
        duration = (ending - beginning).total_seconds()
        if duration <= 0 or elapsed <= 0 or successes > started or successes + failed > started:
            failures.append(f"{label}: measured source counts or collection window are inconsistent.")
            continue
        if len(captures) == 2:
            before_at, after_at = timestamp(captures[0].get("captured_at")), timestamp(captures[1].get("captured_at"))
            if before_at is None or after_at is None:
                unknown.append(f"{label}: egress capture UTC timestamps are missing.")
            elif beginning < before_at or ending > after_at or before_at > after_at:
                failures.append(f"{label}: actual collection does not fall within the stable-egress capture window.")
        seconds = max(duration, elapsed)
        rate, fraction = successes / seconds, failed / started if started else 1.0
        metrics.append({"worker_id": worker_id, "requests_started": started, "successful_requests": successes,
            "failures": failed, "transient_failure_fraction": fraction, "successful_rps": rate,
            "source_started_at": beginning.isoformat(), "source_completed_at": ending.isoformat(),
            "source_duration_seconds": duration, "measured_elapsed_seconds": elapsed,
            "public_ipv4": captures[0].get("public_ipv4") if len(captures) == 2 else None})
        windows.append((beginning, ending))
        if successes < MIN_SUCCESSFUL_REQUESTS:
            failures.append(f"{label}: fewer than {MIN_SUCCESSFUL_REQUESTS} successful source requests were proven.")
        if rate < MIN_WORKER_RPS:
            failures.append(f"{label}: successful source throughput was below {MIN_WORKER_RPS} requests/second.")
        if fraction > MAX_FAILURE_FRACTION:
            failures.append(f"{label}: transient failures exceeded 1% of source requests.")
    if len(addresses) == 2 and len(set(addresses)) != 2:
        failures.append("Workers did not have distinct stable public IPv4 addresses.")
    if len(jobs) == 2 and len(set(jobs)) != 2:
        failures.append("Evidence does not identify two distinct worker jobs.")
    if len(runs) == 2 and len(set(runs)) != 1:
        failures.append("Workers do not belong to the same workflow run.")
    report["machine_count"] = len(set(addresses))
    report["proven_requests"] = sum(value["successful_requests"] for value in metrics)
    report["aggregate_successful_rps"] = sum(value["successful_rps"] for value in metrics)
    if len(windows) == 2:
        report["overlap_seconds"] = max(0, (min(end for _, end in windows) - max(start for start, _ in windows)).total_seconds())
        if report["overlap_seconds"] < MIN_OVERLAP_SECONDS:
            failures.append(f"Actual source collection overlapped for less than {MIN_OVERLAP_SECONDS} seconds.")
    if len(metrics) == 2 and report["aggregate_successful_rps"] < MIN_AGGREGATE_RPS:
        failures.append(f"Aggregate successful throughput was below {MIN_AGGREGATE_RPS} requests/second.")
    if len(metrics) != 2 or len(addresses) != 2 or len(jobs) != 2 or len(runners) != 2:
        unknown.append("Complete measured source, machine, job, and stable egress proof is required from both workers.")
    report["improvement_over_baseline"] = report["aggregate_successful_rps"] / BASELINE_RPS
    report["reasons"] = list(dict.fromkeys([*failures, *unknown]))
    report["status"] = "fail" if failures else "inconclusive" if unknown else "pass"
    report["passed"] = report["allowed_scale_up"] = report["pilotpass"] = report["status"] == "pass"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("capture")
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--phase", choices=("before", "after"), required=True)
    command.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    command.add_argument("--job-id", default=os.environ.get("GITHUB_JOB"))
    command.add_argument("--shard-id", default=os.environ.get("SHARD_ID"))
    command.add_argument("--runner-name", default=os.environ.get("RUNNER_NAME"))
    command = commands.add_parser("assess")
    command.add_argument("--manifest", type=Path, required=True)
    command.add_argument("--summaries-dir", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        result = capture(phase=args.phase, run_id=args.run_id, job_id=args.job_id,
                         shard_id=args.shard_id, runner_name=args.runner_name)
    else:
        try:
            result = assess(read_json(args.manifest), args.summaries_dir)
        except (OSError, ValueError, TypeError, KeyError) as error:
            result = {"schema_version": 1, "assessed_at": utcnow(), "status": "inconclusive", "passed": False,
                "allowed_scale_up": False, "pilotpass": False, "machine_count": 0, "proven_requests": 0,
                "reasons": [f"Pilot evidence could not be assessed ({type(error).__name__}); scaling is not authorized."]}
    atomic_json(args.output, result)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
