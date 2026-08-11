"""Tests for fields shared by normal and filtered EDHREC page payloads."""

import pytest

from edhrec_affinity.edhrec_payload import extract_page_deck_count


def test_extract_page_deck_count_uses_current_edhrec_path() -> None:
    payload = {
        "container": {
            "json_dict": {
                "card": {
                    "num_decks": 49_644,
                }
            }
        },
        "num_decks_avg": 44_863,
    }

    assert extract_page_deck_count(payload) == 49_644


def test_extract_page_deck_count_supports_legacy_payloads() -> None:
    assert extract_page_deck_count({"num_decks_avg": "1,234"}) == 1_234


def test_extract_page_deck_count_rejects_missing_count() -> None:
    with pytest.raises(KeyError, match="missing both"):
        extract_page_deck_count({"container": {"json_dict": {}}})


def test_extract_page_deck_count_rejects_invalid_current_count() -> None:
    payload = {
        "container": {
            "json_dict": {
                "card": {
                    "num_decks": "not-a-number",
                }
            }
        },
        "num_decks_avg": 100,
    }

    with pytest.raises(ValueError, match="must be an integer deck count"):
        extract_page_deck_count(payload)
