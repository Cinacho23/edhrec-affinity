"""Build reproducible package decks from observed lists and NMF memberships.

This is constrained, membership-weighted consensus, not another trained model.
An observed, verified 100-card deck supplies the mana base and role/curve shape;
one-for-one swaps improve consensus without inventing an unrelated top-99 list.
Commander construction checks follow the September 2026 Comprehensive Rules,
903.3, 903.5 and 702.124:
https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt
"""

from __future__ import annotations

from collections import Counter
from functools import wraps
import math
import re
from urllib.parse import quote_plus

from .card_metadata import metadata_aliases
from .sources import card_key, commander_card_alias_groups


ROLES = ("Commander", "Lands", "Fast Mana", "Ramp", "Card Advantage", "Wipe",
         "Removal", "Tutor", "Recursion", "Protection", "Leftover")
_BASIC_COLORS = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G"}
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
                     or re.search(r"return all [^.\n]*\blands\b[^.\n]*hands", text))
    return bool(card.get("game_changer")), extra, mass_land


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


def _bracket_validation(counts, metadata, target, source):
    flags = [(quantity, _bracket_flags(metadata[key])) for key, quantity in counts.items()]
    changers = sum(quantity for quantity, flag in flags if flag[0])
    extra = any(flag[1] for _, flag in flags)
    denial = any(flag[2] for _, flag in flags)
    limit = 0 if target <= 2 else 3 if target == 3 else None
    checks = {"game_changers": limit is None or changers <= limit,
              "extra_turns": target >= 4 or not extra,
              "mass_land_denial": target >= 4 or not denial}
    recommended = max(target, 4) if not all(checks.values()) else target
    notes = ["Exploratory bracket guidance; speed, tutors, combos and all play patterns are not fully evaluated."]
    if source == "default":
        notes.append("No usable source-deck bracket annotations; the construction target defaults to bracket 3.")
    if any(not isinstance(metadata[key].get("game_changer"), bool) for key in counts):
        notes.append("Game Changer annotations are incomplete; the count can be understated.")
    if recommended != target:
        notes.append("Observed cards exceed the target's checked restrictions, so the recommendation is raised to bracket 4 or above.")
    return {"status": "estimated", "target": target, "recommended": recommended, "source": source,
            "game_changer_count": changers, "game_changer_limit": limit, "rule_checks": checks,
            "extra_turns_present": extra, "mass_land_denial_present": denial, "notes": notes}


def _validate(counts, commander_keys, metadata, pool):
    complete = all(_complete_metadata(metadata.get(key)) for key in counts)
    commanders = [metadata[key] for key in commander_keys if _complete_metadata(metadata.get(key))]
    colors = {color for card in commanders for color in card["color_identity"]}
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
        "color_identity": complete and all(set(metadata[key]["color_identity"]) <= colors and
            (not _is_land(metadata[key]) or all(color in colors for land, color in _BASIC_COLORS.items()
             if land in metadata[key]["type_line"].casefold().split())) for key in counts),
        "singleton": complete and all(0 < quantity and identities[metadata[key].get("oracle_id") or key] <= _copy_limit(metadata[key])
                                      for key, quantity in counts.items()),
        "commander_legality": complete and all(metadata[key]["legalities"].get("commander") == "legal" for key in counts),
        "sampled_pool": all(key in pool for key in counts),
    }
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


def _source_targets(counts, metadata, commander_keys):
    colors = {color for key in commander_keys for color in metadata[key]["color_identity"]}
    maximum_pips = {color: 1 for color in colors}
    for key in counts:
        card = metadata[key]
        if not _is_land(card) and card.get("cmc", 0) <= 5:
            pips = Counter(re.findall(r"\{([WUBRG])\}", card.get("mana_cost", "")))
            for color in colors:
                maximum_pips[color] = max(maximum_pips[color], pips[color])
    return {color: min(30, 21 + 4 * maximum_pips[color]) for color in sorted(colors)}


