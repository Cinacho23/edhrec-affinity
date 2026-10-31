"""Build reproducible package decks from observed lists and NMF memberships.

This is constrained, membership-weighted consensus, not another trained model.
An observed, verified 100-card deck supplies the mana base and role/curve shape;
one-for-one swaps first retain the displayed distinctive core, then improve
consensus without inventing an unrelated top-99 list.
Commander construction checks follow the September 2026 Comprehensive Rules,
903.3, 903.4b, 903.5 and 702.124:
https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import wraps
from itertools import product
from heapq import heappop, heappush
import math
import re
from urllib.parse import quote_plus

from .card_metadata import metadata_aliases
from .power_scoring import CHAINABLE_EXTRA_TURN_CARDS, EXTRA_TURN_LIMITS
from .sources import card_key, commander_card_alias_groups


ROLES = ("Commander", "Lands", "Fast Mana", "Ramp", "Card Advantage", "Wipe",
         "Removal", "Tutor", "Recursion", "Protection", "Leftover")
DECK_GENERATION_VERSION = 4
_BASIC_COLORS = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G"}
_COLOR_NAMES = dict(zip("WUBRG", ("white", "blue", "black", "red", "green")))
_NUMBER_WORDS = {word: number for number, word in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"))}


def _cached_card(function):
    @wraps(function)
    def cached(card):
        values = card.get("_build_cache") if isinstance(card, dict) else None
        if values is None:
            return function(card)
        if function.__name__ not in values:
            values[function.__name__] = function(card)
        return values[function.__name__]
    return cached


@_cached_card
def _oracle(card):
    return "\n".join(filter(None, [card.get("oracle_text", ""),
                                    *(face.get("oracle_text", "") for face in card.get("card_faces", []))]))


@_cached_card
def _aliases(card):
    return metadata_aliases({"layout": "normal", "card_faces": [], **card})


def _metadata_identity(card):
    oracle_id = card.get("oracle_id")
    return ("oracle", oracle_id) if isinstance(oracle_id, str) and oracle_id else ("name", card_key(card["name"]))


def _metadata_index(metadata, *, commander_alias_groups=None):
    canonical = {}
    for card in (metadata or {}).values():
        if not isinstance(card, dict) or not isinstance(card.get("name"), str):
            continue
        canonical.setdefault(card_key(card["name"]), {**card, "_build_cache": {}})
    candidates = {}
    for card in canonical.values():
        for alias in _aliases(card):
            candidates.setdefault(alias, []).append(card)
    # Actual canonical names always win. Ambiguous front/flavor aliases do not
    # substitute one card for another, independent of input iteration order.
    result = dict(canonical)
    for alias, cards in candidates.items():
        if alias not in result and len({_metadata_identity(card) for card in cards}) == 1:
            result[alias] = min(cards, key=lambda card: card_key(card["name"]))
    for group in commander_alias_groups or []:
        if (not isinstance(group, list) or len(group) < 2
                or any(not isinstance(name, str) or not name.strip() for name in group)):
            continue
        keys = {card_key(name) for name in group}
        if len(keys) != len(group):
            continue
        resolved = [result[key] for key in keys if key in result]
        if not resolved or len({_metadata_identity(card) for card in resolved}) != 1:
            continue
        card = min(resolved, key=lambda card: card_key(card["name"]))
        for alias in keys:
            result.setdefault(alias, card)
    return result


@_cached_card
def _complete_metadata(card):
    return (isinstance(card, dict) and bool(card.get("name")) and bool(card.get("type_line"))
            and isinstance(card.get("color_identity"), list)
            and isinstance(card.get("legalities"), dict)
            and ("oracle_text" in card or bool(card.get("card_faces"))))


@_cached_card
def _is_land(card):
    # A spell with a land on its back remains a spell slot; its front is the
    # observed deck's primary face. Combining both faces miscounts MDFC lands.
    return "land" in card.get("type_line", "").split(" // ")[0].casefold().split()


@_cached_card
def _is_basic(card):
    return _is_land(card) and "basic" in card.get("type_line", "").casefold().split()


@_cached_card
def _copy_limit(card):
    if _is_basic(card):
        return 100
    text = _oracle(card).casefold()
    if "a deck can have any number of cards named " in text:
        return 100
    match = re.search(r"a deck can have up to (\w+) cards named ", text)
    if match:
        return int(match[1]) if match[1].isdigit() else _NUMBER_WORDS.get(match[1], 1)
    return 1


@_cached_card
def functional_tags(card):
    """Observable text-based roles, separate from learned strategic labels."""
    if _is_land(card):
        return ["Lands"]
    text = _oracle(card).casefold()
    tags = []
    if (re.search(r"\badd (?:\{|one mana|two mana|three mana|an amount of)", text)
            or re.search(r"search your library for[^.\n]*(?:land|plains|island|swamp|mountain|forest)[^.\n]*battlefield", text)
            or re.search(r"(?:create|creates)[^.\n]*(?:treasure|powerstone) token", text)
            or "additional land" in text or "spells you cast cost" in text):
        tags.append("Ramp")
        value = card.get("cmc")
        mana_symbols = re.findall(r"\{[wubrgc]\}", text.split("add ")[-1].split(".")[0])
        if ((isinstance(value, (int, float)) and value <= 1 and
             (len(mana_symbols) >= 2 or value == 0 and "artifact" in card.get("type_line", "").casefold()))
                or re.search(r"exile [^.\n]*from your hand:\s*add", text)):
            tags.insert(0, "Fast Mana")
    if (re.search(r"\bdraw (?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that many|\d+)(?: additional)? cards?", text)
            or re.search(r"\bdraw (?:cards equal|cards until|a number of cards|one or more cards)", text)
            or "draws a card" in text or "investigate" in text
            or re.search(r"exile the top[^.\n]*\.(?:[^.\n]*\.)?[^\n]*(?:play|cast)[^.\n]*exiled", text)
            or re.search(r"exile the top[^.\n]*\.[^\n]*(?:play|cast) (?:those cards|that card|them)", text)
            or re.search(r"look at the top[^\n]*put[^\n]*(?:your hand|into hand)", text)):
        tags.append("Card Advantage")
    wipe = bool(re.search(r"(?:destroy|exile|return) (?:all|each) [^.\n]*(?:creatures|permanents|artifacts|enchantments|lands)", text)
                or re.search(r"each (?:player|opponent) sacrifices? (?:all|each|half)", text)
                or re.search(r"deals? [^.\n]*damage to each (?:other )?creature", text))
    if wipe:
        tags.append("Wipe")
    if (re.search(r"(?:destroy|exile) (?:target|all|each|up to)", text)
            or "counter target" in text or re.search(r"target[^.\n]*gets? -\d+/-\d+", text)
            or re.search(r"(?:opponent|player) sacrifices?", text)
            or re.search(r"deals? [^.\n]*damage to (?:any target|target creature|each (?:other )?creature)", text)):
        tags.append("Removal")
    if re.search(r"search (?:your|their|that player's|target player's) library", text):
        searches = re.findall(r"search [^.\n]*library[^.\n]*", text)
        if any(not re.search(r"for (?:a|an|up to [^ ]+) (?:basic |nonbasic )?(?:lands?|plains|islands?|swamps?|mountains?|forests?)(?: cards?)?\b", search) for search in searches):
            tags.append("Tutor")
    if any(phrase in text for phrase in ("hexproof", "indestructible", "protection from", "phase out", "phases out", "ward ")):
        tags.append("Protection")
    if ("graveyard" in text and any(phrase in text for phrase in
            ("return", "cast", "play", "put that card onto", "put it onto"))):
        tags.append("Recursion")
    return tags or ["Leftover"]


@_cached_card
def _can_command(card):
    types = card.get("type_line", "").split(" // ")[0].casefold()
    return ("can be your commander" in _oracle(card).casefold()
            or ("legendary" in types and
                ("creature" in types or "vehicle" in types or
                 "spacecraft" in types and card.get("power") is not None and card.get("toughness") is not None)))


@_cached_card
def _partner_variants(card):
    text = _oracle(card).casefold().replace("’", "'")
    variants = set()
    for line in text.splitlines():
        line = line.split("(")[0].strip()
        if line == "partner":
            variants.add("partner")
        match = re.match(r"partner\s*[—–-]\s*(.+)", line)
        if match:
            variants.add(match[1].strip())
        if line == "friends forever":
            variants.add("friends forever")
    return variants


def _commander_configuration(cards):
    if len(cards) == 1:
        return _can_command(cards[0])
    if len(cards) != 2 or any("legendary" not in card.get("type_line", "").casefold() for card in cards):
        return False
    a, b = cards
    if _partner_variants(a) & _partner_variants(b):
        return True
    texts = [_oracle(card).casefold().replace("’", "'") for card in cards]
    if all("partner with " + card_key(other["name"]) in text for text, other in zip(texts, (b, a))):
        return True
    for index, (card, other) in enumerate(((a, b), (b, a))):
        other_types = other.get("type_line", "").split(" // ")[0].casefold()
        if "choose a background" in texts[index] and "enchantment" in other_types and "background" in other_types:
            return True
        if ("doctor's companion" in texts[index] and "creature" in card.get("type_line", "").casefold()
                and "creature" in other_types and other_types.split("—")[-1].strip() == "time lord doctor"):
            return True
    return False


@_cached_card
def _chooses_commander_color(card):
    name = card["name"]
    text = f"If {name} is your commander, choose a color before the game begins. {name} is the chosen color."
    return text.casefold() in _oracle(card).casefold()


def _commander_colors(commander_keys, metadata, choices=None):
    """Apply only the exact pregame color-setting ability (CR 903.4b)."""
    colors = {color for key in commander_keys for color in metadata[key]["color_identity"]}
    flexible = {key for key in commander_keys if _chooses_commander_color(metadata[key])}
    if choices is None and not flexible:
        return colors, []
    if not isinstance(choices, list) or len(choices) != len(flexible):
        raise ValueError("An explicit pregame color choice is required for each commander with the color-setting ability.")
    selected = {}
    for choice in choices:
        if (not isinstance(choice, dict) or set(choice) != {"commander_name", "color"}
                or not isinstance(choice["commander_name"], str)
                or not isinstance(choice["color"], str) or choice["color"] not in set("WUBRG")):
            raise ValueError("Commander color choices require a verified commander name and one of W, U, B, R, G.")
        key = card_key(choice["commander_name"])
        if key not in flexible or key in selected:
            raise ValueError("A color choice must belong to a distinct commander with the verified color-setting ability.")
        selected[key] = choice["color"]
        colors.add(choice["color"])
    return colors, [{"commander_name": metadata[key]["name"], "color": selected[key]} for key in commander_keys if key in selected]


def _commander_color_options(base, metadata):
    if "commander_color_choices" in base:
        return [base["commander_color_choices"]]
    names = base.get("commander_names", [])
    if not isinstance(names, list) or not 1 <= len(names) <= 2:
        return [None]
    cards = [metadata.get(card_key(name)) for name in names if isinstance(name, str)]
    if len(cards) != len(names) or any(not _complete_metadata(card) for card in cards):
        return [None]
    flexible = sorted({card_key(card["name"]): card["name"] for card in cards if _chooses_commander_color(card)}.items())
    if not flexible:
        return [None]
    return [[{"commander_name": name, "color": color} for (_, name), color in zip(flexible, colors)]
            for colors in product("WUBRG", repeat=len(flexible))]


def _profile_observation(profile, key, metadata, field="rate", *, ranking=False):
    cards = profile.get("ranking_cards", profile.get("cards", {})) if ranking else profile.get("cards", {})
    for alias in sorted(_aliases(metadata[key])) if key in metadata else [key]:
        if alias in cards:
            value = cards[alias].get(field)
            if isinstance(value, (int, float)) and math.isfinite(value):
                return float(value), True
    return 0.0, False


def _profile_value(profile, key, metadata):
    return _profile_observation(profile, key, metadata)[0]


def _profile_metrics(key, profiles, metadata):
    if not profiles:
        return {"average_inclusion": None, "average_lift": None,
                "inclusion_observed_profiles": 0, "lift_observed_profiles": 0}
    rates = [_profile_observation(profile, key, metadata, ranking=True) for profile in profiles]
    lifts = [_profile_observation(profile, key, metadata, "lift", ranking=True) for profile in profiles]
    return {"average_inclusion": sum(value for value, _ in rates) / len(rates),
            "average_lift": sum(value for value, _ in lifts) / len(lifts),
            "inclusion_observed_profiles": sum(observed for _, observed in rates),
            "lift_observed_profiles": sum(observed for _, observed in lifts)}


@_cached_card
def _bracket_flags(card):
    text = _oracle(card).casefold()
    extra = bool(re.search(r"take(?:s)? (?:an? |\w+ )?extra turn|take (?:two|three|\d+) turns after", text))
    mass_land = bool(re.search(r"(?:destroy|exile) all [^.\n]*\blands\b", text)
                     or re.search(r"each player sacrifices? (?:all|\d+|half)[^.\n]*lands", text)
                     or re.search(r"return all [^.\n]*\blands\b[^.\n]*hands", text)
                     or re.search(r"each player chooses?[^.\n]*lands?[^.\n]*sacrifices? the rest", text))
    return bool(card.get("game_changer")), extra, mass_land


@_cached_card
def _chainable_extra_turn(card):
    return card_key(card["name"]) in CHAINABLE_EXTRA_TURN_CARDS


def _all_bracket_flags(card):
    return (*_bracket_flags(card), _chainable_extra_turn(card))


def _bracket_target(decks, weights):
    values = sorted((int(deck["source_bracket"]), weights.get(deck["id"], 0)) for deck in decks
                    if isinstance(deck.get("source_bracket"), int) and not isinstance(deck["source_bracket"], bool)
                    and 1 <= deck["source_bracket"] <= 5 and weights.get(deck["id"], 0) > 0)
    total = sum(weight for _, weight in values)
    cumulative = 0.0
    for bracket, weight in values:
        cumulative += weight
        if cumulative >= total / 2:
            return bracket, "membership_weighted_median"
    return 3, "default"


def _bracket_counts(counts, metadata):
    changers = extra = denial = chainable = 0
    for key, quantity in counts.items():
        changer_flag, extra_flag, denial_flag = _bracket_flags(metadata[key])
        changers += quantity * changer_flag
        extra += quantity * extra_flag
        denial += quantity * denial_flag
        chainable += quantity * _chainable_extra_turn(metadata[key])
    return changers, extra, denial, chainable


def _bracket_rule_checks(counts, target):
    changers, extra, denial, chainable = counts
    limit = 0 if target <= 2 else 3 if target == 3 else None
    extra_limit = EXTRA_TURN_LIMITS[target - 1]
    return {"game_changers": limit is None or changers <= limit,
            "extra_turns": extra_limit is None or extra <= extra_limit,
            "mass_land_denial": target >= 4 or not denial,
            "chaining_extra_turns": target >= 4 or not chainable}


def _preserves_bracket_counts(current, following, target):
    limits = (0 if target <= 2 else 3 if target == 3 else None,
              EXTRA_TURN_LIMITS[target - 1], 0 if target < 4 else None,
              0 if target < 4 else None)
    # An observed anchor can already exceed a checked restriction. Preserve its
    # honest higher recommendation without permitting further violations.
    return all(limit is None or after <= max(before, limit)
               for before, after, limit in zip(current, following, limits))


def _preserves_bracket(counts, candidate, metadata, target):
    return _preserves_bracket_counts(_bracket_counts(counts, metadata), _bracket_counts(candidate, metadata), target)


def _bracket_validation(counts, metadata, target, source):
    changers, extra_count, denial_count, chainable_count = _bracket_counts(counts, metadata)
    extra, denial = extra_count > 0, denial_count > 0
    limit = 0 if target <= 2 else 3 if target == 3 else None
    checks = _bracket_rule_checks((changers, extra_count, denial_count, chainable_count), target)
    recommended = next(bracket for bracket in range(target, 6)
        if all(_bracket_rule_checks((changers, extra_count, denial_count, chainable_count), bracket).values()))
    notes = ["Exploratory bracket guidance; speed, tutors, combos and all play patterns are not fully evaluated."]
    if source == "default":
        notes.append("No usable source-deck bracket annotations; the construction target defaults to bracket 3.")
    if any(not isinstance(metadata[key].get("game_changer"), bool) for key in counts):
        notes.append("Game Changer annotations are incomplete; the count can be understated.")
    if recommended != target:
        notes.append("Observed cards exceed the target's checked restrictions, so the recommendation uses the lowest bracket that passes those checks.")
    return {"status": "estimated", "target": target, "recommended": recommended, "source": source,
            "game_changer_count": changers, "game_changer_limit": limit, "rule_checks": checks,
            "extra_turn_count": extra_count, "mass_land_denial_count": denial_count,
            "chainable_extra_turn_count": chainable_count,
            "extra_turn_limit": EXTRA_TURN_LIMITS[target - 1],
            "extra_turns_present": extra, "mass_land_denial_present": denial, "notes": notes}


def _validate(counts, commander_keys, metadata, pool, *, commander_color_choices=None):
    complete = all(_complete_metadata(metadata.get(key)) for key in counts)
    commanders = [metadata[key] for key in commander_keys if _complete_metadata(metadata.get(key))]
    try:
        colors, _ = _commander_colors(commander_keys, metadata, commander_color_choices)
        color_choice_valid = True
    except (ValueError, KeyError):
        colors, color_choice_valid = set(), False
    identities = Counter()
    if complete:
        for key, quantity in counts.items():
            identities[metadata[key].get("oracle_id") or key] += quantity
    checks = {
        "card_count": sum(counts.values()) == 100,
        "commander_identity": (len(commanders) == len(commander_keys) and
                               all(counts.get(key) == 1 for key in commander_keys) and
                               _commander_configuration(commanders)),
        "metadata_complete": complete,
        "color_identity": complete and color_choice_valid and all(set(metadata[key]["color_identity"]) <= colors and
            (not _is_land(metadata[key]) or all(color in colors for land, color in _BASIC_COLORS.items()
             if land in metadata[key]["type_line"].casefold().split())) for key in counts),
        "singleton": complete and all(0 < quantity and identities[metadata[key].get("oracle_id") or key] <= _copy_limit(metadata[key])
                                      for key, quantity in counts.items()),
        "commander_legality": complete and all(metadata[key]["legalities"].get("commander") == "legal" for key in counts),
        "sampled_pool": all(key in pool for key in counts),
    }
    if commander_color_choices is not None or any(_chooses_commander_color(card) for card in commanders):
        checks["commander_color_choices"] = color_choice_valid
    return {"valid": all(checks.values()), "checks": checks,
            "errors": [name.replace("_", " ") for name, valid in checks.items() if not valid]}


@_cached_card
def _type_bucket(card):
    types = card.get("type_line", "").split(" // ")[0].casefold().split()
    return next((kind for kind in ("creature", "instant", "sorcery", "artifact", "enchantment", "planeswalker", "battle")
                 if kind in types), "other")


@_cached_card
def _curve_bucket(card):
    value = card.get("cmc", 0)
    return min(3, int(value) // 2) if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0 else 0


@_cached_card
def _land_information(card):
    text = _oracle(card).casefold()
    types = card.get("type_line", "").casefold().split()
    direct = set(card.get("produced_mana", [])) & set("WUBRG")
    direct |= {color for land, color in _BASIC_COLORS.items() if land in types}
    searches = "search your library" in text
    search_types = {land for land in _BASIC_COLORS if re.search(r"\b" + land + r"s?\b", text)} if searches else set()
    basic_fetch = searches and "basic land" in text
    any_fetch = searches and not search_types and "land card" in text and not basic_fetch
    tapped = bool(re.search(r"enters? (?:the battlefield )?tapped", text))
    if not tapped or "pay 2 life" in text:
        untapped = 1.0
    elif "unless" in text or "two or fewer other lands" in text:
        untapped = .7
    else:
        untapped = .2
    if searches and ("put it onto the battlefield tapped" in text or "put that card onto the battlefield tapped" in text):
        untapped = min(untapped, .25)
    return {"direct": direct, "search_types": search_types, "basic_fetch": basic_fetch,
            "any_fetch": any_fetch, "untapped_quality": untapped}


def _land_sources(key, counts, metadata, colors):
    information = _land_information(metadata[key])
    result = set(information["direct"])
    if information["basic_fetch"] or information["any_fetch"] or information["search_types"]:
        for candidate in counts:
            card = metadata[candidate]
            if not _is_land(card):
                continue
            types = set(card.get("type_line", "").casefold().split())
            if (information["basic_fetch"] and _is_basic(card) or information["any_fetch"]
                    or information["search_types"] & types):
                result |= _land_information(card)["direct"]
    return result & colors


def _source_targets(counts, metadata, commander_keys, commander_color_choices=None):
    colors, _ = _commander_colors(commander_keys, metadata, commander_color_choices)
    maximum_pips = {color: 1 for color in colors}
    for key in counts:
        card = metadata[key]
        if not _is_land(card) and card.get("cmc", 0) <= 5:
            pips = Counter(re.findall(r"\{([WUBRG])\}", card.get("mana_cost", "")))
            for color in colors:
                maximum_pips[color] = max(maximum_pips[color], pips[color])
    return {color: min(30, 21 + 4 * maximum_pips[color]) for color in sorted(colors)}


def _mana_diagnostics(counts, metadata, commander_keys, commander_color_choices=None):
    spells = [(metadata[key], quantity) for key, quantity in counts.items() if key not in commander_keys and not _is_land(metadata[key])]
    lands = sum(quantity for key, quantity in counts.items() if _is_land(metadata[key]))
    total_spells = sum(quantity for _, quantity in spells)
    average_cmc = sum(card.get("cmc", 0) * quantity for card, quantity in spells) / max(1, total_spells)
    role_counts = Counter(role for card, quantity in spells for role in functional_tags(card) for _ in range(quantity))
    target = max(34, min(40, round(37 + 2 * (average_cmc - 3) - .15 * role_counts["Ramp"])))
    commander_colors, _ = _commander_colors(commander_keys, metadata, commander_color_choices)
    targets = _source_targets(counts, metadata, commander_keys, commander_color_choices)
    sources = Counter()
    untapped_penalty = 0.0
    for key, quantity in counts.items():
        if _is_land(metadata[key]):
            for color in _land_sources(key, counts, metadata, commander_colors):
                sources[color] += quantity
            untapped_penalty += quantity * (1 - _land_information(metadata[key])["untapped_quality"])
    source_penalty = .04 * sum(max(0, targets[color] - sources[color]) ** 2 for color in commander_colors)
    penalty = (abs(lands - target) + .6 * max(0, 8 - role_counts["Ramp"])
               + .6 * max(0, 8 - role_counts["Card Advantage"])
               + .4 * max(0, 6 - role_counts["Removal"] - role_counts["Wipe"])
               + source_penalty + .3 * untapped_penalty)
    return {"land_count": lands, "suggested_land_count": target, "average_nonland_mana_value": average_cmc,
            "role_counts": dict(role_counts), "colored_land_sources": dict(sources), "colored_source_targets": targets,
            "mana_base_risk": source_penalty + .3 * untapped_penalty, "structural_risk_score": penalty,
            "interpretation": "Native construction heuristic; lower risk is preferred. This is not an EDHPowerLevel percentage or a playtest result."}


def _upgrade_land_base(counts, pool, metadata, commander_keys, support, protected, target, bracket_source,
                       commander_color_choices=None):
    colors, _ = _commander_colors(commander_keys, metadata, commander_color_choices)
    incoming_pool = [key for key in pool if key not in commander_keys and _complete_metadata(metadata.get(key))
                     and _is_land(metadata[key]) and metadata[key]["legalities"].get("commander") == "legal"
                     and set(metadata[key]["color_identity"]) <= colors]
    swaps = []
    for _ in range(18):
        diagnostics = _mana_diagnostics(counts, metadata, commander_keys, commander_color_choices)
        sources, targets = diagnostics["colored_land_sources"], diagnostics["colored_source_targets"]
        present_identities = {_metadata_identity(metadata[key]) for key in counts}
        available = [key for key in incoming_pool if key not in counts
                     and _metadata_identity(metadata[key]) not in present_identities]
        removable = [key for key in counts if key not in commander_keys and key not in protected and _is_land(metadata[key])]
        if not available or not removable:
            break
        source_sets = {key: _land_sources(key, counts, metadata, colors) for key in set(available + removable)}
        characteristics = {key: (frozenset(source_sets[key]), _land_information(metadata[key])["untapped_quality"])
                           for key in source_sets}
        # Many different lands share a color set and access condition. Evaluate
        # each structural exchange once, retaining card inclusion as the tie-break.
        exchange_scores = {}
        choices = []
        for incoming in available:
            for outgoing in removable:
                exchange = (characteristics[incoming], characteristics[outgoing])
                if exchange not in exchange_scores:
                    (incoming_colors, incoming_quality), (outgoing_colors, outgoing_quality) = exchange
                    improvement = .04 * sum(max(0, targets[color] - sources.get(color, 0)) ** 2 -
                        max(0, targets[color] - sources.get(color, 0) - (color in incoming_colors) + (color in outgoing_colors)) ** 2
                        for color in colors)
                    exchange_scores[exchange] = improvement + .3 * (incoming_quality - outgoing_quality)
                improvement = exchange_scores[exchange]
                if improvement > 1e-9:
                    choices.append((improvement, support.get(incoming, 0) - support.get(outgoing, 0), incoming, outgoing))
        changed = False
        for _, _, incoming, outgoing in sorted(choices, key=lambda row: (-row[0], -row[1], row[2], row[3])):
            candidate = counts.copy()
            candidate[outgoing] -= 1
            if candidate[outgoing] == 0:
                del candidate[outgoing]
            candidate[incoming] = 1
            if not _preserves_bracket(counts, candidate, metadata, target):
                continue
            if _mana_diagnostics(candidate, metadata, commander_keys, commander_color_choices)["mana_base_risk"] >= diagnostics["mana_base_risk"] - 1e-9:
                continue
            counts = candidate
            swaps.append({"out": metadata[outgoing]["name"], "in": metadata[incoming]["name"]})
            changed = True
            break
        if not changed:
            break
    return counts, swaps


def _primer(name, commander_rows, rows, package, statistics, bracket, distinctive, commander_color_choices=None):
    themes = ", ".join(tag["tag_name"] for tag in package.get("tag_suggestions", []))
    references = lambda cards: ", ".join("[[" + card["name"] + "]]" for card in cards)
    title = references(commander_rows) + " — " + package.get("name", "Package")
    lines = [title, "", "Plan", f"Build around {references(commander_rows)}."]
    if commander_color_choices:
        lines[2:2] = ["Pregame commander color", *[
            f"Choose {_COLOR_NAMES[choice['color']]} for [[{choice['commander_name']}]] before the game begins. "
            "This choice applies during deck construction and throughout the game, and is part of this list's color identity."
            for choice in commander_color_choices], ""]
    if themes:
        lines.append(f"The learned package most closely matches these EDHREC profiles: {themes}.")
    defining = sorted((row for row in rows if row["primary_role"] not in {"Commander", "Lands"}),
                      key=lambda row: (-row["member_inclusion"], row["name"]))[:5]
    if defining:
        lines.append(f"Recurring cards in this package include {references(defining)}; prioritize hands that can cast and support these cards.")
    lines += ["", "Distinctive package core",
              f"This list includes {distinctive['included_count']} of the {distinctive['total']} displayed distinctive cards."]
    if distinctive["included"]:
        lines.append(f"Core cards: {references(distinctive['included'])}.")
    for omitted in distinctive["omitted"]:
        lines.append(f"[[{omitted['name']}]] is omitted: {omitted['reason']}")
    lines += ["", "Mana and opening hands",
              f"The list has {statistics['land_count']} land cards, based on a complete observed deck. "
              "Look for an opening hand with usable mana and early plays; adjust mulligans to the table and the cards drawn."]
    explanations = {"Fast Mana": "Early mana acceleration includes", "Ramp": "Develop mana with", "Card Advantage": "Replenish cards with",
                    "Wipe": "Broad board interaction includes", "Removal": "Keep interaction available through", "Tutor": "Library-search options include",
                    "Protection": "Protect resources using", "Recursion": "Recover resources with", "Tokens": "Token-producing cards include",
                    "Counters": "Counter-related cards include", "Sacrifice": "Sacrifice-related cards include"}
    for role, explanation in explanations.items():
        selected = sorted((row for row in rows if row["primary_role"] == role),
                          key=lambda row: (-row["member_inclusion"], row["name"]))[:3]
        if selected:
            lines += ["", role, f"{explanation} {references(selected)}. Check each card's conditions and timing before committing resources."]
    lines += ["", "How this list was made",
              f"NMF package memberships weight observed card inclusion. Starting from a complete sampled deck, "
              f"{statistics['distinctive_swaps']} swaps prioritize the distinctive package core; "
              f"{statistics['consensus_swaps']} further role- and curve-preserving swaps improve the package-weighted consensus score. "
              "Every card comes from the sampled pool. EDHREC averages use the matched theme pages and target bracket page; "
              "lift is EDHREC's reported ratio. Missing observations contribute zero for this display; "
              "they do not establish that a card was absent from those decks.", "", "Bracket guidance",
              f"Construction target: {bracket['target']}; exploratory recommendation: {bracket['recommended']}. "
              "This list has not been playtested. Combo lines, speed and a final power level require review; no infinite combination is asserted here."]
    if statistics.get("land_adjustments"):
        lines += ["", "Mana adjustment",
                  f"{len(statistics['land_adjustments'])} low-consensus cards without a classified support role were replaced by copies of basic lands already present in the sample. "
                  "This is a native construction heuristic, not an externally verified playability percentage."]
    if statistics.get("land_swaps"):
        lines += ["", "Mana base",
                  f"{len(statistics['land_swaps'])} land slots were upgraded using legal lands already observed in this commander's sampled pool. "
                  "The heuristic favors coverage of the list's colored mana requirements, then estimated untapped access and learned card inclusion. "
                  "Fetch-land coverage depends on fetchable land types actually retained in this list; conditional mana remains an estimate."]
    return "\n".join(lines)


def _unavailable(reason, **extra):
    return {"status": "unavailable", "generation_version": DECK_GENERATION_VERSION, "reason": reason, "cards": [], "commanders": [],
            "card_count": 0, "export_text": "", "primer": "",
            "validation": {"valid": False, "checks": {}, "errors": [reason]}, **extra}


_CORE_ROLE_FLOORS = {"Ramp": 8, "Card Advantage": 8, "Removal": 6,
                     "Wipe": 2, "Tutor": 1, "Recursion": 1, "Protection": 2}
_CORE_REASONS = {
    "metadata_missing": "Verified card metadata is unavailable, so this card cannot be checked safely.",
    "metadata_incomplete": "The available metadata is incomplete for deck construction checks.",
    "not_in_sampled_pool": "This card was not found in the commander's usable sampled card pool.",
    "illegal_in_commander": "The verified metadata does not mark this card legal in Commander.",
    "color_identity_conflict": "This card is outside the commander's permitted color identity.",
    "essential_role_conflict": "No replacement retained the deck's required ramp, card advantage, and interaction support.",
    "bracket_conflict": "No replacement retained the package's checked bracket restrictions.",
    "score_bracket_conflict": "Replaced because its checked restrictions forced a higher bracket than the original list's power score supports.",
    "mana_base_conflict": "No land replacement retained the mana base's required colored sources.",
    "no_safe_replacement": "No replacement retained the commander, land slots, and higher-priority distinctive cards.",
    "construction_unavailable": "A verified 100-card deck could not be constructed for this package.",
}


def _distinctive_targets(package, prepared, metadata):
    """Resolve displayed rows without inventing aliases or additional card copies."""
    pool = prepared.get("pool", set())
    colors = prepared.get("colors", set())
    pool_identities = {}
    for key in sorted(pool):
        if _complete_metadata(metadata.get(key)):
            pool_identities.setdefault(_metadata_identity(metadata[key]), key)
    targets = []
    for index, row in enumerate(package.get("top_cards", [])):
        row = row if isinstance(row, dict) else {}
        name = row.get("name") if isinstance(row.get("name"), str) and row["name"].strip() else "Unnamed distinctive card"
        target = {"name": name, "index": index}
        value = row.get("relative_weight", 1.0)
        target["weight"] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else 1.0
        card = metadata.get(card_key(name))
        if card is None:
            target["reason_code"] = "metadata_missing"
        elif not _complete_metadata(card):
            target["reason_code"] = "metadata_incomplete"
        else:
            identity = _metadata_identity(card)
            target.update(identity=identity, key=pool_identities.get(identity, card_key(card["name"])))
            if identity not in pool_identities:
                target["reason_code"] = "not_in_sampled_pool"
            elif card["legalities"].get("commander") != "legal":
                target["reason_code"] = "illegal_in_commander"
            elif (not set(card["color_identity"]) <= colors or
                  _is_land(card) and any(color not in colors for land, color in _BASIC_COLORS.items()
                                        if land in card["type_line"].casefold().split())):
                target["reason_code"] = "color_identity_conflict"
                if prepared.get("commander_color_choices"):
                    chosen = ", ".join(_COLOR_NAMES[choice["color"]] for choice in prepared["commander_color_choices"])
                    target["reason_context"] = f" The pregame commander color choice for this list is {chosen}."
        targets.append(target)
    return targets


def _distinctive_coverage(targets, counts, metadata, failures=None, *, unavailable=False):
    present = {_metadata_identity(metadata[key]): metadata[key]["name"] for key in counts if counts[key] > 0}
    included, omitted = [], []
    for target in targets:
        canonical = present.get(target.get("identity"))
        if canonical is not None:
            included.append({"name": target["name"], **({"canonical_name": canonical} if canonical != target["name"] else {})})
        else:
            code = ("construction_unavailable" if unavailable else
                    target.get("reason_code") or (failures or {}).get(target.get("identity"), "no_safe_replacement"))
            omitted.append({"name": target["name"], "reason_code": code,
                            "reason": _CORE_REASONS[code] + target.get("reason_context", "")})
    total = len(targets)
    return {"total": total, "included_count": len(included), "coverage": len(included) / total if total else 0.0,
            "included": included, "omitted": omitted}


def _distinctive_score(targets, counts, metadata):
    present = {_metadata_identity(metadata[key]) for key in counts}
    return (sum(target.get("identity") in present for target in targets),
            sum(target["weight"] for target in targets if target.get("identity") in present))


def _insert_distinctive(counts, targets, prepared, metadata):
    """Prefer core coverage over consensus, retaining mana slots and support floors.

    Type, role, and curve matches are graduated preferences, rather than the
    exact-signature gate used by ordinary consensus swaps. A lower-ranked core
    may yield to a higher-ranked one when both cannot fit safely.
    """
    counts = counts.copy()
    commanders, target = prepared["commander_keys"], prepared["target"]
    priorities = {}
    for row in targets:
        if "reason_code" not in row:
            priorities[row["identity"]] = max(priorities.get(row["identity"], (-1, 0)), (row["weight"], -row["index"]))
    pending = sorted((row for row in targets if "reason_code" not in row), key=lambda row: (-row["weight"], row["index"]))
    floor_roles = Counter(role for key, quantity in counts.items() if key not in commanders
                          for role in functional_tags(metadata[key]) for _ in range(quantity))
    floors = {role: min(floor_roles[role], floor) for role, floor in _CORE_ROLE_FLOORS.items()}
    source_floors = {}
    if any(_is_land(metadata[row["key"]]) for row in pending):
        initial_mana = _mana_diagnostics(counts, metadata, commanders, prepared["commander_color_choices"] or None)
        source_floors = {color: min(value, initial_mana["colored_source_targets"][color])
                         for color, value in initial_mana["colored_land_sources"].items()}
    swaps, failures = [], {}
    for _ in range(len(priorities) + 1):
        changed = False
        for row in pending:
            identity, incoming = row["identity"], row["key"]
            if any(_metadata_identity(metadata[key]) == identity for key in counts):
                failures.pop(identity, None)
                continue
            card = metadata[incoming]
            roles = Counter(role for key, quantity in counts.items() if key not in commanders
                            for role in functional_tags(metadata[key]) for _ in range(quantity))
            current_bracket_counts = _bracket_counts(counts, metadata)
            incoming_roles = set(functional_tags(card))
            candidates = []
            role_blocked = bracket_blocked = mana_blocked = False
            for outgoing in counts:
                old = metadata[outgoing]
                old_identity = _metadata_identity(old)
                if outgoing in commanders or _is_land(old) != _is_land(card):
                    continue
                if counts[outgoing] == 1 and priorities.get(old_identity, (-1, 0)) >= priorities[identity]:
                    continue
                old_roles = set(functional_tags(old))
                if any(roles[role] - (role in old_roles) + (role in incoming_roles) < floor for role, floor in floors.items()):
                    role_blocked = True
                    continue
                old_flags, incoming_flags = _all_bracket_flags(old), _all_bracket_flags(card)
                next_bracket_counts = tuple(value - old + incoming for value, old, incoming in
                                            zip(current_bracket_counts, old_flags, incoming_flags))
                if not _preserves_bracket_counts(current_bracket_counts, next_bracket_counts, target):
                    bracket_blocked = True
                    continue
                if _is_land(card):
                    candidate = counts.copy()
                    candidate[outgoing] -= 1
                    if candidate[outgoing] == 0:
                        del candidate[outgoing]
                    candidate[incoming] += 1
                    mana = _mana_diagnostics(candidate, metadata, commanders, prepared["commander_color_choices"] or None)
                    if any(mana["colored_land_sources"].get(color, 0) < floor for color, floor in source_floors.items()):
                        mana_blocked = True
                        continue
                replaces_core = counts[outgoing] == 1 and old_identity in priorities
                rank = (replaces_core, priorities[old_identity] if replaces_core else (-1, 0),
                        len(old_roles ^ incoming_roles), _type_bucket(old) != _type_bucket(card),
                        abs(_curve_bucket(old) - _curve_bucket(card)),
                        abs(old.get("cmc", 0) - card.get("cmc", 0)),
                        prepared["support"].get(outgoing, 0), outgoing)
                candidates.append((rank, outgoing))
            if not candidates:
                failures[identity] = ("mana_base_conflict" if mana_blocked else "bracket_conflict" if bracket_blocked
                                      else "essential_role_conflict" if role_blocked else "no_safe_replacement")
                continue
            _, outgoing = min(candidates, key=lambda candidate: candidate[0])
            counts[outgoing] -= 1
            if counts[outgoing] == 0:
                del counts[outgoing]
            counts[incoming] += 1
            swaps.append({"out": metadata[outgoing]["name"], "in": card["name"], "reason": "distinctive_core"})
            failures.pop(identity, None)
            changed = True
        if not changed:
            break
    return counts, swaps, failures


def _prepare_sources(source_decks, base, metadata):
    commander_names = base.get("commander_names", [])
    if (not isinstance(commander_names, list) or not commander_names
            or any(not isinstance(name, str) or not name.strip() for name in commander_names)
            or any(not _complete_metadata(metadata.get(card_key(name))) for name in commander_names)):
        return _unavailable("Verified card metadata is required for every commander before constructing a deck.")
    for group in commander_card_alias_groups(base.get("commander_name_groups", [])):
        identities = {_metadata_identity(metadata[card_key(name)]) for name in group if card_key(name) in metadata}
        if len(identities) > 1:
            return _unavailable("Verified commander aliases conflict with distinct card identities.")
    commander_keys = sorted({card_key(metadata[card_key(name)]["name"]) for name in commander_names})
    expected_identities = {_metadata_identity(metadata[key]) for key in commander_keys}
    if len(commander_keys) != len(commander_names) or len(expected_identities) != len(commander_names):
        return _unavailable("The commander configuration contains duplicate card identities.")
    try:
        colors, color_choices = _commander_colors(commander_keys, metadata, base.get("commander_color_choices"))
    except ValueError as error:
        return _unavailable(str(error))
    allowed = set()
    groups = base.get("commander_name_groups", [])
    groups = groups if isinstance(groups, list) else []
    for group in [commander_names, *groups]:
        if (not isinstance(group, list) or len(group) != len(commander_names)
                or any(not isinstance(name, str) or not name.strip() for name in group)):
            continue
        keys = frozenset(card_key(name) for name in group)
        if (len(keys) == len(commander_names) and all(_complete_metadata(metadata.get(key)) for key in keys)
                and {_metadata_identity(metadata[key]) for key in keys} == expected_identities):
            allowed.add(keys)
    commander_row_keys = set().union(*allowed)
    pool, observed = set(commander_keys), []
    for deck in sorted(source_decks, key=lambda row: str(row.get("id", ""))):
        actual = deck.get("commanders", [])
        if (not isinstance(actual, list) or len(actual) != len(commander_names)
                or any(not isinstance(name, str) or not name.strip() for name in actual)
                or frozenset(card_key(name) for name in actual) not in allowed):
            continue
        counts = Counter({key: 1 for key in commander_keys})
        malformed = False
        for card in deck.get("cards", []):
            name, quantity = card.get("name", ""), card.get("quantity", 1)
            if not isinstance(name, str) or not name.strip():
                malformed = True
                break
            key = card_key(name)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                malformed = True
                break
            if key in commander_row_keys or (key in metadata and _metadata_identity(metadata[key]) in expected_identities):
                if quantity != 1:
                    malformed = True
                    break
                continue
            if key in metadata:
                key = card_key(metadata[key]["name"])
            counts[key] += quantity
        if malformed:
            continue
        pool.update(counts)
        record = {**deck, "counts": counts}
        observed.append(record)
    signatures = {key: (tuple(functional_tags(metadata[key])), _type_bucket(metadata[key]), _curve_bucket(metadata[key]))
                  for key in pool if _complete_metadata(metadata.get(key))}
    eligible = [key for key in pool if key not in commander_keys and _complete_metadata(metadata.get(key))
                and metadata[key]["legalities"].get("commander") == "legal" and not _is_land(metadata[key])
                and set(metadata[key]["color_identity"]) <= colors]
    anchors = [deck for deck in observed if _validate(deck["counts"], commander_keys, metadata, pool,
                                                    commander_color_choices=color_choices or None)["valid"]]
    # These normalized observations are never mutated: construction copies an
    # anchor's Counter before core insertion or later candidate adjustments.
    anchor_bracket_counts = {id(deck): _bracket_counts(deck["counts"], metadata) for deck in anchors}
    presence = {key: [] for key in pool}
    # IDs retain source-list order, including repeated IDs if a caller supplies
    # them. Package sums consequently have exactly the former addition order.
    for deck in observed:
        for key in deck["counts"]:
            presence[key].append(deck["id"])
    return {"commander_keys": commander_keys, "pool": pool, "observed": observed, "anchors": anchors,
            "colors": colors, "eligible": eligible, "signatures": signatures, "presence": presence,
            "commander_color_choices": color_choices,
            "anchor_bracket_counts": anchor_bracket_counts, "anchor_power_targets": {}}


def _prepare_package(package, model_decks, source_decks, base, profiles, metadata, *, _source_context=None):
    sources = _source_context.get("prepared") if _source_context is not None else None
    if sources is None:
        sources = _prepare_sources(source_decks, base, metadata)
        if _source_context is not None:
            _source_context["prepared"] = sources
    if sources.get("status") == "unavailable":
        return sources
    commander_keys, pool = sources["commander_keys"], sources["pool"]
    observed = sources["observed"]
    package_id = package.get("id", 0)
    weights = {row["id"]: float(row["weights"][package_id]) for row in model_decks
               if isinstance(row.get("weights"), list) and len(row["weights"]) > package_id
               and isinstance(row["weights"][package_id], (float, int)) and math.isfinite(row["weights"][package_id])
               and row["weights"][package_id] > 0}
    if not weights:
        return _unavailable("No positive learned memberships are available for this package.")
    learned = [deck for deck in observed if deck.get("id") in weights]
    total_weight = sum(weights[deck["id"]] for deck in learned)
    if total_weight <= 0:
        return _unavailable("Learned deck memberships cannot be joined to the observed cardlists.")
    support = {key: sum(weights[identifier] for identifier in sources["presence"][key] if identifier in weights) / total_weight
               for key in pool}
    target, bracket_source = _bracket_target(observed, weights)
    suggested = {tag.get("tag_slug") for tag in package.get("tag_suggestions", [])}
    metric_profiles = [profile for profile in profiles if
                       (profile.get("tag_slug") in suggested and profile.get("bracket") is None)
                       or profile.get("bracket") == target]
    metrics = {key: _profile_metrics(key, metric_profiles, metadata) for key in pool}
    anchors = [deck for deck in sources["anchors"] if deck.get("id") in weights]
    if not anchors:
        missing = sorted({key for deck in learned for key in deck["counts"] if not _complete_metadata(metadata.get(key))})
        return _unavailable("No sampled package deck passes the 100-card, commander, color, singleton and legality checks.",
                            missing_metadata_count=len(missing))
    eligible = [key for key in sources["eligible"] if support.get(key, 0) > 0]
    return {"commander_keys": commander_keys, "package_id": package_id, "weights": weights,
            "pool": pool, "support": support, "target": target, "bracket_source": bracket_source,
            "suggested": suggested, "metric_profiles": metric_profiles, "metrics": metrics,
            "anchors": anchors, "colors": sources["colors"], "eligible": eligible, "signatures": sources["signatures"],
            "commander_color_choices": sources["commander_color_choices"],
            "anchor_bracket_counts": sources["anchor_bracket_counts"],
            "anchor_power_targets": sources["anchor_power_targets"]}


def _generate_one(package, model_decks, source_decks, base, profiles, metadata, *, anchor_id=None, land_adjustment=0,
                  mana_base_optimization=False, _context=None):
    prepared = _context.get("prepared") if _context is not None else None
    if prepared is None:
        prepared = _prepare_package(package, model_decks, source_decks, base, profiles, metadata,
                                    _source_context=_context.get("source_context") if _context is not None else None)
        if _context is not None:
            _context["prepared"] = prepared
    if prepared.get("status") == "unavailable":
        return {**prepared, "distinctive_cards": _distinctive_coverage(
            _distinctive_targets(package, prepared, metadata), {}, metadata, unavailable=True)}
    if "known_score_restrictions" not in prepared:
        prepared["known_score_restrictions"] = _package_observed_restrictions(package, metadata)
    commander_keys, package_id, weights = prepared["commander_keys"], prepared["package_id"], prepared["weights"]
    pool, support = prepared["pool"], prepared["support"]
    target, bracket_source = prepared["target"], prepared["bracket_source"]
    suggested, metric_profiles, metrics = prepared["suggested"], prepared["metric_profiles"], prepared["metrics"]
    anchors, colors, eligible, signatures = prepared["anchors"], prepared["colors"], prepared["eligible"], prepared["signatures"]
    color_choices = prepared["commander_color_choices"] or None
    targets = prepared.get("distinctive_targets")
    if targets is None:
        targets = prepared["distinctive_targets"] = _distinctive_targets(package, prepared, metadata)
    core_anchors = prepared.setdefault("core_anchors", {})
    anchor_contexts = prepared.setdefault("anchor_power_contexts", {})
    def context_for_anchor(deck):
        power_targets = prepared["anchor_power_targets"]
        if id(deck) not in power_targets:
            value = {"cards": [{"name": metadata[key]["name"], "quantity": quantity}
                               for key, quantity in deck["counts"].items()], "card_count": 100}
            _, numeric_target, kind = _construction_power_rating(value, metadata, commander_keys)
            power_targets[id(deck)] = numeric_target, kind
        numeric_target, kind = power_targets[id(deck)]
        if numeric_target is None:
            return prepared
        key = numeric_target, kind
        if key not in anchor_contexts:
            anchor_contexts[key] = _score_target_context(prepared, profiles, metadata, numeric_target,
                "verified_anchor_power_score" if kind == "verified" else "calculated_anchor_power_score")
        return anchor_contexts[key]
    def bracket_penalty(deck):
        return sum(not passed for passed in _bracket_rule_checks(prepared["anchor_bracket_counts"][id(deck)],
                                                               context_for_anchor(deck)["target"]).values())
    if anchor_id is not None:
        anchors = [deck for deck in anchors if deck["id"] == anchor_id]
        if not anchors:
            return _unavailable("The requested anchor does not pass the observed 100-card deck checks.",
                                distinctive_cards=_distinctive_coverage(targets, {}, metadata, unavailable=True))
    anchors = sorted(anchors, key=lambda deck: (bracket_penalty(deck),
        *(-value for value in _distinctive_score(targets, deck["counts"], metadata)), -weights[deck["id"]],
        -sum(support.get(key, 0) for key in deck["counts"] if key not in commander_keys), str(deck["id"])))
    best, best_rank = None, None
    for observed_anchor in anchors:
        anchor_context = context_for_anchor(observed_anchor)
        anchor_target = anchor_context["target"]
        penalty = bracket_penalty(observed_anchor)
        if best_rank is not None and penalty > best_rank[0]:
            break
        if observed_anchor["id"] not in core_anchors:
            core_anchors[observed_anchor["id"]] = _insert_distinctive(observed_anchor["counts"], targets, anchor_context, metadata)
        core_counts, core_swaps, core_failures = core_anchors[observed_anchor["id"]]
        core_score = _distinctive_score(targets, core_counts, metadata)
        candidate_rank = (penalty, *(-value for value in core_score), -weights[observed_anchor["id"]],
            -sum(support.get(key, 0) for key in core_counts if key not in commander_keys), str(observed_anchor["id"]))
        if best_rank is None or candidate_rank < best_rank:
            best, best_rank = (observed_anchor, core_counts, core_swaps, core_failures, anchor_context), candidate_rank
        if core_score[0] == len(targets):
            break
        # Absolute exclusions cannot be solved by trying hundreds more anchors.
        present = {_metadata_identity(metadata[key]) for key in core_counts}
        unresolved = [row for row in targets if row.get("identity") not in present and "reason_code" not in row]
        if not unresolved or all(core_failures.get(row["identity"]) == "bracket_conflict" and
                (anchor_target <= 2 and _bracket_flags(metadata[row["key"]])[0] or anchor_target < 4 and
                 any(_bracket_flags(metadata[row["key"]])[1:])) for row in unresolved):
            break
    anchor, counts, core_swaps, core_failures, prepared = best
    target, bracket_source = prepared["target"], prepared["bracket_source"]
    metric_profiles, metrics = prepared["metric_profiles"], prepared["metrics"]
    counts = counts.copy()
    rank = lambda key: (support.get(key, 0), metrics[key]["average_inclusion"] or 0,
                        metrics[key]["average_lift"] or 0)
    swaps = list(core_swaps)
    protected_identities = {row.get("identity") for row in targets}
    protected = {key for key in counts if _metadata_identity(metadata[key]) in protected_identities}
    present_identities = Counter()
    outgoing_by_signature = {}
    for key, quantity in counts.items():
        present_identities[_metadata_identity(metadata[key])] += quantity
        if (key not in commander_keys and key not in protected and quantity == 1
                and not _is_land(metadata[key]) and _copy_limit(metadata[key]) == 1):
            outgoing_by_signature.setdefault(signatures[key], set()).add(key)
    # Land identities/copies, repeated exceptions and the command zone remain
    # fixed. For spells, conserve all functional roles, type and curve bucket.
    for incoming in sorted(eligible, key=lambda key: (*(-value for value in rank(key)), key)):
        incoming_identity = _metadata_identity(metadata[incoming])
        if incoming in counts or present_identities.get(incoming_identity, 0) > 0:
            continue
        signature = signatures[incoming]
        outgoing = outgoing_by_signature.get(signature, set())
        if not outgoing:
            continue
        remove = min(outgoing, key=lambda key: (rank(key), key))
        if rank(incoming) <= rank(remove):
            continue
        candidate = counts.copy()
        del candidate[remove]
        candidate[incoming] = 1
        if not _preserves_bracket(counts, candidate, metadata, target):
            continue
        counts = candidate
        outgoing.remove(remove)
        present_identities[_metadata_identity(metadata[remove])] -= 1
        present_identities[incoming_identity] += 1
        if incoming not in protected and _copy_limit(metadata[incoming]) == 1:
            outgoing.add(incoming)
        swaps.append({"out": metadata[remove]["name"], "in": metadata[incoming]["name"]})
    land_changes = []
    # Candidate exploration can improve a sparse mana base using only basics
    # already present in the sampled pool. The ordinary builder keeps lands fixed.
    for _ in range(max(0, min(6, land_adjustment))):
        if sum(quantity for key, quantity in counts.items() if _is_land(metadata[key])) >= 40:
            break
        basics = [key for key in pool if key in metadata and _complete_metadata(metadata[key]) and _is_basic(metadata[key])
                  and set(metadata[key]["color_identity"]) <= colors and metadata[key]["legalities"].get("commander") == "legal"]
        removable = [key for key in counts if key not in commander_keys and key not in protected and counts[key] == 1
                     and not _is_land(metadata[key]) and not set(functional_tags(metadata[key])) &
                     {"Ramp", "Fast Mana", "Card Advantage", "Removal", "Wipe", "Tutor", "Recursion", "Protection"}]
        if not basics or not removable:
            break
        demand = Counter(symbol for key, quantity in counts.items() for symbol in re.findall(r"\{([WUBRG])\}", metadata[key].get("mana_cost", ""))
                         for _ in range(quantity))
        diagnostics = _mana_diagnostics(counts, metadata, commander_keys, color_choices)
        add = min(basics, key=lambda key: (-max((demand[color] / max(1, diagnostics["colored_land_sources"].get(color, 0))
                                              for color in metadata[key]["color_identity"]), default=0), key))
        remove = min(removable, key=lambda key: (rank(key), -metadata[key].get("cmc", 0), key))
        candidate = counts.copy()
        del candidate[remove]
        candidate[add] += 1
        if not _preserves_bracket(counts, candidate, metadata, target):
            break
        counts = candidate
        land_changes.append({"out": metadata[remove]["name"], "in": metadata[add]["name"]})
    land_swaps = []
    if mana_base_optimization:
        counts, land_swaps = _upgrade_land_base(counts, pool, metadata, commander_keys, support, protected, target, bracket_source,
                                              color_choices)
    validation = _validate(counts, commander_keys, metadata, pool, commander_color_choices=color_choices)
    if not validation["valid"]:
        return _unavailable("Construction did not pass the final deck checks.",
                            distinctive_cards=_distinctive_coverage(targets, {}, metadata, unavailable=True))
    result = _assemble_generated_deck(package, counts, prepared, metadata, anchor=anchor,
        core_swaps=core_swaps, core_failures=core_failures, swaps=swaps,
        land_changes=land_changes, land_swaps=land_swaps)
    return _repair_score_bracket(result, package, counts, prepared, profiles, metadata, anchor=anchor,
        core_swaps=core_swaps, core_failures=core_failures, swaps=swaps,
        land_changes=land_changes, land_swaps=land_swaps)


def _assemble_generated_deck(package, counts, prepared, metadata, *, anchor, core_swaps, core_failures,
                             swaps, land_changes, land_swaps):
    """Render all derived fields together so repaired lists never retain stale rows."""
    commander_keys, pool = prepared["commander_keys"], prepared["pool"]
    target, bracket_source = prepared["target"], prepared["bracket_source"]
    colors, support, weights = prepared["colors"], prepared["support"], prepared["weights"]
    metrics, metric_profiles = prepared["metrics"], prepared["metric_profiles"]
    targets, package_id = prepared["distinctive_targets"], prepared["package_id"]
    color_choices = prepared["commander_color_choices"] or None
    validation = _validate(counts, commander_keys, metadata, pool, commander_color_choices=color_choices)
    bracket = _bracket_validation(counts, metadata, target, bracket_source)
    distinctive = _distinctive_coverage(targets, counts, metadata, core_failures)
    rows = []
    for key, quantity in counts.items():
        card = metadata[key]
        role = "Commander" if key in commander_keys else functional_tags(card)[0]
        rows.append({"name": card["name"], "quantity": quantity, "primary_role": role,
                     **metrics[key], "member_inclusion": support.get(key, 0)})
    rows.sort(key=lambda row: (ROLES.index(row["primary_role"]), card_key(row["name"])))
    commander_rows = [{"name": metadata[key]["name"], "quantity": 1} for key in commander_keys]
    name = " + ".join(row["name"] for row in commander_rows) + " — " + package.get("name", f"Package {package_id + 1}")
    nonland = [row for row in rows if row["primary_role"] not in {"Commander", "Lands"}]
    statistics = {
        "anchor_deck_id": anchor["id"], "anchor_source_url": anchor.get("source_url", ""),
        "land_count": sum(quantity for key, quantity in counts.items() if _is_land(metadata[key])),
        "nonland_count": sum(quantity for key, quantity in counts.items() if not _is_land(metadata[key])),
        "swaps": len(swaps), "swap_details": swaps, "sampled_pool_size": len(pool),
        "distinctive_swaps": len(core_swaps), "consensus_swaps": len(swaps) - len(core_swaps),
        "distinctive_priority_score": _distinctive_score(targets, counts, metadata)[1],
        "land_adjustments": land_changes, "land_swaps": land_swaps, "anchor_membership": weights[anchor["id"]],
        "mana_quality": _mana_diagnostics(counts, metadata, commander_keys, color_choices),
        "weighted_consensus_score": sum(row["member_inclusion"] for row in nonland) / max(1, len(nonland)),
        "average_inclusion": sum(row["average_inclusion"] for row in nonland) / len(nonland) if metric_profiles and nonland else None,
        "average_lift": sum(row["average_lift"] for row in nonland) / len(nonland) if metric_profiles and nonland else None,
        "profile_count": len(metric_profiles), "profile_slugs": [profile.get("tag_slug") for profile in metric_profiles],
        "profile_names": [profile.get("tag_name") or profile.get("tag_slug", "") for profile in metric_profiles],
        "target_bracket": target, "recommended_bracket": bracket["recommended"],
        "method": "Verified 100-card anchor; distinctive package core prioritized within mana, support-role and bracket constraints; NMF-weighted role/type/curve-preserving consensus swaps",
        "metric_note": "Unweighted mean across the package's matched theme pages and target bracket page, excluding the general page; missing measurements contribute zero. Lift is the raw EDHREC ratio, not a percentage-point difference. Coverage counts show observed measurements.",
    }
    measured = [row for row in rows if row["lift_observed_profiles"] > 0 and row["primary_role"] != "Commander"]
    highest_lift = max(measured, key=lambda row: (row["average_lift"], row["average_inclusion"], row["name"]), default=None)
    statistics["highest_average_lift"] = {"name": highest_lift["name"], "lift": highest_lift["average_lift"]} if highest_lift else None
    export = "\n".join(f"{row['quantity']} {row['name']}" +
                       (" *CMDR*" if row["primary_role"] == "Commander" else "") for row in rows) + "\n"
    plain = ("~".join(f"{row['quantity']} {row['name']}" for row in rows if row["primary_role"] != "Commander")
             + "~~" + "~".join(f"1 {row['name']}" for row in commander_rows) + "~Z~")
    validation.update({"bracket": bracket, "external_url": "https://edhpowerlevel.com?d=" + quote_plus(plain),
                       "scope": "Construction rules checked with supplied card metadata; not playtested or certified for a bracket."})
    if color_choices:
        validation.update(commander_color_choices=deepcopy(color_choices),
                          effective_color_identity=[color for color in "WUBRG" if color in colors])
    result = {"status": "generated", "generation_version": DECK_GENERATION_VERSION, "distinctive_cards": distinctive,
            "name": name, "commanders": commander_rows, "cards": rows,
            "card_count": sum(counts.values()), "export_text": export,
            "primer": _primer(name, commander_rows, rows, package, statistics, bracket, distinctive, color_choices),
            "validation": validation, "statistics": statistics}
    _attach_calculated_power(result, metadata, commander_keys)
    return result


def _attach_calculated_power(deck, metadata, commander_keys):
    from .external_validation import cardlist_hash
    from .power_scoring import calculate_power_rating

    deck.setdefault("validation", {})["deck_sha256"] = cardlist_hash(deck["cards"])
    rating = calculate_power_rating(deck["cards"], metadata,
        commanders=[metadata[key]["name"] for key in commander_keys])
    deck["validation"]["calculated_power"] = rating
    _mark_restriction_validation(deck)
    return rating


def _mark_restriction_validation(deck):
    """Only a complete current site observation verifies the final card list."""
    from .external_validation import cardlist_hash, external_restriction_evidence

    validation = deck.setdefault("validation", {})
    digest = cardlist_hash(deck["cards"])
    rating = validation.get("external_rating", {})
    evidence = external_restriction_evidence(rating, expected_hash=digest,
        expected_commanders=[row["name"] for row in deck.get("commanders", [])])
    result = {"status": "verified" if evidence is not None else "pending",
              "source": "edhpowerlevel.com", "deck_sha256": digest,
              "categories": list(_SCORE_RESTRICTION_NAMES)}
    if evidence is not None:
        result.update(checked_at=rating["checked_at"],
                      source_script_sha256=rating["restriction_check"]["source_script_sha256"],
                      method="edhpowerlevel_page")
    else:
        result.update(method="local_fallback",
            note="Local restriction checks are provisional. This exact final card list still requires a complete EDHPowerLevel restriction check, including land denial, chaining extra turns and qualifying combos.")
    validation["restriction_validation"] = result
    return result


def _construction_power_rating(deck, metadata, commander_keys):
    """Prefer current exact-list site observations, then a dated local estimate."""
    from .external_validation import attach_external_validation
    from .power_brackets import verified_score_bracket

    deck.setdefault("commanders", [{"name": metadata[key]["name"], "quantity": 1} for key in commander_keys])
    calculated = _attach_calculated_power(deck, metadata, commander_keys)
    external = attach_external_validation(deck)
    _mark_restriction_validation(deck)
    target = verified_score_bracket(external)
    if target is not None:
        return external, target, "verified"
    if calculated.get("status") == "calculated":
        return calculated, calculated["score_bracket"], "calculated"
    return external, None, None


def _bracket_excess(counts, target):
    limits = (0 if target <= 2 else 3 if target == 3 else None,
              EXTRA_TURN_LIMITS[target - 1], 0 if target < 4 else None,
              0 if target < 4 else None, 0 if target < 3 else None,
              0 if target < 4 else None)
    return tuple(max(0, value - limit) if limit is not None else 0
                 for value, limit in zip(counts, limits))


_SCORE_RESTRICTION_NAMES = ("game_changers", "extra_turns", "mass_land_denial", "early_combos", "late_combos", "chaining_extra_turns")


def _observed_bracket_restrictions(rating, metadata, *, audited_keys=()):
    """Use only named restrictions observed for this exact external card list."""
    from .external_validation import external_restriction_evidence

    observed = rating.get("bracket_restrictions", {})
    canonical = lambda name: card_key(metadata.get(card_key(name), {}).get("name", name))
    result = {name: ([frozenset(canonical(card) for card in group) for group in observed.get(name, [])]
                     if name in {"early_combos", "late_combos"} else
                     {canonical(card) for card in observed.get(name, [])})
              for name in _SCORE_RESTRICTION_NAMES}
    complete = external_restriction_evidence(rating) is not None
    if not complete:
        # Historical records used extra_turns for the site's restricted chaining
        # list. Keep their positive evidence without certifying missing groups.
        result["chaining_extra_turns"].update(result["extra_turns"])
    result["_audited_cards"] = set(audited_keys) if complete else set()
    result["_observation_sources"] = ([{key: deepcopy(rating[key]) for key in
        ("deck_sha256", "checked_at", "source", "restriction_check") if key in rating}]
        if rating.get("status") == "verified" and observed else [])
    return result


def _merge_observed_restrictions(first, second):
    """Carry observed exclusions, never a different list's verified status."""
    result = deepcopy(first)
    for name in (*_SCORE_RESTRICTION_NAMES, "_audited_cards", "_observation_sources"):
        values = second.get(name, [] if name in {"early_combos", "late_combos", "_observation_sources"} else set())
        if isinstance(values, set):
            result.setdefault(name, set()).update(values)
        else:
            result.setdefault(name, []).extend(group for group in values if group not in result[name])
    return result


