"""Scaling requires actual two-runner source throughput and independent egress proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_distributed_pilot", ROOT / "scripts/check_distributed_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
START = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def at(seconds):
    return (START + timedelta(seconds=seconds)).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def egress(worker, phase, address):
    return pilot.capture(phase=phase, run_id="42", job_id=f"collector-{worker}", shard_id=worker,
        runner_name=f"hosted-{worker}", fetch=lambda _url: address, now=lambda: at(-10 if phase == "before" else 920))


def evidence(tmp_path):
    manifest = {"schema_version": 1, "generation": "42", "workers": [
        {"id": "0", "archive": "shard-0.tar.gz", "commanders": ["a-commander"]},
        {"id": "1", "archive": "shard-1.tar.gz", "commanders": ["z-commander"]}]}
    for worker, address in zip(manifest["workers"], ("8.8.8.8", "1.1.1.1"), strict=True):
        ident = worker["id"]
        folder = tmp_path / f"distributed-result-42-{ident}"
        folder.mkdir(parents=True)
        archive = folder / f"shard-{ident}-result.tar.gz"
        archive.write_bytes(f"deterministic checkpoint {ident}".encode())
        summary = {"schema_version": 1, "generation": "42", "worker_id": ident,
            "commanders": worker["commanders"], "exit_code": 0,
            "checkpoint": {"archive": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "valid": True, "used_fallback": False, "files": 4},
            "source_control": {"blocked_until": None, "block_reason": None},
            "pipeline_summary": {"blocked": False, "failures": [], "measured_request_rate": {
                "requests_started": 900, "successful_responses": 900, "failures": 0,
                "throttles": 0, "blocked": False, "block_kind": None, "elapsed_seconds": 900,
                "started_at": at(0), "completed_at": at(900), "observed_requests_per_second": 1.0}}}
        write(folder / f"shard-{ident}-summary.json", summary)
        for phase in ("before", "after"):
            write(folder / f"egress-{phase}.json", egress(ident, phase, address))
    return manifest


def summary_path(root, worker="0"):
    return root / f"distributed-result-42-{worker}" / f"shard-{worker}-summary.json"


def update_summary(root, mutate, worker="0"):
    path = summary_path(root, worker)
    value = json.loads(path.read_text())
    mutate(value)
    write(path, value)


def assess(manifest, root):
    return pilot.assess(manifest, root, now=lambda: at(1000))


def test_independent_https_capture_records_no_credentials_or_request_details():
    calls = []

    def fetch(url):
        calls.append(url)
        return "8.8.8.8\n"

    result = pilot.capture(phase="before", run_id="42", job_id="job0", shard_id="0",
        runner_name="host0", fetch=fetch, now=lambda: at(0))
    assert result["status"] == "verified"
    assert result["public_ipv4"] == "8.8.8.8"
    assert set(calls) == set(pilot.SERVICES.values())
    assert len(calls) == 2 and all(url.startswith("https://") for url in calls)
    assert result["captured_at"] == at(0)


@pytest.mark.parametrize("responses", [("8.8.8.8", "1.1.1.1"), ("127.0.0.1", "127.0.0.1"),
    ("2606:4700:4700::1111", "2606:4700:4700::1111"), ("<html>Error</html>", "8.8.8.8")])
def test_unverified_or_mismatched_egress_is_inconclusive(responses):
    values = iter(responses)
    result = pilot.capture(phase="after", run_id="42", job_id="job0", shard_id="0",
        runner_name="host0", fetch=lambda _url: next(values))
    assert result["status"] == "inconclusive" and result["public_ipv4"] is None


def test_metadata_failure_is_inconclusive_and_does_not_print_secret_exception():
    def failing(_url):
        raise TimeoutError("https://user:secret-token@example.com")

    result = pilot.capture(phase="before", run_id="42", job_id="job0", shard_id="0",
        runner_name="host0", fetch=failing)
    assert result["status"] == "inconclusive"
    assert set(result["service_errors"].values()) == {"TimeoutError"}
    assert "secret-token" not in json.dumps(result)


def test_complete_two_machine_pilot_passes_with_actual_overlap_and_rates(tmp_path):
    manifest = evidence(tmp_path)
    result = assess(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["passed"] is result["allowed_scale_up"] is result["pilotpass"] is True
    assert result["machine_count"] == 2 and result["proven_requests"] == 1800
    assert result["aggregate_successful_rps"] == 2.0 and result["overlap_seconds"] == 900
    assert result["reasons"] == []
    assert "limit scope remains unknown" in result["scope"]


def test_pilot_requires_exactly_two_jobs_and_disjoint_assignments(tmp_path):
    manifest = evidence(tmp_path)
    manifest["workers"][1]["commanders"] = ["a-commander"]
    result = assess(manifest, tmp_path)
    assert result["status"] == "fail" and not result["allowed_scale_up"]
    assert any("assignments overlap" in reason for reason in result["reasons"])
    manifest["workers"] = manifest["workers"][:1]
    assert assess(manifest, tmp_path)["status"] == "fail"


@pytest.mark.parametrize("field,value", [("generation", "old-generation"), ("worker_id", "different-worker"),
    ("exit_code", 1), ("commanders", ["unexpected-commander"])])
def test_mismatched_worker_generation_identity_or_assignment_fails(tmp_path, field, value):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, lambda summary: summary.update({field: value}))
    assert assess(manifest, tmp_path)["status"] == "fail"


@pytest.mark.parametrize("mutate", [lambda summary: summary["checkpoint"].update(valid=False),
    lambda summary: summary["checkpoint"].update(used_fallback=True),
    lambda summary: summary["checkpoint"].update(sha256="a" * 64),
    lambda summary: summary["checkpoint"].update(archive="../outside.tar.gz")])
def test_invalid_fallback_or_corrupt_checkpoint_never_passes(tmp_path, mutate):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, mutate)
    result = assess(manifest, tmp_path)
    assert result["status"] == "fail" and not result["allowed_scale_up"]


def test_missing_checkpoint_never_passes(tmp_path):
    manifest = evidence(tmp_path)
    (summary_path(tmp_path).parent / "shard-0-result.tar.gz").unlink()
    assert assess(manifest, tmp_path)["status"] == "fail"


@pytest.mark.parametrize("mode", ["missing_summary", "missing_capture", "unverified_capture", "changed_address", "missing_source_window"])
def test_missing_or_incomplete_measurements_are_inconclusive(tmp_path, mode):
    manifest = evidence(tmp_path)
    folder = summary_path(tmp_path).parent
    if mode == "missing_summary":
        summary_path(tmp_path).unlink()
    elif mode == "missing_capture":
        (folder / "egress-after.json").unlink()
    elif mode == "unverified_capture":
        value = egress("0", "after", "8.8.8.8")
        value["status"] = "inconclusive"
        write(folder / "egress-after.json", value)
    elif mode == "changed_address":
        write(folder / "egress-after.json", egress("0", "after", "9.9.9.9"))
    else:
        update_summary(tmp_path, lambda summary: summary["pipeline_summary"]["measured_request_rate"].pop("started_at"))
    result = assess(manifest, tmp_path)
    assert result["status"] == "inconclusive" and not result["allowed_scale_up"]


def test_same_public_address_or_job_identity_cannot_prove_two_machines(tmp_path):
    manifest = evidence(tmp_path)
    for phase in ("before", "after"):
        value = egress("1", phase, "8.8.8.8")
        value["job_id"] = "collector-0"
        value["runner_name"] = "hosted-0"
        write(summary_path(tmp_path, "1").parent / f"egress-{phase}.json", value)
    result = assess(manifest, tmp_path)
    assert result["status"] == "fail" and result["machine_count"] == 1
    assert any("distinct worker jobs" in reason for reason in result["reasons"])


def test_hosted_runner_display_names_can_repeat_across_distinct_jobs(tmp_path):
    manifest = evidence(tmp_path)
    for worker in ("0", "1"):
        folder = summary_path(tmp_path, worker).parent
        for phase in ("before", "after"):
            path = folder / f"egress-{phase}.json"
            value = json.loads(path.read_text())
            value["runner_name"] = "GitHub Actions 1"
            write(path, value)
    assert assess(manifest, tmp_path)["status"] == "pass"


def test_actual_collection_overlap_is_required(tmp_path):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, lambda summary: summary["pipeline_summary"]["measured_request_rate"].update(
        started_at=at(400), completed_at=at(1300)), worker="1")
    later = egress("1", "after", "1.1.1.1")
    later["captured_at"] = at(1310)
    write(summary_path(tmp_path, "1").parent / "egress-after.json", later)
    result = assess(manifest, tmp_path)
    assert result["status"] == "fail" and result["overlap_seconds"] == 500


@pytest.mark.parametrize("stats", [{"successful_responses": 599}, {"successful_responses": 700},
    {"requests_started": 920, "failures": 20},
    {"throttles": 1}, {"blocked": True}, {"status_counts": {"403": 1}},
    {"elapsed_seconds": 1200}, {"successful_responses": 901}])
def test_throughput_failure_and_source_safety_thresholds_fail_closed(tmp_path, stats):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, lambda summary: summary["pipeline_summary"]["measured_request_rate"].update(stats))
    result = assess(manifest, tmp_path)
    assert result["status"] == "fail" and not result["allowed_scale_up"]


def test_aggregate_rate_must_exceed_1_point_8_even_if_workers_exceed_point_8(tmp_path):
    manifest = evidence(tmp_path)
    for worker in ("0", "1"):
        update_summary(tmp_path, lambda summary: summary["pipeline_summary"]["measured_request_rate"].update(
            successful_responses=765), worker=worker)
    result = assess(manifest, tmp_path)
    assert result["aggregate_successful_rps"] == 1.7 and result["status"] == "fail"


def test_long_analysis_time_cannot_inflate_source_throughput(tmp_path):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, lambda summary: summary["pipeline_summary"]["measured_request_rate"].update(
        elapsed_seconds=1800, observed_requests_per_second=100, peak_in_flight=16))
    result = assess(manifest, tmp_path)
    assert result["per_worker"][0]["successful_rps"] == 0.5
    assert result["status"] == "fail"


def test_complete_source_window_must_fall_inside_verified_egress_window(tmp_path):
    manifest = evidence(tmp_path)
    value = egress("0", "before", "8.8.8.8")
    value["captured_at"] = at(1)
    write(summary_path(tmp_path).parent / "egress-before.json", value)
    assert assess(manifest, tmp_path)["status"] == "fail"


def test_machine_evidence_can_be_embedded_in_core_summary(tmp_path):
    manifest = evidence(tmp_path)
    for worker in ("0", "1"):
        folder = summary_path(tmp_path, worker).parent
        captures = {phase: json.loads((folder / f"egress-{phase}.json").read_text()) for phase in ("before", "after")}
        update_summary(tmp_path, lambda summary: summary.update(machine=captures), worker)
        for phase in ("before", "after"):
            (folder / f"egress-{phase}.json").unlink()
    assert assess(manifest, tmp_path)["status"] == "pass"


def test_reported_commander_errors_fail_even_without_a_source_block(tmp_path):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, lambda summary: summary["pipeline_summary"].update(failures=[{"commander": "broken"}]))
    assert assess(manifest, tmp_path)["status"] == "fail"


def test_one_percent_transient_failures_are_accepted_with_sufficient_throughput(tmp_path):
    manifest = evidence(tmp_path)
    update_summary(tmp_path, lambda summary: summary["pipeline_summary"]["measured_request_rate"].update(
        requests_started=1000, successful_responses=990, failures=10))
    result = assess(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["per_worker"][0]["transient_failure_fraction"] == 0.01


def test_duplicate_summary_artifacts_cannot_satisfy_the_pilot(tmp_path):
    manifest = evidence(tmp_path)
    value = json.loads(summary_path(tmp_path).read_text())
    write(tmp_path / "duplicate/shard-0-summary.json", value)
    assert assess(manifest, tmp_path)["status"] == "fail"


def test_cli_persists_inconclusive_gate_for_unreadable_manifest(tmp_path, monkeypatch, capsys):
    output = tmp_path / "assessment.json"
    monkeypatch.setattr("sys.argv", ["check_distributed_pilot.py", "assess", "--manifest",
        str(tmp_path / "missing.json"), "--summaries-dir", str(tmp_path), "--output", str(output)])
    pilot.main()
    value = json.loads(output.read_text())
    assert value["status"] == "inconclusive" and value["allowed_scale_up"] is False
    assert json.loads(capsys.readouterr().out)["allowed_scale_up"] is False


def test_job_identity_must_be_stable_across_collection(tmp_path):
    manifest = evidence(tmp_path)
    path = summary_path(tmp_path).parent / "egress-after.json"
    value = json.loads(path.read_text())
    value["job_id"] = "another-job"
    write(path, value)
    assert assess(manifest, tmp_path)["status"] == "fail"
