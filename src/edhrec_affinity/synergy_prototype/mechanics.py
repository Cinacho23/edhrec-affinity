"""Read Oracle clauses for the Doctor and Rose's actual game actions."""

from dataclasses import replace
import re

from .catalog import card_faces


NOTES = {
    "Repeatable suspend": "Repeatedly puts another card into suspend, building material for Rose and the Doctor.",
    "Recurring suspend": "Returns to suspend after resolving, giving the deck another countdown without spending a new card.",
    "Suspend acceleration": "Can remove time counters from suspended cards and bring a waiting spell back sooner.",
    "Permanent time counters": "Can supply another permanent with time counters for Rose to count on attack.",
    "Permanent counter payoff": "Gets an additional effect from time counters on your permanents.",
    "Commander exile payoff": "Lets a commander create an extra effect when you cast a spell from exile.",
    "Extra upkeep": "An extra upkeep supplies another suspend countdown trigger that turn.",
    "Extra combat": "An additional combat can give the commanders another attack trigger.",
    "Exile access": "Provides extra options to play from exile.",
    "Exile cost support": "Can discount spells that still have a cost when cast from exile.",
    "Proliferate support": "Can add an existing time counter to a permanent such as Rose.",
}

_OLD_THEME = {"Suspend", "Time travel", "Time counters", "Exile payoffs", "Direct synergy"}
_PRINTED_SUSPEND = re.compile(r"^suspend (?:\d+|x)\s*[—–-]", re.M)
_GRANTS_SUSPEND = re.compile(r"\b(?:gains?|has) suspend\b|\b(?:they|those cards) have suspend\b")
_OWN_EXILE_SOURCE = re.compile(
    r"\bexile (?:[^.\n:]{0,100}\bfrom (?:your hand|your graveyard|the top of your library)\b"
    r"|the top [^.\n]{0,36}\bof your library\b"
    r"|this (?:spell|card) with [^.\n]{1,60}\btime counters?\b)"
)
_SELF_EXILE_ON_CAST = re.compile(r"\byou cast this spell\b[^.\n]{0,70}\bexile it with [^.\n]{1,60}\btime counters?\b")
_REPEATABLE = re.compile(r"\bwhenever\b|\bat the beginning of\b|(?:\{|\bexile\b)[^.\n]{0,120}:")
_EXILE_CAST = re.compile(r"\bcast\b[^.;\n]{0,64}\bfrom (?:exile|anywhere other than your hand)\b")
_EXILE_BENEFIT = re.compile(
    r"\b(?:deals?|draws?|cop(?:y|ies)|cascade|demonstrate|creates?|return(?:s)?|gains? [^,.;]{1,28}life)\b"
    r"|\bput\b[^,.;]{0,32}\bcounter\b|\bexile the top\b"
)
_EXILE_COST = re.compile(r"\bless to cast\b|\brather than pay (?:its|the) mana cost\b|\bwithout paying (?:its|their) mana cost\b")
_EXILE_ACCESS = re.compile(r"\byou may (?:play|cast)\b[^.\n]{0,60}\b(?:exiled cards?|those cards|that card)\b")
_TIME_COUNTER_PLACE = r"\bput\b[^.;\n]{0,32}\btime counters?\b[^.;\n]{0,20}\bon\s+"
_TIME_COUNTER_PERMANENT = re.compile(_TIME_COUNTER_PLACE + r"(?:this (?:permanent|artifact|creature|enchantment)|(?:target|each|a) permanent)")
_ON_ENTRY = re.compile(r"\b(?:enters?[^.;\n]{0,28}with[^.;\n]{0,28}time counters?|vanishing \d+)\b")
_COUNTER_PAYOFF = re.compile(
    r"\b(?:number of|for each)\b[^.;\n]{0,25}\btime counters?\b[^.;\n]{0,30}\b(?:on|among)\b"
    r"|\bwhenever you put\b[^.;\n]{0,40}\btime counters? on a permanent you control\b"
)


