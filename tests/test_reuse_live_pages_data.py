import hashlib
import importlib.util
import json
from pathlib import Path

import brotli
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reuse_live_pages_data.py"
spec = importlib.util.spec_from_file_location("reuse_live_pages_data", SCRIPT)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
VERSION = "1234abcd"
VERSION_ROOT = f"https://{VERSION}-{snapshot.WORKER_HOST}"
DATA_BASE = f"{VERSION_ROOT}/data/latest"


@pytest.fixture
def live_responses(monkeypatch):
    pages_manifest = {"unique_commanders": 7038, "data_base_url": DATA_BASE}
    worker_manifest = json.dumps({"unique_commanders": 7038}).encode()
    pages_json = {snapshot.MANIFEST: json.dumps(pages_manifest).encode()}
    pages_json.update({path: json.dumps({"path": path}).encode() for path in snapshot.INDEXES})
    responses = {f"{snapshot.PAGES_DATA}/{path}": raw for path, raw in pages_json.items()}
    responses.update({f"{snapshot.PAGES_DATA}/{path}.br": brotli.compress(raw) for path, raw in pages_json.items()})
    responses.update({f"{DATA_BASE}/{path}.br": brotli.compress(pages_json[path]) for path in snapshot.INDEXES})
    responses[f"{DATA_BASE}/{snapshot.MANIFEST}"] = worker_manifest
    responses[f"{VERSION_ROOT}/deployment.json"] = json.dumps({
        "schema_version": 1,
        "build_id": "12345-1",
        "manifest_sha256": hashlib.sha256(worker_manifest).hexdigest(),
    }).encode()

    def fake_fetch(url, *, from_worker=False):
        assert from_worker == url.startswith(VERSION_ROOT)
        return responses[url]

    monkeypatch.setattr(snapshot, "fetch", fake_fetch)
    return responses


def test_preserves_every_same_origin_file_verbatim(tmp_path, live_responses):
    destination = tmp_path / "data"
    assert snapshot.prepare(destination, VERSION) == DATA_BASE
    expected = {url.removeprefix(f"{snapshot.PAGES_DATA}/"): body
                for url, body in live_responses.items() if url.startswith(snapshot.PAGES_DATA)}
    actual = {path.relative_to(destination).as_posix(): path.read_bytes()
              for path in destination.rglob("*") if path.is_file()}
    assert actual == expected


@pytest.mark.parametrize(("version", "message"), [("malformed", "hex characters"), ("deadbeef", "does not point")])
def test_rejects_invalid_or_stale_requested_versions(tmp_path, live_responses, version, message):
    destination = tmp_path / "data"
    with pytest.raises(ValueError, match=message):
        snapshot.prepare(destination, version)
    assert not destination.exists()


def test_rejects_worker_manifest_changed_after_publication(tmp_path, live_responses):
    live_responses[f"{DATA_BASE}/{snapshot.MANIFEST}"] = b'{"unique_commanders": 4200}'
    destination = tmp_path / "data"
    with pytest.raises(ValueError, match="deployment metadata do not match"):
        snapshot.prepare(destination, VERSION)
    assert not destination.exists()


def test_rejects_an_index_mismatch_before_writing_any_files(tmp_path, live_responses):
    live_responses[f"{DATA_BASE}/commanders/index.json.br"] = brotli.compress(b"[]")
    destination = tmp_path / "data"
    with pytest.raises(ValueError, match="index differs from the pinned Worker"):
        snapshot.prepare(destination, VERSION)
    assert not destination.exists()