def _package_observed_restrictions(package, metadata):
    from .external_validation import attach_external_validation

    existing = package.get("generated_deck", {})
    checked = {"cards": existing.get("cards", []), "card_count": existing.get("card_count", 100),
               "commanders": existing.get("commanders", [])}
    rating = attach_external_validation(checked)
    if rating.get("status") != "verified":
        rating = {}
    keys = {card_key(metadata.get(card_key(row["name"]), {}).get("name", row["name"]))
            for row in existing.get("cards", [])}
    return _observed_bracket_restrictions(rating, metadata, audited_keys=keys)


def _restriction_card_flags(key, metadata, restrictions):
    local = (*_bracket_flags(metadata[key]), _chainable_extra_turn(metadata[key]))
    names = ("game_changers", "extra_turns", "mass_land_denial", "chaining_extra_turns")
    # Complete site observations can clear a local false positive (including
    # the site's MLD whitelist) for audited cards. New cards use local fallback.
    audited = key in restrictions.get("_audited_cards", ())
    return tuple(key in restrictions[name] or (not audited and flag)
                 for name, flag in zip(names, local))


def _preserves_observed_candidate_restrictions(deck, restrictions):
    """Never offer an alternate that restores an observed disallowed group."""
    if not restrictions:
        return True
    from .power_brackets import verified_score_bracket

    validation = deck.get("validation", {})
    target = verified_score_bracket(validation.get("external_rating"))
    calculated = validation.get("calculated_power", {})
    if target is None and calculated.get("status") == "calculated":
        target = calculated["score_bracket"]
    if target is None:
        target = validation["bracket"]["target"]
    counts = Counter()
    for row in deck["cards"]:
        counts[card_key(row["name"])] += row.get("quantity", 1)
    single = ("game_changers", "extra_turns", "mass_land_denial")
    flags = (*[sum(quantity for key, quantity in counts.items() if key in restrictions[name]) for name in single],
             *[sum(group <= counts.keys() for group in restrictions[name]) for name in ("early_combos", "late_combos")],
             sum(quantity for key, quantity in counts.items() if key in restrictions["chaining_extra_turns"]))
    return all(_score_restriction_checks(flags, target).values())


