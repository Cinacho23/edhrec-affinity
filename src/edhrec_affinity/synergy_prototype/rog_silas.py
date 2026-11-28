"""Build the Rograkh and Silas artifact engine from their actual cast paths."""

from dataclasses import replace
import math
import re

from ..build_styles.deckbuilding import _commander_colors, _is_land
from ..build_styles.sources import card_key
from .construction import ConstructionPolicy, ConstructionSearchError, _deck_result, _pick_spells, _spell, _usable_catalog
from .mana_plan import build_mana_base
from .refinement import _evaluate, _refine, land_allocation
from .yshtola import spell_modes


SILAS = "Silas Renn, Seeker Adept"
ROGRAKH = "Rograkh, Son of Rohgahh"
COMMANDER = f"{SILAS} + {ROGRAKH}"
COMMANDER_KEYS = (card_key(SILAS), card_key(ROGRAKH))
COMMANDER_SLUG = "rograkh-son-of-rohgahh-silas-renn-seeker-adept"
COHORT = "rog-silas"
SUMMARY = "Put Rograkh to work early, get Silas through blockers, and replay artifacts to build a board that can close the game."
MODEL_DESCRIPTION = (
    "A neural ranker learns card fit from Oracle text, card characteristics, and collected lists. "
    "The pair’s optimizer checks what Rograkh can pay for, which artifacts Silas can replay, and what helps Silas connect."
)
PROFILES = ((29, 8, .10), (30, 8, .10), (31, 8, .09), (32, 8, .08), (33, 8, .08),
            (34, 8, .08), (35, 9, .08), (36, 9, .08))

_PRIORITIES = ((), ("Rog outlets", "Silas evasion", "Cheap artifacts"), ("Early ramp", "Interaction", "Card advantage"))
_ORIGINAL_DUALS = {"badlands", "underground sea", "volcanic island"}
_MANA_TAP = re.compile(r"^(?P<cost>(?:\{1\}, )?\{t\}[^\n:]*): *(?P<effect>add [^\n]+)", re.M)
_NET_ADD = re.compile(r"add (?:two mana|three mana|\{[wubrgc]\}\{[wubrgc]\})")
_OWN_CREATURE = re.compile(r"\bsacrifice (?:another |a |one )?(?:nontoken )?(?:red |legendary )?creature\b"
                           r"|\bsacrifice one or more creatures\b"
                           r"|\bsacrifice (?:an? )?artifact or (?:a )?creature\b")
_ROG_RESTRICTION = re.compile(r"sacrifice a creature with|equal to (?:that creature's|the sacrificed creature's|its) power"
                              r"|equal to the mana value of the sacrificed (?:creature|permanent)")
_OWN_ARTIFACT = re.compile(r"\bsacrifice (?:another |an? |one |two |x )?(?:nonland |nontoken )?artifacts?\b"
                           r"|\bsacrifice (?:another |a )?creature or (?:an )?artifact\b")
