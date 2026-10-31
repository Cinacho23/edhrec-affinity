"""Only explicit EDHREC flavor references relax exact commander-name matching."""

import json

import pytest

from edhrec_affinity.build_styles.__main__ import collect_evidence, parser
from edhrec_affinity.build_styles.analysis import prepare_decks
from edhrec_affinity.build_styles.sources import (
    CommanderAliasCatalog, card_key, commander_card_alias_groups, commander_printing_ids,
    enrich_commander_name_groups,
)


BRINELIN = "Brinelin, the Moon Kraken"
YOSHIMARU = "Yoshimaru, Ever Faithful"
DOGMEAT = "Dogmeat, Constant Companion"
TORGAL = "Torgal, Clive's Companion"


def pair(*names, aliases=()):
    return {"names": list(names), "name": " // ".join(names),
            "flavor_refs": [{"name": " // ".join(group)} for group in aliases]}


def groups_as_sets(groups):
    return {frozenset(group) for group in groups}


def test_cached_canonical_references_accept_both_alternate_names_without_guessing(tmp_path):
    canonical = pair(BRINELIN, YOSHIMARU, aliases=[(BRINELIN, DOGMEAT), (BRINELIN, TORGAL)])
    source = tmp_path / "canonical"
    source.mkdir()
    (source / "base.json").write_text(json.dumps({"container": {"json_dict": {"card": canonical}}}))
    catalog = CommanderAliasCatalog(tmp_path)
    expected = {frozenset([BRINELIN, name]) for name in (YOSHIMARU, DOGMEAT, TORGAL)}
    assert groups_as_sets(catalog.groups(pair(BRINELIN, DOGMEAT))) == expected
    assert groups_as_sets(catalog.groups(pair(TORGAL, BRINELIN))) == expected

    # This page did not exist during the initial scan. Registering its explicit
    # references adds them without repeatedly scanning or parsing the source cache.
    vial = pair("Vial Smasher the Fierce", "Numa, Joraga Chieftain",
                aliases=[("Clive Rosfield", "Numa, Joraga Chieftain")])
    catalog.groups(vial)
    assert frozenset(vial["names"]) in groups_as_sets(
        catalog.groups(pair("Clive Rosfield", "Numa, Joraga Chieftain")))


def test_alias_catalog_rejects_incomplete_references_and_ignores_commerce_hints():
    card = pair("Clive Rosfield", "Numa, Joraga Chieftain")
    card.update(scryfall_uri="https://scryfall.com/card/dsc/239/vial-smasher-the-fierce",
                prices={"example": {"slug": "Vial Smasher the Fierce"}},
                flavor_refs=[{"name": "Vial Smasher the Fierce"},
                             {"name": "Numa, Joraga Chieftain // Numa, Joraga Chieftain"},
                             {"names": ["Vial Smasher the Fierce", None]}, None])
    assert CommanderAliasCatalog().groups(card) == [card["names"]]


def test_matching_keeps_complete_identity_and_excludes_canonical_commander_from_features():
    target = pair(BRINELIN, DOGMEAT)
    canonical = pair(BRINELIN, YOSHIMARU, aliases=[(BRINELIN, DOGMEAT), (BRINELIN, TORGAL)])
    aliases = CommanderAliasCatalog(cards=[canonical]).groups(target)
    deck = {"id": "canonical", "commanders": [YOSHIMARU, BRINELIN], "cards": [
        {"name": "Forest", "quantity": 97}, {"name": "Engine", "quantity": 1},
        {"name": YOSHIMARU, "quantity": 1},
    ]}
    invalid = [
        {**deck, "id": "wrong-partner", "commanders": [YOSHIMARU, "Other Commander"]},
        {**deck, "id": "missing", "commanders": [YOSHIMARU]},
        {**deck, "id": "extra", "commanders": [YOSHIMARU, BRINELIN, DOGMEAT]},
        {**deck, "id": "duplicate", "commanders": [YOSHIMARU, YOSHIMARU]},
    ]
    kept, rejected, duplicates = prepare_decks([deck, *invalid], target["names"], commander_name_groups=aliases)
    assert [row["id"] for row in kept] == ["canonical"]
    assert kept[0]["card_count"] == 100
    assert set(kept[0]["names"].values()) == {"Forest", "Engine"}
    assert len(rejected) == 4 and all(row["reason"] == "Commander mismatch" for row in rejected)
    assert duplicates == 0
    assert not prepare_decks([deck], target["names"])[0]  # Unknown aliases remain strict.


