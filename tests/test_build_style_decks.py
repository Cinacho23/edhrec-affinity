"""Construction safety and data-driven choices, independent of the NMF fitter."""

from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta, timezone
import re

import pytest

from edhrec_affinity.build_styles.deckbuilding import (
    DECK_GENERATION_VERSION, functional_tags, generate_package_deck_candidates, generate_package_decks,
)
from edhrec_affinity.build_styles.sources import card_key, enrich_commander_name_groups


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


def rename_fixture_card(data, old_name, new_name):
    """Give a synthetic card a named restriction while keeping pool identities."""
    card = data[4].pop(card_key(old_name))
    card["name"] = new_name
    data[4][card_key(new_name)] = card
    for sampled in data[1]:
        for row in sampled["cards"]:
            if row["name"] == old_name:
                row["name"] = new_name
    for package in [*data[0]["packages"], *[package for model in data[0].get("alternative_models", [])
                                        for package in model.get("packages", [])]]:
        for row in package.get("top_cards", []):
            if row.get("name") == old_name:
                row["name"] = new_name


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


def test_untagged_export_preserves_internal_roles_and_full_name_primer_references():
    data = fixture()
    data[4]["consensus card"]["oracle_text"] = "{T}: Add {G}. Whenever a Plant enters, draw a card."
    data[4]["weak card"]["oracle_text"] = "{T}: Add {G}. Whenever a Plant enters, draw a card."
    result = generate(data)
    assert "1 Consensus Card\n" in result["export_text"]
    assert all("tags" not in card for card in result["cards"])
    assert "#" not in result["export_text"]
    assert "1 Commander *CMDR*\n" in result["export_text"]
    assert "//" not in result["export_text"]
    assert next(card for card in result["cards"] if card["name"] == "Consensus Card")["primary_role"] == "Ramp"
    assert "Ramp\nDevelop mana with [[Consensus Card]]" in result["primer"]
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


def renamed_partner_fixture():
    data = fixture()
    partner, original, alternate = "Kamahl, Heart of Krosa", "Yoshimaru, Ever Faithful", "Dogmeat, Constant Companion"
    commander = data[4].pop("commander")
    commander.update(name=partner, oracle_text="Partner", oracle_id="kamahl-oracle")
    data[4][card_key(partner)] = commander
    data[4][card_key(original)] = metadata_card(original, types="Legendary Creature — Dog", text="Partner",
                                               colors=["W"], oracle_id="yoshimaru-oracle")
    data[2]["commander_names"] = [partner, alternate]
    data[2]["commander_name_groups"] = [[partner, alternate], [original, partner]]
    for index, deck in enumerate(data[1]):
        deck["commanders"] = [original, partner]
        deck["cards"][-1]["quantity"] = 35
        # Exports sometimes repeat command-zone cards in their card rows. Both
        # the canonical and verified flavor names must stay out of the 98.
        deck["cards"] += [{"name": partner, "quantity": 1},
                          {"name": alternate if index == 1 else original, "quantity": 1}]
    return data


def test_verified_flavor_commander_uses_canonical_observed_partner_lists_without_double_counting():
    result = generate(renamed_partner_fixture())
    assert result["status"] == "generated" and result["validation"]["valid"]
    assert result["card_count"] == 100
    assert {card["name"] for card in result["commanders"]} == {"Kamahl, Heart of Krosa", "Yoshimaru, Ever Faithful"}
    assert "Dogmeat, Constant Companion" not in result["export_text"]
    assert result["export_text"].count("1 Yoshimaru, Ever Faithful *CMDR*") == 1
    assert "#" not in result["export_text"]
    assert all("tags" not in card for card in result["cards"])


@pytest.mark.parametrize("actual", [
    ["Yoshimaru, Ever Faithful", "Wrong Partner"],
    ["Yoshimaru, Ever Faithful"],
    ["Yoshimaru, Ever Faithful", "Kamahl, Heart of Krosa", "Dogmeat, Constant Companion"],
    ["Yoshimaru, Ever Faithful", "Yoshimaru, Ever Faithful"],
    ["Kamahl, Heart of Krosa", "Kamahl, Heart of Krosa", "Yoshimaru, Ever Faithful"],
])
def test_verified_flavor_configuration_does_not_relax_partner_membership_or_commander_count(actual):
    data = renamed_partner_fixture()
    for deck in data[1]:
        deck["commanders"] = actual
    assert generate(data)["status"] == "unavailable"


def test_flavor_configuration_conflicting_with_an_actual_canonical_card_fails_closed():
    data = renamed_partner_fixture()
    data[4]["dogmeat, constant companion"] = metadata_card("Dogmeat, Constant Companion",
        types="Legendary Creature — Dog", text="Partner", colors=["W"], oracle_id="different-real-card")
    result = generate(data)
    assert result["status"] == "unavailable" and "conflict" in result["reason"]
    for deck in data[1]:
        deck["commanders"] = list(data[2]["commander_names"])
    assert generate(data)["status"] == "unavailable"  # A conflicting alias family never constructs a different card.
    data = renamed_partner_fixture()
    data[2].pop("commander_name_groups")
    assert generate(data)["status"] == "unavailable"  # No unverified name substitutions.


def flavor_alias_proof(card, alternate):
    return {"name": alternate, "source_field": "flavor_name", "printing_id": "verified-printing-id",
            "lang": "en", "oracle_id": card["oracle_id"], "canonical_name": card["name"]}


def test_builder_keeps_verified_scryfall_flavor_aliases_through_metadata_reindexing():
    data = fixture()
    card = data[4].pop("core 0")
    card.update(name="Original Engine", oracle_id="original-engine-oracle")
    card["name_aliases"] = [flavor_alias_proof(card, "Core 0")]
    data[4]["original engine"] = card
    result = generate(data)
    assert result["status"] == "generated" and result["validation"]["valid"]
    assert "Original Engine" in {row["name"] for row in result["cards"]}
    assert "Core 0" not in {row["name"] for row in result["cards"]}
    # Two observed aliases of one Oracle card count as two copies, rather than
    # overwriting one another and accidentally passing singleton validation.
    for deck in data[1]:
        deck["cards"][1]["name"] = "Original Engine"
    assert generate(data)["status"] == "unavailable"


def test_cold_printing_proof_can_add_the_full_canonical_configuration_before_construction():
    data = fixture()
    alternate, original = "Max, the Daredevil", "Elmar, Ulvenwald Informant"
    commander = data[4].pop("commander")
    commander.update(name=original, oracle_id="elmar-oracle")
    commander["name_aliases"] = [{**flavor_alias_proof(commander, alternate), "source_field": "printed_name"}]
    data[4][card_key(original)] = commander
    data[2].update(commander_names=[alternate], commander_name_groups=[[alternate]])
    for deck in data[1]:
        deck["commanders"] = [original]
    assert generate(deepcopy(data))["status"] == "unavailable"  # Full configuration is still required.
    data[2]["commander_name_groups"] = enrich_commander_name_groups(data[2], data[4])
    result = generate(data)
    assert result["status"] == "generated" and result["validation"]["valid"]
    assert result["card_count"] == 100
    assert result["commanders"] == [{"name": original, "quantity": 1}]
    assert f"1 {original} *CMDR*" in result["export_text"] and "#" not in result["export_text"]


def test_builder_prefers_actual_names_and_rejects_ambiguous_flavor_aliases():
    data = fixture()
    for name in ("Other Engine", "Another Engine"):
        card = metadata_card(name, oracle_id=f"oracle-{name}")
        card["name_aliases"] = [flavor_alias_proof(card, "Core 0")]
        data[4][card_key(name)] = card
    assert "Core 0" in {row["name"] for row in generate(data)["cards"]}
    del data[4]["core 0"]
    assert generate(data)["status"] == "unavailable"


def test_bracket_guidance_uses_source_memberships_and_raises_for_checked_restrictions():
    data = fixture()
    for deck in data[1]:
        deck["source_bracket"] = 2
    data[4]["core 0"]["game_changer"] = True
    result = generate(data)
    bracket = result["validation"]["bracket"]
    assert bracket["target"] == 2 and bracket["recommended"] == 3
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
        if "extra turn" in text:
            rename_fixture_card(data, "Core 0", "Time Warp")
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
    def uncached_index(metadata, **options):
        indexed = original_index(metadata, **options)
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


def add_observed_core(data, cards, *, include_in_package=True):
    """A partial observed list supplies candidates but cannot become an anchor."""
    for card in cards:
        data[4][card_key(card["name"])] = card
    data[1].append({"id": "observed-core", "commanders": data[2]["commander_names"],
                    "cards": [{"name": card["name"], "quantity": 1} for card in cards], "source_bracket": 3})
    data[0]["decks"].append({"id": "observed-core", "memberships": [.01]})
    if include_in_package:
        data[0]["packages"][0]["top_cards"] = [
            {"name": card["name"], "relative_weight": 1 - index / max(1, len(cards))}
            for index, card in enumerate(cards)]


def test_all_twelve_distinctive_cards_are_inserted_despite_lower_consensus_and_different_signatures():
    data = fixture()
    distinctive = [metadata_card(f"Distinctive {index}", types="Enchantment" if index % 2 else "Artifact",
                                  cmc=4 + index % 3, text="Draw a card." if index % 3 else "")
                   for index in range(12)]
    add_observed_core(data, distinctive)
    result = generate(data)
    names = {card["name"] for card in result["cards"]}
    assert {card["name"] for card in distinctive} <= names
    assert result["generation_version"] == data[0]["deck_generation"]["version"] == DECK_GENERATION_VERSION
    assert result["statistics"]["anchor_deck_id"] == "a"
    assert result["statistics"]["distinctive_swaps"] == 12
    assert result["distinctive_cards"] == {"total": 12, "included_count": 12, "coverage": 1,
                                           "included": [{"name": card["name"]} for card in distinctive], "omitted": []}
    assert result["validation"]["valid"] and result["card_count"] == 100
    assert result["statistics"]["land_count"] == 36
    assert "includes 12 of the 12" in result["primer"]
    assert "#" not in result["export_text"]
    # An alternative with identical memberships but another core cannot reuse
    # the primary package's cached deck and its coverage claims.
    assert data[0]["alternative_models"][0]["packages"][0]["generated_deck"]["distinctive_cards"]["total"] == 1


