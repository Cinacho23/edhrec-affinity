"""Optimize the Doctor and Rose's spell engine and price the matching mana base."""

from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations
import math
import re

from ..build_styles.deckbuilding import _BASIC_COLORS, _commander_colors, _is_land, _oracle
from ..build_styles.sources import card_key
from . import mechanics
from .budget_construction import _budget_lands
from .construction import (
    COMMANDER_NAMES,
    ORACLE_POLICY,
    ConstructionSearchError,
    _deck_result,
    _pick_spells,
    _relative_scores,
    _score_list,
    _usable_catalog,
)
from .prices import cheapest_copies


# The Jeskai original duals are excluded for this pair. Other kinds of fixing
# compete for the land allocation on price and colored mana quality.
ORIGINAL_DUALS = frozenset(map(card_key, ("Plateau", "Tundra", "Volcanic Island")))
SCORE_EXPLANATION = (
    "The search uses the neural ranker, Oracle-text prerequisites, and the fit "
    "between repeatable suspend sources, countdown effects, and exile payoffs."
)
HAND_ONLY_COUNTER = re.compile(r"enters?[^.\n]*with [^,.;\n]+ counter[^.\n]*if you cast (?:it|this \w+) from your hand")
SWEEPER_TEXT = re.compile(r"\b(?:destroy|exile|return|phase out|sacrifice)\b[^.;\n]*\ball\b")
KICKED_RECURSION = re.compile(r"^multikicker\b[\s\S]*exile[^.\n]*instant cards? from your graveyard")
SELF_SCALING = re.compile(r"power and toughness (?:are )?(?:each )?equal to the number of time counters on (?:it|this \w+)")
RELIABLE_EARLY_INTERACTION = re.compile(
    r"(?:^|\n)(?:you may )?(?:destroy|exile|counter|return|phase out) (?:up to \w+ )?target"
    r"\s+(?:non\w+ |tapped |attacking ){0,2}(?:permanents?|creatures?|spells?|artifacts?|enchantments?)\b"
)


@dataclass(frozen=True)
class ManaProfile:
    lands: int
    early_ramp: int
    land_ratio: float
    price_pressure: float = .75


MANA_PROFILES = (
    ManaProfile(35, 7, .55),
    ManaProfile(35, 8, .80),
    ManaProfile(36, 7, .70),
    ManaProfile(36, 8, .55),
    ManaProfile(37, 7, .55),
    ManaProfile(37, 8, .80),
    ManaProfile(38, 7, .70),
    ManaProfile(36, 8, .70, .32),
    ManaProfile(37, 8, .70, .32),
    ManaProfile(38, 7, .70, .32),
)


def land_allocation(nonland_cents, land_ratio, *, basic_floor):
    """Use the price of the whole spell core to fund colored sources."""
    return max(basic_floor, round(nonland_cents * land_ratio))


def classify_spell(card, price_cents=0):
    spell = ORACLE_POLICY.classify(card)
    text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold())
    roles = set(spell.roles)
    signals = list(spell.signals)
    efficiency = spell.efficiency - .10 * math.log1p(price_cents / 150)

    if ("Sweepers" in roles and HAND_ONLY_COUNTER.search(text)
            and any(line.startswith("remove ") and " counter" in line and SWEEPER_TEXT.search(line)
                    for line in text.splitlines())
            and not any(SWEEPER_TEXT.search(line) and not line.startswith("remove ")
                        for line in text.splitlines())):
        roles.difference_update(("Sweepers", "Wipe", "Removal"))
        roles.add("Hand-cast sweeper")
        efficiency -= .50
    if KICKED_RECURSION.search(text):
        roles.add("Kicked instant recursion")
        efficiency -= .24
        if not RELIABLE_EARLY_INTERACTION.search(text):
            roles.difference_update(("Interaction", "Removal"))
    if ("Permanent time counters" in roles and "vanishing" in text
            and SELF_SCALING.search(text) and "island you control" in text):
        roles.difference_update(("Permanent counter payoff", "Permanent time counters", "Early attackers"))
        signals = [signal for signal in signals if signal in roles]
        roles.add("Island-dependent time counters")
        efficiency += .12

    if not mechanics._DIRECT.intersection(signals):
        roles.discard("Direct synergy")
    if card["type_line"].split(" // ", 1)[0].startswith("Instant"):
        roles.add("Instants")
    return replace(spell, roles=frozenset(roles or {"Leftover"}), signals=tuple(signals), efficiency=efficiency)


