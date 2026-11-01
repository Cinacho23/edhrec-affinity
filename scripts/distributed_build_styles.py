#!/usr/bin/env python3
"""Plan independent EDHREC runners and merge their immutable checkpoints.

The planner owns commander assignments. A runner receives only its assigned
files and source controls; it cannot publish another runner's reports. The
merger checks the complete generation before changing the canonical checkpoint.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from zoneinfo import ZoneInfo

from edhrec_affinity.build_styles.capacity import plan_capacity
from edhrec_affinity.build_styles.eligibility import commander_is_eligible, valid_deck_count

SCHEMA_VERSION = 1
SEED_REQUEST_RATE = 1.318359375
MAX_FILES = 300000
MAX_BYTES = 32 * 1024 ** 3
MIN_DISK_RESERVE_BYTES = 2 * 1024 ** 3
MARKER = "_collection/distributed-worker.json"
STATE = "_collection/state.json"
MACHINE = "_collection/distributed-machine.json"


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def reject_nonfinite(value):
    raise ValueError(f"Nonfinite JSON: {value}")


def read_json(path):
    return json.loads(Path(path).read_text(), parse_constant=reject_nonfinite)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json_bytes(value))
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 ** 2), b""):
            h.update(block)
    return h.hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("Invalid commander slug")
    return value


def generation_id(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Invalid distributed generation")
    return value


def source_rate(value):
    result = deepcopy(value) if isinstance(value, dict) else {}
    rate = result.get("learned_rate") or SEED_REQUEST_RATE
    if type(rate) not in {float, int} or not math.isfinite(rate) or rate <= 0:
        raise ValueError("Invalid learned worker request rate")
    result["learned_rate"] = rate
    throttles = result.get("throttles", 0)
    if type(throttles) is not int or throttles < 0:
        raise ValueError("Invalid worker throttle history")
    result["throttles"] = throttles
    return result


def checked_state(value, owned=None):
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("commanders"), dict):
        raise ValueError("Invalid collection checkpoint")
    for slug, record in value["commanders"].items():
        identifier(slug)
        if not isinstance(record, dict):
            raise ValueError("Invalid commander collection record")
        if owned is not None and slug not in owned:
            raise ValueError("Worker checkpoint contains an unowned commander")
    if "source_rate" in value:
        source_rate(value["source_rate"])
    if value.get("blocked_until") is not None and timestamp(value["blocked_until"]) is None:
        raise ValueError("Invalid source cooldown")
    return value


def inventory(path):
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError("Commander index must be a list")
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid commander index row")
        slug = identifier(row.get("commander_slug"))
        if slug in found:
            raise ValueError("Duplicate commander index identity")
        if commander_is_eligible(row.get("total_decks")):
            found[slug] = deepcopy(row)
    return found


def remaining(row, record):
    frame = valid_deck_count(record.get("frame_decks")) or valid_deck_count(row.get("total_decks")) or 0
    loaded = valid_deck_count(record.get("current_previews")) or 0
    return max(0, frame - loaded)


def allowed_path(name, owned, *, shared_inputs=False):
    """Allow only owned inputs, state, and this runner's machine receipt."""
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name
            or any(part in {"", "."} for part in name.split("/")) or path.as_posix() != name):
        return False
    parts = path.parts
    if name in {STATE, MARKER, MACHINE}:
        return True
    if (shared_inputs and len(parts) == 3 and parts[:2] == ("_collection", "external-validations")
            and re.fullmatch(r"[a-f0-9]{64}(?:\.[a-f0-9]{64})?\.json", parts[2])):
        return True
    if len(parts) == 1:
        return path.suffix == ".json" and path.stem in owned
    if parts[:2] in {("_collection", "previews"), ("_collection", "evidence")} and len(parts) == 3:
        return parts[2].endswith(".json.gz") and parts[2][:-8] in owned
    return (len(parts) >= 4 and parts[:2] == ("_collection", "sources") and parts[2] in owned
            and "previews" not in parts[3:] and not any(part.endswith(".tmp") for part in parts))


def owned_files(root, owned):
    found = {}
    for slug in sorted(owned):
        candidates = [root / f"{slug}.json", root / "_collection/previews" / f"{slug}.json.gz",
                      root / "_collection/evidence" / f"{slug}.json.gz"]
        source = root / "_collection/sources" / slug
        if source.is_symlink():
            raise ValueError("Symlink source directories are not supported")
        if source.exists():
            candidates.extend(source.rglob("*"))
        for path in candidates:
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError("Symlink checkpoint files are not supported")
            if path.is_file() and allowed_path(name, owned):
                found[name] = path
    return found


