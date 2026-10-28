"""Score boundaries are independent of observed card-restriction brackets."""

import pytest

from edhrec_affinity.build_styles.power_brackets import (
    bracket_for_power_score, parse_power_score, verified_score_bracket,
)


@pytest.mark.parametrize("score,expected", [
    (0, 1), (4.699999, 1), (4.7, 2), (6.09, 2), (6.699999, 2),
    (6.7, 3), (7.699999, 3), (7.7, 4), (9.249999, 4), (9.25, 5),
    (10, 5), (" 6.09 ", 2), ("9.25", 5),
])
def test_score_threshold_belongs_to_higher_bracket(score, expected):
    assert bracket_for_power_score(score) == expected


@pytest.mark.parametrize("score", [
    None, True, False, -1, float("inf"), float("nan"), 10 ** 1000,
    "", " ", "NaN", "inf", "10+", "6–7", "6.09 / 10", "6.09%", "6.09e0",
    "-1", "Bracket 2", [], {},
])
def test_missing_invalid_or_ambiguous_score_has_no_target(score):
    assert parse_power_score(score) is None
    assert bracket_for_power_score(score) is None


def test_target_uses_score_not_reported_bracket_or_playability():
    rating = {"status": "verified", "source": "edhpowerlevel.com",
              "power_level": 6.09, "bracket": 4, "playability_pct": 75.0,
              "score_bracket": 5}
    assert verified_score_bracket(rating) == 2


@pytest.mark.parametrize("changes", [
    {"status": "stale"}, {"status": "unverified"}, {"source": "another.example"},
    {"power_level": None}, {"power_level": "6–7"},
])
def test_only_verified_unambiguous_external_score_can_drive_repair(changes):
    rating = {"status": "verified", "source": "edhpowerlevel.com", "power_level": 6.09, **changes}
    assert verified_score_bracket(rating) is None
