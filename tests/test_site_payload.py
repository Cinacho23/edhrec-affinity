from copy import deepcopy
import math
import random

from edhrec_affinity.site_payload import project_build_report, _display_percentage


def test_projection_preserves_all_models_and_deck_fields_and_only_omits_request_audit_history():
    report = {"collection": {"complete": True, "freshness": {"oldest_source_at": "2026-11-01"},
                             "provenance": [{"request": "private checkpoint audit"}]},
              "deck_analysis": {"membership_threshold": 0.25,
                "packages": [{"generated_deck": {"score": 6.699999999,
                    "primer": "[[Card Name]]", "cards": [{"average_lift": 1.2345678901234567}]}}],
                "decks": [{"id": "one", "source_url": "https://example.test/deck", "memberships": [0.123456789]}],
                "alternative_models": [{"components": count, "packages": list(range(count)),
                    "memberships": [{"id": "one", "weights": [0.123456789] * count}]} for count in range(1, 28)]}}
    original = deepcopy(report)
    result = project_build_report(report)
    assert result["collection"] == {k: v for k, v in original["collection"].items() if k != "provenance"}
    assert result["deck_analysis"]["packages"] == original["deck_analysis"]["packages"]
    assert result["deck_analysis"]["decks"][0] == {**original["deck_analysis"]["decks"][0], "memberships": [0.123457]}
    assert len(result["deck_analysis"]["alternative_models"]) == 27
    for before, after in zip(original["deck_analysis"]["alternative_models"], result["deck_analysis"]["alternative_models"]):
        assert after["components"] == before["components"]
        assert after["packages"] == before["packages"]
        assert after["memberships"][0]["weights"] == [0.123457] * before["components"]


def test_rounding_never_changes_membership_decisions_percentage_displays_or_ordering():
    rng = random.Random(42)
    weights = [[rng.random(), rng.random()] for _ in range(1000)]
    weights += [[value, 0.5] for value in [0, 1, 0.24999999, 0.25000001, 0.00499999, 0.00500001,
                                         0.1234561, 0.1234562, 0.1234563, 0.00000001, 0.00000002,
                                         math.nextafter(0.005, 0), math.nextafter(0.145, 0)]]
    report = {"deck_analysis": {"membership_threshold": 0.25,
        "decks": [{"id": str(i), "memberships": values} for i, values in enumerate(weights)]}}
    original = deepcopy(report)
    project_build_report(report)
    before = original["deck_analysis"]["decks"]
    after = report["deck_analysis"]["decks"]
    for col in range(2):
        assert sorted(range(len(before)), key=lambda i: before[i]["memberships"][col]) == sorted(
            range(len(after)), key=lambda i: after[i]["memberships"][col])
    for old, new in zip(before, after):
        for a, b in zip(old["memberships"], new["memberships"]):
            assert abs(a - b) <= 0.000000500001
            assert (a >= 0.25) == (b >= 0.25)
            assert _display_percentage(a) == _display_percentage(b)


def test_projection_handles_insufficient_reports_without_fabricating_a_fit():
    report = {"deck_analysis": {"status": "insufficient_data", "packages": [], "decks": []}}
    original = deepcopy(report)
    project_build_report(report)
    assert report["deck_analysis"] == original["deck_analysis"]