def _score_restriction_counts(counts, metadata, restrictions):
    values = [0, 0, 0]
    for key, quantity in counts.items():
        for index, flag in enumerate(_restriction_card_flags(key, metadata, restrictions)[:3]):
            values[index] += quantity * flag
    chainable = sum(quantity * _restriction_card_flags(key, metadata, restrictions)[3]
                    for key, quantity in counts.items())
    return (*values, *[sum(group <= counts.keys() for group in restrictions[name])
                       for name in _SCORE_RESTRICTION_NAMES[3:5]], chainable)


def _score_restriction_checks(counts, target):
    return {name: not excess for name, excess in zip(_SCORE_RESTRICTION_NAMES, _bracket_excess(counts, target))}


def _strict_bracket_replacements(counts, prepared, metadata, target, restrictions):
    """Bounded search for a complete repair, never an incremental partial fix.

    Each edge removes at least one excess restriction and introduces none.
    Whole-list role and mana floors remain those of the original generated list.
    A small beam can explore alternative Game Changer omissions and substitutes
    without an unbounded combination search over a large observed card pool.
    """
    commanders, colors = prepared["commander_keys"], prepared["colors"]
    targets, support = prepared["distinctive_targets"], prepared["support"]
    color_choices = prepared["commander_color_choices"] or None
    initial_roles = Counter(role for key, quantity in counts.items() if key not in commanders
                            for role in functional_tags(metadata[key]) for _ in range(quantity))
    role_floors = {role: min(initial_roles[role], floor) for role, floor in _CORE_ROLE_FLOORS.items()}
    initial_mana = _mana_diagnostics(counts, metadata, commanders, color_choices)
    source_floors = {color: min(value, initial_mana["colored_source_targets"][color])
                     for color, value in initial_mana["colored_land_sources"].items()}
    incoming_pool = [key for key in prepared["pool"] if _complete_metadata(metadata.get(key))
                     and metadata[key]["legalities"].get("commander") == "legal"
                     and set(metadata[key]["color_identity"]) <= colors and key not in commanders
                     and (not _is_land(metadata[key]) or all(color in colors for land, color in _BASIC_COLORS.items()
                          if land in metadata[key]["type_line"].casefold().split()))]
    incoming_pool.sort(key=lambda key: (-support.get(key, 0), key))
    initial_core = _distinctive_score(targets, counts, metadata)
    core_counts = Counter(row.get("identity") for row in targets)
    core_weights = Counter()
    for row in targets:
        core_weights[row.get("identity")] += row["weight"]
    card_flags = {key: _restriction_card_flags(key, metadata, restrictions)
                  for key in set(incoming_pool) | counts.keys()}
    # A repair can compare thousands of observed cards against several beam
    # states. Card properties stay fixed throughout that search.
    traits = {key: (_metadata_identity(metadata[key]), _is_land(metadata[key]),
                    frozenset(functional_tags(metadata[key])), _copy_limit(metadata[key]),
                    _type_bucket(metadata[key]), _curve_bucket(metadata[key]))
              for key in card_flags}
    buckets = {}
    for key in incoming_pool:
        identity, land, roles, _, kind, curve = traits[key]
        signature = (land, roles, kind, curve, card_flags[key],
                     identity if core_counts[identity] or core_weights[identity] else None,
                     key if any(key in group for category in _SCORE_RESTRICTION_NAMES[3:5]
                                for group in restrictions[category]) else None)
        buckets.setdefault(signature, []).append(key)
    # Members share every rank term except support/name, already sorted above.
    # A heap merges these exact ranked streams until four valid lists survive.
    buckets = list(buckets.values())
    beam = [(counts.copy(), [], 0.0)]
    seen = {tuple(sorted(counts.items()))}
    for _ in range(sum(counts.values()) - len(commanders)):
        following = []
        for current, swaps, shape_cost in beam:
            current_flags = _score_restriction_counts(current, metadata, restrictions)
            excess = _bracket_excess(current_flags, target)
            if not any(excess):
                return current, swaps
            excess_total = sum(excess)
            restriction_updates = {}
            identities = Counter(traits[key][0] for key in current for _ in range(current[key]))
            current_core = _distinctive_score(targets, current, metadata)
            roles = Counter(role for key, quantity in current.items() if key not in commanders
                            for role in functional_tags(metadata[key]) for _ in range(quantity))
            for outgoing in sorted(current):
                if outgoing in commanders:
                    continue
                old = metadata[outgoing]
                outgoing_identity, outgoing_land, old_roles, _, old_type, old_curve = traits[outgoing]
                remaining_keys = current.keys() - {outgoing} if current[outgoing] == 1 else current.keys()
                without_flags = (*[value - flag for value, flag in zip(current_flags[:3], card_flags[outgoing])],
                                 *[sum(group <= remaining_keys for group in restrictions[name])
                                   for name in _SCORE_RESTRICTION_NAMES[3:5]],
                                 current_flags[5] - card_flags[outgoing][3])
                if sum(_bracket_excess(without_flags, target)) >= excess_total:
                    continue
                combo_baselines, combo_completers = [], []
                for category in _SCORE_RESTRICTION_NAMES[3:5]:
                    baseline, completers = 0, Counter()
                    for group in restrictions[category]:
                        missing = group - remaining_keys
                        if not missing:
                            baseline += 1
                        elif len(missing) == 1:
                            completers[next(iter(missing))] += 1
                    combo_baselines.append(baseline)
                    combo_completers.append(completers)
                local = []

                def offer(bucket_index, position, prefix, distance, next_excess):
                    members = buckets[bucket_index]
                    while position < len(members):
                        incoming = members[position]
                        identity, _, _, copy_limit, _, _ = traits[incoming]
                        if (incoming != outgoing
                                and identities[identity] - (outgoing_identity == identity) < copy_limit):
                            rank = (*prefix, -support.get(incoming, 0), incoming)
                            heappush(local, (rank, bucket_index, position, distance, next_excess))
                            return
                        position += 1

                for bucket_index, members in enumerate(buckets):
                    incoming = members[0]
                    identity, incoming_land, incoming_roles, _, incoming_type, incoming_curve = traits[incoming]
                    if incoming_land != outgoing_land:
                        continue
                    if any(roles[role] - (role in old_roles) + (role in incoming_roles) < floor
                           for role, floor in role_floors.items()):
                        continue
                    combo_counts = tuple(baseline + completers[incoming]
                                         for baseline, completers in zip(combo_baselines, combo_completers))
                    exchange = card_flags[outgoing], card_flags[incoming], combo_counts
                    if exchange not in restriction_updates:
                        next_flags = (*[value - old + new for value, old, new in
                                        zip(current_flags[:3], card_flags[outgoing], card_flags[incoming])],
                                      *combo_counts,
                                      current_flags[5] - card_flags[outgoing][3] + card_flags[incoming][3])
                        next_excess = _bracket_excess(next_flags, target)
                        restriction_updates[exchange] = (next_excess, sum(next_excess) < excess_total
                            and all(after <= before for before, after in zip(excess, next_excess)))
                    next_excess, reduces_restrictions = restriction_updates[exchange]
                    if not reduces_restrictions:
                        continue
                    lost = outgoing_identity != identity and identities[outgoing_identity] == 1
                    gained = outgoing_identity != identity and identities[identity] == 0
                    core = (current_core[0] - lost * core_counts[outgoing_identity] + gained * core_counts[identity],
                            current_core[1] - lost * core_weights[outgoing_identity] + gained * core_weights[identity])
                    distance = (len(old_roles ^ incoming_roles) + (old_type != incoming_type)
                                + abs(old_curve - incoming_curve))
                    prefix = (initial_core[0] - core[0], initial_core[1] - core[1], sum(next_excess),
                              shape_cost + distance)
                    offer(bucket_index, 0, prefix, distance, next_excess)
                # Only four valid replacements enter this outgoing card's beam.
                # Rank the same scalar criteria first, then build/check complete
                # lists in that order, including seen-state and mana exclusions.
                accepted = 0
                while local:
                    rank, bucket_index, position, distance, next_excess = heappop(local)
                    incoming = buckets[bucket_index][position]
                    offer(bucket_index, position + 1, rank[:-2], distance, next_excess)
                    candidate = current.copy()
                    candidate[outgoing] -= 1
                    if not candidate[outgoing]:
                        del candidate[outgoing]
                    candidate[incoming] += 1
                    identity_counts = tuple(sorted(candidate.items()))
                    if identity_counts in seen:
                        continue
                    if outgoing_land:
                        mana = _mana_diagnostics(candidate, metadata, commanders, color_choices)
                        if any(mana["colored_land_sources"].get(color, 0) < floor for color, floor in source_floors.items()):
                            continue
                    removed_restrictions = [name for name, before, after in zip(
                        _SCORE_RESTRICTION_NAMES, excess, next_excess) if after < before]
                    swap = {"out": old["name"], "in": metadata[incoming]["name"], "reason": "score_bracket_repair",
                            "restrictions": removed_restrictions}
                    following.append((rank, candidate, [*swaps, swap], shape_cost + distance, identity_counts))
                    accepted += 1
                    if accepted == 4:
                        break
        if not following:
            return None
        following.sort(key=lambda row: row[0])
        completed = [row for row in following if all(_score_restriction_checks(
            _score_restriction_counts(row[1], metadata, restrictions), target).values())]
        if completed:
            _, repaired, swaps, _, _ = min(completed, key=lambda row: row[0])
            return repaired, swaps
        beam = []
        for _, candidate, swaps, shape_cost, identity_counts in following:
            if identity_counts in seen:
                continue
            seen.add(identity_counts)
            beam.append((candidate, swaps, shape_cost))
            if len(beam) == 8:
                break
    return None


