"""External observations apply only to the exact complete list that was checked."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from edhrec_affinity.build_styles.external_validation import (
    attach_external_validation, cardlist_hash, record_external_validation,
)


def deck():
    return {"card_count": 100, "cards": [
        {"name": "Ghave, Guru of Spores", "quantity": 1, "tags": ["Commander"]},
        {"name": "Forest", "quantity": 98, "tags": ["Lands"]},
        {"name": "Sol Ring", "quantity": 1, "tags": ["Ramp"]},
    ], "validation": {"external_url": "https://edhpowerlevel.com?d=existing-link"}}


def observations(**changes):
    return {"source": "edhpowerlevel.com", "checked_at": datetime.now(timezone.utc).isoformat(),
            "card_count": 100, "playability_pct": 74.5, "bracket": 5, "power_level": "10+",
            "source_url": "https://edhpowerlevel.com?d=1+Ghave~99+Forest~Z~", **changes}


def test_cardlist_hash_ignores_order_tags_case_and_equivalent_quantity_rows():
    original = deck()["cards"]
    equivalent = [{"name": " SOL RING ", "quantity": 1, "tags": ["Other"]},
                  {"name": "forest", "quantity": 48}, {"name": "FOREST", "quantity": 50},
                  {"name": "Ghave, Guru of Spores", "quantity": 1}]
    assert cardlist_hash(original) == cardlist_hash(equivalent)
    changed = deepcopy(original)
    changed[2]["name"] = "Arcane Signet"
    assert cardlist_hash(original) != cardlist_hash(changed)


def test_missing_record_is_explicitly_unverified_and_keeps_existing_link(tmp_path):
    current = deck()
    rating = attach_external_validation(current, tmp_path)
    assert rating == {"status": "unverified", "source": "edhpowerlevel.com"}
    assert current["validation"]["external_rating"] is rating
    assert current["validation"]["external_url"] == "https://edhpowerlevel.com?d=existing-link"
    assert not list(tmp_path.iterdir())


def test_records_exact_observations_and_reattaches_after_reload(tmp_path):
    current = deck()
    rating = record_external_validation(current, observations(), tmp_path)
    assert rating["status"] == "verified"
    assert rating["playability_pct"] == 74.5
    assert rating["bracket"] == 5
    assert rating["power_level"] == "10+"
    record_path = tmp_path / f"{cardlist_hash(current['cards'])}.json"
    stored = json.loads(record_path.read_text())
    assert stored["deck_sha256"] == cardlist_hash(current["cards"])
    assert "status" not in stored
    assert attach_external_validation(deck(), tmp_path) == rating
    assert not list(tmp_path.glob("*.tmp"))


def test_old_record_is_stale_with_score_and_original_timestamp_preserved(tmp_path):
    checked_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    record_external_validation(deck(), observations(checked_at=checked_at), tmp_path)
    rating = attach_external_validation(deck(), tmp_path)
    assert rating["status"] == "stale"
    assert rating["checked_at"] == checked_at
    assert rating["playability_pct"] == 74.5


def test_changed_deck_cannot_inherit_previous_package_rating(tmp_path):
    record_external_validation(deck(), observations(), tmp_path)
    changed = deck()
    changed["cards"][2]["name"] = "Arcane Signet"
    assert attach_external_validation(changed, tmp_path)["status"] == "unverified"


def test_record_with_different_internal_hash_is_rejected(tmp_path):
    current = deck()
    path = tmp_path / f"{cardlist_hash(current['cards'])}.json"
    path.write_text(json.dumps({"schema_version": 1, "deck_sha256": "a" * 64, **observations()}))
    rating = attach_external_validation(current, tmp_path)
    assert rating["status"] == "unverified"
    assert "another card list" in rating["reason"]


@pytest.mark.parametrize("changes", [
    {"card_count": 99}, {"card_count": True}, {"source": "unrelated.com"},
    {"bracket": 0}, {"bracket": 6}, {"bracket": True},
    {"playability_pct": -1}, {"playability_pct": 101}, {"playability_pct": True},
    {"playability_pct": float("nan")}, {"playability_pct": float("inf")},
    {"power_level": float("nan")}, {"power_level": ""},
    {"source_url": "http://edhpowerlevel.com?d=list"},
    {"source_url": "https://edhpowerlevel.com.evil.example?d=list"},
    {"source_url": "https://another.example?d=list"},
    {"source_url": "https://edhpowerlevel.com"},
    {"source_url": "https://user@edhpowerlevel.com?d=list"},
    {"source_url": "https://edhpowerlevel.com:444?d=list"},
    {"checked_at": "2026-01-01T00:00:00"},
    {"checked_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
])
def test_invalid_observations_are_not_recorded(tmp_path, changes):
    with pytest.raises(ValueError):
        record_external_validation(deck(), observations(**changes), tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("extra_json", ['"invalid":NaN', '"invalid":1e999'])
def test_nonfinite_json_is_rejected_on_attachment(tmp_path, extra_json):
    current = deck()
    record_external_validation(current, observations(), tmp_path)
    path = tmp_path / f"{cardlist_hash(current['cards'])}.json"
    path.write_text(path.read_text()[:-1] + "," + extra_json + "}")
    rating = attach_external_validation(deck(), tmp_path)
    assert rating["status"] == "unverified"
    assert "reason" in rating


def test_incomplete_or_misreported_generated_list_cannot_be_checked(tmp_path):
    current = deck()
    current["cards"][1]["quantity"] = 97
    assert attach_external_validation(current, tmp_path)["status"] == "unverified"
    with pytest.raises(ValueError):
        record_external_validation(current, observations(), tmp_path)
    current = deck()
    current["card_count"] = 99
    with pytest.raises(ValueError):
        record_external_validation(current, observations(), tmp_path)


@pytest.mark.parametrize("cards", [[], [{"name": "Forest", "quantity": True}], [{"name": ""}], "Forest"])
def test_hash_rejects_malformed_card_lists(cards):
    with pytest.raises(ValueError):
        cardlist_hash(cards)