def archive_files(destination, files, extra):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz", compresslevel=1) as archive:
        for name, path in sorted(files.items()):
            archive.add(path, arcname=name, recursive=False)
        for name, value in sorted(extra.items()):
            data = json_bytes(value)
            item = tarfile.TarInfo(name)
            item.size, item.mtime, item.mode = len(data), 0, 0o644
            archive.addfile(item, io.BytesIO(data))
    temporary.replace(destination)
    return len(files) + len(extra)


def archive_members(archive, owned=None, *, shared_inputs=False):
    """Preflight every member before extraction; links and special files fail."""
    found, total = {}, 0
    for member in archive.getmembers():
        name = member.name
        path = PurePosixPath(name)
        if (member.isdir() or not member.isfile() or path.is_absolute() or ".." in path.parts
                or "\\" in name or "\x00" in name or any(part in {"", "."} for part in name.split("/"))
                or path.as_posix() != name or name in found):
            raise ValueError("Unsafe or duplicate checkpoint archive member")
        if owned is not None and not allowed_path(name, owned, shared_inputs=shared_inputs):
            raise ValueError(f"Checkpoint contains unowned path: {name}")
        total += member.size
        if total > MAX_BYTES or len(found) >= MAX_FILES:
            raise ValueError("Checkpoint archive exceeds resource bounds")
        found[name] = member
    return found


def extract_archive(path, destination, *, owned=None, shared_inputs=False, selected=None):
    destination = Path(destination)
    with tarfile.open(path, "r:gz") as archive:
        members = archive_members(archive, owned, shared_inputs=shared_inputs)
        for name in members:
            if selected is not None and not selected(name):
                continue
            target = destination / name
            for parent in [target, *target.parents]:
                if parent == destination.parent:
                    break
                if parent.is_symlink():
                    raise ValueError("Extraction destination contains a symlink")
        destination.mkdir(parents=True, exist_ok=True)
        for name, member in members.items():
            if selected is not None and not selected(name):
                continue
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return list(members)


def load_manifest(path):
    manifest = read_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Invalid distributed manifest")
    generation_id(manifest.get("generation"))
    workers = manifest.get("workers")
    if not isinstance(workers, list) or not 2 <= len(workers) <= 16:
        raise ValueError("Distributed manifest needs 2 to 16 workers")
    ids, owned = set(), set()
    for worker in workers:
        if not isinstance(worker, dict) or worker.get("id") != str(len(ids)) or worker["id"] in ids:
            raise ValueError("Invalid worker identity")
        ids.add(worker["id"])
        slugs = worker.get("commanders")
        if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs) or len(set(slugs)) != len(slugs):
            raise ValueError("Duplicate commander claims")
        for slug in slugs:
            identifier(slug)
            if slug in owned:
                raise ValueError("Commander is claimed by multiple runners")
            owned.add(slug)
        if worker.get("archive") != f"shard-{worker['id']}.tar.gz":
            raise ValueError("Invalid shard archive identity")
        if not re.fullmatch(r"[0-9a-f]{64}", worker.get("archive_sha256", "")):
            raise ValueError("Invalid shard archive hash")
    return manifest


def worker_record(manifest, worker):
    worker = str(worker)
    for item in manifest["workers"]:
        if item["id"] == worker:
            return item
    raise ValueError("Unknown distributed worker")


def marker(manifest, worker):
    return {"schema_version": 1, "generation": manifest["generation"], "worker_id": worker["id"],
            "commanders": worker["commanders"]}