def _mana_diagnostics(counts, metadata, commander_keys):
    spells = [(metadata[key], quantity) for key, quantity in counts.items() if key not in commander_keys and not _is_land(metadata[key])]
    lands = sum(quantity for key, quantity in counts.items() if _is_land(metadata[key]))
    total_spells = sum(quantity for _, quantity in spells)
    average_cmc = sum(card.get("cmc", 0) * quantity for card, quantity in spells) / max(1, total_spells)
    role_counts = Counter(role for card, quantity in spells for role in functional_tags(card) for _ in range(quantity))
    target = max(34, min(40, round(37 + 2 * (average_cmc - 3) - .15 * role_counts["Ramp"])))
    commander_colors = {color for key in commander_keys for color in metadata[key]["color_identity"]}
    targets = _source_targets(counts, metadata, commander_keys)
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


def _upgrade_land_base(counts, pool, metadata, commander_keys, support, protected, target, bracket_source):
    colors = {color for key in commander_keys for color in metadata[key]["color_identity"]}
    incoming_pool = [key for key in pool if key not in commander_keys and _complete_metadata(metadata.get(key))
                     and _is_land(metadata[key]) and metadata[key]["legalities"].get("commander") == "legal"
                     and set(metadata[key]["color_identity"]) <= colors]
    swaps = []
    for _ in range(18):
        diagnostics = _mana_diagnostics(counts, metadata, commander_keys)
        sources, targets = diagnostics["colored_land_sources"], diagnostics["colored_source_targets"]
        available = [key for key in incoming_pool if key not in counts]
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
        current_bracket = _bracket_validation(counts, metadata, target, bracket_source)
        for _, _, incoming, outgoing in sorted(choices, key=lambda row: (-row[0], -row[1], row[2], row[3])):
            candidate = counts.copy()
            candidate[outgoing] -= 1
            if candidate[outgoing] == 0:
                del candidate[outgoing]
            candidate[incoming] = 1
            next_bracket = _bracket_validation(candidate, metadata, target, bracket_source)
            if any(not passed and current_bracket["rule_checks"].get(check, True)
                   for check, passed in next_bracket["rule_checks"].items()):
                continue
            if _mana_diagnostics(candidate, metadata, commander_keys)["mana_base_risk"] >= diagnostics["mana_base_risk"] - 1e-9:
                continue
            counts = candidate
            swaps.append({"out": metadata[outgoing]["name"], "in": metadata[incoming]["name"]})
            changed = True
            break
        if not changed:
            break
    return counts, swaps


def _primer(name, commander_rows, rows, package, statistics, bracket):
    themes = ", ".join(tag["tag_name"] for tag in package.get("tag_suggestions", []))
    references = lambda cards: ", ".join("[[" + card["name"] + "]]" for card in cards)
    title = references(commander_rows) + " — " + package.get("name", "Package")
    lines = [title, "", "Plan", f"Build around {references(commander_rows)}."]
    if themes:
        lines.append(f"The learned package most closely matches these EDHREC profiles: {themes}.")
    defining = sorted((row for row in rows if row["primary_role"] not in {"Commander", "Lands"}),
                      key=lambda row: (-row["member_inclusion"], row["name"]))[:5]
    if defining:
        lines.append(f"Recurring cards in this package include {references(defining)}; prioritize hands that can cast and support these cards.")
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
              f"{statistics['swaps']} role- and curve-preserving swaps improve the package-weighted consensus score. "
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
    return {"status": "unavailable", "reason": reason, "cards": [], "commanders": [],
            "card_count": 0, "export_text": "", "primer": "",
            "validation": {"valid": False, "checks": {}, "errors": [reason]}, **extra}


