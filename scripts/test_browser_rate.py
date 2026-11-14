#!/usr/bin/env python3
"""Bound one standard WebKit runner's rate test; never tune production.

An explicitly requested diagnostic alongside the unchanged production pool.
No retries, fallback endpoints, identity overrides, or attempts beyond 5 RPS.
The first denial, challenge, redirect, or invalid source ends the experiment.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import threading
import time

from coordinate_build_styles import GitHub, timestamp
from probe_rate_limiter import ProbeLimiter
from edhrec_affinity.build_styles.browser_transport import BrowserHTMLTransport, extract_gallery_data
from edhrec_affinity.build_styles.sources import BudgetExpired, SourceBlocked, extract_preview_data, parse_preview

SLUG = "the-tenth-doctor-rose-tyler"
STAGES = [(step / 10, 120 if step == 50 else 30) for step in range(10, 51)]
MAX_REQUESTS = 4500
CONCURRENCY = 4
EXPECTED_COMMANDERS = {"The Tenth Doctor", "Rose Tyler"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def check_controls(summary, now):
    if not isinstance(summary, dict) or summary.get("preflight_skipped"):
        raise ValueError("No valid canonical collector summary")
    controls = [summary]
    distributed = summary.get("distributed") or {}
    if not isinstance(distributed, dict) or distributed.get("systemic_block"):
        raise ValueError("Saved distributed source access is stopped")
    for worker in distributed.get("workers", []):
        if not isinstance(worker, dict):
            raise ValueError("Invalid worker source controls")
        controls.extend([worker.get("source_control") or {}, worker.get("pipeline_summary") or {}])
    for control in controls:
        if not isinstance(control, dict):
            raise ValueError("Invalid saved source controls")
        expiry = timestamp(control.get("blocked_until"))
        if (control.get("blocked") or control.get("block_reason") == "access_restriction"
                or control.get("blocked_until") is not None and expiry is None
                or expiry is not None and expiry > now
                or (control.get("measured_request_rate") or {}).get("blocked")):
            raise ValueError("A saved source stop or cooldown prevents rate testing")


def metrics(records, elapsed):
    successful = [row for row in records if row.get("valid")]
    starts = [row["monotonic"] for row in successful]
    gaps = [right - left for left, right in zip(starts, starts[1:])]
    latencies = [row["latency_seconds"] for row in successful]
    return {"attempts": len(records), "valid_responses": len(successful),
            "elapsed_seconds": elapsed,
            "observed_request_start_rps": (len(starts) - 1) / (starts[-1] - starts[0]) if len(starts) > 1 else None,
            "completed_responses_per_second": len(successful) / elapsed if elapsed else None,
            "minimum_start_gap_seconds": min(gaps) if gaps else None,
            "median_latency_seconds": statistics.median(latencies) if latencies else None,
            "p95_latency_seconds": sorted(latencies)[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
            "http_status_counts": {str(code): sum(row.get("status_code") == code for row in records)
                                   for code in sorted({row["status_code"] for row in records if row.get("status_code") is not None})}}


class RateTest:
    def __init__(self, output, *, transport_factory=BrowserHTMLTransport,
                 monotonic=time.monotonic, now=utcnow, stages=STAGES,
                 limiter_factory=ProbeLimiter, concurrency=CONCURRENCY):
        self.output, self.monotonic, self.now = Path(output), monotonic, now
        self.stages = list(stages)
        if (not self.stages or any(type(rate) not in {int, float} or not math.isfinite(rate) or not 0 < rate <= 5
                                  or type(seconds) is not int or not 0 < seconds <= 300 for rate, seconds in self.stages)
                or sum(seconds for _, seconds in self.stages) > 1800
                or type(concurrency) is not int or not 1 <= concurrency <= CONCURRENCY):
            raise ValueError("Rate test exceeds its permitted rate or duration")
        self._receipt_lock = threading.RLock()
        self._attempts = 0
        self.limiter_factory = limiter_factory
        self.output.mkdir(parents=True, exist_ok=True)
        self.receipt = {"schema_version": 1, "started_at": now(), "outcome": "preparing",
            "engine": "standard Playwright WebKit", "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_commit": os.environ.get("GITHUB_SHA"), "production_settings_changed": False,
            "scope": "One extra runner alongside the unchanged production pool; shared quota scope unknown",
            "maximum_requests": MAX_REQUESTS, "maximum_target_rps": 5, "retries": 0,
            "browsers_sharing_one_gate": concurrency,
            "stages": [], "requests": [], "limitations": [
                "Short tests measure observed behavior, not a permanent quota or guarantee.",
                "Other production workers remain at their existing rates.",
                "A target is not an achieved rate; inspect measured starts per second.",
                "No claim about rates above five requests per second or scaling this rate to sixteen runners."]}
        self.limiter = limiter_factory(max_rate=self.stages[0][0], monotonic=monotonic)
        self.transports = [transport_factory(self.limiter) for _ in range(concurrency)]
        self.last_start = None
        self.last_rate = self.stages[0][0]

    def save(self):
        with self._receipt_lock:
            path = self.output / "receipt.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.receipt, indent=2, allow_nan=False) + "\n")
            temporary.replace(path)

    def get(self, url, *, kind, transport):
        with self._receipt_lock:
            if self._attempts >= MAX_REQUESTS:
                self.limiter.block("Diagnostic request cap reached", kind="budget")
                raise RuntimeError("Diagnostic request cap reached")
            self._attempts += 1
        row = {"url": url, "kind": kind, "checked_at": self.now(), "valid": False}
        before = self.monotonic()
        try:
            response = transport.get_response(url)
            row.update(status_code=response.status_code, body_bytes=len(response.content),
                       sha256=hashlib.sha256(response.content).hexdigest(),
                       response_headers={key: response.headers.get(key) for key in
                                         ("retry-after", "x-cache", "cache-control", "server", "cf-mitigated")
                                         if response.headers.get(key) is not None})
            # Transport records actual document starts after pacing, not call start.
            starts = transport.stats["request_starts"]
            row["monotonic"] = starts[-1]["monotonic"]
            with self._receipt_lock:
                self.last_start = max(self.last_start or row["monotonic"], row["monotonic"])
            row["latency_seconds"] = self.monotonic() - row["monotonic"]
            if response.status_code in {401, 403, 429} or self.limiter.blocked:
                with self._receipt_lock:
                    if "stop" not in self.receipt:
                        self.receipt["stop"] = {**row, "reason": self.limiter.kind or "access_restriction"}
                        (self.output / "stop-response.txt").write_text(response.text[:2000])
                raise SourceBlocked("Source denied or challenged this request; no retry attempted")
            if response.status_code != 200:
                raise ValueError(f"Unexpected HTTP {response.status_code}; no retry attempted")
            if kind == "gallery":
                payload = extract_gallery_data(response.text, SLUG)
                row.update(index_rows=len(payload["table"]), complete_index=payload["index_complete"])
            else:
                payload = extract_preview_data(response.text)
                identifier = url.rsplit("/", 1)[1]
                if payload.get("urlhash") != identifier:
                    raise ValueError("Wrong preview identity")
                deck = parse_preview(payload)
                if set(deck["commanders"]) != EXPECTED_COMMANDERS:
                    raise ValueError("Wrong source commander pair")
                row.update(mainboard_entries=len(deck["cards"]), cards_including_commanders=(
                    len(deck["commanders"]) + sum(card["quantity"] for card in deck["cards"])))
            row["valid"] = True
            self.limiter.record_success()
            return payload
        except BudgetExpired:
            # A deadline in the pacing gate sent no request at all.
            raise
        except Exception as error:
            row.update(error_type=type(error).__name__, error=str(error)[:400])
            self.limiter.block("Diagnostic source response failed validation; no retry attempted", kind="diagnostic_stop")
            raise
        finally:
            if "status_code" in row or "error_type" in row:
                row.setdefault("monotonic", before)
                row.setdefault("latency_seconds", self.monotonic() - before)
                row["completed_at"] = self.now()
                with self._receipt_lock:
                    self.receipt["requests"].append(row)
                    self.save()

    def stage_requests(self, iterator, beginning, seconds):
        """Keep at most one navigation per browser; drain before changing rate."""
        error = None
        with ThreadPoolExecutor(max_workers=len(self.transports)) as pool:
            pending = {}
            def submit(transport):
                identifier = next(iterator)
                future = pool.submit(self.get, f"https://edhrec.com/deckpreview/{identifier}",
                                     kind="preview", transport=transport)
                pending[future] = transport
            for transport in self.transports:
                submit(transport)
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                available = []
                for future in done:
                    transport = pending.pop(future)
                    try:
                        future.result()
                        available.append(transport)
                    except BudgetExpired:
                        pass
                    except Exception as caught:
                        error = error or caught
                        self.limiter.block("Diagnostic stopped after source failure", kind="diagnostic_stop")
                if error is None and self.monotonic() < beginning + seconds and not self.limiter.blocked:
                    for transport in available:
                        try:
                            submit(transport)
                        except StopIteration:
                            error = RuntimeError("Unique preview index exhausted; no requests repeated")
                            self.limiter.block(str(error), kind="budget")
                            break
        if error is not None:
            raise error

    def execute(self):
        self.save()
        try:
            self.receipt["outcome"] = "running"
            gallery = self.get(f"https://edhrec.com/commanders/{SLUG}/decks", kind="gallery", transport=self.transports[0])
            ids = [row["urlhash"] for row in gallery["table"]]
            random.Random(20261114).shuffle(ids)
            iterator = iter(ids)
            for rate, seconds in self.stages:
                stage = {"target_rps": rate, "planned_seconds": seconds, "started_at": self.now(), "complete": False}
                self.receipt["stages"].append(stage)
                beginning = self.monotonic()
                self.limiter = self.limiter_factory(max_rate=rate, deadline=beginning + seconds,
                                                    monotonic=self.monotonic)
                # Respect both adjacent stage spacings at the boundary.
                if self.last_start is not None:
                    self.limiter.defer(max(0, self.last_start + max(1 / self.last_rate, 1 / rate) - self.monotonic()))
                for transport in self.transports:
                    transport.limiter = self.limiter
                self.last_rate = rate
                first = len(self.receipt["requests"])
                self.save()
                print(f"Testing {rate:g} requests/second for {seconds} seconds", flush=True)
                try:
                    self.stage_requests(iterator, beginning, seconds)
                except BudgetExpired:
                    pass
                finally:
                    stage.update(metrics(sorted(self.receipt["requests"][first:], key=lambda row: row["monotonic"]), self.monotonic() - beginning),
                                 completed_at=self.now())
                    observed = stage.get("observed_request_start_rps")
                    stage["attained_target"] = observed is not None and observed >= rate * .95
                stage["complete"] = True
                self.save()
                print(json.dumps(stage, allow_nan=False), flush=True)
            self.receipt["outcome"] = "completed_without_throttle"
        except SourceBlocked as error:
            self.receipt.update(outcome="stopped_on_source_denial", reason=str(error))
        except Exception as error:
            self.receipt.update(outcome="inconclusive", error_type=type(error).__name__, reason=str(error)[:500])
        finally:
            self.receipt["browsers"] = []
            for transport in self.transports:
                try:
                    self.receipt["browsers"].append(transport.stats)
                    transport.close()
                except Exception as error:
                    self.receipt.update(outcome="inconclusive", close_error=type(error).__name__)
            self.receipt.update(completed_at=self.now(), highest_completed_target_rps=max(
                (stage["target_rps"] for stage in self.receipt["stages"] if stage["complete"] and stage.get("valid_responses", 0) >= 5),
                default=None))
            self.receipt["highest_attained_target_rps"] = max(
                (stage["target_rps"] for stage in self.receipt["stages"] if stage["complete"] and stage.get("attained_target")), default=None)
            if self.limiter.blocked:
                self.receipt["limiter_stop"] = {"reason": self.limiter.reason, "kind": self.limiter.kind,
                                                "details": self.limiter.details}
            self.save()
        return self.receipt


def write_summary(receipt, path):
    lines = ["## Bounded EDHREC browser rate test", "", f"Outcome: **{receipt['outcome']}**", "",
             "One standard WebKit runner, four browsers sharing one pacing gate; production settings were unchanged.", "",
             "| Target requests/s | Valid previews | HTTP statuses | Observed starts/s | Median latency | Complete |",
             "|---:|---:|---|---:|---:|---|"]
    for stage in receipt.get("stages", []):
        observed, median = stage.get("observed_request_start_rps"), stage.get("median_latency_seconds")
        lines.append(f"| {stage['target_rps']:g} | {stage.get('valid_responses', 0)} | {stage.get('http_status_counts', {})} | "
                     f"{observed:.3f}" if observed is not None else
                     f"| {stage['target_rps']:g} | {stage.get('valid_responses', 0)} | {stage.get('http_status_counts', {})} | unknown")
        lines[-1] += f" | {median:.3f}s | {stage.get('complete', False)} |" if median is not None else f" | unknown | {stage.get('complete', False)} |"
    lines += ["", f"Highest completed target: {receipt.get('highest_completed_target_rps')} requests/s.",
              f"Highest target actually attained (within 5%): {receipt.get('highest_attained_target_rps')} requests/s.",
              "", receipt.get("reason", "No throttle or challenge was observed within the tested window."),
              "", "This is an observed short test, not a permanent quota or a guarantee for sixteen machines."]
    Path(path).write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        client = GitHub(os.environ["GITHUB_REPOSITORY"])
        latest = next(client.snapshots(), None)
        if latest is None or not latest.durable:
            raise ValueError("No durable canonical checkpoint controls available")
        check_controls(latest.summary, datetime.now(timezone.utc))
        control = {"run_id": latest.run_id, "blocked": latest.summary.get("blocked"),
                   "active_background_runs": [{"id": run["id"], "status": run["status"]} for run in client.runs()
                                              if run.get("status") in {"in_progress", "queued", "waiting"}]}
        (args.output / "controls.json").write_text(json.dumps(control, indent=2))
        result = RateTest(args.output).execute()
    except Exception as error:
        result = {"outcome": "preflight_stopped", "reason": str(error)[:500], "stages": [],
                  "production_settings_changed": False}
        (args.output / "receipt.json").write_text(json.dumps(result, indent=2))
    summary = args.output / "summary.md"
    write_summary(result, summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary.read_text())
    print(json.dumps({key: value for key, value in result.items() if key not in {"requests", "browsers"}}, indent=2))
    return 0 if result["outcome"] == "completed_without_throttle" else 1


if __name__ == "__main__":
    raise SystemExit(main())
