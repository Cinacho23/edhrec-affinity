#!/usr/bin/env python3
"""Read one owned checkpoint on GitHub; no EDHREC or production mutations."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from coordinate_build_styles import GitHub
from distributed_build_styles import production_queue_plan, load_manifest

SLUG = "the-tenth-doctor-rose-tyler"


def download(repository, run_id, name, output):
    result = subprocess.run(["gh", "run", "download", str(run_id), "--repo", repository,
        "--name", name, "--dir", str(output)], capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError("Could not read the selected GitHub checkpoint artifact")


def inspect(client, root):
    latest = next(client.snapshots(), None)
    if latest is None or not latest.durable:
        raise ValueError("No durable collector summary available")
    # An active generation's immutable inputs are exactly the latest saved
    # state its workers resumed. No in-progress source files are observable.
    candidates = [run for run in client.runs() if client.trusted(run)
                  and run.get("status") == "in_progress"]
    for run in candidates:
        available = client.api(f"{client.root}/actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
        names = {item["name"] for item in available if item.get("expired") is False}
        if f"distributed-shared-{run['id']}" in names:
            break
    else:
        raise ValueError("No active distributed generation with immutable input artifacts")
    run_id = run["id"]
    shared = root / "shared"
    download(client.repository, run_id, f"distributed-shared-{run_id}", shared)
    manifest = load_manifest(shared / "manifest.json")
    if str(manifest["generation"]) != str(run_id):
        raise ValueError("Manifest identity does not match this generation")
    owner = next(worker for worker in manifest["workers"] if SLUG in worker["commanders"])
    own = root / "owned"
    name = f"distributed-input-{run_id}-{owner['id']}"
    if name not in names:
        raise ValueError("Selected worker's immutable input is unavailable")
    download(client.repository, run_id, name, own)
    with tarfile.open(own / owner["archive"], "r:gz") as archive:
        member = archive.getmember("_collection/state.json")
        if not member.isfile() or member.size > 16 * 1024 * 1024:
            raise ValueError("Invalid checkpoint state member")
        state = json.load(archive.extractfile(member))
    rows = [row for row in manifest["inventory"] if row["commander_slug"] in owner["commanders"]]
    published = {slug for slug in owner["commanders"] if manifest["base_report_sha256"].get(slug)}
    plan = production_queue_plan(state, rows, published, root / "dry-plan")
    queue = plan["selected"]
    position = queue.index(SLUG) if SLUG in queue else None
    selected = queue[:position + 1] if position is not None else []
    records = state.get("commanders", {})
    inventory = {row["commander_slug"]: row for row in rows}
    def record(slug):
        saved = records.get(slug, {})
        frame = saved.get("frame_decks") or inventory[slug].get("total_decks") or 0
        loaded = saved.get("current_previews") or 0
        return {"commander_slug": slug, "total_decks": inventory[slug].get("total_decks"),
            "frame_decks": frame, "current_previews": loaded, "remaining_previews": max(0, frame - loaded),
            **{key: saved.get(key) for key in ("status", "coverage_complete", "last_collection_at",
                "analysis_version", "unavailable_previews", "verified_at")}}
    return {"commander_slug": SLUG, "input_generation": run_id, "input_created_at": manifest["created_at"],
        "canonical_summary_run_id": latest.run_id, "worker_id": owner["id"], "saved": record(SLUG),
        "published_report_existed": SLUG in published, "position_in_priority_queue": position,
        "queue_through_target": [record(slug) for slug in selected],
        "active_local_claims": state.get("pipeline", {}).get("collection_workers", {}),
        "source_rate": state.get("source_rate"),
        "limitations": "Immutable batch input, not live progress; both local workers share the VM request budget."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="commander-checkpoint-") as temporary:
        result = inspect(GitHub(os.environ["GITHUB_REPOSITORY"]), Path(temporary))
    text = json.dumps(result, indent=2, allow_nan=False)
    (args.output / "progress.json").write_text(text + "\n")
    print(text, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write("## Doctor and Rose saved collection progress\n\n```json\n" + text + "\n```\n")


if __name__ == "__main__":
    main()
