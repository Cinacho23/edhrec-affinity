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
        self.publications = set()
        self.publication_failure = False
        self.cooldowns = set()
        self.cooldown_failure = False

    def active(self, excluding=None):
        return self.is_active

    def snapshots(self):
        return iter(self.saved)

    def source(self, run_id, *, allow_uploaded=False):
        return next((item for item in self.saved if item.run_id == run_id), None)

    def verify_timer(self):
        self.timer_checks += 1
        if not self.timer:
            raise ValueError("Environment must have a wait timer of at least 60 minutes")

    def dispatch(self, workflow, inputs=None):
        if workflow == coordinator.FRONTEND and self.publication_failure:
            raise RuntimeError("Publication dispatch unavailable")
        if workflow == coordinator.COOLDOWN and self.cooldown_failure:
            raise RuntimeError("Cooldown dispatch unavailable")
        self.dispatches.append((workflow, inputs or {}))
        if workflow == coordinator.FRONTEND:
            self.publications.add(int(inputs["source_run_id"]))
        if workflow == coordinator.COOLDOWN:
            self.cooldowns.add(int(inputs["source_run_id"]))

    def publication_exists(self, source_run_id):
        return any(run_id >= source_run_id for run_id in self.publications)

    def cooldown_exists(self, source_run_id):
        return source_run_id in self.cooldowns


def test_watchdog_and_preflight_skip_active_collectors_before_reading_checkpoints():
    github = GitHub(active=True)
    assert not coordinator.coordinate(github, "watchdog", now=NOW)["run"]
    assert not coordinator.coordinate(github, "preflight", now=NOW)["run"]
    assert github.dispatches == []


def test_real_client_combines_both_trusted_collector_histories_and_active_jobs():
    client = object.__new__(coordinator.GitHub)
    client.default_branch, client.root, client._runs = "main", "repos/owner/repo", None
    paths = {
        coordinator.COLLECTOR: [{"id": 100, "status": "completed"}],
        coordinator.DISTRIBUTED_COLLECTOR: [{"id": 102, "status": "in_progress"}],
    }

    def api(endpoint):
        workflow = endpoint.split("/workflows/", 1)[1].split("/", 1)[0]
        return {"workflow_runs": [{**run, "head_branch": "main", "event": "workflow_dispatch",
                "path": f".github/workflows/{workflow}"} for run in paths[workflow]]}

    client.api = api
    assert [run["id"] for run in client.runs()] == [102, 100]
    assert all(client.trusted(run) for run in client.runs())
    assert client.active() and not client.active(excluding=102)


