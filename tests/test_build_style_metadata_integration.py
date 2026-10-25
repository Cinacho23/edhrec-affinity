"""Keep verified commander aliases intact between collection and saved analysis."""

from copy import deepcopy
import json

import pytest

from edhrec_affinity.build_styles.__main__ import collect_evidence, parser
from edhrec_affinity.build_styles.pipeline import evidence_version
from edhrec_affinity.build_styles.sources import CommanderAliasCatalog, card_key


BRINELIN = "Brinelin, the Moon Kraken"
DOGMEAT = "Dogmeat, Constant Companion"
YOSHIMARU = "Yoshimaru, Ever Faithful"
MAX = "Max, the Daredevil"
ELMAR = "Elmar, Ulvenwald Informant"
SOPHINA = "Sophina, Spearsage Deserter"
MAX_PRINTING = "abb04ab1-2a74-432f-b3d3-d4f5b2534066"
SOPHINA_PRINTING = "e0a64924-068e-483f-91a6-d450b67b3c75"


def cached_collection(tmp_path, monkeypatch, names, **card_fields):
    root = tmp_path / "sources" / "commander"
    root.mkdir(parents=True)
    card = {"names": names, "name": " // ".join(names), "num_decks": 200,
            "url": "/commanders/commander", **card_fields}
    payload = {"container": {"json_dict": {"card": card, "cardlists": []}}}
    for filename in ("base.json", "bracket-3.json"):
        (root / filename).write_text(json.dumps(payload))
    decks = [{"id": str(index), "commanders": names,
              "cards": [{"name": "Forest", "quantity": 98}]} for index in range(8)]
    monkeypatch.setattr("edhrec_affinity.build_styles.__main__.collect_previews",
                        lambda *args, **kwargs: (decks, {"complete": True, "failures": []}))
    args = parser().parse_args(["--commander", "commander", "--cache-dir", str(root)])
    return args


def test_collection_forwards_individual_verified_aliases_and_saves_resolved_metadata(tmp_path, monkeypatch):
    args = cached_collection(tmp_path, monkeypatch, [BRINELIN, DOGMEAT])
    aliases = CommanderAliasCatalog(cards=[{
        "names": [YOSHIMARU, BRINELIN], "name": f"{YOSHIMARU} // {BRINELIN}",
        "flavor_refs": [{"name": f"{BRINELIN} // {DOGMEAT}"}],
    }])
    resolved = {card_key(DOGMEAT): {"name": YOSHIMARU, "oracle_id": "yoshimaru-oracle"}}
    summary = {"requested": 3, "resolved": 3, "missing": [], "alias_resolutions": {
        DOGMEAT: {"name": YOSHIMARU, "source": "edhrec_commander_alias"}}}
    calls = []

    class Provider:
        def load(self, names, *, fetch, commander_alias_groups):
            calls.append((names, fetch, commander_alias_groups))
            return resolved, summary

    result = collect_evidence(args, metadata_provider=Provider(), commander_alias_provider=aliases)

    assert len(calls) == 1
    names, fetch, groups = calls[0]
    assert set(names) == {BRINELIN, DOGMEAT, "Forest"}
    assert fetch is False
    assert {frozenset(group) for group in groups} == {frozenset([DOGMEAT, YOSHIMARU])}
    assert result["metadata"] == resolved
    assert result["metadata_summary"] == summary
    assert frozenset([YOSHIMARU, BRINELIN]) in {
        frozenset(group) for group in result["base"]["commander_name_groups"]}


def test_collection_without_verified_aliases_keeps_existing_provider_contract(tmp_path, monkeypatch):
    args = cached_collection(tmp_path, monkeypatch, [BRINELIN, YOSHIMARU])
    calls = []

    class Provider:
        def load(self, names, *, fetch):
            calls.append((names, fetch))
            return {}, {"missing": names}

    result = collect_evidence(args, metadata_provider=Provider())

    assert len(calls) == 1
    assert set(calls[0][0]) == {BRINELIN, YOSHIMARU, "Forest"}
    assert result["metadata_summary"]["missing"] == calls[0][0]


@pytest.mark.parametrize("proof_kind", ["verified", "missing", "wrong_identity"])
def test_cold_start_uses_commander_printing_ids_but_requires_returned_identity_proof(
        tmp_path, monkeypatch, proof_kind):
    args = cached_collection(tmp_path, monkeypatch, [MAX, SOPHINA],
        # Partner printing IDs belong to the matching nested display names;
        # the aggregate ID identifies only one of the two commanders.
        id=MAX_PRINTING, cards=[{"name": SOPHINA, "id": SOPHINA_PRINTING},
                                {"name": MAX, "id": MAX_PRINTING}])
    canonical = {"name": ELMAR, "oracle_id": "elmar-oracle", "layout": "normal", "card_faces": []}
    if proof_kind != "missing":
        canonical["name_aliases"] = [{
            "name": MAX, "source_field": "printed_name", "printing_id": MAX_PRINTING,
            "lang": "en", "oracle_id": "elmar-oracle" if proof_kind == "verified" else "other-oracle",
            "canonical_name": ELMAR,
        }]
    resolved = {card_key(MAX): canonical, card_key(SOPHINA): {
        "name": SOPHINA, "oracle_id": "sophina-oracle", "layout": "normal", "card_faces": []}}
    calls = []

    class Provider:
        def load(self, names, *, fetch, printing_ids):
            calls.append((names, fetch, printing_ids))
            return resolved, {"missing": []}

    # No canonical commander page has been cached or supplied to this catalog.
    result = collect_evidence(args, metadata_provider=Provider(), commander_alias_provider=CommanderAliasCatalog())

    assert len(calls) == 1
    names, fetch, printing_ids = calls[0]
    assert set(names) == {MAX, SOPHINA, "Forest"}
    assert fetch is False
    assert printing_ids == {MAX: MAX_PRINTING, SOPHINA: SOPHINA_PRINTING}
    assert result["metadata"] == resolved
    groups = {frozenset(group) for group in result["base"]["commander_name_groups"]}
    assert frozenset([MAX, SOPHINA]) in groups
    assert (frozenset([ELMAR, SOPHINA]) in groups) is (proof_kind == "verified")
    assert all(len(group) == 2 for group in groups)


def test_corrected_commander_identity_semantics_invalidate_saved_version_four_results():
    source = {
        "collection": {"dataset_sha256": "saved-secret-lair-preview-set"},
        "base": {"commander_names": [BRINELIN, DOGMEAT],
                 "commander_name_groups": [[BRINELIN, DOGMEAT], [BRINELIN, YOSHIMARU]]},
        "ranking_profiles": [],
        "metadata": {card_key(DOGMEAT): {"name": YOSHIMARU, "oracle_id": "yoshimaru-oracle",
                                       "checked_at": "2026-10-24T00:00:00Z"}},
    }
    # This exact input previously reused a report whose builder could reject
    # canonical commander previews. It must now be analyzed again on refresh.
    historical_version_four = "28fc904b618b6eef6b345285c26fbe7707ee507cad765863e0b9d404340b8d96"
    current = evidence_version(source)
    assert current != historical_version_four

    reverified = deepcopy(source)
    reverified["metadata"][card_key(DOGMEAT)]["checked_at"] = "2026-10-25T00:00:00Z"
    assert evidence_version(reverified) == current
