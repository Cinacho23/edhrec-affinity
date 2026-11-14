"""Checkpoint inspection reads immutable GitHub files without source access."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("commander_inspection", ROOT / "scripts/inspect_commander_collection.py")
inspect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect)


def test_exact_owned_input_reports_partial_target_and_preceding_work(tmp_path, monkeypatch):
    slug = inspect.SLUG
    generation = 123456
    owner = {"id": "1", "archive": "shard-1.tar.gz", "commanders": [slug, "muerra-trash-tactician"]}
    rows = [{"commander_slug": slug, "total_decks": 5627},
            {"commander_slug": "muerra-trash-tactician", "total_decks": 5499}]
    manifest = {"generation": str(generation), "created_at": "2026-11-14T12:00:00Z",
                "workers": [owner], "inventory": rows, "base_report_sha256": {slug: None}}
    state = {"commanders": {slug: {"current_previews": 800, "status": "collecting"},
                "muerra-trash-tactician": {"current_previews": 5300, "status": "collecting"}},
                "pipeline": {"collection_workers": {"0": "muerra-trash-tactician"}},
                "source_rate": {"learned_rate": 1}}
    names = [f"distributed-shared-{generation}", f"distributed-input-{generation}-1"]
    calls = []
    def download(repository, run_id, name, output):
        calls.append((repository, run_id, name))
        output.mkdir(parents=True)
        if name.startswith("distributed-shared"):
            (output / "manifest.json").write_text(json.dumps(manifest))
        else:
            raw = json.dumps(state).encode()
            with tarfile.open(output / owner["archive"], "w:gz") as archive:
                member = tarfile.TarInfo("_collection/state.json")
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
    def plan(saved, inventory, published, root):
        assert saved == state and inventory == rows and published == set()
        return {"selected": ["muerra-trash-tactician", slug]}
    monkeypatch.setattr(inspect, "download", download)
    monkeypatch.setattr(inspect, "load_manifest", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr(inspect, "production_queue_plan", plan)
    client = SimpleNamespace(repository="owner/repo", root="repos/owner/repo",
        snapshots=lambda: iter([SimpleNamespace(run_id=123455, durable=True)]),
        runs=lambda: [{"id": generation, "status": "in_progress"}], trusted=lambda run: True,
        api=lambda endpoint: {"artifacts": [{"name": name, "expired": False} for name in names]})
    result = inspect.inspect(client, tmp_path)
    assert result["worker_id"] == "1" and result["input_generation"] == generation
    assert result["saved"]["remaining_previews"] == 4827
    assert result["queue_through_target"][0]["remaining_previews"] == 199
    assert result["position_in_priority_queue"] == 1
    assert len(calls) == 2 and all(call[0] == "owner/repo" for call in calls)