def test_single_double_faced_commander_name_is_not_split_into_two_commanders():
    name = "Front Face // Back Face"
    card = {"name": name, "names": [name], "flavor_refs": [{"name": "Alternate Front // Alternate Back"}]}
    assert CommanderAliasCatalog().groups(card) == [[name], ["Alternate Front // Alternate Back"]]


def test_collection_saves_explicit_alias_groups_for_offline_analysis(tmp_path):
    root = tmp_path / "sources" / "brinelin-dogmeat"
    root.mkdir(parents=True)
    card = pair(BRINELIN, DOGMEAT)
    card.update(num_decks=200, url="/commanders/brinelin-dogmeat")
    (root / "base.json").write_text(json.dumps({"container": {"json_dict": {
        "card": card, "cardlists": [],
    }}}))
    catalog = CommanderAliasCatalog(cards=[pair(BRINELIN, YOSHIMARU, aliases=[(BRINELIN, DOGMEAT)])])
    args = parser().parse_args(["--commander", "brinelin-dogmeat", "--cache-dir", str(root), "--sample-decks", "0"])
    evidence = collect_evidence(args, commander_alias_provider=catalog)
    assert evidence["base"]["commander_names"] == [BRINELIN, DOGMEAT]
    assert frozenset([BRINELIN, YOSHIMARU]) in groups_as_sets(evidence["base"]["commander_name_groups"])


def test_individual_aliases_use_one_name_differences_and_transitive_links_without_partner_order():
    configurations = [[BRINELIN, DOGMEAT], [YOSHIMARU, BRINELIN], [BRINELIN, TORGAL]]
    assert groups_as_sets(commander_card_alias_groups(configurations)) == {frozenset([DOGMEAT, YOSHIMARU, TORGAL])}
    assert commander_card_alias_groups([["First", "Second"], ["New First", "New Second"]]) == []
    assert commander_card_alias_groups([["Front // Back"], ["Alternate Front // Alternate Back"]]) == [
        ["Alternate Front // Alternate Back", "Front // Back"]]


def test_individual_aliases_reject_malformed_groups_and_aliases_that_would_collapse_partners():
    groups = [[BRINELIN, YOSHIMARU], [BRINELIN, DOGMEAT], [YOSHIMARU],
              [BRINELIN, None], [DOGMEAT, DOGMEAT], "not a configuration"]
    assert groups_as_sets(commander_card_alias_groups(groups)) == {frozenset([DOGMEAT, YOSHIMARU])}
    assert commander_card_alias_groups([["A", "B"], ["A", "C"], ["B", "C"]]) == []
    for malformed in (None, {}, "Commander", [], [["A", "A"], ["A", "B"]], [[None]]):
        assert commander_card_alias_groups(malformed) == []


def test_commander_printing_hints_match_constituent_names_not_partner_order_and_omit_conflicts():
    first, second, other = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
                            "33333333-3333-3333-3333-333333333333")
    card = pair(BRINELIN, DOGMEAT)
    card.update(id=other, cards=[{"name": DOGMEAT, "id": second}, {"name": BRINELIN, "id": first},
                                {"name": "Unrelated Card", "id": other}])
    assert commander_printing_ids(card) == {BRINELIN: first, DOGMEAT: second}
    card["cards"].append({"name": DOGMEAT, "id": other})
    assert commander_printing_ids(card) == {BRINELIN: first}
    assert commander_printing_ids({**pair("Front // Back"), "id": first}) == {"Front // Back": first}
    assert commander_printing_ids({**pair(DOGMEAT), "id": "not-a-uuid"}) == {}


