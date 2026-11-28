"""Oracle-aware construction for Pantlaza's Dinosaur and discover engine."""

from collections import Counter
from dataclasses import replace
import math
import re

from ..build_styles.deckbuilding import _commander_colors, _is_land, _oracle
from ..build_styles.sources import card_key
from .catalog import card_faces
from .construction import (
    ConstructionPolicy, ConstructionSearchError, _deck_result, _pick_spells, _spell, _usable_catalog,
)
from .mana_plan import build_mana_base
from .refinement import _evaluate, _refine, land_allocation


COMMANDER = "Pantlaza, Sun-Favored"
COMMANDER_KEY = card_key(COMMANDER)
COMMANDER_KEYS = (COMMANDER_KEY,)
COMMANDER_SLUG = "pantlaza-sun-favored"
COHORT = "pantlaza"
SUMMARY = "Ramp into Pantlaza, discover from Dinosaur entries, and keep the engine going on other players’ turns."
SCORE_EXPLANATION = (
    "The search scores Dinosaur entries, useful discover hits, and ways to keep the engine active across turns."
)
MODEL_DESCRIPTION = (
    "A neural ranker learns card fit from Oracle text, card characteristics, exact creature types, "
    "and collected lists. Pantlaza's optimizer grades discover hits and Dinosaur entries, "
    "then prices the mana base against the spell core."
)

_DINO_TOKEN = re.compile(r"\bcreates?\b[^.\n]{0,130}\bdinosaur creature tokens?\b")
_DINO_SUPPORT = re.compile(
    r"\bdinosaurs?(?: spells?| cards?| creatures?)? you (?:control|cast|own)\b"
    r"|\b(?:another|each|any number of) dinosaurs?\b|\b(?:search|return|reveal)\b[^.\n]{0,65}\bdinosaur cards?\b"
)
_TRIBAL_COST = re.compile(r"\b(?:creature|dinosaur) spells (?:you cast )?costs?\b[^.\n]{0,35}\bless\b")
_TRIBAL_MANA = re.compile(r"add one mana of any color\.?[^.\n]{0,75}\bcast (?:a )?(?:dinosaur|creature) spells?\b")
_BLINK_TARGET = re.compile(
    r"\bexile\b[^.:;\n]{0,100}\b(?:creatures?|permanents?)\b[^.:;\n]{0,40}\byou control\b"
    r"|\bexile\b[^.:;\n]{0,45}\btarget (?:nontoken |nonland )?(?:creature|permanent)\b"
)
_BLINK_RETURN = re.compile(
    r"\breturn (?:that card|those cards|the exiled cards?|it|them|each card exiled this way)\b"
    r"[^.\n]{0,50}\bto the battlefield under (?:your|its owner's|their owners') control\b"
)
_CREATURE_ENTRY = re.compile(
    r"\bput\b[^.\n]{0,78}\b(?:creature|dinosaur)(?: cards?)?\b[^.\n]{0,75}\bonto the battlefield\b"
)
_DINO_RECURSION = re.compile(r"\breturn\b[^.\n]{0,60}\bdinosaur card\b[^.\n]{0,70}\bfrom your graveyard\b[^.\n]{0,40}\bto the battlefield\b")
_RAMP_SEARCH = re.compile(
    r"\bsearch your library\b[^\n]{0,150}\b(?:land|forest|plains|mountain) cards?\b[^\n]{0,100}\bonto the battlefield\b"
)
_INDEPENDENT_SWEEP = re.compile(
    r"\b(?:destroy|exile|return)\b[^.;\n]{0,20}\ball (?:other )?(?:non-dinosaur )?creatures\b"
    r"|\bdeals? (?:[4-9]|1\d) damage to each creature\b"
)
_IMMEDIATE_REMOVAL = re.compile(
    r"\b(?:destroy|exile|counter|return) (?:up to (?:one|two) )?target\b[^.;\n]{0,35}"
    r"\b(?:spell|creature|permanent|artifact|enchantment)\b"
    r"|\bfights?\b[^.;\n]{0,35}\b(?:target|creature you don.t control)\b"
)
_PROTECT = re.compile(r"\b(?:creatures?|permanents?) you control\b[^.;\n]{0,38}\b(?:hexproof|indestructible|phase out)\b")
_UTILITY = re.compile(r"\b(?:draw|create|put|search|discover|add|return|exile|copy|fight|destroy)\b")
_UNSUPPORTED = re.compile(r"\b(?:search your library for a snow land|taps? a snow land for mana|target wall)\b")
_TURN_GATE = re.compile(r"activate only (?:as a sorcery|during your turn|during your upkeep|during combat on your turn)")

