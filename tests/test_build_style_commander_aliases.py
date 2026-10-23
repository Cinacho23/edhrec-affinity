"""Only explicit EDHREC flavor references relax exact commander-name matching."""

import json

from edhrec_affinity.build_styles.__main__ import collect_evidence, parser
from edhrec_affinity.build_styles.analysis import prepare_decks
from edhrec_affinity.build_styles.sources import CommanderAliasCatalog


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