def _score_target_context(prepared, profiles, metadata, target, source):
    metric_profiles = [profile for profile in profiles if
                       (profile.get("tag_slug") in prepared["suggested"] and profile.get("bracket") is None)
                       or profile.get("bracket") == target]
    return {**prepared, "target": target, "bracket_source": source, "metric_profiles": metric_profiles,
            "metrics": {key: _profile_metrics(key, metric_profiles, metadata) for key in prepared["pool"]}}


def _repair_score_bracket(option, package, counts, prepared, profiles, metadata, *, anchor,
                          core_swaps, core_failures, swaps, land_changes, land_swaps):
    from .external_validation import attach_external_validation, cardlist_hash
    from .power_brackets import POWER_SCORE_BRACKET_POLICY_VERSION

    rating, score_target, rating_kind = _construction_power_rating(option, metadata, prepared["commander_keys"])
    if score_target is None:
        return option
    current_bracket = max(rating.get("bracket", 0), option["validation"]["bracket"]["recommended"])
    original = option
    original_counts = counts.copy()
    repair = {"status": "blocked", "policy_version": POWER_SCORE_BRACKET_POLICY_VERSION,
              "from_deck_sha256": cardlist_hash(option["cards"]),
              "from_power_level": rating["power_level"], "from_bracket": current_bracket,
              "score_bracket": score_target, "initial_score_bracket": score_target,
              "checked_at": rating["checked_at"], "rating_kind": rating_kind,
              "source": rating["source"], "source_url": rating["source_url"], "swap_details": [],
              "from_cards": [{"name": row["name"], "quantity": row["quantity"]} for row in option["cards"]]}
    restrictions = _merge_observed_restrictions(
        prepared.get("known_score_restrictions", _observed_bracket_restrictions({}, metadata)),
        _observed_bracket_restrictions(rating, metadata, audited_keys=counts.keys()))
    repair["restriction_observation_sources"] = deepcopy(restrictions["_observation_sources"])
    repair["observed_restrictions"] = deepcopy(rating.get("bracket_restrictions", {}))
    repair["original_rule_checks"] = _score_restriction_checks(
        _score_restriction_counts(counts, metadata, restrictions), score_target)
    all_swaps, seen = [], set()
    for _ in range(6):
        identity = (cardlist_hash(option["cards"]), score_target)
        if identity in seen:
            repair["reason"] = "The bounded score/bracket adjustment did not converge to one consistent card list and target."
            break
        seen.add(identity)
        flags = _score_restriction_counts(counts, metadata, restrictions)
        checks = _score_restriction_checks(flags, score_target)
        if all(checks.values()):
            # An unknown externally reported restriction must remain a review,
            # while a lower source-deck bracket can safely follow the score.
            if not all_swaps and rating.get("bracket", 0) > score_target:
                repair["reason"] = "The external bracket is higher than the score, but no excess checked card restriction identifies a safe card to remove."
                break
            context = _score_target_context(prepared, profiles, metadata, score_target,
                "verified_power_score" if rating_kind == "verified" else "calculated_power_score")
            removed = {_metadata_identity(metadata[card_key(row["out"])]) for row in all_swaps
                       if card_key(row["out"]) not in counts}
            failures = {**core_failures, **{identity: "score_bracket_conflict" for identity in removed}}
            result = _assemble_generated_deck(package, counts, context, metadata, anchor=anchor,
                core_swaps=core_swaps, core_failures=failures, swaps=swaps,
                land_changes=land_changes, land_swaps=land_swaps)
            _apply_score_bracket_checks(result, flags, checks, score_target)
            attach_external_validation(result)
            _mark_restriction_validation(result)
            if all_swaps:
                note = ("The original exact-list power score selected the initial construction target. "
                        "Every replacement list was recalculated when complete fresh inputs were available; "
                        "a local estimate is not an externally verified EDHPowerLevel rating.")
                repair.update(status="repaired", score_bracket=score_target, swap_details=all_swaps,
                    rule_checks=checks, iterations=len(seen), note=note)
                result["statistics"]["score_bracket_repair"] = repair
                result["validation"]["bracket"]["notes"].append(note)
                result["primer"] += "\n\nScore and bracket adjustment\n" + note
                for swap in all_swaps:
                    result["primer"] += (f"\nReplaced [[{swap['out']}]] with [[{swap['in']}]] to remove "
                        f"{', '.join(swap['restrictions']).replace('_', ' ')}.")
            return result
        outcome = _strict_bracket_replacements(counts, prepared, metadata, score_target, restrictions)
        if outcome is None:
            repair["reason"] = "No complete in-pool replacement retained the commander, 100-card legality, land slots, colored mana sources and required support roles while meeting the score bracket's checked restrictions."
            break
        counts, replacements = outcome
        all_swaps.extend(replacements)
        context = _score_target_context(prepared, profiles, metadata, score_target,
            "verified_power_score_repair" if rating_kind == "verified" else "calculated_power_score_repair")
        option = _assemble_generated_deck(package, counts, context, metadata, anchor=anchor,
            core_swaps=core_swaps, core_failures=core_failures, swaps=swaps,
            land_changes=land_changes, land_swaps=land_swaps)
        next_rating, next_target, next_kind = _construction_power_rating(option, metadata, prepared["commander_keys"])
        if next_target is not None:
            rating, score_target, rating_kind = next_rating, next_target, next_kind
            following = _observed_bracket_restrictions(rating, metadata, audited_keys=counts.keys())
            restrictions = _merge_observed_restrictions(restrictions, following)
    else:
        repair["reason"] = "The score/bracket adjustment reached its bounded iteration limit without a consistent final target."
    # A later failed step never publishes a partially repaired card list.
    repair.update(swap_details=[], rule_checks=_score_restriction_checks(
        _score_restriction_counts(original_counts, metadata, restrictions), repair["initial_score_bracket"]))
    original["statistics"]["score_bracket_repair"] = repair
    original["primer"] += "\n\nScore and bracket review\n" + repair["reason"]
    return original