_NOTES = {
    "Dinosaur": "Its entry can trigger Pantlaza, with discover set by its toughness.",
    "High-toughness Dinosaur": "Its toughness lets Pantlaza reach more expensive spells with discover.",
    "Opponents' turns": "Can put a Dinosaur onto the battlefield when a different player's turn gives Pantlaza another trigger.",
    "Blink": "Can return Pantlaza as a new object or reuse another creature's enter-the-battlefield ability.",
    "Dinosaur token": "Makes a Dinosaur that can trigger Pantlaza when it enters.",
    "Dinosaur payoff": "Rewards playing a deck with many Dinosaurs.",
    "Creature deployment": "Puts creatures onto the battlefield through spells or activated abilities.",
    "Dinosaur recursion": "Brings a Dinosaur back from your graveyard and can reuse its entry.",
    "Early ramp": "Helps reach five mana for Pantlaza.",
    "Discover 4 plays": "Has a proactive use when discovered from Pantlaza's four-toughness entry.",
    "Sweepers": "Can clear creatures through the deck's normal cast or discover paths.",
    "Hand-cast sweeper": "The board wipe requires a cast from hand.",
}
_BONUSES = {
    "Dinosaur": 1.10, "High-toughness Dinosaur": .38, "Dinosaur payoff": .74,
    "Dinosaur token": .64, "Dinosaur entry effect": .55, "Creature deployment": 1.05,
    "Blink": 1.48, "Opponents' turns": 1.08, "Discover payoff": .86, "Dinosaur recursion": .66,
    "Discover 4 plays": .33, "Early ramp": .53, "Creature protection": .62,
    "Typed ramp": .33, "Repeatable entry": .65, "Finisher": .45,
    "Early land ramp": .62, "Early universal ramp": .38, "Sweepers": .40,
}
_PRIORITY_ROLES = (
    (), ("Dinosaur", "Opponents' turns", "Blink"), ("Early ramp", "Discover 4 plays", "Removal"),
)
_ENGINE_ROLES = ("Dinosaur token", "Dinosaur payoff", "High-toughness Dinosaur", "Repeatable entry", "Dinosaur recursion",
                 "Creature deployment", "Creature protection", "Dinosaur entry effect", "Discover payoff")
_ENTRY_ROLES = ("Blink", "Opponents' turns", "Repeatable entry", "Creature deployment")
PROFILES = ((37, 11, .21), (38, 10, .14), (38, 11, .23), (39, 11, .15),
            (40, 10, .15), (40, 11, .20), (41, 10, .16))


