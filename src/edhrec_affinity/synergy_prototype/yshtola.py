"""Build the Esper spell and life-loss engine for Y'shtola."""

from dataclasses import replace
import math
import re

from ..build_styles.deckbuilding import _commander_colors, _is_land
from ..build_styles.sources import card_key
from .catalog import card_faces
from .construction import ConstructionPolicy, ConstructionSearchError, _deck_result, _pick_spells, _spell, _usable_catalog
from .mana_plan import build_mana_base
from .refinement import _evaluate, _refine, land_allocation


COMMANDER = "Y'shtola, Night's Blessed"
COMMANDER_KEY = card_key(COMMANDER)
COMMANDER_SLUG = "yshtola-nights-blessed"
COHORT = "yshtola"
SUMMARY = (
    "Cast noncreature spells to drain the table, cross four life lost before the end step, "
    "then use the extra cards to keep casting through the turn cycle."
)
MODEL_DESCRIPTION = (
    "A neural ranker learns card fit from Oracle text, card characteristics, and collected lists. "
    "Y'shtola's optimizer checks cast mana value, spell timing, and whether life loss can reach the draw threshold."
)
_SCORE_EXPLANATION = (
    "The search rewards affordable qualifying casts, early interaction, and ways to reach four life lost on one turn."
)

_PIPS = re.compile(r"\{([^}]+)\}")
_MANA_TAP = re.compile(r"^(?:\{1\}, )?\{t\}: add [^.\n]+", re.M)
_SPELL_DISCOUNT = re.compile(r"(?:instant|sorcery|noncreature|blue|white|black) spells[^.\n]{0,60}\bcost\b[^.\n]{0,25}\bless\b")
_DISCOUNTED_CAST = re.compile(
    r"^delve\b|^affinity for (?:artifacts|enchantments)\b|this spell costs?[^.\n]+less to cast for each",
    re.M,
)
_UNTAP = re.compile(r"\buntap (?:up to )?(?:two|three|four|five|[2-5]) (?:target )?lands?\b")
_REPEAT_LOSS = re.compile(
    r"\bwhenever\b[^.\n]{0,95}\b(?:opponents?|that player) loses? (?:[12]|one|two) life\b"
)
_LIFE_BRIDGE = re.compile(r"\bwhenever you gain life\b[^.\n]{0,100}\b(?:opponents?|player) loses? that much life\b")
_LIFE_ECHO = re.compile(r"\bwhenever you gain life\b[^.\n]{0,90}\b(?:opponents?|player) loses? (?:1|one) life\b")
_FIXED_LOSS = re.compile(
    r"\b(?:each (?:other )?opponent|target (?:opponent|player)|each player|you) loses? (?:[4-9]|1\d) life\b"
    r"|\byou lose (?:[4-9]|1\d) life\b"
    r"|\bdeals? (?:[4-9]|1\d) damage to (?:each opponent|target (?:opponent|player))\b"
)
_SMALL_LOSS = re.compile(
    r"\b(?:each opponent|target (?:opponent|player)|each player|you) loses? (?:[1-3]|one|two|three) life\b"
    r"|\byou lose (?:[1-3]|one|two|three) life\b"
    r"|\bdeals? (?:[1-3]|one|two|three) damage to (?:each opponent|target (?:opponent|player))\b"
)
_DAMAGE_PAYOFF = re.compile(
    r"\bwhenever (?:enchanted creature|equipped creature|this creature) deals damage to (?:an? opponent|a player)\b"
)
_TRUE_DRAW = re.compile(
    r"\b(?:draws? (?:two|three|four|five|six|[2-9]|x|that many) cards?|draws? a card for each|"
    r"(?:whenever|at the beginning of)[^.\n]{0,130}\bdraws? (?:a card|cards)|"
    r"draws? cards? equal to|put (?:two|three|four|all)[^.\n]{0,60} into your hand|exchange (?:your )?hand)\b"
)
_INTERACTION = re.compile(
    r"\b(?:destroy|exile|return)\b[^.\n]{0,19}\btarget\b[^.\n]{0,65}"
    r"\b(?:spells?|creatures?|artifacts?|enchantments?|nonland permanents?|permanents?|planeswalkers?|abilities|ability)\b"
    r"|\bcounter (?:up to \w+ )?target\b[^.\n]{0,36}\b(?:spells?|abilities|ability)\b"
    r"|\b(?:damage to|gets? -\d+/-\d+)[^.\n]{0,20}\btarget creature\b"
    r"|\btarget creature gets -\d+/-\d+\b"
    r"|\btarget opponent sacrifices?\b"
)
_NEEDS_SPELL = re.compile(r"\b(?:counter|return)\b[^.\n]{0,25}\btarget\b[^.\n]{0,25}\bspell\b")
_SWEEPER = re.compile(
    r"\b(?:destroy|exile|return) (?:up to \w+ |all other |all |each )(?:(?:nonland|tapped|attacking) )?"
    r"(?:creatures|nonland permanents)\b|\ball creatures get -[x\d]+/-[x\d]+\b"
)
_PROTECTION = re.compile(
    r"\b(?:target creature you control|target creature|creatures you control|permanents you control)\b"
    r"[^.\n]{0,55}\b(?:hexproof|indestructible|phase out|protection from)\b"
)
_END_STEP = re.compile(r"at the beginning of (?:each|your|the next|that player's|their) end step")
_OWN_TURN = re.compile(r"activate only (?:as a sorcery|during your turn|during your upkeep|during combat on your turn)")
_LOOP_PAY = re.compile(r"^pay [1-4] life: ", re.M)
_TRIBAL_DOUBLER = re.compile(r"triggered ability of another creature you control of the chosen type triggers")
_ABILITY_COPY = re.compile(r"copy target (?:activated or )?triggered ability you control")
_LIFE_FINISH = re.compile(
    r"\beach opponent loses (?:(?:(?:twice|two times) )?x|[5-9]|1\d) life\b"
    r"|\beach opponent loses life equal to the life (?:they|that player) lost"
)
_UNSUPPORTED = re.compile(r"\bshrines? you control\b|\b(?:thirteen|13) blood tokens\b")

