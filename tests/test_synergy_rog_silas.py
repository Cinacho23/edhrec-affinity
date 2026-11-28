import json
from pathlib import Path

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.synergy_prototype.mana_plan import land_option
from edhrec_affinity.synergy_prototype.opening_benchmark import _ramp
from edhrec_affinity.synergy_prototype.rog_silas import ROGRAKH, SILAS, classify_spell


def _card(cost, text, *, type_line="Instant", **fields):
    return {"name": "Example", "type_line": type_line, "mana_cost": cost, "cmc": 0,
            "oracle_text": text, "card_faces": [], "power": "", "toughness": "", "keywords": [], **fields}


def test_outlets_can_spend_rograkhs_zero_power_creature_body():
    draw = _card("{1}{B}", "As an additional cost to cast this spell, sacrifice an artifact or a creature.\nDraw two cards.")
    artifacts = _card("{4}", "Sacrifice an artifact: Add {C}{C}.", type_line="Artifact")
    creature_power = _card("{B}", "As an additional cost to cast this spell, sacrifice a creature.\n"
                           "Draw cards equal to that creature's power.")
    mana_value = _card("{1}{B}{B}", "As an additional cost to cast this spell, sacrifice an artifact or creature.\n"
                       "Draw cards equal to the mana value of the sacrificed permanent.")
    enters = _card("{1}{B}", "When this creature enters, you may sacrifice another creature or artifact. "
                   "If you do, draw a card.", type_line="Creature")
    assert {"Rog outlets", "Sacrifice creature", "Sacrifice artifacts"} <= classify_spell(draw).roles
    assert "Sacrifice artifacts" in classify_spell(artifacts).roles
    assert "Rog outlets" not in classify_spell(artifacts).roles
    assert "Rog outlets" not in classify_spell(creature_power).roles
    assert "Rog outlets" not in classify_spell(mana_value).roles
    assert {"Rog outlets", "Sacrifice creature", "Sacrifice artifacts"} <= classify_spell(enters).roles


def test_silas_needs_a_castable_artifact_card_in_the_graveyard():
    bauble = _card("{0}", "{T}, Sacrifice this artifact: Draw a card.", type_line="Artifact")
    suspend = {**bauble, "mana_cost": "", "oracle_text": "Suspend 3—{0}\n" + bauble["oracle_text"]}
    land = _card("", "{T}: Add {U}.", type_line="Artifact Land")
    locket = _card("{3}", "{T}: Add {U} or {R}.\n{U/R}{U/R}{U/R}{U/R}, {T}, Sacrifice this artifact: Draw two cards.",
                   type_line="Artifact")
    back_only = {**_card("{U}", "", type_line="Artifact // Artifact"), "layout": "modal_dfc",
                 "card_faces": [_card("{U}", "Draw a card."), bauble]}
    assert {"Consumable artifact", "Cheap artifacts"} <= classify_spell(bauble).roles
    assert "Consumable artifact" not in classify_spell(locket).roles
    for card in (suspend, land, back_only):
        assert not (classify_spell(card).roles & {"Consumable artifact", "Cheap artifacts"})


def test_evasion_and_closers_affect_silas_or_the_artifact_board():
    key = _card("{2}", "{T}, Discard a card: Up to one target creature can't be blocked this turn.", type_line="Artifact")
    solo = _card("{2}{U}", "Flying\nThis creature can't be blocked.", type_line="Creature — Bird")
    tribal = _card("{1}", "Equipped creature can't be blocked by Vampires or Zombies.", type_line="Artifact — Equipment")
    awaken = _card("{5}{U}", "When this creature enters, each noncreature artifact you control becomes a 4/4 "
                   "artifact creature until end of turn.", type_line="Artifact Creature — Construct")
    animate_one = _card("{1}{U}", "Target noncreature artifact you control becomes a 0/0 artifact creature.", type_line="Sorcery")
    assert "Silas evasion" in classify_spell(key).roles
    assert "Silas evasion" not in classify_spell(solo).roles
    assert "Silas evasion" not in classify_spell(tribal).roles
    assert {"Finisher", "Board finisher", "Artifact payoff"} <= classify_spell(awaken).roles
    assert "Board finisher" not in classify_spell(animate_one).roles


def test_passive_artifact_payoffs_reward_other_artifacts():
    own_trigger = _card("{B}", "When this artifact enters, create a Blood token.", type_line="Artifact")
    engine = _card("{2}{R}", "Whenever you cast an artifact spell, draw a card.", type_line="Creature")
    pressure = _card("{1}{B}", "Whenever another creature or artifact you control is put into a graveyard "
                     "from the battlefield, each opponent loses 1 life.", type_line="Creature")
    assert "Artifact payoff" not in classify_spell(own_trigger).roles
    assert {"Artifact payoff", "Card advantage"} <= classify_spell(engine).roles
    assert {"Artifact payoff", "Artifact pressure"} <= classify_spell(pressure).roles


def test_free_commander_ramp_uses_rograkhs_red_and_needs_no_artifact_setup():
    amber = _card("{0}", "{T}: Add one mana of any color among legendary creatures and planeswalkers you control.",
                  type_line="Legendary Artifact", produced_mana=["B", "R", "U"])
    opal = _card("{0}", "Metalcraft — {T}: Add one mana of any color. Activate only if you control three or more artifacts.",
                 type_line="Legendary Artifact")
    signet = _card("{2}", "{1}, {T}: Add {U}{B}.", type_line="Artifact")
    filter_only = _card("{1}", "{1}, {T}: Add one mana of any color.", type_line="Artifact")
    restricted = _card("{1}{R}", "{T}: Add {R}. Spend this mana only to cast an Elemental spell.", type_line="Creature")
    roles = classify_spell(amber).roles
    assert {"Rog mana", "Efficient ramp"} <= roles
    assert _ramp(amber, amber["oracle_text"].casefold(), roles, {"B", "R", "U"},
                 free_commander_colors=frozenset({"R"})).colors == {"R"}
    assert "Early ramp" not in classify_spell(opal).roles
    assert "Early ramp" not in classify_spell(filter_only).roles
    assert "Early ramp" not in classify_spell(restricted).roles
    assert "Efficient ramp" in classify_spell(signet).roles


def test_artifact_land_rewards_the_engine_without_becoming_a_silas_target():
    land = _card("", "{T}: Add {B}.", type_line="Artifact Land")
    baseline = land_option(land, {"B", "R", "U"}, None)
    artifact_build = land_option(land, {"B", "R", "U"}, None, artifact_support=True)
    assert artifact_build.utility > baseline.utility
    assert artifact_build.colors == baseline.colors == {"B"}
    assert "Cheap artifacts" not in classify_spell(land).roles


def test_published_pair_has_a_legal_priced_list_matching_the_external_check():
    path = Path(__file__).resolve().parents[1] / "frontend/src/generated/synergy-deck-rog-silas.json"
    report = json.loads(path.read_text())
    deck, rating, price = (report[key] for key in ("deck", "external_rating", "pricing"))
    assert [card["name"] for card in deck["commanders"]] == [SILAS, ROGRAKH]
    assert sum(card["quantity"] for card in deck["cards"]) == 100
    assert deck["validation"]["valid"] and all(deck["validation"]["checks"].values())
    assert rating["status"] == "verified" and 72.5 <= rating["playability_pct"] <= 77.5
    assert cardlist_hash(deck["cards"]) == deck["deck_sha256"] == rating["deck_sha256"]
    assert len(report["synergy"]["highlights"]) >= 3
    assert not price["unpriced"]
    assert .80 <= price["lands_cents"] / price["nonlands_cents"] <= 1.10
