"""Constrained, context-aware construction for The Tenth Doctor and Rose Tyler."""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import combinations
import math
import re

from ..build_styles.deckbuilding import (
    _BASIC_COLORS,
    _commander_colors,
    _complete_metadata,
    _is_land,
    _land_information,
    _land_sources,
    _mana_diagnostics,
    _metadata_identity,
    _oracle,
    _validate,
    functional_tags,
)
from ..build_styles.external_validation import cardlist_hash
from ..build_styles.official_checker import deck_url
from ..build_styles.sources import card_key
from . import mechanics


COMMANDER_NAMES = ("The Tenth Doctor", "Rose Tyler")
LAND_SLOTS = 37
SPELL_SLOTS = 100 - len(COMMANDER_NAMES) - LAND_SLOTS
GOALS = {
    "Efficient ramp": 5,
    "Early ramp": 8,
    "Interaction": 7,
    "Card advantage": 8,
    "Sweepers": 2,
    "Early attackers": 11,
    "Creatures": 19,
    "Rose support": 3,
    "Commander protection": 2,
    "Evasion support": 1,
    "Suspend": 7,
    "Time travel": 3,
    "Exile payoffs": 4,
    "Direct synergy": 20,
    "Finishers": 3,
}
LIMITS = {"Mana acceleration": 13, "Mundane mana rocks": 3, "Suspend": 15, "Rose support": 6}
STRATEGY_NOTES = {
    "Suspend": "Adds a suspended card that can grow Rose Tyler and can be accelerated with time travel.",
    "Time travel": "Adjusts time counters to get suspended spells back sooner or grow Rose Tyler.",
    "Time counters": "Interacts with time counters or suspended cards, a resource both commanders use.",
    "Exile payoffs": "Rewards spells cast from exile, including the Doctor's suspended spells.",
    "Library setup": "Can change the top of the library before the Doctor's attack trigger.",
    "Attack setup": "Helps establish attackers or connect with combat damage for the commander's attack plan.",
    "Early ramp": "Helps cast the five-mana Doctor and pay for his seven-mana time-travel ability.",
    "Interaction": "Supplies early interaction while the deck sets up its attackers and suspended cards.",
    "Card advantage": "Provides card advantage to keep the deck stocked through its setup turns.",
    "Sweepers": "Resets a threatening board and gives the deck time to recover.",
    "Early attackers": "Can establish a creature early so the Doctor can trigger as combat starts.",
    "Rose support": "Can protect an attacker or help Rose connect after her time counters build up.",
    "Commander protection": "Protects Rose Tyler or the Doctor through a removal spell or combat.",
    "Evasion support": "Can help an attacker, including Rose Tyler, get through blockers.",
    "Finishers": "Can help close a game; the Doctor can suspend it and bypass its normal mana cost.",
}
_SUSPEND = re.compile(r"^suspend (?:\d+|x)\s*[—–-]", re.MULTILINE)
_EXILE_PAYOFF = re.compile(
    r"(?:spells? (?:that )?you cast from exile|"
    r"you cast (?:(?:a|the|your|each|first|second|noncreature|creature|instant|sorcery) )+spells? from exile|"
    r"cast from anywhere other than (?:your|its owner's) hand|"
    r"cast without paying (?:its|their) mana cost[^.\n]*you may copy)"
)
_LAND_MANA = re.compile(r"^\{t\}(?:, pay [123] life)?: add (.*)$", re.MULTILINE)
_FETCH = re.compile(r"^\{t\}(?:, pay 1 life)?, sacrifice this land: search your library", re.MULTILINE)
_REPEATABLE_MANA = re.compile(r"^(\{1\}, )?\{t\}(?:, pay 1 life)?: add ([^.\n]+)", re.MULTILINE)
_ATTACKER_TARGET = r"(?:equipped creature|target creatures?\b(?! tokens?)(?: you control)?|(?:creatures|permanents) you control)"
_MAJOR_SIGNALS = {"Time travel", "Suspend", "Exile payoffs", "Time counters"}


@dataclass(frozen=True)
class Spell:
    roles: frozenset[str]
    signals: tuple[str, ...]
    expensive: bool
    efficiency: float


