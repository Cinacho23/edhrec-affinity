"""The approved speed increase runs once and never removes source controls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from edhrec_affinity.build_styles.rate_policy import (
    APPROVAL_ID, APPROVAL_TIME, APPROVAL_1_5_ID, APPROVAL_1_5_TIME,
    WEBKIT_START_TIME, promote_initial_browser_rate,
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


def promote_one_point_five(value, **changes):
    return promote_initial_browser_rate(value, changes.pop("ceiling", 1.5),
        **({"approval": APPROVAL_1_5_ID, "transport": "webkit",
            "now": APPROVAL_1_5_TIME + timedelta(minutes=10)} | changes))


@pytest.mark.parametrize("prior", [.1, 1.0])
def test_new_approval_promotes_only_unchanged_configured_budgets_and_keeps_prior_audit(prior):
    value = state()
    if prior == 1:
        assert promote(value)
    original = deepcopy(value)
    assert promote_one_point_five(value)
    assert value["source_rate"]["learned_rate"] == 1.5
    audit = value["source_rate"]["rate_approval"]
    assert audit["id"] == APPROVAL_1_5_ID
    assert audit["previous_controls"] == original["source_rate"]
    assert value["commanders"] == original["commanders"]
    assert value["official_validation"] == original["official_validation"]
    assert value["pipeline"] == original["pipeline"]
    value["source_rate"]["learned_rate"] = 1
    assert not promote_one_point_five(value)  # The approval cannot be reused.


@pytest.mark.parametrize("controls", [
    {"blocked": True}, {"block_reason": "access_restriction"},
    {"blocked_until": (APPROVAL_1_5_TIME + timedelta(days=1)).isoformat()},
    {"blocked_until": "bad"}, {"source_stop": {"reason": "access_restriction"}},
])
def test_new_approval_preserves_every_source_hold(controls):
    value = state() | controls
    value["source_rate"]["learned_rate"] = 1
    original = deepcopy(value)
    assert not promote_one_point_five(value)
    assert value == original


@pytest.mark.parametrize("prior", [.075, .75, 1.125, 1.5, None, True, False])
def test_new_approval_never_overrides_a_learned_or_invalid_budget(prior):
    value = state()
    value["source_rate"]["learned_rate"] = prior
    original = deepcopy(value)
    assert not promote_one_point_five(value)
    assert value == original


@pytest.mark.parametrize("throttle", [{"at": WEBKIT_START_TIME.isoformat()},
    {"at": APPROVAL_1_5_TIME.isoformat()}, {"at": "bad"}, {}, "unknown"])
def test_new_approval_does_not_reset_an_intervening_browser_throttle(throttle):
    value = state()
    value["source_rate"].update(learned_rate=1, last_throttle=throttle)
    original = deepcopy(value)
    assert not promote_one_point_five(value)
    assert value == original


@pytest.mark.parametrize("changes", [{"ceiling": 1}, {"transport": "http"},
    {"approval": APPROVAL_ID}, {"now": APPROVAL_1_5_TIME - timedelta(seconds=1)}])
def test_new_approval_requires_the_explicit_matching_current_configuration(changes):
    value = state()
    value["source_rate"]["learned_rate"] = 1
    assert not promote_one_point_five(value, **changes)
