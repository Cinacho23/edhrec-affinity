"""Automatic package counts must follow card evidence rather than display limits."""

import copy

import numpy as np
import pytest

from edhrec_affinity.build_styles.analysis import analyze_decks


BASE = {"commander_names": ["Ghave"], "land_keys": [], "cards": {}}


def package_decks(packages=2, per_package=8, hybrids=False, cards_per_package=6):
    decks = []
    combinations = [(i,) for i in range(packages)]
    if hybrids:
        combinations.append(tuple(range(packages)))
    for combination in combinations:
        for i in range(per_package):
            cards = [{"name": f"Engine {package} {card}", "quantity": 1}
                     for package in combination for card in range(cards_per_package)]
            cards.append({"name": f"Unique {combination} {i}", "quantity": 1})
            cards.append({"name": "Forest", "quantity": 99 - len(cards), "type_line": "Basic Land"})
            decks.append({"id": f"{combination}-{i}", "commanders": ["Ghave"], "cards": cards, "tags": []})
    return decks


def analyze(*args, **kwargs):
    pytest.importorskip("sklearn")
    return analyze_decks(*args, **kwargs)


def test_auto_selects_two_overlapping_packages_and_reuses_alternatives():
    result = analyze(package_decks(hybrids=True), BASE, [])
    assert result["status"] == "fitted"
    assert result["components_requested"] is None
    assert result["package_count"] == 2
    assert result["model_selection"]["mode"] == "automatic"
    assert result["model_selection"]["selected_components"] == 2
    assert result["model_selection"]["max_components"] == 2  # Data rank, not hard-coded three.
    assert [row["components"] for row in result["alternative_models"]] == [1, 2]
    assert [row["selected"] for row in result["model_selection"]["candidates"]] == [False, True]
    assert result["overlap"] == [{"left": 0, "right": 1, "deck_count": 8}]
    alternative = result["alternative_models"][1]
    assert len(alternative["packages"]) == 2
    assert [deck["id"] for deck in alternative["memberships"]] == [deck["id"] for deck in result["decks"]]
    np.testing.assert_allclose([deck["weights"] for deck in alternative["memberships"]],
                               [deck["memberships"] for deck in result["decks"]])


def test_auto_can_find_more_than_three_supported_packages():
    result = analyze(package_decks(packages=4, per_package=10), BASE, [], max_components=6)
    assert result["package_count"] == 4
    assert result["model_selection"]["max_components"] == 4
    assert [row["components"] for row in result["model_selection"]["candidates"]] == [1, 2, 3, 4]
    assert len(result["packages"]) == 4
    assert all(len(deck["memberships"]) == 4 for deck in result["decks"])


def test_homogeneous_sample_keeps_one_shared_package_instead_of_forcing_separation():
    result = analyze(package_decks(packages=1), BASE, [])
    assert result["status"] == "fitted"
    assert result["package_count"] == 1
    assert result["feature_filter_mode"] == "shared_cards_fallback"
    assert result["model_selection"]["max_components"] == 1
    assert result["overlap"] == []
    assert all(deck["memberships"] == [1.0] for deck in result["decks"])
    assert "no evidence for separate" in result["model_selection"]["reason"]


def test_max_components_is_a_bound_and_one_is_an_allowed_manual_choice():
    decks = package_decks(packages=4, per_package=10)
    capped = analyze(decks, BASE, [], max_components=2)
    assert capped["model_selection"]["max_components"] == 2
    assert capped["package_count"] <= 2
    manual = analyze(decks, BASE, [], components=1)
    assert manual["package_count"] == 1
    assert manual["model_selection"]["mode"] == "manual"
    assert manual["model_selection"]["selected_components"] == 1
    assert all(deck["memberships"] == [1.0] for deck in manual["decks"])


def test_source_tags_cannot_determine_auto_package_count_or_weights():
    decks = package_decks(hybrids=True)
    original = analyze(decks, BASE, [])
    labeled = copy.deepcopy(decks)
    for index, deck in enumerate(labeled):
        deck["tags"] = ["Fake theme"] * (1 + index % 4)
    changed = analyze(labeled, BASE, [])
    assert original["model_selection"] == changed["model_selection"]
    np.testing.assert_allclose([deck["memberships"] for deck in original["decks"]],
                               [deck["memberships"] for deck in changed["decks"]])


def test_profile_suggestions_can_include_more_than_three_supported_tags():
    decks = package_decks(packages=2, cards_per_package=24)
    baseline = {**BASE, "cards": {f"engine {package} {card}": {"name": f"Engine {package} {card}", "rate": 0.1}
                                  for package in range(2) for card in range(24)}}
    profiles = [{"tag_slug": f"theme-{theme}", "tag_name": f"Theme {theme}",
                 "cards": {key: {"name": card["name"], "rate": 0.5 if key.startswith("engine 0") else 0.1,
                                  "count": 500 if key.startswith("engine 0") else 100, "denominator": 1000}
                           for key, card in baseline["cards"].items()}}
                for theme in range(5)]
    result = analyze(decks, baseline, profiles, components=2)
    matching = next(package for package in result["packages"] if package["top_cards"][0]["name"].startswith("Engine 0"))
    assert len(matching["tag_suggestions"]) == 5
    other = next(package for package in result["packages"] if package is not matching)
    assert other["tag_suggestions"] == []
    assert result["tag_suggestion_rule"]["minimum_cosine"] == 0.2


@pytest.mark.parametrize("components", [0, -1, 1.5, True, "auto"])
def test_invalid_manual_count_is_rejected(components):
    with pytest.raises(ValueError, match="positive integer or None"):
        analyze_decks([], BASE, [], components=components)


@pytest.mark.parametrize("maximum", [0, -1, 1.5, True])
def test_invalid_maximum_is_rejected(maximum):
    with pytest.raises(ValueError, match="max_components must be a positive integer"):
        analyze_decks([], BASE, [], max_components=maximum)


def test_insufficient_data_keeps_selection_schema_without_fitting():
    result = analyze_decks([], BASE, [])
    assert result["status"] == "insufficient_data"
    assert result["package_count"] == 0
    assert result["model_selection"]["selected_components"] is None
    assert result["model_selection"]["candidates"] == []
    assert result["alternative_models"] == []


def test_tiny_extra_package_is_not_offered_as_a_supported_alternative():
    decks = package_decks(packages=3, per_package=10)
    decks = [deck for deck in decks if not deck["id"].startswith("(2,)") or deck["id"].endswith(("-0", "-1"))]
    result = analyze(decks, BASE, [], max_components=3)
    candidate = next(row for row in result["model_selection"]["candidates"] if row["components"] == 3)
    assert candidate["minimum_member_count"] == 2
    assert candidate["supported"] is False
    assert candidate["eligible"] is False
    assert "at least 3" in candidate["reason"]
    assert 3 not in [row["components"] for row in result["alternative_models"]]
    assert result["package_count"] < 3