def _spell(card: dict) -> Spell:
    text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold())
    type_line = card["type_line"].split(" // ")[0].casefold()
    front = card["card_faces"][0] if card.get("card_faces") else card
    front_text = re.sub(r"\([^)]*\)", "", front.get("oracle_text", "").casefold())
    cost = front.get("mana_cost", "")
    value = card["cmc"]
    creature = "creature" in type_line.split()
    ordinary_early_cast = bool(cost) and value <= 3
    roles = set(functional_tags(card))
    attack_setup = bool(re.search(r"(?:creatures? you control|target creature)[^.\n]*"
                                  r"(?:can't be blocked|gains? haste|have haste)", text))
    exile_payoff = bool(_EXILE_PAYOFF.search(text))
    signals = tuple(label for label, present in (
        ("Time travel", bool(re.search(r"\btime travel\b", text))),
        ("Suspend", bool(_SUSPEND.search(text))),
        ("Exile payoffs", exile_payoff),
        ("Time counters", "time counter" in text or "suspended card" in text or bool(re.search(r"^vanishing \d+", text, re.M))),
        ("Library setup", bool(re.search(r"(?:put|return)[^.\n]*(?:on top of|on the top of) your library", text))
         and not re.search(r"(?:put|return) [^.\n]*rest[^.\n]*top of your library", text)),
        ("Attack setup", attack_setup),
    ) if present)
    roles.update(signals)
    if _MAJOR_SIGNALS.intersection(signals):
        roles.add("Direct synergy")
    if creature:
        roles.add("Creatures")
    if creature and ordinary_early_cast and "defender" not in card.get("keywords", []) and "defender" not in text:
        roles.add("Early attackers")
    mana_lines = [mana for cost, mana in _REPEATABLE_MANA.findall(front_text)
                  if not cost or re.search(r"\{[wubrgc]\}\{[wubrgc]\}|(?:two|three) mana", mana)]
    artifact = "artifact" in type_line.split()
    conditional = bool(re.search(r"spend this mana only|activate only|for each|add one mana of any color that", front_text))
    land_ramp = bool(re.search(r"search your library[^.\n]*land cards?[^.\n]*onto the battlefield", front_text))
    self_treasure = bool(re.search(r"(?:you )?create (?:a|one|two|three|that many|x) treasure tokens?", front_text)
                         and not re.search(r"controller creates?|opponent creates?|enchanted permanent|\{[4-9]\}[^:\n]*:",
                                           front_text))
    produces_extra = bool(re.search(r"\badd (?:an amount of|a number of|\{[wubrgc]\}\{[wubrgc]\})", front_text))
    reduces_cost = bool(re.search(r"(?:^|\n)(?:your )?(?:artifact|creature|instant and sorcery|legendary) spells you cast cost \{1\} less", front_text))
    rolls_mana = bool(mana_lines or land_ramp or self_treasure or produces_extra or reduces_cost)
    efficiency = 0.0
    if rolls_mana:
        roles.update(("Ramp", "Mana acceleration"))
        if ordinary_early_cast and not conditional and (mana_lines or land_ramp or self_treasure):
            roles.add("Early ramp")
        if ordinary_early_cast and value <= 2 and not conditional and mana_lines:
            roles.add("Efficient ramp")
            colored_mana = set(re.findall(r"\{([wubrg])\}", " ".join(mana_lines)))
            flexible = len(colored_mana) >= 2 or any("any color" in line for line in mana_lines)
            efficiency = .63 if any("your commander's color identity" in line for line in mana_lines) else (
                .50 if flexible or produces_extra else .12)
        if artifact and value >= 3 and mana_lines and not _MAJOR_SIGNALS.intersection(signals):
            roles.add("Mundane mana rocks")
            efficiency = -.52
    else:
        roles.discard("Ramp")
    if ordinary_early_cast:
        if re.search(_ATTACKER_TARGET + r"[^.\n]*(?:hexproof|indestructible|shroud|protection from|phases? out)", front_text):
            roles.update(("Commander protection", "Rose support"))
        if re.search(_ATTACKER_TARGET + r"[^.\n]*can't be blocked", front_text):
            roles.update(("Evasion support", "Rose support"))
    if ("Card Advantage" in roles and (not ordinary_early_cast or re.search(
            r"(?:\bdraws? (?:two|three|four|five|six|seven|x|that many|\d+) cards?|"
            r"(?:whenever|at the beginning|\{t\}:)[^.\n]*draw|draw[^.\n]*cards? equal|"
            r"(?:play|cast) [^.\n]*(?:exil|those cards|that card|them)|put [^.\n]*into your hand)", text))
            and not re.search(r"(?:put (?:a card|one card|two cards|three cards) from your hand[^.\n]*top of your library|"
                              r"draw (two|three|four) cards?, then discard \1 cards?)", text)):
        roles.add("Card advantage")
    if "Wipe" in roles or re.search(r"all creatures[^.\n]*(?:phase out|are phased out)", text):
        if not re.search(r"destroy all lands|destroy all other permanents|sacrifices? (?:all|each) land", text):
            roles.add("Sweepers")
    if ordinary_early_cast and (re.search(r"(?:destroy|exile|return|phase out|counter) (?:up to \w+ )?target ", text)
                                or ("enchanted permanent" in text and "loses all other abilities" in text)
                                or ("Removal" in roles and not re.search(r"\{[4-9]\}[^:\n]*:", text)
                                    and "Sweepers" not in roles and "destroy all" not in text)):
        roles.add("Interaction")
    power = card.get("power", "")
    if ((creature and power.isdecimal() and int(power) >= 5 and value >= 4)
            or re.search(r"additional combat phase|deals? [^.\n]*(?:damage to each opponent|damage to all opponents)", text)):
        roles.add("Finishers")
    cheap_alternative = bool(re.search(r"(?:suspend \d+\s*[—–-]|evoke |prototype )\{(?:[0-3]|[wubrg])\}", text))
    return Spell(frozenset(roles or {"Leftover"}), signals, value >= 6 and not cheap_alternative, efficiency)