def plan(base, index, output_dir, runners, generation, *, pilot=False):
    base, output_dir = Path(base), Path(output_dir)
    if type(runners) is not int or not 2 <= runners <= 16 or pilot and runners != 2:
        raise ValueError("Use 2 to 16 runners; a pilot requires exactly two")
    generation = generation_id(generation)
    rows = inventory(index)
    state_path = base / STATE
    state = checked_state(read_json(state_path)) if state_path.exists() else {"schema_version": 1, "commanders": {}}
    previous = state.get("distributed", {}).get("workers", {})
    previous = previous if isinstance(previous, dict) else {}
    if state.get("block_reason") == "access_restriction" or any(
            isinstance(item, dict) and item.get("block_reason") == "access_restriction" for item in previous.values()):
        raise ValueError("A saved source access restriction requires review before distributed collection")
    records = deepcopy(state["commanders"])
    # A reshard may move a commander, but it must not erase the old owner's
    # source cooldown by putting that commander behind a different IP address.
    for control in previous.values():
        if not isinstance(control, dict):
            continue
        expiry = timestamp(control.get("blocked_until"))
        if expiry and expiry > now():
            for slug in control.get("commanders", []):
                if slug in rows:
                    record = records.setdefault(slug, {})
                    retry = timestamp(record.get("retry_after"))
                    if retry is None or retry < expiry:
                        record.update(retry_after=expiry.isoformat(), status="blocked")
    if pilot:
        candidates = [slug for slug, row in rows.items()
            if remaining(row, records.get(slug, {})) >= 2000
            and not records.get(slug, {}).get("coverage_complete")
            and records.get(slug, {}).get("status") not in {"analysis_pending", "ineligible", "blocked"}
            and not ((retry := timestamp(records.get(slug, {}).get("retry_after"))) and retry > now())]
        candidates.sort(key=lambda slug: (-remaining(rows[slug], records.get(slug, {})), slug))
        if len(candidates) < 2:
            raise ValueError("Pilot requires two network-ready commanders with at least 2,000 remaining previews each")
        selected = set(candidates[:2])
    else:
        selected = set(rows)
    if len(selected) < runners:
        raise ValueError("Each distributed runner needs at least one eligible commander")
    assignments = {str(i): [] for i in range(runners)}
    claimed = set()
    if not pilot:
        for worker, control in sorted(previous.items()):
            if worker not in assignments or not isinstance(control, dict):
                continue
            for slug in control.get("commanders", []):
                record = records.get(slug, {})
                sticky = (record.get("status") in {"collecting", "analysis_pending"}
                    or (valid_deck_count(record.get("current_previews")) or 0) > 0 and not record.get("coverage_complete"))
                if slug in selected and slug not in claimed and sticky:
                    assignments[worker].append(slug)
                    claimed.add(slug)
    loads = {worker: sum(remaining(rows[s], records.get(s, {})) for s in slugs) for worker, slugs in assignments.items()}
    # Completed samples may still need CPU-only model repairs. Balance those
    # independently of preview requests, which otherwise sends every zero-load
    # report to the same runner with the smallest unfinished network frame.
    offline_counts = {worker: sum(remaining(rows[s], records.get(s, {})) == 0 for s in slugs)
                      for worker, slugs in assignments.items()}
    for slug in sorted(selected - claimed, key=lambda s: (-remaining(rows[s], records.get(s, {})), s)):
        requests = remaining(rows[slug], records.get(slug, {}))
        worker = min(assignments, key=lambda key: (
            offline_counts[key] if requests == 0 else loads[key], len(assignments[key]), int(key)))
        assignments[worker].append(slug)
        loads[worker] += requests
        if requests == 0:
            offline_counts[worker] += 1
    report_hashes = {slug: digest(base / f"{slug}.json") if (base / f"{slug}.json").exists() else None for slug in selected}
    manifest = {"schema_version": 1, "generation": generation, "created_at": now().isoformat(),
        "runners": runners, "pilot": pilot, "min_total_decks": 200,
        "base_state_sha256": digest(state_path) if state_path.exists() else None,
        "base_report_sha256": report_hashes, "inventory": list(rows.values()), "workers": []}
    output_dir.mkdir(parents=True, exist_ok=True)
    seed = source_rate(state.get("source_rate", {}))
    global_cooldown = timestamp(state.get("blocked_until")) if not previous else None
    for worker, slugs in assignments.items():
        control = previous.get(worker) if isinstance(previous.get(worker), dict) else {}
        worker_state = {"schema_version": 1, "commanders": {s: records[s] for s in slugs if s in records},
            "source_rate": source_rate(control.get("source_rate", seed)),
            "pipeline": deepcopy(control.get("pipeline") or state.get("pipeline", {"version": 1, "cycle_started_at": now().isoformat()}))}
        worker_state["pipeline"]["collection_workers"] = {k: slug for k, slug in
            worker_state["pipeline"].get("collection_workers", {}).items() if slug in slugs}
        expiry = timestamp(control.get("blocked_until")) or global_cooldown
        if expiry and expiry > now():
            worker_state.update(blocked_until=expiry.isoformat(), block_reason=control.get("block_reason") or state.get("block_reason") or "rate_limit")
            for key in ("source_stop", "source_stops"):
                if key in control or key in state:
                    worker_state[key] = deepcopy(control.get(key, state.get(key)))
        item = {"id": worker, "archive": f"shard-{worker}.tar.gz", "commanders": slugs,
            "estimated_remaining_requests": loads[worker], "source_rate": worker_state["source_rate"]}
        manifest["workers"].append(item)
        files = owned_files(base, set(slugs))
        for proof in (base / "_collection/external-validations").glob("*.json"):
            name = proof.relative_to(base).as_posix()
            if proof.is_symlink():
                raise ValueError("Symlink observation inputs are not supported")
            if allowed_path(name, set(slugs), shared_inputs=True):
                files[name] = proof
        count = archive_files(output_dir / item["archive"], files, {STATE: worker_state, MARKER: marker(manifest, item)})
        item.update(archive_sha256=digest(output_dir / item["archive"]), files=count)
    atomic_json(output_dir / "manifest.json", manifest)
    atomic_json(output_dir / "commander-index.json", list(rows.values()))
    return manifest


