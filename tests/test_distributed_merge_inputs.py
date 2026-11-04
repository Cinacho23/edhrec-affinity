import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/distributed_merge_inputs.py"
spec = importlib.util.spec_from_file_location("distributed_merge_inputs", SCRIPT)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def artifact(name, size=100, *, expired=False):
    return {"name": name, "size_in_bytes": size, "expired": expired}


def fixture():
    manifest = {"schema_version": 1, "generation": "100", "runners": 2, "pilot": False,
                "workers": [{"id": "0"}, {"id": "1"}]}
    artifacts = [artifact(f"distributed-{kind}-100-{worker}") for kind in ["input", "result"] for worker in range(2)]
    artifacts += [artifact("distributed-base-100"), artifact("distributed-shared-100")]
    return manifest, artifacts


class Client:
    def __init__(self, manifest, artifacts):
        self.manifest, self.saved = manifest, artifacts
        self.downloads = []
        self.source = {"id": 100, "path": core.WORKFLOW, "head_branch": "main", "event": "workflow_dispatch",
                       "status": "completed", "conclusion": "failure"}
        self.newer = []
        self.newer_artifacts = []

    def artifacts(self, source):
        return self.saved if str(source) == "100" else self.newer_artifacts

    def download(self, source, name, destination):
        self.downloads.append((str(source), name))
        (destination / "manifest.json").write_text(json.dumps(self.manifest))

    def api(self, endpoint):
        if endpoint == "":
            return {"default_branch": "main"}
        if endpoint == "actions/runs/100":
            return self.source
        if endpoint == "actions/runs/100/jobs?per_page=100":
            return {"jobs": [{"name": "Restore one canonical checkpoint and partition owned work", "conclusion": "success"}]}
        if endpoint.startswith("actions/workflows/"):
            return {"workflow_runs": self.newer}
        raise AssertionError(endpoint)


def test_complete_workers_do_not_download_any_duplicate_original_inputs():
    manifest, artifacts = fixture()
    chosen = core.merge_artifact_plan(manifest, artifacts, "100")
    assert [row["name"] for row in chosen] == ["distributed-result-100-0", "distributed-result-100-1"]


def test_only_missing_or_expired_workers_use_their_exact_original_fallback():
    manifest, artifacts = fixture()
    next(a for a in artifacts if a["name"] == "distributed-result-100-1")["expired"] = True
    chosen = core.merge_artifact_plan(manifest, artifacts, "100")
    assert [(row["worker_id"], row["kind"]) for row in chosen] == [("0", "result"), ("1", "fallback")]
    artifacts = [a for a in artifacts if a["name"] != "distributed-input-100-1"]
    with pytest.raises(ValueError, match="No saved result or original fallback"):
        core.merge_artifact_plan(manifest, artifacts, "100")


def test_generation_identity_and_duplicate_receipts_cannot_select_another_batch():
    manifest, artifacts = fixture()
    with pytest.raises(ValueError, match="requested generation"):
        core.merge_artifact_plan(manifest, artifacts, "101")
    with pytest.raises(ValueError, match="ambiguous"):
        core.merge_artifact_plan(manifest, artifacts + [artifacts[0]], "100")
    manifest["workers"][1]["id"] = "0"
    with pytest.raises(ValueError, match="worker identity"):
        core.merge_artifact_plan(manifest, artifacts, "100")


def test_restore_checks_the_combined_disk_budget_before_downloading_anything(tmp_path, monkeypatch):
    manifest, artifacts = fixture()
    client = Client(manifest, artifacts)
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="Insufficient disk"):
        core.restore_merge_artifacts(client, manifest, "100", tmp_path / "results", tmp_path / "inputs")
    assert client.downloads == []


def test_restore_downloads_selected_archives_once_and_keeps_result_metadata_layout(tmp_path, monkeypatch):
    manifest, artifacts = fixture()
    client = Client(manifest, artifacts)
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    result = core.restore_merge_artifacts(client, manifest, "100", tmp_path / "results", tmp_path / "inputs")
    assert result["result_workers"] == ["0", "1"] and result["fallback_workers"] == []
    assert client.downloads == [("100", "distributed-result-100-0"), ("100", "distributed-result-100-1")]
    assert list((tmp_path / "inputs").iterdir()) == []
    assert (tmp_path / "results/distributed-result-100-0/manifest.json").is_file()


def test_merge_only_recovery_uses_original_pilot_and_runner_scope():
    client = Client(*fixture())
    assert core.check_recovery(client, "100", "200") == {"source_run_id": "100", "runners": "2", "pilot": "false"}
    assert client.downloads == [("100", "distributed-shared-100")]


@pytest.mark.parametrize("changes", [{"status": "in_progress"}, {"conclusion": "success"},
    {"head_branch": "untrusted"}, {"event": "pull_request"}, {"path": ".github/workflows/other.yml"}])
def test_recovery_rejects_untrusted_active_or_completed_successful_generations(changes):
    client = Client(*fixture())
    client.source.update(changes)
    with pytest.raises(ValueError, match="completed interrupted"):
        core.check_recovery(client, "100", "200")
    assert client.downloads == []


def test_recovery_rejects_expired_baseline_and_newer_canonical_state():
    client = Client(*fixture())
    next(a for a in client.saved if a["name"] == "distributed-base-100")["expired"] = True
    with pytest.raises(ValueError, match="expired or missing"):
        core.check_recovery(client, "100", "200")
    client = Client(*fixture())
    client.newer = [{"id": 150, "head_branch": "main", "event": "workflow_dispatch", "status": "completed"}]
    client.newer_artifacts = [artifact("build-styles-distributed-150")]
    with pytest.raises(ValueError, match="newer canonical checkpoint"):
        core.check_recovery(client, "100", "200")
    assert client.downloads == []


def test_recovery_checks_past_the_first_page_before_allowing_a_rollback():
    class PagedClient(Client):
        def api(self, endpoint):
            if "collect-distributed-build-styles.yml/runs" in endpoint:
                ids = range(300, 200, -1) if endpoint.endswith("page=1") else [150]
                return {"workflow_runs": [{"id": i, "head_branch": "main", "event": "workflow_dispatch",
                                           "status": "completed"} for i in ids]}
            if "build-commander-styles.yml/runs" in endpoint:
                return {"workflow_runs": []}
            return super().api(endpoint)

        def artifacts(self, source):
            if str(source) == "150":
                return [artifact("build-styles-distributed-150")]
            return self.saved if str(source) == "100" else []

    client = PagedClient(*fixture())
    with pytest.raises(ValueError, match="newer canonical checkpoint"):
        core.check_recovery(client, "100", "400")
    assert client.downloads == []