_SAC_ACTION = re.compile(r"\b(?:draw|create|add|deals?|search|scry|surveil|destroy|return|exile)\b")
_DRAW = re.compile(
    r"\b(?:draws? (?:two|three|four|[2-9]|x|that many) cards?|draws? cards? equal to|"
    r"(?:whenever|at the beginning of)[^.\n]{0,120}\b(?:you may )?draw a card)\b"
)
_CANTRIP = re.compile(r"\b(?:draw a card|exile the top (?:card|two cards) of your library|scry \d|surveil \d)\b")
_RECURSION = re.compile(
    r"\b(?:cast|return|put)\b[^.\n]{0,110}\b(?:artifact cards?|artifact creature cards?)\b[^.\n]{0,85}"
    r"\bfrom your graveyard\b|\bchoose target artifact card in your graveyard\b[^\n]{0,90}\b(?:cast|return)\b"
    r"|\btarget artifact card in that player's graveyard\b[^\n]{0,165}\breturns?\b"
)
_EVASION = re.compile(
    r"\b(?:equipped creature|enchanted creature|target (?:artifact )?creature|creatures you control|creature you control)\b"
    r"[^.\n]{0,75}\b(?:can.t be blocked(?! by)|unblockable|(?:has|have|gains?) (?:shadow|flying)|landwalk)\b"
)
_PROTECTION = re.compile(
    r"\b(?:equipped creature|target (?:artifact )?creature|creatures you control)\b"
    r"[^.\n]{0,65}\b(?:hexproof|shroud|indestructible|protection from|ward|phases? out)\b"
)
_INTERACTION = re.compile(
    r"\b(?:destroy|exile|return|counter) (?:up to \w+ )?target\b[^.\n]{0,45}"
    r"\b(?:spells?|creatures?|artifacts?|enchantments?|permanents?|planeswalkers?|abilit(?:y|ies))\b"
    r"|\btarget creature gets -[x\d]+/-[x\d]+\b"
    r"|\bdeals? [2-9] damage to (?:any target|target creature)\b"
)
_NONINTERACTION = re.compile(r"\btarget creature you control\b|\bfrom (?:your |a |that player's )?graveyard\b"
                             r"|\b(?:when|whenever)[^.\n]{0,65}\bdies\b")
_WIPE = re.compile(r"\b(?:destroy|exile|return) all (?:other |nonartifact |nonland )?(?:creatures|nonland permanents)\b"
                   r"|\b(?:each|all) creature[s]? gets? -[x\d]+/-[x\d]+\b"
                   r"|\bdeals? (?:[4-9]|1\d) damage to each creature\b")
_ARTIFACT_TRIGGER = re.compile(
    r"\bwhenever you (?:cast an? (?:artifact|historic) spell|sacrifice an artifact)\b"
    r"|\bwhenever\b[^.\n]{0,75}\bartifacts? you control (?:enters?|leaves?|is put|are put|dies|die)\b"
    r"|\bwhenever (?:an|another|one or more) artifacts? (?:enters?|is put into a graveyard from the battlefield)\b"
    r"|\bwhenever (?:a|another) player sacrifices (?:a )?permanent\b"
)
_ARTIFACT_ENGINE = re.compile(r"\btap two untapped artifacts you control:|\bcreate a token that's a copy of target "
                              r"(?:noncreature )?artifact you control\b")
_ARTIFACT_REACH = re.compile(r"\bdeals? (?:[1-9]\d*|that much|damage)\b[^.\n]{0,75}\b(?:each|target) opponent\b"
                             r"|\b(?:target|each) opponent loses (?:[1-9]\d*|that much|x) life\b"
                             r"|\bdeals? [1-9] damage to any target\b")
_DENSITY = re.compile(r"\b(?:number of|for each) artifacts?(?: and/or enchantment)? you control\b")
_MANA_FILTER = re.compile(r"sacrifice this artifact: add (?:\{[wubrgc]\})+[.] draw a card")
_EMBLEM = re.compile(r"\byou get an emblem\b")
_DEVICE_USE = re.compile(r"\bsacrifice this (?:artifact|creature)\b")
_TOKEN_PAYOFF = re.compile(r"\bwhenever\b[^.\n]{0,80}\b(?:tokens? you control|you create a token|you sacrifice a token)\b")
_TOKENS = re.compile(r"\bcreates?\b[^.\n]{0,45}\b(?:treasure|clue|blood|food|powerstone|servo|thopter|myr) tokens?\b")
_ANIMATE_BOARD = re.compile(r"\b(?:each (?:noncreature )?artifact|(?:noncreature )?artifacts) you control "
                            r"becomes?\b[^.\n]{0,65}\b[045]/[045]\b")
_ANIMATE_OVERLOAD = re.compile(r"^target (?:noncreature )?artifact you control becomes?\b[^.\n]{0,65}"
                               r"\b[045]/[045]\b[^\n]*\n[^\n]*overload \{", re.M)