def _prepare_package(package, model_decks, source_decks, base, profiles, metadata):
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
    package_id = package.get("id", 0)
    weights = {row["id"]: float(row["weights"][package_id]) for row in model_decks
               if isinstance(row.get("weights"), list) and len(row["weights"]) > package_id
               and isinstance(row["weights"][package_id], (float, int)) and math.isfinite(row["weights"][package_id])
               and row["weights"][package_id] > 0}
    if not weights:
        return _unavailable("No positive learned memberships are available for this package.")
    pool, observed, by_id = set(commander_keys), [], {}
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
        by_id.setdefault(deck["id"], record)
    learned = [deck for deck in observed if deck.get("id") in weights]
    total_weight = sum(weights[deck["id"]] for deck in learned)
    if total_weight <= 0:
        return _unavailable("Learned deck memberships cannot be joined to the observed cardlists.")
    support = {key: sum(weights[deck["id"]] for deck in learned if key in deck["counts"]) / total_weight for key in pool}
    target, bracket_source = _bracket_target(observed, weights)
    suggested = {tag.get("tag_slug") for tag in package.get("tag_suggestions", [])}
    metric_profiles = [profile for profile in profiles if
                       (profile.get("tag_slug") in suggested and profile.get("bracket") is None)
                       or profile.get("bracket") == target]
    metrics = {key: _profile_metrics(key, metric_profiles, metadata) for key in pool}
    anchors = [deck for deck in learned if _validate(deck["counts"], commander_keys, metadata, pool)["valid"]]
    if not anchors:
        missing = sorted({key for deck in learned for key in deck["counts"] if not _complete_metadata(metadata.get(key))})
        return _unavailable("No sampled package deck passes the 100-card, commander, color, singleton and legality checks.",
                            missing_metadata_count=len(missing))
    colors = {color for key in commander_keys for color in metadata[key]["color_identity"]}
    eligible = [key for key in pool if key not in commander_keys and _complete_metadata(metadata.get(key))
                and metadata[key]["legalities"].get("commander") == "legal" and not _is_land(metadata[key])
                and set(metadata[key]["color_identity"]) <= colors and support.get(key, 0) > 0]
    signatures = {key: (tuple(functional_tags(metadata[key])), _type_bucket(metadata[key]), _curve_bucket(metadata[key]))
                  for key in pool if _complete_metadata(metadata.get(key))}
    return {"commander_keys": commander_keys, "package_id": package_id, "weights": weights,
            "pool": pool, "support": support, "target": target, "bracket_source": bracket_source,
            "suggested": suggested, "metric_profiles": metric_profiles, "metrics": metrics,
            "anchors": anchors, "colors": colors, "eligible": eligible, "signatures": signatures}