def _apply_score_bracket_checks(deck, flags, checks, target):
    bracket = deck["validation"]["bracket"]
    bracket["rule_checks"].update(checks)
    recommended = next(bracket for bracket in range(target, 6)
                       if all(_score_restriction_checks(flags, bracket).values()))
    bracket.update(target=target, recommended=recommended,
        game_changer_count=flags[0], extra_turn_count=flags[1], mass_land_denial_count=flags[2],
        extra_turns_present=flags[1] > 0, mass_land_denial_present=flags[2] > 0,
        observed_early_combo_count=flags[3], observed_late_combo_count=flags[4],
        chainable_extra_turn_count=flags[5], extra_turn_limit=EXTRA_TURN_LIMITS[target - 1])
    deck["statistics"].update(target_bracket=target, recommended_bracket=recommended)


def _successful_score_repair(option):
    return option.get("statistics", {}).get("score_bracket_repair", {}).get("status") in {"repaired", "retained"}


def _score_repair_priority(option):
    if _successful_score_repair(option):
        return 0
    repair = option.get("statistics", {}).get("score_bracket_repair", {})
    return 1 if repair.get("retention_blocked") else 2


def _repair_existing_package_deck(existing, package, memberships, decks, base, profiles, metadata, context):
    """Repair a rated old list, or retain a still-legal previously applied fix.

    A detector or source refresh can change anchors before an old rated hash is
    regenerated. Preserve that exact-list evidence, and never undo a completed
    repair merely because its original observation has since expired.
    """
    if not isinstance(existing, dict) or existing.get("status") != "generated":
        return None
    from .external_validation import attach_external_validation, cardlist_hash
    from .power_brackets import bracket_for_power_score

    current = deepcopy(existing)
    commander_keys = [card_key(row["name"]) for row in current.get("commanders", [])
                      if isinstance(row, dict) and isinstance(row.get("name"), str)
                      and card_key(row["name"]) in metadata]
    current_rating, current_target, _ = _construction_power_rating(current, metadata, commander_keys)
    history = deepcopy(current.get("statistics", {}).get("score_bracket_repair", {}))
    successful_history = history.get("status") in {"repaired", "retained"}
    seed = deepcopy(current)
    original_cards = history.get("from_cards")
    if original_cards is not None:
        try:
            if cardlist_hash(original_cards) != history.get("from_deck_sha256"):
                return None
        except (ValueError, TypeError):
            return None
        seed["cards"] = deepcopy(original_cards)
        seed["card_count"] = sum(row["quantity"] for row in original_cards)
    original_rating, original_target, _ = _construction_power_rating(seed, metadata, commander_keys)
    if current_target is not None:
        seed = current
    elif original_target is None:
        if (not successful_history or bracket_for_power_score(history.get("from_power_level"))
                != history.get("initial_score_bracket", history.get("score_bracket"))):
            return None
        seed = current
    prepared = context.get("prepared")
    if prepared is None:
        prepared = _prepare_package(package, memberships, decks, base, profiles, metadata,
            _source_context=context.get("source_context"))
        context["prepared"] = prepared
    if prepared.get("status") == "unavailable":
        return None
    prepared.setdefault("distinctive_targets", _distinctive_targets(package, prepared, metadata))
    prepared.setdefault("known_score_restrictions", _package_observed_restrictions(package, metadata))

    def counts_for(value):
        result = Counter()
        for row in value.get("cards", []):
            card = metadata.get(card_key(row.get("name", "")))
            quantity = row.get("quantity", 1)
            if card is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                return None
            result[card_key(card["name"])] += quantity
        return result if _validate(result, prepared["commander_keys"], metadata, prepared["pool"],
            commander_color_choices=prepared["commander_color_choices"] or None)["valid"] else None

    counts = counts_for(seed)
    if counts is None:
        return None
    anchor_id = seed.get("statistics", {}).get("anchor_deck_id")
    anchor = next((row for row in prepared["anchors"] if row["id"] == anchor_id), None)
    if anchor is None:
        return None
    statistics = seed.get("statistics", {})
    old_swaps = deepcopy(statistics.get("swap_details", []))
    core_swaps = [row for row in old_swaps if row.get("reason") == "distinctive_core"]
    failures = {target.get("identity"): row["reason_code"] for target in prepared["distinctive_targets"]
                for row in seed.get("distinctive_cards", {}).get("omitted", [])
                if target["name"] == row.get("name") and row.get("reason_code") in _CORE_REASONS}
    seed.get("statistics", {}).pop("score_bracket_repair", None)
    seed.setdefault("validation", {})["bracket"] = _bracket_validation(
        counts, metadata, prepared["target"], prepared["bracket_source"])
    if current_target is not None or original_target is not None:
        result = _repair_score_bracket(seed, package, counts, prepared, profiles, metadata, anchor=anchor,
            core_swaps=core_swaps, core_failures=failures, swaps=old_swaps,
            land_changes=statistics.get("land_adjustments", []), land_swaps=statistics.get("land_swaps", []))
        if "score_bracket_repair" in result.get("statistics", {}):
            # A fresh current check that agrees with its numeric bracket can
            # still supersede an unnecessarily high old source-anchor target.
            if not (successful_history and current_target is not None
                    and current_rating.get("bracket", 0) <= current_target
                    and result["statistics"]["score_bracket_repair"].get("status") == "blocked"):
                return result
        if not successful_history or current_target is None:
            return None
    # This branch makes no new card swaps from an expired score. It retains the
    # existing applied construction only when today's metadata and pool pass.
    target = current_target if current_target is not None else history["score_bracket"]
    counts = counts_for(current)
    if counts is None:
        return None
    observations = deepcopy(current_rating if current_target is not None else history)
    observations["bracket_restrictions"] = (current_rating.get("bracket_restrictions", {})
        if current_target is not None else history.get("observed_restrictions", {}))
    restrictions = _merge_observed_restrictions(prepared["known_score_restrictions"],
        _observed_bracket_restrictions(observations, metadata, audited_keys=counts.keys()))
    flags = _score_restriction_counts(counts, metadata, restrictions)
    checks = _score_restriction_checks(flags, target)
    if not all(checks.values()):
        # Preserve honest guidance when changed metadata invalidates an old fix.
        current["validation"]["bracket"] = _bracket_validation(
            counts, metadata, target, "previous_score_bracket_repair")
        _apply_score_bracket_checks(current, flags, checks, target)
        current["statistics"]["score_bracket_repair"] = {**history, "status": "blocked", "rule_checks": checks,
            "retention_blocked": True,
            "reason": "Current checked restrictions no longer allow the previous score-bracket repair to be retained. Fresh exact-list power inputs or an external check and a complete repair are required."}
        return current
    retained_context = _score_target_context(prepared, profiles, metadata, target, "previous_score_bracket_repair")
    retained = _assemble_generated_deck(package, counts, retained_context, metadata, anchor=anchor,
        core_swaps=core_swaps, core_failures=failures, swaps=old_swaps,
        land_changes=statistics.get("land_adjustments", []), land_swaps=statistics.get("land_swaps", []))
    _apply_score_bracket_checks(retained, flags, checks, target)
    note = ("Retained the already-applied score/bracket adjustment after checking its unchanged card list against current metadata and sampled cards. "
            "The original score is historical evidence; it is not a current rating of this list.")
    retained["statistics"]["score_bracket_repair"] = {**history, "status": "retained", "score_bracket": target,
        "initial_score_bracket": history.get("initial_score_bracket", bracket_for_power_score(history.get("from_power_level"))),
        "rule_checks": checks,
        "original_rating_status": original_rating.get("status", "unverified"), "note": note}
    retained["validation"]["bracket"]["notes"].append(note)
    retained["primer"] += "\n\nRetained score and bracket adjustment\n" + note
    for swap in history.get("swap_details", []):
        retained["primer"] += f"\nPreviously replaced [[{swap['out']}]] with [[{swap['in']}]]."
    attach_external_validation(retained)
    _mark_restriction_validation(retained)
    return retained