def test_distinctive_omissions_explain_metadata_legality_pool_color_and_bracket_failures():
    data = fixture()
    cards = [metadata_card("Present Core"), metadata_card("Banned Core", legalities={"commander": "banned"}),
             metadata_card("Red Core", colors=["R"]), metadata_card("Time Warp", text="Take an extra turn after this one."),
             metadata_card("Incomplete Core")]
    add_observed_core(data, cards)
    del data[4]["incomplete core"]["type_line"]
    data[4]["outside core"] = metadata_card("Outside Core")
    data[0]["packages"][0]["top_cards"] += [{"name": "Missing Core"}, {"name": "Outside Core"}]
    result = generate(data)
    coverage = result["distinctive_cards"]
    assert coverage["total"] == 7 and coverage["included_count"] == 1
    assert coverage["coverage"] == pytest.approx(1 / 7)
    assert {row["name"]: row["reason_code"] for row in coverage["omitted"]} == {
        "Banned Core": "illegal_in_commander", "Red Core": "color_identity_conflict",
        "Time Warp": "bracket_conflict", "Incomplete Core": "metadata_incomplete",
        "Missing Core": "metadata_missing", "Outside Core": "not_in_sampled_pool"}
    assert result["validation"]["valid"] and result["validation"]["bracket"]["recommended"] == 3
    for row in coverage["omitted"]:
        assert row["reason"] and f"[[{row['name']}]] is omitted: {row['reason']}" in result["primer"]


@pytest.mark.parametrize("lowest", [0, 1, 2])
def test_higher_nmf_priority_wins_when_only_three_distinctive_game_changers_fit(lowest):
    data = fixture()
    for index in range(3):
        data[4][f"core {index}"]["game_changer"] = True
    add_observed_core(data, [metadata_card("Leading Core", game_changer=True)])
    data[0]["packages"][0]["top_cards"] = [{"name": "Leading Core", "relative_weight": 1},
        *[{"name": f"Core {index}", "relative_weight": .1 if index == lowest else .2 + index / 10}
          for index in range(3)]]
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    assert {"Leading Core", *[f"Core {index}" for index in range(3) if index != lowest]} <= names
    assert f"Core {lowest}" not in names
    assert result["distinctive_cards"]["included_count"] == 3
    assert result["distinctive_cards"]["omitted"][0]["name"] == f"Core {lowest}"
    assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "bracket_conflict"
    assert result["validation"]["bracket"]["game_changer_count"] == 3
    assert result["validation"]["valid"]


def test_distinctive_core_does_not_remove_last_required_support_roles():
    data = fixture()
    roles = [("Ramp", "{T}: Add {G}.", 8), ("Draw", "Draw a card.", 8),
             ("Removal", "Destroy target creature.", 4), ("Wipe", "Destroy all artifacts.", 2),
             ("Tutor", "Search your library for a card, put it into your hand, then shuffle.", 1),
             ("Recursion", "Return target card from your graveyard to your hand.", 1),
             ("Protection", "Creatures you control have hexproof.", 2)]
    cards = []
    for role, text, quantity in roles:
        for index in range(quantity):
            card = metadata_card(f"{role} Support {index}", text=text)
            cards.append({"name": card["name"], "quantity": 1})
            data[4][card_key(card["name"])] = card
    for deck in data[1]:
        deck["cards"] = [*deepcopy(cards), {"name": "Forest", "quantity": 73}]
    add_observed_core(data, [metadata_card("Unsupported Core")])
    result = generate(data)
    assert result["validation"]["valid"] and result["card_count"] == 100
    assert result["distinctive_cards"]["included_count"] == 0
    assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "essential_role_conflict"
    assert {row["name"] for row in cards} <= {row["name"] for row in result["cards"]}


def test_distinctive_land_preserves_colored_sources_and_uses_a_land_slot():
    data = fixture()
    data[4]["commander"]["color_identity"] = ["G", "W"]
    data[4]["plains"] = metadata_card("Plains", types="Basic Land — Plains", colors=["W"], cmc=0)
    for deck in data[1]:
        deck["cards"][-1]["quantity"] = 18
        deck["cards"].append({"name": "Plains", "quantity": 18})
    add_observed_core(data, [metadata_card("Core Utility Land", types="Land", colors=[], text="{T}: Add {C}.", cmc=0)])
    result = generate(data)
    assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "mana_base_conflict"
    assert result["statistics"]["mana_quality"]["colored_land_sources"] == {"G": 18, "W": 18}
    data[4]["core utility land"].update(oracle_text="{T}: Add {G} or {W}.", produced_mana=["G", "W"])
    result = generate(data)
    assert result["distinctive_cards"]["included_count"] == 1
    assert result["statistics"]["land_count"] == 36
    assert result["validation"]["valid"]


def test_distinctive_alias_rows_are_covered_by_one_oracle_card_not_extra_copies():
    data = fixture()
    card = data[4]["core 0"]
    card["oracle_id"] = "shared-core-oracle"
    card["name_aliases"] = [flavor_alias_proof(card, "Flavor Core")]
    duplicate = metadata_card("Other Canonical Core", oracle_id="shared-core-oracle")
    add_observed_core(data, [duplicate])
    data[0]["packages"][0]["top_cards"] = [{"name": "Flavor Core"}, {"name": "Other Canonical Core"}]
    result = generate(data)
    assert result["distinctive_cards"] == {"total": 2, "included_count": 2, "coverage": 1,
        "included": [{"name": "Flavor Core", "canonical_name": "Core 0"},
                     {"name": "Other Canonical Core", "canonical_name": "Core 0"}], "omitted": []}
    assert "Other Canonical Core" not in {row["name"] for row in result["cards"]}
    assert next(row for row in result["cards"] if row["name"] == "Core 0")["quantity"] == 1
    assert result["validation"]["valid"]


def test_all_mana_and_feedback_variants_keep_the_inserted_distinctive_core():
    data = sparse_fixture()
    distinctive = [metadata_card(f"Inserted Core {index}", cmc=6, types="Enchantment") for index in range(12)]
    add_observed_core(data, distinctive)
    candidates = generate_package_deck_candidates(*data, max_candidates=8)
    assert candidates and any(candidate["statistics"]["land_adjustments"] for candidate in candidates)
    for candidate in candidates:
        assert candidate["distinctive_cards"]["included_count"] == 12
        assert {card["name"] for card in distinctive} <= {row["name"] for row in candidate["cards"]}
        assert candidate["validation"]["valid"] and candidate["card_count"] == 100


def test_full_core_candidate_beats_current_external_rating_of_a_lower_coverage_list(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import deckbuilding, external_validation

    data = fixture()
    memberships = data[0]["alternative_models"][0]["memberships"]
    package = {"id": 0, "name": "Package 1", "top_cards": [{"name": "Weak Card"}]}
    indexed = deckbuilding._metadata_index(data[4])
    high = deckbuilding._generate_one(package, memberships, data[1], data[2], data[3], indexed)
    low = deckbuilding._generate_one({**package, "top_cards": []}, memberships, data[1], data[2], data[3], indexed)
    assert "Weak Card" in {row["name"] for row in high["cards"]}
    assert "Weak Card" not in {row["name"] for row in low["cards"]}
    low["distinctive_cards"] = {"total": 1, "included_count": 0, "coverage": 0, "included": [], "omitted": []}
    external_validation.record_external_validation(low, {"source": "edhpowerlevel.com",
        "source_url": low["validation"]["external_url"], "card_count": 100,
        "checked_at": datetime.now(timezone.utc).isoformat(), "playability_pct": 75, "bracket": 3}, tmp_path)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, tmp_path))
    chosen = deckbuilding._prefer_playability([low, high])
    assert chosen is high
    assert chosen["validation"]["external_rating"]["status"] == "unverified"
    assert "Prioritized distinctive-card coverage" in chosen["statistics"]["selection_reason"]


def test_zero_core_and_unavailable_packages_have_honest_versioned_coverage():
    data = fixture()
    data[0]["packages"][0]["top_cards"] = []
    assert generate(data)["distinctive_cards"] == {"total": 0, "included_count": 0, "coverage": 0,
                                                   "included": [], "omitted": []}
    data = fixture()
    del data[4]["commander"]
    result = generate(data)
    assert result["generation_version"] == DECK_GENERATION_VERSION and result["status"] == "unavailable"
    assert result["distinctive_cards"]["total"] == 1 and result["distinctive_cards"]["included_count"] == 0
    assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "construction_unavailable"


def test_candidate_search_prefers_an_anchor_that_can_fit_more_core_over_membership():
    data = fixture()
    roles = [("Ramp", "{T}: Add {G}.", 8), ("Draw", "Draw a card.", 8),
             ("Removal", "Destroy target creature.", 4), ("Wipe", "Destroy all artifacts.", 2),
             ("Tutor", "Search your library for a card, put it into your hand, then shuffle.", 1),
             ("Recursion", "Return target card from your graveyard to your hand.", 1),
             ("Protection", "Creatures you control have hexproof.", 2)]
    support = []
    for role, text, quantity in roles:
        for index in range(quantity):
            name = f"{role} Support {index}"
            data[4][card_key(name)] = metadata_card(name, text=text)
            support.append({"name": name, "quantity": 1})
    for deck in data[1]:
        deck["cards"] = [*deepcopy(support), {"name": "Forest", "quantity": 73}]
    data[1][1]["cards"][-1]["quantity"] = 72
    data[1][1]["cards"].append({"name": "Core 0", "quantity": 1})
    add_observed_core(data, [metadata_card("Desired Core")])
    result = generate(data)
    assert result["statistics"]["anchor_deck_id"] == "b"
    assert result["distinctive_cards"]["included_count"] == 1
    candidates = generate_package_deck_candidates(*data, max_candidates=1)
    assert len(candidates) == 1
    assert candidates[0]["statistics"]["anchor_deck_id"] == "b"
    assert candidates[0]["distinctive_cards"]["included_count"] == 1
    assert candidates[0]["validation"]["valid"]