def verified_name_metadata(name, oracle, alternate=None):
    card = {"name": name, "oracle_id": oracle, "layout": "normal", "card_faces": []}
    if alternate:
        card["name_aliases"] = [{"name": alternate, "source_field": "printed_name", "lang": "en",
                                 "printing_id": "verified-printing", "oracle_id": oracle, "canonical_name": name}]
    return card


def test_canonical_configuration_requires_every_commander_and_independent_name_proof():
    base = {"commander_names": [BRINELIN, DOGMEAT], "commander_name_groups": [[BRINELIN, DOGMEAT]]}
    canonical = verified_name_metadata(YOSHIMARU, "yoshimaru-oracle", DOGMEAT)
    metadata = {card_key(YOSHIMARU): canonical, card_key(BRINELIN): verified_name_metadata(BRINELIN, "brinelin-oracle")}
    assert enrich_commander_name_groups(base, metadata) == [[BRINELIN, DOGMEAT], [BRINELIN, YOSHIMARU]]
    assert enrich_commander_name_groups(base, {card_key(YOSHIMARU): canonical}) == [[BRINELIN, DOGMEAT]]
    # A caller-provided key cannot substitute for a Scryfall or EDHREC proof.
    metadata[card_key(DOGMEAT)] = metadata[card_key(YOSHIMARU)] = verified_name_metadata(YOSHIMARU, "yoshimaru-oracle")
    assert enrich_commander_name_groups(base, metadata) == [[BRINELIN, DOGMEAT]]


def test_canonical_configuration_rejects_wrong_proof_identity_and_duplicate_commanders():
    base = {"commander_names": [BRINELIN, DOGMEAT]}
    canonical = verified_name_metadata(YOSHIMARU, "yoshimaru-oracle", DOGMEAT)
    metadata = {card_key(YOSHIMARU): canonical, card_key(BRINELIN): verified_name_metadata(BRINELIN, "brinelin-oracle")}
    canonical["name_aliases"][0]["oracle_id"] = "unrelated-oracle"
    assert enrich_commander_name_groups(base, metadata) == [[BRINELIN, DOGMEAT]]
    canonical["name_aliases"][0]["oracle_id"] = "yoshimaru-oracle"
    metadata[card_key(BRINELIN)]["oracle_id"] = "yoshimaru-oracle"
    assert enrich_commander_name_groups(base, metadata) == [[BRINELIN, DOGMEAT]]


def test_canonical_configuration_can_follow_existing_unambiguous_edhrec_card_aliases():
    base = {"commander_names": [BRINELIN, DOGMEAT],
            "commander_name_groups": [[BRINELIN, DOGMEAT], [TORGAL, BRINELIN]]}
    metadata = {card_key(YOSHIMARU): verified_name_metadata(YOSHIMARU, "yoshimaru-oracle", TORGAL),
                card_key(BRINELIN): verified_name_metadata(BRINELIN, "brinelin-oracle")}
    assert enrich_commander_name_groups(base, metadata)[-1] == [BRINELIN, YOSHIMARU]
    metadata[card_key(DOGMEAT)] = verified_name_metadata(DOGMEAT, "conflicting-actual-card")
    assert enrich_commander_name_groups(base, metadata) == base["commander_name_groups"]


FRONT = "Blitzwing, Cruel Tormentor"
BACK = "Blitzwing, Adaptive Assailant"
FRONT_ID = "11111111-1111-1111-1111-111111111111"
PARTNER_ID = "22222222-2222-2222-2222-222222222222"


def faced_card(layout="transform"):
    return {"name": FRONT, "names": [FRONT, BACK], "layout": layout, "id": FRONT_ID,
        "cards": [{"name": FRONT, "layout": layout, "id": FRONT_ID},
                  {"name": BACK, "layout": layout, "id": FRONT_ID}]}