_NOTES = {
    "Turn-cycle cast": "A qualifying instant or flash spell can trigger Y’shtola before an opponent’s end step.",
    "Qualifying cast": "A noncreature mode reaches mana value three and triggers Y’shtola when cast.",
    "Four-life enabler": "Can cause a single player to lose four life in the same turn.",
    "Flexible life payment": "Can contribute life loss on your own turn or an opponent’s turn.",
    "Life-gain conversion": "Turns Y’shtola’s two life gained into another two lost by one opponent.",
    "Repeat loss": "Can add to an opponent’s life loss before the end step.",
    "Proactive turn-cycle cast": "Can be cast for value before an opponent’s end step when there is no threat to answer.",
    "Double trigger": "Can double Y’shtola’s damage trigger, or copy the ability when the activation is available.",
    "Damage payoff": "Rewards Y’shtola’s noncombat damage to players.",
    "Spell payoff": "Rewards casting instants, sorceries, or other noncreature spells.",
    "Flash support": "Lets more of the spell core be cast in another player’s turn.",
    "Mana refund": "Can reopen mana for a second spell after the first qualifying cast.",
    "Spell discount": "Reduces how much mana some spells cost while preserving their mana value.",
    "Early ramp": "Helps cast Y’shtola with the right three colors and keep mana open afterward.",
    "Interaction": "Can answer a threat while the deck develops its engine.",
    "Protection": "Helps keep Y’shtola available for later casts and end steps.",
    "Finisher": "Turns a large mana investment or a full turn of life loss into a way to close the game.",
}
_ENGINE = ("Four-life enabler", "Flexible life payment", "Life-gain conversion", "Repeat loss", "Double trigger",
           "Damage payoff", "Spell payoff", "Flash support", "Mana refund", "Spell discount", "Finisher", "Small life loss")