def classify_spell(card, price_cents=0):
    """Classify effects by the actions Pantlaza can actually use."""
    baseline = _spell(card)
    front = card_faces(card)[0]
    head, _, subtype = front["type_line"].partition(" — ")
    creature = "Creature" in head.split()
    dinosaur = creature and "Dinosaur" in subtype.split()
    text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold()).replace("’", "'")
    value = card["cmc"]
    cost = front.get("mana_cost") or ""
    toughness = front.get("toughness") or ""
    toughness = int(toughness) if str(toughness).isdigit() else 0
    typed_cost = bool(_TRIBAL_COST.search(text) or _TRIBAL_MANA.search(text))
    ramp = typed_cost or bool(_RAMP_SEARCH.search(text)) or bool(
        {"Ramp", "Mana acceleration", "Fast Mana"} & baseline.roles
    )
    cheap = bool(cost) and value <= 3
    ramp_is_early = cheap and (typed_cost or _RAMP_SEARCH.search(text) or "Early ramp" in baseline.roles)
    # Requiring a large board or high power is a late ramp condition.
    ramp_is_early = ramp_is_early and not re.search(
        r"seven or more lands|power (?:is )?4 or greater|only if you control a legendary|is dealt damage|whenever [^.\n]+ attacks", text,
    )
    ramp_is_early = ramp_is_early and not (value >= 3 and "artifact" in head.casefold() and "enters tapped" in text)

    roles = set()
    if dinosaur:
        roles.add("Dinosaur")
        if toughness >= 5:
            roles.add("High-toughness Dinosaur")
        self_name = re.escape(front.get("name", card["name"]).casefold().split(",", 1)[0])
        if (re.search(rf"\bwhen(?:ever)? (?:this creature|{self_name})(?: or [^.\n]{{0,40}})? enters\b", text)
                and _UTILITY.search(text)):
            roles.add("Dinosaur entry effect")
    if _DINO_TOKEN.search(text):
        roles.add("Dinosaur token")
    chosen_type_trigger = "choose a creature type" in text and bool(re.search(
        r"triggered ability of another creature you control of the chosen type", text,
    ))
    if _DINO_SUPPORT.search(text) or typed_cost or chosen_type_trigger:
        roles.add("Dinosaur payoff")
    if ramp:
        roles.add("Mana acceleration")
    if ramp_is_early:
        roles.add("Early ramp")
        if not typed_cost:
            roles.add("Early universal ramp")
        if _RAMP_SEARCH.search(text):
            roles.add("Early land ramp")
    if typed_cost:
        roles.add("Typed ramp")

    blink = bool(_BLINK_TARGET.search(text) and _BLINK_RETURN.search(text)) and not re.search(
        r"mana value (?:[0-4]|less than 5)\b", text,
    )
    flash_entries = bool(re.search(
        r"\b(?:dinosaur|creature) spells?[^.\n]{0,70}(?:as though (?:they|it) had flash|have flash)\b", text,
    ))
    deployment = bool(_CREATURE_ENTRY.search(text) and not blink and "your graveyard" not in text.split("onto the battlefield")[0])
    if blink:
        roles.add("Blink")
    if deployment:
        roles.add("Creature deployment")
    if _DINO_RECURSION.search(text):
        roles.add("Dinosaur recursion")
    activated_entry = any(":" in line and (_BLINK_TARGET.search(line) or _CREATURE_ENTRY.search(line))
                          for line in text.splitlines())
    if ((blink or deployment) and (activated_entry and not _TURN_GATE.search(text) or "Instant" in head)
            or "flash" in card.get("keywords", ()) and dinosaur or flash_entries):
        roles.add("Opponents' turns")
    repeatable_trigger = bool(re.search(r"\bat the beginning of (?:each|your) end step\b|\bwhenever\b[^.\n]*attacks\b", text))
    if ((blink or deployment) and (activated_entry or repeatable_trigger)
            or repeatable_trigger and "Dinosaur token" in roles):
        roles.add("Repeatable entry")
    if _PROTECT.search(text) or blink and "Instant" in head:
        roles.add("Creature protection")
    if re.search(r"\b(?:whenever|when) you discover\b|\bdiscover \d+\b", text):
        roles.add("Discover payoff")
    if _INDEPENDENT_SWEEP.search(text) and not re.search(r"\bblocked by target wall\b|\btarget wall\b", text):
        cast_from_hand = bool(re.search(r"if you cast (?:it|this \w+) from your hand", text))
        roles.add("Hand-cast sweeper" if cast_from_hand else "Sweepers")
    if ((_IMMEDIATE_REMOVAL.search(text) or "damage to target creature" in text) and not blink
            and not re.search(r"\bwhen(?:ever)?\b[^.\n]{0,40}\bdies\b", text)):
        roles.add("Removal")
        if value <= 4:
            roles.add("Early interaction")
    if ("Card Advantage" in baseline.roles and (
        re.search(r"\bdraw\b|\bmay (?:play|cast)\b|into your hand\b", text)
    )) or "Discover payoff" in roles:
        roles.add("Card advantage")
    if re.search(r"additional combat phase|damage to each opponent|creatures you control gain (?:double strike|trample)", text):
        roles.add("Finisher")

    reactive = bool(roles & {"Sweepers", "Removal", "Creature protection"}) and not creature and not re.search(
        r"\bchoose (?:one|two)|\bcreate\b", text,
    )
    gated = "{X}" in cost or "as an additional cost" in text or "cast this spell only" in text or reactive
    if value <= 4 and not gated and (creature or roles & {"Mana acceleration", "Creature deployment", "Card advantage", "Dinosaur token"}
                                     or _UTILITY.search(text)) and "Blink" not in roles:
        roles.add("Discover 4 plays")
    # An eight-mana card that only wipes via a hand cast loses that credit on discover.
    efficiency = .23 * (2 <= value <= 4 and dinosaur) + .14 * ramp_is_early
    efficiency -= .13 * math.log1p(price_cents / 350)
    efficiency -= .28 * ("Hand-cast sweeper" in roles)
    efficiency -= .14 * (value >= 7 and not (roles & {"Dinosaur entry effect", "Finisher"}))
    efficiency -= .38 * (value >= 3 and ramp and "Early ramp" not in roles and not dinosaur)
    efficiency -= .38 * max(0, max((cost.count("{" + color + "}") for color in "GRW"), default=0) - 2)
    if dinosaur and re.search(r"whenever a player plays a land[^.\n]*return this creature to its owner's hand", text):
        efficiency -= 1.65
    signals = tuple(role for role in (*_NOTES, *_ENGINE_ROLES) if role in roles)
    return replace(baseline, roles=frozenset(roles or {"Support"}), signals=tuple(dict.fromkeys(signals)),
                   expensive=value >= 7, efficiency=efficiency)