def _generate_one(package, model_decks, source_decks, base, profiles, metadata, *, anchor_id=None, land_adjustment=0,
                  mana_base_optimization=False, _context=None):
    prepared = _context.get("prepared") if _context is not None else None
    if prepared is None:
        prepared = _prepare_package(package, model_decks, source_decks, base, profiles, metadata)
        if _context is not None:
            _context["prepared"] = prepared
    if prepared.get("status") == "unavailable":
        return prepared
    commander_keys, package_id, weights = prepared["commander_keys"], prepared["package_id"], prepared["weights"]
    pool, support = prepared["pool"], prepared["support"]
    target, bracket_source = prepared["target"], prepared["bracket_source"]
    suggested, metric_profiles, metrics = prepared["suggested"], prepared["metric_profiles"], prepared["metrics"]
    anchors, colors, eligible, signatures = prepared["anchors"], prepared["colors"], prepared["eligible"], prepared["signatures"]
    def bracket_penalty(deck):
        result = _bracket_validation(deck["counts"], metadata, target, bracket_source)
        return sum(not valid for valid in result["rule_checks"].values())
    if anchor_id is not None:
        anchors = [deck for deck in anchors if deck["id"] == anchor_id]
        if not anchors:
            return _unavailable("The requested anchor does not pass the observed 100-card deck checks.")
    anchor = min(anchors, key=lambda deck: (bracket_penalty(deck), -weights[deck["id"]],
                 -sum(support.get(key, 0) for key in deck["counts"] if key not in commander_keys), str(deck["id"])))
    counts = anchor["counts"].copy()
    rank = lambda key: (support.get(key, 0), metrics[key]["average_inclusion"] or 0,
                        metrics[key]["average_lift"] or 0)
    swaps = []
    protected = {card_key(metadata[card_key(card["name"])]["name"])
                 for card in package.get("top_cards", []) if card_key(card.get("name", "")) in metadata}
    # Land identities/copies, repeated exceptions and the command zone remain
    # fixed. For spells, conserve all functional roles, type and curve bucket.
    for incoming in sorted(eligible, key=lambda key: (*(-value for value in rank(key)), key)):
        if incoming in counts:
            continue
        signature = signatures[incoming]
        outgoing = [key for key in counts if key not in commander_keys and key not in protected and counts[key] == 1
                    and not _is_land(metadata[key]) and _copy_limit(metadata[key]) == 1
                    and signatures[key] == signature
                    and rank(incoming) > rank(key)]
        if not outgoing:
            continue
        remove = min(outgoing, key=lambda key: (rank(key), key))
        candidate = counts.copy()
        del candidate[remove]
        candidate[incoming] = 1
        current_bracket = _bracket_validation(counts, metadata, target, bracket_source)
        next_bracket = _bracket_validation(candidate, metadata, target, bracket_source)
        if (sum(not value for value in next_bracket["rule_checks"].values()) >
                sum(not value for value in current_bracket["rule_checks"].values())):
            continue
        counts = candidate
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
        diagnostics = _mana_diagnostics(counts, metadata, commander_keys)
        add = min(basics, key=lambda key: (-max((demand[color] / max(1, diagnostics["colored_land_sources"].get(color, 0))
                                              for color in metadata[key]["color_identity"]), default=0), key))
        remove = min(removable, key=lambda key: (rank(key), -metadata[key].get("cmc", 0), key))
        del counts[remove]
        counts[add] += 1
        land_changes.append({"out": metadata[remove]["name"], "in": metadata[add]["name"]})
    land_swaps = []
    if mana_base_optimization:
        counts, land_swaps = _upgrade_land_base(counts, pool, metadata, commander_keys, support, protected, target, bracket_source)
    validation = _validate(counts, commander_keys, metadata, pool)
    if not validation["valid"]:
        return _unavailable("Construction did not pass the final deck checks.")
    bracket = _bracket_validation(counts, metadata, target, bracket_source)
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
        "land_adjustments": land_changes, "land_swaps": land_swaps, "anchor_membership": weights[anchor["id"]],
        "mana_quality": _mana_diagnostics(counts, metadata, commander_keys),
        "weighted_consensus_score": sum(row["member_inclusion"] for row in nonland) / max(1, len(nonland)),
        "average_inclusion": sum(row["average_inclusion"] for row in nonland) / len(nonland) if metric_profiles and nonland else None,
        "average_lift": sum(row["average_lift"] for row in nonland) / len(nonland) if metric_profiles and nonland else None,
        "profile_count": len(metric_profiles), "profile_slugs": [profile.get("tag_slug") for profile in metric_profiles],
        "profile_names": [profile.get("tag_name") or profile.get("tag_slug", "") for profile in metric_profiles],
        "target_bracket": target, "recommended_bracket": bracket["recommended"],
        "method": "NMF-membership-weighted observed card inclusion; verified 100-card anchor; role/type/curve-preserving consensus swaps",
        "metric_note": "Unweighted mean across the package's matched theme pages and target bracket page, excluding the general page; missing measurements contribute zero. Lift is the raw EDHREC ratio, not a percentage-point difference. Coverage counts show observed measurements.",
    }
    measured = [row for row in rows if row["lift_observed_profiles"] > 0 and row["primary_role"] != "Commander"]
    highest_lift = max(measured, key=lambda row: (row["average_lift"], row["average_inclusion"], row["name"]), default=None)
    statistics["highest_average_lift"] = {"name": highest_lift["name"], "lift": highest_lift["average_lift"]} if highest_lift else None
    export = "\n".join(f"{row['quantity']} {row['name']}" +
                       (" *CMDR*" if row["primary_role"] == "Commander" else "") for row in rows) + "\n"
    plain = "~".join(f"{row['quantity']} {row['name']}" for row in rows) + "~Z~"
    validation.update({"bracket": bracket, "external_url": "https://edhpowerlevel.com?d=" + quote_plus(plain),
                       "scope": "Construction rules checked with supplied card metadata; not playtested or certified for a bracket."})
    return {"status": "generated", "name": name, "commanders": commander_rows, "cards": rows,
            "card_count": sum(counts.values()), "export_text": export,
            "primer": _primer(name, commander_rows, rows, package, statistics, bracket),
            "validation": validation, "statistics": statistics}


