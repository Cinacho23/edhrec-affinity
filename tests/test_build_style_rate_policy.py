"""The approved speed increase runs once and never removes source controls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from edhrec_affinity.build_styles.rate_policy import (
    APPROVAL_ID, APPROVAL_TIME, WEBKIT_START_TIME, promote_initial_browser_rate,
)

NOW = APPROVAL_TIME + timedelta(minutes=10)


def state():
    return {"commanders": {"example": {"current_previews": 123, "status": "collecting"}},
            "source_rate": {"learned_rate": .1, "throttles": 3,
                "last_throttle": {"at": "2026-10-23T00:00:00+00:00"},
                "observed_requests_per_second": .09, "observed_rate_at": "2026-11-13T20:00:00+00:00"},
            "official_validation": {"source_blocked": True}, "pipeline": {"collection_workers": {"0": "example"}}}


def promote(value, **changes):
    return promote_initial_browser_rate(value, changes.pop("ceiling", 1),
        **({"approval": APPROVAL_ID, "transport": "webkit", "now": NOW} | changes))


def test_promotion_keeps_decks_claims_throttle_history_and_an_exact_audit():
    value = state()
    original = deepcopy(value)
    assert promote(value)
    saved = value["source_rate"]
    assert saved["learned_rate"] == 1
    assert saved["throttles"] == original["source_rate"]["throttles"]
    assert saved["last_throttle"] == original["source_rate"]["last_throttle"]
    assert saved["rate_approval"]["previous_controls"] == original["source_rate"]
    assert "observed_requests_per_second" not in saved and "observed_rate_at" not in saved
    assert {key: item for key, item in value.items() if key != "source_rate"} == {
        key: item for key, item in original.items() if key != "source_rate"}


@pytest.mark.parametrize("changes", [{"approval": None}, {"approval": "other"}, {"transport": "http"},
    {"transport": None}, {"ceiling": .5}, {"now": APPROVAL_TIME - timedelta(seconds=1)}])
def test_approval_must_be_explicit_current_and_for_the_selected_browser(changes):
    value = state()
    original = deepcopy(value)
    assert not promote(value, **changes)
    assert value == original


@pytest.mark.parametrize("controls", [
    {"blocked": True}, {"block_reason": "access_restriction"},
    {"blocked_until": (NOW + timedelta(seconds=1)).isoformat()}, {"blocked_until": "bad"},
    {"source_stop": {"provider": "edhrec", "reason": "access_restriction"}},
])
def test_source_holds_are_never_released_or_promoted(controls):
    value = state() | controls
    original = deepcopy(value)
    assert not promote(value)
    assert value == original


@pytest.mark.parametrize("throttle", [{"at": WEBKIT_START_TIME.isoformat()},
    {"at": (APPROVAL_TIME - timedelta(seconds=1)).isoformat()}, {"at": NOW.isoformat()},
    {"at": "bad"}, {}, "unknown"])
def test_any_modern_or_unknown_throttle_prevents_promotion(throttle):
    value = state()
    value["source_rate"]["last_throttle"] = throttle
    original = deepcopy(value)
    assert not promote(value)
    assert value == original


def test_new_reductions_survive_even_if_they_eventually_reach_the_old_point_one_rate():
    value = state()
    assert promote(value)
    value["source_rate"]["learned_rate"] = .1
    value["source_rate"]["last_throttle"] = {"at": NOW.isoformat()}
    original = deepcopy(value)
    assert not promote(value, now=NOW + timedelta(days=2))
    assert value == original


@pytest.mark.parametrize("saved", [.075, .5, 1, None])
def test_only_the_legacy_initial_budget_is_promoted(saved):
    value = state()
    value["source_rate"]["learned_rate"] = saved
    original = deepcopy(value)
    assert not promote(value)
    assert value == original
