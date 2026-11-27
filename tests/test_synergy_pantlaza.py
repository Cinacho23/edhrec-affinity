import gzip
import json
from pathlib import Path

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.synergy_prototype.mana_plan import land_option
from edhrec_affinity.synergy_prototype.pantlaza import classify_spell
from edhrec_affinity.synergy_prototype.pantlaza_pipeline import freeze_target


def _spell(text, *, type_line="Creature — Human", mana_cost="{2}{G}", cost=3, toughness="3"):
    card = {"name": "Example", "oracle_text": text, "card_faces": [], "cmc": cost, "mana_cost": mana_cost,
            "type_line": type_line, "power": "3", "toughness": toughness, "keywords": []}
    return classify_spell(card).roles


def test_support_text_does_not_count_as_a_printed_dinosaur():
    support = _spell("Dinosaur spells you cast cost {1} less.")
    assert {"Typed ramp", "Early ramp", "Dinosaur payoff"} <= support
    assert "Dinosaur" not in support
    body = _spell("", type_line="Creature — Dinosaur", toughness="5")
    assert {"Dinosaur", "High-toughness Dinosaur", "Discover 4 plays"} <= body


def test_blink_resets_an_entry_but_is_not_independent_removal():
    blink = _spell("Exile target creature you control, then return that card to the battlefield under its owner's control.",
                   type_line="Instant", mana_cost="{W}", cost=1)
    recursion_from_a_death = _spell("Exile this creature: Choose another target creature. When it's put into a graveyard "
                                   "this turn, return that card to the battlefield under its owner's control.")
    assert {"Blink", "Opponents' turns", "Creature protection"} <= blink
    assert "Early interaction" not in blink
    assert "Blink" not in recursion_from_a_death


def test_sweeper_credit_requires_the_effect_on_the_decks_cast_path():
    hand_cast = _spell("When this creature enters, if you cast it from your hand, destroy all non-Dinosaur creatures.",
                      type_line="Creature — Dinosaur", cost=8)
    needs_wall = _spell("Destroy all creatures that were blocked by target Wall this turn.", type_line="Instant", cost=1)
    usual = _spell("Destroy all creatures.", type_line="Sorcery", cost=4)
    assert "Hand-cast sweeper" in hand_cast and "Sweepers" not in hand_cast
    assert "Sweepers" not in needs_wall
    assert "Sweepers" in usual


def test_subtype_mana_counts_only_toward_casting_the_chosen_creatures():
    text = ("As this land enters, choose a creature type.\n{T}: Add {C}.\n{T}: Add one mana of any color. "
            "Spend this mana only to cast a creature spell of the chosen type, and that spell can't be countered.")
    cavern = {"name": "Cavern of Souls", "type_line": "Land", "oracle_text": text, "card_faces": []}
    option = land_option(cavern, set("GRW"), "Dinosaur")
    assert option.colors == set("GRW")
    assert option.restricted
    assert not land_option(cavern, set("GRW"), None).colors


def test_frozen_training_coverage_keeps_the_real_public_population(tmp_path):
    frame = [{"urlhash": key} for key in ("a", "b", "c", "d")]
    current = {"commander_slug": "pantlaza-sun-favored", "seed": 42, "frame": frame,
               "results": {"a": {"deck": {"id": "a", "commanders": ["Pantlaza"], "cards": []}}}}
    historical = {**current, "frame": frame[:2],
                  "results": {"a": current["results"]["a"],
                              "b": {"deck": {"id": "b", "commanders": ["Pantlaza"], "cards": []}}}}
    source_paths = [tmp_path / "fresh.gz", tmp_path / "older.gz"]
    for path, source in zip(source_paths, (current, historical)):
        with gzip.open(path, "wt") as stream:
            json.dump(source, stream)
    output = tmp_path / "frozen.gz"
    summary = freeze_target(source_paths[0], [source_paths[1]], output)
    assert summary["public_index_decks"] == 4
    assert summary["frozen_preview_count"] == 2
    assert summary["historical_previews_added"] == 1
    assert not summary["complete_current_index"]
    with gzip.open(output, "rt") as stream:
        frozen = json.load(stream)
    assert set(frozen["results"]) == {"a", "b"}


def test_published_pantlaza_receipt_matches_the_legal_priced_list():
    path = Path(__file__).resolve().parents[1] / "frontend/src/generated/synergy-deck-pantlaza.json"
    report = json.loads(path.read_text())
    deck = report["deck"]
    rating, price, training = (report[key] for key in ("external_rating", "pricing", "synergy"))
    assert [card["name"] for card in deck["commanders"]] == ["Pantlaza, Sun-Favored"]
    assert sum(card["quantity"] for card in deck["cards"]) == 100
    assert deck["validation"]["valid"] and all(deck["validation"]["checks"].values())
    assert sum(card["quantity"] for card in deck["cards"] if "Dinosaur" in card["roles"]) >= 28
    assert rating["status"] == "verified" and 72.5 <= rating["playability_pct"] <= 77.5
    assert cardlist_hash(deck["cards"]) == deck["deck_sha256"] == rating["deck_sha256"]
    assert not price["unpriced"] and price["total_cents"] == price["lands_cents"] + price["nonlands_cents"]
    assert .80 <= price["lands_cents"] / price["nonlands_cents"] <= 1.10
    assert 0 <= training["target_commander_training_decks"] <= training["target_commander_eligible_decks"]
    assert training["current_previews_loaded"] <= training["public_index_decks"]