def _usable_catalog(catalog: Mapping[str, dict], colors: set[str]) -> dict[str, dict]:
    result = {}
    identities = set()
    for card in sorted(catalog.values(), key=lambda row: row["name"]):
        if (not _complete_metadata(card) or card["legalities"].get("commander") != "legal"
                or not set(card["color_identity"]) <= colors):
            continue
        if (_is_land(card) and any(color not in colors for subtype, color in _BASIC_COLORS.items()
                                    if subtype in card["type_line"].casefold().split())):
            continue
        identity = _metadata_identity(card)
        if identity not in identities:
            result[card_key(card["name"])] = {**card, "_build_cache": {}}
            identities.add(identity)
    return result


def _land_quality(card: dict) -> float:
    text = _oracle(card).casefold()
    quality = _land_information(card)["untapped_quality"]
    if (re.search(r"enters? (?:the battlefield )?tapped(?! unless)", text)
            and "pay 2 life" not in text):
        quality = min(quality, .2)
    if ("you control two or more other lands" in text or "hand contains" in text
            or re.search(r"enters?[^.\n]*tapped unless you reveal", text)):
        quality *= .45
    if "two or more opponents" in text:
        quality = 1.0
    if card.get("layout") == "modal_dfc":
        quality *= .87
    if "pay 1 life" in text or re.search(r"deals 1 damage to you|you lose 1 life", text):
        quality *= .9
    if "pay 2 life" in text:
        quality *= .88
    if re.search(r"deals [23] damage to you|opponent gains control|opponent creates? a .* token|"
                 r"sacrifice (?:this land|it) unless|if you control no (?:creatures|artifacts)|"
                 r"doesn't untap|return this land to its owner's hand", text):
        quality *= .50
    return quality


def _land_colors(card: dict, colors: set[str]) -> set[str]:
    text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold())
    direct = {color for subtype, color in _BASIC_COLORS.items()
              if subtype in card["type_line"].casefold().split()}
    for activation in _LAND_MANA.findall(text):
        if "spend this mana only" in activation or "activate only" in activation:
            continue
        mana = activation.split(".")[0].strip()
        if re.fullmatch(r"one mana of any color(?: in your commander's color identity)?", mana):
            direct.update(colors)
        elif re.fullmatch(r"\{[wubrgc]\}(?:(?: or )?\{[wubrgc]\})*", mana):
            direct.update(re.findall(r"\{([wubrg])\}", mana))
    return {color.upper() for color in direct} & colors


def _land_priority(card, sources, quality, early, fetch_penalty):
    text = _oracle(card).casefold()
    fixing = sum(1.7 * max(.28, 1 - early[color] / 24) for color in sorted(sources))
    utility = (.62 if "time counter" in text else .22 if any(
        phrase in text for phrase in ("channel —", "draw a card", "gains haste")) else 0)
    return fixing * quality + .16 * quality + utility - fetch_penalty


