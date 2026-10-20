"""Construction safety and data-driven choices, independent of the NMF fitter."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

import pytest

from edhrec_affinity.build_styles.deckbuilding import (
    functional_tags, generate_package_deck_candidates, generate_package_decks,
)
from edhrec_affinity.build_styles.sources import card_key


def metadata_card(name, *, types="Creature — Plant", text="", colors=None, cmc=2, **extra):
    return {"name": name, "type_line": types, "oracle_text": text,
            "color_identity": ["G"] if colors is None else colors,
            "legalities": {"commander": "legal"}, "cmc": cmc, "game_changer": False, **extra}


def fixture():
    names = [f"Core {index}" for index in range(62)] + ["Weak Card", "Consensus Card", "Variant B", "Variant C"]
    metadata = {card_key(name): metadata_card(name) for name in names}
    metadata["commander"] = metadata_card("Commander", types="Legendary Creature — Fungus")
    metadata["forest"] = metadata_card("Forest", types="Basic Land — Forest", cmc=0)
    decks = []
    for identifier, replacement, unique, weight in (
            ("a", "Weak Card", None, 1.0), ("b", "Consensus Card", "Variant B", .8),
            ("c", "Consensus Card", "Variant C", .8)):
        cards = [{"name": f"Core {index}", "quantity": 1} for index in range(62)]
        if unique:
            cards[-1 if identifier == "b" else -2]["name"] = unique
        cards += [{"name": replacement, "quantity": 1}, {"name": "Forest", "quantity": 36}]
        decks.append({"id": identifier, "commanders": ["Commander"], "cards": cards,
                      "source_url": f"https://example.test/{identifier}", "source_bracket": 3, "weight": weight})
    package = {"id": 0, "name": "Package 1", "tag_suggestions": [{"tag_slug": "plants", "tag_name": "Plants"}],
               "top_cards": [{"name": "Core 0"}]}
    memberships = [{"id": deck["id"], "weights": [deck["weight"]]} for deck in decks]
    analysis = {"status": "fitted", "packages": [package],
                "decks": [{"id": row["id"], "memberships": row["weights"]} for row in memberships],
                "alternative_models": [{"components": 1, "packages": [deepcopy(package)], "memberships": memberships}]}
    base = {"commander_names": ["Commander"], "cards": {"consensus card": {"rate": .1, "lift": 99}}}
    profiles = [{"tag_slug": "plants", "tag_name": "Plants", "cards": {
        "consensus card": {"rate": .8, "lift": 4}, "core 0": {"rate": .4, "lift": 2}}},
        {"tag_slug": "bracket-3", "bracket": 3, "cards": {"consensus card": {"rate": .6, "lift": 2}}},
        {"tag_slug": "irrelevant", "tag_name": "Ignored", "cards": {"consensus card": {"rate": 1, "lift": 100}}},
        {"tag_slug": "bracket-5", "bracket": 5, "cards": {"consensus card": {"rate": 1, "lift": 100}}}]
    return analysis, decks, base, profiles, metadata


def generate(data):
    analysis, decks, base, profiles, metadata = data
    generate_package_decks(analysis, decks, base, profiles, metadata)
    return analysis["packages"][0]["generated_deck"]


def test_membership_consensus_preserves_real_structure_and_only_uses_sampled_cards():
    data = fixture()
    result = generate(data)
    assert result["status"] == "generated"
    assert result["validation"]["valid"]
    assert all(result["validation"]["checks"].values())
    assert result["card_count"] == sum(row["quantity"] for row in result["cards"]) == 100
    cards = {row["name"]: row for row in result["cards"]}
    assert cards["Forest"]["quantity"] == 36
    assert "Consensus Card" in cards and "Weak Card" not in cards
    assert "Core 0" in cards
    assert result["statistics"]["anchor_deck_id"] == "a"
    assert result["statistics"]["swaps"] >= 1
    assert result["statistics"]["land_count"] == 36
    assert set(cards) <= {card["name"] for deck in data[1] for card in deck["cards"]} | {"Commander"}
    assert data[0]["alternative_models"][0]["packages"][0]["generated_deck"] == result
    assert generate(fixture()) == result


def test_metrics_average_raw_edhrec_lift_and_missing_zero_on_matched_theme_plus_bracket_only():
    data = fixture()
    # ranking_cards include measurements filtered out of mature NMF profiles.
    data[3][0]["ranking_cards"] = {"consensus card": {"rate": .8, "lift": 4}}
    result = generate(data)
    cards = {row["name"]: row for row in result["cards"]}
    assert cards["Consensus Card"]["average_inclusion"] == pytest.approx(.7)
    assert cards["Consensus Card"]["average_lift"] == pytest.approx(3)
    assert cards["Consensus Card"]["lift_observed_profiles"] == 2
    assert cards["Core 1"]["average_inclusion"] == 0
    assert cards["Core 1"]["average_lift"] == 0
    assert cards["Core 1"]["lift_observed_profiles"] == 0
    assert result["statistics"]["profile_slugs"] == ["plants", "bracket-3"]
    assert result["statistics"]["profile_names"] == ["Plants", "bracket-3"]
    assert result["statistics"]["highest_average_lift"] == {"name": "Consensus Card", "lift": 3}
    assert "raw EDHREC ratio" in result["statistics"]["metric_note"]


def test_missing_authoritative_metadata_or_incomplete_anchor_does_not_invent_a_deck():
    data = fixture()
    assert generate_package_decks(data[0], data[1], data[2], data[3])["packages"][0]["generated_deck"]["status"] == "unavailable"
    data = fixture()
    del data[4]["forest"]
    assert generate(data)["status"] == "unavailable"
    data = fixture()
    for deck in data[1]:
        deck["cards"][-1]["quantity"] -= 1
    result = generate(data)
    assert result["status"] == "unavailable"
    assert result["cards"] == []


@pytest.mark.parametrize("violation", ["banned", "off_color", "duplicate", "ineligible_commander"])
def test_illegal_anchors_never_generate_verified_decks(violation):
    data = fixture()
    if violation == "banned":
        data[4]["core 1"]["legalities"]["commander"] = "banned"
    elif violation == "off_color":
        data[4]["core 1"]["color_identity"] = ["R"]
    elif violation == "duplicate":
        for deck in data[1]:
            deck["cards"][1]["quantity"] = 2
            deck["cards"][-1]["quantity"] = 35
    else:
        data[4]["commander"]["type_line"] = "Creature — Fungus"
    assert generate(data)["status"] == "unavailable"


def test_package_defining_cards_are_not_replaced_by_generic_high_frequency_cards():
    data = fixture()
    data[0]["packages"][0]["top_cards"] = [{"name": "Weak Card"}]
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    assert "Weak Card" in names
    assert "Consensus Card" not in names


def test_export_preserves_multiple_tags_and_full_name_primer_references():
    data = fixture()
    data[4]["consensus card"]["oracle_text"] = "{T}: Add {G}. Whenever a Plant enters, draw a card."
    data[4]["weak card"]["oracle_text"] = "{T}: Add {G}. Whenever a Plant enters, draw a card."
    result = generate(data)
    assert "1 Consensus Card #Ramp #Card Advantage #Plants" in result["export_text"]
    assert "1 Commander *CMDR* #Commander" in result["export_text"]
    assert "//" not in result["export_text"]
    assert "[[Consensus Card]]" in result["primer"] and "[[Commander]]" in result["primer"]
    assert result["primer"].startswith("[[Commander]] — Package 1")
    without_references = re.sub(r"\[\[[^\]]+\]\]", "", result["primer"])
    assert all(row["name"] not in without_references for row in result["cards"])
    assert "not been playtested" in result["primer"]
    assert result["validation"]["external_url"].startswith("https://edhpowerlevel.com?d=")
    assert "%23" not in result["validation"]["external_url"]


@pytest.mark.parametrize(("name", "text", "types", "cmc", "expected"), [
    ("Sol Ring", "{T}: Add {C}{C}.", "Artifact", 1, {"Fast Mana", "Ramp"}),
    ("Llanowar Elves", "{T}: Add {G}.", "Creature — Elf Druid", 1, {"Ramp"}),
    ("Harmonize", "Draw three cards.", "Sorcery", 4, {"Card Advantage"}),
    ("Sylvan Library", "At the beginning of your draw step, you may draw two additional cards.", "Enchantment", 2, {"Card Advantage"}),
    ("Light Up the Stage", "Exile the top two cards of your library. Until the end of your next turn, you may play those cards.", "Sorcery", 3, {"Card Advantage"}),
    ("Return of the Wildspeaker", "Draw cards equal to the greatest power among non-Human creatures you control.", "Instant", 5, {"Card Advantage"}),
    ("Wrath of God", "Destroy all creatures. They can't be regenerated.", "Sorcery", 4, {"Wipe", "Removal"}),
    ("Blasphemous Act", "Blasphemous Act deals 13 damage to each creature.", "Sorcery", 9, {"Wipe", "Removal"}),
    ("Lightning Bolt", "Lightning Bolt deals 3 damage to any target.", "Instant", 1, {"Removal"}),
    ("Demonic Tutor", "Search your library for a card, put that card into your hand, then shuffle.", "Sorcery", 2, {"Tutor"}),
    ("Rampant Growth", "Search your library for a basic land card, put that card onto the battlefield tapped, then shuffle.", "Sorcery", 2, {"Ramp"}),
    ("Nature's Lore", "Search your library for a Forest card, put that card onto the battlefield, then shuffle.", "Sorcery", 2, {"Ramp"}),
    ("Eternal Witness", "When this creature enters, you may return target card from your graveyard to your hand.", "Creature — Human Shaman", 3, {"Recursion"}),
    ("Heroic Intervention", "Permanents you control gain hexproof and indestructible until end of turn.", "Instant", 2, {"Protection"}),
    ("Grizzly Bears", "", "Creature — Bear", 2, {"Leftover"}),
])
def test_functional_roles_cover_actual_cards_without_turning_every_mana_dork_into_fast_mana(name, text, types, cmc, expected):
    assert set(functional_tags(metadata_card(name, text=text, types=types, cmc=cmc))) == expected


def test_partner_commanders_and_textual_singleton_exceptions_are_validated():
    data = fixture()
    data[2]["commander_names"] = ["Commander", "Partner"]
    data[4]["commander"]["oracle_text"] = "Partner"
    data[4]["partner"] = metadata_card("Partner", types="Legendary Creature — Elf", text="Partner", colors=["U"])
    for deck in data[1]:
        deck["commanders"] = ["Commander", "Partner"]
        deck["cards"][-1]["quantity"] = 35
    result = generate(data)
    assert result["status"] == "generated" and len(result["commanders"]) == 2
    assert result["card_count"] == 100
    data[4]["partner"]["oracle_text"] = ""
    assert generate(data)["status"] == "unavailable"
    data = fixture()
    data[4]["core 1"]["oracle_text"] = "A deck can have up to seven cards named Core 1."
    for deck in data[1]:
        deck["cards"][1]["quantity"] = 7
        deck["cards"][-1]["quantity"] = 30
    assert generate(data)["status"] == "generated"
    for deck in data[1]:
        deck["cards"][1]["quantity"] = 8
        deck["cards"][-1]["quantity"] = 29
    assert generate(data)["status"] == "unavailable"


def test_bracket_guidance_uses_source_memberships_and_raises_for_checked_restrictions():
    data = fixture()
    for deck in data[1]:
        deck["source_bracket"] = 2
    data[4]["core 0"]["game_changer"] = True
    result = generate(data)
    bracket = result["validation"]["bracket"]
    assert bracket["target"] == 2 and bracket["recommended"] == 4
    assert bracket["game_changer_count"] == 1
    assert not bracket["rule_checks"]["game_changers"]
    assert "not fully evaluated" in bracket["notes"][0]
    data = fixture()
    for deck in data[1]:
        deck.pop("source_bracket")
    bracket = generate(data)["validation"]["bracket"]
    assert bracket["source"] == "default" and bracket["target"] == 3


def test_obvious_mass_land_denial_and_extra_turns_raise_lower_target_guidance():
    for text in ("Destroy all lands.", "Take an extra turn after this one."):
        data = fixture()
        data[4]["core 0"]["oracle_text"] = text
        result = generate(data)
        assert result["validation"]["bracket"]["recommended"] == 4


def test_different_alternative_memberships_generate_their_own_deck():
    data = fixture()
    alternative = data[0]["alternative_models"][0]
    alternative["memberships"] = [{"id": "a", "weights": [1]}, {"id": "b", "weights": [.01]}, {"id": "c", "weights": [.01]}]
    generate(data)
    primary = data[0]["packages"][0]["generated_deck"]
    alternative = alternative["packages"][0]["generated_deck"]
    assert "Consensus Card" in {row["name"] for row in primary["cards"]}
    assert "Weak Card" in {row["name"] for row in alternative["cards"]}


def test_dfc_front_name_resolves_to_full_card_without_accepting_back_face_or_split_half():
    data = fixture()
    card = data[4].pop("core 0")
    card.update({"name": "Core 0 // Back Face", "layout": "modal_dfc",
                 "type_line": "Creature — Plant // Land", "card_faces": [
                     {"name": "Core 0", "oracle_text": ""}, {"name": "Back Face", "oracle_text": "{T}: Add {G}."}]})
    data[4]["core 0 // back face"] = card
    result = generate(data)
    assert result["status"] == "generated"
    assert "Core 0 // Back Face" in {row["name"] for row in result["cards"]}
    assert result["statistics"]["land_count"] == 36
    for deck in data[1]:
        deck["cards"][0]["name"] = "Back Face"
    assert generate(data)["status"] == "unavailable"
    card["layout"] = "split"
    for deck in data[1]:
        deck["cards"][0]["name"] = "Core 0"
    assert generate(data)["status"] == "unavailable"


def test_current_rules_allow_legendary_vehicles_but_not_arbitrary_legendary_artifacts():
    data = fixture()
    data[4]["commander"]["type_line"] = "Legendary Artifact — Vehicle"
    assert generate(data)["status"] == "generated"
    data[4]["commander"]["type_line"] = "Legendary Artifact"
    assert generate(data)["status"] == "unavailable"


def test_wipes_and_ramp_are_preserved_when_consensus_cards_have_different_roles():
    data = fixture()
    data[4]["weak card"]["oracle_text"] = "Destroy all creatures."
    result = generate(data)
    assert "Weak Card" in {row["name"] for row in result["cards"]}
    weak = next(row for row in result["cards"] if row["name"] == "Weak Card")
    assert weak["primary_role"] == "Wipe"


def test_prepared_front_name_resolves_without_stealing_a_real_spell_name():
    data = fixture()
    prepared = data[4].pop("core 0")
    prepared.update({"name": "Core 0 // Reanimate", "layout": "prepare", "card_faces": [
        {"name": "Core 0", "oracle_text": ""}, {"name": "Reanimate", "oracle_text": "Return target creature card from a graveyard to the battlefield."}]})
    data[4]["core 0 // reanimate"] = prepared
    data[4]["reanimate"] = metadata_card("Reanimate", types="Sorcery", text="Return target creature card from a graveyard to the battlefield.")
    result = generate(data)
    assert result["status"] == "generated"
    assert "Core 0 // Reanimate" in {row["name"] for row in result["cards"]}
    assert "Reanimate" not in {row["name"] for row in result["cards"]}
    # A real canonical card wins over a conflicting front alias, whichever
    # insertion order the caller's metadata mapping happens to use.
    data = fixture()
    conflicting = metadata_card("Core 0 // Unrelated", types="Creature — Plant // Sorcery", layout="prepare",
                               card_faces=[{"name": "Core 0", "oracle_text": ""}, {"name": "Unrelated", "oracle_text": ""}])
    data[4]["conflicting prepared card"] = conflicting
    assert "Core 0" in {row["name"] for row in generate(data)["cards"]}


def test_null_game_changer_annotation_is_reported_as_incomplete():
    data = fixture()
    data[4]["core 0"]["game_changer"] = None
    bracket = generate(data)["validation"]["bracket"]
    assert any("annotations are incomplete" in note for note in bracket["notes"])


def test_candidate_search_is_bounded_deterministic_and_keeps_the_model_unchanged():
    data = fixture()
    original = deepcopy(data[0])
    candidates = generate_package_deck_candidates(*data, max_candidates=2)
    assert 0 < len(candidates) <= 2
    assert all(candidate["card_count"] == 100 and candidate["validation"]["valid"] for candidate in candidates)
    assert data[0] == original
    assert generate_package_deck_candidates(*data, max_candidates=2) == candidates
    assert "not an EDHPowerLevel percentage" in candidates[0]["statistics"]["mana_quality"]["interpretation"]
    assert generate_package_deck_candidates(*data, components=99) == []
    assert generate_package_deck_candidates(*data, components=1)
    with pytest.raises(ValueError):
        generate_package_deck_candidates(*data, max_candidates=0)


def test_sparse_mana_candidates_only_add_sampled_basic_types_and_keep_all_100_cards_legal():
    data = fixture()
    for index in range(7):
        name = f"Extra Support {index}"
        data[4][card_key(name)] = metadata_card(name)
        for deck in data[1]:
            deck["cards"].append({"name": name, "quantity": 1})
    for deck in data[1]:
        next(card for card in deck["cards"] if card["name"] == "Forest")["quantity"] = 29
    candidates = generate_package_deck_candidates(*data, max_candidates=4)
    assert candidates
    tuned = [candidate for candidate in candidates if candidate["statistics"]["land_adjustments"]]
    assert tuned
    source_names = {card["name"] for deck in data[1] for card in deck["cards"]} | {"Commander"}
    for candidate in tuned:
        assert candidate["card_count"] == 100
        assert candidate["validation"]["valid"]
        assert candidate["statistics"]["land_count"] == 32
        assert {row["name"] for row in candidate["cards"]} <= source_names
        assert all(swap["in"] == "Forest" for swap in candidate["statistics"]["land_adjustments"])
        assert "Core 0" in {row["name"] for row in candidate["cards"]}
        assert "Mana adjustment" in candidate["primer"]


def sparse_fixture():
    data = fixture()
    for index in range(7):
        name = f"Extra Support {index}"
        data[4][card_key(name)] = metadata_card(name)
        for deck in data[1]:
            deck["cards"].append({"name": name, "quantity": 1})
    for deck in data[1]:
        next(card for card in deck["cards"] if card["name"] == "Forest")["quantity"] = 29
    return data


def test_playability_preference_preserves_an_exact_current_target_met_list_over_native_proxy(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = sparse_fixture()
    original = generate(deepcopy(data))
    assert original["statistics"]["land_count"] == 29
    external_validation.record_external_validation(original, {
        "source": "edhpowerlevel.com", "source_url": original["validation"]["external_url"],
        "card_count": 100, "checked_at": datetime.now(timezone.utc).isoformat(),
        "playability_pct": 74.3, "bracket": 3,
    }, tmp_path)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, tmp_path))
    generate_package_decks(*data, prefer_playability=True)
    chosen = data[0]["packages"][0]["generated_deck"]
    assert chosen["cards"] == original["cards"]
    assert chosen["validation"]["external_rating"]["status"] == "verified"
    assert chosen["validation"]["external_rating"]["playability_pct"] == 74.3
    assert "current externally observed" in chosen["statistics"]["selection_reason"]


@pytest.mark.parametrize("record_state", ["none", "stale", "unmet"])
def test_native_preference_does_not_claim_unverified_stale_or_unmet_target_success(monkeypatch, tmp_path, record_state):
    from edhrec_affinity.build_styles import external_validation

    data = sparse_fixture()
    native = generate_package_deck_candidates(*data)[0]
    if record_state != "none":
        external_validation.record_external_validation(native, {
            "source": "edhpowerlevel.com", "source_url": native["validation"]["external_url"], "card_count": 100,
            "checked_at": (datetime.now(timezone.utc) - timedelta(days=8 if record_state == "stale" else 0)).isoformat(),
            "playability_pct": 75 if record_state == "stale" else 66, "bracket": 3,
        }, tmp_path)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, tmp_path))
    generate_package_decks(*data, prefer_playability=True)
    chosen = data[0]["packages"][0]["generated_deck"]
    assert "not a verified playability percentage" in chosen["statistics"]["selection_reason"]
    if record_state == "unmet":
        assert chosen["statistics"]["land_count"] == 35
        assert chosen["statistics"]["playability_repair"]["from_playability_pct"] == 66
        assert chosen["validation"]["external_rating"]["status"] == "unverified"
    else:
        assert chosen["cards"] == native["cards"]
        assert chosen["statistics"]["land_count"] == 32
        assert chosen["validation"]["external_rating"]["status"] == {"none": "unverified", "stale": "stale"}[record_state]


def test_cached_card_roles_do_not_absorb_package_themes_and_match_uncached_exports(monkeypatch):
    from edhrec_affinity.build_styles import deckbuilding

    data = fixture()
    original_metadata = deepcopy(data[4])
    cached = generate(deepcopy(data))
    original_index = deckbuilding._metadata_index
    def uncached_index(metadata):
        indexed = original_index(metadata)
        for card in indexed.values():
            card.pop("_build_cache", None)
        return indexed
    monkeypatch.setattr(deckbuilding, "_metadata_index", uncached_index)
    uncached_data = deepcopy(data)
    uncached = generate(uncached_data)
    assert cached == uncached
    assert data[4] == original_metadata
    assert uncached_data[0]["alternative_models"][0]["packages"][0]["generated_deck"] == uncached
    indexed = original_index(data[4])
    deckbuilding._generate_one(data[0]["packages"][0], data[0]["alternative_models"][0]["memberships"],
                              data[1], data[2], data[3], indexed)
    assert functional_tags(indexed["consensus card"]) == ["Leftover"]


def test_land_upgrades_use_only_sampled_duals_and_fetches_preserving_size_and_bracket():
    data = fixture()
    data[4]["commander"]["color_identity"] = ["G", "W"]
    for deck in data[1]:
        deck["source_bracket"] = 2
    observed_lands = [
        metadata_card("On Color Dual", types="Land — Forest Plains", text="{T}: Add {G} or {W}.",
                      colors=["G", "W"], cmc=0, produced_mana=["G", "W"]),
        metadata_card("Tapped Dual", types="Land", text="Tapped Dual enters tapped. {T}: Add {G} or {W}.",
                      colors=["G", "W"], cmc=0, produced_mana=["G", "W"]),
        metadata_card("Forest Plains Fetch", types="Land", text="{T}, Pay 1 life, Sacrifice this land: Search your library for a Forest or Plains card, put it onto the battlefield, then shuffle.",
                      colors=[], cmc=0, produced_mana=[]),
        metadata_card("Forbidden Fast Dual", types="Land", text="{T}: Add {G} or {W}.",
                      colors=["G", "W"], cmc=0, produced_mana=["G", "W"], game_changer=True),
        metadata_card("Off Color Dual", types="Land", text="{T}: Add {R} or {G}.",
                      colors=["G", "R"], cmc=0, produced_mana=["G", "R"]),
    ]
    for card in observed_lands:
        data[4][card_key(card["name"])] = card
        data[1][1]["cards"].append({"name": card["name"], "quantity": 1})
    next(card for card in data[1][1]["cards"] if card["name"] == "Forest")["quantity"] -= len(observed_lands)
    # The deliberately illegal source list can contribute observed candidate
    # cards, while the complete legal anchor remains the original package deck.
    original = generate(deepcopy(data))
    candidates = generate_package_deck_candidates(*data, max_candidates=8, tune_mana=False)
    upgraded = [candidate for candidate in candidates if candidate["statistics"]["land_swaps"]]
    assert upgraded
    best = upgraded[0]
    names = {row["name"] for row in best["cards"]}
    assert "On Color Dual" in names and "Forest Plains Fetch" in names
    assert "Off Color Dual" not in names and "Forbidden Fast Dual" not in names
    assert best["validation"]["valid"] and best["card_count"] == 100
    assert best["statistics"]["land_count"] == original["statistics"]["land_count"] == 36
    assert best["statistics"]["mana_quality"]["colored_land_sources"]["W"] > original["statistics"]["mana_quality"]["colored_land_sources"].get("W", 0)
    assert best["statistics"]["mana_quality"]["mana_base_risk"] < original["statistics"]["mana_quality"]["mana_base_risk"]
    assert best["validation"]["bracket"]["rule_checks"]["game_changers"]
    assert all(row["quantity"] == 1 for row in best["cards"] if row["name"] != "Forest")
    observed_names = {card["name"] for deck in data[1] for card in deck["cards"]} | {"Commander"}
    assert names <= observed_names
    assert "fetchable land types actually retained" in best["primer"]


def test_dated_below_target_feedback_retains_three_extra_land_experiments_without_inheriting_a_rating(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = fixture()
    baseline = generate_package_deck_candidates(*data)[0]
    external_validation.record_external_validation(baseline, {
        "source": "edhpowerlevel.com", "source_url": baseline["validation"]["external_url"], "card_count": 100,
        "checked_at": datetime.now(timezone.utc).isoformat(), "playability_pct": 71.9, "bracket": 3,
    }, tmp_path)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, tmp_path))
    candidates = generate_package_deck_candidates(*data, max_candidates=8)
    repairs = [candidate for candidate in candidates if candidate["statistics"].get("playability_repair")]
    assert [candidate["statistics"]["land_count"] for candidate in repairs] == [37, 38, 39]
    assert all(candidate["card_count"] == 100 and candidate["validation"]["valid"] for candidate in repairs)
    assert all(candidate["validation"]["external_rating"]["status"] == "unverified" for candidate in repairs)
    assert all(candidate["statistics"]["playability_repair"]["from_playability_pct"] == 71.9 for candidate in repairs)
    assert all(candidate["statistics"]["mana_quality"]["structural_risk_score"] > baseline["statistics"]["mana_quality"]["structural_risk_score"] for candidate in repairs)
    assert all("Core 0" in {row["name"] for row in candidate["cards"]} for candidate in repairs)
    assert all(next(row for row in candidate["cards"] if row["name"] == "Forest")["quantity"] == candidate["statistics"]["land_count"] for candidate in repairs)
    # A separately checked repair can satisfy the target on the next run.
    external_validation.record_external_validation(repairs[0], {
        "source": "edhpowerlevel.com", "source_url": repairs[0]["validation"]["external_url"], "card_count": 100,
        "checked_at": datetime.now(timezone.utc).isoformat(), "playability_pct": 75, "bracket": 3,
    }, tmp_path)
    generate_package_decks(*data, prefer_playability=True)
    chosen = data[0]["packages"][0]["generated_deck"]
    assert chosen["cards"] == repairs[0]["cards"]
    assert chosen["validation"]["external_rating"]["playability_pct"] == 75


def test_feedback_repair_stops_at_40_lands_and_cannot_remove_all_protected_support(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = fixture()
    for deck in data[1]:
        next(card for card in deck["cards"] if card["name"] == "Forest")["quantity"] = 39
        deck["cards"] = [card for card in deck["cards"] if card["name"] not in {"Core 10", "Core 11", "Core 12"}]
    baseline = generate_package_deck_candidates(*data)[0]
    external_validation.record_external_validation(baseline, {
        "source": "edhpowerlevel.com", "source_url": baseline["validation"]["external_url"], "card_count": 100,
        "checked_at": datetime.now(timezone.utc).isoformat(), "playability_pct": 70, "bracket": 3,
    }, tmp_path)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, tmp_path))
    candidates = generate_package_deck_candidates(*data)
    repairs = [candidate for candidate in candidates if candidate["statistics"].get("playability_repair")]
    assert len(repairs) == 1 and repairs[0]["statistics"]["land_count"] == 40
    data[0]["packages"][0]["top_cards"] = [{"name": card["name"]} for deck in data[1] for card in deck["cards"] if card["name"] != "Forest"]
    assert not any(candidate["statistics"].get("playability_repair") for candidate in generate_package_deck_candidates(*data))