def test_face_names_describe_one_physical_commander_and_accept_front_or_whole():
    from edhrec_affinity.build_styles.sources import commander_names
    card = faced_card()
    groups = CommanderAliasCatalog().groups(card)
    assert commander_names(card) == [FRONT]
    assert groups_as_sets(groups) == {frozenset([FRONT]), frozenset([f"{FRONT} // {BACK}"])}
    assert commander_printing_ids(card) == {FRONT: FRONT_ID}
    deck = {"id": "front", "commanders": [FRONT], "cards": [{"name": "Forest", "quantity": 98},
        {"name": "Engine", "quantity": 1}, {"name": f"{FRONT} // {BACK}", "quantity": 1}]}
    kept, rejected, _ = prepare_decks([deck], commander_names(card), commander_name_groups=groups)
    assert len(kept) == 1 and not rejected and kept[0]["card_count"] == 100
    assert card_key(f"{FRONT} // {BACK}") not in kept[0]["counts"]
    whole = {**deck, "id": "whole", "commanders": [f"{FRONT} // {BACK}"]}
    assert len(prepare_decks([whole], commander_names(card), commander_name_groups=groups)[0]) == 1
    for actual in ([BACK], [FRONT, BACK], ["Unrelated Commander"]):
        invalid = {**deck, "commanders": actual}
        assert prepare_decks([invalid], commander_names(card), commander_name_groups=groups)[1][0]["reason"] == "Commander mismatch"


def test_supported_face_layouts_and_partial_printing_hints_never_create_two_roles():
    from edhrec_affinity.build_styles.sources import commander_names
    for layout in ("transform", "modal_dfc", "flip", "adventure", "prepare", "meld", "reversible_card"):
        card = faced_card(layout)
        assert commander_names(card) == [FRONT]
        card["cards"] = card["cards"][:1]
        assert commander_names(card) == [FRONT]
        assert [f"{FRONT} // {BACK}"] in CommanderAliasCatalog().groups(card)
        card.pop("cards")
        assert commander_names(card) == [FRONT]


def test_real_partner_with_a_double_faced_card_retains_two_physical_commanders():
    from edhrec_affinity.build_styles.sources import commander_names
    card = faced_card()
    card.update(name=f"{FRONT} // {BACK} // Partner", names=[FRONT, BACK, "Partner"], layout="normal")
    card["cards"] = [{"name": "Partner", "layout": "normal", "id": PARTNER_ID}, *reversed(card["cards"])]
    assert commander_names(card) == [FRONT, "Partner"]
    groups = CommanderAliasCatalog().groups(card)
    assert groups_as_sets(groups) == {frozenset([FRONT, "Partner"]), frozenset([f"{FRONT} // {BACK}", "Partner"])}
    assert commander_printing_ids(card) == {FRONT: FRONT_ID, "Partner": PARTNER_ID}
    deck = {"id": "partner", "commanders": ["Partner", FRONT], "cards": [{"name": "Forest", "quantity": 97}, {"name": "Engine", "quantity": 1}]}
    assert len(prepare_decks([deck], commander_names(card), commander_name_groups=groups)[0]) == 1
    assert not prepare_decks([{**deck, "commanders": [BACK, "Partner"]}], commander_names(card), commander_name_groups=groups)[0]


def test_partial_partner_printing_hints_cannot_drop_the_missing_commander():
    from edhrec_affinity.build_styles.sources import commander_names
    card = pair(BRINELIN, DOGMEAT)
    card["cards"] = [{"name": BRINELIN, "id": FRONT_ID, "layout": "normal"}]
    assert commander_names(card) == [BRINELIN, DOGMEAT]
    assert CommanderAliasCatalog().groups(card) == [[BRINELIN, DOGMEAT]]


def test_conflicting_face_printing_identities_fail_closed():
    import pytest
    from edhrec_affinity.build_styles.sources import commander_names
    card = faced_card()
    card["cards"].append({"name": FRONT, "layout": "transform", "id": PARTNER_ID})
    with pytest.raises(ValueError, match="conflicting printing"):
        commander_names(card)
    assert CommanderAliasCatalog(cards=[card])._names == {}


def test_flavor_references_keep_full_face_names_but_never_enable_standalone_backs():
    card = faced_card()
    card["flavor_refs"] = [{"name": "Alternate Front // Alternate Back"}, {"name": BACK},
        {"name": "Alternate Front", "names": ["Alternate Front", "Alternate Back"]}]
    groups = CommanderAliasCatalog().groups(card)
    assert ["Alternate Front // Alternate Back"] in groups
    assert ["Alternate Front"] in groups
    assert [BACK] not in groups