_BONUSES = {
    "Qualifying cast": .62, "Turn-cycle cast": .78, "Four-life enabler": 1.58, "Flexible life payment": 1.12,
    "Life-gain conversion": 2.0, "Repeat loss": 1.12, "Double trigger": 1.82, "Damage payoff": 1.65,
    "Spell payoff": .70, "Flash support": .95, "Mana refund": 1.36, "Spell discount": .84, "Finisher": .68,
    "Small life loss": .24, "Interaction": .49, "Protection": .64, "Card advantage": .55, "Efficient ramp": .83,
    "Conditional conversion": .58, "Proactive turn-cycle cast": .72,
}
_TURN_ENGINE = ("Four-life enabler", "Flexible life payment", "Life-gain conversion", "Repeat loss", "Double trigger")
_PRIORITY_ROLES = ((), ("Draw enabler", "Turn-cycle cast", "Qualifying cast"), ("Early ramp", "Interaction", "Protection"))
_ORIGINAL_DUALS = {"scrubland", "tundra", "underground sea"}
PROFILES = ((35, 10, .17), (36, 10, .15), (37, 10, .13), (37, 9, .11),
            (38, 9, .10), (39, 9, .10), (40, 9, .09))


def _mana_value(cost):
    return sum(max((int(ch) for ch in symbol.split("/") if ch.isdecimal()), default=0 if symbol == "X" else 1)
               for symbol in _PIPS.findall(cost or ""))


def spell_modes(card):
    faces = card_faces(card)
    if card.get("layout") not in {"adventure", "split", "modal_dfc"}:
        faces = faces[:1]
    for face in faces:
        types = set(face["type_line"].split(" — ", 1)[0].split())
        cost = face.get("mana_cost") or ""
        text = re.sub(r"\([^)]*\)", "", face.get("oracle_text") or "").casefold().replace("’", "'")
        yield types, cost, _mana_value(cost), text


def _life_roles(types, cost, text):
    roles = set()
    lines = [line for line in text.splitlines() if not _END_STEP.search(line) and "combat damage" not in line]
    active = "\n".join(lines)
    phyrexian = sum("/P" in symbol for symbol in _PIPS.findall(cost))
    payment = _LOOP_PAY.search(active)
    repeatable = bool(payment and not _OWN_TURN.search(active) and "activate only once" not in active)
    alt_pay = bool(re.search(r"you may pay (?:4|5) life\b[^.\n]{0,70}\brather than pay", active))
    fixed_loss = any(_FIXED_LOSS.search(line) and "unless" not in line for line in lines)
    combined_modes = "choose one or more" in active and sum(map(int, re.findall(r"you lose ([1-3]) life", active))) >= 4
    if fixed_loss or alt_pay or phyrexian >= 2 or repeatable or combined_modes:
        roles.add("Four-life enabler")
    if alt_pay or phyrexian >= 2 or repeatable or fixed_loss and ("Instant" in types or "flash" in active.splitlines()[:1]):
        roles.add("Flexible life payment")
    for line in lines:
        conversion = _LIFE_BRIDGE.search(line)
        if conversion:
            roles.add("Conditional conversion" if ":" in line[:conversion.start()] else "Life-gain conversion")
    if _REPEAT_LOSS.search(active) and not re.search(r"whenever[^.\n]{0,82}\b(?:dies|sacrifice|is put into a graveyard)\b", active):
        roles.add("Repeat loss")
    if _LIFE_ECHO.search(active) or _SMALL_LOSS.search(active):
        roles.add("Small life loss")
    return roles


