"""Independent runner ownership and durable checkpoint merge boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("distributed_build_styles", ROOT / "scripts/distributed_build_styles.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def write(path, value):
    core.atomic_json(path, value)


def setup(tmp_path, *, totals=None):
    base = tmp_path / "canonical"
    totals = totals or {"a-large": 10000, "b-medium": 6000, "c-small": 3000, "d-ready": 2500, "low": 199, "unknown": None}
    rows = [{"commander_slug": slug, "total_decks": total, "name": slug} for slug, total in totals.items()]
    index = tmp_path / "index.json"
    write(index, rows)
    state = {"schema_version": 1, "source_rate": {"learned_rate": core.SEED_REQUEST_RATE, "throttles": 3},
             "commanders": {}, "pipeline": {"version": 1, "cycle_started_at": "2026-10-20T00:00:00+00:00"}}
    for slug in totals:
        write(base / f"{slug}.json", {"commander_slug": slug, "unchanged": True, "total_edhrec_decks": totals[slug]})
        write(base / "_collection/sources" / slug / "base.json", {"from": slug})
        write(base / "_collection/sources" / slug / "previews/raw.json", {"raw": True})
        path = base / "_collection/previews" / f"{slug}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"parsed checkpoint preserved")
    write(base / core.STATE, state)
    proof = base / "_collection/external-validations" / ("a" * 64 + ".json")
    write(proof, {"source": "saved exact proof"})
    return base, index, state


def planned(tmp_path, **kwargs):
    base, index, state = setup(tmp_path)
    inputs = tmp_path / "plan"
    manifest = core.plan(base, index, inputs, kwargs.pop("runners", 2), "run-123", **kwargs)
    return base, inputs, manifest


def run_worker(tmp_path, inputs, manifest, worker_id, *, blocked=False, access=False, throttle=0, change=True):
    worker = core.worker_record(manifest, worker_id)
    root = tmp_path / "work" / str(worker_id) / "styles"
    core.extract_archive(inputs / worker["archive"], root, owned=set(worker["commanders"]), shared_inputs=True)
    state = core.read_json(root / core.STATE)
    for slug in worker["commanders"]:
        state["commanders"][slug] = {"status": "collecting", "current_previews": 100, "frame_decks": 10000}
    if blocked:
        state.update(blocked_until=(datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
                     block_reason="access_restriction" if access else "rate_limit")
    state["source_rate"]["learned_rate"] = 1.0 if blocked else core.SEED_REQUEST_RATE
    if change:
        write(root / f"{worker['commanders'][0]}.json", {"commander_slug": worker["commanders"][0], "changed": True})
    write(root / core.STATE, state)
    write(root.parent / "build_style_batch.json", {"completed": worker["commanders"][:1], "new_previews_attempted": 90,
        "elapsed_seconds": 100,
        "capacity": {"evaluated_at": "2026-10-30T12:01:40+00:00"},
        "measured_request_rate": {"requests_started": 100, "successful_responses": 99, "failures": 1,
            "throttles": throttle, "started_at": "2026-10-30T12:00:00+00:00", "completed_at": "2026-10-30T12:01:40+00:00"}})
    write(root / core.MACHINE, {"before": {"public_ipv4": "192.0.2.1"}, "after": {"public_ipv4": "192.0.2.1"}})
    result_dir = tmp_path / "results" / f"artifact-{worker_id}"
    summary = core.checkpoint(inputs / "manifest.json", worker_id, root, result_dir)
    return root, result_dir, summary


def merge(tmp_path, base, inputs):
    return core.merge(base, inputs / "manifest.json", tmp_path / "results", inputs,
                      tmp_path / "merged-summary.json", generation="run-123")


def test_plan_is_disjoint_balanced_and_enforces_population_minimum(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    claims = [slug for worker in manifest["workers"] for slug in worker["commanders"]]
    assert set(claims) == {"a-large", "b-medium", "c-small", "d-ready"}
    assert len(claims) == len(set(claims))
    loads = [worker["estimated_remaining_requests"] for worker in manifest["workers"]]
    assert max(loads) - min(loads) <= 10000
    for worker in manifest["workers"]:
        with tarfile.open(inputs / worker["archive"]) as archive:
            names = archive.getnames()
            assert any("external-validations" in name for name in names)
            assert not any("/previews/" in name and "/sources/" in name for name in names)
            assert not any(name.startswith("low.") or name.startswith("unknown.") for name in names)
            assert all(core.allowed_path(name, set(worker["commanders"]), shared_inputs=True) for name in names)
    assert core.read_json(inputs / "manifest.json") == manifest
    assert core.load_manifest(inputs / "manifest.json") == manifest


def test_scaling_preserves_partial_ownership_and_balances_unstarted_work(tmp_path):
    base, index, state = setup(tmp_path)
    state["commanders"]["a-large"] = {"status": "collecting", "current_previews": 100}
    state["distributed"] = {"workers": {"1": {"commanders": ["a-large", "b-medium", "c-small", "d-ready"],
        "source_rate": {"learned_rate": 0.9, "throttles": 4}}}}
    write(base / core.STATE, state)
    manifest = core.plan(base, index, tmp_path / "plan", 4, "run-123")
    assert "a-large" in manifest["workers"][1]["commanders"]
    assert all(worker["commanders"] for worker in manifest["workers"])
    assert manifest["workers"][1]["source_rate"]["learned_rate"] == 0.9
    assert all(worker["source_rate"]["learned_rate"] == core.SEED_REQUEST_RATE for worker in manifest["workers"] if worker["id"] != "1")


@pytest.mark.parametrize("completed_count", [16, 19])
def test_completed_samples_balance_offline_work_independently_of_unequal_network_loads(tmp_path, completed_count):
    network = {"a-network": 10000, "b-network": 6000, "c-network": 3000, "d-network": 2500,
               "new-network": 4000}
    completed = {f"completed-{number:02}": 200 for number in range(completed_count)}
    base, index, state = setup(tmp_path, totals=network | completed)
    state["distributed"] = {"workers": {}}
    for number, slug in enumerate(list(network)[:4]):
        state["commanders"][slug] = {"status": "collecting", "coverage_complete": False,
                                      "frame_decks": network[slug], "current_previews": 100}
        state["distributed"]["workers"][str(number)] = {"commanders": [slug]}
    for slug in completed:
        state["commanders"][slug] = {"status": "fitted", "coverage_complete": True,
                                      "frame_decks": 200, "current_previews": 200}
    write(base / core.STATE, state)
    network_index = tmp_path / "network-index.json"
    write(network_index, [row for row in core.read_json(index) if row["commander_slug"] in network])
    network_only = core.plan(base, network_index, tmp_path / "network-plan", 4, "network-only")
    manifest = core.plan(base, index, tmp_path / "plan", 4, "with-offline-repairs")
    counts = [len(set(worker["commanders"]) & set(completed)) for worker in manifest["workers"]]
    assert sum(counts) == completed_count
    assert max(counts) - min(counts) <= 1
    assert all(count >= completed_count // 4 for count in counts)
    claims = [slug for worker in manifest["workers"] for slug in worker["commanders"]]
    assert len(claims) == len(set(claims)) == len(network) + completed_count
    assert len({worker["estimated_remaining_requests"] for worker in manifest["workers"]}) > 1
    for worker, original in zip(manifest["workers"], network_only["workers"]):
        assert [slug for slug in worker["commanders"] if slug in network] == original["commanders"]
        assert worker["estimated_remaining_requests"] == original["estimated_remaining_requests"]
        if int(worker["id"]) < 4:
            assert list(network)[int(worker["id"])] in worker["commanders"]


def test_pilot_avoids_model_backlog_small_frames_and_saved_retries(tmp_path):
    base, index, state = setup(tmp_path)
    state["commanders"]["a-large"] = {"status": "analysis_pending", "current_previews": 1}
    state["commanders"]["c-small"] = {"status": "collecting", "current_previews": 1500}
    write(base / core.STATE, state)
    manifest = core.plan(base, index, tmp_path / "plan", 2, "run-123", pilot=True)
    assert {s for worker in manifest["workers"] for s in worker["commanders"]} == {"b-medium", "d-ready"}
    assert manifest["pilot"]


def test_pilot_requires_exactly_two_network_ready_commanders(tmp_path):
    base, index, state = setup(tmp_path)
    with pytest.raises(ValueError, match="exactly two"):
        core.plan(base, index, tmp_path / "plan", 4, "run-123", pilot=True)
    for slug in ("a-large", "b-medium", "c-small"):
        state["commanders"][slug] = {"status": "analysis_pending"}
    write(base / core.STATE, state)
    with pytest.raises(ValueError, match="network-ready"):
        core.plan(base, index, tmp_path / "plan", 2, "run-123", pilot=True)


def test_saved_access_restriction_prevents_distributed_restart(tmp_path):
    base, index, state = setup(tmp_path)
    state["block_reason"] = "access_restriction"
    write(base / core.STATE, state)
    with pytest.raises(ValueError, match="access restriction"):
        core.plan(base, index, tmp_path / "plan", 2, "run-123")


def test_resharding_cannot_erase_old_workers_commander_cooldown(tmp_path):
    base, index, state = setup(tmp_path)
    until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    state["distributed"] = {"workers": {"9": {"commanders": ["a-large"], "blocked_until": until, "block_reason": "rate_limit"}}}
    write(base / core.STATE, state)
    manifest = core.plan(base, index, tmp_path / "plan", 2, "run-123")
    worker = next(item for item in manifest["workers"] if "a-large" in item["commanders"])
    root = tmp_path / "restored"
    core.extract_archive(tmp_path / "plan" / worker["archive"], root, owned=set(worker["commanders"]), shared_inputs=True)
    assert core.read_json(root / core.STATE)["commanders"]["a-large"]["retry_after"] == until


def test_merge_preserves_unowned_data_proofs_and_independent_rates(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    original_low = (base / "low.json").read_bytes()
    proof = base / "_collection/external-validations" / ("a" * 64 + ".json")
    original_proof = proof.read_bytes()
    for worker in manifest["workers"]:
        root, result, receipt = run_worker(tmp_path, inputs, manifest, worker["id"])
        assert receipt["machine"]["before"]["public_ipv4"] == "192.0.2.1"
        assert receipt["checkpoint"]["valid"] and not receipt["checkpoint"]["used_fallback"]
        with tarfile.open(result / receipt["checkpoint"]["archive"]) as archive:
            assert not any("external-validations" in name for name in archive.getnames())
    summary = merge(tmp_path, base, inputs)
    state = core.read_json(base / core.STATE)
    assert (base / "low.json").read_bytes() == original_low
    assert proof.read_bytes() == original_proof
    assert len(summary["changed_reports"]) == 2
    assert summary["measured_request_rate"]["requests_started"] == 200
    assert summary["measured_request_rate"]["successful_responses"] == 198
    assert summary["measured_request_rate"]["observed_requests_per_second"] == pytest.approx(1.98)
    assert summary["capacity"]["effective_requests_per_second"] == 2 * core.SEED_REQUEST_RATE
    assert state["source_rate"]["learned_rate"] == core.SEED_REQUEST_RATE
    assert state["progress"]["capacity"]["rate_scope"] == "aggregate_independent_workers"
    assert summary["continuation_needed"] and not summary["blocked"]


def test_missing_result_uses_original_input_without_losing_other_progress(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    run_worker(tmp_path, inputs, manifest, "0")
    other_slug = manifest["workers"][1]["commanders"][0]
    before = (base / f"{other_slug}.json").read_bytes()
    summary = merge(tmp_path, base, inputs)
    assert summary["distributed"]["missing_workers"] == ["1"]
    assert summary["distributed"]["fallback_workers"] == ["1"]
    assert summary["measured_request_rate"]["requests_started"] == 100
    assert (base / f"{other_slug}.json").read_bytes() == before
    assert not summary["automatic_continuation_allowed"] and not summary["continuation_needed"]


def test_checkpoint_with_corrupted_worktree_returns_valid_original_fallback(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    root = tmp_path / "missing" / "styles"
    receipt = core.checkpoint(inputs / "manifest.json", "0", root, tmp_path / "results", exit_code=1)
    assert receipt["checkpoint"]["valid"] and receipt["checkpoint"]["used_fallback"]
    assert receipt["exit_code"] == 1
    summary = merge(tmp_path, base, inputs)
    assert "0" in summary["distributed"]["fallback_workers"]
    assert not summary["continuation_needed"]


def test_failed_collection_still_retains_valid_progress_and_source_controls(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    root, result_dir, _ = run_worker(tmp_path, inputs, manifest, "0", blocked=True, throttle=1)
    receipt = core.checkpoint(inputs / "manifest.json", "0", root, result_dir, exit_code=1)
    assert not receipt["checkpoint"]["used_fallback"]
    run_worker(tmp_path, inputs, manifest, "1")
    summary = merge(tmp_path, base, inputs)
    state = core.read_json(base / core.STATE)
    assert state["distributed"]["workers"]["0"]["block_reason"] == "rate_limit"
    assert not summary["blocked"] and summary["distributed"]["systemic_block"] is False
    assert summary["failures"]


def test_correlated_throttles_stop_global_automatic_collection(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    for worker in manifest["workers"]:
        run_worker(tmp_path, inputs, manifest, worker["id"], blocked=True, throttle=1)
    summary = merge(tmp_path, base, inputs)
    assert summary["blocked"] and summary["block_reason"] == "rate_limit"
    assert summary["distributed"]["systemic_block"]
    assert not summary["continuation_needed"]


def test_one_access_restriction_stops_every_worker(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    run_worker(tmp_path, inputs, manifest, "0", blocked=True, access=True)
    run_worker(tmp_path, inputs, manifest, "1")
    summary = merge(tmp_path, base, inputs)
    assert summary["blocked"] and summary["block_reason"] == "access_restriction"
    with pytest.raises(ValueError, match="access restriction"):
        core.plan(base, tmp_path / "index.json", tmp_path / "new-plan", 2, "run-124")


def test_pilot_never_self_scales_or_continues(tmp_path):
    base, inputs, manifest = planned(tmp_path, pilot=True)
    for worker in manifest["workers"]:
        run_worker(tmp_path, inputs, manifest, worker["id"])
    summary = merge(tmp_path, base, inputs)
    assert summary["distributed"]["pilot"]
    assert not summary["automatic_continuation_allowed"] and not summary["continuation_needed"]


def test_generation_mismatch_rejected_before_canonical_mutation(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    root, result, receipt = run_worker(tmp_path, inputs, manifest, "0")
    receipt["generation"] = "other-run"
    write(result / "shard-0-summary.json", receipt)
    before = (base / core.STATE).read_bytes(), (base / f"{manifest['workers'][0]['commanders'][0]}.json").read_bytes()
    with pytest.raises(ValueError, match="generation"):
        merge(tmp_path, base, inputs)
    assert before == ((base / core.STATE).read_bytes(), (base / f"{manifest['workers'][0]['commanders'][0]}.json").read_bytes())


def test_duplicate_claims_are_rejected(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    manifest["workers"][1]["commanders"].append(manifest["workers"][0]["commanders"][0])
    write(inputs / "manifest.json", manifest)
    with pytest.raises(ValueError, match="multiple runners"):
        core.load_manifest(inputs / "manifest.json")


def test_missing_or_changed_original_fallback_stops_merge(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    (inputs / manifest["workers"][0]["archive"]).write_bytes(b"broken")
    before = (base / core.STATE).read_bytes()
    with pytest.raises(ValueError, match="fallback"):
        merge(tmp_path, base, inputs)
    assert (base / core.STATE).read_bytes() == before


def test_report_or_state_changed_after_planning_rejects_stale_base(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    slug = manifest["workers"][0]["commanders"][0]
    write(base / f"{slug}.json", {"commander_slug": slug, "newer": True})
    with pytest.raises(ValueError, match="report changed"):
        merge(tmp_path, base, inputs)


def test_new_report_absent_in_baseline_is_also_guarded(tmp_path):
    base, index, state = setup(tmp_path)
    (base / "a-large.json").unlink()
    inputs = tmp_path / "plan"
    manifest = core.plan(base, index, inputs, 2, "run-123")
    assert manifest["base_report_sha256"]["a-large"] is None
    write(base / "a-large.json", {"commander_slug": "a-large", "newer": True})
    with pytest.raises(ValueError, match="report changed"):
        merge(tmp_path, base, inputs)


@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "hardlink", "duplicate", "unowned"])
def test_extract_rejects_unsafe_archive_before_writing_any_files(tmp_path, kind):
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        first = tarfile.TarInfo("a-large.json");first.size = 2
        archive.addfile(first, io.BytesIO(b"{}"))
        name = {"traversal": "../escape", "absolute": "/tmp/escape", "unowned": "other.json"}.get(kind, "link")
        if kind == "duplicate": name = "a-large.json"
        item = tarfile.TarInfo(name)
        if kind in {"symlink", "hardlink"}:
            item.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            item.linkname = "../escape"
            archive.addfile(item)
        else:
            item.size = 2
            archive.addfile(item, io.BytesIO(b"{}"))
    destination = tmp_path / "extract"
    with pytest.raises(ValueError, match="Unsafe|unowned"):
        core.extract_archive(archive_path, destination, owned={"a-large"})
    assert not destination.exists()


def test_extract_rejects_existing_symlink_destination(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    worker = manifest["workers"][0]
    destination = tmp_path / "extract"
    destination.mkdir()
    outside = tmp_path / "outside";outside.mkdir()
    (destination / "_collection").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        core.extract_archive(inputs / worker["archive"], destination, owned=set(worker["commanders"]), shared_inputs=True)
    assert not list(outside.iterdir())


def test_duplicate_worker_receipts_cannot_win_by_download_order(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    root, result, receipt = run_worker(tmp_path, inputs, manifest, "0")
    write(tmp_path / "results/duplicate/shard-0-summary.json", receipt)
    with pytest.raises(ValueError, match="Duplicate distributed result"):
        merge(tmp_path, base, inputs)


def test_merge_streams_caches_without_staging_another_full_copy(tmp_path, monkeypatch):
    base, inputs, manifest = planned(tmp_path)
    expected = {}
    for worker in manifest["workers"]:
        root, results, _ = run_worker(tmp_path, inputs, manifest, worker["id"])
        slug = worker["commanders"][0]
        content = (f"updated parsed lists for {slug}").encode()
        (root / "_collection/previews" / f"{slug}.json.gz").write_bytes(content)
        expected[slug] = content
        core.checkpoint(inputs / "manifest.json", worker["id"], root, results)
    extractor = core.extract_archive
    observed = []
    def inspect(*args, **kwargs):
        selected = kwargs["selected"]
        assert selected(core.STATE)
        assert selected("a-large.json")
        assert not selected("_collection/previews/a-large.json.gz")
        observed.append(True)
        return extractor(*args, **kwargs)
    monkeypatch.setattr(core, "extract_archive", inspect)
    summary = merge(tmp_path, base, inputs)
    assert len(observed) == 2
    for slug, content in expected.items():
        assert (base / "_collection/previews" / f"{slug}.json.gz").read_bytes() == content
    storage = summary["distributed"]["storage"]
    assert storage["input_bytes_reclaimed"] > 0
    assert storage["canonical_bytes_after"] > 0
    state = core.read_json(base / core.STATE)
    assert state["progress"]["rate_control"]["learned_rate"] == 2 * core.SEED_REQUEST_RATE
    assert state["source_rate"]["learned_rate"] == core.SEED_REQUEST_RATE
    assert state["progress"]["distributed"]["runners"] == 2


def test_insufficient_stage_disk_preserves_canonical_and_original_inputs(tmp_path, monkeypatch):
    from collections import namedtuple
    base, inputs, manifest = planned(tmp_path)
    before = (base / core.STATE).read_bytes()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _path: usage(10, 10, 0))
    with pytest.raises(ValueError, match="free disk"):
        merge(tmp_path, base, inputs)
    assert (base / core.STATE).read_bytes() == before
    assert all((inputs / worker["archive"]).exists() for worker in manifest["workers"])


def test_insufficient_commit_reserve_stops_before_changing_canonical_files(tmp_path, monkeypatch):
    from collections import namedtuple
    base, inputs, manifest = planned(tmp_path)
    for worker in manifest["workers"]:
        root, results, _ = run_worker(tmp_path, inputs, manifest, worker["id"])
        slug = worker["commanders"][0]
        (root / "_collection/previews" / f"{slug}.json.gz").write_bytes(b"x" * (2 * 1024 ** 2))
        core.checkpoint(inputs / "manifest.json", worker["id"], root, results)
    before = {path.relative_to(base).as_posix(): path.read_bytes() for path in base.rglob("*") if path.is_file()}
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(core, "MIN_DISK_RESERVE_BYTES", 1024 ** 2)
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _path: usage(10 * 1024 ** 2, 7 * 1024 ** 2, 3 * 1024 ** 2))
    with pytest.raises(ValueError, match="commit.*reserve"):
        merge(tmp_path, base, inputs)
    assert before == {path.relative_to(base).as_posix(): path.read_bytes() for path in base.rglob("*") if path.is_file()}


def test_unknown_failed_job_keeps_checkpoint_but_requires_review_before_retry(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    for worker in manifest["workers"]:
        root, results, _ = run_worker(tmp_path, inputs, manifest, worker["id"])
        if worker["id"] == "0":
            core.checkpoint(inputs / "manifest.json", "0", root, results, exit_code=137)
    summary = merge(tmp_path, base, inputs)
    assert summary["changed_reports"]
    assert summary["distributed"]["failed_workers"] == ["0"]
    assert not summary["automatic_continuation_allowed"] and not summary["continuation_needed"]


def test_partial_worker_cooldown_suppresses_incomplete_catalog_eta(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    run_worker(tmp_path, inputs, manifest, "0", blocked=True, throttle=1)
    run_worker(tmp_path, inputs, manifest, "1")
    summary = merge(tmp_path, base, inputs)
    progress = core.read_json(base / core.STATE)["progress"]
    assert progress["distributed"]["active_workers"] == 1
    assert progress["distributed"]["blocked_workers"] == 1
    assert summary["capacity"]["available_requests_per_second"] == core.SEED_REQUEST_RATE
    assert summary["capacity"]["estimated_minimum_seconds"] is None
    assert summary["capacity"]["measured_projection_seconds"] is None
    assert summary["capacity"]["projected_finish_at"] is None
    assert not summary["blocked"]


def test_per_worker_pipeline_sticky_claims_survive_merge_and_next_plan(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    original_claims = {}
    for worker in manifest["workers"]:
        root, results, _ = run_worker(tmp_path, inputs, manifest, worker["id"])
        state = core.read_json(root / core.STATE)
        slug = worker["commanders"][0]
        state["pipeline"]["collection_workers"] = {"0": slug}
        original_claims[worker["id"]] = slug
        write(root / core.STATE, state)
        core.checkpoint(inputs / "manifest.json", worker["id"], root, results)
    merge(tmp_path, base, inputs)
    next_inputs = tmp_path / "next-plan"
    next_manifest = core.plan(base, tmp_path / "index.json", next_inputs, 2, "run-124")
    for worker in next_manifest["workers"]:
        destination = tmp_path / "next-work" / worker["id"]
        core.extract_archive(next_inputs / worker["archive"], destination, owned=set(worker["commanders"]), shared_inputs=True)
        state = core.read_json(destination / core.STATE)
        assert state["pipeline"]["collection_workers"] == {"0": original_claims[worker["id"]]}


def test_completion_reuses_production_freshness_and_model_policy_queue(tmp_path):
    base, index, state = setup(tmp_path)
    rows = list(core.inventory(index).values())
    published = {r["commander_slug"] for r in rows}
    policy = core.production_queue_plan(state, rows, published, tmp_path / "policy")
    fresh = datetime.now(timezone.utc) - timedelta(hours=2)
    for row in rows:
        state["commanders"][row["commander_slug"]] = {"status": "fitted", "coverage_complete": True,
            "frame_decks": row["total_decks"], "current_previews": row["total_decks"],
            "verified_at": fresh.isoformat(), "model_policy": policy["model_policy"],
            "tag_label_policy": policy["tag_label_policy"]}
    assert core.production_queue_plan(state, rows, published, tmp_path / "fresh")["remaining_in_batch"] == 0
    state["commanders"]["a-large"]["verified_at"] = (fresh - timedelta(hours=23)).isoformat()
    state["commanders"]["b-medium"]["model_policy"] = {"version": 0, "max_components": 8}
    inputs = tmp_path / "plan"
    manifest = core.plan(base, index, inputs, 2, "run-123")
    # Put the complete current state into the planned baseline before rebuilding
    # the manifest, so all list counters are full but two refresh tasks remain.
    write(base / core.STATE, state)
    manifest = core.plan(base, index, inputs, 2, "run-123")
    for worker in manifest["workers"]:
        root = tmp_path / "work" / worker["id"] / "styles"
        core.extract_archive(inputs / worker["archive"], root, owned=set(worker["commanders"]), shared_inputs=True)
        write(root.parent / "build_style_batch.json", {"measured_request_rate": {"requests_started": 1, "successful_responses": 1},
            "elapsed_seconds": 1, "capacity": {"evaluated_at": datetime.now(timezone.utc).isoformat()}})
        core.checkpoint(inputs / "manifest.json", worker["id"], root, tmp_path / "results" / worker["id"])
    summary = merge(tmp_path, base, inputs)
    assert summary["remaining_in_batch"] == 2
    assert summary["continuation_needed"]


def test_worker_resource_limit_is_retained_and_stops_automatic_retry(tmp_path):
    base, inputs, manifest = planned(tmp_path)
    for worker in manifest["workers"]:
        root, results, _ = run_worker(tmp_path, inputs, manifest, worker["id"])
        if worker["id"] == "0":
            pipeline = core.read_json(root.parent / "build_style_batch.json")
            pipeline["resource_limit"] = "Insufficient local disk for another durable checkpoint"
            write(root.parent / "build_style_batch.json", pipeline)
            core.checkpoint(inputs / "manifest.json", "0", root, results)
    summary = merge(tmp_path, base, inputs)
    assert summary["resource_limit"]["0"].startswith("Insufficient")
    assert not summary["continuation_needed"] and not summary["automatic_continuation_allowed"]
    assert core.read_json(base / core.STATE)["progress"]["resource_limit"] == summary["resource_limit"]