def test_saved_evidence_normalization_is_in_place_idempotent_and_rejects_old_back_alias():
    from copy import deepcopy
    from edhrec_affinity.build_styles.__main__ import normalize_commander_evidence
    evidence = {"card": faced_card(), "base": {"commander_names": [FRONT, BACK],
        "commander_name_groups": [[FRONT, BACK], [BACK]]}, "metadata": {}}
    assert normalize_commander_evidence(evidence) is evidence
    assert evidence["base"]["commander_names"] == [FRONT]
    assert [BACK] not in evidence["base"]["commander_name_groups"]
    once = deepcopy(evidence)
    normalize_commander_evidence(evidence)
    assert evidence == once
    legacy = {"base": {"commander_names": [BRINELIN, DOGMEAT]}}
    assert normalize_commander_evidence(legacy) == legacy


def test_collection_normalizes_faces_before_returning_saved_analysis_evidence(tmp_path):
    from copy import deepcopy
    from edhrec_affinity.build_styles.__main__ import normalize_commander_evidence
    root = tmp_path / "sources" / "blitzwing-cruel-tormentor"
    root.mkdir(parents=True)
    card = faced_card()
    card.update(num_decks=200, url="/commanders/blitzwing-cruel-tormentor")
    (root / "base.json").write_text(json.dumps({"container": {"json_dict": {"card": card, "cardlists": []}}}))
    args = parser().parse_args(["--commander", "blitzwing-cruel-tormentor", "--cache-dir", str(root), "--sample-decks", "0"])
    evidence = collect_evidence(args)
    assert evidence["base"]["commander_names"] == [FRONT]
    once = deepcopy(evidence)
    normalize_commander_evidence(evidence)
    assert evidence == once


def test_pending_partner_evidence_drops_back_only_roles_and_keeps_known_renames():
    from edhrec_affinity.build_styles.__main__ import normalize_commander_evidence
    from edhrec_affinity.build_styles.sources import commander_back_names
    card = faced_card()
    card.update(name=f"{FRONT} // {BACK} // Partner", names=[FRONT, BACK, "Partner"], layout="normal")
    card["cards"].append({"name": "Partner", "layout": "normal", "id": PARTNER_ID})
    evidence = {"card": card, "base": {"commander_names": [FRONT, BACK, "Partner"],
        "commander_name_groups": [[FRONT, BACK, "Partner"], [BACK, "Partner"], [FRONT, "Known Alternate Partner"]]},
        "metadata": {}}
    normalize_commander_evidence(evidence)
    assert evidence["base"]["commander_names"] == [FRONT, "Partner"]
    assert commander_back_names(card) == {card_key(BACK)}
    assert [BACK, "Partner"] not in evidence["base"]["commander_name_groups"]
    assert [FRONT, "Known Alternate Partner"] in evidence["base"]["commander_name_groups"]
    minimal = {"name": FRONT, "names": None, "layout": "transform"}
    assert commander_back_names(minimal) == set()
    normalize_commander_evidence({"card": minimal, "base": {"commander_names": [FRONT]}, "metadata": {}})


def test_explicit_dfc_layout_with_one_whole_name_still_identifies_its_back():
    from edhrec_affinity.build_styles.__main__ import normalize_commander_evidence
    from edhrec_affinity.build_styles.sources import commander_back_names, commander_names
    whole = f"{FRONT} // {BACK}"
    card = {"name": whole, "names": [whole], "layout": "transform"}
    assert commander_names(card) == [FRONT]
    assert commander_back_names(card) == {card_key(BACK)}
    assert groups_as_sets(CommanderAliasCatalog().groups(card)) == {frozenset([whole]), frozenset([FRONT])}
    evidence = {"card": card, "base": {"commander_names": [whole], "commander_name_groups": [[BACK]]}, "metadata": {}}
    normalize_commander_evidence(evidence)
    assert [BACK] not in evidence["base"]["commander_name_groups"]