def _card_fit(spell, model_score, coverage, *, emphasis=1.0):
    return .65 * model_score + spell.efficiency + 1.2 * emphasis * mechanics.mechanic_bonus(spell, coverage)


def _policy(early_ramp):
    # These functional floors prevent the search from trading away all core
    # coverage. Other useful roles contribute smoothly to the deck objective.
    goals = {"Early ramp": early_ramp, "Interaction": 6, "Sweepers": 2,
             "Early attackers": 10, "Card advantage": 6, "Commander protection": 2,
             "Suspend": 6, "Exile payoffs": 4, "Time travel": 3}
    limits = {"Mana acceleration": early_ramp + 4, "Mundane mana rocks": 2}
    return replace(ORACLE_POLICY, goals=goals, limits=limits, fit=_card_fit,
                   classify=classify_spell,
                   notes={**ORACLE_POLICY.notes,
                          "Hand-cast sweeper": "Needs its counter from a hand cast to clear the board.",
                          "Kicked instant recursion": "Needs kicker mana, a useful instant in the graveyard, and an attack.",
                          "Island-dependent time counters": "Can add one to Rose's attack count while it has a time counter."},
                   score_explanation=SCORE_EXPLANATION)


def _strategy_bonus(coverage):
    engine = (coverage["Repeatable suspend"] + .6 * coverage["Recurring suspend"]
              + .17 * max(0, coverage["Suspend"] - coverage["Repeatable suspend"] - coverage["Recurring suspend"]))
    acceleration = coverage["Time travel"] + .75 * coverage["Suspend acceleration"] + coverage["Extra upkeep"]
    payoff = coverage["Exile payoffs"] + .5 * coverage["Commander exile payoff"]
    attack = coverage["Early attackers"]
    recursion = coverage["Kicked instant recursion"] * min(.30, coverage["Instants"] / 45)
    return (1.55 * math.log1p(engine * acceleration / 7) + 1.8 * math.log1p(engine * payoff / 7)
            + .55 * math.log1p(attack) + .55 * math.log1p(coverage["Evasion support"]) + recursion)


def _evaluate(selected, spells, rank, policy):
    fit, scores, coverage = _score_list(selected, spells, rank, policy)
    return fit + _strategy_bonus(coverage) / len(selected), selected, scores, coverage


def _refine(current, spells, rank, policy):
    """Search coordinated one- and two-card swaps and rescore complete lists."""
    fit, selected, scores, coverage = current
    present = set(selected)
    predictions = _relative_scores(rank, selected, spells)
    values = {key: policy.fit(spells[key], value, coverage)
              for key, value in predictions.items() if key not in present}
    best_incoming = sorted(values, key=lambda key: (values[key], key), reverse=True)
    pool = set(best_incoming[:24])
    tracked_roles = (*policy.goals, "Repeatable suspend", "Recurring suspend", "Suspend acceleration",
                     "Commander exile payoff", "Evasion support")
    for role in tracked_roles:
        pool.update([key for key in best_incoming if role in spells[key].roles][:3])
    edits = []
    old_bonus = _strategy_bonus(coverage)
    for index, outgoing in enumerate(selected):
        removed = spells[outgoing].roles
        for incoming in sorted(pool):
            added = spells[incoming].roles
            next_roles = coverage.copy()
            next_roles.subtract(removed)
            next_roles.update(added)
            delta = values[incoming] - scores[outgoing] + _strategy_bonus(next_roles) - old_bonus
            edits.append((delta, index, incoming, next_roles))
    edits.sort(key=lambda row: row[:3], reverse=True)

    def admissible(roles):
        return (all(roles[role] >= goal for role, goal in policy.goals.items())
                and all(roles[role] <= limit for role, limit in policy.limits.items()))

    singles = [(delta, ((index, incoming),)) for delta, index, incoming, roles in edits if admissible(roles)][:4]
    pairs = []
    for (gain, index, incoming, roles), (other_gain, other_index, other, other_roles) in combinations(edits[:40], 2):
        if index == other_index or incoming == other:
            continue
        next_roles = roles.copy()
        next_roles.subtract(spells[selected[other_index]].roles)
        next_roles.update(spells[other].roles)
        if admissible(next_roles):
            linked_bonus = _strategy_bonus(next_roles) + old_bonus - _strategy_bonus(roles) - _strategy_bonus(other_roles)
            pairs.append((gain + other_gain + linked_bonus, ((index, incoming), (other_index, other))))
    best = current
    for _, changes in [*singles, *sorted(pairs, reverse=True)[:4]]:
        candidate = list(selected)
        for index, incoming in changes:
            candidate[index] = incoming
        result = _evaluate(candidate, spells, rank, policy)
        if result[0] > best[0] + 1e-6:
            best = result
    return best


