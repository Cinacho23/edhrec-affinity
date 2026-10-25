"""Only explicit EDHREC flavor references relax exact commander-name matching."""

import json

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