def _mana_base(metadata: dict, colors: set[str]) -> tuple[Counter, dict]:
    pool = {key: card for key, card in metadata.items() if _is_land(card)}
    basic_keys = [land for land, color in _BASIC_COLORS.items() if color in colors]
    if not all(key in pool for key in basic_keys):
        raise ValueError("The Scryfall catalog has no verified basic land for a commander color")
    lands = Counter(dict.fromkeys(basic_keys, 3))
    qualities = {key: _land_quality(card) for key, card in pool.items()}
    # Establish untapped typed duals before ranking fetchlands. A land that can
    # search the library only contributes colors from targets in the exact deck.
    for pair in combinations(sorted(colors), 2):
        choices = [key for key, card in pool.items() if qualities[key] >= .75
                   and set(pair) <= _land_information(card)["direct"]
                   and sum(subtype in card["type_line"].casefold().split() for subtype in _BASIC_COLORS) == 2]
        if choices:
            lands[max(choices, key=lambda key: (qualities[key], key))] = 1
    anchors = frozenset(lands)
    sources = {}
    fetches = set()
    for key, card in pool.items():
        text = re.sub(r"\([^)]*\)", "", _oracle(card).casefold())
        if _FETCH.search(text):
            fetches.add(key)
            sources[key] = _land_sources(key, anchors, metadata, colors)
            continue
        sources[key] = _land_colors(card, colors)
    early = Counter()
    chosen_fetches = 0
    for key, quantity in lands.items():
        for color in sources[key]:
            early[color] += quantity * qualities[key]
    for _ in range(sum(lands.values()), LAND_SLOTS):
        def priority(key):
            fetch_penalty = .42 if key in fetches and chosen_fetches >= 7 else 0
            return _land_priority(pool[key], sources[key], qualities[key], early, fetch_penalty)

        key = max((key for key in pool if sources[key] and (key not in lands or key in basic_keys)),
                  key=lambda key: (priority(key), key))
        lands[key] += 1
        chosen_fetches += key in fetches
        for color in sources[key]:
            early[color] += qualities[key]
    return lands, {color: round(early[color], 1) for color in sorted(colors)}