def test_joined_dfc_name_with_separate_face_rows_never_creates_a_third_face():
    from edhrec_affinity.build_styles.sources import commander_names
    card = faced_card()
    whole = f"{FRONT} // {BACK}"
    card.update(name=whole, names=[whole])
    assert commander_names(card) == [FRONT]
    groups = CommanderAliasCatalog().groups(card)
    assert groups_as_sets(groups) == {frozenset([FRONT]), frozenset([whole])}
    deck = {"id": "front", "commanders": [FRONT], "cards": [{"name": "Forest", "quantity": 99}]}
    assert prepare_decks([deck], commander_names(card), commander_name_groups=groups)[0]


def test_joined_dfc_constituent_and_real_partner_remain_two_cards():
    from edhrec_affinity.build_styles.sources import commander_names
    card = faced_card()
    whole = f"{FRONT} // {BACK}"
    card.update(name=f"{whole} // Partner", names=[whole, "Partner"], layout="normal")
    card["cards"].append({"name": "Partner", "id": PARTNER_ID, "layout": "normal"})
    assert commander_names(card) == [FRONT, "Partner"]
    assert groups_as_sets(CommanderAliasCatalog().groups(card)) == {
        frozenset([FRONT, "Partner"]), frozenset([whole, "Partner"])}


@pytest.mark.parametrize("alternate_front,alternate_back,front,back", [
    ("The Three Weird Sisters", "Fiends of Darkest Night", "Henrika Domnathi", "Henrika, Infernal Seer"),
    ("Doric, Nature's Warden", "Doric, Owlbear Avenger", "Casal, Lurkwood Pathfinder", "Casal, Pathbreaker Owlbear"),
])
def test_verified_alternate_dfc_accepts_canonical_front_and_whole_but_never_back(
        alternate_front, alternate_back, front, back):
    from copy import deepcopy
    from edhrec_affinity.build_styles.__main__ import normalize_commander_evidence
    card = {"name": alternate_front, "names": [alternate_front, alternate_back], "layout": "transform"}
    canonical = verified_name_metadata(f"{front} // {back}", "physical-oracle", alternate_front)
    canonical.update(layout="transform", card_faces=[{"name": front}, {"name": back}])
    evidence = {"card": card, "base": {"commander_names": [alternate_front, alternate_back]},
                "metadata": {card_key(canonical["name"]): canonical}}
    normalize_commander_evidence(evidence)
    base = evidence["base"]
    assert base["commander_names"] == [alternate_front]
    assert groups_as_sets(base["commander_name_groups"]) == {
        frozenset([alternate_front]), frozenset([f"{alternate_front} // {alternate_back}"]),
        frozenset([front]), frozenset([f"{front} // {back}"]),
    }
    normalized = deepcopy(evidence)
    normalize_commander_evidence(evidence)
    assert evidence == normalized
    for spelling in (front, f"{front} // {back}"):
        deck = {"id": spelling, "commanders": [spelling], "cards": [
            {"name": "Forest", "quantity": 99}, {"name": canonical["name"], "quantity": 1}]}
        kept, rejected, _ = prepare_decks([deck], base["commander_names"], commander_name_groups=base["commander_name_groups"])
        assert len(kept) == 1 and not rejected and kept[0]["card_count"] == 100
        assert card_key(canonical["name"]) not in kept[0]["counts"]
    for actual in ([back], [alternate_back], [front, back]):
        deck = {"id": "back", "commanders": actual, "cards": [{"name": "Forest", "quantity": 99}]}
        assert not prepare_decks([deck], base["commander_names"], commander_name_groups=base["commander_name_groups"])[0]


