"""Extract exact creature subtype and action facts for the neural ranker."""

import re

from ..synergy_prototype.catalog import card_faces


ACTIONS = ("enter", "cast", "attack", "combat_damage", "die")
FEATURE_VERSION = 1
FEATURE_COUNT = 3 * len(ACTIONS) + 3

_PLURALS = {
    "Dwarf": "Dwarves", "Elf": "Elves", "Mouse": "Mice", "Werewolf": "Werewolves", "Wolf": "Wolves",
}
_TRIGGER = re.compile(r"\bwhen(?:ever)?\s+([^,.;\n]+)", re.I)
_OPPOSING = re.compile(
    r"^\s*(?:an?|each|your|target) opponent\b|\ban opponent controls\b|\b(?:attacks?|blocks?) you\b", re.I,
)
_EVENTS = {
    "enter": re.compile(r"\benters?\b", re.I),
    "cast": re.compile(r"\b(?:you cast|you've cast)\b", re.I),
    "attack": re.compile(r"\battacks?\b", re.I),
    "combat_damage": re.compile(r"\bdeals? combat damage\b", re.I),
    "die": re.compile(r"\bdies?\b", re.I),
}
_HELPERS = {
    "enter": re.compile(
        r"\bexile [^.\n]{0,110}\bcreatures? you control\b[\s\S]{0,160}"
        r"\breturn (?:it|them|that card|those cards) to the battlefield\b"
        r"|\breturn target creature card from your graveyard to the battlefield\b"
        r"|\bcast creature spells as though they had flash\b", re.I,
    ),
    "cast": re.compile(r"\bcreature spells you cast cost\b[^.\n]{0,45}\bless\b", re.I),
    "attack": re.compile(
        r"\b(?:creatures you control have haste|target creature gains haste|additional combat phase)\b", re.I,
    ),
    "combat_damage": re.compile(
        r"\b(?:creatures you control|target creature) (?:can't be blocked|gains? trample|have trample)\b", re.I,
    ),
    "die": re.compile(r"^\s*sacrifice (?:a |another )creature\s*:", re.I | re.M),
}
_TOKEN = re.compile(r"\bcreates?\b([^.;\n]{0,100})\bcreature tokens?\b", re.I)
_TYPED_BOOST = re.compile(
    r"^(?:\s+(?:creatures?|spells?|cards?))?(?:\s+you (?:control|cast))?\s+"
    r"(?:gets?|gains?|have|has|costs?\b[^.]{0,45}\bless\b|can't be countered)\b", re.I,
)
_TYPE_SELECTION = re.compile(r"\b(?:search your library for|put|return)\b[^.]{0,120}$", re.I)
_TYPE_SELECTION_RESULT = re.compile(r"^\s+[^.]{0,120}\b(?:onto the battlefield|to your hand)\b", re.I)
_CHOSEN_TYPE = re.compile(
    r"\bof the chosen type\b[^.\n]{0,45}\b(?:gets?|gains?|have|has|costs?\b[^.]{0,40}\bless\b|can't be countered)\b"
    r"|\bspend this mana only to cast a creature spell of the chosen type\b", re.I,
)


def _subtypes(face):
    head, _, tail = face.get("type_line", "").partition(" — ")
    types = set(head.split())
    if not types.intersection({"Creature", "Kindred", "Tribal"}):
        return set(), False
    return set(tail.replace("Time Lord", "Time-Lord").split()), "Creature" in types


def _plural(subtype):
    if subtype in _PLURALS:
        return _PLURALS[subtype]
    if subtype.endswith("y") and len(subtype) > 1 and subtype[-2] not in "aeiou":
        return subtype[:-1] + "ies"
    return subtype + ("es" if subtype.endswith(("s", "ch", "sh", "x")) else "s")


def _type_matcher(catalog):
    vocab = {subtype for card in catalog.values() for face in card_faces(card) for subtype in _subtypes(face)[0]}
    forms = {form.replace("Time-Lord", "Time Lord"): subtype
             for subtype in vocab for form in (subtype, _plural(subtype))}
    if not forms:
        return re.compile(r"(?!x)x"), forms
    pattern = "|".join(re.escape(form) for form in sorted(forms, key=lambda form: (-len(form), form)))
    return re.compile(rf"(?<![\w-])(?:{pattern})(?![\w-])"), forms


def _face_text(face):
    text = re.sub(r"\([^()]*\)", "", face.get("oracle_text") or "").replace("’", "'")
    name = face.get("name", "").replace("’", "'")
    for form in dict.fromkeys(filter(None, (name, name.split(",", 1)[0]))):
        text = re.sub(rf"(?<![\w-]){re.escape(form)}(?![\w-])", "this card", text)
    return text


def _card_traits(card, pattern, forms):
    creature, spell, tokens, supports, mentions = set(), set(), set(), set(), set()
    triggers = {action: set() for action in ACTIONS}
    events, helpers = set(), set()
    wildcard_creature = wildcard_spell = chooses = False

    def types_in(text):
        return {forms[match.group()] for match in pattern.finditer(text)}

    for index, face in enumerate(card_faces(card)):
        types, is_creature = _subtypes(face)
        text = _face_text(face)
        if index == 0 or card.get("layout") in {"modal_dfc", "split", "adventure"}:
            changeling = bool(re.search(r"^\s*changeling\b", text, re.I | re.M))
            spell.update(types)
            wildcard_spell |= bool(types) and changeling
            if is_creature:
                creature.update(types)
                wildcard_creature |= changeling
        mentions.update(types_in(text))
        for clause in _TRIGGER.findall(text):
            if _OPPOSING.search(clause):
                continue
            for action, event in _EVENTS.items():
                match = event.search(clause)
                if match:
                    events.add(action)
                    subject = clause[match.end():] if action == "cast" or re.match(r"^you attack with\b", clause, re.I) else clause[:match.start()]
                    triggers[action].update(types_in(subject))
        for token in _TOKEN.finditer(text):
            tokens.update(types_in(token[1]))
        for clause in re.split(r"[.;\n]", text):
            if _OPPOSING.search(clause):
                continue
            for match in pattern.finditer(clause):
                tail = clause[match.end():]
                if _TYPED_BOOST.search(tail) or (
                    _TYPE_SELECTION.search(clause[:match.start()]) and _TYPE_SELECTION_RESULT.search(tail)
                ):
                    supports.add(forms[match.group()])
        helpers.update(action for action, helper in _HELPERS.items() if helper.search(text))
        chooses |= bool(re.search(r"\bchoose a creature type\b", text, re.I) and _CHOSEN_TYPE.search(text))

    return {key: value for key, value in (
        ("creature", "*" if wildcard_creature else sorted(creature)),
        ("spell", "*" if wildcard_spell else sorted(spell)),
        ("tokens", sorted(tokens)),
        ("triggers", {action: sorted(types) for action, types in triggers.items() if types}),
        ("events", [action for action in ACTIONS if action in events]),
        ("helpers", [action for action in ACTIONS if action in helpers]),
        ("supports", sorted(supports)),
        ("chooses", chooses),
        ("mentions", sorted(mentions)),
    ) if value}


def catalog_traits(catalog):
    pattern, forms = _type_matcher(catalog)
    return {key: _card_traits(card, pattern, forms) for key, card in catalog.items()}