_FINISH = re.compile(
    r"\b(?:equipped creature|this creature) gets \+1/\+\d for each artifact\b"
    r"|\b(?:deals?|loses?)\b[^.\n]{0,75}\b(?:for each|the number of|twice the number of) artifacts? you control\b"
    r"|\btarget opponent loses the game\b"
)
_DISCOUNT = re.compile(r"^affinity for artifacts\b|^improvise\b|this spell costs?[^.\n]+less to cast for each artifact", re.M)
_DEPENDENCY = re.compile(r"\b(?:shrines?|gates?) you control\b|\b(?:thirteen|13) blood tokens\b")
_SUPPORT = (
    "Rog mana", "Free with commander", "Sacrifice creature", "Sacrifice artifacts", "Artifact recursion", "Artifact pressure",
    "Artifact tokens", "Artifact payoff", "Token payoff", "Silas protection", "Silas evasion", "Finisher",
)
_NOTES = {
    "Rog mana": "A zero-cost Rograkh can turn this into an early mana source.",
    "Rog outlets": "Uses Rograkh as a creature, a legend, or a commander already in play.",
    "Free with commander": "Rograkh can enable its alternate cost as soon as he is on the battlefield.",
    "Silas evasion": "Helps Silas deal combat damage to a player and unlock an artifact in the graveyard.",
    "Silas protection": "Helps Silas survive until the next attack.",
    "Consumable artifact": "Puts itself into the graveyard after generating value that Silas can reuse.",
    "Artifact recursion": "Keeps artifacts accessible even when Silas cannot attack.",
    "Artifact payoff": "Rewards the artifact density and replays in the deck.",
    "Artifact pressure": "Turns artifact plays or sacrifices into repeatable damage or life loss.",
    "Sacrifice artifacts": "Lets you trade a used artifact for value and gives Silas a new target.",
    "Sacrifice creature": "Can spend the free Rograkh body for value.",
    "Cheap artifacts": "A low-cost artifact spell Silas can cast after combat.",
    "Finisher": "Turns the stocked artifact board into a route to close the game.",
    "Early ramp": "Adds mana early so Silas can be cast and the deck can pay for replays.",
}
_BONUS = {
    "Rog outlets": 1.03, "Rog mana": 1.35, "Free with commander": 1.26,
    "Silas evasion": 1.21, "Silas protection": .70, "Consumable artifact": .80,
    "Cheap artifacts": .34, "Artifact recursion": 1.23, "Artifact payoff": 1.08, "Artifact pressure": 1.24,
    "Sacrifice artifacts": .78, "Artifact tokens": .52, "Token payoff": .65,
    "Finisher": 1.02, "Interaction": .53, "Card advantage": .62, "Efficient ramp": .54,
}
_GROUPS = (("Rog mana", "Rog outlets", "Free with commander"), ("Cheap artifacts", "Consumable artifact"))


def _costed_sacrifices(text):
    for line in text.splitlines():
        cost, _, effect = line.partition(":")
        if _SAC_ACTION.search(effect):
            yield cost
        if sacrifice := re.match(r"(?:as an additional cost to cast this spell, |(?:[^:]+: )?(?:you may )?)(sacrifice [^.\n]+)", line):
            yield sacrifice[1]
        if optional := re.search(r"\byou may (sacrifice [^.\n]+)\. if you do,", line):
            yield optional[1]


def _consumable(name, text):
    direct = _DEVICE_USE.search(text) or re.search(rf"\bsacrifice {re.escape(name.casefold().split(',')[0])}\b", text)
    if not direct or not _SAC_ACTION.search(text):
        return False
    # A front-loaded artifact can be a good replay even if its sacrifice ability costs more.
    if re.search(r"\bwhen this (?:artifact|creature|equipment) enters,\s*(?:draw|create|destroy|exile)\b", text):
        return True
    return any(sum(int(m) if m.isdecimal() else 1 for m in re.findall(r"\{([^}]+)\}", line.split(":", 1)[0]) if m != "t") <= 3
               for line in text.splitlines() if direct.group() in line)