def generate_package_decks(analysis, decks, base, profiles, card_metadata=None, *, prefer_playability=False):
    """Attach ``generated_deck`` to primary and alternative package summaries.

    ``card_metadata`` is a mapping of normalized names to authoritative card
    records. Missing metadata returns an unavailable result rather than an
    unverifiable generated list. The supplied analysis is updated and returned.
    """
    metadata = _metadata_index(card_metadata,
                               commander_alias_groups=commander_card_alias_groups(base.get("commander_name_groups", [])))
    models = [(analysis.get("packages", []),
               [{"id": row["id"], "weights": row.get("memberships", [])} for row in analysis.get("decks", [])], None)]
    models.extend((model.get("packages", []), model.get("memberships", []), model.get("components"))
                  for model in analysis.get("alternative_models", []))
    cache = {}
    for packages, memberships, components in models:
        for package in packages:
            identity = (package.get("id", 0), tuple((row.get("id"), tuple(row.get("weights", []))) for row in memberships))
            if identity not in cache:
                context = {}
                cache[identity] = _generate_one(package, memberships, decks, base, profiles, metadata, _context=context)
                if prefer_playability and cache[identity].get("status") == "generated":
                    candidates = generate_package_deck_candidates(analysis, decks, base, profiles, card_metadata,
                                     package_id=package.get("id", 0), components=components, max_candidates=8,
                                     _context=context, _indexed_metadata=metadata)
                    cache[identity] = _prefer_playability([cache[identity], *candidates])
            package["generated_deck"] = cache[identity]
    analysis["deck_generation"] = {"method": "Constrained consensus of observed lists using learned NMF memberships",
                                    "metadata_required": True,
                                    "generated_packages": sum(package.get("generated_deck", {}).get("status") == "generated"
                                                              for package in analysis.get("packages", []))}
    return analysis


def _prefer_playability(options):
    from .external_validation import attach_external_validation, cardlist_hash

    unique = {}
    for option in options:
        if option.get("status") == "generated":
            unique.setdefault(cardlist_hash(option["cards"]), option)
    options = list(unique.values())
    verified = []
    for option in options:
        rating = attach_external_validation(option)
        if rating.get("status") == "verified" and 72.5 <= rating.get("playability_pct", -1) <= 77.5:
            verified.append(option)
    if verified:
        chosen = max(verified, key=lambda option: (option["statistics"]["weighted_consensus_score"],
                         option["statistics"]["anchor_membership"], -option["statistics"]["mana_quality"]["structural_risk_score"]))
        reason = "Selected an exact card list with a current externally observed 72.5–77.5% playability rating; preferred learned package consensus among qualifying candidates."
    else:
        chosen = min(options, key=lambda option: (option["statistics"]["mana_quality"]["structural_risk_score"],
                         -option["statistics"]["anchor_membership"], -option["statistics"]["weighted_consensus_score"]))
        reason = "No reviewed candidate has a current externally observed rating in the 72.5–77.5% target; selected native mana/role structure, not a verified playability percentage."
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


def generate_package_deck_candidates(analysis, decks, base, profiles, card_metadata=None, *,
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
    candidates.sort(key=lambda option: (option["statistics"]["mana_quality"]["structural_risk_score"],
                                       -option["statistics"]["anchor_membership"],
                                       -option["statistics"]["weighted_consensus_score"], option["statistics"]["anchor_deck_id"]))
    repairs = _feedback_repairs(candidates, package, memberships, decks, base, profiles, metadata, context)
    # Preserve explicit repair experiments even if their extra lands score worse
    # under the native heuristic. Their success requires an independent rating.
    repair_slots = min(len(repairs), max(0, max_candidates - 1))
    return candidates[:max_candidates - repair_slots] + repairs[:repair_slots]
