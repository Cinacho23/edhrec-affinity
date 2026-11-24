import gzip
import json
from pathlib import Path

import pytest

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.synergy_prototype import __main__ as prototype
from edhrec_affinity.synergy_prototype.catalog import load_catalog
from edhrec_affinity.synergy_prototype.construction import ORACLE_POLICY
from edhrec_affinity.synergy_prototype.experiments import DEFAULT_BASELINE, DEFAULT_EXPERIMENTS


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