def test_distinctive_changer_replaces_a_changer_when_anchor_already_exceeds_target_cap():
    data = fixture()
    for index in range(4):
        data[4][f"core {index}"]["game_changer"] = True
    add_observed_core(data, [metadata_card("Extra Core Changer", game_changer=True)])
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    assert "Extra Core Changer" in names
    assert len({f"Core {index}" for index in range(4)} & names) == 3
    assert result["validation"]["bracket"]["game_changer_count"] == 4
    assert result["validation"]["bracket"]["recommended"] == 4
    assert result["distinctive_cards"]["included_count"] == 1
    assert result["validation"]["valid"]


def test_distinctive_changer_is_explicitly_omitted_when_existing_over_cap_core_is_protected():
    data = fixture()
    for index in range(4):
        data[4][f"core {index}"]["game_changer"] = True
    add_observed_core(data, [metadata_card("Extra Core Changer", game_changer=True)])
    data[0]["packages"][0]["top_cards"] = [
        *[{"name": f"Core {index}", "relative_weight": 1 - index / 10} for index in range(4)],
        {"name": "Extra Core Changer", "relative_weight": .1}]
    result = generate(data)
    assert result["validation"]["bracket"]["game_changer_count"] == 4
    assert result["distinctive_cards"]["included_count"] == 4
    assert result["distinctive_cards"]["omitted"][0]["name"] == "Extra Core Changer"
    assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "bracket_conflict"
    assert result["validation"]["valid"]


@pytest.mark.parametrize(("text", "count_key"), [
    ("Take an extra turn after this one.", "extra_turn_count"),
    ("Destroy all lands.", "mass_land_denial_count"),
])
def test_distinctive_restricted_effects_replace_like_for_like_without_increasing_existing_violations(text, count_key):
    data = fixture()
    data[4]["core 0"]["oracle_text"] = text
    add_observed_core(data, [metadata_card("Restricted Core", text=text)])
    if "extra turn" in text:
        rename_fixture_card(data, "Core 0", "Time Warp")
        rename_fixture_card(data, "Restricted Core", "Capture of Jingzhou")
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    expected = "Capture of Jingzhou" if "extra turn" in text else "Restricted Core"
    removed = "Time Warp" if "extra turn" in text else "Core 0"
    assert expected in names and removed not in names
    assert result["validation"]["bracket"][count_key] == 1
    assert result["validation"]["bracket"]["recommended"] == 4
    assert result["distinctive_cards"]["included_count"] == 1
    assert result["validation"]["valid"]


@pytest.mark.parametrize("restriction", ["game_changer", "extra_turns", "mass_land_denial"])
def test_consensus_does_not_add_more_restricted_cards_to_an_already_over_target_anchor(restriction):
    data = fixture()
    if restriction == "game_changer":
        for index in range(4):
            data[4][f"core {index}"]["game_changer"] = True
        data[4]["consensus card"]["game_changer"] = True
        count_key, expected = "game_changer_count", 4
    else:
        text = "Take an extra turn after this one." if restriction == "extra_turns" else "Destroy all lands."
        # Give the outgoing ordinary slot the same functional roles without the
        # restricted effect so the exact-signature consensus swap is eligible.
        data[4]["core 0"]["oracle_text"] = text
        data[4]["consensus card"]["oracle_text"] = text
        if restriction == "extra_turns":
            rename_fixture_card(data, "Core 0", "Time Warp")
            rename_fixture_card(data, "Consensus Card", "Capture of Jingzhou")
        if restriction == "mass_land_denial":
            data[4]["weak card"]["oracle_text"] = "Destroy all artifacts."
        count_key = "extra_turn_count" if restriction == "extra_turns" else "mass_land_denial_count"
        expected = 1
    result = generate(data)
    assert "Weak Card" in {row["name"] for row in result["cards"]}
    assert ("Capture of Jingzhou" if restriction == "extra_turns" else "Consensus Card") not in {row["name"] for row in result["cards"]}
    assert result["validation"]["bracket"][count_key] == expected
    assert result["validation"]["valid"]


def test_mana_upgrades_do_not_add_a_game_changer_when_anchor_is_already_over_target():
    data = fixture()
    data[4]["commander"]["color_identity"] = ["G", "W"]
    for index in range(4):
        data[4][f"core {index}"]["game_changer"] = True
    add_observed_core(data, [metadata_card("Restricted Dual", types="Land — Forest Plains", text="{T}: Add {G} or {W}.",
                                         colors=["G", "W"], produced_mana=["G", "W"], cmc=0, game_changer=True)],
                      include_in_package=False)
    candidates = generate_package_deck_candidates(*data, max_candidates=8)
    assert candidates
    for candidate in candidates:
        assert "Restricted Dual" not in {row["name"] for row in candidate["cards"]}
        assert candidate["validation"]["bracket"]["game_changer_count"] == 4
        assert candidate["validation"]["valid"]


def test_shared_source_preparation_keeps_primary_alternatives_and_candidates_identical(monkeypatch):
    from edhrec_affinity.build_styles import deckbuilding

    data = sparse_fixture()
    add_observed_core(data, [metadata_card(f"New Core {index}", types="Enchantment", cmc=4 + index)
                             for index in range(3)])
    data[0]["alternative_models"].append({
        "components": 2,
        "packages": [{"id": 0, "name": "Different core", "top_cards": [{"name": "Weak Card"}]},
                     {"id": 1, "name": "Other core", "top_cards": [{"name": "New Core 1"}]}],
        "memberships": [{"id": deck["id"], "weights": [1, .1] if deck["id"] == "a" else [.1, .8]}
                        for deck in data[1]]})
    original = deepcopy(data)
    prepare_sources = deckbuilding._prepare_sources
    source_calls = []
    def observed_sources(*args):
        source_calls.append(None)
        return prepare_sources(*args)
    monkeypatch.setattr(deckbuilding, "_prepare_sources", observed_sources)
    cached_data = deepcopy(data)
    generate_package_decks(*cached_data, prefer_playability=True)
    assert len(source_calls) == 1
    cached_candidates = generate_package_deck_candidates(*data, components=2, package_id=1, max_candidates=8)
    source_calls.clear()
    prepare_package = deckbuilding._prepare_package
    def uncached_package(*args, **options):
        options["_source_context"] = None
        return prepare_package(*args, **options)
    monkeypatch.setattr(deckbuilding, "_prepare_package", uncached_package)
    uncached_data = deepcopy(data)
    generate_package_decks(*uncached_data, prefer_playability=True)
    assert len(source_calls) == 4
    assert cached_data == uncached_data
    assert cached_candidates == generate_package_deck_candidates(*data, components=2, package_id=1, max_candidates=8)
    assert data == original


def test_presence_index_preserves_original_float_addition_order_and_model_weights():
    from edhrec_affinity.build_styles import deckbuilding

    data = fixture()
    # Deliberately uneven magnitudes and a repeated source ID expose reordered
    # or deduplicated sums. Production collectors deduplicate IDs, but the
    # builder preserves its supplied observations rather than altering them.
    data[1].append({**deepcopy(data[1][0]), "cards": deepcopy(data[1][0]["cards"])})
    memberships = [{"id": "a", "weights": [1e-9, 1]}, {"id": "b", "weights": [1, 1e-9]},
                   {"id": "c", "weights": [1e-5, .3]}]
    indexed = deckbuilding._metadata_index(data[4])
    shared = {}
    first = deckbuilding._prepare_package(data[0]["packages"][0], memberships, data[1], data[2], data[3], indexed,
                                          _source_context=shared)
    second = deckbuilding._prepare_package({"id": 1}, memberships, data[1], data[2], data[3], indexed,
                                           _source_context=shared)
    observed = shared["prepared"]["observed"]
    for package_index, prepared in enumerate((first, second)):
        weights = {row["id"]: float(row["weights"][package_index]) for row in memberships}
        learned = [deck for deck in observed if deck["id"] in weights]
        total = sum(weights[deck["id"]] for deck in learned)
        expected = {key: sum(weights[deck["id"]] for deck in learned if key in deck["counts"]) / total
                    for key in shared["prepared"]["pool"]}
        assert prepared["support"] == expected
    assert first["support"] != second["support"]


def test_anchor_bracket_cache_stays_immutable_while_generated_variants_recompute_counts(monkeypatch):
    from edhrec_affinity.build_styles import deckbuilding

    data = fixture()
    data[4]["weak card"]["game_changer"] = True
    add_observed_core(data, [metadata_card("Ordinary Distinctive")])
    indexed = deckbuilding._metadata_index(data[4])
    context = {"source_context": {}}
    package = data[0]["packages"][0]
    memberships = [{"id": row["id"], "weights": row["memberships"]} for row in data[0]["decks"]]
    first = deckbuilding._generate_one(package, memberships, data[1], data[2], data[3], indexed,
                                       anchor_id="a", _context=context)
    sources = context["source_context"]["prepared"]
    anchor = next(deck for deck in sources["anchors"] if deck["id"] == "a")
    assert sources["anchor_bracket_counts"][id(anchor)] == (1, 0, 0, 0)
    assert anchor["counts"]["weak card"] == 1 and "ordinary distinctive" not in anchor["counts"]
    assert first["validation"]["bracket"]["game_changer_count"] == 0
    original_counters = {id(deck["counts"]) for deck in sources["anchors"]}
    counts = deckbuilding._bracket_counts
    scanned_originals = []
    def observed_counts(cards, metadata):
        if id(cards) in original_counters:
            scanned_originals.append(id(cards))
        return counts(cards, metadata)
    monkeypatch.setattr(deckbuilding, "_bracket_counts", observed_counts)
    second = deckbuilding._generate_one(package, memberships, data[1], data[2], data[3], indexed,
                                        anchor_id="a", land_adjustment=3, mana_base_optimization=True, _context=context)
    assert not scanned_originals
    assert second["validation"]["bracket"]["game_changer_count"] == 0
    assert second["validation"]["valid"] and second["card_count"] == 100
    assert sources["anchor_bracket_counts"][id(anchor)] == (1, 0, 0, 0)
    assert anchor["counts"]["weak card"] == 1


