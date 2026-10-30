#!/usr/bin/env python3
"""Exchange immutable power observations without overwriting newer collections."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from edhrec_affinity.build_styles.external_validation import (
    _check_record, _preload_cards, cardlist_hash, record_external_validation, validation_record_key,
)
from edhrec_affinity.build_styles.sources import card_key, checked_identifier
from rebuild_generated_decks import atomic_json, packages, read_json
from validate_generated_decks import attach_report


MAX_DELTA_BYTES = 5 * 1024 ** 3
RECORD_NAME = re.compile(r"[a-f0-9]{64}\.[a-f0-9]{64}\.json")
CONTROLS = ("official-validation-queue.json", "official-validation-summary.json")


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Expected a dated observation")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Observation timestamp has no timezone")
    return result


def manifest_value(path):
    value = read_json(path)
    if (value.get("schema_version") != 1 or not isinstance(value.get("generation"), str)
            or not isinstance(value.get("base_report_sha256"), dict)):
        raise ValueError("Invalid distributed generation manifest")
    return value


def snapshot(root, manifest_path, output):
    manifest = manifest_value(manifest_path)
    records = root / "_collection/external-validations"
    value = {"schema_version": 1, "generation": manifest["generation"],
        "base_report_sha256": manifest["base_report_sha256"],
        "records": {path.name: digest(path) for path in records.glob("*.json") if path.is_file()}}
    atomic_json(output, value)
    return value


def export_delta(root, snapshot_path, output, *, summary_path=None):
    before = read_json(snapshot_path)
    if before.get("schema_version") != 1 or not isinstance(before.get("base_report_sha256"), dict):
        raise ValueError("Invalid power snapshot")
    output.mkdir(parents=True, exist_ok=True)
    proposals, records, paths = {}, {}, {}
    for slug, baseline in before["base_report_sha256"].items():
        checked_identifier(slug)
        path = root / f"{slug}.json"
        if path.is_file() and digest(path) != baseline:
            content_hash = digest(path)
            proposals[slug] = {"base_sha256": baseline, "sha256": content_hash}
            paths[f"reports/{slug}.json"] = path
    for path in sorted((root / "_collection/external-validations").glob("*.json")):
        if path.is_file() and RECORD_NAME.fullmatch(path.name) and digest(path) != before.get("records", {}).get(path.name):
            records[path.name] = digest(path)
            paths[f"records/{path.name}"] = path
    controls = {}
    for name in CONTROLS:
        path = summary_path if name == CONTROLS[1] and summary_path is not None else root / "_collection" / name
        if path.is_file():
            controls[name] = digest(path)
            paths[f"control/{name}"] = path
    delta = {"schema_version": 1, "generation": before["generation"],
             "proposals": proposals, "records": records, "controls": controls}
    with tempfile.TemporaryDirectory(prefix="power-delta-", dir=output) as temporary:
        description = Path(temporary) / "delta.json"
        atomic_json(description, delta)
        archive = output / "power-delta.tar.gz"
        with tarfile.open(archive.with_suffix(".tmp"), "w:gz", compresslevel=1) as bundle:
            bundle.add(description, arcname="delta.json", recursive=False)
            for name, path in sorted(paths.items()):
                if path.is_symlink():
                    raise ValueError("Power delta cannot contain symbolic links")
                bundle.add(path, arcname=name, recursive=False)
        archive.with_suffix(".tmp").replace(archive)
    summary = {"schema_version": 1, "generation": before["generation"],
               "proposed_reports": len(proposals), "new_records": len(records),
               "archive_sha256": digest(archive)}
    atomic_json(output / "power-delta-summary.json", summary)
    return summary


def unpack_delta(archive, destination):
    with tarfile.open(archive, "r:gz") as bundle:
        names, total = set(), 0
        for member in bundle.getmembers():
            relative = PurePosixPath(member.name)
            if (member.name in names or relative.is_absolute() or ".." in relative.parts
                    or not member.isfile() or member.size < 0):
                raise ValueError("Unsafe or duplicate power delta path")
            total += member.size
            if total > MAX_DELTA_BYTES:
                raise ValueError("Power delta exceeds the storage bound")
            names.add(member.name)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("wb") as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(block)
    return names


def checked_record(path):
    record = read_json(path)
    cards, commanders = _preload_cards(record.get("source_url", ""))
    deck = {"cards": cards, "commanders": [{"name": name} for name in commanders]}
    if path.name != f"{validation_record_key(deck)}.json":
        raise ValueError("Power record filename does not match its exact deck and roles")
    checked = _check_record(record, cardlist_hash(cards), sum(card["quantity"] for card in cards),
                            {card_key(card["name"]) for card in cards}, commanders)
    if checked.get("restriction_check", {}).get("status") != "complete":
        raise ValueError("Distributed power result has no complete official evidence")
    return deck, record, checked


def checked_report(value, slug):
    """Validate every structure consumed by exact-list proof attachment."""
    if not isinstance(value, dict) or value.get("commander_slug") != slug:
        raise ValueError("Power report commander identity is invalid")
    checked_identifier(slug)
    analysis = value.get("deck_analysis", {})
    if not isinstance(analysis, dict):
        raise ValueError("Power report analysis is invalid")
    alternatives = analysis.get("alternative_models", [])
    if not isinstance(alternatives, list) or any(not isinstance(model, dict) for model in alternatives):
        raise ValueError("Power report alternatives are invalid")
    for model in [analysis, *alternatives]:
        rows = model.get("packages", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Power report packages are invalid")
        for package in rows:
            deck = package.get("generated_deck", {})
            if not isinstance(deck, dict) or not isinstance(deck.get("validation", {}), dict):
                raise ValueError("Power report generated deck is invalid")
            if deck.get("status") == "generated":
                validation_record_key(deck)
    return value


def checked_control(value, name):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Invalid power queue state")
    if name == CONTROLS[0]:
        for field in ("check_attempts", "check_retry_at", "repair_attempts", "repair_retry_at", "worker_claims"):
            if not isinstance(value.get(field, {}), dict):
                raise ValueError(f"Invalid power queue {field}")
        for key, count in value.get("check_attempts", {}).items():
            if not RECORD_NAME.fullmatch(f"{key}.json") or type(count) is not int or count < 0:
                raise ValueError("Invalid power check attempts")
        for field in ("check_retry_at", "repair_retry_at"):
            for key, when in value.get(field, {}).items():
                if field == "repair_retry_at":
                    checked_identifier(key)
                elif not RECORD_NAME.fullmatch(f"{key}.json"):
                    raise ValueError("Invalid power retry deck identity")
                timestamp(when)
        for slug, entry in value.get("repair_attempts", {}).items():
            checked_identifier(slug)
            if not isinstance(entry, dict) or type(entry.get("attempts", 0)) is not int or entry.get("attempts", 0) < 0:
                raise ValueError("Invalid power repair attempts")
            for field in ("started_at", "checked_at"):
                if entry.get(field) is not None:
                    timestamp(entry[field])
        for owner, slug in value.get("worker_claims", {}).items():
            if not isinstance(owner, str) or not owner.isdigit():
                raise ValueError("Invalid power worker claim")
            checked_identifier(slug)
        ordering = value.get("family_order", [])
        if not isinstance(ordering, list):
            raise ValueError("Invalid power family ordering")
        for slug in ordering:
            checked_identifier(slug)
        for field in ("source_retry_at", "session_retry_at", "source_blocked_at", "updated_at"):
            if value.get(field) is not None:
                timestamp(value[field])
    else:
        for field in ("checked", "records_written", "repaired_reports", "changed_reports", "pending", "verified", "conflicts"):
            if field in value and (type(value[field]) is not int or value[field] < 0):
                raise ValueError("Invalid power summary count")
        for field in ("errors", "invalid", "unresolved", "commander_claims"):
            if not isinstance(value.get(field, []), list):
                raise ValueError("Invalid power summary entries")
    if "source_blocked" in value and not isinstance(value["source_blocked"], bool):
        raise ValueError("Invalid power source state")
    return value


def _view_paths(root):
    yield from sorted(path for path in root.glob("*.json") if path.is_file())
    yield from sorted(path for path in (root / "_collection/external-validations").glob("*.json") if path.is_file())
    for name in CONTROLS:
        path = root / "_collection" / name
        if path.is_file():
            yield path


def _stage_view(root, view):
    """Hardlinks are safe because every mutation uses an atomic replacement."""
    baselines = {}
    for path in _view_paths(root):
        if path.is_symlink():
            raise ValueError("Canonical power files cannot be symbolic links")
        relative = path.relative_to(root)
        target = view / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        baseline = digest(path)
        os.link(path, target)
        if digest(target) != baseline:
            raise ValueError("Canonical power file changed while staging")
        baselines[relative] = baseline
        if relative.parent == Path(".") and relative.stem not in {"index", "build_style_batch"}:
            checked_report(read_json(target), relative.stem)
        elif relative.name in CONTROLS:
            checked_control(read_json(target), relative.name)
    return baselines


def _publish_view(root, view, baselines, backup):
    changes = []
    for path in _view_paths(view):
        relative = path.relative_to(view)
        current = digest(path)
        if current != baselines.get(relative):
            changes.append((relative, current))
    # Guard every touched destination before the first publish, then again
    # immediately before its replacement. A newer canonical writer always wins.
    for relative, _ in changes:
        path = root / relative
        if (digest(path) if path.is_file() else None) != baselines.get(relative):
            raise ValueError("Canonical power file changed before publication")
    published = []
    try:
        for relative, current in changes:
            destination = root / relative
            if (digest(destination) if destination.is_file() else None) != baselines.get(relative):
                raise ValueError("Canonical power file changed during publication")
            saved = backup / relative
            if destination.exists():
                saved.parent.mkdir(parents=True, exist_ok=True)
                os.link(destination, saved)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(view / relative, destination)
            published.append((relative, current, saved if saved.exists() else None))
    except Exception:
        for relative, published_hash, saved in reversed(published):
            destination = root / relative
            if not destination.is_file() or digest(destination) != published_hash:
                continue  # Preserve a later independent writer during rollback.
            if saved is None:
                destination.unlink()
            else:
                os.replace(saved, destination)
        raise
    return len(changes)


def apply_delta(root, manifest_path, archive, output_summary):
    """Validate and attach in an isolated view, then publish with rollback."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="power-view-", dir=root.parent) as temporary:
        temporary = Path(temporary)
        view = temporary / "view"
        view.mkdir()
        baselines = _stage_view(root, view)
        summary = _apply_delta_view(view, manifest_path, archive)
        summary["published_files"] = _publish_view(root, view, baselines, temporary / "backup")
    atomic_json(output_summary, summary)
    return summary