def test_verified_canonical_front_expansion_preserves_every_partner_role():
    alternate, front, back = "Alternate Front", "Canonical Front", "Canonical Back"
    canonical = verified_name_metadata(f"{front} // {back}", "dfc-oracle", alternate)
    canonical.update(layout="transform", card_faces=[{"name": front}, {"name": back}])
    partner = verified_name_metadata("Partner", "partner-oracle")
    base = {"commander_names": [alternate, "Partner"]}
    groups = enrich_commander_name_groups(base, {"dfc": canonical, "partner": partner})
    assert groups_as_sets(groups) == {
        frozenset([alternate, "Partner"]), frozenset([front, "Partner"]), frozenset([canonical["name"], "Partner"])}
    assert all(len(group) == 2 and back not in group for group in groups)
    assert enrich_commander_name_groups(base, {"dfc": canonical}) == [[alternate, "Partner"]]
    partner["oracle_id"] = canonical["oracle_id"]
    assert enrich_commander_name_groups(base, {"dfc": canonical, "partner": partner}) == [[alternate, "Partner"]]


def test_canonical_front_expansion_requires_strict_metadata_front_and_rename_proof():
    canonical = verified_name_metadata("Canonical Front // Canonical Back", "dfc-oracle", "Alternate")
    canonical.update(layout="transform", card_faces=[{"name": "Canonical Front"}, {"name": "Canonical Back"}])
    base = {"commander_names": ["Alternate"]}
    canonical["name_aliases"][0]["oracle_id"] = "wrong-oracle"
    assert enrich_commander_name_groups(base, {"Alternate": canonical}) == [["Alternate"]]
    canonical["name_aliases"][0]["oracle_id"] = "dfc-oracle"
    canonical["layout"] = "split"
    assert enrich_commander_name_groups(base, {"Alternate": canonical}) == [["Alternate"], [canonical["name"]]]
    canonical["layout"] = "room"
    assert enrich_commander_name_groups(base, {"Alternate": canonical}) == [["Alternate"], [canonical["name"]]]


def test_display_uses_complete_physical_faces_without_changing_legal_roles():
    from edhrec_affinity.build_styles.sources import commander_display_name, commander_names
    card = faced_card()
    assert commander_display_name(card) == f"{FRONT} // {BACK}"
    assert commander_names(card) == [FRONT]
    assert commander_display_name({"name": "Normal", "names": ["Normal"]}) == "Normal"
    card.update(name=f"{FRONT} // {BACK} // Partner", names=[FRONT, BACK, "Partner"], layout="normal")
    card["cards"].append({"name": "Partner", "layout": "normal", "id": PARTNER_ID})
    assert commander_display_name(card) == f"{FRONT} // {BACK} // Partner"
    assert commander_names(card) == [FRONT, "Partner"]


def test_display_can_use_verified_metadata_whole_name_and_preserves_source_flavor_faces():
    from edhrec_affinity.build_styles.sources import commander_display_name
    metadata = verified_name_metadata("Canonical Front // Canonical Back", "oracle", FRONT)
    metadata.update(layout="transform", card_faces=[{"name": "Canonical Front"}, {"name": "Canonical Back"}])
    assert commander_display_name({"name": FRONT}, {FRONT: metadata}) == metadata["name"]
    assert commander_display_name(faced_card(), {FRONT: metadata}) == f"{FRONT} // {BACK}"
    assert commander_display_name({"name": "Unrelated"}, {"Unrelated": metadata}) == "Unrelated"
    duplicate = {**metadata, "oracle_id": "different-oracle", "name_aliases": [
        {**metadata["name_aliases"][0], "oracle_id": "different-oracle"}]}
    assert commander_display_name({"name": FRONT}, {"one": metadata, "two": duplicate}) == FRONT


def test_analysis_report_shows_whole_dfc_display_name(tmp_path):
    from edhrec_affinity.build_styles.__main__ import analyze_evidence
    root = tmp_path / "sources" / "blitzwing-cruel-tormentor"
    root.mkdir(parents=True)
    card = faced_card()
    card.update(num_decks=200, url="/commanders/blitzwing-cruel-tormentor")
    (root / "base.json").write_text(json.dumps({"container": {"json_dict": {"card": card, "cardlists": []}}}))
    args = parser().parse_args(["--commander", "blitzwing-cruel-tormentor", "--cache-dir", str(root), "--sample-decks", "0"])
    evidence = collect_evidence(args)
    report = analyze_evidence(args, evidence)
    assert report["commander_name"] == f"{FRONT} // {BACK}"
    assert evidence["base"]["commander_names"] == [FRONT]
