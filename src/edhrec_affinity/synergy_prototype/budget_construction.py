"""Build an Oracle-mechanics deck with a whole-deck retail price ceiling."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
import math
import re

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csc_matrix

from ..build_styles.deckbuilding import _BASIC_COLORS, _commander_colors, _is_land, _land_sources, _oracle
from ..build_styles.sources import card_key
from .construction import (
    _FETCH,
    COMMANDER_NAMES,
    LAND_SLOTS,
    ORACLE_POLICY,
    ConstructionSearchError,
    _deck_result,
    _land_colors,
    _land_priority,
    _land_quality,
    _relative_scores,
    _score_list,
    _usable_catalog,
)


def _price_of(counts, prices):
    return sum(prices[key] * quantity for key, quantity in counts.items())


def _budget_lands(metadata, colors, prices, budget, count, *, price_pressure=.75):
    pool = {key: card for key, card in metadata.items() if _is_land(card)}
    basics = [key for key, color in _BASIC_COLORS.items() if color in colors]
    missing = [key for key in basics if key not in pool]
    if missing:
        raise ConstructionSearchError(f"No verified retail price for basic lands: {', '.join(missing)}")
    if count < len(basics):
        raise ValueError("The land count must leave room for a basic land of each commander color")
    fetches = {key for key, card in pool.items()
               if _FETCH.search(re.sub(r"\([^)]*\)", "", _oracle(card).casefold()))}
    qualities = {key: _land_quality(card) for key, card in pool.items()}
    direct = {key: _land_colors(card, colors) for key, card in pool.items() if key not in fetches}
    replacement_cost = min(prices[key] for key in basics)
    lands = Counter(dict.fromkeys(basics, min(3, count // len(basics))))
    spent = _price_of(lands, prices)
    if spent + (count - sum(lands.values())) * replacement_cost > budget:
        raise ConstructionSearchError(f"The land allocation of {budget} cents cannot cover the basic mana base")
    slot_budget = max(100, budget / count)
    cost_penalties = {
        key: price_pressure * math.log1p(max(0, prices[key] - replacement_cost) / slot_budget) for key in pool
    }

    def sources_now():
        return {**direct, **{key: _land_sources(key, lands, metadata, colors) for key in fetches}}

    def early_sources(sources):
        early = Counter()
        for key, quantity in lands.items():
            for color in sources[key]:
                early[color] += quantity * qualities[key]
        return early

    for slots_left in range(count - sum(lands.values()), 0, -1):
        sources = sources_now()
        early = early_sources(sources)
        fetch_count = sum(lands[key] for key in fetches)
        ceiling = budget - spent - (slots_left - 1) * replacement_cost
        key = max((key for key in pool if prices[key] <= ceiling and sources[key]
                   and (key not in lands or key in basics)),
                  key=lambda key: (
                      _land_priority(pool[key], sources[key], qualities[key], early,
                                     .42 if key in fetches and fetch_count >= 7 else 0)
                      - cost_penalties[key], -prices[key], key))
        lands[key] += 1
        spent += prices[key]
    early = early_sources(sources_now())
    return lands, {color: round(early[color], 1) for color in sorted(colors)}


def _budget_policy(ramp_goal, ramp_limit):
    goals, limits = dict(ORACLE_POLICY.goals), dict(ORACLE_POLICY.limits)
    if ramp_goal is not None:
        goals["Early ramp"] = ramp_goal
        goals["Efficient ramp"] = min(goals["Efficient ramp"], ramp_goal)
    if ramp_limit is not None:
        limits["Mana acceleration"] = ramp_limit
    if (type(goals["Early ramp"]) is not int or type(limits["Mana acceleration"]) is not int
            or not 0 <= goals["Early ramp"] <= limits["Mana acceleration"]):
        raise ValueError("The early ramp goal must be a nonnegative integer at or below the ramp limit")
    return replace(ORACLE_POLICY, goals=goals, limits=limits)


def _budget_spells(metadata, spells, rank, policy, prices, budget, count, starts, mana_value_bias):
    if len(spells) < count:
        raise ConstructionSearchError(f"Only {len(spells)} legal priced spells are available for {count} slots")
    roles = tuple(dict.fromkeys((*policy.goals, *policy.limits)))
    role_pools = {role: [key for key, spell in spells.items() if role in spell.roles] for role in policy.goals}
    preferences = {
        key: -.015 * math.log1p(prices[key] / 100) - .12 * spells[key].expensive
        - mana_value_bias * metadata[key]["cmc"] for key in spells
    }
    context = []
    coverage = Counter(policy.goals)
    candidates, failed = [], []
    for start in range(starts):
        prediction = _relative_scores(rank, context, spells)
        values = {key: policy.fit(spells[key], score, coverage) + preferences[key] for key, score in prediction.items()}
        best = sorted(values, key=lambda key: (-values[key], prices[key], key))
        economical = sorted(values, key=lambda key: (prices[key], -values[key], key))
        shortlist = set(best[:180]) | set(economical[:100])
        for pool in role_pools.values():
            covered = [key for key in pool if key in values]
            shortlist.update(sorted(covered, key=lambda key: (-values[key], prices[key], key))[:24])
            shortlist.update(sorted(covered, key=lambda key: (prices[key], -values[key], key))[:24])

        keys = sorted(shortlist)
        costs = np.asarray([prices[key] for key in keys], dtype=float)
        masks = [[role in spells[key].roles for key in keys] for role in roles]
        constraints = LinearConstraint(
            csc_matrix(np.asarray([np.ones(len(keys)), costs, *masks], dtype=float)),
            [count, 0, *(policy.goals.get(role, 0) for role in roles)],
            [count, budget, *(policy.limits.get(role, np.inf) for role in roles)],
        )
        result = milp(-np.asarray([values[key] for key in keys]), integrality=np.ones(len(keys)),
                      bounds=Bounds(0, 1), constraints=constraints,
                      options={"time_limit": 5, "mip_rel_gap": .005})
        selected = [key for key, picked in zip(keys, result.x) if picked > .5] if result.x is not None else []
        coverage = Counter(role for key in selected for role in spells[key].roles)
        if (len(selected) != count or sum(prices[key] for key in selected) > budget
                or any(coverage[role] < goal for role, goal in policy.goals.items())
                or any(coverage[role] > limit for role, limit in policy.limits.items())):
            failed.append({"start": start + 1, "reason": str(result.message)})
            continue
        context = selected
        fit, individual, coverage = _score_list(selected, spells, rank, policy)
        candidates.append((fit, selected, individual, coverage))
    if not candidates:
        raise ConstructionSearchError(
            f"No legal spell allocation satisfied the Oracle deck roles within {budget} cents. "
            + " | ".join(row["reason"] for row in failed)
        )
    return max(candidates, key=lambda row: row[0]), [round(row[0], 4) for row in candidates], failed


def build_budget_deck(catalog: Mapping[str, dict], rank, prices_cents: Mapping[str, int], cap_cents: int,
                      *, land_count: int = LAND_SLOTS, ramp_goal: int | None = None,
                      ramp_limit: int | None = None, land_share: float = .35, starts: int = 3,
                      mana_value_bias: float = 0) -> dict:
    """Return a 100-card list whose quoted units, including both commanders, fit the cap."""
    commanders = tuple(map(card_key, COMMANDER_NAMES))
    if type(cap_cents) is not int or cap_cents < 0:
        raise ValueError("The deck price cap must be expressed in nonnegative integer cents")
    if type(land_count) is not int or not 0 < land_count < 100 - len(commanders):
        raise ValueError("The land count must leave room for the commanders and spells")
    if not 0 <= land_share <= 1 or type(starts) is not int or not 1 <= starts <= 3 or not math.isfinite(mana_value_bias):
        raise ValueError("Use a land share between zero and one, one to three starts, and a finite mana value bias")
    if any(key not in catalog for key in commanders):
        raise ValueError("The catalog is missing one or both prototype commanders")
    colors, _ = _commander_colors(commanders, catalog)
    legal = _usable_catalog(catalog, colors)
    prices = {key: price for key, price in prices_cents.items() if type(price) is int and price >= 0}
    metadata = {key: card for key, card in legal.items() if key in prices}
    missing = [legal[key]["name"] if key in legal else key for key in commanders if key not in metadata]
    if missing:
        raise ConstructionSearchError(f"No verified eligible price for commanders: {', '.join(missing)}")
    commander_total = sum(prices[key] for key in commanders)
    if commander_total > cap_cents:
        raise ConstructionSearchError("The commanders alone exceed the requested whole-deck cap")

    land_budget = int((cap_cents - commander_total) * land_share)
    lands, early = _budget_lands(metadata, colors, prices, land_budget, land_count)
    land_total = _price_of(lands, prices)
    spell_budget = cap_cents - commander_total - land_total
    policy = _budget_policy(ramp_goal, ramp_limit)
    spells = {key: policy.classify(card) for key, card in metadata.items()
              if key not in commanders and not _is_land(card) and prices[key] <= spell_budget}
    (fit, selected, scores, roles), fits, failed = _budget_spells(
        metadata, spells, rank, policy, prices, spell_budget, 100 - len(commanders) - land_count, starts, mana_value_bias)
    spell_total = sum(prices[key] for key in selected)
    total = commander_total + land_total + spell_total
    deck = _deck_result(metadata, commanders, lands, early, spells, selected, scores, fit, roles, policy,
                        starts, fits, failed, 0, colors)
    deck["validation"]["checks"]["complete_retail_price"] = all(card_key(row["name"]) in prices for row in deck["cards"])
    deck["validation"]["checks"]["within_price_cap"] = total <= cap_cents
    if not all(deck["validation"]["checks"].values()):
        raise ConstructionSearchError("The constructed deck did not satisfy the retail budget")
    for row in deck["cards"]:
        row["price_cents"] = prices[card_key(row["name"])]
    deck["construction"]["budget"] = {
        "cap_cents": cap_cents, "total_cents": total, "land_cents": land_total,
        "commander_cents": commander_total, "spell_cents": spell_total,
        "land_allocation_cents": land_budget, "unpriced_legal_cards": len(legal) - len(metadata),
        "mana_value_bias": mana_value_bias,
    }
    return deck
