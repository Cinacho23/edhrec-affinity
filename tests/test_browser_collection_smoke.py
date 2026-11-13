"""The live smoke command has a fixed scope and fails without source proof."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from edhrec_affinity.build_styles.sources import SourceBlocked

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("browser_collection_smoke", ROOT / "scripts/test_browser_collection.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def gallery():
    return {"table": [{"urlhash": smoke.PREVIEW_ID}, {"urlhash": "another-deck"}],
            "commander_slug": smoke.COMMANDER_SLUG, "displayed_total": 2, "index_complete": True}


def preview():
    return {"urlhash": smoke.PREVIEW_ID, "deck": {"commander": sorted(smoke.EXPECTED_COMMANDERS),
            "cards": {"Lands": [["Island", 98]]}}, "url": "https://moxfield.com/decks/example"}


@pytest.mark.parametrize("alter", [
    lambda data: data.update(commander_slug="another-commander"),
    lambda data: data.update(index_complete=False),
    lambda data: data.update(displayed_total=50),
    lambda data: data.update(displayed_total=True),
    lambda data: data["table"].append(dict(data["table"][0])),
    lambda data: data["table"][0].update(urlhash="wrong-deck"),
])
def test_gallery_requires_identity_unique_ids_and_complete_population(alter):
    data = gallery()
    alter(data)
    with pytest.raises(ValueError):
        smoke.check_gallery(data)


@pytest.mark.parametrize("alter", [
    lambda data: data.update(urlhash="another-deck"),
    lambda data: data["deck"].update(commander=["Sol Ring"]),
    lambda data: data["deck"]["cards"].update(Lands=[["Island", 97]]),
])
def test_preview_requires_source_id_intended_commanders_and_exact_card_count(alter):
    data = preview()
    alter(data)
    with pytest.raises(ValueError):
        smoke.check_preview(data)


@pytest.mark.parametrize("blocked_at", [None, "gallery", "preview"])
def test_smoke_calls_only_two_explicit_sources_and_preserves_failure_receipt(tmp_path, monkeypatch, blocked_at):
    monkeypatch.setenv("BUILD_STYLE_EDHREC_TRANSPORT", "webkit")
    monkeypatch.setattr(smoke.importlib.metadata, "version", lambda name: "1.64.1")
    calls, instances = [], []

    class FakeCache:
        def __init__(self, root, *, fetch, limiter):
            assert fetch is True
            assert root == tmp_path / "source-cache"
            assert limiter.stats["configured_maximum_requests_per_second"] == 0.1
            self.closed = False
            instances.append(self)

        def get_deck_index(self, slug, *, force_refresh):
            assert slug == smoke.COMMANDER_SLUG and force_refresh is True
            calls.append("gallery")
            if blocked_at == "gallery":
                raise SourceBlocked("HTTP 403; no retry attempted")
            return copy.deepcopy(gallery())

        def get_preview(self, identifier, *, force_refresh):
            assert identifier == smoke.PREVIEW_ID and force_refresh is True
            calls.append("preview")
            if blocked_at == "preview":
                raise SourceBlocked("HTTP 429; no retry attempted")
            return copy.deepcopy(preview())

        def provenance_for(self, url):
            return [{"url": url, "fetched_at": "2026-11-12T00:00:00+00:00", "sha256": "proof"}]

        def close(self):
            self.closed = True

    result = smoke.run_test(tmp_path, cache_factory=FakeCache)
    assert result == json.loads((tmp_path / "receipt.json").read_text())
    assert instances[0].closed
    assert calls == (["gallery"] if blocked_at == "gallery" else ["gallery", "preview"])
    assert result["passed"] is (blocked_at is None)
    assert result["checkpoint_controls_changed"] is False
    assert result["collector_dispatched"] is False
    assert (tmp_path / "sample-deck.txt").exists() is (blocked_at is None)
    if blocked_at:
        assert result["error_type"] == "SourceBlocked"


def test_smoke_never_silently_uses_the_http_transport(tmp_path, monkeypatch):
    monkeypatch.delenv("BUILD_STYLE_EDHREC_TRANSPORT", raising=False)
    result = smoke.run_test(tmp_path, cache_factory=lambda *args, **kwargs: pytest.fail("Do not create a client"))
    assert result["passed"] is False
    assert "Set BUILD_STYLE_EDHREC_TRANSPORT=webkit" in result["error"]


def test_runners_install_webkit_only_for_edhrec_source_jobs():
    distributed = (ROOT / ".github/workflows/collect-distributed-build-styles.yml").read_text()
    plan, collect = distributed.split("  collect:\n", 1)
    collect, following_jobs = collect.split("  power:\n", 1)
    assert 'python -m pip install -e ".[prototype,collector]"' in collect
    assert "python -m playwright install --with-deps webkit" in collect
    assert "BUILD_STYLE_EDHREC_TRANSPORT: webkit" in collect
    assert "--with-deps webkit" not in plan + following_jobs
    assert "vars.BUILD_STYLE_BROWSER_REQUESTS_PER_SECOND || '0.1'" in distributed
    legacy = (ROOT / ".github/workflows/build-commander-styles.yml").read_text()
    install = legacy.split("      - name: Install the EDHREC document browser\n", 1)[1].split("      - name:", 1)[0]
    assert "inputs.power_only != true && inputs.validate_power != true && inputs.rebuild_only != true" in install
    assert "BUILD_STYLE_EDHREC_TRANSPORT: webkit" in legacy
    assert "vars.BUILD_STYLE_BROWSER_REQUESTS_PER_SECOND || '0.1'" in legacy


def test_smoke_workflow_cannot_clear_holds_or_start_production():
    workflow = (ROOT / ".github/workflows/test-browser-collection.yml").read_text()
    assert "group: commander-build-style-samples" in workflow
    assert "timeout-minutes: 15" in workflow
    assert "BUILD_STYLE_EDHREC_TRANSPORT: webkit" in workflow
    assert "if: always()" in workflow
    assert "contents: read" in workflow and "actions: write" not in workflow
    assert "restore-build-styles" not in workflow
    assert "coordinate_build_styles.py" not in workflow
    assert "scripts/test_browser_collection.py" in workflow