def classify_spell(card, price_cents=0):
    """Score real resource use and the targets Silas can cast."""
    front_types, front_cost, front_value, front_text = next(spell_modes(card))
    baseline = _spell(card)
    roles = set()
    for types, cost, value, text in spell_modes(card):
        artifact = "Artifact" in front_types and "Artifact" in types and "Land" not in types and bool(cost.strip("{X}"))
        if artifact and value <= 3:
            roles.add("Cheap artifacts")
        if _DISCOUNT.search(text):
            roles.add("Discounted artifact")
        if artifact and value <= 3 and _consumable(card["name"], text):
            roles.add("Consumable artifact")
        for payment in _costed_sacrifices(text):
            if _OWN_ARTIFACT.search(payment):
                roles.add("Sacrifice artifacts")
            if _OWN_CREATURE.search(payment) and not _ROG_RESTRICTION.search(text):
                roles.update(("Sacrifice creature", "Rog outlets"))
        if "equipped creature gets +1/-1" in text and "equipped creature dies" in text:
            roles.add("Rog outlets")
        if re.search(r"you may sacrifice a nontoken red creature rather than pay", text):
            roles.update(("Sacrifice creature", "Rog outlets"))
        if "if you control a commander" in text and re.search(r"without paying|choose both", text):
            roles.add("Rog outlets")
            if "without paying" in text:
                roles.add("Free with commander")
        if value <= 4 and _EVASION.search(text) and "an opponent controls" not in text:
            roles.add("Silas evasion")
        if value <= 4 and _PROTECTION.search(text) and "an opponent controls" not in text:
            roles.add("Silas protection")
        if _RECURSION.search(text):
            roles.add("Artifact recursion")
        live_text = "\n".join(line for line in text.splitlines() if not _EMBLEM.search(line))
        engine = _ARTIFACT_TRIGGER.search(live_text) or _ARTIFACT_ENGINE.search(live_text)
        if engine or _DENSITY.search(text) or "Discounted artifact" in roles:
            roles.add("Artifact payoff")
        if engine and _ARTIFACT_REACH.search(live_text):
            roles.add("Artifact pressure")
        if _TOKENS.search(text):
            roles.add("Artifact tokens")
        if _TOKEN_PAYOFF.search(text):
            roles.add("Token payoff")
        animate = _ANIMATE_BOARD.search(text) or _ANIMATE_OVERLOAD.search(text)
        if _FINISH.search(text) or animate:
            roles.add("Finisher")
            if animate:
                roles.update(("Artifact payoff", "Board finisher"))
        if _DRAW.search(text) and not re.search(r"draw (two|three|four) cards?, then discard \1 cards?", text):
            roles.add("Card advantage")
        if _CANTRIP.search(text) and "Consumable artifact" in roles:
            roles.add("Consumable value")
        if (value <= 4 or "Free with commander" in roles) and any(
            _INTERACTION.search(line) and not _NONINTERACTION.search(line) for line in live_text.splitlines()
        ):
            roles.add("Interaction")
        if _WIPE.search(live_text) and not re.search(r"blocked by|you cast (?:it|this \w+) from your hand|"
                                                    r"additional cost[^.\n]+sacrifice|that player may put a doom counter", text):
            roles.add("Sweepers")

    mana = [(m["cost"], m["effect"]) for m in _MANA_TAP.finditer(front_text)
            if not re.search(r"spend this mana only|for each", m["effect"])
            and (not m["cost"].startswith("{1},") or _NET_ADD.match(m["effect"]))]
    early = []
    setup = re.search(r"enters tapped|activate only if|\bmetalcraft\b|\bimprint\b|discard a land card", front_text)
    if front_cost and front_value <= 3 and not setup:
        for cost, effect in mana:
            if "remove " in cost or "sacrifice " in cost or "discard " in cost:
                continue
            free_body = bool(re.search(r"tap an untapped (?:artifact or |legendary )?creature you control", cost))
            free_legend = "legendary creatures and planeswalkers you control" in effect
            if "tap an untapped" in cost and not free_body:
                continue
            early.append((cost, effect))
            if free_body or free_legend:
                roles.update(("Rog mana", "Rog outlets"))
    if mana:
        roles.add("Mana acceleration")
    if early:
        roles.add("Early ramp")
        if front_value <= 2:
            roles.add("Efficient ramp")
    if "tap an untapped legendary creature you control: add" in front_text:
        roles.update(("Rog mana", "Rog outlets"))
    if re.search(r"\bsacrifice\b[^:\n]{0,40}: add ", front_text):
        roles.add("One-shot mana")
    if "discard your hand" in front_text:
        # A hand dump can be a combo piece, but it isn't reusable opening ramp.
        roles.difference_update(("Early ramp", "Efficient ramp"))

    efficiency = .19 * ("Consumable value" in roles)
    efficiency += .43 * ("Discounted artifact" in roles and bool(roles & {"Card advantage", "Finisher"}))
    efficiency += .13 * ("Efficient ramp" in roles) + .21 * ("Artifact pressure" in roles)
    efficiency -= .24 * math.log1p(price_cents / 300)
    if _MANA_FILTER.search(front_text):
        efficiency -= .44
    if "Early ramp" in roles and front_value == 3 and not roles & {"Rog mana", "Consumable artifact"}:
        efficiency -= .52
    if front_value >= 4 and "Creature" in front_types and not roles & set(_SUPPORT):
        efficiency -= .42
    if "discard your hand" in front_text:
        efficiency -= .56
    if "equip {3}" in front_text or "equip {4}" in front_text:
        efficiency -= .24
    signals = tuple(role for role in _NOTES if role in roles)
    return replace(baseline, roles=frozenset(roles or {"Support"}), signals=signals,
                   efficiency=efficiency, expensive=front_value >= 6 and "Discounted artifact" not in roles)


