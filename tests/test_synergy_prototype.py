import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.synergy_prototype import __main__ as prototype
from edhrec_affinity.synergy_prototype.catalog import load_catalog
from edhrec_affinity.synergy_prototype.construction import ORACLE_POLICY
from edhrec_affinity.synergy_prototype.experiments import DEFAULT_BASELINE, DEFAULT_EXPERIMENTS
from edhrec_affinity.synergy_prototype.ranker import DeckRanker


def test_completed_target_checkpoint_trains_on_every_distinct_target_list(tmp_path):
    pair = ("doctor", "rose")
    common = [f"spell-{i}" for i in range(25)]
    alternatives = [f"choice-{i}" for i in range(50)]
    keys = [*pair, "another-commander", "basic-land", *common, *alternatives, "target-spell", "test-spell"]
    ranker = DeckRanker.__new__(DeckRanker)
    ranker.index = ranker.alias_indices = {name: index for index, name in enumerate(keys)}
    ranker.masks = np.zeros(len(keys), dtype=np.int32)
    ranker.spells = np.array([name != "basic-land" for name in keys])
    ranker.holdout = np.array([name in {"target-spell", "test-spell"} for name in keys])

    def deck(commanders, cards):
        return {"deck": {"commanders": commanders, "cards": [
            *({"name": card, "quantity": 1} for card in cards),
            {"name": "basic-land", "quantity": 100 - len(commanders) - len(cards)},
        ]}}

    def checkpoint(path, **value):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(value, handle)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    checkpoint(corpus / "others.json.gz", results={
        name: deck(["another-commander"], [*common, name, "test-spell"]) for name in alternatives
    })
    first = deck(list(pair), [*common, "target-spell"])
    target = tmp_path / "target.json.gz"
    results = {
        "direct": first,
        "reverse": deck(list(reversed(pair)), [*common, "target-spell", alternatives[0]]),
        "duplicate": first,
        "land-heavy": deck(list(pair), ["target-spell", alternatives[1]]),
        "unavailable": {"error": "source preview returned HTTP 404"},
    }
    frame = [{"urlhash": name} for name in results]
    checkpoint(target, frame=frame, results=results)

    train, test, stats = ranker.load_decks(corpus, pair, target_previews=target)
    command = tuple(ranker.index[name] for name in pair)
    paired = [row for row in train if row.commanders == command]
    assert len(paired) == stats["target_pair_training_decks"] == 3
    assert stats["target_duplicate_decks"] == stats["target_unavailable_previews"] == 1
    assert stats["target_indexed_decks"] == 5
    assert all(ranker.index["target-spell"] in row.spells for row in paired)
    assert all(row.commanders != command and row.held_out == (ranker.index["test-spell"],) for row in test)

    checkpoint(target, frame=[*frame, {"urlhash": "pending"}], results=results)
    with pytest.raises(ValueError, match="incomplete deck index"):
        ranker.load_decks(corpus, pair, target_previews=target)


def test_catalog_uses_oracle_legality_across_printings(tmp_path):
    tundra = {
        "name": "Tundra", "layout": "normal", "type_line": "Land — Plains Island", "cmc": 0,
        "color_identity": ["W", "U"], "games": ["mtgo"], "digital": True,
        "oracle_id": "bfc1c873-c492-403b-ba36-224c6d6df5e0", "legalities": {"commander": "legal"},
    }
    banned = {**tundra, "name": "Banned land", "legalities": {"commander": "banned"}}
    snapshot = tmp_path / "oracle.jsonl.gz"
    with gzip.open(snapshot, "wt", encoding="utf-8") as handle:
        for row in (tundra, banned):
            print(json.dumps(row), file=handle)

    catalog, aliases = load_catalog(snapshot, "2026-11-21T22:01:57Z")

    assert list(catalog) == ["tundra"]
    assert aliases == {"tundra": "tundra"}


def test_external_check_rejects_a_changed_list_before_submitting(monkeypatch, tmp_path):
    def unexpected_check(*args, **kwargs):
        pytest.fail("A mismatched deck was submitted")

    monkeypatch.setattr(prototype, "check_deck", unexpected_check)
    report = {"deck": {"cards": [{"name": "Island", "quantity": 2}], "deck_sha256": "old hash"}}

    with pytest.raises(ValueError, match="does not match"):
        prototype.verify_report(report, validation_directory=tmp_path)


def test_published_deck_has_exact_list_and_commander_roles():
    path = Path(__file__).resolve().parents[1] / prototype.DEFAULT_REPORT
    report = json.loads(path.read_text(encoding="utf-8"))
    deck = report["deck"]
    checks = deck["validation"]["checks"]

    assert tuple(card["name"] for card in deck["commanders"]) == prototype.COMMANDER_NAMES
    assert sum(card["quantity"] for card in deck["cards"]) == 100
    assert cardlist_hash(deck["cards"]) == deck["deck_sha256"]
    assert {card["name"] for card in deck["commanders"]} <= {card["name"] for card in deck["cards"]}
    assert deck["validation"]["valid"] and all(checks.values())
    construction = deck["construction"]
    assert construction["score_explanation"] == ORACLE_POLICY.score_explanation
    assert report["synergy"]["model"] == prototype.ORACLE_MODEL_DESCRIPTION
    roles = construction["spell_role_counts"]
    assert all(roles[role] >= minimum for role, minimum in construction["spell_role_targets"].items())
    assert all(roles[role] <= maximum for role, maximum in construction["spell_role_limits"].items())
    assert report["external_rating"]["deck_sha256"] == deck["deck_sha256"]
    if report["external_rating"]["status"] == "verified":
        assert report["external_rating"]["restriction_check"]["commander_names"] == list(prototype.COMMANDER_NAMES)


def test_experiments_have_distinct_legal_decks_and_exact_assessments():
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / DEFAULT_BASELINE).read_text(encoding="utf-8"))
    experiments = json.loads((root / DEFAULT_EXPERIMENTS).read_text(encoding="utf-8"))
    assert experiments["baseline_deck_sha256"] == baseline["deck"]["deck_sha256"]
    variants = experiments["variants"]
    assert {variant["id"] for variant in variants} == {"mechanics", "balanced", "interactions", "combined"}
    known_hashes = {experiments["baseline_deck_sha256"]}
    baseline_cards = {card["name"] for card in baseline["deck"]["cards"]}
    for variant in variants:
        deck = variant["deck"]
        sha256 = cardlist_hash(deck["cards"])
        assert sha256 == deck["deck_sha256"] == variant["external_rating"]["deck_sha256"]
        assert sha256 not in known_hashes
        known_hashes.add(sha256)
        assert sum(card["quantity"] for card in deck["cards"]) == 100
        assert tuple(card["name"] for card in deck["commanders"]) == prototype.COMMANDER_NAMES
        assert all(deck["validation"]["checks"].values())
        roles = deck["construction"]["spell_role_counts"]
        assert all(roles[role] >= minimum for role, minimum in deck["construction"]["spell_role_targets"].items())
        assert all(roles[role] <= maximum for role, maximum in deck["construction"]["spell_role_limits"].items())
        names = {card["name"] for card in deck["cards"]}
        assert variant["comparison"]["added"] == sorted(names - baseline_cards)
        assert variant["comparison"]["removed"] == sorted(baseline_cards - names)
    results = experiments["model_benchmark"]
    for metric in ("queries", "max_candidates_per_query", "held_out_identities", "text_similarity"):
        assert results["baseline"][metric] == results["interaction"][metric]
