"""Promotion requires exact workflow identities and immutable source evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coordinate_build_styles", ROOT / "scripts/coordinate_build_styles.py")
coordinator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coordinator
spec.loader.exec_module(coordinator)
spec = importlib.util.spec_from_file_location("rollout_browser_collection", ROOT / "scripts/rollout_browser_collection.py")
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)
SHA = "a" * 40
SOURCE = "42967307416"
NOW = datetime(2026, 11, 12, 5, tzinfo=timezone.utc)


def smoke_proof():
    return {"passed": True, "transport": "webkit", "configured_requests_per_second": 0.1,
            "github_run_id": "101", "github_commit": SHA,
            "checkpoint_controls_changed": False, "collector_dispatched": False,
            "checks": [{"kind": "gallery", "valid": True, "source_commander_verified": True,
                        "complete_gallery_index_verified": True, "index_rows": 5629,
                        "unique_deck_ids": 5629, "displayed_total": 5629},
                       {"kind": "preview", "valid": True, "source_preview_id_verified": True,
                        "cards_including_commanders": 100}]}


def summary():
    return {"blocked": False, "block_reason": None, "source_stop": None, "failures": [],
            "new_previews_attempted": 30, "collected": ["a-commander"], "request_ceiling_per_second": 0.1,
            "measured_request_rate": {"blocked": False, "block_kind": None, "throttles": 0,
                                      "successful_responses": 40}}


def review_proof():
    return {"passed": True, "released": True, "edhrec_transport": "webkit", "source_run_id": SOURCE,
            "checks": [{"valid": True, "status_code": 200, "challenge": False} for _ in range(4)]}


def pilot_proof():
    return {"status": "pass", "passed": True, "allowed_scale_up": True, "pilotpass": True,
            "machine_count": 2, "generation": "103", "reasons": [],
            "configured_requests_per_second": 0.1, "collect_seconds": 900}


def pilot_summary():
    value = summary()
    value["distributed"] = {"runners": 2, "pilot": True, "generation": "103", "systemic_block": False,
                            "missing_workers": [], "fallback_workers": [], "failed_workers": [],
                            "pilot_gate": pilot_proof(), "workers": [
        {"worker_id": str(worker), "pipeline_summary": {"new_previews_attempted": 80,
         "measured_request_rate": {"configured_maximum_requests_per_second": 0.1}}} for worker in range(2)]}
    return value


class Clock:
    def __init__(self):
        self.seconds = 0

    def now(self):
        return NOW + timedelta(seconds=self.seconds)

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        assert seconds == 30
        self.seconds += seconds


class GitHub:
    repository = "Cinacho23/edhrec-affinity"
    root = f"repos/{repository}"
    default_branch = "main"
    trusted = coordinator.GitHub.trusted

    def __init__(self, clock):
        self.clock = clock
        self.dispatches, self.downloads, self.api_calls = [], [], []
        self.records = {}
        self.files = {101: {"receipt.json": smoke_proof()},
                      102: {"build_style_batch.json": summary(), "access-review.json": review_proof()},
                      103: {"build_style_batch.json": pilot_summary(), "distributed-pilot.json": pilot_proof()}}
        self.sha = SHA
        self.active_value = False
        self.artifacts_expired = False
        self.checkpoint_present = True
        self.ambiguous = False
        self.wrong_head = False
        self.failure_at = None

    def active(self):
        return self.active_value

    def dispatch(self, workflow, inputs):
        self.dispatches.append((workflow, inputs))
        run_id = 100 + len(self.dispatches)
        record = {"id": run_id, "head_branch": "main", "head_sha": "b" * 40 if self.wrong_head else SHA,
                  "event": "workflow_dispatch", "path": f".github/workflows/{workflow}",
                  "head_repository": {"full_name": self.repository},
                  "created_at": self.clock.now().isoformat(), "status": "completed",
                  "conclusion": "failure" if self.failure_at == run_id else "success"}
        self.records[run_id] = record
        if self.ambiguous:
            self.records[999] = {**record, "id": 999}

    def api(self, endpoint):
        self.api_calls.append(endpoint)
        assert endpoint.startswith(self.root + "/")
        if "/commits/" in endpoint:
            return {"sha": self.sha}
        if "/workflows/" in endpoint:
            workflow = endpoint.split("/workflows/", 1)[1].split("/", 1)[0]
            return {"workflow_runs": [deepcopy(value) for value in self.records.values()
                                      if value["path"].endswith("/" + workflow)]}
        run_id = int(endpoint.split("/runs/", 1)[1].split("/", 1)[0])
        if "/artifacts?" in endpoint:
            name = f"browser-collection-proof-{run_id}" if run_id == 101 else f"build-style-summary-{run_id}"
            artifacts = [{"id": run_id + 1000, "name": name, "size_in_bytes": 1000,
                          "expired": self.artifacts_expired, "digest": "sha256:immutable"}]
            if run_id != 101 and self.checkpoint_present:
                artifacts.append({"name": f"build-styles-{run_id}", "expired": False, "size_in_bytes": 100_000_000})
            return {"artifacts": artifacts}
        return deepcopy(self.records[run_id])

    def command(self, args):
        assert args[:2] == ["run", "download"]
        assert "--slurp" not in args and "--jq" not in args
        self.downloads.append(args)
        run_id = int(args[2])
        destination = Path(args[args.index("--dir") + 1])
        for name, value in self.files[run_id].items():
            (destination / name).write_text(json.dumps(value))
        return ""


def execute(tmp_path, client=None):
    clock = client.clock if client else Clock()
    client = client or GitHub(clock)
    result = rollout.Rollout(client, tmp_path, now=clock.now, monotonic=clock.monotonic,
                             sleep=clock.sleep).execute(SOURCE, 16)
    return result, client


def test_complete_rollout_verifies_all_artifacts_before_sixteen_worker_dispatch(tmp_path):
    result, client = execute(tmp_path)
    assert result["status"] == "collection_dispatched"
    assert result["full_collection_run_id"] == 104
    assert client.dispatches == [(rollout.SMOKE, {}), (coordinator.COLLECTOR, {"access_review_run_id": SOURCE}),
        (coordinator.DISTRIBUTED_COLLECTOR, {"runners": "2", "pilot": "true", "collect_seconds": "900"}),
        (coordinator.DISTRIBUTED_COLLECTOR, {"runners": "16", "pilot": "false"})]
    assert len(client.downloads) == 3
    assert all(stage["proof_verified"] for stage in result["stages"][:3])
    assert result == json.loads((tmp_path / "rollout.json").read_text())
    assert result["provider_requests_made"] is False
    assert result["repository_variables_changed"] is False
    assert result["source_controls_changed_by_helper"] is False
    assert all(stage["url"].endswith(str(stage["run_id"])) for stage in result["stages"])
    assert result["stages"][2]["proof_files"]["distributed-pilot.json"]["sha256"]


@pytest.mark.parametrize("run_id,filename,alter,dispatches", [
    (101, "receipt.json", lambda data: data.update(passed=False), 1),
    (101, "receipt.json", lambda data: data.update(github_commit="b" * 40), 1),
    (101, "receipt.json", lambda data: data["checks"][0].update(displayed_total=6000), 1),
    (101, "receipt.json", lambda data: data["checks"][1].update(cards_including_commanders=99), 1),
    (102, "build_style_batch.json", lambda data: data.update(preflight_skipped=True), 2),
    (102, "build_style_batch.json", lambda data: data.update(new_previews_attempted=0), 2),
    (102, "access-review.json", lambda data: data.update(released=False), 2),
    (102, "access-review.json", lambda data: data.update(source_run_id="123"), 2),
    (102, "access-review.json", lambda data: data.update(edhrec_transport="http"), 2),
    (103, "distributed-pilot.json", lambda data: data.update(allowed_scale_up=False), 3),
    (103, "build_style_batch.json", lambda data: data.update(blocked=True), 3),
    (103, "build_style_batch.json", lambda data: data["distributed"]["workers"][0]["pipeline_summary"].update(new_previews_attempted=0), 3),
    (103, "build_style_batch.json", lambda data: data["distributed"]["workers"][0]["pipeline_summary"]["measured_request_rate"].update(configured_maximum_requests_per_second=1), 3),
])
def test_invalid_evidence_never_dispatches_a_later_stage(tmp_path, run_id, filename, alter, dispatches):
    client = GitHub(Clock())
    alter(client.files[run_id][filename])
    result, _ = execute(tmp_path, client)
    assert result["status"] == "stopped" and len(client.dispatches) == dispatches
    assert result["error_type"] == "ValueError"
    assert "full_collection_run_id" not in result


@pytest.mark.parametrize("run_id", [101, 102, 103])
def test_failed_workflow_never_promotes_even_with_a_saved_successful_proof(tmp_path, run_id):
    client = GitHub(Clock())
    client.failure_at = run_id
    result, _ = execute(tmp_path, client)
    assert result["status"] == "stopped"
    assert len(client.dispatches) == run_id - 100
    assert len(client.downloads) == run_id - 101


@pytest.mark.parametrize("mode", ["expired", "missing_checkpoint", "ambiguous", "wrong_sha", "active"])
def test_missing_immutable_proof_or_competing_runs_stop_rollout(tmp_path, mode):
    client = GitHub(Clock())
    if mode == "expired":
        client.artifacts_expired = True
    elif mode == "missing_checkpoint":
        client.checkpoint_present = False
    elif mode == "ambiguous":
        client.ambiguous = True
    elif mode == "wrong_sha":
        client.wrong_head = True
    else:
        client.active_value = True
    result, _ = execute(tmp_path, client)
    assert result["status"] == "stopped"
    assert len(client.dispatches) <= 2
    assert "full_collection_run_id" not in result


def test_main_change_between_stages_requires_a_new_reviewed_rollout(tmp_path):
    client = GitHub(Clock())
    original = client.command

    def changing(args):
        result = original(args)
        client.sha = "b" * 40
        return result

    client.command = changing
    result, _ = execute(tmp_path, client)
    assert result["status"] == "stopped" and len(client.dispatches) == 1
    assert "Default branch changed" in result["error"]


def test_in_progress_stage_waits_only_within_its_budget(tmp_path, monkeypatch):
    client = GitHub(Clock())
    original = client.dispatch

    def running(workflow, inputs):
        original(workflow, inputs)
        client.records[101]["status"] = "in_progress"

    client.dispatch = running
    monkeypatch.setitem(rollout.STAGE_TIMEOUTS, "smoke", 60)
    result, _ = execute(tmp_path, client)
    assert result["status"] == "stopped" and len(client.dispatches) == 1
    assert client.clock.seconds == 60
    assert "bounded wait" in result["error"]


@pytest.mark.parametrize("value", [0, True, float("inf"), float("nan"), 1])
def test_cautious_browser_rate_fails_closed(value):
    assert rollout.cautious_rate(value) is False
