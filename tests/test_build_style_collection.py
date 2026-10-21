"""Uncapped collection must survive pauses without losing or refetching lists."""

import gzip
import json

import httpx
import numpy as np
import pytest
from scipy import sparse

from edhrec_affinity.build_styles.__main__ import parser
from edhrec_affinity.build_styles.analysis import _reconstruction_ratio, analyze_decks
from edhrec_affinity.build_styles.collection import collect_previews
from edhrec_affinity.build_styles.sources import SourceBlocked


class PreviewCache:
    def __init__(self, count=205):
        self.frame = [{"urlhash": f"deck-{index}", "bracket": 3} for index in range(count)]
        self.calls = []
        self.provenance = []
        self.failure = None

    def get(self, relative, url, **kwargs):
        self.calls.append(relative)
        self.provenance.append({"url": url, "sha256": "source-hash"})
        if relative == "decks.json":
            return {"table": self.frame}
        if self.failure:
            raise self.failure
        return {"deck": {"commander": ["Ghave"], "cards": {
            "Creature": [[relative, 1]], "Land": [["Forest", 98]],
        }}, "url": "https://archidekt.com/decks/123"}


def test_default_collects_more_than_old_60_and_200_limits(tmp_path):
    assert parser().parse_args(["--commander", "ghave"]).sample_decks is None
    assert parser().parse_args(["--commander", "ghave", "--sample-decks", "10000"]).sample_decks == 10000
    cache = PreviewCache()
    decks, status = collect_previews(cache, "ghave", limit=None, seed=42, progress_path=tmp_path / "state.gz")
    assert len(decks) == status["previews_attempted"] == status["frame_decks"] == 205
    assert status["target_mode"] == "all" and status["complete"]


def test_time_slice_resumes_frozen_frame_and_lists_without_refetch(tmp_path):
    path = tmp_path / "state.gz"
    cache = PreviewCache(12)
    ticks = iter(range(20))
    decks, status = collect_previews(cache, "ghave", limit=None, seed=42, progress_path=path,
                                     max_seconds=3, monotonic=lambda: next(ticks))
    assert len(decks) == 2 and not status["complete"]
    already_loaded = set(cache.calls) - {"decks.json"}
    resumed = PreviewCache(0)  # The upstream index may change between jobs.
    decks, status = collect_previews(resumed, "ghave", limit=None, seed=42, progress_path=path)
    assert len(decks) == 12 and status["complete"]
    assert not already_loaded.intersection(resumed.calls)
    assert "decks.json" not in resumed.calls
    assert len(resumed.provenance) == 12
    assert all(deck["source_bracket"] == 3 for deck in decks)


@pytest.mark.parametrize("error", [SourceBlocked("challenge"), httpx.ConnectError("temporary outage"), FileNotFoundError("offline")])
def test_access_or_transient_failure_is_not_a_permanent_exclusion(tmp_path, error):
    path = tmp_path / "state.gz"
    cache = PreviewCache(2)
    cache.failure = error
    _, status = collect_previews(cache, "ghave", limit=None, seed=42, progress_path=path)
    assert not status["complete"] and status["previews_attempted"] == 0
    decks, status = collect_previews(PreviewCache(0), "ghave", limit=None, seed=42, progress_path=path)
    assert len(decks) == 2 and status["complete"]


def test_optional_sample_can_expand_to_all_without_losing_initial_lists(tmp_path):
    path = tmp_path / "state.gz"
    first, status = collect_previews(PreviewCache(20), "ghave", limit=3, seed=42, progress_path=path)
    assert status["complete"] and status["target_decks"] == 3
    cache = PreviewCache(0)
    expanded, status = collect_previews(cache, "ghave", limit=None, seed=42, progress_path=path)
    assert {deck["id"] for deck in first} <= {deck["id"] for deck in expanded}
    assert len(cache.calls) == 17 and len(expanded) == 20
    with gzip.open(path, "rt") as handle:
        assert len(json.load(handle)["results"]) == 20


def test_sparse_validation_error_matches_dense_calculation():
    rng = np.random.default_rng(42)
    matrix, weights, components = rng.random((20, 15)), rng.random((20, 3)), rng.random((3, 15))
    assert _reconstruction_ratio(sparse.csr_matrix(matrix), weights, components) == pytest.approx(
        _reconstruction_ratio(matrix, weights, components), rel=1e-12)


def test_large_model_uses_all_usable_decks_with_sparse_features():
    decks = []
    for index in range(1002):
        cards = [{"name": f"Engine {index % 2} {j}", "quantity": 1} for j in range(6)]
        cards += [{"name": f"Unique {index}", "quantity": 1},
                  {"name": "Forest", "quantity": 92, "type_line": "Basic Land"}]
        decks.append({"id": str(index), "commanders": ["Ghave"], "cards": cards})
    result = analyze_decks(decks, {"commander_names": ["Ghave"], "land_keys": [], "cards": {}}, [], components=2)
    assert result["status"] == "fitted"
    assert result["unique_decks"] == len(result["decks"]) == 1002
    assert result["model_selection"]["data_limits"]["sparse_matrix"]
    assert result["near_duplicate_pairs"] is None