@pytest.mark.parametrize("count", [2, 16])
def test_watchdog_routes_collection_and_official_work_to_approved_distributed_runners(monkeypatch, count):
    monkeypatch.setenv("BUILD_STYLE_DISTRIBUTED_RUNNERS", str(count))
    github = GitHub([{"blocked": False, "official_validation": {"pending": 100, "checked": 5}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["dispatched"] and result["distributed_runners"] == count
    assert "validate_power" not in result
    assert github.dispatches == [(coordinator.DISTRIBUTED_COLLECTOR, {"runners": str(count), "pilot": "false"})]


def test_distributed_summary_continues_collection_and_power_together_without_alternation():
    github = GitHub([{"distributed": {"runners": 4, "systemic_block": False},
        "continuation_needed": True, "no_progress": False,
        "official_validation": {"pending": 100, "checked": 5}}])
    result = coordinator.coordinate(github, "continue", source_run_id=100, now=NOW, runner_count=4)
    assert result["run"] and "validate_power" not in result
    assert github.dispatches == [(coordinator.DISTRIBUTED_COLLECTOR, {"runners": "4", "pilot": "false"})]


@pytest.mark.parametrize("summary", [
    {"distributed": {"pilot": True}, "automatic_continuation_allowed": False},
    {"distributed": {"systemic_block": True}, "continuation_needed": True, "no_progress": False},
    {"distributed": {"missing_workers": ["1"]}, "continuation_needed": False},
    {"distributed": {}, "continuation_needed": False, "no_progress": True},
])
def test_distributed_pilot_blocks_and_missing_checkpoints_do_not_auto_restart(summary):
    github = GitHub([summary])
    result = coordinator.coordinate(github, "continue", source_run_id=100, now=NOW, runner_count=2)
    assert not result["run"] and github.dispatches == []


def test_normal_legacy_preflight_redirects_only_when_explicitly_routed(monkeypatch):
    monkeypatch.setenv("BUILD_STYLE_DISTRIBUTED_RUNNERS", "16")
    github = GitHub([{"blocked": False}])
    assert coordinator.coordinate(github, "preflight", now=NOW)["run"]
    assert github.dispatches == []
    result = coordinator.coordinate(github, "preflight", route_distributed=True, now=NOW)
    assert not result["run"] and result["redirected"]
    assert github.dispatches == [(coordinator.DISTRIBUTED_COLLECTOR, {"runners": "16", "pilot": "false"})]


@pytest.mark.parametrize("value", ["-1", "17", "2.5", "nan", True])
def test_distributed_runner_rollout_configuration_fails_closed(value):
    with pytest.raises(ValueError, match="runner count"):
        coordinator.distributed_runners(value)


@pytest.mark.parametrize("power_validation,expected_inputs", [(False, {"validate_power": "true"}), (True, {})])
def test_watchdog_alternates_saved_official_checks_with_source_collection(power_validation, expected_inputs):
    github = GitHub([{"blocked": False, "power_validation": power_validation,
        "official_validation": {"pending": 100, "checked": 5, "source_blocked": False},
        "remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["dispatched"]
    assert github.dispatches == [(coordinator.COLLECTOR, expected_inputs)]


@pytest.mark.parametrize("retry_at,expected", [
    ((NOW + timedelta(hours=24)).isoformat(), False),
    ((NOW - timedelta(seconds=1)).isoformat(), True),
    ("invalid", False),
])
def test_official_source_backoff_does_not_trigger_futile_checks(retry_at, expected):
    github = GitHub([{"blocked": False, "power_validation": False,
        "official_validation": {"pending": 100, "checked": 1, "source_blocked": True, "retry_at": retry_at},
        "remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result.get("validate_power", False) is expected
    assert result["run"] is expected
    assert github.dispatches == ([(coordinator.COLLECTOR, {"validate_power": "true"})] if expected else [])


def test_new_generated_lists_request_official_checks_even_when_source_cycle_is_current():
    github = GitHub([{"blocked": False, "completed": ["new-commander"],
        "remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] and result["validate_power"]
    assert github.dispatches == [(coordinator.FRONTEND, {"source_run_id": "100"}),
                                (coordinator.COLLECTOR, {"validate_power": "true"})]


def test_metadata_repair_backoff_can_resume_without_bypassing_source_controls():
    github = GitHub([{"blocked": False, "official_validation": {"pending": 1, "checked": 1,
        "metadata_source_blocked": True, "retry_at": (NOW - timedelta(seconds=1)).isoformat()},
        "remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] and result["validate_power"]
    assert github.dispatches == [(coordinator.COLLECTOR, {"validate_power": "true"})]


def test_due_session_retry_resumes_even_when_failed_batch_made_no_progress():
    github = GitHub([{"blocked": False, "official_validation": {"pending": 10, "checked": 0,
        "changed_reports": 0, "retry_at": (NOW - timedelta(seconds=1)).isoformat()},
        "remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] and result["validate_power"]
    assert github.dispatches == [(coordinator.COLLECTOR, {"validate_power": "true"})]


def test_watchdog_waits_without_retrying_source_before_known_cooldown_expires():
    github = GitHub([paused()])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False
    assert result["retry_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert github.dispatches == [(coordinator.COOLDOWN, {"source_run_id": "100", "attempt": "0"})]
    assert github.timer_checks == 1


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
        elif endpoint.endswith("/actions/runs/100"):
            print(json.dumps({"id":100,"path":".github/workflows/build-commander-styles.yml",
                "head_branch":"main","event":"workflow_dispatch",
                "status":os.environ.get("TEST_SOURCE_STATUS","completed")}))
        elif "/artifacts?" in endpoint:
            artifacts=[{"name":"build-style-summary-100","expired":False}]
            if os.environ["TEST_DURABLE"] == "1":
                artifacts.append({"name":"build-styles-2026-10-23-100","expired":False})
            print(json.dumps({"artifacts":artifacts}))
        elif "/environments/" in endpoint:
            print(json.dumps({"protection_rules":[{"type":"wait_timer","wait_timer":60}]}))
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


def test_only_explicit_latest_incomplete_run_can_recover_an_older_checkpoint():
    github = GitHub([{"blocked": False, "source_rate": {"learned_rate": 1.75}}, {"blocked": False}])
    github.saved[0].durable = False
    assert not coordinator.coordinate(github, "watchdog", now=NOW)["run"]
    assert not coordinator.coordinate(github, "preflight", now=NOW)["run"]
    assert not coordinator.coordinate(github, "preflight", now=NOW, recovery_run_id=99)["run"]
    result = coordinator.coordinate(github, "preflight", now=NOW, recovery_run_id=100)
    assert result["run"] is True
    assert (result["recovery_run_id"], result["restore_run_id"]) == (100, 99)
    assert github.dispatches == []


def test_manual_recovery_requires_a_durable_fallback_and_preserves_source_guards():
    for summary in (paused(), paused(expiry=NOW, throttles=0)):
        github = GitHub([summary, {"blocked": False}])
        github.saved[0].durable = False
        assert not coordinator.coordinate(github, "preflight", now=NOW, recovery_run_id=100)["run"]
    github = GitHub([{"blocked": False}])
    github.saved[0].durable = False
    assert not coordinator.coordinate(github, "preflight", now=NOW, recovery_run_id=100)["run"]


@pytest.mark.parametrize("summary", [
    {"blocked": False, "block_reason": "access_restriction"},
    {"blocked": False, "blocked_until": (NOW + timedelta(hours=1)).isoformat()},
    {"blocked": False, "blocked_until": "invalid"},
])
def test_manual_recovery_cannot_ignore_newer_access_or_cooldown_controls(summary):
    github = GitHub([summary, {"blocked": False}])
    github.saved[0].durable = False
    assert not coordinator.coordinate(github, "preflight", now=NOW, recovery_run_id=100)["run"]


@pytest.mark.parametrize("older", [paused(), paused(expiry=NOW, throttles=0),
                                  {"blocked": False, "block_reason": "access_restriction"}])
def test_recovery_validates_the_older_durable_checkpoint_source_guards(older):
    github = GitHub([{"blocked": False}, older])
    github.saved[0].durable = False
    assert not coordinator.coordinate(github, "preflight", now=NOW, recovery_run_id=100)["run"]


def test_source_access_restriction_is_never_downgraded_by_a_newer_rate_pause():
    state = {"schema_version": 1, "commanders": {}, "block_reason": "access_restriction",
             "blocked_until": (NOW - timedelta(seconds=1)).isoformat()}
    with pytest.raises(ValueError, match="access restriction"):
        coordinator.merge_recovery_controls(state, paused(expiry=NOW, block_reason="rate_limit"),
                                            source_run_id=100, checkpoint_run_id=99, now=NOW)
    assert state["block_reason"] == "access_restriction"


def test_recovery_cli_requires_a_manual_workflow_dispatch_event(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "preflight", "--recovery-run-id", "100"],
                            env={**os.environ, "GITHUB_EVENT_NAME": "schedule"}, text=True, capture_output=True)
    assert result.returncode == 1
    assert "explicit workflow_dispatch" in result.stdout


def test_saved_rate_limit_progress_dispatches_frontend_explicitly_once():
    github = GitHub([paused(publish_needed=True)])
    source = github.saved[0]
    assert coordinator.publish_source(github, source)["publication_dispatched"] is True
    assert github.dispatches == [(coordinator.FRONTEND, {"source_run_id": "100"})]
    assert coordinator.publish_source(github, source)["publication_current"] is True
    assert len(github.dispatches) == 1


def test_newer_queued_publication_prevents_watchdog_from_republishing_an_older_checkpoint():
    client = object.__new__(coordinator.GitHub)
    client.root, client.default_branch = "repos/owner/repo", "main"
    runs = [{"display_title": "Publish collector #101", "status": "queued", "head_branch": "main",
             "event": "workflow_dispatch", "path": ".github/workflows/deploy-frontend.yml"}]
    client.api = lambda endpoint: {"workflow_runs": runs}
    assert client.publication_exists(100) is True
    assert client.publication_exists(102) is False
    github = GitHub([{"publish_needed": True}], active=True)
    github.publications.add(101)
    assert coordinator.coordinate(github, "watchdog", now=NOW)["publication_current"] is True
    assert github.dispatches == []


def test_saved_actual_429_dispatches_the_explicit_waiter_once():
    github = GitHub([paused()])
    source = github.saved[0]
    assert coordinator.queue_cooldown(github, source, now=NOW)["cooldown_dispatched"] is True
    assert github.dispatches == [(coordinator.COOLDOWN, {"source_run_id": "100", "attempt": "0"})]
    assert coordinator.queue_cooldown(github, source, now=NOW)["cooldown_current"] is True
    assert len(github.dispatches) == 1 and github.timer_checks == 1


def test_a_failed_later_cooldown_attempt_can_be_repaired_despite_an_earlier_success():
    client = object.__new__(coordinator.GitHub)
    client.root, client.default_branch = "repos/owner/repo", "main"
    trusted = {"head_branch": "main", "event": "workflow_dispatch",
               "path": ".github/workflows/resume-build-styles.yml", "status": "completed"}
    runs = [{**trusted, "id": 201, "display_title": "Resume collector #100 after cooldown (attempt 0)",
             "conclusion": "success"},
            {**trusted, "id": 202, "display_title": "Resume collector #100 after cooldown (attempt 1)",
             "conclusion": "failure"}]
    client.api = lambda endpoint: {"workflow_runs": runs}
    assert client.cooldown_exists(100) is False
    runs[1].update(status="waiting", conclusion=None)
    assert client.cooldown_exists(100) is True


@pytest.mark.parametrize("summary", [
    {"blocked": False}, paused(throttles=0), paused(block_reason="access_restriction"),
    paused(scope={"sample_decks": 60}), paused(automatic_continuation_allowed=False),
    paused(expiry=NOW), paused(blocked_until="invalid"),
])
def test_cooldown_dispatch_does_not_turn_other_stops_into_automatic_full_collection(summary):
    github = GitHub([summary])
    assert coordinator.queue_cooldown(github, github.saved[0], now=NOW)["cooldown_dispatched"] is False
    assert github.dispatches == [] and github.timer_checks == 0


def test_cooldown_dispatch_requires_an_uploaded_checkpoint_and_a_verified_free_timer():
    github = GitHub([paused()], timer=False)
    for source in (None, coordinator.Snapshot(100, paused(), durable=False)):
        with pytest.raises(ValueError, match="uploaded collector checkpoint"):
            coordinator.queue_cooldown(github, source, now=NOW)
    with pytest.raises(ValueError, match="60 minutes"):
        coordinator.queue_cooldown(github, github.saved[0], now=NOW)
    assert github.dispatches == []


def test_watchdog_repairs_a_missing_cooldown_even_when_publication_fails():
    github = GitHub([paused(publish_needed=True)])
    github.publication_failure = True
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False and result["cooldown_dispatched"] is True
    assert result["publication_error"] == "Publication dispatch unavailable"
    assert github.dispatches == [(coordinator.COOLDOWN, {"source_run_id": "100", "attempt": "0"})]


def test_failed_cooldown_dispatch_does_not_prevent_saved_partial_progress_publication():
    github = GitHub([paused(publish_needed=True)])
    github.cooldown_failure = True
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False and result["publication_dispatched"] is True
    assert result["cooldown_error"] == "Cooldown dispatch unavailable"
    assert github.dispatches == [(coordinator.FRONTEND, {"source_run_id": "100"})]


def test_publication_requires_a_checkpoint_and_does_not_deploy_unchanged_data():
    github = GitHub([{"publish_needed": False}])
    assert coordinator.publish_source(github, github.saved[0])["publication_dispatched"] is False
    assert github.dispatches == []
    for source in (None, coordinator.Snapshot(100, {"publish_needed": True}, durable=False)):
        with pytest.raises(ValueError, match="uploaded collector checkpoint"):
            coordinator.publish_source(github, source)
    assert github.dispatches == []


def test_watchdog_repairs_publication_while_another_collector_is_active():
    github = GitHub([{"publish_needed": True}], active=True)
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False and result["publication_dispatched"] is True
    assert github.dispatches == [(coordinator.FRONTEND, {"source_run_id": "100"})]


def test_watchdog_publishes_the_latest_cumulative_checkpoint_for_older_missed_changes(tmp_path):
    github = GitHub([{"publish_needed": False}, {"publish_needed": True}], active=True)
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False and result["publication_source_run_id"] == 100
    assert github.dispatches == [(coordinator.FRONTEND, {"source_run_id": "100"})]
    assert coordinator.coordinate(github, "watchdog", now=NOW)["publication_dispatched"] is False
    assert len(github.dispatches) == 1
    environment, _ = github_stub(tmp_path, github.saved[0].summary)
    source_run_id = github.dispatches[0][1]["source_run_id"]
    receiver = subprocess.run([sys.executable, str(SCRIPT), "publication", "--source-run-id", source_run_id],
                              env=environment, text=True, capture_output=True)
    assert receiver.returncode == 0, receiver.stdout + receiver.stderr
    assert "publish=true" in (tmp_path / "output").read_text()


def test_failed_publication_does_not_prevent_collection_continuation():
    github = GitHub([{"publish_needed": True, "remaining_in_batch": 10}])
    github.publication_failure = True
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is True and result["dispatched"] is True
    assert result["publication_error"] == "Publication dispatch unavailable"
    assert github.dispatches == [(coordinator.COLLECTOR, {})]


def test_watchdog_can_publish_when_collection_is_complete_and_no_next_batch_is_due():
    github = GitHub([{"publish_needed": True, "remaining_in_batch": 0,
                      "capacity": {"evaluated_at": NOW.isoformat()}}])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is False and result["publication_dispatched"] is True
    assert github.dispatches == [(coordinator.FRONTEND, {"source_run_id": "100"})]


@pytest.mark.parametrize("progress", [{"regenerated": 1}, {"durable_metadata_progress": True},
                                     {"scored_reports": 1}, {"power_refreshed_reports": 1}])
def test_saved_deck_rebuild_progress_continues_after_source_collection_is_complete(progress):
    summary = {"remaining_in_batch": 0, "publish_needed": False,
               "capacity": {"evaluated_at": NOW.isoformat()},
               "deck_rebuild": {"pending": 12, **progress}}
    github = GitHub([summary])
    result = coordinator.coordinate(github, "watchdog", now=NOW)
    assert result["run"] is True
    assert github.dispatches == [(coordinator.COLLECTOR, {})]
    assert coordinator.coordinate(GitHub([summary]), "preflight", now=NOW)["run"] is True


@pytest.mark.parametrize("rebuild", [
    {"pending": 12, "regenerated": 0},
    {"pending": 0, "regenerated": 1},
    {"pending": True, "regenerated": 1},
    {"pending": 12, "regenerated": True},
    {"pending": 12, "scored_reports": True},
    {"pending": 12, "power_refreshed_reports": -1},
    {"pending": 12, "metadata_prefetch": {"fetched": 100}},
    {"pending": 12, "durable_metadata_progress": True, "metadata_source_blocked": True},
    {"pending": 12, "durable_metadata_progress": True,
     "metadata_prefetch": {"status": "source_access_stopped"}},
])
def test_unfinished_rebuild_alone_does_not_bypass_source_idle_wait(rebuild):
    github = GitHub([{"remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()},
                      "deck_rebuild": rebuild}])
    assert coordinator.coordinate(github, "watchdog", now=NOW)["run"] is False
    assert github.dispatches == []


@pytest.mark.parametrize("guard", [{"resource_limit": True}, paused(),
                                  {"scope": {"commanders": ["the-indomitable"]}}])
def test_deck_rebuild_progress_preserves_source_resource_and_manual_guards(guard):
    github = GitHub([{"remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()},
                      "deck_rebuild": {"pending": 12, "durable_metadata_progress": True}, **guard}])
    assert coordinator.coordinate(github, "watchdog", now=NOW)["run"] is False
    assert not any(workflow == coordinator.COLLECTOR for workflow, _ in github.dispatches)


def test_power_only_readiness_uses_durable_inputs_even_when_source_collection_is_idle():
    summary = {"remaining_in_batch": 0, "capacity": {"evaluated_at": NOW.isoformat()}}
    github = GitHub([summary])
    assert coordinator.coordinate(github, "preflight", now=NOW)["run"] is False
    ready = coordinator.coordinate(github, "preflight", now=NOW, power_only=True)
    assert ready["run"] is True and ready["restore_run_id"] == 100
    assert github.dispatches == []
    assert coordinator.coordinate(GitHub(active=True), "preflight", now=NOW, power_only=True)["run"] is False
    assert coordinator.coordinate(GitHub(), "preflight", now=NOW, power_only=True)["run"] is False


@pytest.mark.parametrize("changes", [paused(), {"resource_limit": True},
    {"deck_rebuild": {"metadata_source_blocked": True}},
    {"deck_rebuild": {"metadata_prefetch": {"blocked": True}}},
    {"deck_rebuild": {"metadata_prefetch": {"status": "source_access_stopped"}}}])
def test_power_only_readiness_retains_source_and_resource_guards(changes):
    assert coordinator.coordinate(GitHub([changes]), "preflight", now=NOW, power_only=True)["run"] is False


def test_power_only_readiness_does_not_use_an_incomplete_checkpoint():
    github = GitHub([{}])
    github.saved[0].durable = False
    assert coordinator.coordinate(github, "preflight", now=NOW, power_only=True)["run"] is False


@pytest.mark.parametrize("options", [{"mode": "watchdog"}, {"mode": "preflight", "recovery_run_id": 100}])
def test_power_only_readiness_cannot_change_watchdog_or_recovery_semantics(options):
    with pytest.raises(ValueError, match="only for preflight"):
        coordinator.coordinate(GitHub([{}]), now=NOW, power_only=True, **options)


def test_power_only_summary_preserves_source_facts_without_inheriting_publication_progress():
    original = {"scope": {"commanders": ["commander-a"]}, "automatic_continuation_allowed": False,
                "source_rate": {"learned_rate": 1.25}, "measured_request_rate": {"requests_started": 123},
                "capacity": {"evaluated_at": NOW.isoformat()}, "remaining_in_batch": 500,
                "publish_needed": True, "continuation_needed": True, "no_progress": False,
                "deck_rebuild": {"regenerated": 100}}
    saved = json.loads(json.dumps(original))
    prepared = coordinator.prepare_power_only_summary(coordinator.Snapshot(100, original), now=NOW)
    assert original == saved
    for key in ("scope", "automatic_continuation_allowed", "source_rate", "measured_request_rate", "capacity", "remaining_in_batch"):
        assert prepared[key] == original[key]
    assert prepared["publish_needed"] is False and prepared["continuation_needed"] is False
    assert prepared["no_progress"] is True and "deck_rebuild" not in prepared
    assert prepared["power_only"] is True and prepared["power_only_source_run_id"] == 100


@pytest.mark.parametrize("summary,durable", [({}, False), (paused(), True),
    ({"resource_limit": True}, True), ({"preflight_skipped": True}, True),
    ({"deck_rebuild": {"metadata_source_blocked": True}}, True),
    ({"deck_rebuild": {"metadata_prefetch": {"blocked": True}}}, True),
    ({"deck_rebuild": {"metadata_prefetch": {"status": "source_access_stopped"}}}, True)])
def test_power_only_summary_rejects_unsafe_source_context(summary, durable):
    with pytest.raises(ValueError):
        coordinator.prepare_power_only_summary(coordinator.Snapshot(100, summary, durable), now=NOW)


@pytest.mark.parametrize("durable,status,allowed", [(True, "completed", True), (False, "completed", False),
                                                   (True, "in_progress", False)])
def test_power_only_cli_prepares_the_exact_restored_checkpoint_offline(tmp_path, durable, status, allowed):
    summary = {"publish_needed": True, "continuation_needed": True, "no_progress": False,
               "source_rate": {"learned_rate": 1.25}, "deck_rebuild": {"regenerated": 100}}
    environment, dispatches = github_stub(tmp_path, summary, durable=durable)
    environment["TEST_SOURCE_STATUS"] = status
    target = tmp_path / "prepared.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "prepare-power-only", "--source-run-id", "100",
                             "--summary-file", str(target)], env=environment, text=True, capture_output=True)
    assert (result.returncode == 0) is allowed, result.stdout + result.stderr
    assert target.exists() is allowed
    assert not dispatches.exists()
    if allowed:
        prepared = json.loads(target.read_text())
        assert prepared["power_only_source_run_id"] == 100
        assert prepared["source_rate"] == summary["source_rate"]
        assert prepared["publish_needed"] is False and "deck_rebuild" not in prepared


@pytest.mark.parametrize("changed", [True, False])
def test_frontend_receiver_accepts_an_explicit_uploaded_source_before_collector_completion(tmp_path, changed):
    environment, dispatches = github_stub(tmp_path, {"publish_needed": changed})
    environment["TEST_SOURCE_STATUS"] = "in_progress"
    result = subprocess.run([sys.executable, str(SCRIPT), "publication", "--source-run-id", "100"],
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "publish=true" in (tmp_path / "output").read_text()
    assert "source_run_id=100" in (tmp_path / "output").read_text()
    assert not dispatches.exists()


def test_real_publish_cli_uses_authenticated_workflow_dispatch_instead_of_workflow_run(tmp_path):
    environment, dispatches = github_stub(tmp_path, {"publish_needed": True})
    environment["TEST_SOURCE_STATUS"] = "in_progress"
    result = subprocess.run([sys.executable, str(SCRIPT), "publish", "--source-run-id", "100"],
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    request = json.loads(dispatches.read_text())
    assert request["endpoint"].endswith("/workflows/deploy-frontend.yml/dispatches")
    assert request["body"] == {"ref": "main", "inputs": {"source_run_id": "100"}}


def test_explicit_cooldown_cli_accepts_uploaded_artifacts_before_the_collector_finishes(tmp_path):
    environment, dispatches = github_stub(tmp_path, paused(expiry=datetime(2099, 1, 1, tzinfo=timezone.utc)))
    environment["TEST_SOURCE_STATUS"] = "in_progress"
    result = subprocess.run([sys.executable, str(SCRIPT), "queue-cooldown", "--source-run-id", "100"],
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    request = json.loads(dispatches.read_text())
    assert request["endpoint"].endswith("/workflows/resume-build-styles.yml/dispatches")
    assert request["body"] == {"ref": "main", "inputs": {"source_run_id": "100", "attempt": "0"}}
    dispatches.unlink()
    result = subprocess.run([sys.executable, str(SCRIPT), "prepare-cooldown", "--source-run-id", "100"],
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "wait=true" in (tmp_path / "output").read_text()
    assert not dispatches.exists()


def test_recovery_merges_only_conservative_source_controls_not_unuploaded_progress():
    state = {"schema_version": 1, "commanders": {"old": {"current_previews": 100}},
             "progress": {"capacity": {"remaining_previews": 900}},
             "source_rate": {"learned_rate": 1.75, "throttles": 2,
                             "observed_requests_per_second": 1.5, "observed_rate_at": "2026-10-23T10:00:00Z"},
             "blocked_until": "2026-10-23T11:00:00Z"}
    summary = {"blocked": False, "completed": ["unuploaded"], "new_previews_attempted": 5000,
               "capacity": {"remaining_previews": 1}, "blocked_until": "2026-10-23T11:30:00Z",
               "source_rate": {"learned_rate": 2, "throttles": 3,
                               "observed_requests_per_second": 1.6, "observed_rate_at": "2026-10-23T11:00:00Z"}}
    coordinator.merge_recovery_controls(state, summary, source_run_id=100, checkpoint_run_id=99, now=NOW)
    assert state["source_rate"]["learned_rate"] == 1.75
    assert state["source_rate"]["throttles"] == 3
    assert state["source_rate"]["observed_requests_per_second"] == 1.6
    assert state["commanders"] == {"old": {"current_previews": 100}}
    assert state["progress"] == {"capacity": {"remaining_previews": 900}}
    assert state["blocked_until"] == "2026-10-23T11:30:00+00:00"
    assert state["recovery"]["source_run_id"] == 100
    summary["source_rate"]["learned_rate"] = 1
    coordinator.merge_recovery_controls(state, summary, source_run_id=100, checkpoint_run_id=99, now=NOW)
    assert state["source_rate"]["learned_rate"] == 1


@pytest.mark.parametrize("rate", [True, 0, -1, float("nan"), float("inf"), "1.75"])
def test_recovery_rejects_invalid_source_rate_controls(rate):
    latest = coordinator.Snapshot(100, {"blocked": False, "source_rate": {"learned_rate": rate}}, durable=False)
    with pytest.raises(ValueError, match="recovery learned_rate"):
        coordinator.recovery_plan(latest, [coordinator.Snapshot(99, {})], requested_run_id=100, now=NOW)
