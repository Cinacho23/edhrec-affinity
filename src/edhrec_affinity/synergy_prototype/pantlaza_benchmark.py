"""Simulate Pantlaza's opening mana and count proactive discover 4 hits."""

import argparse
from collections import Counter
from dataclasses import dataclass, replace
from itertools import permutations
import json
from pathlib import Path
import random
import re

from ..build_styles.deckbuilding import _land_information, _oracle
from ..build_styles.sources import card_key
from .catalog import card_faces, load_catalog
from .mana_plan import land_option
from .pantlaza import classify_spell


_COLORS = {"G", "R", "W"}


@dataclass(frozen=True)
class Land:
    colors: frozenset
    restricted: bool
    types: frozenset
    basic: bool
    entry: str
    required_types: frozenset
    fetch_basic: bool
    fetch: bool
    fetch_tapped: bool
    choose_color: bool


@dataclass(frozen=True)
class Ramp:
    cost: int
    pips: tuple
    colors: frozenset
    restricted: bool
    taps: int
    discount: int
    fetch_basic: bool
    fetch_types: frozenset


def _land(card, text):
    option = land_option(card, _COLORS, "Dinosaur")
    info = _land_information(card)
    entry = "untapped"
    required = frozenset()
    if "a land an opponent controls could produce" in text:
        entry = "orchard"
    elif "first, second, or third turn" in text:
        entry = "first-three-turns"
    elif "two or fewer other lands" in text:
        entry = "fast"
    elif "two or more other lands" in text:
        entry = "slow"
    elif "two or more basic lands" in text:
        entry = "battle"
    elif match := re.search(r"unless you control (?:a |an )?(forest|mountain|plains) or (?:a |an )?(forest|mountain|plains)", text):
        entry, required = "check", frozenset(match.groups())
    elif "enters tapped" in text and "pay 2 life" not in text and "two or more opponents" not in text:
        entry = "tapped"
    return Land(option.colors, option.restricted, option.types,
                card["type_line"].startswith("Basic Land"), entry,
                frozenset(info["search_types"]) if option.fetch else required, info["basic_fetch"],
                option.fetch, "onto the battlefield tapped" in text, card.get("layout") == "modal_dfc")


def _ramp(card, text, roles):
    if "Early ramp" not in roles:
        return None
    front = card_faces(card)[0]
    pips = tuple(frozenset(pip.split("/")) & _COLORS for pip in re.findall(r"\{([^}]+)\}", front["mana_cost"])
                 if any(color in pip for color in _COLORS))
    permanent = bool(re.search(r"\b(?:Artifact|Creature|Enchantment)\b", front["type_line"].split("—", 1)[0]))
    reduction = re.search(r"(?:creature|dinosaur) spells (?:you cast )?costs? \{(\d+)\} less", text)
    land_search = bool(re.search(r"search your library\b[^.\n]*onto the battlefield", text))
    if land_search and (not permanent or "enters" in text):
        basic = "basic land card" in text
        types = frozenset(re.findall(r"\b(?:forest|plains|mountain|island|swamp)\b", text))
        return Ramp(int(card["cmc"]), pips, frozenset(), False, 0, 0, basic, types)
    if reduction:
        return Ramp(int(card["cmc"]), pips, frozenset(), False, 0, int(reduction[1]), False, frozenset())
    if permanent and re.search(r"\{t\}[^:\n]*: add ", text):
        colors = frozenset(card.get("produced_mana", ())) & _COLORS
        if "add one mana of any color" in text:
            colors = frozenset(_COLORS)
        restricted = "spend this mana only" in text
        return Ramp(int(card["cmc"]), pips, colors, restricted, 2 if "add {c}{c}" in text else 1,
                    0, False, frozenset())
    return None


def _pays(sources, cost, pips, *, dinosaur=False):
    castable = [(colors, restricted) for colors, restricted, generic in sources if dinosaur or not restricted or generic]
    if len(castable) < cost:
        return False
    available = tuple(colors if dinosaur or not restricted else frozenset() for colors, restricted in castable)
    eligible = tuple(i for i, colors in enumerate(available) if any(colors & pip for pip in pips))
    return any(all(available[index] & pip for index, pip in zip(indices, pips))
               for indices in permutations(eligible, len(pips)))


def _source_weight(land, current):
    colors = Counter(color for existing, _, _ in current for color in existing)
    return sum((1.22 if color == "G" else 1) / (1 + colors[color]) for color in land.colors) * (.70 if land.restricted else 1)