def _relative_scores(rank, context, spells):
    raw = {key: float(value) for key, value in rank(tuple(context)).items() if key in spells and math.isfinite(value)}
    if not raw:
        raise ValueError("The neural ranker returned no scores for the legal spell pool")
    ordered = sorted(raw.values())
    center = ordered[len(ordered) // 2]
    scale = max(ordered[int(.95 * (len(ordered) - 1))] - center, 1e-6)
    return {key: max(-2.0, min(4.0, (score - center) / scale)) for key, score in raw.items()}


def _fit(spell: Spell, model_score: float, coverage: Counter, *, emphasis: float = 1.0) -> float:
    bonuses = {"Time travel": .9, "Suspend": .58, "Exile payoffs": .64, "Time counters": .48,
               "Library setup": .38, "Attack setup": .24}
    return model_score + spell.efficiency + emphasis * sum(
        bonuses[role] / (1 + .12 * coverage[role]) for role in spell.signals)


@dataclass(frozen=True)
class ConstructionPolicy:
    goals: Mapping[str, int]
    limits: Mapping[str, int]
    classify: Callable[[dict], Spell]
    fit: Callable[..., float]
    notes: Mapping[str, str]
    score_explanation: str = "Higher scores rank ahead within this construction run."


class ConstructionSearchError(ValueError):
    """A search start could not finish a deck while respecting its constraints."""


BASELINE_POLICY = ConstructionPolicy(
    GOALS, LIMITS, _spell, _fit, STRATEGY_NOTES,
    "Mean masked-context neural fit with adjustments for direct rules interactions. Higher scores rank ahead within this run.",
)


def _classify_oracle_spell(card):
    return mechanics.classify(card, _spell(card))


def _oracle_mechanics_fit(spell, model_score, coverage, *, emphasis=1.0):
    return model_score + spell.efficiency + emphasis * mechanics.mechanic_bonus(spell, coverage)


ORACLE_POLICY = ConstructionPolicy(
    GOALS, LIMITS, _classify_oracle_spell, _oracle_mechanics_fit, {**STRATEGY_NOTES, **mechanics.NOTES},
    "The fit score includes graded suspend, time-counter, and exile interactions.",
)


def _within_limits(spell: Spell, coverage: Counter, limits, removed: frozenset[str] = frozenset()) -> bool:
    return all(coverage[role] - (role in removed) < limit for role, limit in limits.items() if role in spell.roles)


def _pick_spells(spells: dict[str, Spell], rank, start: int, policy: ConstructionPolicy,
                 *, spell_slots: int = SPELL_SLOTS) -> list[str]:
    selected = []
    used = set()
    coverage = Counter()
    pools = {role: [key for key, spell in spells.items() if role in spell.roles] for role in policy.goals}
    first_roles = ((), ("Suspend", "Time travel", "Exile payoffs"), ("Early attackers", "Rose support"))[start % 3]
    order = (*[role for role in first_roles if role in policy.goals],
             *(role for role in policy.goals if role not in first_roles))
    expensive = 0
    for slot in range(spell_slots):
        if slot % 7 == 0:
            scores = _relative_scores(rank, selected, spells)
        role = next((role for role in order if coverage[role] < policy.goals[role]), None)
        pool = pools[role] if role else scores
        candidates = (key for key in pool if key not in used and key in scores
                      and _within_limits(spells[key], coverage, policy.limits)
                      and (role or expensive < 9 or not spells[key].expensive))
        key = max(candidates, key=lambda key: (
            policy.fit(spells[key], scores[key], coverage, emphasis=(1.0, .8, 1.2)[start % 3])
            - .17 * (expensive >= 7 and spells[key].expensive), key), default=None)
        if key is None:
            raise ConstructionSearchError(f"The scored catalog could not fill the {role or 'remaining spell'} slots")
        selected.append(key)
        used.add(key)
        coverage.update(spells[key].roles)
        expensive += spells[key].expensive
    missing = [role for role, minimum in policy.goals.items() if coverage[role] < minimum]
    if missing:
        raise ConstructionSearchError("Unfilled construction targets: " + ", ".join(missing))
    return selected


def _score_list(selected, spells, rank, policy: ConstructionPolicy):
    # Each inclusion is evaluated with that card masked from the context.
    scores = {}
    coverage = Counter(role for key in selected for role in spells[key].roles)
    for fold in range(4):
        context = [key for index, key in enumerate(selected) if index % 4 != fold]
        predicted = _relative_scores(rank, context, spells)
        for key in selected[fold::4]:
            scores[key] = policy.fit(spells[key], predicted[key], coverage)
    return sum(scores.values()) / len(selected), scores, coverage


def _improve_list(selected, spells, rank, fit, scores, coverage, policy: ConstructionPolicy):
    present = set(selected)
    expensive = sum(spells[key].expensive for key in selected)
    best = None
    for fold in range(4):
        context = [key for index, key in enumerate(selected) if index % 4 != fold]
        prediction = _relative_scores(rank, context, spells)
        alternatives = sorted(((policy.fit(spells[key], score, coverage), key) for key, score in prediction.items()
                               if key not in present), reverse=True)[:140]
        for index in range(fold, len(selected), 4):
            outgoing = selected[index]
            for estimate, incoming in alternatives:
                gain = estimate - scores[outgoing]
                if gain <= (best[0] if best else .06):
                    break
                next_expensive = expensive + spells[incoming].expensive - spells[outgoing].expensive
                if next_expensive > max(9, expensive):
                    continue
                if not _within_limits(spells[incoming], coverage, policy.limits, spells[outgoing].roles):
                    continue
                dropped = spells[outgoing].roles - spells[incoming].roles
                if any(coverage[role] <= policy.goals[role] for role in dropped & policy.goals.keys()):
                    continue
                best = (gain, index, incoming)
                break
    if best:
        _, index, incoming = best
        candidate = [*selected]
        candidate[index] = incoming
        improved, new_scores, new_coverage = _score_list(candidate, spells, rank, policy)
        if improved > fit:
            return improved, candidate, new_scores, new_coverage, 1
    return fit, selected, scores, coverage, 0


def build_deck(catalog: Mapping[str, dict], rank: Callable[[tuple[str, ...]], Mapping[str, float]],
               *, starts: int = 3, policy: ConstructionPolicy = ORACLE_POLICY) -> dict:
    """Build a 100-card list from the exact legal pool and a context-aware ranker."""
    if not 1 <= starts <= 3:
        raise ValueError("Construction supports one to three deterministic search starts")
    commanders = tuple(map(card_key, COMMANDER_NAMES))
    if any(key not in catalog for key in commanders):
        raise ValueError("The catalog is missing one or both prototype commanders")
    colors, _ = _commander_colors(commanders, catalog)
    metadata = _usable_catalog(catalog, colors)
    if any(key not in metadata for key in commanders):
        raise ValueError("The prototype commanders must have verified legal metadata")
    lands, early_colors = _mana_base(metadata, colors)
    spells = {key: policy.classify(card) for key, card in metadata.items() if key not in commanders and not _is_land(card)}
    candidates = []
    failed_starts = []
    for start in range(starts):
        try:
            selected = _pick_spells(spells, rank, start, policy)
        except ConstructionSearchError as error:
            failed_starts.append({"start": start + 1, "reason": str(error)})
            continue
        fit, scores, coverage = _score_list(selected, spells, rank, policy)
        candidates.append((fit, selected, scores, coverage))
    if not candidates:
        raise ConstructionSearchError("Every search start failed. " + " | ".join(row["reason"] for row in failed_starts))
    fit, selected, scores, coverage = max(candidates, key=lambda candidate: candidate[0])
    fit, selected, scores, coverage, improvements = _improve_list(selected, spells, rank, fit, scores, coverage, policy)
    return _deck_result(metadata, commanders, lands, early_colors, spells, selected, scores, fit, coverage, policy,
                        starts, [round(row[0], 4) for row in candidates], failed_starts, improvements, colors)


def _deck_result(metadata, commanders, lands, early_colors, spells, selected, scores, fit, coverage, policy,
                 starts, candidate_fit_scores, failed_starts, improvements, colors):
    counts = Counter(commanders) + lands + Counter(selected)
    validation = _validate(counts, commanders, metadata, metadata)
    validation["checks"]["legal_catalog"] = validation["checks"].pop("sampled_pool")
    if not validation["valid"]:
        raise ValueError("Constructed deck failed validation: " + ", ".join(validation["errors"]))
    mana = _mana_diagnostics(counts, metadata, commanders)
    mana["early_colored_land_sources_adjusted"] = early_colors
    rows = []
    for key in [*commanders, *sorted(lands), *sorted(selected)]:
        card = metadata[key]
        spell = spells.get(key)
        roles = (["Commander"] if key in commanders else ["Lands"] if spell is None
                 else [*spell.signals, *sorted(spell.roles.difference(spell.signals))])
        evidence = next((policy.notes[role] for role in roles if role in policy.notes), None)
        if spell is None and key in lands and "time counter" in _oracle(card).casefold():
            evidence = STRATEGY_NOTES["Time counters"]
        rows.append({"name": card["name"], "quantity": counts[key], "type_line": card["type_line"],
                     "mana_cost": card.get("mana_cost", ""), "mana_value": card["cmc"],
                     "roles": roles, "primary_role": roles[0], "oracle_id": card.get("oracle_id"),
                     "synergy_score": round(scores[key], 4) if key in scores else None, "synergy_signal": evidence})
    tagged = "\n".join(f"{row['quantity']} {row['name']}" + (" *CMDR*" if row["primary_role"] == "Commander" else "")
                       for row in rows) + "\n"
    result = {"status": "generated", "name": " + ".join(COMMANDER_NAMES), "card_count": sum(counts.values()),
              "commanders": rows[:2], "cards": rows, "deck_text": tagged, "export_text": tagged,
              "synergy_score": round(fit, 4), "cardlist_sha256": cardlist_hash(rows), "validation": validation,
              "mana_diagnostics": mana, "construction": {"candidate_spell_count": len(spells),
                  "spell_role_counts": {role: coverage[role] for role in (*policy.goals, *policy.limits, "Time counters", "Library setup", "Attack setup")},
                  "spell_role_targets": dict(policy.goals), "spell_role_limits": dict(policy.limits), "color_identity": sorted(colors),
                  "search_starts": starts, "candidate_fit_scores": candidate_fit_scores,
                  "accepted_refinements": improvements,
                  "failed_starts": failed_starts,
                  "score_explanation": policy.score_explanation,
              }}
    validation["external_url"] = deck_url(result)
    return result