def validate_tree(root, manifest, worker):
    owned = set(worker["commanders"])
    if read_json(root / MARKER) != marker(manifest, worker):
        raise ValueError("Worker checkpoint belongs to another generation or assignment")
    state = checked_state(read_json(root / STATE), owned)
    for slug in owned:
        path = root / f"{slug}.json"
        if path.exists() and (not isinstance(report := read_json(path), dict) or report.get("commander_slug") != slug):
            raise ValueError("Report identity does not match its assigned commander")
    return state


def checkpoint(manifest_path, worker_id, workroot, output_dir, *, exit_code=0):
    manifest_path, workroot, output_dir = Path(manifest_path), Path(workroot), Path(output_dir)
    manifest = load_manifest(manifest_path)
    worker = worker_record(manifest, worker_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_path = workroot.parent / "build_style_batch.json"
    try:
        pipeline = read_json(pipeline_path) if pipeline_path.exists() else {}
    except (OSError, ValueError):
        pipeline = {}
    machine_path = workroot / MACHINE
    try:
        machine = read_json(machine_path) if machine_path.exists() else {}
    except (OSError, ValueError):
        machine = {}
    fallback, reason = False, None
    temporary = None
    try:
        try:
            state = validate_tree(workroot, manifest, worker)
            files = owned_files(workroot, set(worker["commanders"]))
        except (OSError, ValueError, TypeError) as error:
            temporary = tempfile.TemporaryDirectory(prefix="distributed-checkpoint-")
            fallback_root = Path(temporary.name)
            archive = manifest_path.parent / worker["archive"]
            if digest(archive) != worker["archive_sha256"]:
                raise ValueError("Original shard fallback hash mismatch")
            extract_archive(archive, fallback_root, owned=set(worker["commanders"]), shared_inputs=True)
            state = validate_tree(fallback_root, manifest, worker)
            files = owned_files(fallback_root, set(worker["commanders"]))
            fallback, reason = True, f"{type(error).__name__}: {error}"[:500]
        archive = output_dir / f"shard-{worker['id']}-result.tar.gz"
        extra = {STATE: state, MARKER: marker(manifest, worker)}
        if machine:
            extra[MACHINE] = machine
        count = archive_files(archive, files, extra)
        summary = {"schema_version": 1, "generation": manifest["generation"], "worker_id": worker["id"],
            "commanders": worker["commanders"], "exit_code": exit_code,
            "checkpoint": {"archive": archive.name, "sha256": digest(archive), "valid": True,
                           "files": count, "used_fallback": fallback, "fallback_reason": reason},
            "pipeline_summary": pipeline, "machine": machine,
            "source_control": {key: deepcopy(state.get(key)) for key in
                               ("source_rate", "blocked_until", "block_reason", "source_stop", "source_stops")}}
        atomic_json(output_dir / f"shard-{worker['id']}-summary.json", summary)
        return summary
    finally:
        if temporary is not None:
            temporary.cleanup()


def _find(root, name):
    paths = list(Path(root).rglob(name))
    if len(paths) > 1:
        raise ValueError(f"Duplicate distributed result: {name}")
    return paths[0] if paths else None


def source_window(summary):
    machine, pipeline = summary.get("machine", {}), summary.get("pipeline_summary", {})
    measured = pipeline.get("measured_request_rate") or {}
    start, end = timestamp(measured.get("started_at")), timestamp(measured.get("completed_at"))
    if start and end and end > start:
        return start, end
    window = machine.get("source_window") or machine.get("source") or {}
    start = timestamp(window.get("started_at") or machine.get("source_started_at") or machine.get("started_at"))
    end = timestamp(window.get("completed_at") or machine.get("source_completed_at") or machine.get("completed_at"))
    if start and end and end > start:
        return start, end
    end = timestamp(pipeline.get("capacity", {}).get("evaluated_at"))
    seconds = pipeline.get("elapsed_seconds")
    if end and type(seconds) in {int, float} and math.isfinite(seconds) and seconds > 0:
        return end - timedelta(seconds=seconds), end
    return None


def production_queue_plan(state, rows, published, scratch):
    """Reuse the production dry-run queue for freshness and policy decisions.

    Only existence receipts are needed: the pipeline returns before loading
    evidence, creating executors, or making network requests in dry-run mode.
    This avoids a second, inevitably drifting definition of due commanders.
    """
    from edhrec_affinity.build_styles.pipeline import run_pipeline
    script = Path(__file__).with_name("build_site_build_styles.py")
    spec = importlib.util.spec_from_file_location("distributed_source_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.parser().parse_args(["--pipeline", "--dry-run", "--output-dir", str(scratch),
        "--workers", "2", "--refresh-after-days", "3", "--index-max-age-hours", "24",
        "--main-tag-decks", "15", "--min-tag-decks", "5", "--main-tag-fraction", "0.03",
        "--min-tag-fraction", "0.01", "--max-components", "all"])
    atomic_json(scratch / STATE, state)
    for slug in published:
        (scratch / f"{slug}.json").touch()
    return run_pipeline(args, rows, now=now)


