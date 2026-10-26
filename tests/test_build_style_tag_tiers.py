"""Population-relative package labels keep collection and evidence separate."""

import importlib

import pytest

from edhrec_affinity.build_styles.__main__ import collect_evidence, parser
from edhrec_affinity.build_styles.analysis import supported_tag_profiles, tag_label_policy, tag_label_thresholds


@pytest.mark.parametrize("total,counts,expected", [
    (200, (4, 5, 14, 15), [(15, "main"), (14, "supporting"), (5, "supporting")]),
    (1000, (5, 9, 10, 29, 30), [(30, "main"), (29, "supporting"), (10, "supporting")]),
    (1001, (10, 11, 30, 31), [(31, "main"), (30, "supporting"), (11, "supporting")]),
])
def test_collection_applies_both_population_fraction_and_evidence_floor(monkeypatch, total, counts, expected):
    module = importlib.import_module("edhrec_affinity.build_styles.__main__")
    counts = {f"theme-{count}": count for count in counts}
    calls = []

    class Cache:
        blocked = False
        provenance = []

        def __init__(self, *args, **kwargs):
            pass

        def get(self, relative, url, **kwargs):
            calls.append(relative)
            count = total if relative == "base.json" else counts[relative.removesuffix(".json")]
            return {"container": {"json_dict": {"card": {
                "name": "Ghave", "names": ["Ghave"], "num_decks": count,
                "url": "/commanders/ghave" if relative == "base.json" else url,
            }, "cardlists": [{"tag": "topcards", "cardviews": [
                {"name": "Engine", "num_decks": count, "potential_decks": count},
            ]}]}}, "panels": {"taglinks": [{"slug": slug, "value": slug, "count": count}
                                                       for slug, count in counts.items()]}}

        def close(self):
            pass

    class Aliases:
        def groups(self, card):
            return [["Ghave"]]

    monkeypatch.setattr(module, "Cache", Cache)
    args = parser().parse_args(["--commander", "ghave", "--sample-decks", "0"])
    result = collect_evidence(args, commander_alias_provider=Aliases())
    assert calls == ["base.json", *[f"theme-{count}.json" for count, _ in expected]]
    assert [(profile["reported_tag_decks"], profile["support_tier"]) for profile in result["profiles"]] == expected
    assert all(profile["commander_deck_count"] == total
               and profile["deck_fraction"] == profile["reported_tag_decks"] / total for profile in result["profiles"])
    assert result["tag_label_policy"] == tag_label_policy()
    assert result["tag_label_thresholds"] == tag_label_thresholds(total, tag_label_policy())


def test_collection_rejects_explicit_reversed_thresholds_before_source_access():
    args = parser().parse_args(["--commander", "ghave", "--min-tag-decks", "16", "--main-tag-decks", "15"])
    with pytest.raises(ValueError, match="Tag thresholds"):
        collect_evidence(args)


@pytest.mark.parametrize("total,support,main", [(200, 5, 15), (500, 5, 15), (1000, 10, 30),
                                              (1001, 11, 31), (10000, 100, 300)])
def test_percentage_thresholds_round_up_and_preserve_minimum_evidence(total, support, main):
    assert tag_label_thresholds(total, tag_label_policy()) == {
        "commander_deck_count": total, "supporting_minimum_decks": support, "main_minimum_decks": main}


def test_custom_fraction_policy_is_population_independent_and_preserves_exact_boundaries():
    policy = tag_label_policy(10, 20, min_tag_fraction=0.025, main_tag_fraction=0.07)
    assert policy == {"version": 2, "supporting_minimum_decks": 10, "main_minimum_decks": 20,
                      "supporting_minimum_fraction": .025, "main_minimum_fraction": .07, "max_profiles": 12}
    assert tag_label_thresholds(1000, policy) == {"commander_deck_count": 1000,
                                                "supporting_minimum_decks": 25, "main_minimum_decks": 70}
    assert "commander_deck_count" not in policy


@pytest.mark.parametrize("minimum,main", [(0.04, .03), (-.01, .03), (.01, 1.01),
                                        (float("nan"), .03), (.01, float("inf")), (True, .03)])
def test_invalid_fraction_policies_are_rejected(minimum, main):
    with pytest.raises(ValueError, match="Tag fractions"):
        tag_label_policy(min_tag_fraction=minimum, main_tag_fraction=main)


@pytest.mark.parametrize("total", [None, 0, True, "unknown", -1])
def test_legacy_missing_population_does_not_invent_label_evidence(total):
    profile = {"deck_count": 100, "support_tier": "main", "commander_deck_count": 500,
               "deck_fraction": .2}
    assert supported_tag_profiles([profile], commander_deck_count=total) == []
    assert tag_label_thresholds(total, tag_label_policy())["commander_deck_count"] is None


def test_profile_support_uses_commander_total_and_rejects_inconsistent_counts():
    profiles = [{"deck_count": count} for count in (9, 10, 29, 30, 1001)]
    supported = supported_tag_profiles(profiles, commander_deck_count=1000)
    assert [(row["reported_tag_decks"], row["support_tier"]) for row in supported] == [
        (10, "supporting"), (29, "supporting"), (30, "main")]