def classify_spell(card, price_cents=0):
    """Describe actual cast modes, threshold enablers, and independent jobs."""
    baseline = _spell(card)
    roles = set()
    front_types, front_cost, front_value, front_text = next(spell_modes(card))
    for types, cost, value, text in spell_modes(card):
        instant = "Instant" in types or bool(re.search(r"^flash\b", text, re.M))
        noncreature = "Creature" not in types and "Land" not in types
        life_roles = _life_roles(types, cost, text)
        roles.update(life_roles)
        if noncreature and value >= 3:
            roles.add("Qualifying cast")
            if instant:
                roles.add("Turn-cycle cast")
                proactive = bool(_TRUE_DRAW.search(text) or _UNTAP.search(text)) and (
                    not _NEEDS_SPELL.search(text) or "choose one" in text
                ) and not re.search(r"as an additional cost[^.\n]+sacrifice|if that spell is countered", text)
                if proactive:
                    roles.add("Proactive turn-cycle cast")
        elif noncreature and "{X}" in cost:
            roles.add("Variable cast")
        if _TRUE_DRAW.search(text) and not re.search(r"draw (two|three|four) cards?, then discard \1 cards?", text):
            roles.add("Card advantage")
        if _UNTAP.search(text) and noncreature:
            roles.add("Mana refund")
        if _SPELL_DISCOUNT.search(text):
            roles.add("Spell discount")
        if _DISCOUNTED_CAST.search(text) and noncreature and value >= 3:
            roles.add("Discounted cast")
        if _LIFE_FINISH.search(text) or ("each opponent loses" in text and "repeat this process x times" in text) or (
            "{X}" in cost and re.search(r"each opponent loses (?:twice )?x life", text)
        ):
            roles.add("Finisher")
        if _PROTECTION.search(text) or re.search(r"\b(?:target permanent you control|you and permanents you control) phase out\b", text):
            if value <= 4:
                roles.add("Protection")
        if _INTERACTION.search(text) and value <= 4 and not _DAMAGE_PAYOFF.search(text) and not re.search(
            r"\b(?:target|a) (?:creature|permanent) you control\b|\bwhen[^.\n]{0,40}\bdies\b", text,
        ):
            roles.add("Interaction")
            if instant:
                roles.add("Instant interaction")
        if _SWEEPER.search(text) and not re.search(
            r"blocked by|you cast (?:it|this \w+) from your hand|additional cost to cast[^.\n]+sacrifice", text,
        ):
            roles.add("Sweepers")
        if _DAMAGE_PAYOFF.search(text) and not re.search(r"combat damage|deals damage to a creature", text):
            roles.add("Damage payoff")
        if re.search(r"\bwhenever you cast (?:an? |your (?:first|second) )?(?:instant|sorcery|noncreature)\b", text):
            roles.add("Spell payoff")
        if "choose a creature type" in text and _TRIBAL_DOUBLER.search(text) or _ABILITY_COPY.search(text):
            roles.add("Double trigger")
        if re.search(r"\b(?:you may cast spells as though they had flash|noncreature spells[^.\n]+have flash)\b", text):
            roles.add("Flash support")

    taps = _MANA_TAP.findall(front_text)
    valid_taps = [line for line in taps if not any(gate in line for gate in (
        "spend this mana only", "for each", "any color of mana that",
    )) and (not line.startswith("{1},") or re.search(
        r"\badd (?:two mana|\{[wubrgc]\}\{[wubrgc]\})", line.split(":", 1)[1],
    ))]
    early_ramp = bool(front_cost and front_value <= 3 and valid_taps and not re.search(
        r"activate only|enters tapped|remove [^.\n]*counter|if you (?:control|have)", front_text,
    ))
    # An immediate search for a Plains into play is rare in Esper. Restrict
    # ramp credit to a mana-producing main face or an independent land search.
    land_ramp = bool(front_cost and front_value <= 3 and re.search(
        r"search your library for[^.\n]+land cards?[^.\n]+onto the battlefield", front_text,
    ) and "if an opponent controls" not in front_text)
    if early_ramp or land_ramp:
        roles.add("Early ramp")
        if front_value <= 2:
            roles.add("Efficient ramp")
    if valid_taps or land_ramp:
        roles.add("Mana acceleration")
    if roles & set(_TURN_ENGINE):
        roles.add("Draw enabler")
    if "Instant interaction" in roles or "Protection" in roles:
        roles.add("Hold-up interaction")
    if any("Creature" not in types and "Land" not in types and 3 <= value <= 4 for types, _, value, _ in spell_modes(card)):
        roles.add("Affordable trigger")

    efficiency = (.36 * ("Affordable trigger" in roles) + .36 * ("Discounted cast" in roles)
                  + .24 * ("Interaction" in roles and front_value <= 2))
    if early_ramp and front_value <= 2:
        efficiency += .38 if any("any color" in line and "opponent" not in line or " or " in line
                                 or len(_PIPS.findall(line)) >= 2 for line in valid_taps) else .13
    efficiency -= .16 * math.log1p(price_cents / 275)
    if front_value >= 4 and "Creature" in front_types and not roles & set(_ENGINE):
        efficiency -= .31
    if "at the beginning of each end step" in front_text and "lost this turn" in front_text:
        efficiency -= .19
    if "first outlaw spell each turn" in front_text:
        efficiency -= .65
    efficiency -= .26 * max(0, max((front_cost.count("{" + color + "}") for color in "WUB"), default=0) - 2)
    signals = tuple(label for label in _NOTES if label in roles)
    return replace(baseline, roles=frozenset(roles or {"Support"}), signals=signals,
                   efficiency=efficiency, expensive=front_value >= 6 and "Discounted cast" not in roles)