def _strategy_bonus(roles):
    entries = .3 * roles["Dinosaur"] + .65 * roles["Dinosaur token"] + .9 * roles["Repeatable entry"]
    discover_hits = roles["Discover 4 plays"]
    off_turns = roles["Opponents' turns"]
    return (1.75 * math.log1p(entries * (roles["High-toughness Dinosaur"] + .4 * discover_hits) / 12)
            + 1.85 * math.log1p(entries * off_turns / 12)
            + 1.30 * math.log1p(roles["Early ramp"] / 3) + .65 * math.log1p(discover_hits / 8))


def _fit(spell, model_score, roles, *, emphasis=1.0):
    bonuses = {role: bonus / (1 + (.28 if role in _ENTRY_ROLES else .12) * roles.get(role, 0))
               for role, bonus in _BONUSES.items() if role in spell.roles}
    entries = [bonuses.pop(role) for role in _ENTRY_ROLES if role in bonuses]
    entry_fit = .25 * sum(entries) + .75 * max(entries, default=0)
    return .66 * model_score + spell.efficiency + emphasis * (sum(bonuses.values()) + entry_fit)


def build_candidates(catalog, rank, prices, *, profiles=None):
    """Search spell packages, then vary the land spend around their actual cost."""
    commanders = (COMMANDER_KEY,)
    colors, _ = _commander_colors(commanders, catalog)
    legal = _usable_catalog(catalog, colors)
    # Keep an affordable base for this trial. Near-parity is evaluated against
    # the cost of the built spell core.
    metadata = {key: card for key, card in legal.items() if key in prices and key not in {"savannah", "plateau", "taiga"}}
    spells = {key: classify_spell(card, prices[key]) for key, card in metadata.items()
              if key != COMMANDER_KEY and not _is_land(card) and not _UNSUPPORTED.search(_oracle(card).casefold())}
    if COMMANDER_KEY not in metadata:
        raise ConstructionSearchError("Pantlaza has no matching legal stocked printing")
    if profiles is None:
        profiles = PROFILES
    heavy = {key: max(0, metadata[key]["cmc"] - 4) for key in spells}
    for land_count, ramp_goal, curve_penalty in profiles:
        goals = {"Early ramp": ramp_goal, "Early land ramp": 3, "Early universal ramp": 7,
                 "Dinosaur": 28, "Early interaction": 4, "Sweepers": 1,
                 "Discover 4 plays": 19, "Opponents' turns": 3, "Blink": 2, "Card advantage": 4,
                 "Creature protection": 2}
        limits = {"Mana acceleration": ramp_goal + 4}
        policy = ConstructionPolicy(goals, limits, classify_spell, _fit, _NOTES, SCORE_EXPLANATION)
        candidates = {key: replace(spell, efficiency=spell.efficiency - curve_penalty * heavy[key])
                      if heavy[key] else spell for key, spell in spells.items()}
        starts, failures = [], []
        for start in range(3):
            try:
                selected = _pick_spells(candidates, rank, start, policy, spell_slots=99 - land_count,
                                        priority_roles=_PRIORITY_ROLES)
            except ConstructionSearchError as error:
                failures.append({"start": start + 1, "reason": str(error)})
            else:
                starts.append(_evaluate(selected, candidates, rank, policy, strategy_bonus=_strategy_bonus))
        if not starts:
            print(f"{land_count} lands / {ramp_goal} early ramp: {failures}", flush=True)
            continue
        best = max(starts, key=lambda row: row[0])
        improvements = 0
        for _ in range(3):
            updated = _refine(best, candidates, rank, policy, strategy_bonus=_strategy_bonus, extra_roles=_ENGINE_ROLES)
            if updated is best:
                break
            improvements += 1
            best = updated
        fit, selected, scores, coverage = best
        nonlands = sum(prices[key] for key in (COMMANDER_KEY, *selected))
        for ratio, pressure in ((.9, .25), (1.0, .19), (1.05, .12)):
            cap = land_allocation(nonlands, ratio, basic_floor=0)
            try:
                lands, general, tribal = build_mana_base(metadata, prices, colors, "Dinosaur",
                                                          budget=cap, count=land_count, price_pressure=pressure)
            except ConstructionSearchError as error:
                print(f"{land_count} lands / {ramp_goal} early ramp / {ratio:g} allocation: {error}", flush=True)
                continue
            deck = _deck_result(metadata, commanders, lands, general, candidates, selected, scores, fit, coverage,
                                policy, 3, [round(row[0], 4) for row in starts], failures, improvements, colors)
            deck["construction"]["spell_role_counts"].update({role: coverage[role] for role in _ENGINE_ROLES})
            deck["construction"]["land_allocation"] = {
                "share_of_nonland_price": ratio, "estimated_cents": cap, "nonland_estimated_cents": nonlands,
            }
            deck["mana_diagnostics"]["dinosaur_cast_sources_adjusted"] = tribal
            yield deck


