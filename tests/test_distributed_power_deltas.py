"""Parallel power checks cannot replace a newer commander analysis."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile

import pytest

from edhrec_affinity.build_styles.external_validation import (
    RESTRICTION_CATEGORIES, record_external_validation, validation_record_key,
)
from edhrec_affinity.build_styles.official_checker import deck_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("distributed_power_deltas", ROOT / "scripts/distributed_power_deltas.py")
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def deck(card="Good Card"):
    return {"status": "generated", "cards": [{"name": "Commander", "quantity": 1},
        {"name": "Forest", "quantity": 98}, {"name": card, "quantity": 1}],
        "commanders": [{"name": "Commander", "quantity": 1}], "validation": {}}


def report(card="Good Card"):
    return {"commander_slug": "commander", "commander_name": "Commander", "generated_at": "2026-10-30T00:00:00Z",
        "deck_analysis": {"status": "fitted", "packages": [{"id": 0, "generated_deck": deck(card)}]}}


def observation(value):
    return {"source": "edhpowerlevel.com", "checked_at": datetime.now(timezone.utc).isoformat(),
        "card_count": 100, "playability_pct": 75, "power_level": 6.1, "bracket": 2,
        "source_url": deck_url(value), "bracket_restrictions": {key: [] for key in RESTRICTION_CATEGORIES},
        "restriction_check": {"status": "complete", "method": "edhpowerlevel_page",
            "source_script_sha256": "a" * 64, "commander_names": ["Commander"]}}


def fixture(tmp_path, *, original="Bad Card"):
    base, worker = tmp_path / "base", tmp_path / "worker"
    write(base / "commander.json", report(original))
    shutil.copytree(base, worker)
    manifest = tmp_path / "manifest.json"
    write(manifest, {"schema_version": 1, "generation": "12345",
        "base_report_sha256": {"commander": power.digest(base / "commander.json")}})
    snapshot = tmp_path / "snapshot.json"
    power.snapshot(worker, manifest, snapshot)
    return base, worker, manifest, snapshot


def export(tmp_path, worker, snapshot):
    results = tmp_path / "results"
    power.export_delta(worker, snapshot, results)
    return results / "power-delta.tar.gz"


def test_current_parent_accepts_repair_and_complete_exact_proof(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    repaired = report()
    value = repaired["deck_analysis"]["packages"][0]["generated_deck"]
    record_external_validation(value, observation(value), worker / "_collection/external-validations")
    write(worker / "commander.json", repaired)
    write(worker / "_collection/official-validation-queue.json", {"schema_version": 1,
        "check_attempts": {}, "repair_attempts": {"commander": {"attempts": 1}}})
    archive = export(tmp_path, worker, snapshot)
    result = power.apply_delta(base, manifest, archive, tmp_path / "applied.json")
    assert result["reports_applied"] == result["records_imported"] == 1
    current = json.loads((base / "commander.json").read_text())["deck_analysis"]["packages"][0]["generated_deck"]
    assert current["cards"] == value["cards"]
    assert current["validation"]["restriction_validation"]["status"] == "verified"
    assert not result["stale_proposals"]


def test_new_collection_wins_but_matching_current_hash_gets_official_proof(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    repaired = report()
    value = repaired["deck_analysis"]["packages"][0]["generated_deck"]
    record_external_validation(value, observation(value), worker / "_collection/external-validations")
    write(worker / "commander.json", repaired)
    write(worker / "_collection/official-validation-queue.json", {"schema_version": 1,
        "repair_attempts": {"commander": {"attempts": 2}, "other": {"attempts": 1}},
        "repair_retry_at": {"commander": "2099-01-01T00:00:00Z", "other": "2099-01-02T00:00:00Z"}})
    newer = report()
    newer.update(generated_at="2026-10-30T01:00:00Z", newly_collected=True)
    write(base / "commander.json", newer)
    result = power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    current = json.loads((base / "commander.json").read_text())
    assert current["newly_collected"] and current["generated_at"] == newer["generated_at"]
    assert current["deck_analysis"]["packages"][0]["generated_deck"]["validation"]["restriction_validation"]["status"] == "verified"
    assert result["stale_proposals"] == ["commander"] and result["reports_applied"] == 0
    queue = json.loads((base / "_collection/official-validation-queue.json").read_text())
    assert queue["repair_attempts"] == {"other": {"attempts": 1}}
    assert queue["repair_retry_at"] == {"other": "2099-01-02T00:00:00Z"}


def test_proof_for_replaced_list_cannot_verify_replacement(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    repaired = report()
    value = repaired["deck_analysis"]["packages"][0]["generated_deck"]
    record_external_validation(value, observation(value), worker / "_collection/external-validations")
    write(worker / "commander.json", repaired)
    write(base / "commander.json", report("Different Card"))
    result = power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    current = json.loads((base / "commander.json").read_text())["deck_analysis"]["packages"][0]["generated_deck"]
    assert current["cards"][-1]["name"] == "Different Card"
    assert current["validation"]["restriction_validation"]["status"] == "pending"
    assert result["stale_proposals"] == ["commander"]


def test_newer_existing_record_is_preserved(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path, original="Good Card")
    value = deck()
    earlier = observation(value)
    earlier["checked_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    record_external_validation(value, earlier, worker / "_collection/external-validations")
    later = observation(value)
    record_external_validation(value, later, base / "_collection/external-validations")
    result = power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    stored = json.loads((base / "_collection/external-validations" / f"{validation_record_key(value)}.json").read_text())
    assert stored["checked_at"] == later["checked_at"] and result["records_imported"] == 0


def test_wrong_generation_rejected_before_any_canonical_write(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    write(worker / "commander.json", report())
    archive = export(tmp_path, worker, snapshot)
    value = json.loads(manifest.read_text())
    value["generation"] = "different"
    write(manifest, value)
    before = (base / "commander.json").read_bytes()
    with pytest.raises(ValueError, match="different distributed generation"):
        power.apply_delta(base, manifest, archive, tmp_path / "applied.json")
    assert (base / "commander.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate"])
def test_unsafe_archives_are_rejected(tmp_path, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("../outside" if kind == "traversal" else "delta.json")
        if kind == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "/outside"
            bundle.addfile(member)
        else:
            member.size = 2
            bundle.addfile(member, io.BytesIO(b"{}"))
            if kind == "duplicate":
                bundle.addfile(member, io.BytesIO(b"{}"))
    with pytest.raises(ValueError, match="Unsafe or duplicate"):
        power.unpack_delta(archive, tmp_path / "staging")


def test_no_changes_export_contains_no_replacement_reports(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    result = power.export_delta(worker, snapshot, tmp_path / "results")
    assert result["proposed_reports"] == result["new_records"] == 0
    applied = power.apply_delta(base, manifest, tmp_path / "results/power-delta.tar.gz", tmp_path / "applied.json")
    assert applied["reports_applied"] == applied["records_imported"] == 0


def test_invalid_record_rejects_delta_before_applying_repair(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    repaired = report()
    value = repaired["deck_analysis"]["packages"][0]["generated_deck"]
    directory = worker / "_collection/external-validations"
    record_external_validation(value, observation(value), directory)
    record_path = directory / f"{validation_record_key(value)}.json"
    invalid = json.loads(record_path.read_text())
    invalid["restriction_check"]["commander_names"] = ["Different Commander"]
    write(record_path, invalid)
    write(worker / "commander.json", repaired)
    before = (base / "commander.json").read_bytes()
    with pytest.raises(ValueError):
        power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    assert (base / "commander.json").read_bytes() == before
    assert not (base / "_collection/external-validations").exists()


def test_metadata_merge_uses_latest_checked_identity_without_source_requests(tmp_path):
    from edhrec_affinity.build_styles import card_metadata as metadata
    earlier = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {"name": "Forest", "type_line": "Basic Land — Forest", "oracle_text": "Add green.",
        "color_identity": ["G"], "legalities": {"commander": "legal"}, "keywords": [],
        "cmc": 0, "layout": "normal", "game_changer": False, "produced_mana": ["G"],
        "oracle_id": "oracle-forest"}
    old = metadata._metadata(payload, earlier.isoformat())
    new = metadata._metadata({**payload, "oracle_text": "({T}: Add {G}.)"}, datetime.now(timezone.utc).isoformat())
    base, incoming, output = tmp_path / "base.json", tmp_path / "inputs", tmp_path / "merged.json"
    metadata._write_cache(base, {"forest": old})
    metadata._write_cache(incoming / "worker-1-metadata.json", {"forest": new})
    metadata._write_cache(incoming / "worker-2-metadata.json", {"forest": old})
    result = power.merge_metadata(base, incoming, output)
    assert result["input_caches"] == 2 and result["cards"] == 1
    assert metadata._read_cache(output)["forest"]["checked_at"] == new["checked_at"]
    assert metadata._read_cache(output)["forest"]["oracle_text"] == new["oracle_text"]


def test_conflicting_metadata_identity_never_replaces_canonical_cache(tmp_path):
    from edhrec_affinity.build_styles import card_metadata as metadata
    payload = {"name": "Forest", "type_line": "Basic Land — Forest", "oracle_text": "Add green.",
        "color_identity": ["G"], "legalities": {"commander": "legal"}, "keywords": [],
        "cmc": 0, "layout": "normal", "game_changer": False, "produced_mana": ["G"], "oracle_id": "one"}
    first = metadata._metadata(payload, datetime.now(timezone.utc).isoformat())
    second = metadata._metadata({**payload, "oracle_id": "two"}, datetime.now(timezone.utc).isoformat())
    base, incoming = tmp_path / "metadata.json", tmp_path / "inputs"
    metadata._write_cache(base, {"forest": first})
    metadata._write_cache(incoming / "worker-metadata.json", {"forest": second})
    before = base.read_bytes()
    with pytest.raises(ValueError, match="disagree"):
        power.merge_metadata(base, incoming, base)
    assert base.read_bytes() == before


def test_spawned_analysis_uses_its_own_saved_official_observation_directory(tmp_path, monkeypatch):
    import gzip
    from edhrec_affinity.build_styles import __main__ as prototype
    from edhrec_affinity.build_styles.external_validation import attach_external_validation
    from edhrec_affinity.build_styles.pipeline import analyze_saved_evidence
    proof = tmp_path / "proofs"
    value = deck()
    record_external_validation(value, observation(value), proof)
    evidence, output = tmp_path / "evidence.json.gz", tmp_path / "analysis"
    output.mkdir()
    with gzip.open(evidence, "wt") as handle:
        json.dump({"analysis_version": "version"}, handle)
    def analyze(options, evidence):
        checked_deck = deck()
        assert attach_external_validation(checked_deck)["status"] == "verified"
        return {"commander_slug": "commander", "deck_analysis": {"status": "fitted"}}
    monkeypatch.setattr(prototype, "analyze_evidence", analyze)
    result = analyze_saved_evidence(evidence, {"output_dir": output, "validation_directory": proof})
    assert result["status"] == "fitted"


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def repaired_delta(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    repaired = report()
    value = repaired["deck_analysis"]["packages"][0]["generated_deck"]
    record_external_validation(value, observation(value), worker / "_collection/external-validations")
    write(worker / "commander.json", repaired)
    write(worker / "_collection/official-validation-queue.json", {"schema_version": 1,
        "check_attempts": {}, "repair_attempts": {"commander": {"attempts": 1}}})
    return base, worker, manifest, snapshot


def test_malformed_control_is_rejected_without_any_canonical_changes(tmp_path):
    base, worker, manifest, snapshot = repaired_delta(tmp_path)
    write(worker / "_collection/official-validation-queue.json", {"schema_version": 1, "repair_attempts": []})
    before = tree_bytes(base)
    with pytest.raises(ValueError, match="queue repair_attempts"):
        power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    assert tree_bytes(base) == before


def test_malformed_replacement_report_is_rejected_without_canonical_changes(tmp_path):
    base, worker, manifest, snapshot = fixture(tmp_path)
    malformed = report()
    malformed["deck_analysis"] = []
    write(worker / "commander.json", malformed)
    before = tree_bytes(base)
    with pytest.raises(ValueError, match="analysis"):
        power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    assert tree_bytes(base) == before


def test_late_attachment_failure_cannot_leak_staged_reports_or_records(tmp_path, monkeypatch):
    base, worker, manifest, snapshot = repaired_delta(tmp_path)
    other = report()
    other["commander_slug"] = "other"
    write(base / "other.json", other)
    before, calls = tree_bytes(base), []
    attach = power.attach_report

    def fail_after_one(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("Injected later attachment failure")
        return attach(path, *args, **kwargs)

    monkeypatch.setattr(power, "attach_report", fail_after_one)
    with pytest.raises(OSError, match="later attachment"):
        power.apply_delta(base, manifest, export(tmp_path, worker, snapshot), tmp_path / "applied.json")
    assert len(calls) == 2
    assert tree_bytes(base) == before


@pytest.mark.parametrize("failed_publish", [2, 3])
def test_publish_io_failure_rolls_back_reports_new_records_and_controls(tmp_path, monkeypatch, failed_publish):
    base, worker, manifest, snapshot = repaired_delta(tmp_path)
    archive = export(tmp_path, worker, snapshot)
    before = tree_bytes(base)
    replace, count = power.os.replace, [0]

    def fail_once(source, destination):
        if Path(destination).is_relative_to(base):
            count[0] += 1
            if count[0] == failed_publish:
                raise OSError("Injected canonical publish failure")
        return replace(source, destination)

    monkeypatch.setattr(power.os, "replace", fail_once)
    with pytest.raises(OSError, match="canonical publish"):
        power.apply_delta(base, manifest, archive, tmp_path / "applied.json")
    assert count[0] >= failed_publish
    assert tree_bytes(base) == before
    assert not (tmp_path / "applied.json").exists()


def test_new_canonical_source_during_staging_wins_before_any_publish(tmp_path, monkeypatch):
    base, worker, manifest, snapshot = repaired_delta(tmp_path)
    archive = export(tmp_path, worker, snapshot)
    original = power._apply_delta_view
    newer = report("Independent Source Card")
    newer["newly_collected"] = True

    def update_source_after_staging(*args, **kwargs):
        result = original(*args, **kwargs)
        write(base / "commander.json", newer)
        return result

    monkeypatch.setattr(power, "_apply_delta_view", update_source_after_staging)
    with pytest.raises(ValueError, match="changed before publication"):
        power.apply_delta(base, manifest, archive, tmp_path / "applied.json")
    assert json.loads((base / "commander.json").read_text()) == newer
    assert not (base / "_collection/external-validations").exists()


def test_rollback_preserves_a_later_independent_writer(tmp_path, monkeypatch):
    base, worker, manifest, snapshot = repaired_delta(tmp_path)
    archive = export(tmp_path, worker, snapshot)
    replace, count = power.os.replace, [0]
    newer = report("Newer Concurrent Source Card")

    def source_changes_before_failure(source, destination):
        if Path(destination).is_relative_to(base):
            count[0] += 1
            if count[0] == 2:
                write(base / "commander.json", newer)
                raise OSError("Injected second publish failure")
        return replace(source, destination)

    monkeypatch.setattr(power.os, "replace", source_changes_before_failure)
    with pytest.raises(OSError, match="second publish"):
        power.apply_delta(base, manifest, archive, tmp_path / "applied.json")
    assert json.loads((base / "commander.json").read_text()) == newer


@pytest.mark.parametrize("aliases_in_base", [True, False])
def test_metadata_alias_proofs_survive_both_observation_date_orders(tmp_path, aliases_in_base):
    from edhrec_affinity.build_styles import card_metadata as metadata
    now = datetime.now(timezone.utc)
    payload = {"name": "Counterspell", "type_line": "Instant", "oracle_text": "Counter target spell.",
        "color_identity": ["U"], "legalities": {"commander": "legal"}, "keywords": [],
        "cmc": 2, "layout": "normal", "game_changer": False, "produced_mana": [], "oracle_id": "oracle-counterspell"}
    proof = {"name": "Secret Lair Alternate Name", "source_field": "flavor_name", "printing_id": "alternate-printing",
        "lang": "en", "oracle_id": "oracle-counterspell", "canonical_name": "Counterspell"}
    older = metadata._metadata({**payload, "name_aliases": [proof]}, (now - timedelta(hours=1)).isoformat())
    newer = metadata._metadata({**payload, "oracle_text": "Latest observed card text."}, now.isoformat())
    base, incoming, output = tmp_path / "metadata.json", tmp_path / "inputs", tmp_path / "result.json"
    metadata._write_cache(base, {"counterspell": older if aliases_in_base else newer})
    metadata._write_cache(incoming / "worker-metadata.json", {"counterspell": newer if aliases_in_base else older})
    result = power.merge_metadata(base, incoming, output)
    merged = metadata._read_cache(output)["counterspell"]
    assert merged["checked_at"] == newer["checked_at"] and merged["oracle_text"] == newer["oracle_text"]
    assert merged["name_aliases"] == [proof]
    assert result["observations_imported"] == 1
    assert metadata.card_key(proof["name"]) in metadata.metadata_aliases(merged)