def _face_signals(face):
    text = re.sub(r"\([^)]*\)", "", face.get("oracle_text", "").casefold())
    clauses = re.split(r"[.\n]", text)
    name = re.escape(face.get("name", "").casefold())
    self_card = rf"(?:{name}|this (?:spell|card))" if name else r"this (?:spell|card)"
    signals = []
    printed = bool(_PRINTED_SUSPEND.search(text))
    exile_source = _OWN_EXILE_SOURCE.search(text) or _SELF_EXILE_ON_CAST.search(text)
    grants = bool(_GRANTS_SUSPEND.search(text) and exile_source and "time counter" in text)
    if grants and _REPEATABLE.search(text):
        signals.append("Repeatable suspend")
    if printed and re.search(rf"\bexile {self_card} with [^.\n]+time counters?\b", text):
        signals.append("Recurring suspend")
    if printed or grants:
        signals.append("Suspend")

    if re.search(r"\btime travel\b", text):
        signals.append("Time travel")
    if (re.search(r"\b(?:suspended cards? you own|target [^.\n]{0,45}suspended card|each suspended card|choose a suspended card)\b", text)
            and re.search(r"\bremove\b[^.;\n]{0,38}\bcounters?\b", text)):
        signals.append("Suspend acceleration")
    if any(_COUNTER_PAYOFF.search(clause) and not _OWN_EXILE_SOURCE.search(clause)
           and "exiled this way" not in clause for clause in clauses):
        signals.append("Permanent counter payoff")
    if (_ON_ENTRY.search(text) or any(_TIME_COUNTER_PERMANENT.search(clause) and "with a time counter on it" not in clause
                                      for clause in clauses)
            or name and re.search(_TIME_COUNTER_PLACE + rf"{name}\b", text)):
        signals.append("Permanent time counters")
    if "time counter" in text or "suspended card" in text:
        signals.append("Time counters")

    cast_clauses = [clause for clause in clauses if _EXILE_CAST.search(clause)]
    if any(_EXILE_BENEFIT.search(clause) for clause in cast_clauses):
        if "commander creatures you own have" in text:
            signals.append("Commander exile payoff")
        signals.append("Exile payoffs")
    elif any(_EXILE_COST.search(clause) for clause in cast_clauses):
        signals.append("Exile cost support")
    if "exile" in text and _EXILE_ACCESS.search(text):
        signals.append("Exile access")
    if "additional upkeep step" in text and "opponent gets" not in text:
        signals.append("Extra upkeep")
    if "additional combat phase" in text and "opponent gets" not in text:
        signals.append("Extra combat")
    if re.search(r"\bproliferate\b", text):
        signals.append("Proliferate support")
    return signals


_DIRECT = {"Suspend", "Time travel", "Suspend acceleration", "Permanent time counters",
           "Permanent counter payoff", "Exile payoffs", "Extra upkeep", "Extra combat"}
_BONUS_GROUPS = (
    {"Repeatable suspend": 1.85, "Recurring suspend": 1.3, "Suspend": .46},
    {"Time travel": 1.1, "Suspend acceleration": 1.32, "Extra upkeep": 1.3},
    {"Permanent counter payoff": 1.22, "Permanent time counters": .68, "Proliferate support": .28},
    {"Commander exile payoff": 2.7, "Exile payoffs": 1.28, "Exile access": .28, "Exile cost support": .06},
    {"Library setup": .38, "Attack setup": .26, "Extra combat": .88},
)


def classify(card, spell):
    """Replace theme roles while keeping independent deck-construction roles."""
    theme = dict.fromkeys(signal for face in card_faces(card) for signal in _face_signals(face))
    preserved = tuple(signal for signal in spell.signals if signal not in _OLD_THEME)
    signals = tuple(dict.fromkeys((*theme, *preserved)))
    roles = set(spell.roles - _OLD_THEME) | set(signals)
    if _DIRECT.intersection(signals):
        roles.add("Direct synergy")
    return replace(spell, roles=frozenset(roles or {"Leftover"}), signals=signals)


def mechanic_bonus(spell, coverage):
    """Reward separate useful actions, then taper the reward for repeated roles."""
    return sum(max((group[signal] / (1 + .12 * coverage.get(signal, 0))
                    for signal in spell.signals if signal in group), default=0)
               for group in _BONUS_GROUPS)