def _generate_package_decks(analysis, decks, base, profiles, card_metadata=None, *, prefer_playability=False,
                            _indexed_metadata=None):
    """Attach ``generated_deck`` to primary and alternative package summaries.

    ``card_metadata`` is a mapping of normalized names to authoritative card
    records. Missing metadata returns an unavailable result rather than an
    unverifiable generated list. The supplied analysis is updated and returned.
    """
    from .external_validation import cardlist_hash

    metadata = _indexed_metadata if _indexed_metadata is not None else _metadata_index(card_metadata,
        commander_alias_groups=commander_card_alias_groups(base.get("commander_name_groups", [])))
    models = [(analysis.get("packages", []),
               [{"id": row["id"], "weights": row.get("memberships", [])} for row in analysis.get("decks", [])], None)]
    models.extend((model.get("packages", []), model.get("memberships", []), model.get("components"))
                  for model in analysis.get("alternative_models", []))
    cache, source_context = {}, {}
    for packages, memberships, components in models:
        for package in packages:
            existing = package.get("generated_deck", {})
            existing_identity = cardlist_hash(existing["cards"]) if existing.get("status") == "generated" else None
            identity = (package.get("id", 0), package.get("name"),
                        tuple((row.get("name"), repr(row.get("relative_weight"))) for row in package.get("top_cards", []) if isinstance(row, dict)),
                        tuple(tag.get("tag_slug") for tag in package.get("tag_suggestions", [])),
                        tuple((row.get("id"), tuple(row.get("weights", []))) for row in memberships), existing_identity)
            if identity not in cache:
                context = {"source_context": source_context}
                seed = _repair_existing_package_deck(package.get("generated_deck"), package, memberships,
                    decks, base, profiles, metadata, context)
                cache[identity] = _generate_one(package, memberships, decks, base, profiles, metadata, _context=context)
                if seed is not None and _score_repair_priority(seed) < 2:
                    cache[identity] = seed
                if prefer_playability and cache[identity].get("status") == "generated":
                    candidates = generate_package_deck_candidates(analysis, decks, base, profiles, card_metadata,
                                     package_id=package.get("id", 0), components=components, max_candidates=8,
                                     _context=context, _indexed_metadata=metadata)
                    cache[identity] = _prefer_playability([cache[identity], *candidates])
            package["generated_deck"] = cache[identity]
    analysis["deck_generation"] = {"version": DECK_GENERATION_VERSION,
                                    "method": "Distinctive package core prioritized within constrained consensus of observed lists using learned NMF memberships",
                                    "metadata_required": True,
                                    "generated_packages": sum(package.get("generated_deck", {}).get("status") == "generated"
                                                              for package in analysis.get("packages", []))}
    return analysis


