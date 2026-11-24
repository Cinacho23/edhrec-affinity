from edhrec_affinity.synergy_prototype.construction import Spell
from edhrec_affinity.synergy_prototype.mechanics import classify, mechanic_bonus


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