def _apply_delta_view(root, manifest_path, archive, output_summary=None):
    manifest = manifest_value(manifest_path)
    summary = {"schema_version": 1, "generation": manifest["generation"], "records_imported": 0,
        "reports_applied": 0, "stale_proposals": [], "reports_attached": 0, "errors": []}
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="power-merge-", dir=root.parent) as temporary:
        staging = Path(temporary)
        names = unpack_delta(archive, staging)
        delta = read_json(staging / "delta.json")
        if delta.get("schema_version") != 1 or delta.get("generation") != manifest["generation"]:
            raise ValueError("Power delta belongs to a different distributed generation")
        proposals, records, controls = delta.get("proposals"), delta.get("records"), delta.get("controls")
        if not all(isinstance(value, dict) for value in (proposals, records, controls)):
            raise ValueError("Invalid power delta inventory")
        expected = {"delta.json"}
        for slug, proposal in proposals.items():
            checked_identifier(slug)
            if proposal.get("base_sha256") != manifest["base_report_sha256"].get(slug):
                raise ValueError("Power proposal has a different parent report")
            path = staging / "reports" / f"{slug}.json"
            value = read_json(path)
            checked_report(value, slug)
            if value.get("commander_slug") != slug or digest(path) != proposal.get("sha256"):
                raise ValueError("Power proposal content or identity mismatch")
            expected.add(f"reports/{slug}.json")
        # Validate the whole inventory before changing the canonical directory.
        for name, expected_hash in records.items():
            if not RECORD_NAME.fullmatch(name):
                raise ValueError("Invalid exact-list record name")
            path = staging / "records" / name
            if digest(path) != expected_hash:
                raise ValueError("Power observation content hash mismatch")
            checked_record(path)
            expected.add(f"records/{name}")
        for name, expected_hash in controls.items():
            if name not in CONTROLS or digest(staging / "control" / name) != expected_hash:
                raise ValueError("Power control file mismatch")
            value = read_json(staging / "control" / name)
            checked_control(value, name)
            expected.add(f"control/{name}")
        if names != expected:
            raise ValueError("Power archive contains undeclared files")
        directory = root / "_collection/external-validations"
        for name in sorted(records):
            deck, record, checked = checked_record(staging / "records" / name)
            existing = directory / name
            if existing.exists():
                try:
                    if timestamp(read_json(existing).get("checked_at")) >= timestamp(checked["checked_at"]):
                        continue
                except (ValueError, KeyError, TypeError):
                    pass  # A complete checked record can replace an unreadable cache entry.
            record_external_validation(deck, record, directory)
            summary["records_imported"] += 1
        for slug, proposal in proposals.items():
            destination = root / f"{slug}.json"
            current_hash = digest(destination) if destination.is_file() else None
            if current_hash == proposal["sha256"]:
                continue  # Safe reapplication after a later publication retry.
            if current_hash != proposal["base_sha256"]:
                summary["stale_proposals"].append(slug)
                continue
            value = read_json(staging / "reports" / f"{slug}.json")
            atomic_json(destination, value)
            summary["reports_applied"] += 1
        for name in controls:
            value = read_json(staging / "control" / name)
            if name == CONTROLS[0]:
                # A replacement source report starts a new repair lineage;
                # failed proposals must not consume its bounded repair budget.
                for slug in summary["stale_proposals"]:
                    for field in ("repair_attempts", "repair_retry_at"):
                        value.get(field, {}).pop(slug, None)
            atomic_json(root / "_collection" / name, value)
        # Proof attaches by exact current list+roles, including a source report
        # that changed while this job checked an older version of its family.
        for path in sorted(root.glob("*.json")):
            if path.stem in {"index", "build_style_batch"}:
                continue
            report = read_json(path)
            checked_report(report, path.stem)
            if report.get("commander_slug") == path.stem and attach_report(path, directory):
                summary["reports_attached"] += 1
    if output_summary is not None:
        atomic_json(output_summary, summary)
    return summary