def build_candidates(catalog, rank, prices, *, profiles=MANA_PROFILES):
    """Generate legal exact lists from the fixed ranker and a dated retailer snapshot."""
    commanders = tuple(map(card_key, COMMANDER_NAMES))
    colors, _ = _commander_colors(commanders, catalog)
    legal = _usable_catalog(catalog, colors)
    metadata = {key: card for key, card in legal.items() if key in prices and key not in ORIGINAL_DUALS}
    basic = [key for key, color in _BASIC_COLORS.items() if color in colors]
    spell_catalog = {key: card for key, card in metadata.items() if key not in commanders and not _is_land(card)}
    if not all(key in metadata for key in (*commanders, *basic)):
        raise ConstructionSearchError("The price snapshot is missing commanders or basic lands")
    spells = {key: classify_spell(card, prices[key]) for key, card in spell_catalog.items()}
    refine_cache = {}

    for profile in profiles:
        policy = _policy(profile.early_ramp)
        count = 100 - len(commanders) - profile.lands
        search_key = (count, profile.early_ramp)
        if search_key not in refine_cache:
            starts = [_evaluate(_pick_spells(spells, rank, start, policy, spell_slots=count), spells, rank, policy)
                      for start in range(3)]
            best = max(starts, key=lambda result: result[0])
            refinements = 0
            for _ in range(3):
                improved = _refine(best, spells, rank, policy)
                if improved is best:
                    break
                refinements += 1
                best = improved
            refine_cache[search_key] = best, refinements, [round(start[0], 4) for start in starts]
        (fit, selected, scores, coverage), refinements, start_scores = refine_cache[search_key]

        nonlands = sum(prices[key] for key in (*commanders, *selected))
        allocation = land_allocation(
            nonlands, profile.land_ratio, basic_floor=profile.lands * max(prices[key] for key in basic))
        lands, early = _budget_lands(metadata, colors, prices, allocation, profile.lands,
                                     price_pressure=profile.price_pressure)
        deck = _deck_result(metadata, commanders, lands, early, spells, selected, scores, fit, coverage, policy,
                            3, start_scores, [], refinements, colors)
        deck["construction"]["spell_role_counts"].update({
            role: coverage[role] for role in ("Repeatable suspend", "Recurring suspend", "Suspend acceleration",
                                             "Permanent counter payoff", "Commander exile payoff", "Evasion support")
        })
        deck["construction"]["land_allocation"] = {
            "share_of_nonland_price": profile.land_ratio, "estimated_cents": allocation,
            "nonland_estimated_cents": nonlands,
        }
        yield deck


def price_deck(deck, snapshot):
    """Return exact stock allocations and retail subtotals for the deck list."""
    totals = {"lands_cents": 0, "nonlands_cents": 0}
    choices, missing = {}, []
    for card in deck["cards"]:
        offers = cheapest_copies(snapshot["cards"].get(card_key(card["name"])), card["quantity"])
        if not offers:
            missing.append(card["name"])
            continue
        choices[card["name"]] = offers
        category = "lands_cents" if "Lands" in card["roles"] else "nonlands_cents"
        totals[category] += sum(offer["price_cents"] * offer["quantity"] for offer in offers)
    return {**totals, "total_cents": None if missing else sum(totals.values()), "unpriced": missing,
            "source": snapshot["source"], "source_url": snapshot["source_url"],
            "checked_at": snapshot["checked_at"], "source_created_at": snapshot.get("source_created_at")}, choices


def selection_score(report):
    """Favor spell coherence, reliable access to all colors, and lower spend."""
    deck = report["deck"]
    lowest_color = min(deck["mana_diagnostics"]["early_colored_land_sources_adjusted"].values())
    price = report["pricing"]["total_cents"]
    return deck["synergy_score"] + .008 * lowest_color - .000002 * price