def _strategy_bonus(roles):
    reusable = roles["Consumable artifact"] + .25 * roles["Cheap artifacts"]
    outlets = roles["Sacrifice artifacts"] + roles["Artifact recursion"]
    trigger = roles["Silas evasion"] + .4 * roles["Silas protection"]
    return (1.80 * math.log1p(reusable * trigger / 12)
            + 1.30 * math.log1p(reusable * (outlets + roles["Artifact pressure"]) / 17)
            + .95 * math.log1p(roles["Rog outlets"] / 4))


def _fit(spell, score, roles, *, emphasis=1.0):
    bonuses = {role: value / (1 + .12 * roles.get(role, 0)) for role, value in _BONUS.items() if role in spell.roles}
    group_value = 0.0
    for group in _GROUPS:
        values = [bonuses.pop(role) for role in group if role in bonuses]
        group_value += .25 * sum(values) + .75 * max(values, default=0)
    return .58 * score + spell.efficiency + emphasis * (sum(bonuses.values()) + group_value)


def build_candidates(catalog, rank, prices, *, profiles=None):
    colors, _ = _commander_colors(COMMANDER_KEYS, catalog)
    legal = _usable_catalog(catalog, colors)
    metadata = {key: card for key, card in legal.items() if key in prices and key not in _ORIGINAL_DUALS}
    if any(key not in metadata for key in COMMANDER_KEYS):
        raise ConstructionSearchError("Both commanders need a legal stocked printing")
    spells = {key: classify_spell(card, prices[key]) for key, card in metadata.items()
              if key not in COMMANDER_KEYS and not _is_land(card)
              and not any(_DEPENDENCY.search(text) for _, _, _, text in spell_modes(card))}
    heavy = {key: max(0, card["cmc"] - 4) for key, card in metadata.items() if key in spells}
    for land_count, ramp, curve_penalty in (PROFILES if profiles is None else profiles):
        goals = {"Early ramp": ramp, "Efficient ramp": 5, "Cheap artifacts": 20, "Consumable artifact": 7,
                 "Rog mana": 3, "Rog outlets": 6, "Sacrifice creature": 3, "Silas evasion": 4,
                 "Artifact pressure": 3, "Artifact payoff": 9, "Artifact recursion": 3, "Interaction": 9,
                 "Card advantage": 8, "Sweepers": 2, "Finisher": 2, "Board finisher": 1}
        limits = {"Mana acceleration": ramp + 2, "Cheap artifacts": 30, "Consumable artifact": 12, "Silas evasion": 7}
        policy = ConstructionPolicy(goals, limits, classify_spell, _fit, _NOTES,
                                    "The search links Rograkh’s resource roles to Silas’s attack and artifact replays.")
        candidates = {key: replace(spell, efficiency=spell.efficiency - curve_penalty * heavy[key])
                      if heavy[key] and "Discounted artifact" not in spell.roles else spell for key, spell in spells.items()}
        starts, failures = [], []
        for start in range(3):
            try:
                chosen = _pick_spells(candidates, rank, start, policy, spell_slots=98 - land_count, priority_roles=_PRIORITIES)
            except ConstructionSearchError as error:
                failures.append({"start": start + 1, "reason": str(error)})
            else:
                starts.append(_evaluate(chosen, candidates, rank, policy, strategy_bonus=_strategy_bonus))
        if not starts:
            print(f"{land_count} lands / {ramp} early ramp: {failures}", flush=True)
            continue
        best = max(starts, key=lambda row: row[0])
        improvements = 0
        for _ in range(3):
            revised = _refine(best, candidates, rank, policy, strategy_bonus=_strategy_bonus, extra_roles=_SUPPORT)
            if revised is best:
                break
            best = revised
            improvements += 1
        fit, selected, scores, coverage = best
        core = sum(prices[key] for key in (*COMMANDER_KEYS, *selected))
        for ratio, pressure in ((.9, .25), (1.0, .19), (1.05, .12)):
            budget = land_allocation(core, ratio, basic_floor=0)
            try:
                lands, early, _ = build_mana_base(metadata, prices, colors, None, budget=budget, count=land_count,
                                                   price_pressure=pressure, artifact_support=True)
            except ConstructionSearchError as error:
                print(f"{land_count} lands / {ratio:g} allocation: {error}", flush=True)
                continue
            deck = _deck_result(metadata, COMMANDER_KEYS, lands, early, candidates, selected, scores, fit, coverage,
                                policy, 3, [round(row[0], 4) for row in starts], failures, improvements, colors)
            deck["construction"]["spell_role_counts"].update({role: coverage[role] for role in _SUPPORT})
            deck["construction"]["land_allocation"] = {
                "share_of_nonland_price": ratio, "estimated_cents": budget, "nonland_estimated_cents": core,
            }
            yield deck