def _strategy_bonus(coverage):
    qualify = coverage["Qualifying cast"]
    engine = (1.3 * coverage["Life-gain conversion"] + coverage["Double trigger"]
              + .9 * coverage["Repeat loss"] + .45 * coverage["Small life loss"])
    flexible = (coverage["Turn-cycle cast"] + coverage["Proactive turn-cycle cast"]
                + coverage["Flash support"] + .75 * coverage["Mana refund"])
    return (
        2.0 * math.log1p(qualify * engine / 24)
        + 1.55 * math.log1p(flexible * (coverage["Four-life enabler"] + .45 * engine) / 13)
        + 1.10 * math.log1p(coverage["Affordable trigger"] / 8)
        + .85 * math.log1p(coverage["Efficient ramp"] / 3)
    )


def _fit(spell, score, coverage, *, emphasis=1.0):
    bonuses = {role: bonus / (1 + (.26 if role in _TURN_ENGINE else .12) * coverage.get(role, 0))
               for role, bonus in _BONUSES.items() if role in spell.roles}
    engine = [bonuses.pop(role) for role in _TURN_ENGINE if role in bonuses]
    engine_fit = .25 * sum(engine) + .75 * max(engine, default=0)
    return .59 * score + spell.efficiency + emphasis * (sum(bonuses.values()) + engine_fit)


