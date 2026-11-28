"""Allocate a quoted mana budget using early colors and real search targets."""

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
import math
import re

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from ..build_styles.deckbuilding import _BASIC_COLORS, _is_land, _land_sources, _oracle
from .construction import ConstructionSearchError, _FETCH, _land_colors, _land_quality


@dataclass(frozen=True)
class LandOption:
    colors: frozenset[str]
    quality: float
    types: frozenset[str]
    fetch: bool
    restricted: bool
    utility: float


def land_option(card, colors, creature_type, *, artifact_support=False):
    text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold())
    direct = _land_colors(card, colors)
    quality = _land_quality(card)
    types = frozenset(card["type_line"].casefold().split()) & _BASIC_COLORS.keys()
    fetch = bool(_FETCH.search(text))
    restricted = False
    utility = 0.0
    if "a land an opponent controls could produce" in text:
        direct, quality = colors, .62
    elif "unless you have two or more opponents" in text:
        quality = .99
    elif "unless you control two or fewer other lands" in text:
        quality = .84
    elif "unless you control two or more other lands" in text:
        quality = .62
    elif "unless you control two or more basic lands" in text:
        quality = .52
    elif re.search(r"unless you control (?:a |an )?(?:forest|plains|mountain|swamp|island) or", text):
        quality = .73
    elif "unless you control a planeswalker" in text or "a player has 13 or less life" in text:
        quality = .18
    elif "opponents control eight or more lands" in text:
        quality = .53

    typed_mana = bool(re.search(
        r"spend this mana only to cast a creature spell of the chosen type|"
        r"spend this mana only to cast a creature spell or activate an ability of a creature source of the chosen type",
        text,
    ))
    if creature_type and "choose a creature type" in text and typed_mana:
        direct, restricted = colors, True
        quality = max(quality, .92)
        utility = 1.65 if "can't be countered" in text else .12
    if creature_type and creature_type.casefold() in text and "search your library" not in text:
        utility += .16
    if "channel —" in text or "sacrifice this land: draw a card" in text:
        utility += .18
    if "target opponent creates" in text or "remove a mining counter" in text:
        quality *= .63
    if re.search(r"if you control no (?:creatures|artifacts)\b[^.\n]*sacrifice", text):
        quality *= .35
    if "opponent gains control of this land" in text:
        quality *= .22
    if card.get("layout") == "modal_dfc":
        quality *= .86
    if "charge counter" in text or "energy counter" in text:
        quality *= .65
    if "first, second, or third turn" in text:
        quality = .80
    if artifact_support and "Artifact" in card["type_line"].split(" — ", 1)[0].split():
        utility += 1.25 * quality
    return LandOption(frozenset(direct), quality, frozenset(types), fetch, restricted, utility)


def build_mana_base(metadata, prices, colors, creature_type, *, budget, count, price_pressure, required=(),
                    artifact_support=False):
    """Select a mana base and report general and tribal-only casting sources."""
    basics = [key for key, color in _BASIC_COLORS.items() if color in colors]
    if not all(key in metadata for key in basics):
        raise ConstructionSearchError("The price snapshot is missing one or more basic land types")
    options = {key: land_option(card, colors, creature_type, artifact_support=artifact_support)
               for key, card in metadata.items() if _is_land(card) and prices[key] <= budget}
    options = {key: option for key, option in options.items() if option.colors or option.fetch}
    minimum_price = min(prices[key] for key in basics)
    lands = Counter(dict.fromkeys(basics, 3))
    if any(key not in options for key in required):
        raise ConstructionSearchError("An included land has no usable mana or verified price")
    lands.update(key for key in required if key not in lands)
    spent = sum(prices[key] * num for key, num in lands.items())
    if spent + (count - sum(lands.values())) * minimum_price > budget:
        raise ConstructionSearchError("The allocation cannot buy enough legal basics")

    # Establish affordable typed lands so fetching can be scored against targets
    # that actually appear in the final list.
    for pair in combinations(sorted(colors), 2):
        ceiling = budget - spent - (count - sum(lands.values()) - 1) * minimum_price
        choices = [key for key, option in options.items()
                   if key not in lands and len(option.types) == 2 and set(pair) <= option.colors
                   and option.quality >= .82 and prices[key] <= min(ceiling, budget // 5)]
        if choices:
            key = max(choices, key=lambda candidate: (options[candidate].quality, -prices[candidate], candidate))
            lands[key] = 1
            spent += prices[key]

    colors_by_land = {key: frozenset(_land_sources(key, lands, metadata, colors)) if option.fetch else option.colors
                      for key, option in options.items()}
    keys = sorted(key for key in options if colors_by_land[key])
    quality = np.asarray([options[key].quality for key in keys])
    restricted = np.asarray([options[key].restricted for key in keys])
    tribal_matrix = np.asarray([[quality[i] if color in colors_by_land[key] else 0
                                for i, key in enumerate(keys)] for color in sorted(colors)])
    general_matrix = tribal_matrix * (~restricted)
    weights = np.asarray([1.17 if color == "G" else 1 for color in sorted(colors)])
    price_unit = max(100, budget / count)
    slot_prices = [prices[key] for key in keys]
    penalty = [price_pressure * math.log1p(max(0, price - minimum_price) / price_unit) for price in slot_prices]
    utility = np.asarray([options[key].utility for key in keys])
    rewards = weights @ (.76 * general_matrix + .56 * tribal_matrix) + .24 * quality + utility - penalty
    # Jointly choose all slots. The extra variable rewards improving the
    # weakest general color even when a land supplies a stronger color too.
    constraints = LinearConstraint(
        [[1] * len(keys) + [0], [*slot_prices, 0], *([*row, -1] for row in general_matrix)],
        [count, 0, *([0] * len(colors))],
        [count, budget, *([np.inf] * len(colors))],
    )
    result = milp(-np.append(rewards, 1.18), integrality=[1] * len(keys) + [0],
                  bounds=Bounds([lands[key] for key in keys] + [0],
                                [count if key in basics else 1 for key in keys] + [count]),
                  constraints=constraints, options={"time_limit": 3, "mip_rel_gap": .005})
    if result.x is None:
        raise ConstructionSearchError("No legal mana base fits this allocation")
    amounts = np.rint(result.x[:-1]).astype(int)
    result_lands = Counter({key: int(num) for key, num in zip(keys, amounts, strict=True) if num})
    if sum(result_lands.values()) != count or np.dot(amounts, slot_prices) > budget:
        raise ConstructionSearchError("The optimized lands did not satisfy the slot and price constraints")
    return result_lands, dict(zip(sorted(colors), np.round(general_matrix @ amounts, 1).tolist(), strict=True)), dict(
        zip(sorted(colors), np.round(tribal_matrix @ amounts, 1).tolist(), strict=True)
    )
