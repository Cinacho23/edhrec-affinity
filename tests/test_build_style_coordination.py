"""Offline GitHub coordination checks; no EDHREC or repository mutations."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/coordinate_build_styles.py"
spec = importlib.util.spec_from_file_location("coordinate_build_styles", SCRIPT)
coordinator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coordinator
spec.loader.exec_module(coordinator)
NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def paused(*, expiry=NOW + timedelta(hours=1), throttles=1, **changes):
    return {"blocked": True, "blocked_until": expiry.isoformat(), "remaining_in_batch": 10,
            "measured_request_rate": {"requests_started": 100, "throttles": throttles}, **changes}


class GitHub:
    def __init__(self, summaries=(), *, active=False, timer=True):
        self.saved = [coordinator.Snapshot(100 - index, summary) for index, summary in enumerate(summaries)]
        self.is_active, self.timer = active, timer
        self.dispatches = []
        self.timer_checks = 0

    def active(self, excluding=None):
        return self.is_active

    def snapshots(self):
        return iter(self.saved)

    def source(self, run_id):
        return next((item for item in self.saved if item.run_id == run_id), None)

    def verify_timer(self):
        self.timer_checks += 1
        if not self.timer:
            raise ValueError("Environment must have a wait timer of at least 60 minutes")

    def dispatch(self, workflow, inputs=None):
        self.dispatches.append((workflow, inputs or {}))


def test_watchdog_and_preflight_skip_active_collectors_before_reading_checkpoints():
    github = GitHub(active=True)
    assert not coordinator.coordinate(github, "watchdog", now=NOW)["run"]
    assert not coordinator.coordinate(github, "preflight", now=NOW)["run"]
    assert github.dispatches == []


def test_watchdog_waits_without_retrying_source_before_known_cooldown_expires():
    github = GitHub([paused()])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False
    assert result["retry_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert github.dispatches == [] and github.timer_checks == 0


@pytest.mark.parametrize("scope", [{"commanders": ["commander-a"]}, {"sample_decks": 60},
                                   {"sample_decks": 0}, {"max_commanders": 1}, {"refresh": True}])
def test_manual_or_finite_scope_does_not_restart_full_collection_automatically(scope):
    github = GitHub([{"blocked": False, "remaining_in_batch": 0,
        "capacity": {"evaluated_at": NOW.isoformat()}, "scope": scope}])
    assert coordinator.coordinate(github, "watchdog", now=NOW)["run"] is False
    assert github.dispatches == []
    assert coordinator.coordinate(github, "preflight", now=NOW)["run"] is True
    rate_limited = GitHub([paused(scope=scope)])
    assert coordinator.coordinate(rate_limited, "prepare-cooldown", now=NOW, source_run_id=100)["wait"] is False


def test_expired_rate_limit_resumes_but_access_restrictions_do_not():
    github = GitHub([paused(expiry=NOW - timedelta(seconds=1), block_reason="rate_limit")])
    assert coordinator.coordinate(github, "watchdog", now=NOW)["dispatched"] is True
    assert github.dispatches == [(coordinator.COLLECTOR, {})]
    for summary in (paused(expiry=NOW, throttles=0), paused(expiry=NOW, block_reason="access_restriction")):
        blocked = GitHub([summary])
        assert coordinator.coordinate(blocked, "watchdog", now=NOW)["run"] is False
        assert blocked.dispatches == []


@pytest.mark.parametrize("origin_throttles,allowed", [(1, True), (0, False)])
def test_legacy_cooldown_only_summary_inherits_only_the_latest_real_attempt_reason(origin_throttles, allowed):
    current = paused(expiry=NOW, measured_request_rate=None)
    github = GitHub([current, paused(expiry=NOW, throttles=origin_throttles)])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is allowed
    assert bool(github.dispatches) is allowed


def test_waiter_requires_original_real_429_and_an_existing_durable_snapshot():
    for summary in (paused(throttles=0), paused(preflight_skipped=True)):
        github = GitHub([summary])
        assert not coordinator.coordinate(github, "prepare-cooldown", now=NOW, source_run_id=100)["wait"]
        assert github.timer_checks == 0
    assert not coordinator.coordinate(GitHub(), "prepare-cooldown", now=NOW, source_run_id=100)["wait"]
    github = GitHub([paused()])
    result = coordinator.coordinate(github, "prepare-cooldown", now=NOW, source_run_id=100)
    assert result["wait"] is True and github.timer_checks == 1
    assert github.dispatches == []


def test_missing_environment_timer_fails_closed_without_an_immediate_dispatch_loop():
    github = GitHub([paused()], timer=False)
    with pytest.raises(ValueError, match="60 minutes"):
        coordinator.coordinate(github, "prepare-cooldown", now=NOW, source_run_id=100)
    assert github.dispatches == []


@pytest.mark.parametrize("timer,allowed", [(0, False), (59, False), (True, False), (60, True), (120, True)])
def test_real_timer_validation_requires_an_effective_server_side_wait(timer, allowed):
    client = object.__new__(coordinator.GitHub)
    client.root = "repos/owner/repo"
    client.api = lambda endpoint: {"protection_rules": [{"type": "wait_timer", "wait_timer": timer}]}
    if allowed:
        client.verify_timer()
    else:
        with pytest.raises(ValueError, match="60 minutes"):
            client.verify_timer()


def test_cooldown_environment_does_not_silently_require_human_approval():
    client = object.__new__(coordinator.GitHub)
    client.root = "repos/owner/repo"
    client.api = lambda endpoint: {"protection_rules": [
        {"type": "wait_timer", "wait_timer": 60}, {"type": "required_reviewers", "reviewers": [{"id": 1}]}]}
    with pytest.raises(ValueError, match="manual reviewers"):
        client.verify_timer()


@pytest.mark.parametrize("changes", [{"event": "pull_request"}, {"event": "pull_request_target"},
                                    {"head_branch": "feature"}, {"path": ".github/workflows/unrelated.yml"}])
def test_only_default_branch_collector_runs_can_supply_coordination_state(changes):
    client = object.__new__(coordinator.GitHub)
    client.default_branch = "main"
    run = {"head_branch": "main", "event": "workflow_dispatch",
           "path": ".github/workflows/build-commander-styles.yml", **changes}
    assert client.trusted(run) is False


def test_late_waiter_dispatches_due_work_without_waiting_another_hour():
    github = GitHub([paused(expiry=NOW - timedelta(seconds=1))])
    result = coordinator.coordinate(github, "prepare-cooldown", now=NOW, source_run_id=100)
    assert result["wait"] is False and result["dispatched"] is True
    assert github.timer_checks == 0


def test_long_retry_after_requeues_only_the_free_timer_with_a_bounded_attempt():
    github = GitHub([paused(expiry=NOW + timedelta(hours=2))])
    result = coordinator.coordinate(github, "resume", now=NOW, source_run_id=100, attempt=1)
    assert result["run"] is False and result["requeued"] is True
    assert github.dispatches == [(coordinator.COOLDOWN, {"source_run_id": "100", "attempt": "2"})]
    limited = GitHub([paused()])
    coordinator.coordinate(limited, "resume", now=NOW, source_run_id=100,
                           attempt=coordinator.MAX_WAIT_ATTEMPTS - 1)
    assert limited.dispatches == []


def test_waiter_rechecks_newer_access_blocks_and_active_collectors():
    github = GitHub([paused(expiry=NOW, throttles=0), paused(expiry=NOW)])
    assert not coordinator.coordinate(github, "resume", now=NOW, source_run_id=99)["run"]
    assert github.dispatches == []
    active = GitHub([paused(expiry=NOW)], active=True)
    assert not coordinator.coordinate(active, "resume", now=NOW, source_run_id=100)["run"]
    assert active.dispatches == []


def test_requeued_waiter_follows_a_newer_rate_limit_instead_of_an_expired_origin():
    github = GitHub([paused(expiry=NOW + timedelta(hours=2)), paused(expiry=NOW - timedelta(hours=1))])
    result = coordinator.coordinate(github, "resume", now=NOW, source_run_id=99)
    assert result["requeued"] is True
    assert github.dispatches == [(coordinator.COOLDOWN, {"source_run_id": "100", "attempt": "1"})]


@pytest.mark.parametrize("summary,expected", [
    (paused(), "rate_limit_paused"), (paused(throttles=0), "failed"),
    ({"blocked": False, "failures": ["schema error"], "no_progress": True}, "failed"),
    ({"blocked": False, "failures": [], "no_progress": False}, "success"),
])
def test_expected_429_pause_is_distinct_from_a_fatal_collector(summary, expected):
    assert coordinator.classify_outcome(summary) == expected


def github_stub(tmp_path, summary, *, durable=True):
    executable = tmp_path / "gh"
    executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
        import json, os, pathlib, sys
        args = sys.argv[1:]
        if args[:2] == ["run", "download"]:
            assert args[args.index("--name") + 1].startswith("build-style-summary-")
            target = pathlib.Path(args[args.index("--dir") + 1])
            (target / "build_style_batch.json").write_text(os.environ["TEST_SUMMARY"])
            sys.exit(0)
        endpoint = args[1]
        if "--method" in args:
            with open(os.environ["TEST_DISPATCHES"], "a") as out:
                out.write(json.dumps({"endpoint":endpoint,"body":json.load(sys.stdin)})+"\\n")
            sys.exit(0)
        if endpoint == "repos/owner/repo":
            print(json.dumps({"default_branch":"main"}))
        elif "/workflows/" in endpoint and "/runs?" in endpoint:
            print(json.dumps({"workflow_runs":[{"id":100,"path":".github/workflows/build-commander-styles.yml",
                "head_branch":"main","event":"workflow_dispatch","status":"completed"}]}))
        elif "/artifacts?" in endpoint:
            artifacts=[{"name":"build-style-summary-100","expired":False}]
            if os.environ["TEST_DURABLE"] == "1":
                artifacts.append({"name":"build-styles-2026-10-23-100","expired":False})
            print(json.dumps({"artifacts":artifacts}))
        else:
            sys.exit("Unexpected GitHub endpoint")
    '''))
    executable.chmod(0o755)
    dispatches = tmp_path / "dispatches"
    return {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "200", "GITHUB_OUTPUT": str(tmp_path / "output"),
            "TEST_SUMMARY": json.dumps(summary), "TEST_DURABLE": "1" if durable else "0",
            "TEST_DISPATCHES": str(dispatches)}, dispatches


def test_real_cli_uses_small_summary_then_dispatches_through_mocked_github_api(tmp_path):
    environment, dispatches = github_stub(tmp_path, paused(expiry=datetime(2000, 1, 1, tzinfo=timezone.utc)))
    result = subprocess.run([sys.executable, str(SCRIPT), "watchdog"], env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(dispatches.read_text())["body"] == {"ref": "main", "inputs": {}}
    assert "dispatched=true" in (tmp_path / "output").read_text()


def test_preflight_skips_cooldown_and_writes_a_nonpublishing_summary(tmp_path):
    environment, dispatches = github_stub(tmp_path, paused(expiry=datetime(2099, 1, 1, tzinfo=timezone.utc)))
    target = tmp_path / "skip-summary.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "preflight", "--summary-file", str(target)],
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(target.read_text())
    assert saved["preflight_skipped"] is True and saved["publish_needed"] is False
    assert saved["measured_request_rate"] is None
    assert not dispatches.exists()


def test_snapshot_does_not_trust_a_summary_without_the_full_checkpoint(tmp_path):
    environment, _ = github_stub(tmp_path, paused(), durable=False)
    old = os.environ.copy()
    try:
        os.environ.update(environment)
        client = coordinator.GitHub("owner/repo")
        snapshots = list(client.snapshots())
        assert len(snapshots) == 1 and snapshots[0].durable is False
        assert coordinator.decision(snapshots[0], now=NOW)["run"] is False
        assert not coordinator.coordinate(client, "watchdog", now=NOW)["run"]
    finally:
        os.environ.clear()
        os.environ.update(old)