def game_plan(deck):
    names = {card["name"] for card in deck["cards"]}
    stages = [
        ("Cast Pantlaza early",
         "Open on green mana for land ramp. Casting Pantlaza triggers discover 4 from its own entry, so it starts the engine even on an empty board.",
         ("Nature's Lore", "Three Visits", "Farseek", "Otepec Huntmaster", "Drover of the Mighty")),
        ("Choose which Dinosaur to discover from",
         "Discover uses the entering Dinosaur's toughness. You can decline a small discover if a bigger Dinosaur is about to enter. Token makers and creatures with their own entry effects help build a board.",
         ("Regisaur Alpha", "Trumpeting Carnosaur", "Palani's Hatcher", "Gishath, Sun's Avatar",
          "Ghalta, Stampede Tyrant", "Vaultborn Tyrant")),
        ("Keep the engine running across turns",
         "Save an instant blink to protect Pantlaza or return it as a fresh creature with its own discover. Putting another Dinosaur into play on a different player's turn can also use that turn's discover.",
         ("Ephemerate", "Elvish Piper", "Vivien's Arkbow", "Cloudshift", "Teleportation Circle")),
    ]
    result = []
    for title, description, examples in stages:
        present = [name for name in examples if name in names]
        if present:
            description += " Look for " + ", ".join(present[:3]) + "."
        result.append({"title": title, "description": description})
    return result