def _untapped(land, board, turn):
    if land.entry == "battle":
        return sum(item.basic for item in board) >= 2
    if land.entry == "check":
        return any(item.types & land.required_types for item in board)
    if land.entry == "fast":
        return len(board) <= 2
    if land.entry == "first-three-turns":
        return turn <= 3
    if land.entry == "slow":
        return len(board) >= 2
    if land.entry == "orchard":
        return turn > 1
    return land.entry == "untapped"


def _fetch(library, current, *, basic, types):
    return max(((index, land) for index, (land, _) in enumerate(library)
                if land is not None and not land.fetch and (land.basic if basic else bool(land.types & types))),
               key=lambda row: _source_weight(row[1], current), default=None)


def _draw_game(cards, rng):
    library = list(cards)
    rng.shuffle(library)
    hand, library = library[:7], library[7:]
    board, sources = [], []
    discount = 0
    pips = tuple(frozenset(color) for color in "GRW")
    for turn in range(1, 6):
        hand.append(library.pop())
        mana = list(sources)
        choices = []
        for index, (land, _) in enumerate(hand):
            if land is None:
                continue
            target = _fetch(library, sources, basic=land.fetch_basic, types=land.required_types) if land.fetch else None
            if land.fetch and target is None:
                continue
            real_land = target[1] if target else land
            if real_land.choose_color and real_land.colors:
                real_land = max((replace(real_land, colors=frozenset(color)) for color in sorted(real_land.colors)),
                                key=lambda side: _source_weight(side, sources))
            untapped = _untapped(real_land, board, turn) and (not target or not land.fetch_tapped)
            choices.append((1.50 * untapped + _source_weight(real_land, sources), index, real_land, untapped, target))
        if choices:
            _, index, land, untapped, target = max(choices, key=lambda row: (row[0], -row[1]))
            hand.pop(index)
            if target:
                library.pop(target[0])
                rng.shuffle(library)
            board.append(land)
            source = land.colors, land.restricted, True
            sources.append(source)
            if untapped:
                mana.append(source)
        if _pays(mana, max(3, 5 - discount), pips, dinosaur=True):
            return turn
        ramp = min(((index, rock) for index, (_, rock) in enumerate(hand) if rock is not None and _pays(mana, rock.cost, rock.pips)),
                   key=lambda row: (row[1].cost, not row[1].fetch_basic and not row[1].fetch_types, -len(row[1].colors)), default=None)
        if ramp is None:
            continue
        index, rock = ramp
        hand.pop(index)
        discount += rock.discount
        sources.extend([(rock.colors, rock.restricted, False)] * rock.taps)
        if rock.fetch_basic or rock.fetch_types:
            target = _fetch(library, sources, basic=rock.fetch_basic, types=rock.fetch_types)
            if target:
                library.pop(target[0])
                rng.shuffle(library)
                land = target[1]
                board.append(land)
                sources.append((land.colors, land.restricted, True))
    return 6


def benchmark(report, catalog, *, trials=6000, seed=197):
    """Model land drops, early mana and one ramp play per turn."""
    cards = []
    eligible = proactive = 0
    for row in report["deck"]["cards"]:
        if row.get("primary_role") == "Commander" or "Commander" in row.get("roles", ()):
            continue
        card = catalog[card_key(row["name"])]
        text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold())
        roles = classify_spell(card).roles
        is_land = row["type_line"].split(" // ", 1)[0].split(" — ", 1)[0].endswith("Land")
        if not is_land:
            eligible += int(card["cmc"] <= 4) * row["quantity"]
            proactive += ("Discover 4 plays" in roles) * row["quantity"]
        cards.extend([(_land(card, text), None) if is_land else (None, _ramp(card, text, roles))] * row["quantity"])
    if len(cards) != 99:
        raise ValueError("The benchmark expects 99 cards outside the command zone")
    randomizer = random.Random(seed)
    turns = Counter(_draw_game(cards, randomizer) for _ in range(trials))
    return {"trials": trials, "pantlaza_by_turn_4": round(sum(turns[turn] for turn in range(1, 5)) / trials * 100, 1),
            "pantlaza_by_turn_5": round((trials - turns[6]) / trials * 100, 1),
            "eligible_discover_4_cards": eligible, "proactive_discover_4_cards": proactive}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-jsonl", required=True, type=Path)
    parser.add_argument("--catalog-updated-at", required=True)
    parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args()
    catalog, _ = load_catalog(args.oracle_jsonl, args.catalog_updated_at)
    results = {str(path): benchmark(json.loads(path.read_text(encoding="utf-8")), catalog) for path in args.reports}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
