from edhrec_affinity.synergy_prototype.construction import Spell
from edhrec_affinity.synergy_prototype.mechanics import classify, mechanic_bonus
from edhrec_affinity.synergy_prototype.refinement import classify_spell, land_allocation


def _classify(oracle):
    old = Spell(frozenset({"Creatures", "Exile payoffs"}), ("Exile payoffs",), False, 0)
    return classify({"name": "Example", "oracle_text": oracle, "card_faces": []}, old)


def test_counts_repeatable_suspend_only_for_a_source_of_own_suspended_cards():
    own = _classify("{2}, Exile a nonland card from your hand: Put four time counters on it. It gains suspend.")
    opposing = _classify("Exile target creature and put two time counters on it. It gains suspend.")
    counterspell = _classify("Counter target spell. Exile it with three time counters on it. It gains suspend.")
    library = _classify("Exile the top five cards of your library. For each nonland card exiled this way, "
                        "put a number of time counters on that card equal to its mana value. That card gains suspend.")

    assert {"Creatures", "Suspend", "Repeatable suspend", "Direct synergy"} <= own.roles
    assert "Suspend" not in opposing.roles
    assert "Suspend" not in counterspell.roles
    assert "Suspend" in library.roles
    assert "Permanent counter payoff" not in library.roles


def test_rewards_additive_exile_triggers_above_overlapping_alternative_costs():
    reward = _classify("Whenever you cast a spell from exile, copy it.")
    cost = _classify("You may pay {0} rather than pay the mana cost for a spell you cast from exile.")

    assert "Exile payoffs" in reward.roles
    assert "Exile payoffs" not in cost.roles
    assert "Exile cost support" in cost.roles
    assert mechanic_bonus(reward, {}) > mechanic_bonus(cost, {})


def test_generic_counter_manipulation_only_counts_as_acceleration_when_it_reaches_exile():
    exile = _classify("Choose a counter on target permanent or suspended card. Remove that counter from it.")
    battlefield = _classify("Remove a time counter from target creature you control.")

    assert "Suspend acceleration" in exile.roles
    assert "Suspend acceleration" not in battlefield.roles


def _priced_spell(oracle, *, mana_value=3, card_type="Creature — Human"):
    return classify_spell({"name": "Example", "oracle_text": oracle, "card_faces": [], "type_line": card_type,
                           "cmc": mana_value, "mana_cost": "{2}{U}", "power": "2", "keywords": []}, 50)


def test_hand_only_sweep_cannot_fill_the_general_sweeper_role():
    gated = _priced_spell("Example enters with a divinity counter on it if you cast it from your hand.\n"
                          "Remove a divinity counter from Example: Destroy all other creatures.", mana_value=8)
    ordinary = _priced_spell("Destroy all creatures.", mana_value=4, card_type="Sorcery")

    assert "Hand-cast sweeper" in gated.roles
    assert "Sweepers" not in gated.roles
    assert "Sweepers" in ordinary.roles


def test_kicked_recursion_and_island_dependent_vanishing_get_narrower_roles():
    kicked = _priced_spell("Multikicker {W/U}\nWhen Example enters, exile up to X target instant cards from your "
                           "graveyard, where X is the number of times Example was kicked.")
    vanishing = _priced_spell("This creature enters with a time counter on it for each Island you control.\n"
                              "Vanishing\nExample's power and toughness are each equal to the number of time counters on it.")
    assert "Kicked instant recursion" in kicked.roles
    assert "Interaction" not in kicked.roles
    assert "Island-dependent time counters" in vanishing.roles
    assert "Direct synergy" not in vanishing.roles


def test_land_allocation_scales_with_the_spell_core_and_can_cover_basics():
    assert land_allocation(12000, .70, basic_floor=800) == 8400
    assert land_allocation(4000, .70, basic_floor=800) == 2800
    assert land_allocation(1000, .55, basic_floor=800) == 800