def build_candidates(catalog, rank, prices, *, profiles=None):
    commanders = (COMMANDER_KEY,)
    colors, _ = _commander_colors(commanders, catalog)
    legal = _usable_catalog(catalog, colors)
    metadata = {key: card for key, card in legal.items() if key in prices and key not in _ORIGINAL_DUALS}
    if COMMANDER_KEY not in metadata:
        raise ConstructionSearchError("Y'shtola has no legal stocked printing")
    spells = {key: classify_spell(card, prices[key]) for key, card in metadata.items()
              if key != COMMANDER_KEY and not _is_land(card)
              and not any(_UNSUPPORTED.search(text) for _, _, _, text in spell_modes(card))}
    heavy = {key: max(0, metadata[key]["cmc"] - 4) for key in spells}
    if profiles is None:
        profiles = PROFILES
    for land_count, ramp, curve_penalty in profiles:
        goals = {
            "Early ramp": ramp, "Efficient ramp": 6, "Qualifying cast": 26, "Affordable trigger": 16,
            "Turn-cycle cast": 9, "Proactive turn-cycle cast": 5, "Draw enabler": 6, "Four-life enabler": 2, "Interaction": 7,
            "Hold-up interaction": 6, "Sweepers": 2, "Protection": 2, "Card advantage": 6, "Finisher": 2,
        }
        policy = ConstructionPolicy(goals, {"Mana acceleration": ramp + 3}, classify_spell, _fit, _NOTES, _SCORE_EXPLANATION)
        candidates = {key: replace(spell, efficiency=spell.efficiency - curve_penalty * heavy[key])
                      if heavy[key] and "Discounted cast" not in spell.roles else spell for key, spell in spells.items()}
        starts, failures = [], []
        for start in range(3):
            try:
                picked = _pick_spells(candidates, rank, start, policy, spell_slots=99 - land_count,
                                      priority_roles=_PRIORITY_ROLES)
            except ConstructionSearchError as error:
                failures.append({"start": start + 1, "reason": str(error)})
            else:
                starts.append(_evaluate(picked, candidates, rank, policy, strategy_bonus=_strategy_bonus))
        if not starts:
            print(f"{land_count} lands / {ramp} early ramp: {failures}", flush=True)
            continue
        best = max(starts, key=lambda row: row[0])
        improvements = 0
        for _ in range(3):
            revised = _refine(best, candidates, rank, policy, strategy_bonus=_strategy_bonus, extra_roles=_ENGINE)
            if revised is best:
                break
            best = revised
            improvements += 1
        fit, selected, scores, coverage = best
        nonlands = sum(prices[key] for key in (COMMANDER_KEY, *selected))
        for ratio, pressure in ((.9, .25), (1.0, .19), (1.05, .12)):
            allowance = land_allocation(nonlands, ratio, basic_floor=0)
            try:
                lands, general, _ = build_mana_base(metadata, prices, colors, None, budget=allowance,
                                                      count=land_count, price_pressure=pressure)
            except ConstructionSearchError as error:
                print(f"{land_count} lands / {ratio:g} allocation: {error}", flush=True)
                continue
            deck = _deck_result(metadata, commanders, lands, general, candidates, selected, scores, fit, coverage,
                                policy, 3, [round(row[0], 4) for row in starts], failures, improvements, colors)
            deck["construction"]["spell_role_counts"].update({role: coverage[role] for role in _ENGINE})
            deck["construction"]["land_allocation"] = {
                "share_of_nonland_price": ratio, "estimated_cents": allowance, "nonland_estimated_cents": nonlands,
            }
            yield deck


def game_plan(deck):
    names = {card["name"] for card in deck["cards"]}
    life_example = ""
    if "Dismember" in names:
        life_example += " Paying both life costs on Dismember loses four life at once."
    if "Vito, Thorn of the Dusk Rose" in names:
        life_example += " Vito can turn one cast trigger into four life lost by the opponent you choose."
    close = " Debt to the Deathless can close the game once you have enough mana." if "Debt to the Deathless" in names else ""
    draw_payoff = " Queza makes each extra card drain an opponent." if "Queza, Augur of Agonies" in names else ""
    stages = [
        ("Fix all three colors",
         "Open with white, blue, and black sources and a cheap mana rock. Aim to cast Y'shtola while keeping cheap interaction available for the next turn.",
         ("Arcane Signet", "Talisman of Dominance", "Talisman of Progress", "Talisman of Hierarchy", "Fellwar Stone")),
        ("Get one player to four life lost",
         "Cast noncreature spells with mana value 3 or more. Two triggers in one turn are enough, and adding two more to the same opponent also works. Your own life payments can qualify you separately. Reach four before the end step begins." + life_example,
         ("Dismember", "Snuff Out", "Enduring Tenacity", "Vito, Thorn of the Dusk Rose", "Doom Whisperer", "Roaming Throne")),
        ("Use other players' turns",
         "Hold up qualifying instants when a player has already lost life that turn. An untap effect makes a second cast easier. Use instant draw spells before the end step when the table is quiet." + draw_payoff + close,
         ("Frantic Search", "Rewind", "Unwind", "Ophidian Eye", "Quick Study", "Exsanguinate", "Wound Reflection")),
    ]
    result = []
    for title, detail, examples in stages:
        present = [name for name in examples if name in names]
        if present:
            detail += " Look for " + ", ".join(present[:3]) + "."
        result.append({"title": title, "description": detail})
    return result