COLOR_SETTING_TEXT = "If {name} is your commander, choose a color before the game begins. {name} is the chosen color."


def clara_fixture():
    data = fixture()
    doctor = data[4].pop("commander")
    doctor.update(name="The Fifth Doctor", type_line="Legendary Creature — Time Lord Doctor", color_identity=["W", "U"])
    data[4]["the fifth doctor"] = doctor
    data[4]["clara oswald"] = metadata_card("Clara Oswald", types="Legendary Creature — Human Advisor", colors=[],
        text="Impossible Girl — " + COLOR_SETTING_TEXT.format(name="Clara Oswald") +
             "\nIf a triggered ability of a Doctor you control triggers, that ability triggers an additional time.\nDoctor's companion")
    data[4]["plains"] = metadata_card("Plains", types="Basic Land — Plains", colors=["W"], cmc=0)
    for card in data[4].values():
        if card["type_line"] == "Creature — Plant":
            card["color_identity"] = []
    data[4]["green core"] = metadata_card("Green Core", colors=["G"])
    data[4]["red core"] = metadata_card("Red Core", colors=["R"])
    data[2]["commander_names"] = ["The Fifth Doctor", "Clara Oswald"]
    for deck in data[1]:
        deck["commanders"] = ["The Fifth Doctor", "Clara Oswald"]
        deck["cards"][-1] = {"name": "Plains", "quantity": 35}
        if deck["id"] in {"b", "c"}:
            deck["cards"][-2]["name"] = "Green Core" if deck["id"] == "b" else "Red Core"
    data[0]["packages"][0]["top_cards"] = [{"name": "Green Core", "relative_weight": 1},
                                            {"name": "Red Core", "relative_weight": .1}]
    data[0]["alternative_models"][0]["packages"][0]["top_cards"] = [
        {"name": "Green Core", "relative_weight": .1}, {"name": "Red Core", "relative_weight": 1}]
    return data


def test_clara_color_choice_is_selected_per_package_without_refitting_or_mutating_metadata():
    data = clara_fixture()
    original = deepcopy(data)
    primary = generate(data)
    alternate = data[0]["alternative_models"][0]["packages"][0]["generated_deck"]
    for result, color, identity, included, excluded in (
            (primary, "G", ["W", "U", "G"], "Green Core", "Red Core"),
            (alternate, "R", ["W", "U", "R"], "Red Core", "Green Core")):
        assert result["status"] == "generated" and result["validation"]["valid"] and result["card_count"] == 100
        assert result["validation"]["checks"]["commander_color_choices"]
        assert result["validation"]["commander_color_choices"] == [{"commander_name": "Clara Oswald", "color": color}]
        assert result["validation"]["effective_color_identity"] == identity
        names = {row["name"] for row in result["cards"]}
        assert included in names and excluded not in names
        assert result["distinctive_cards"]["included_count"] == 1
        assert result["distinctive_cards"]["omitted"][0]["name"] == excluded
        assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "color_identity_conflict"
        assert "Pregame commander color" in result["primer"] and "for [[Clara Oswald]] before the game begins" in result["primer"]
        assert "#" not in result["export_text"] and "chooses" not in result["export_text"]
    assert data[4] == original[4] and data[1:] == original[1:]
    assert data[0]["decks"] == original[0]["decks"]
    assert data[0]["alternative_models"][0]["memberships"] == original[0]["alternative_models"][0]["memberships"]


def test_clara_adds_exactly_one_chosen_color_and_rejects_an_anchor_requiring_two_extra_colors():
    from edhrec_affinity.build_styles import deckbuilding

    data = clara_fixture()
    data[2]["commander_color_choices"] = [{"commander_name": "Clara Oswald", "color": "G"}]
    indexed = deckbuilding._metadata_index(data[4])
    prepared = deckbuilding._prepare_sources(data[1], data[2], indexed)
    assert {deck["id"] for deck in prepared["anchors"]} == {"a", "b"}
    green_anchor = next(deck for deck in prepared["anchors"] if deck["id"] == "b")
    mixed = green_anchor["counts"].copy()
    del mixed["core 0"]
    mixed["red core"] = 1
    checked = deckbuilding._validate(mixed, prepared["commander_keys"], indexed, prepared["pool"],
                                     commander_color_choices=data[2]["commander_color_choices"])
    assert not checked["valid"] and not checked["checks"]["color_identity"]
    assert checked["checks"]["commander_color_choices"]
    assert data[4]["clara oswald"]["color_identity"] == []


@pytest.mark.parametrize("choices", [[], [{"commander_name": "Clara Oswald", "color": "GR"}],
    [{"commander_name": "Clara Oswald", "color": "C"}], [{"commander_name": "Green Core", "color": "G"}],
    [{"commander_name": "Clara Oswald", "color": "G"}, {"commander_name": "Clara Oswald", "color": "R"}]])
def test_invalid_or_arbitrary_commander_color_choices_fail_closed(choices):
    data = clara_fixture()
    data[2]["commander_color_choices"] = choices
    assert generate(data)["status"] == "unavailable"
    data = fixture()
    data[2]["commander_color_choices"] = [{"commander_name": "Commander", "color": "R"}]
    assert generate(data)["status"] == "unavailable"


@pytest.mark.parametrize("name", ["The Prismatic Piper", "Faceless One", "Clara Oswald"])
def test_exact_actual_color_setting_ability_supports_single_commanders(name):
    data = fixture()
    commander = data[4].pop("commander")
    commander.update(name=name, color_identity=[], oracle_text=COLOR_SETTING_TEXT.format(name=name))
    data[4][card_key(name)] = commander
    data[2]["commander_names"] = [name]
    for deck in data[1]:
        deck["commanders"] = [name]
    result = generate(data)
    assert result["status"] == "generated" and result["validation"]["valid"]
    assert result["validation"]["commander_color_choices"] == [{"commander_name": name, "color": "G"}]
    assert result["validation"]["effective_color_identity"] == ["G"]
    assert commander["color_identity"] == []


def test_chosen_color_candidates_cover_legal_choices_and_preserve_analysis_and_bracket_checks():
    data = clara_fixture()
    before = deepcopy(data)
    candidates = generate_package_deck_candidates(*data, max_candidates=8)
    assert candidates
    assert any(candidate["validation"]["commander_color_choices"][0]["color"] == "G" for candidate in candidates)
    for candidate in candidates:
        colors = set(candidate["validation"]["effective_color_identity"])
        assert len(colors - {"W", "U"}) <= 1
        assert all(set(data[4][card_key(row["name"])]["color_identity"]) <= colors for row in candidate["cards"])
        assert candidate["validation"]["valid"] and candidate["card_count"] == 100
    assert data == before
    data[4]["green core"]["oracle_text"] = "Take an extra turn after this one."
    rename_fixture_card(data, "Green Core", "Time Warp")
    result = generate(data)
    assert result["validation"]["commander_color_choices"] == [{"commander_name": "Clara Oswald", "color": "R"}]
    assert result["validation"]["bracket"]["recommended"] == 3


def test_identical_lists_keep_the_best_native_color_choice_before_external_rating_deduplication():
    data = clara_fixture()
    doctor = data[4].pop("the fifth doctor")
    doctor.update(name="The Sixth Doctor", color_identity=["G", "U"])
    data[4]["the sixth doctor"] = doctor
    data[2]["commander_names"] = ["The Sixth Doctor", "Clara Oswald"]
    for deck in data[1]:
        deck["commanders"] = data[2]["commander_names"].copy()
        deck["cards"][-1] = {"name": "Forest", "quantity": 35}
    for package in [data[0]["packages"][0], data[0]["alternative_models"][0]["packages"][0]]:
        package["top_cards"] = [{"name": "Core 0"}]
    generate_package_decks(*data, prefer_playability=True)
    for package in [data[0]["packages"][0], data[0]["alternative_models"][0]["packages"][0]]:
        result = package["generated_deck"]
        assert result["validation"]["valid"] and result["distinctive_cards"]["included_count"] == 1
        assert result["validation"]["commander_color_choices"][0]["color"] in {"U", "G"}
        assert result["validation"]["effective_color_identity"] == ["U", "G"]
        assert "W" not in result["statistics"]["mana_quality"]["colored_source_targets"]


_RESTORE_BALANCE_TEXT = ("Each player chooses a number of lands they control equal to the number of lands controlled "
                         "by the player who controls the fewest, then sacrifices the rest. Players sacrifice creatures "
                         "and discard cards the same way.")


def score_repair_fixture():
    data = fixture()
    for deck in data[1]:
        deck["source_bracket"] = 4
        for card in deck["cards"]:
            if card["name"] == "Core 0":
                card["name"] = "Restore Balance"
    del data[4]["core 0"]
    data[4]["restore balance"] = metadata_card("Restore Balance", types="Sorcery", text=_RESTORE_BALANCE_TEXT)
    for package in [*data[0]["packages"], *data[0]["alternative_models"][0]["packages"]]:
        package["top_cards"] = [{"name": "Restore Balance"}]
    return data


