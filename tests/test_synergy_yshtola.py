import json
from pathlib import Path

from edhrec_affinity.build_styles.external_validation import cardlist_hash
from edhrec_affinity.synergy_prototype.yshtola import COMMANDER, classify_spell


def _card(cost, text, *, type_line="Instant", **fields):
    return {"name": "Example", "type_line": type_line, "mana_cost": cost, "cmc": 0,
            "oracle_text": text, "card_faces": [], "power": "", "toughness": "", "keywords": [], **fields}


def test_noncreature_cast_uses_the_cast_mode_and_mana_value():
    creature = _card("{3}{U}", "", type_line="Creature — Cat")
    discounted = _card("{5}{U}", "Delve (Each card you exile from your graveyard while casting this spell pays for {1}.)")
    split = _card("{1}{U}{2}{B}", "", type_line="Instant // Instant", layout="split",
                  card_faces=[_card("{1}{U}", ""), _card("{2}{B}", "")])
    assert "Qualifying cast" not in classify_spell(creature).roles
    assert {"Qualifying cast", "Turn-cycle cast", "Discounted cast"} <= classify_spell(discounted).roles
    assert {"Turn-cycle cast", "Affordable trigger"} <= classify_spell(split).roles
    assert "Qualifying cast" not in classify_spell(_card("{X}{B}{B}", "Each opponent loses X life.")).roles


def test_life_loss_must_happen_before_the_end_step_and_be_reasonably_available():
    early = _card("{1}{B/P}{B/P}", "Target creature gets -5/-5 until end of turn.")
    late = _card("{5}{B}", "At the beginning of each end step, each opponent loses 4 life.", type_line="Enchantment")
    large_payment = _card("{4}", "Pay 50 life: This artifact deals 50 damage to any target.", type_line="Artifact")
    assert {"Four-life enabler", "Flexible life payment", "Turn-cycle cast"} <= classify_spell(early).roles
    assert "Four-life enabler" not in classify_spell(late).roles
    assert "Four-life enabler" not in classify_spell(large_payment).roles


def test_life_conversion_routine_trigger_and_mana_gated_ability_are_distinct():
    ability = "Whenever you gain life, target opponent loses that much life."
    conversion = _card("{2}{B}{B}", ability, type_line="Creature — Snake")
    activated = _card("{W}{B}", "{1}{W}{B}: " + ability, type_line="Creature — Human Wizard")
    assert "Life-gain conversion" in classify_spell(conversion).roles
    assert "Life-gain conversion" not in classify_spell(activated).roles
    assert "Conditional conversion" in classify_spell(activated).roles


def test_copying_a_spell_and_combat_damage_do_not_get_commander_payoff_credit():
    copy_spell = _card("{2}{U}", "Copy target instant or sorcery spell.")
    copy_trigger = _card("{1}{U}", "Copy target triggered ability you control.")
    noncombat = _card("{U}", "Whenever enchanted creature deals damage to an opponent, you may draw a card.",
                      type_line="Enchantment — Aura")
    combat = _card("{U}", "Whenever enchanted creature deals combat damage to a player, draw a card.",
                   type_line="Enchantment — Aura")
    assert "Double trigger" not in classify_spell(copy_spell).roles
    assert "Double trigger" in classify_spell(copy_trigger).roles
    assert "Damage payoff" in classify_spell(noncombat).roles
    assert "Damage payoff" not in classify_spell(combat).roles


def test_filtering_one_mana_is_not_ramp_and_a_counter_is_not_a_proactive_cast():
    filtering = _card("{1}", "{1}, {T}: Add one mana of any color.", type_line="Artifact")
    signet = _card("{2}", "{1}, {T}: Add {U}{B}.", type_line="Artifact")
    draw = _card("{2}{U}", "Draw two cards.")
    answer = _card("{2}{U}", "Counter target noncreature spell. Untap up to three lands.")
    counters = _card("{1}{B}{B}", "Pay 3 life: Put a +1/+1 counter on target creature.", type_line="Enchantment")
    assert "Early ramp" not in classify_spell(filtering).roles
    assert "Efficient ramp" in classify_spell(signet).roles
    assert "Proactive turn-cycle cast" in classify_spell(draw).roles
    assert {"Turn-cycle cast", "Mana refund"} <= classify_spell(answer).roles
    assert "Proactive turn-cycle cast" not in classify_spell(answer).roles
    assert "Interaction" not in classify_spell(counters).roles


def test_published_yshtola_receipt_matches_the_legal_priced_list():
    path = Path(__file__).resolve().parents[1] / "frontend/src/generated/synergy-deck-yshtola.json"
    report = json.loads(path.read_text())
    deck, observed, price, synergy = (report[key] for key in ("deck", "external_rating", "pricing", "synergy"))
    assert [card["name"] for card in deck["commanders"]] == [COMMANDER]
    assert sum(card["quantity"] for card in deck["cards"]) == 100
    assert deck["validation"]["valid"] and all(deck["validation"]["checks"].values())
    assert observed["status"] == "verified" and 72.5 <= observed["playability_pct"] <= 77.5
    assert cardlist_hash(deck["cards"]) == deck["deck_sha256"] == observed["deck_sha256"]
    assert len(synergy["highlights"]) >= 3
    assert not price["unpriced"]
    assert .80 <= price["lands_cents"] / price["nonlands_cents"] <= 1.10