def merge_metadata(base, inputs, output):
    """Keep latest card fields and all verified aliases for the same identity."""
    from edhrec_affinity.build_styles.card_metadata import _merge_cards, _read_cache, _write_cache
    merged = _read_cache(base) if base.is_file() else {}
    imported = 0
    candidates = sorted(path for path in inputs.rglob("*.json")
                        if "metadata" in path.name and path.resolve() not in {base.resolve(), output.resolve()})
    for path in candidates:
        for key, card in _read_cache(path).items():
            current = merged.get(key)
            if current is not None and (current.get("oracle_id") != card.get("oracle_id")
                                        or current.get("name") != card.get("name")):
                raise ValueError("Independent metadata caches disagree about a card identity")
            if current is None or timestamp(card["checked_at"]) > timestamp(current["checked_at"]):
                _merge_cards(merged, {key: deepcopy(card)})
            else:
                # The older observation can still carry an independently
                # verified Secret Lair printing alias the newer one lacks.
                combined = {key: deepcopy(card)}
                _merge_cards(combined, {key: deepcopy(current)})
                merged[key] = combined[key]
            imported += int(current != merged[key])
    if merged:
        _write_cache(output, merged)
    return {"cards": len(merged), "observations_imported": imported, "input_caches": len(candidates)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot")
    snap.add_argument("--build-styles-dir", required=True, type=Path)
    snap.add_argument("--manifest", required=True, type=Path)
    snap.add_argument("--output", required=True, type=Path)
    export = commands.add_parser("export")
    export.add_argument("--build-styles-dir", required=True, type=Path)
    export.add_argument("--snapshot", required=True, type=Path)
    export.add_argument("--output-dir", required=True, type=Path)
    export.add_argument("--summary", type=Path)
    apply = commands.add_parser("apply")
    apply.add_argument("--build-styles-dir", required=True, type=Path)
    apply.add_argument("--manifest", required=True, type=Path)
    apply.add_argument("--archive", required=True, type=Path)
    apply.add_argument("--output-summary", required=True, type=Path)
    metadata = commands.add_parser("merge-metadata")
    metadata.add_argument("--base", required=True, type=Path)
    metadata.add_argument("--inputs", required=True, type=Path)
    metadata.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "snapshot":
        result = snapshot(args.build_styles_dir, args.manifest, args.output)
        print(json.dumps({"generation": result["generation"], "reports": len(result["base_report_sha256"])}))
    elif args.command == "export":
        print(json.dumps(export_delta(args.build_styles_dir, args.snapshot, args.output_dir, summary_path=args.summary)))
    elif args.command == "apply":
        print(json.dumps(apply_delta(args.build_styles_dir, args.manifest, args.archive, args.output_summary)))
    else:
        print(json.dumps(merge_metadata(args.base, args.inputs, args.output)))


if __name__ == "__main__":
    main()