@pytest.mark.parametrize("conflict", ["mass_land_denial", "early_combos"])
@pytest.mark.parametrize("protect_core", [True, False])
def test_large_repair_pool_materializes_only_ranked_beam_lists(conflict, protect_core):
    from edhrec_affinity.build_styles import deckbuilding

    analysis, decks, base, profiles, metadata = fixture()
    # Thousands of interchangeable observed replacements must not require a
    # complete Counter copy and sorted-list identity for every comparison.
    incoming = [f"Replacement {index:04}" for index in range(2000)]
    metadata.update({card_key(name): metadata_card(name) for name in incoming})
    copies = []

    class TrackedCounts(Counter):
        def copy(self):
            copies.append(True)
            return type(self)(self)

    counts = TrackedCounts({"commander": 1, **{card_key(row["name"]): row["quantity"]
                                              for row in decks[0]["cards"]}})
    restrictions = deckbuilding._observed_bracket_restrictions({}, metadata)
    restrictions[conflict] = ([frozenset({"core 0", "core 1"})] if conflict == "early_combos" else {"core 0"})
    prepared = {"commander_keys": ["commander"], "commander_color_choices": [], "colors": {"G"},
                "pool": set(metadata), "support": {card_key(name): 1 for name in incoming},
                "distinctive_targets": [{"identity": ("name", "core 1"), "weight": 1}] if protect_core else []}
    repaired, swaps = deckbuilding._strict_bracket_replacements(counts, prepared, metadata, 2, restrictions)
    assert swaps == [{"out": "Core 0", "in": incoming[0], "reason": "score_bracket_repair",
                      "restrictions": [conflict]}]
    assert repaired["core 1"] == 1 and "core 0" not in repaired
    assert deckbuilding._validate(repaired, ["commander"], metadata, prepared["pool"])["valid"]
    assert all(deckbuilding._score_restriction_checks(
        deckbuilding._score_restriction_counts(repaired, metadata, restrictions), 2).values())
    assert sum(repaired.values()) == sum(counts.values()) == 100
    assert len(copies) < 200


def test_repair_ranked_stream_skips_higher_support_land_that_loses_required_color_source():
    from edhrec_affinity.build_styles import deckbuilding

    _, decks, _, _, metadata = fixture()
    metadata.update({
        "restricted land": metadata_card("Restricted Land", types="Land", cmc=0, produced_mana=["G"]),
        "wastes": metadata_card("Wastes", types="Basic Land — Wastes", colors=[], cmc=0),
        "colorless alternative": metadata_card("Colorless Alternative", types="Land", colors=[], cmc=0),
    })
    counts = Counter({"commander": 1, **{card_key(row["name"]): row["quantity"]
                                          for row in decks[0]["cards"]}})
    counts.update({"forest": -12, "wastes": 11, "restricted land": 1})
    restrictions = deckbuilding._observed_bracket_restrictions({}, metadata)
    restrictions["mass_land_denial"] = {"restricted land"}
    prepared = {"commander_keys": ["commander"], "commander_color_choices": [], "colors": {"G"},
                "pool": set(metadata), "support": {"colorless alternative": 2, "forest": 1},
                "distinctive_targets": []}
    repaired, swaps = deckbuilding._strict_bracket_replacements(counts, prepared, metadata, 2, restrictions)
    assert swaps[0]["out"] == "Restricted Land" and swaps[0]["in"] == "Forest"
    assert "colorless alternative" not in repaired
    assert deckbuilding._mana_diagnostics(repaired, metadata, ["commander"])["colored_land_sources"] == {"G": 25}
    assert deckbuilding._validate(repaired, ["commander"], metadata, prepared["pool"])["valid"]


def attach_score_record(data, monkeypatch, directory, *, power=6.09, bracket=4, days=0, **values):
    from edhrec_affinity.build_styles import external_validation

    original = generate(deepcopy(data))
    external_validation.record_external_validation(original, {
        "source": "edhpowerlevel.com", "source_url": original["validation"]["external_url"],
        "card_count": 100, "checked_at": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
        "playability_pct": 75, "bracket": bracket, "power_level": power, **values,
    }, directory)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, directory))
    return original


def attach_complete_restriction_record(data, monkeypatch, directory, *, restrictions=None,
                                       power=6.71, bracket=4, days=0):
    from edhrec_affinity.build_styles import external_validation

    original = generate(deepcopy(data))
    evidence = {category: [] for category in external_validation.RESTRICTION_CATEGORIES}
    evidence.update(restrictions or {})
    external_validation.record_external_validation(original, {
        "source": "edhpowerlevel.com", "source_url": original["validation"]["external_url"],
        "card_count": 100, "checked_at": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
        "playability_pct": 75, "bracket": bracket, "power_level": power,
        "bracket_restrictions": evidence,
        "restriction_check": {"status": "complete", "method": "edhpowerlevel_page",
                              "source_script_sha256": "a" * 64, "commander_names": ["Commander"]},
    }, directory)
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, directory))
    return original


def test_generated_site_link_preserves_commander_roles_separately_from_main_deck():
    from edhrec_affinity.build_styles.external_validation import _preload_cards, cardlist_hash

    result = generate(fixture())
    rows, commanders = _preload_cards(result["validation"]["external_url"])
    assert commanders == ["Commander"]
    assert cardlist_hash(rows) == cardlist_hash(result["cards"])


def test_complete_site_named_land_denial_repairs_a_card_missed_by_local_text_in_all_models(monkeypatch, tmp_path):
    data = fixture()
    rename_fixture_card(data, "Core 0", "Vorinclex, Voice of Hunger")
    # This actual class of untapping restriction is absent from the local MLD
    # regex; only the exact official observation establishes it here.
    data[4]["vorinclex, voice of hunger"]["oracle_text"] = (
        "Whenever an opponent taps a land for mana, that land doesn't untap during its controller's next untap step.")
    original = attach_complete_restriction_record(data, monkeypatch, tmp_path,
        restrictions={"mass_land_denial": ["Vorinclex, Voice of Hunger"]})
    generate_package_decks(*data, prefer_playability=True)
    for package in [*data[0]["packages"], *data[0]["alternative_models"][0]["packages"]]:
        result = package["generated_deck"]
        assert "Vorinclex, Voice of Hunger" not in {row["name"] for row in result["cards"]}
        assert result["statistics"]["score_bracket_repair"]["swap_details"][0]["restrictions"] == ["mass_land_denial"]
        assert result["validation"]["bracket"]["recommended"] == 3
        assert result["validation"]["restriction_validation"]["status"] == "pending"
        assert result["validation"]["external_rating"]["status"] == "unverified"
        assert result["card_count"] == original["card_count"] == 100 and result["validation"]["valid"]


def test_complete_site_absence_clears_a_local_land_denial_false_positive(monkeypatch, tmp_path):
    data = fixture()
    rename_fixture_card(data, "Core 0", "Charitable Levy")
    data[4]["charitable levy"]["oracle_text"] = "Each player sacrifices half the lands they control."
    original = attach_complete_restriction_record(data, monkeypatch, tmp_path, bracket=3)
    assert original["validation"]["bracket"]["recommended"] == 4
    result = generate(data)
    assert result["cards"] == original["cards"]
    assert result["validation"]["bracket"]["recommended"] == 3
    assert result["validation"]["bracket"]["mass_land_denial_count"] == 0
    assert result["validation"]["restriction_validation"]["status"] == "verified"
    assert result["validation"]["restriction_validation"]["source_script_sha256"] == "a" * 64


def test_complete_site_ordinary_turn_is_not_treated_as_chaining(monkeypatch, tmp_path):
    data = fixture()
    rename_fixture_card(data, "Core 0", "Part the Waterveil")
    data[4]["part the waterveil"]["oracle_text"] = "Take an extra turn after this one. Exile Part the Waterveil."
    original = attach_complete_restriction_record(data, monkeypatch, tmp_path, bracket=3,
        restrictions={"extra_turns": ["Part the Waterveil"]})
    result = generate(data)
    assert result["cards"] == original["cards"]
    assert result["validation"]["bracket"]["extra_turn_count"] == 1
    assert result["validation"]["bracket"]["chainable_extra_turn_count"] == 0
    assert result["validation"]["restriction_validation"]["status"] == "verified"


def test_complete_site_chaining_and_qualified_combos_require_a_new_final_list_check(monkeypatch, tmp_path):
    data = fixture()
    # Synthetic metadata intentionally offers no heuristic evidence for these.
    original = attach_complete_restriction_record(data, monkeypatch, tmp_path,
        restrictions={"chaining_extra_turns": ["Core 0"],
                      "early_combos": [["Core 1", "Core 2"]], "late_combos": [["Core 3", "Core 4"]]})
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    assert "Core 0" not in names and not {"Core 1", "Core 2"} <= names
    assert {"Core 3", "Core 4"} <= names
    assert result["validation"]["bracket"]["observed_early_combo_count"] == 0
    assert result["validation"]["bracket"]["observed_late_combo_count"] == 1
    assert result["validation"]["restriction_validation"]["status"] == "pending"
    assert result["validation"]["restriction_validation"]["deck_sha256"] != original["validation"]["deck_sha256"]
    assert "restriction_check" not in result["validation"]["external_rating"]


@pytest.mark.parametrize("days", [0, 8])
def test_legacy_numeric_or_stale_complete_records_cannot_certify_final_restrictions(monkeypatch, tmp_path, days):
    data = fixture()
    if days:
        attach_complete_restriction_record(data, monkeypatch, tmp_path, days=days,
            restrictions={"mass_land_denial": ["Core 0"]})
    else:
        attach_score_record(data, monkeypatch, tmp_path, bracket=3, power=6.71)
    result = generate(data)
    assert result["validation"]["restriction_validation"]["status"] == "pending"
    assert "Core 0" in {row["name"] for row in result["cards"]}


