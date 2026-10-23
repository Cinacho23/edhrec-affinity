"""Capacity estimates must not promise full coverage from a rate setting alone."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from edhrec_affinity.build_styles.capacity import plan_capacity


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def inventory(entries):
    return [{"commander_slug": f"commander-{index}", "total_decks": count} for index, count in enumerate(entries)]


def test_full_catalog_estimate_counts_requests_without_claiming_measured_capacity():
    result = plan_capacity(inventory([1_000_000] * 12), target_hours=96, now=NOW)
    assert result["estimated_catalog_deck_entries"] == 12_000_000
    assert result["remaining_previews"] == 12_000_000
    assert result["overhead_requests"] == 12 * 19
    assert result["minimum_required_preview_rate"] == pytest.approx(12_000_000 / (96 * 3600))
    assert result["estimated_minimum_seconds"] == (12_000_000 + 228) / 40
    assert result["configured_capacity_sufficient"] is True
    assert result["observed_requests_per_second"] is None
    assert result["projected_finish_at"] is None
    assert result["status"] == "unmeasured"
    assert result["distinct_decks_known"] is False
    assert result["deadline_at"] == (NOW + timedelta(hours=96)).isoformat()


def test_real_wall_clock_rate_can_put_an_optimistic_configuration_behind_target():
    result = plan_capacity(inventory([12_000_000]), target_hours=96, observed_requests_per_second=1,
                           elapsed_seconds=24 * 3600, now=NOW)
    assert result["configured_requests_per_second"] == 40
    assert result["configured_capacity_sufficient"] is False
    assert result["measured_capacity_sufficient"] is False
    assert result["status"] == "behind_target"
    assert result["remaining_seconds"] == 72 * 3600
    assert result["projected_finish_at"] == (NOW + timedelta(seconds=12_000_019)).isoformat()
    assert result["deadline_at"] == (NOW + timedelta(hours=72)).isoformat()


def test_learned_source_ceiling_controls_projection_without_hiding_configuration():
    result = plan_capacity(inventory([12_000_000]), max_requests_per_second=40,
                           target_hours=96, effective_requests_per_second=1.875, now=NOW)
    assert result["configured_requests_per_second"] == 40
    assert result["effective_requests_per_second"] == 1.875
    assert result["configured_capacity_sufficient"] is True
    assert result["effective_capacity_sufficient"] is False
    assert result["estimated_minimum_seconds"] == (12_000_000 + 19) / 1.875
    assert result["status"] == "behind_target"


def test_default_eta_uses_learned_ceiling_and_cooldown_without_a_deadline():
    result = plan_capacity(inventory([100]), effective_requests_per_second=1.5,
                           observed_requests_per_second=10, cooldown_seconds=3600, now=NOW)
    assert result["target_hours"] is None
    assert result["deadline_at"] is None
    assert result["remaining_seconds"] is None
    assert result["minimum_required_request_rate"] is None
    assert result["configured_capacity_sufficient"] is None
    assert result["status"] == "collecting"
    assert result["observed_requests_per_second"] == 10
    assert result["projection_requests_per_second"] == 1.5
    assert result["estimated_minimum_seconds"] == 119 / 1.5 + 3600
    assert result["measured_projection_seconds"] == 119 / 1.5 + 3600
    assert result["earliest_possible_finish_at"] == (NOW + timedelta(seconds=119 / 1.5 + 3600)).isoformat()


def test_actual_remaining_work_replaces_the_initial_full_catalog_estimate():
    result = plan_capacity(inventory([12_000_000]), remaining_previews=60, remaining_overhead_requests=0,
                           target_hours=96, observed_requests_per_second=5, elapsed_seconds=95 * 3600, now=NOW)
    assert result["remaining_requests"] == 60
    assert result["measured_projection_seconds"] == 12
    assert result["status"] == "on_track"
    assert result["estimated_catalog_deck_entries"] == 12_000_000


@pytest.mark.parametrize("rate", [None, 0, 40])
def test_elapsed_deadlines_stay_finite_and_do_not_claim_completion(rate):
    result = plan_capacity(inventory([100]), target_hours=96, elapsed_seconds=97 * 3600,
                           observed_requests_per_second=rate, now=NOW)
    assert result["minimum_required_request_rate"] is None
    assert result["remaining_seconds"] == 0
    assert result["status"] == "behind_target"
    assert result["configured_capacity_sufficient"] is False
    json.dumps(result, allow_nan=False)


def test_missing_inventory_counts_are_explicit_and_duplicate_commander_rows_are_replaced():
    rows = [{"commander_slug": "a", "total_decks": 100}, {"commander_slug": "a", "total_decks": 80},
            {"commander_slug": "b"}]
    result = plan_capacity(rows, observed_requests_per_second=40, now=NOW)
    assert result["commander_count"] == 2
    assert result["estimated_catalog_deck_entries"] == 80
    assert result["unknown_deck_counts"] == 1
    assert result["status"] == "inventory_incomplete"
    assert result["configured_capacity_sufficient"] is None
    assert result["measured_capacity_sufficient"] is None


def test_empty_inventory_can_be_complete_and_stopped_collection_has_no_fabricated_projection():
    assert plan_capacity([], now=NOW)["status"] == "complete"
    result = plan_capacity(inventory([100]), observed_requests_per_second=0, now=NOW)
    assert result["status"] == "unmeasured"
    assert result["projected_finish_at"] is None


@pytest.mark.parametrize("options", [{"target_hours": 0}, {"max_requests_per_second": float("inf")},
    {"effective_requests_per_second": 0}, {"effective_requests_per_second": float("nan")},
    {"cooldown_seconds": -1},
    {"observed_requests_per_second": -1}, {"elapsed_seconds": -1}, {"remaining_previews": 2.5},
    {"profile_requests_per_commander": True}, {"now": datetime(2026, 10, 21)}])
def test_capacity_rejects_invalid_planning_inputs(options):
    with pytest.raises(ValueError):
        plan_capacity(inventory([100]), **options)