def merge(base, manifest_path, results, inputs, output_summary, *, generation=None):
    base, manifest_path, results, inputs = map(Path, (base, manifest_path, results, inputs))
    manifest = load_manifest(manifest_path)
    if generation is not None and str(generation) != manifest["generation"]:
        raise ValueError("Merge generation does not match the manifest")
    state_path = base / STATE
    current_hash = digest(state_path) if state_path.exists() else None
    if current_hash != manifest.get("base_state_sha256"):
        raise ValueError("Canonical state changed since planning")
    for slug, expected in manifest["base_report_sha256"].items():
        current = digest(base / f"{slug}.json") if (base / f"{slug}.json").exists() else None
        if current != expected:
            raise ValueError("Canonical commander report changed since planning")
    state = checked_state(read_json(state_path)) if state_path.exists() else {"schema_version": 1, "commanders": {}}
    staged, summaries, missing = [], [], []
    staged_report_bytes = 0
    # All archives, summaries and ownership are validated before the first
    # canonical file changes. A missing result uses its immutable original.
    with tempfile.TemporaryDirectory(prefix="distributed-merge-") as temporary:
        for worker in manifest["workers"]:
            summary_file = _find(results, f"shard-{worker['id']}-summary.json")
            if summary_file:
                summary = read_json(summary_file)
                if (summary.get("schema_version") != 1 or summary.get("generation") != manifest["generation"]
                        or summary.get("worker_id") != worker["id"] or summary.get("commanders") != worker["commanders"]):
                    raise ValueError("Worker result does not match its generation or ownership")
                expected_name = f"shard-{worker['id']}-result.tar.gz"
                proof = summary.get("checkpoint", {})
                if proof.get("archive") != expected_name or proof.get("valid") is not True:
                    raise ValueError("Invalid worker checkpoint receipt")
                archive = _find(results, expected_name)
                if archive is None or digest(archive) != proof.get("sha256"):
                    raise ValueError("Worker checkpoint hash mismatch")
            else:
                archive = _find(inputs, worker["archive"])
                if archive is None or digest(archive) != worker["archive_sha256"]:
                    raise ValueError("Missing valid original shard fallback")
                missing.append(worker["id"])
                summary = {"schema_version": 1, "generation": manifest["generation"], "worker_id": worker["id"],
                    "commanders": worker["commanders"], "exit_code": None, "pipeline_summary": {}, "machine": {},
                    "checkpoint": {"archive": worker["archive"], "sha256": worker["archive_sha256"],
                                   "valid": True, "used_fallback": True, "fallback_reason": "Worker returned no checkpoint"}}
            destination = Path(temporary) / worker["id"]
            with tarfile.open(archive, "r:gz") as stream:
                inspected = archive_members(stream, set(worker["commanders"]),
                    shared_inputs=summary["checkpoint"].get("used_fallback") is True)
                report_bytes = sum(member.size for name, member in inspected.items()
                    if name in {STATE, MARKER, MACHINE} or "/" not in name)
            if shutil.disk_usage(Path(temporary)).free < report_bytes + MIN_DISK_RESERVE_BYTES:
                raise ValueError("Insufficient free disk to stage validated distributed reports; checkpoint inputs remain durable")
            staged_report_bytes += report_bytes
            names = extract_archive(archive, destination, owned=set(worker["commanders"]),
                                    shared_inputs=summary["checkpoint"].get("used_fallback") is True,
                                    selected=lambda name: name in {STATE, MARKER, MACHINE} or "/" not in name)
            worker_state = validate_tree(destination, manifest, worker)
            staged.append((worker, worker_state, destination, names, archive))
            summaries.append(summary)
        merged = deepcopy(state)
        distributed = merged.setdefault("distributed", {})
        controls = distributed.setdefault("workers", {})
        changed, completed, source_windows, stats_rows = set(), set(), [], []
        pending_copies = []
        actual_summaries = []
        for (worker, worker_state, destination, names, archive), summary in zip(staged, summaries, strict=True):
            merged["commanders"].update(deepcopy(worker_state["commanders"]))
            controls[worker["id"]] = {"commanders": worker["commanders"], "generation": manifest["generation"],
                "updated_at": now().isoformat(), **{key: deepcopy(worker_state.get(key)) for key in
                    ("source_rate", "blocked_until", "block_reason", "source_stop", "source_stops", "pipeline")}}
            actual = not summary["checkpoint"].get("used_fallback")
            if actual:
                actual_summaries.append(summary)
                pipeline = summary.get("pipeline_summary", {})
                completed.update(s for s in pipeline.get("completed", []) if s in worker["commanders"])
                stats = pipeline.get("measured_request_rate") or {}
                stats_rows.append(stats)
                if window := source_window(summary):
                    source_windows.append(window)
            for name in names:
                if name in {STATE, MARKER, MACHINE} or name.startswith("_collection/external-validations/"):
                    continue
                if not actual:
                    # The original input is identical to the pinned canonical
                    # snapshot. Missing workers cannot alter or roll it back.
                    continue
                source, target = destination / name, base / name
                if name.endswith(".json") and "/" not in name and (not target.exists() or digest(source) != digest(target)):
                    changed.add(name[:-5])
                for parent in [target, *target.parents]:
                    if parent == base.parent:
                        break
                    if parent.is_symlink():
                        raise ValueError("Canonical checkpoint contains a symlink target")
                pending_copies.append((archive, name, target))
        # Input artifacts are immutable remotely. Once an actual output is
        # fully validated locally, its now-unused original download can be
        # removed to avoid retaining another full dataset on the merger VM.
        archives_in_use = {archive.resolve() for _, _, _, _, archive in staged}
        reclaimed = 0
        for worker in manifest["workers"]:
            original = _find(inputs, worker["archive"])
            if (original is not None and inputs.resolve() in original.resolve().parents
                    and original.resolve() not in archives_in_use):
                reclaimed += original.stat().st_size
                original.unlink()
        distributed.update(schema_version=1, generation=manifest["generation"], runners=manifest["runners"],
                           pilot=manifest["pilot"], updated_at=now().isoformat())
        active_controls = [controls[w["id"]] for w in manifest["workers"]]
        # Retain the provider's original safe diagnostic instead of reducing a
        # metadata429 or HTTP403 to an indistinguishable global stop message.
        # These are observations, not permission to clear or reinterpret stops.
        source_stops = [{"worker_id": worker["id"], **deepcopy(stop)}
            for worker in manifest["workers"]
            if isinstance(stop := controls[worker["id"]].get("source_stop"), dict)]
        merged["source_stops"] = source_stops
        blocked = [c for c in active_controls if (until := timestamp(c.get("blocked_until"))) and until > now()]
        access = any(c.get("block_reason") == "access_restriction" for c in active_controls)
        # Multiple independent workers throttled in one generation is evidence
        # that the aggregate load needs review; do not simply multiply IPs.
        def throttled_worker(summary):
            pipeline = summary.get("pipeline_summary") or {}
            stop = pipeline.get("source_stop") or {}
            metadata_throttle = (isinstance(stop, dict) and stop.get("provider") == "scryfall"
                                 and stop.get("status_code") == 429 and stop.get("reason") == "rate_limit")
            return (pipeline.get("measured_request_rate") or {}).get("throttles", 0) > 0 or metadata_throttle
        throttled = [s for s in actual_summaries if throttled_worker(s)]
        systemic = access or len(throttled) >= 2
        distributed["systemic_block"] = systemic
        if systemic:
            merged["block_reason"] = "access_restriction" if access else "rate_limit"
            expiries = [timestamp(c.get("blocked_until")) for c in blocked]
            merged["blocked_until"] = (max(expiries) if expiries else now() + timedelta(hours=1)).isoformat()
            relevant_stops = [stop for stop in source_stops if stop.get("reason") == merged["block_reason"]]
            if relevant_stops:
                merged["source_stop"] = deepcopy(max(relevant_stops,
                    key=lambda stop: timestamp(stop.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc)))
        elif state.get("distributed"):
            merged.pop("blocked_until", None)
            merged.pop("block_reason", None)
            merged.pop("source_stop", None)
        rows = manifest["inventory"]
        published = {r["commander_slug"] for r in rows if (base / f"{r['commander_slug']}.json").exists()}
        published.update(name[:-5] for _, name, _ in pending_copies if "/" not in name and name.endswith(".json"))
        due = production_queue_plan(merged, rows, published, Path(temporary) / "due-plan")
        selected = set(due["selected"])
        planning_rows = [r for r in rows if not (merged["commanders"].get(r["commander_slug"], {}).get("status") == "ineligible"
                                                and r["commander_slug"] not in selected)]
        preview_remaining = due["capacity"]["remaining_previews"]
        ceiling = sum(source_rate(c.get("source_rate"))["learned_rate"] for c in active_controls)
        elapsed = ((max(w[1] for w in source_windows) - min(w[0] for w in source_windows)).total_seconds() if source_windows else 0)
        successes = sum(int(s.get("successful_responses") or 0) for s in stats_rows)
        started = sum(int(s.get("requests_started") or 0) for s in stats_rows)
        observed = successes / elapsed if elapsed > 0 else None
        capacity = plan_capacity(planning_rows, max_requests_per_second=40 * len(active_controls),
            effective_requests_per_second=ceiling, observed_requests_per_second=observed,
            remaining_previews=preview_remaining,
            remaining_overhead_requests=due["capacity"]["remaining_overhead_requests"], now=now())
        capacity.update(rate_scope="aggregate_independent_workers", independent_workers=len(active_controls),
            rate_limit_learned=True, observed_rate_at=now().isoformat())
        measured = {"requests_started": started, "successful_responses": successes,
            "failures": sum(int(s.get("failures") or 0) for s in stats_rows),
            "throttles": sum(int(s.get("throttles") or 0) for s in stats_rows),
            "observed_requests_per_second": observed, "maximum_requests_per_second": ceiling,
            "elapsed_seconds": elapsed, "blocked": systemic, "block_kind": merged.get("block_reason") if systemic else None}
        paused_workers = len(active_controls) if systemic else len(blocked)
        capacity["available_requests_per_second"] = (0 if systemic else
            sum(source_rate(c.get("source_rate"))["learned_rate"] for c in active_controls if c not in blocked))
        if paused_workers:
            capacity.update(estimated_minimum_seconds=None, earliest_possible_finish_at=None,
                measured_projection_seconds=None, projected_finish_at=None, projection_requests_per_second=None,
                status="worker_cooldown",
                estimate_note="Independent worker cooldowns are pending. A catalog-wide ETA is unavailable until the paused work can resume.")
        merged.setdefault("progress", {}).update(updated_at=now().isoformat(), capacity=capacity,
            measured_request_rate=measured, blocked=systemic, blocked_until=merged.get("blocked_until") if systemic else None,
            rate_control={"learned_rate": ceiling, "effective_requests_per_second": ceiling,
                "configured_requests_per_second": 40 * len(active_controls), "observed_requests_per_second": observed,
                "observed_rate_at": now().isoformat(), "status": "independent_workers",
                "rate_scope": "aggregate_independent_workers", "throttles": measured["throttles"]},
            distributed={"runners": manifest["runners"], "active_workers": len(active_controls) - paused_workers,
                "blocked_workers": paused_workers, "pilot": manifest["pilot"]})
        # Preserve the global one-runner source_rate. The aggregate cap/rate
        # belongs in progress only and can never seed a new runner's limiter.
        pending = due["remaining_in_batch"]
        resource_limits = {s["worker_id"]: s.get("pipeline_summary", {}).get("resource_limit")
            for s in actual_summaries if s.get("pipeline_summary", {}).get("resource_limit")}
        fallback = [s["worker_id"] for s in summaries if s["checkpoint"].get("used_fallback")]
        unsafe_failures = [s["worker_id"] for s in summaries if s.get("exit_code") not in {0, None}
            and not ((until := timestamp(s.get("source_control", {}).get("blocked_until"))) and until > now()
                     and s.get("source_control", {}).get("block_reason") in {"rate_limit", "access_restriction"})]
        summary = {"schema_version": 1, "snapshot_date": now().astimezone(ZoneInfo("America/Toronto")).date().isoformat(),
            "pipeline_version": 1, "distributed": {"generation": manifest["generation"], "runners": manifest["runners"],
                "pilot": manifest["pilot"], "systemic_block": systemic, "workers": summaries,
                "missing_workers": missing, "fallback_workers": fallback, "failed_workers": unsafe_failures},
            "scope": {"commanders": [], "max_commanders": 0, "sample_decks": None, "refresh": False},
            "automatic_continuation_allowed": not manifest["pilot"] and not fallback and not unsafe_failures and not resource_limits,
            "continuation_needed": bool((started or changed) and pending and not systemic and not fallback and not unsafe_failures and not resource_limits and not manifest["pilot"]),
            "publish_needed": bool(changed or started), "no_progress": not (started or changed),
            "completed": sorted(completed), "changed_reports": sorted(changed), "remaining_in_batch": pending,
            "resource_limit": resource_limits or None,
            "blocked": systemic, "blocked_until": merged.get("blocked_until") if systemic else None,
            "block_reason": merged.get("block_reason") if systemic else None,
            "source_stop": deepcopy(merged.get("source_stop")) if systemic else None,
            "source_stops": deepcopy(source_stops),
            "failures": [{"worker": s["worker_id"], "reason": s["checkpoint"].get("fallback_reason") or "Worker exited unsuccessfully"}
                for s in summaries if s["checkpoint"].get("used_fallback") or s.get("exit_code") not in {0, None}],
            "capacity": capacity, "measured_request_rate": measured,
            "new_previews_attempted": sum(int(s.get("pipeline_summary", {}).get("new_previews_attempted") or 0) for s in actual_summaries),
            "min_total_decks": 200, "no_fixed_completion_deadline": True,
            "model_policy": due["model_policy"], "tag_label_policy": due["tag_label_policy"]}
        sizes, growth, delta = {}, 0, 0
        for archive_path in {item[0] for item in pending_copies}:
            with tarfile.open(archive_path, "r:gz") as stream:
                sizes[archive_path] = {item.name: item.size for item in stream.getmembers()}
        for archive_path, name, target in pending_copies:
            old = target.stat().st_size if target.exists() else 0
            change = sizes[archive_path][name] - old
            growth += max(0, change)
            delta += change
        largest = max((sizes[a][name] for a, name, _ in pending_copies), default=0)
        free = shutil.disk_usage(base).free
        if free < growth + largest + MIN_DISK_RESERVE_BYTES:
            raise ValueError("Insufficient free disk to commit the distributed checkpoint while retaining the reserve")
        canonical_bytes = sum(path.stat().st_size for path in base.rglob("*") if path.is_file() and not path.is_symlink())
        storage = {"canonical_bytes_before": canonical_bytes, "canonical_bytes_after": canonical_bytes + delta,
            "staged_report_bytes": staged_report_bytes, "input_bytes_reclaimed": reclaimed,
            "result_archive_bytes": sum(a.stat().st_size for a in archives_in_use),
            "free_disk_bytes_before_commit": free, "reserve_bytes": MIN_DISK_RESERVE_BYTES}
        distributed["storage"] = storage
        summary["distributed"]["storage"] = storage
        merged["progress"]["resource_limit"] = resource_limits or None
        merged["progress"]["distributed"].update(canonical_bytes=canonical_bytes + delta, free_disk_bytes=free)
        capacity.update(canonical_bytes=canonical_bytes + delta, disk_free_bytes=free)
        # Parsed preview caches can dominate the dataset. Validate every
        # archive first, then stream members into atomic target files instead
        # of keeping another full extracted dataset beside the canonical one.
        by_archive = {}
        for archive, name, target in pending_copies:
            by_archive.setdefault(archive, []).append((name, target))
        for archive_path, copies in by_archive.items():
            with tarfile.open(archive_path, "r:gz") as archive:
                members = {member.name: member for member in archive.getmembers()}
                for name, target in copies:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary_target = target.with_suffix(target.suffix + ".tmp")
                    with archive.extractfile(members[name]) as source, temporary_target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 ** 2)
                    temporary_target.replace(target)
        atomic_json(state_path, merged)
        atomic_json(output_summary, summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--build-styles-dir", type=Path, required=True)
    p.add_argument("--commander-index", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--runners", type=int, required=True)
    p.add_argument("--generation", required=True)
    p.add_argument("--pilot", action="store_true")
    p = sub.add_parser("extract")
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--worker")
    p = sub.add_parser("checkpoint")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--build-styles-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--exit-code", type=int, default=0)
    p = sub.add_parser("merge")
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--output-summary", type=Path, required=True)
    p.add_argument("--generation")
    args = parser.parse_args()
    if args.command == "plan":
        result = plan(args.build_styles_dir, args.commander_index, args.output_dir, args.runners, args.generation, pilot=args.pilot)
    elif args.command == "extract":
        owned = None
        if bool(args.manifest) != (args.worker is not None):
            parser.error("--manifest and --worker must be supplied together")
        if args.manifest:
            manifest = load_manifest(args.manifest)
            worker = worker_record(manifest, args.worker)
            owned = set(worker["commanders"])
            if args.archive.name == worker["archive"] and digest(args.archive) != worker["archive_sha256"]:
                raise ValueError("Shard input archive hash mismatch")
        result = {"files": extract_archive(args.archive, args.destination, owned=owned,
            shared_inputs=bool(args.manifest and args.archive.name == worker["archive"]))}
        if args.manifest:
            validate_tree(args.destination, manifest, worker)
    elif args.command == "checkpoint":
        result = checkpoint(args.manifest, args.worker, args.build_styles_dir, args.output_dir, exit_code=args.exit_code)
    else:
        result = merge(args.base, args.manifest, args.results, args.inputs, args.output_summary, generation=args.generation)
    if args.command == "plan":
        result = {"manifest": str(args.output_dir / "manifest.json"), "generation": result["generation"],
            "runners": result["runners"], "pilot": result["pilot"],
            "workers": [{key: worker[key] for key in ("id", "archive", "commanders", "estimated_remaining_requests", "files")}
                        for worker in result["workers"]]}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