def test_repaired_list_becomes_verified_only_after_its_own_complete_page_observation(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = fixture()
    attach_complete_restriction_record(data, monkeypatch, tmp_path,
        restrictions={"mass_land_denial": ["Core 0"]})
    first = generate(data)
    assert first["validation"]["restriction_validation"]["status"] == "pending"
    external_validation.record_external_validation(first, {
        "source": "edhpowerlevel.com", "source_url": first["validation"]["external_url"],
        "card_count": 100, "checked_at": datetime.now(timezone.utc).isoformat(),
        "playability_pct": 75, "bracket": 3, "power_level": 6.71,
        "bracket_restrictions": {category: [] for category in external_validation.RESTRICTION_CATEGORIES},
        "restriction_check": {"status": "complete", "method": "edhpowerlevel_page",
                              "source_script_sha256": "a" * 64, "commander_names": ["Commander"]},
    }, tmp_path)
    second = generate(data)
    assert second["cards"] == first["cards"]
    assert second["validation"]["restriction_validation"]["status"] == "verified"
    assert second["validation"]["external_rating"]["status"] == "verified"


@pytest.mark.parametrize("fresh_local_inputs", [False, True])
def test_alternate_candidates_never_restore_officially_observed_disallowed_cards_or_combos(
        monkeypatch, tmp_path, fresh_local_inputs):
    data = fixture()
    if fresh_local_inputs:
        fresh_power_inputs(data)
    original = attach_complete_restriction_record(data, monkeypatch, tmp_path,
        restrictions={"mass_land_denial": ["Core 0"], "early_combos": [["Core 1", "Core 2"]]})
    data[0]["packages"][0]["generated_deck"] = deepcopy(original)
    candidates = generate_package_deck_candidates(*data, max_candidates=8)
    if fresh_local_inputs:
        assert candidates
    for candidate in candidates:
        names = {row["name"] for row in candidate["cards"]}
        assert "Core 0" not in names and not {"Core 1", "Core 2"} <= names
        assert candidate["validation"]["restriction_validation"]["status"] == "pending"
        assert candidate["statistics"]["score_bracket_repair"]["restriction_observation_sources"][0]["restriction_check"]["method"] == "edhpowerlevel_page"


def test_restore_balance_land_sacrifice_text_is_mass_land_denial():
    from edhrec_affinity.build_styles.deckbuilding import _bracket_flags

    assert _bracket_flags(metadata_card("Restore Balance", text=_RESTORE_BALANCE_TEXT)) == (False, False, True)
    assert _bracket_flags(metadata_card("Fair Land", text="Sacrifice this land: Search your library for a basic land card.")) == (False, False, False)


def test_verified_power_score_repairs_distinctive_restore_balance_without_inheriting_old_rating(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = score_repair_fixture()
    original = attach_score_record(data, monkeypatch, tmp_path)
    result = generate(data)
    repair = result["statistics"]["score_bracket_repair"]
    assert repair["status"] == "repaired"
    assert repair["from_power_level"] == 6.09 and repair["score_bracket"] == 2
    assert repair["from_deck_sha256"] == cardlist_hash(original["cards"])
    assert cardlist_hash(result["cards"]) != cardlist_hash(original["cards"])
    assert "Restore Balance" not in {row["name"] for row in result["cards"]}
    assert repair["swap_details"][0]["out"] == "Restore Balance"
    assert repair["swap_details"][0]["restrictions"] == ["mass_land_denial"]
    assert result["validation"]["external_rating"]["status"] == "unverified"
    assert "power_level" not in result["validation"]["external_rating"]
    assert result["card_count"] == 100 and result["validation"]["valid"]
    assert result["statistics"]["land_count"] == original["statistics"]["land_count"]
    assert result["validation"]["bracket"]["target"] == result["validation"]["bracket"]["recommended"] == 2
    assert all(result["validation"]["bracket"]["rule_checks"].values())
    assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "score_bracket_conflict"
    assert "[[Restore Balance]]" in result["primer"]
    assert "Replaced [[Restore Balance]]" in result["primer"]
    assert "Restore Balance" not in result["export_text"]
    pool = {row["name"] for deck in data[1] for row in deck["cards"]} | {"Commander"}
    assert {row["name"] for row in result["cards"]} <= pool
    assert "bracket-3" not in result["statistics"]["profile_slugs"]


@pytest.mark.parametrize("mode", ["stale", "missing_power", "unparseable_power", "changed_list", "already_matched"])
def test_bracket_repair_requires_a_current_exact_list_numeric_score(monkeypatch, tmp_path, mode):
    from edhrec_affinity.build_styles import external_validation

    data = score_repair_fixture()
    original = generate(deepcopy(data))
    values = {"source": "edhpowerlevel.com", "source_url": original["validation"]["external_url"],
              "card_count": 100, "checked_at": (datetime.now(timezone.utc) - timedelta(days=8 if mode == "stale" else 0)).isoformat(),
              "playability_pct": 75, "bracket": 4}
    if mode != "missing_power":
        values["power_level"] = {"unparseable_power": "6 or 8", "already_matched": 8.2}.get(mode, 6.09)
    external_validation.record_external_validation(original, values, tmp_path)
    if mode == "changed_list":
        for deck in data[1]:
            deck["cards"][1]["name"] = "Changed Core"
        data[4]["changed core"] = metadata_card("Changed Core")
    real_attach = external_validation.attach_external_validation
    monkeypatch.setattr(external_validation, "attach_external_validation", lambda deck: real_attach(deck, tmp_path))
    result = generate(data)
    assert "score_bracket_repair" not in result["statistics"]
    assert "Restore Balance" in {row["name"] for row in result["cards"]}


def test_score_repair_removes_multiple_restrictions_and_preserves_support_roles(monkeypatch, tmp_path):
    data = score_repair_fixture()
    data[4]["core 1"]["game_changer"] = True
    data[4]["core 2"]["oracle_text"] = "Take an extra turn after this one."
    data[4]["core 3"]["oracle_text"] = "Draw three cards."
    data[4]["variant b"]["oracle_text"] = "Draw two cards."
    data[4]["core 2"]["oracle_text"] += " Draw three cards."
    rename_fixture_card(data, "Core 2", "Time Warp")
    attach_score_record(data, monkeypatch, tmp_path)
    result = generate(data)
    assert result["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert len(result["statistics"]["score_bracket_repair"]["swap_details"]) == 3
    assert result["validation"]["bracket"]["game_changer_count"] == 0
    assert result["validation"]["bracket"]["extra_turn_count"] == 0
    assert result["validation"]["bracket"]["mass_land_denial_count"] == 0
    role_count = sum(row["quantity"] for row in result["cards"] if row["primary_role"] == "Card Advantage")
    assert role_count >= 2


def test_score_repair_prefers_non_distinctive_excess_game_changer_at_bracket_three(monkeypatch, tmp_path):
    data = fixture()
    data[0]["packages"][0]["top_cards"] = [{"name": "Core 0"}, {"name": "Core 1"}, {"name": "Core 2"}]
    for index in range(4):
        data[4][f"core {index}"]["game_changer"] = True
    attach_score_record(data, monkeypatch, tmp_path, power=7.0)
    result = generate(data)
    assert result["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert result["statistics"]["score_bracket_repair"]["swap_details"][0]["out"] == "Core 3"
    assert result["distinctive_cards"]["included_count"] == 3
    assert result["validation"]["bracket"]["recommended"] == 3
    assert result["validation"]["bracket"]["game_changer_count"] == 3


@pytest.mark.parametrize("commander_name", ["First Commander", "Second Commander"])
@pytest.mark.parametrize(("power", "target"), [(6.699999, 2), (6.7, 3)])
def test_three_to_two_score_boundary_applies_to_all_commanders_and_package_models(
        monkeypatch, tmp_path, commander_name, power, target):
    data = fixture()
    card = data[4].pop("commander")
    card["name"] = commander_name
    data[4][commander_name.casefold()] = card
    data[2]["commander_names"] = [commander_name]
    for sampled in data[1]:
        sampled["commanders"] = [commander_name]
    data[4]["core 0"]["game_changer"] = True
    alternative = data[0]["alternative_models"][0]
    alternative["components"] = 2
    alternative["packages"].append({**deepcopy(alternative["packages"][0]), "id": 1, "name": "Package 2"})
    for member in alternative["memberships"]:
        member["weights"] = [member["weights"][0] / 2] * 2
    original = attach_score_record(data, monkeypatch, tmp_path, power=power, bracket=3)
    assert original["validation"]["bracket"]["recommended"] == 3
    generate_package_decks(*data)
    for package in [*data[0]["packages"], *alternative["packages"]]:
        result = package["generated_deck"]
        names = {row["name"] for row in result["cards"]}
        assert result["card_count"] == 100 and result["validation"]["valid"]
        assert commander_name in names
        assert result["validation"]["bracket"]["recommended"] == target
        assert ("Core 0" in names) == (target == 3)
        if target == 2:
            repair = result["statistics"]["score_bracket_repair"]
            assert repair["status"] == "repaired" and repair["from_bracket"] == 3
            assert repair["policy_version"] == 2 and all(repair["rule_checks"].values())
            assert result["validation"]["external_rating"]["status"] == "unverified"
        else:
            assert "score_bracket_repair" not in result["statistics"]


@pytest.mark.parametrize("prefer_playability", [False, True])
def test_six_point_seven_one_replaces_repeatable_extra_turns_in_auto_and_alternative_packages(
        monkeypatch, tmp_path, prefer_playability):
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = fixture()
    data[4]["commander"]["color_identity"] = ["G", "U"]
    data[4].pop("core 0")
    data[4]["timestream navigator"] = metadata_card(
        "Timestream Navigator", types="Creature — Human Pirate Wizard", colors=["U"],
        text=("Ascend (If you control ten or more permanents, you get the city's blessing for the rest of the game.)\n"
              "{2}{U}{U}, {T}, Put this creature on the bottom of its owner's library: Take an extra turn after this one. "
              "Activate only if you have the city's blessing."))
    data[4]["core 1"]["oracle_text"] = "Draw three cards."
    data[4]["core 2"]["oracle_text"] = "{T}: Add {G}."
    for sampled in data[1]:
        sampled["source_bracket"] = 2
        next(row for row in sampled["cards"] if row["name"] == "Core 0")["name"] = "Timestream Navigator"
    for package in [*data[0]["packages"], *data[0]["alternative_models"][0]["packages"]]:
        package["top_cards"] = [{"name": "Timestream Navigator"}]
    data[0]["packages"].append({**deepcopy(data[0]["packages"][0]), "id": 1, "name": "Package 2"})
    for member in data[0]["decks"]:
        member["memberships"] = [member["memberships"][0] / 2] * 2
    alternative = data[0]["alternative_models"][0]
    alternative["components"] = 2
    alternative["packages"] = deepcopy(data[0]["packages"])
    for member in alternative["memberships"]:
        member["weights"] = [member["weights"][0] / 2] * 2

    original = attach_score_record(data, monkeypatch, tmp_path, power=6.71, bracket=4)
    assert original["validation"]["bracket"]["target"] == 2
    assert original["validation"]["bracket"]["recommended"] == 4
    assert original["validation"]["bracket"]["extra_turn_count"] == 1
    original_hash = cardlist_hash(original["cards"])
    observed_pool = {row["name"] for sampled in data[1] for row in sampled["cards"]} | {"Commander"}

    # The same score-based construction rule applies to Auto and a selected
    # alternative model, and survives a subsequent full rebuild.
    hashes = []
    for _ in range(2):
        generate_package_decks(*data, prefer_playability=prefer_playability)
        current_hashes = []
        for package in [*data[0]["packages"], *alternative["packages"]]:
            result = package["generated_deck"]
            names = {row["name"] for row in result["cards"]}
            repair = result["statistics"]["score_bracket_repair"]
            assert repair["status"] == "repaired" and repair["from_deck_sha256"] == original_hash
            assert repair["from_power_level"] == 6.71 and repair["score_bracket"] == 3
            assert repair["from_bracket"] == 4 and all(repair["rule_checks"].values())
            assert repair["swap_details"][0]["out"] == "Timestream Navigator"
            assert repair["swap_details"][0]["restrictions"] == ["chaining_extra_turns"]
            assert "Timestream Navigator" not in names and names <= observed_pool
            assert {"Commander", "Core 1", "Core 2"} <= names
            assert result["card_count"] == 100 and all(result["validation"]["checks"].values())
            assert result["statistics"]["land_count"] == original["statistics"]["land_count"] == 36
            assert result["validation"]["bracket"]["target"] == result["validation"]["bracket"]["recommended"] == 3
            assert result["validation"]["bracket"]["extra_turn_count"] == 0
            assert result["validation"]["external_rating"]["status"] == "unverified"
            assert "power_level" not in result["validation"]["external_rating"]
            assert result["distinctive_cards"]["omitted"][0]["reason_code"] == "score_bracket_conflict"
            current_hashes.append(cardlist_hash(result["cards"]))
        if hashes:
            assert current_hashes == hashes
        hashes = current_hashes


def fresh_power_inputs(data, *, rank=100, price=".15"):
    checked_at = datetime.now(timezone.utc).isoformat()
    for card in data[4].values():
        card.update(edhrec_rank=rank, reserved=False, colors=card["color_identity"].copy(),
            prices={"usd": price, "usd_foil": None, "usd_etched": None}, layout="normal", checked_at=checked_at)
    return data


def test_local_numeric_power_drives_all_package_brackets_without_a_site_observation():
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = fresh_power_inputs(fixture())
    for sampled in data[1]:
        sampled["source_bracket"] = 2
    generate_package_decks(*data)
    for package in [*data[0]["packages"], *data[0]["alternative_models"][0]["packages"]]:
        deck = package["generated_deck"]
        rating = deck["validation"]["calculated_power"]
        assert rating["status"] == "calculated" and rating["source"] == "local_public_formula"
        assert rating["power_level"] == 7.24 and rating["score_bracket"] == 3
        assert rating["deck_sha256"] == deck["validation"]["deck_sha256"] == cardlist_hash(deck["cards"])
        assert deck["validation"]["bracket"]["target"] == deck["validation"]["bracket"]["recommended"] == 3
        assert deck["validation"]["external_rating"]["status"] == "unverified"


@pytest.mark.parametrize("distinctive_changers", [1, 3, 4])
def test_anchor_numeric_bracket_allows_distinctive_game_changers_despite_lower_source_annotations(distinctive_changers):
    data = fixture()
    for sampled in data[1]:
        sampled["source_bracket"] = 2
    cards = [metadata_card(f"Distinctive Changer {index}", game_changer=True)
             for index in range(distinctive_changers)]
    add_observed_core(data, cards)
    for package in data[0]["alternative_models"][0]["packages"]:
        package["top_cards"] = deepcopy(data[0]["packages"][0]["top_cards"])
    fresh_power_inputs(data)
    generate_package_decks(*data)
    for package in [*data[0]["packages"], *data[0]["alternative_models"][0]["packages"]]:
        deck = package["generated_deck"]
        assert deck["validation"]["calculated_power"]["score_bracket"] == 3
        assert deck["validation"]["bracket"]["recommended"] == 3
        assert deck["validation"]["bracket"]["game_changer_count"] == min(3, distinctive_changers)
        assert deck["distinctive_cards"]["included_count"] == min(3, distinctive_changers)
        assert all(row["reason_code"] == "bracket_conflict" for row in deck["distinctive_cards"]["omitted"])
        assert deck["validation"]["valid"] and deck["card_count"] == 100


def test_verified_exact_numeric_power_takes_priority_over_local_estimate(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = fixture()
    original = attach_score_record(data, monkeypatch, tmp_path, power=6.5, bracket=2)
    fresh_power_inputs(data)
    result = generate(data)
    assert cardlist_hash(result["cards"]) == cardlist_hash(original["cards"])
    assert result["validation"]["calculated_power"]["score_bracket"] == 3
    assert result["validation"]["external_rating"]["power_level"] == 6.5
    assert result["validation"]["external_rating"]["status"] == "verified"
    assert result["validation"]["bracket"]["target"] == result["validation"]["bracket"]["recommended"] == 2


@pytest.mark.parametrize("include_chain", [False, True])
def test_one_shot_extra_turn_remains_legal_at_three_while_named_chain_is_replaced(include_chain):
    data = fixture()
    data[4]["core 0"]["oracle_text"] = "Take an extra turn after this one. Exile Part the Waterveil."
    rename_fixture_card(data, "Core 0", "Part the Waterveil")
    if include_chain:
        data[4]["core 1"]["oracle_text"] = "Buyback—Sacrifice three Islands. Take an extra turn after this one."
        rename_fixture_card(data, "Core 1", "Walk the Aeons")
    fresh_power_inputs(data)
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    assert "Part the Waterveil" in names and "Walk the Aeons" not in names
    assert result["validation"]["bracket"]["target"] == result["validation"]["bracket"]["recommended"] == 3
    assert result["validation"]["bracket"]["extra_turn_count"] == 1
    assert result["validation"]["bracket"]["chainable_extra_turn_count"] == 0
    assert all(result["validation"]["bracket"]["rule_checks"].values())
    if include_chain:
        repair = result["statistics"]["score_bracket_repair"]
        assert repair["rating_kind"] == "calculated" and repair["source"] == "local_public_formula"
        assert len(repair["swap_details"]) == 1 and repair["swap_details"][0]["out"] == "Walk the Aeons"
    else:
        assert "score_bracket_repair" not in result["statistics"]


@pytest.mark.parametrize(("target", "turns", "recommended"), [
    (1, 1, 2), (2, 2, 2), (2, 3, 3), (3, 3, 3), (3, 4, 4),
])
def test_ordinary_extra_turn_counts_use_the_public_bracket_limits(target, turns, recommended):
    data = fixture()
    for sampled in data[1]:
        sampled["source_bracket"] = target
    for index in range(turns):
        data[4][f"core {index}"]["oracle_text"] = "Take an extra turn after this one. Exile this spell."
    bracket = generate(data)["validation"]["bracket"]
    assert bracket["extra_turn_count"] == turns and bracket["chainable_extra_turn_count"] == 0
    assert bracket["recommended"] == recommended


def test_fresh_local_repair_survives_repeated_generation_with_current_score():
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = fixture()
    data[4]["core 0"]["oracle_text"] = "Take an extra turn after this one."
    rename_fixture_card(data, "Core 0", "Time Warp")
    fresh_power_inputs(data)
    first = generate(data)
    second = generate(data)
    assert first["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert second["statistics"]["score_bracket_repair"]["status"] == "retained"
    assert cardlist_hash(second["cards"]) == cardlist_hash(first["cards"])
    assert second["validation"]["calculated_power"]["status"] == "calculated"
    assert second["validation"]["calculated_power"]["score_bracket"] == second["validation"]["bracket"]["recommended"] == 3
    assert second["validation"]["external_rating"]["status"] == "unverified"


@pytest.mark.parametrize("blocked_second_step", [False, True])
def test_replacements_recalculate_power_and_repair_new_lower_bracket_or_keep_original(monkeypatch, blocked_second_step):
    from edhrec_affinity.build_styles import power_scoring
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = fixture()
    for sampled in data[1]:
        sampled["source_bracket"] = 4
    data[4]["core 0"]["oracle_text"] = "Take an extra turn after this one."
    rename_fixture_card(data, "Core 0", "Time Warp")
    data[4]["core 1"]["game_changer"] = True
    if blocked_second_step:
        data[4]["core 1"]["oracle_text"] = "Draw two cards."
    checked_at = datetime.now(timezone.utc).isoformat()
    def observed_calculation(cards, metadata, **kwargs):
        names = {row["name"] for row in cards}
        power, bracket = (7.0, 3) if "Time Warp" in names else (6.5, 2) if "Core 1" in names else (6.4, 2)
        return {"status": "calculated", "source": "local_public_formula", "power_level": power,
            "score_bracket": bracket, "checked_at": checked_at, "source_url": power_scoring.ALGORITHM_SOURCE_URL,
            "deck_sha256": cardlist_hash(cards), "card_count": 100}
    monkeypatch.setattr(power_scoring, "calculate_power_rating", observed_calculation)
    result = generate(data)
    repair = result["statistics"]["score_bracket_repair"]
    names = {row["name"] for row in result["cards"]}
    assert result["card_count"] == 100 and result["validation"]["valid"]
    if blocked_second_step:
        assert repair["status"] == "blocked" and not repair["swap_details"]
        assert {"Time Warp", "Core 1"} <= names
        assert result["validation"]["calculated_power"]["power_level"] == 7.0
    else:
        assert repair["status"] == "repaired" and repair["initial_score_bracket"] == 3
        assert repair["score_bracket"] == 2 and repair["iterations"] == 3
        assert not {"Time Warp", "Core 1"} & names
        assert result["validation"]["calculated_power"]["power_level"] == 6.4
        assert result["validation"]["bracket"]["recommended"] == 2
        assert all(repair["rule_checks"].values())


@pytest.mark.parametrize("blocker", ["commander", "support_floor", "unknown_external"])
def test_impossible_score_repairs_are_explicit_and_never_commit_a_partial_list(monkeypatch, tmp_path, blocker):
    data = score_repair_fixture() if blocker != "unknown_external" else fixture()
    if blocker == "commander":
        data[4]["commander"]["oracle_text"] = "Destroy all lands."
    elif blocker == "support_floor":
        data[4]["restore balance"]["oracle_text"] += " Draw three cards."
    original = attach_score_record(data, monkeypatch, tmp_path)
    result = generate(data)
    assert result["statistics"]["score_bracket_repair"]["status"] == "blocked"
    assert result["statistics"]["score_bracket_repair"]["swap_details"] == []
    assert result["cards"] == original["cards"]
    assert result["validation"]["external_rating"]["status"] == "verified"
    assert result["validation"]["bracket"]["recommended"] == original["validation"]["bracket"]["recommended"]


def test_playability_preference_keeps_repaired_core_exception_over_unscored_offending_alternates(monkeypatch, tmp_path):
    data = score_repair_fixture()
    attach_score_record(data, monkeypatch, tmp_path)
    generate_package_decks(*data, prefer_playability=True)
    chosen = data[0]["packages"][0]["generated_deck"]
    assert chosen["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert "Restore Balance" not in {row["name"] for row in chosen["cards"]}
    assert chosen["validation"]["bracket"]["recommended"] == 2


@pytest.mark.parametrize(("power", "target", "late_retained"), [(6.09, 2, False), (7.0, 3, True)])
def test_observed_exact_list_combos_are_broken_only_when_disallowed_in_score_bracket(monkeypatch, tmp_path, power, target, late_retained):
    data = score_repair_fixture()
    add_observed_core(data, [metadata_card(f"Fair Replacement {index}") for index in range(6)], include_in_package=False)
    evidence = {"game_changers": ["Core 1"], "extra_turns": ["Core 2"], "mass_land_denial": ["Restore Balance"],
                "early_combos": [["Core 4", "Core 5"]], "late_combos": [["Core 6", "Core 7"]]}
    attach_score_record(data, monkeypatch, tmp_path, power=power, bracket_restrictions=evidence)
    result = generate(data)
    names = {row["name"] for row in result["cards"]}
    repair = result["statistics"]["score_bracket_repair"]
    assert repair["status"] == "repaired"
    assert result["validation"]["bracket"]["recommended"] == target
    assert not {"Core 4", "Core 5"} <= names
    assert ({"Core 6", "Core 7"} <= names) == late_retained
    assert "Core 2" not in names and "Restore Balance" not in names
    assert ("Core 1" in names) == (target == 3)
    assert all(repair["rule_checks"].values())
    assert repair["observed_restrictions"] == evidence
    assert result["validation"]["bracket"]["observed_early_combo_count"] == 0
    assert result["validation"]["bracket"]["observed_late_combo_count"] == late_retained
    assert result["validation"]["external_rating"]["status"] == "unverified"


def test_overlapping_observed_combos_can_share_one_replacement_without_reinsertion(monkeypatch, tmp_path):
    data = fixture()
    evidence = {"early_combos": [["Core 0", "Core 1"], ["Core 1", "Core 2"]]}
    attach_score_record(data, monkeypatch, tmp_path, bracket_restrictions=evidence)
    result = generate(data)
    repair = result["statistics"]["score_bracket_repair"]
    assert repair["status"] == "repaired" and len(repair["swap_details"]) == 1
    assert repair["swap_details"][0]["out"] == "Core 1"
    assert result["distinctive_cards"]["included_count"] == 1
    assert result["validation"]["bracket"]["observed_early_combo_count"] == 0



def test_rated_existing_deck_is_a_repair_seed_when_current_anchor_selection_changes(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = score_repair_fixture()
    original = attach_score_record(data, monkeypatch, tmp_path)
    original_hash = cardlist_hash(original["cards"])
    for row in data[0]["decks"]:
        row["memberships"] = [.1 if row["id"] == "a" else 1.0]
    for row in data[0]["alternative_models"][0]["memberships"]:
        row["weights"] = [.1 if row["id"] == "a" else 1.0]
    data[4]["new anchor card"] = metadata_card("New Anchor Card", types="Artifact")
    for deck in data[1][1:]:
        next(row for row in deck["cards"] if row["name"] == "Core 1")["name"] = "New Anchor Card"
    fresh = generate(deepcopy(data))
    assert cardlist_hash(fresh["cards"]) != original_hash
    assert "score_bracket_repair" not in fresh["statistics"]
    for package in [*data[0]["packages"], *data[0]["alternative_models"][0]["packages"]]:
        package["generated_deck"] = deepcopy(original)
    generate_package_decks(*data, prefer_playability=True)
    chosen = data[0]["packages"][0]["generated_deck"]
    assert chosen["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert chosen["statistics"]["score_bracket_repair"]["from_deck_sha256"] == original_hash
    assert chosen["validation"]["bracket"]["recommended"] == 2
    assert "Restore Balance" not in {row["name"] for row in chosen["cards"]}


def test_existing_rated_deck_seed_cannot_use_cards_outside_current_observed_pool(monkeypatch, tmp_path):
    data = score_repair_fixture()
    original = attach_score_record(data, monkeypatch, tmp_path)
    for deck in data[1]:
        for row in deck["cards"]:
            if row["name"] == "Restore Balance":
                row["name"] = "Core 0"
    data[4]["core 0"] = metadata_card("Core 0")
    data[0]["packages"][0]["generated_deck"] = original
    chosen = generate(data)
    assert "score_bracket_repair" not in chosen["statistics"]
    assert "Restore Balance" not in {row["name"] for row in chosen["cards"]}



def test_successful_score_repair_is_reproducible_on_repeated_full_regeneration(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles.external_validation import cardlist_hash

    data = score_repair_fixture()
    original = attach_score_record(data, monkeypatch, tmp_path)
    first = generate(data)
    assert first["statistics"]["score_bracket_repair"]["from_cards"] == [
        {"name": row["name"], "quantity": row["quantity"]} for row in original["cards"]]
    first_hash = cardlist_hash(first["cards"])
    second = generate(data)
    assert cardlist_hash(second["cards"]) == first_hash
    assert second["statistics"]["score_bracket_repair"]["status"] == "repaired"
    assert second["validation"]["external_rating"]["status"] == "unverified"
    assert "Restore Balance" not in second["export_text"]


def test_expired_source_score_retains_an_already_applied_legal_repair_without_new_swaps(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = score_repair_fixture()
    original = attach_score_record(data, monkeypatch, tmp_path)
    first = generate(data)
    first_hash = external_validation.cardlist_hash(first["cards"])
    old_swaps = deepcopy(first["statistics"]["score_bracket_repair"]["swap_details"])
    external_validation.record_external_validation(original, {
        "source": "edhpowerlevel.com", "source_url": original["validation"]["external_url"], "card_count": 100,
        "checked_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        "playability_pct": 75, "power_level": 6.09, "bracket": 4,
    }, tmp_path)
    generate_package_decks(*data, prefer_playability=True)
    retained = data[0]["packages"][0]["generated_deck"]
    assert external_validation.cardlist_hash(retained["cards"]) == first_hash
    assert retained["statistics"]["score_bracket_repair"]["status"] == "retained"
    assert retained["statistics"]["score_bracket_repair"]["swap_details"] == old_swaps
    assert retained["statistics"]["score_bracket_repair"]["original_rating_status"] == "stale"
    assert retained["validation"]["external_rating"]["status"] == "unverified"
    assert retained["validation"]["bracket"]["recommended"] == 2
    assert "historical evidence" in retained["primer"]
    assert "Restore Balance" not in retained["export_text"]


def test_fresh_matching_check_of_repaired_list_is_retained_over_old_anchor_bracket(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = score_repair_fixture()
    attach_score_record(data, monkeypatch, tmp_path)
    first = generate(data)
    external_validation.record_external_validation(first, {
        "source": "edhpowerlevel.com", "source_url": first["validation"]["external_url"], "card_count": 100,
        "checked_at": datetime.now(timezone.utc).isoformat(), "playability_pct": 75, "power_level": 6.0, "bracket": 2,
    }, tmp_path)
    second = generate(data)
    assert second["cards"] == first["cards"]
    assert second["statistics"]["score_bracket_repair"]["status"] == "retained"
    assert second["validation"]["bracket"]["recommended"] == 2
    assert second["validation"]["external_rating"]["status"] == "verified"
    assert second["validation"]["external_rating"]["power_level"] == 6.0



def test_new_metadata_cannot_use_an_expired_score_to_make_further_swaps_or_restore_removed_cards(monkeypatch, tmp_path):
    from edhrec_affinity.build_styles import external_validation

    data = score_repair_fixture()
    original = attach_score_record(data, monkeypatch, tmp_path)
    first = generate(data)
    first_hash = external_validation.cardlist_hash(first["cards"])
    replacement = first["statistics"]["score_bracket_repair"]["swap_details"][0]["in"]
    external_validation.record_external_validation(original, {
        "source": "edhpowerlevel.com", "source_url": original["validation"]["external_url"], "card_count": 100,
        "checked_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        "playability_pct": 75, "power_level": 6.09, "bracket": 4,
    }, tmp_path)
    data[4][card_key(replacement)]["game_changer"] = True
    generate_package_decks(*data, prefer_playability=True)
    reviewed = data[0]["packages"][0]["generated_deck"]
    assert external_validation.cardlist_hash(reviewed["cards"]) == first_hash
    assert reviewed["statistics"]["score_bracket_repair"]["status"] == "blocked"
    assert reviewed["statistics"]["score_bracket_repair"]["retention_blocked"]
    assert not reviewed["validation"]["bracket"]["rule_checks"]["game_changers"]
    assert reviewed["validation"]["bracket"]["recommended"] == 3
    assert reviewed["statistics"]["recommended_bracket"] == 3
    assert "Restore Balance" not in reviewed["export_text"]
    assert reviewed["validation"]["external_rating"]["status"] == "unverified"
