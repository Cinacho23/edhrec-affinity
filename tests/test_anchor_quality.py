"""Representative deck selection, including pathological and unusual anchors."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from edhrec_affinity.build_styles import deckbuilding as builder
from edhrec_affinity.build_styles.external_validation import (
    cardlist_hash, external_validation_directory, record_external_validation,
)
from edhrec_affinity.build_styles.sources import card_key


def card(name, types="Creature — Plant", text="", **extra):
    return {"name": name, "type_line": types, "oracle_text": text,
            "color_identity": ["G"], "legalities": {"commander": "legal"},
            "cmc": 2, "game_changer": False, **extra}


def sample(land_counts, weights=None):
    metadata = {f"spell {i}": card(f"Spell {i}") for i in range(99)}
    metadata["forest"] = card("Forest", "Basic Land — Forest", "{T}: Add {G}.", cmc=0)
    metadata["commander"] = card("Commander", "Legendary Creature — Fungus")
    decks, memberships = [], []
    for i, lands in enumerate(land_counts):
        rows = [{"name": f"Spell {j}", "quantity": 1} for j in range(99 - lands)]
        if lands:
            rows.append({"name": "Forest", "quantity": lands})
        identifier = f"anchor-{i:03}"
        decks.append({"id": identifier, "commanders": ["Commander"], "cards": rows, "source_bracket": 3})
        memberships.append({"id": identifier, "weights": [weights[i] if weights else 1]})
    package = {"id": 0, "name": "Package 1", "top_cards": [{"name": "Spell 0"}], "tag_suggestions": []}
    analysis = {"status": "fitted", "packages": [package],
                "decks": [{"id": r["id"], "memberships": r["weights"]} for r in memberships],
                "alternative_models": [{"components": 1, "packages": [deepcopy(package)], "memberships": memberships}]}
    return analysis, decks, {"commander_names": ["Commander"]}, [], metadata


def generate(data, *, explore=False):
    builder.generate_package_decks(*data, prefer_playability=explore)
    return data[0]["packages"][0]["generated_deck"]


def legacy_deck(data, index):
    observed = data[1][index]
    rows = [{"name": "Commander", "quantity": 1}, *deepcopy(observed["cards"])]
    return {"status": "generated", "generation_version": 5, "card_count": 100, "cards": rows,
            "commanders": [{"name": "Commander", "quantity": 1}],
            "statistics": {"anchor_deck_id": observed["id"], "swap_details": [],
                "score_bracket_repair": {"status": "retained", "from_power_level": 6,
                    "score_bracket": 2, "swap_details": []}},
            "validation": {"external_url": "https://edhpowerlevel.com/?d=test"}}


def record_rating(deck, directory):
    record_external_validation(deck, {"source": "edhpowerlevel.com", "source_url": "https://edhpowerlevel.com/?d=test",
        "card_count": 100, "checked_at": datetime.now(timezone.utc).isoformat(),
        "playability_pct": 75, "power_level": 6, "bracket": 2}, directory)


@pytest.mark.parametrize("lands", range(100))
def test_single_anchor_conservative_envelope_and_empty_spell_rejection(lands):
    data = sample([lands])
    result = generate(data)
    if 18 <= lands <= 60:
        assert result["status"] == "generated" and result["card_count"] == 100
        assert result["statistics"]["land_count"] == lands
        assert result["validation"]["checks"]["anchor_quality"]
    else:
        assert result["status"] == "unavailable"
        assert result["reason_code"] == "anchor_quality"
        assert result["anchor_quality"]["checked"] == result["anchor_quality"]["rejected"] == 1
        assert result["cards"] == []
        assert builder.generate_package_deck_candidates(*data) == []


@pytest.mark.parametrize("explore", [False, True])
@pytest.mark.parametrize("bad_weight", [1, 100, .01])
def test_ur_dragon_shape_never_wins_even_with_strong_membership_and_retained_site_rating(tmp_path, explore, bad_weight):
    data = sample([95, 36, 37, 38, 36, 35, 37], [bad_weight, *([.01] * 6)])
    old = legacy_deck(data, 0)
    for package in [data[0]["packages"][0], data[0]["alternative_models"][0]["packages"][0]]:
        package["generated_deck"] = deepcopy(old)
    record_rating(old, tmp_path)
    with external_validation_directory(tmp_path):
        result = generate(data, explore=explore)
        candidates = builder.generate_package_deck_candidates(*data, max_candidates=8)
    assert result["validation"]["valid"] and result["card_count"] == 100
    assert 35 <= result["statistics"]["land_count"] <= 38
    assert result["statistics"]["anchor_deck_id"] != "anchor-000"
    assert result["statistics"]["anchor_screening"]["rejected"] == 1
    assert result["distinctive_cards"]["included_count"] == 1
    assert result["validation"]["external_rating"]["status"] == "unverified"
    assert cardlist_hash(result["cards"]) != cardlist_hash(old["cards"])
    assert candidates and all(35 <= c["statistics"]["land_count"] <= 38 for c in candidates)
    alternate = data[0]["alternative_models"][0]["packages"][0]["generated_deck"]
    assert alternate["cards"] == result["cards"]


@pytest.mark.parametrize("lands", [50, 60, 65, 70, 73, 79])
@pytest.mark.parametrize("weight", [1, 1e-20, 1e100])
def test_supported_land_heavy_packages_and_weight_scale_invariance(lands, weight):
    result = generate(sample([lands] * 6, [weight] * 6))
    assert result["status"] == "generated"
    quality = result["statistics"]["anchor_quality"]
    assert quality["accepted"] and quality["tier"] == 0
    assert quality["reference"]["basis"] == "membership_weighted_package"
    assert quality["reference"]["effective_anchors"] == pytest.approx(6)
    assert result["statistics"]["land_count"] == lands


def test_rare_land_heavy_outlier_is_screened_without_deleting_observed_evidence():
    data = sample([36] * 10 + [72], [1] * 10 + [.001])
    before = deepcopy(data)
    context = {}
    indexed = builder._metadata_index(data[4])
    prepared = builder._prepare_package(data[0]["packages"][0],
        data[0]["alternative_models"][0]["memberships"], data[1], data[2], data[3], indexed, _source_context=context)
    assert len(prepared["anchors"]) == 10 and len(context["prepared"]["observed"]) == 11
    assert prepared["support"]["forest"] == 1
    assert data == before
    result = generate(data)
    assert result["statistics"]["land_count"] == 36
    assert data[1:] == before[1:]
    assert data[0]["decks"] == before[0]["decks"]


def test_one_dominant_anchor_or_duplicate_ids_cannot_fabricate_package_evidence():
    data = sample([70] * 6, [1, 1e-9, 1e-9, 1e-9, 1e-9, 1e-9])
    assert generate(data)["status"] == "unavailable"
    data = sample([70] * 6)
    for row in data[1]:
        row["id"] = "same"
    data[0]["decks"] = [{"id": "same", "memberships": [1]}]
    data[0]["alternative_models"][0]["memberships"] = [{"id": "same", "weights": [1]}]
    result = generate(data)
    assert result["status"] == "unavailable"
    assert result["anchor_quality"]["reference"]["observed_anchors"] == 1


def test_each_package_uses_its_own_weighted_land_distribution():
    data = sample([36] * 5 + [70] * 5, [1] * 5 + [.001] * 5)
    for i, row in enumerate(data[0]["alternative_models"][0]["memberships"]):
        row["weights"] = [.001 if i < 5 else 1]
    result = generate(data)
    assert result["statistics"]["land_count"] == 36
    alternate = data[0]["alternative_models"][0]["packages"][0]["generated_deck"]
    assert alternate["statistics"]["land_count"] == 70
    assert alternate["statistics"]["anchor_quality"]["accepted"]


def modalize(data, number, *, layout="modal_dfc"):
    for i in range(number):
        metadata = data[4][f"spell {i}"]
        metadata.update(type_line="Sorcery // Land", layout=layout, produced_mana=["G"], card_faces=[
            {"name": f"Spell {i}", "type_line": "Sorcery", "oracle_text": "Draw a card."},
            {"name": f"Land {i}", "type_line": "Land", "oracle_text": "{T}: Add {G}."}])


@pytest.mark.parametrize("modal_count", [18, 36, 60, 99])
def test_zero_physical_land_mdfc_decks_have_real_mana_slots(modal_count):
    data = sample([0] * 5)
    modalize(data, modal_count)
    result = generate(data, explore=True)
    assert result["status"] == "generated"
    assert result["statistics"]["land_count"] == 0
    quality = result["statistics"]["anchor_quality"]
    assert quality["modal_land_count"] == modal_count and quality["spell_count"] == 99
    assert result["card_count"] == 100


@pytest.mark.parametrize("layout", ["transform", "adventure", "normal"])
def test_a_land_on_an_unplayable_back_face_is_not_an_opening_mana_source(layout):
    data = sample([0] * 5)
    modalize(data, 36, layout=layout)
    for i in range(36):
        data[4][f"spell {i}"]["type_line"] = "Enchantment // Land"
    assert generate(data)["status"] == "unavailable"


@pytest.mark.parametrize("kind,lands,sources,accepted", [
    ("rock", 10, 16, True), ("rock", 10, 3, False), ("rock", 0, 30, False),
    ("dork", 12, 12, True), ("cost_reducer", 10, 16, False),
    ("ritual", 10, 16, False), ("expensive_rock", 10, 16, False),
])
def test_low_land_mana_credit_requires_early_permanent_sources(kind, lands, sources, accepted):
    data = sample([lands])
    for i in range(sources):
        m = data[4][f"spell {i}"]
        m.update(type_line="Creature — Elf" if kind == "dork" else "Sorcery" if kind == "ritual" else "Artifact",
                 oracle_text="Artifact spells you cast cost {1} less to cast." if kind == "cost_reducer" else "{T}: Add {G}.",
                 cmc=5 if kind == "expensive_rock" else 1)
    result = generate(data)
    assert (result["status"] == "generated") == accepted


@pytest.mark.parametrize("explore", [False, True])
def test_good_structure_beats_a_verified_borderline_retained_repair(tmp_path, explore):
    data = sample([55, 36], [1, .1])
    old = legacy_deck(data, 0)
    for p in [data[0]["packages"][0], data[0]["alternative_models"][0]["packages"][0]]:
        p["generated_deck"] = deepcopy(old)
    record_rating(old, tmp_path)
    with external_validation_directory(tmp_path):
        result = generate(data, explore=explore)
    assert result["statistics"]["land_count"] == 36
    assert result["statistics"]["anchor_quality"]["tier"] == 0
    assert result["validation"]["external_rating"]["status"] == "unverified"


def test_quality_preferences_do_not_override_current_bracket_restrictions():
    normal = generate(sample([36]))
    borderline = generate(sample([55]))
    normal["validation"]["bracket"]["rule_checks"]["mass_land_denial"] = False
    result = builder._prefer_playability([normal, borderline])
    assert result["statistics"]["land_count"] == 55
    assert all(result["validation"]["bracket"]["rule_checks"].values())


@pytest.mark.parametrize("explore", [False, True])
def test_pregame_color_variants_use_the_same_bracket_safety_priority(monkeypatch, explore):
    data = sample([36])
    normal = generate(sample([36]))
    borderline = generate(sample([55]))
    normal["validation"]["bracket"]["rule_checks"]["mass_land_denial"] = False
    monkeypatch.setattr(builder, "_commander_color_options", lambda *args: ["G", "R"])

    def variant(analysis, decks, base, *args, **kwargs):
        result = normal if base["commander_color_choices"] == "G" else borderline
        for package in builder._all_packages(analysis):
            package["generated_deck"] = deepcopy(result)
        return analysis

    monkeypatch.setattr(builder, "_generate_package_decks", variant)
    result = generate(data, explore=explore)
    assert result["statistics"]["land_count"] == 55
    assert all(result["validation"]["bracket"]["rule_checks"].values())


def test_all_invalid_candidates_are_unavailable_even_with_external_target_claims():
    option = generate(sample([36]))
    option["statistics"]["anchor_quality"]["accepted"] = False
    option["validation"]["external_rating"] = {"status": "verified", "playability_pct": 75}
    result = builder._prefer_playability([option])
    assert result["status"] == "unavailable" and result["reason_code"] == "anchor_quality"


@pytest.mark.parametrize("bad_quantity", [0, -1, True, "36", 37])
def test_quality_does_not_relax_complete_legal_anchor_requirements(bad_quantity):
    data = sample([36, 37])
    data[1][0]["cards"][-1]["quantity"] = bad_quantity
    result = generate(data)
    assert result["statistics"]["anchor_deck_id"] == "anchor-001"
    assert result["card_count"] == 100 and result["validation"]["valid"]


def test_quality_results_are_deterministic_and_generation_is_versioned():
    data = sample([95, 35, 36, 37, 38, 36, 35])
    reversed_data = deepcopy(data)
    reversed_data[1].reverse()
    assert generate(data) == generate(reversed_data)
    assert data[0]["deck_generation"]["version"] == builder.DECK_GENERATION_VERSION == 6


@pytest.mark.parametrize("lands,accepted", [(78, True), (79, False)])
def test_partner_commanders_do_not_count_toward_minimum_spell_slots(lands, accepted):
    data = sample([lands] * 6)
    data[4]["commander"]["oracle_text"] = "Partner"
    data[4]["partner"] = card("Partner", "Legendary Creature — Elf", "Partner")
    data[2]["commander_names"] = ["Commander", "Partner"]
    for observed in data[1]:
        observed["commanders"] = ["Commander", "Partner"]
        observed["cards"].pop(0)
    result = generate(data)
    assert (result["status"] == "generated") == accepted
    if accepted:
        assert result["statistics"]["anchor_quality"]["spell_count"] == 20
        assert len(result["commanders"]) == 2 and result["card_count"] == 100


def test_singleton_exceptions_are_spell_slots_not_a_false_low_variety_rejection():
    data = sample([20])
    data[4]["petitioners"] = card("Petitioners", text="A deck can have any number of cards named Petitioners.")
    data[1][0]["cards"] = [{"name": "Forest", "quantity": 20}, {"name": "Petitioners", "quantity": 79}]
    result = generate(data)
    assert result["status"] == "generated" and result["validation"]["checks"]["singleton"]
    assert result["statistics"]["anchor_quality"]["spell_count"] == 79


def test_forced_bad_anchor_cannot_bypass_screening():
    data = sample([95, 36])
    indexed = builder._metadata_index(data[4])
    result = builder._generate_one(data[0]["packages"][0], data[0]["alternative_models"][0]["memberships"],
        data[1], data[2], data[3], indexed, anchor_id="anchor-000")
    assert result["status"] == "unavailable" and result["reason_code"] == "anchor_quality"


def test_distinctive_insertion_cannot_remove_the_last_required_modal_land_slot():
    data = sample([0] * 3)
    modalize(data, 18)
    data[4]["desired"] = card("Desired", "Enchantment", "Draw a card.")
    data[1].append({"id": "pool-only", "commanders": ["Commander"], "cards": [{"name": "Desired", "quantity": 1}]})
    data[0]["decks"].append({"id": "pool-only", "memberships": [.01]})
    data[0]["packages"][0]["top_cards"] = [{"name": "Desired"}]
    result = generate(data)
    assert result["distinctive_cards"]["included_count"] == 1
    assert result["statistics"]["anchor_quality"]["modal_land_count"] == 18
    assert result["validation"]["checks"]["anchor_quality"]


def test_bracket_repair_cannot_commit_a_list_that_loses_required_modal_mana(tmp_path):
    data = sample([0] * 3)
    modalize(data, 18)
    data[4]["spell 0"]["card_faces"][0]["oracle_text"] = "Destroy all lands."
    original = generate(deepcopy(data))
    record_rating(original, tmp_path)
    with external_validation_directory(tmp_path):
        result = generate(data)
    assert result["statistics"]["score_bracket_repair"]["status"] == "blocked"
    assert cardlist_hash(result["cards"]) == cardlist_hash(original["cards"])
    assert result["statistics"]["anchor_quality"]["modal_land_count"] == 18
    assert result["statistics"]["score_bracket_repair"]["swap_details"] == []


def test_recorded_ur_dragon_77_island_export_fails_quality_while_other_packages_pass():
    import json
    from pathlib import Path
    snapshot = json.loads((Path(__file__).parent / 'fixtures/ur_dragon_95_lands.json').read_text())
    metadata, counts = {}, Counter()
    for row in snapshot["cards"]:
        key = card_key(row["name"])
        metadata[key] = card(row["name"], "Legendary Creature — Dragon" if row["primary_role"] == "Commander"
                             else "Basic Land — Island" if row["name"] == "Island"
                             else "Land" if row["primary_role"] == "Lands" else "Creature")
        counts[key] = row["quantity"]
    bad = builder._anchor_facts(counts, metadata, {"the ur-dragon"})
    assert bad["land_count"] == 95 and bad["spell_count"] == 4 and counts["island"] == 77
    references = [{"id": f"package-{i}"} for i in range(len(snapshot["other_package_land_counts"]))]
    facts = {id(row): {"land_count": lands, "spell_count": 99 - lands, "mana_equivalent": lands,
                      "modal_land_count": 0, "early_mana_sources": 0}
             for row, lands in zip(references, snapshot["other_package_land_counts"])}
    reference = builder._quality_reference(references, {row["id"]: 1 for row in references}, facts)
    quality = builder._anchor_quality(bad, reference)
    assert not quality["accepted"] and not quality["checks"]["nonland_spell_slots"]
    assert all(builder._anchor_quality(value, reference)["accepted"] for value in facts.values())


def test_package_preparation_reuses_anchor_facts_across_large_alternative_models(monkeypatch):
    data = sample([36] * 1500)
    metadata = builder._metadata_index(data[4])
    calls = []
    original = builder._anchor_facts
    def measured(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(builder, "_anchor_facts", measured)
    context = {}
    memberships = data[0]["alternative_models"][0]["memberships"]
    for _ in range(3):
        prepared = builder._prepare_package(data[0]["packages"][0], memberships, data[1], data[2], data[3],
                                            metadata, _source_context=context)
        assert len(prepared["anchors"]) == 1500
    assert len(calls) == 1500


@pytest.mark.parametrize("bad_weight", [None, [], float('nan'), float('inf'), -1, 0, 'invalid'])
def test_candidate_search_reuses_validated_memberships_for_quality_screened_anchors(bad_weight):
    data = sample([36, 37])
    data[0]["decks"][0]["memberships"] = [bad_weight]
    candidates = builder.generate_package_deck_candidates(*data, max_candidates=4)
    assert candidates and all(x["statistics"]["anchor_deck_id"] == "anchor-001" for x in candidates)


def test_primary_search_prefers_a_bracket_compliant_anchor_before_quality_tier():
    data = sample([36, 55], [1, .001])
    # Only the 36-land list contains this card. The 55-land list remains a
    # permissible fallback even though its shape is the secondary quality tier.
    data[4]["spell 60"]["oracle_text"] = "Destroy all lands."
    result = generate(data, explore=False)
    assert result["statistics"]["land_count"] == 55
    assert result["validation"]["bracket"]["rule_checks"]["mass_land_denial"]
    assert result["statistics"]["anchor_quality"]["accepted"]