def game_plan(deck):
    names = {card["name"] for card in deck["cards"]}
    phases = [
        ("Put Rograkh to work early",
         "Rograkh costs zero on the first cast. Tap him to help pay for Silas, or keep him around to enable a commander spell. "
         "Once you have enough mana, you can sacrifice him to draw cards.",
         ("Springleaf Drum", "Relic of Legends", "Deadly Dispute", "Mox Amber", "Skullclamp")),
        ("Connect with Silas",
         "Get a worthwhile artifact into your graveyard before combat and give Silas a path past blockers. After he hits a player, you can cast the chosen artifact that turn if you can pay its cost.",
         ("Key to the City", "Brotherhood Regalia", "Thieves' Tools", "Whispersilk Cloak", "Prowler's Helm", "Swiftfoot Boots")),
        ("Keep reusing artifacts",
         "Replay cheap artifacts for cards and removal. Use additional recursion to keep the engine going when Silas can’t connect.",
         ("Aether Spellbomb", "Pyrite Spellbomb", "Emry, Lurker of the Loch", "Daretti, Scrap Savant", "Trading Post")),
        ("Turn the artifact board into damage",
         "Repeated artifact entries and sacrifices wear down opponents. When you have a large board, turn it into attackers or use a payoff that scales with the artifact count.",
         ("Reckless Fireweaver", "Oni-Cult Anvil", "Rise and Shine", "The Antiquities War", "Cyberdrive Awakener")),
    ]
    result = []
    for title, description, cards in phases:
        present = [name for name in cards if name in names]
        if present:
            description += " Look for " + ", ".join(present[:3]) + "."
        result.append({"title": title, "description": description})
    return result