def _all_packages(analysis):
    return [*analysis.get("packages", []),
            *[package for model in analysis.get("alternative_models", []) for package in model.get("packages", [])]]


def _native_preference(option):
    return (_score_repair_priority(option), *(-value for value in _core_preference(option)),
            option["statistics"]["mana_quality"]["structural_risk_score"],
            -option["statistics"]["anchor_membership"], -option["statistics"]["weighted_consensus_score"])


def generate_package_decks(analysis, decks, base, profiles, card_metadata=None, *, prefer_playability=False):
    """Generate each package, evaluating verified pregame commander colors.

    NMF membership and metadata never change. Each legal color-setting choice
    gets its own constrained construction, and each package keeps its strongest
    distinctive core before the existing playability/mana preference is applied.
    """
    metadata = _metadata_index(card_metadata,
        commander_alias_groups=commander_card_alias_groups(base.get("commander_name_groups", [])))
    choices = _commander_color_options(base, metadata)
    if choices == [None] or "commander_color_choices" in base:
        return _generate_package_decks(analysis, decks, base, profiles, card_metadata,
            prefer_playability=prefer_playability, _indexed_metadata=metadata)
    variants = []
    for color_choices in choices:
        variant = deepcopy(analysis)
        _generate_package_decks(variant, decks, {**base, "commander_color_choices": color_choices}, profiles, card_metadata,
                               prefer_playability=prefer_playability, _indexed_metadata=metadata)
        variants.append(_all_packages(variant))
    for index, package in enumerate(_all_packages(analysis)):
        options = [variant[index]["generated_deck"] for variant in variants]
        generated = [option for option in options if option.get("status") == "generated"]
        # Exact-list external ratings are shared across legal color choices;
        # retain the best native choice when their card-list hashes coincide.
        generated.sort(key=_native_preference)
        package["generated_deck"] = (_prefer_playability(generated) if prefer_playability else min(generated, key=_native_preference)) if generated else options[0]
    analysis["deck_generation"] = {
        "version": DECK_GENERATION_VERSION,
        "method": "Distinctive package core prioritized within constrained consensus of observed lists using learned NMF memberships",
        "metadata_required": True,
        "generated_packages": sum(package["generated_deck"].get("status") == "generated" for package in analysis.get("packages", [])),
    }
    return analysis


def _core_preference(option):
    return (option.get("distinctive_cards", {}).get("included_count", 0),
            option.get("statistics", {}).get("distinctive_priority_score", 0))


def _prefer_playability(options):
    from .external_validation import attach_external_validation, cardlist_hash

    unique = {}
    for option in options:
        if option.get("status") == "generated":
            identity = cardlist_hash(option["cards"])
            if identity not in unique or _successful_score_repair(option) and not _successful_score_repair(unique[identity]):
                unique[identity] = option
    options = list(unique.values())
    # A measured bracket mismatch takes precedence over restoring an offending
    # distinctive card through an unmeasured alternate observed anchor.
    repair_priority = min(_score_repair_priority(option) for option in options)
    if repair_priority < 2:
        options = [option for option in options if _score_repair_priority(option) == repair_priority]
    strongest_core = max(_core_preference(option) for option in options)
    options = [option for option in options if _core_preference(option) == strongest_core]
    verified = []
    for option in options:
        rating = attach_external_validation(option)
        if rating.get("status") == "verified" and 72.5 <= rating.get("playability_pct", -1) <= 77.5:
            verified.append(option)
    if verified:
        chosen = max(verified, key=lambda option: (option["statistics"]["weighted_consensus_score"],
                         option["statistics"]["anchor_membership"], -option["statistics"]["mana_quality"]["structural_risk_score"]))
        reason = ("Prioritized resolving the observed score/bracket mismatch, with explained distinctive-card exceptions. "
                  if _successful_score_repair(chosen) else "Prioritized distinctive-card coverage. ")
        reason += "Selected an exact card list with a current externally observed 72.5–77.5% playability rating; preferred learned package consensus among qualifying candidates."
    else:
        chosen = min(options, key=lambda option: (option["statistics"]["mana_quality"]["structural_risk_score"],
                         -option["statistics"]["anchor_membership"], -option["statistics"]["weighted_consensus_score"]))
        reason = ("Prioritized resolving the observed score/bracket mismatch, with explained distinctive-card exceptions. "
                  if _successful_score_repair(chosen) else "Prioritized distinctive-card coverage. ")
        reason += "No qualifying candidate has a current externally observed rating in the 72.5–77.5% target; selected native mana/role structure, not a verified playability percentage."
    chosen["statistics"]["selection_reason"] = reason
    chosen["statistics"]["selection_candidate_count"] = len(options)
    return chosen


def _feedback_repairs(options, package, memberships, decks, base, profiles, metadata, context):
    from .external_validation import attach_external_validation, cardlist_hash

    rated = []
    for option in options:
        rating = attach_external_validation(option)
        if rating.get("status") != "verified":
            continue
        if 72.5 <= rating.get("playability_pct", -1) <= 77.5:
            return []
        if rating.get("playability_pct", 100) < 72.5 and option["statistics"]["land_count"] < 40:
            rated.append(option)
    if not rated:
        return []
    strongest_core = max(_core_preference(option) for option in rated)
    rated = [option for option in rated if _core_preference(option) == strongest_core]
    # Use the closest actually observed below-target list, not a guessed score.
    source = max(rated, key=lambda option: (option["validation"]["external_rating"]["playability_pct"],
                 -option["statistics"]["mana_quality"]["structural_risk_score"],
                 option["statistics"]["weighted_consensus_score"]))
    original_hash = cardlist_hash(source["cards"])
    seen = {cardlist_hash(option["cards"]) for option in options}
    repairs = []
    current_adjustment = len(source["statistics"].get("land_adjustments", []))
    for extra in range(1, 4):
        candidate = _generate_one(package, memberships, decks, base, profiles, metadata,
            anchor_id=source["statistics"]["anchor_deck_id"], land_adjustment=current_adjustment + extra,
            mana_base_optimization=bool(source["statistics"].get("land_swaps")), _context=context)
        if candidate.get("status") != "generated" or candidate["statistics"]["land_count"] <= source["statistics"]["land_count"]:
            continue
        identity = cardlist_hash(candidate["cards"])
        if identity in seen:
            continue
        seen.add(identity)
        candidate["statistics"]["playability_repair"] = {
            "status": "experimental", "from_deck_sha256": original_hash,
            "from_playability_pct": source["validation"]["external_rating"]["playability_pct"],
            "extra_lands": candidate["statistics"]["land_count"] - source["statistics"]["land_count"],
            "note": "Extra mana slots explore a dated below-target external result; this variant needs its own exact-list external check.",
        }
        candidate["primer"] += "\n\nPlayability experiment\nA previously checked list fell below the requested playability range. "
        candidate["primer"] += "This variant adds observed basic-land types in place of low-consensus cards, then checks construction rules. Its external result must be measured separately."
        attach_external_validation(candidate)
        repairs.append(candidate)
    return repairs


def _generate_package_deck_candidates(analysis, decks, base, profiles, card_metadata=None, *,
                                     package_id=0, components=None, max_candidates=8, tune_mana=True,
                                     mana_base_optimization=True, _context=None, _indexed_metadata=None):
    """Return bounded verified alternatives for review or external validation.

    Candidates use high-membership observed anchors, the same learned consensus
    swaps, and optional additions of up to three in-pool basics. No candidate is
    labelled as meeting an external playability target without an actual check.
    The supplied analysis is not changed and large candidate lists are not saved
    inside reports. ``components=None`` selects the primary model.
    """
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= 32:
        raise ValueError("max_candidates must be an integer from 1 through 32")
    if components is None:
        packages = analysis.get("packages", [])
        memberships = [{"id": row["id"], "weights": row.get("memberships", [])} for row in analysis.get("decks", [])]
    else:
        model = next((model for model in analysis.get("alternative_models", []) if model.get("components") == components), None)
        if model is None:
            return []
        packages, memberships = model.get("packages", []), model.get("memberships", [])
    package = next((package for package in packages if package.get("id") == package_id), None)
    if package is None:
        return []
    metadata = _indexed_metadata if _indexed_metadata is not None else _metadata_index(card_metadata,
        commander_alias_groups=commander_card_alias_groups(base.get("commander_name_groups", [])))
    context = {} if _context is None else _context
    ranked = sorted((row for row in memberships if len(row.get("weights", [])) > package_id and row["weights"][package_id] > 0),
                    key=lambda row: (-row["weights"][package_id], str(row["id"])))
    if not ranked:
        return []
    strongest = ranked[0]["weights"][package_id]
    anchors = [row for row in ranked if row["weights"][package_id] >= .5 * strongest][:max_candidates]
    candidates, seen = [], set()
    primary = _generate_one(package, memberships, decks, base, profiles, metadata, _context=context)
    if primary.get("status") == "generated":
        candidates.append(primary)
        seen.add(tuple((card["name"], card["quantity"]) for card in primary["cards"]))
    for row in anchors:
        candidate = _generate_one(package, memberships, decks, base, profiles, metadata, anchor_id=row["id"], _context=context)
        if candidate["status"] != "generated":
            continue
        options = [candidate]
        diagnostics = candidate["statistics"]["mana_quality"]
        adjustment = max(0, min(3, diagnostics["suggested_land_count"] - diagnostics["land_count"]))
        if tune_mana and adjustment:
            options.append(_generate_one(package, memberships, decks, base, profiles, metadata,
                                         anchor_id=row["id"], land_adjustment=adjustment, _context=context))
        if mana_base_optimization:
            options.append(_generate_one(package, memberships, decks, base, profiles, metadata,
                                         anchor_id=row["id"], land_adjustment=adjustment if tune_mana else 0,
                                         mana_base_optimization=True, _context=context))
        for option in options:
            identity = tuple((card["name"], card["quantity"]) for card in option.get("cards", []))
            if option["status"] == "generated" and identity not in seen:
                candidates.append(option)
                seen.add(identity)
    known = context.get("prepared", {}).get("known_score_restrictions")
    candidates = [option for option in candidates if _preserves_observed_candidate_restrictions(option, known)]
    candidates.sort(key=lambda option: (_score_repair_priority(option), *(-value for value in _core_preference(option)),
                                       option["statistics"]["mana_quality"]["structural_risk_score"],
                                       -option["statistics"]["anchor_membership"],
                                       -option["statistics"]["weighted_consensus_score"], option["statistics"]["anchor_deck_id"]))
    repairs = _feedback_repairs(candidates, package, memberships, decks, base, profiles, metadata, context)
    repairs = [option for option in repairs if _preserves_observed_candidate_restrictions(option, known)]
    # Preserve explicit repair experiments even if their extra lands score worse
    # under the native heuristic. Their success requires an independent rating.
    repair_slots = min(len(repairs), max(0, max_candidates - 1))
    return candidates[:max_candidates - repair_slots] + repairs[:repair_slots]


def generate_package_deck_candidates(analysis, decks, base, profiles, card_metadata=None, *,
                                     package_id=0, components=None, max_candidates=8, tune_mana=True,
                                     mana_base_optimization=True, _context=None, _indexed_metadata=None):
    """Generate bounded alternatives across verified pregame commander colors."""
    metadata = _indexed_metadata if _indexed_metadata is not None else _metadata_index(card_metadata,
        commander_alias_groups=commander_card_alias_groups(base.get("commander_name_groups", [])))
    options = _commander_color_options(base, metadata)
    arguments = {"package_id": package_id, "components": components, "max_candidates": max_candidates,
                 "tune_mana": tune_mana, "mana_base_optimization": mana_base_optimization, "_indexed_metadata": metadata}
    if options == [None] or "commander_color_choices" in base:
        return _generate_package_deck_candidates(analysis, decks, base, profiles, card_metadata,
                                                 _context=_context, **arguments)
    candidates = [candidate for choices in options for candidate in _generate_package_deck_candidates(
        analysis, decks, {**base, "commander_color_choices": choices}, profiles, card_metadata, **arguments)]
    unique = {}
    for candidate in sorted(candidates, key=_native_preference):
        identity = tuple((row["name"], row["quantity"]) for row in candidate["cards"])
        unique.setdefault(identity, candidate)
    return list(unique.values())[:max_candidates]
