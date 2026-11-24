"""Build the legal inference catalog and resolve historical deck names to it."""

from collections import defaultdict
import gzip
import json
import re

from edhrec_affinity.build_styles.card_metadata import _metadata, metadata_aliases
from edhrec_affinity.build_styles.sources import card_key


def load_catalog(path, snapshot_at, alias_cache=None):
    """Read Scryfall's gzipped Oracle Cards JSONL export."""
    cards = {}
    indexed_names = defaultdict(set)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            # The representative printing can be digital even for a legal paper card.
            if raw.get("legalities", {}).get("commander") != "legal" or not raw.get("oracle_id"):
                continue
            card = _metadata(raw, snapshot_at)
            card["colors"] = raw.get("colors", [])
            card["color_indicator"] = raw.get("color_indicator", [])
            for face, original in zip(card["card_faces"], raw.get("card_faces", ())):
                face["colors"] = original.get("colors", [])
                face["color_indicator"] = original.get("color_indicator", [])
            images = raw.get("image_uris", {}) or (raw.get("card_faces") or [{}])[0].get("image_uris", {})
            card["image_url"] = images.get("normal")
            key = card_key(card["name"])
            if key in cards and cards[key]["oracle_id"] != card["oracle_id"]:
                raise ValueError(f"Conflicting legal Oracle cards for {card['name']}")
            cards[key] = card
            for alias in metadata_aliases(card):
                indexed_names[alias].add(key)
    if alias_cache:
        by_id = {card["oracle_id"]: key for key, card in cards.items()}
        with alias_cache.open(encoding="utf-8") as handle:
            cached_cards = json.load(handle)["cards"].values()
        for card in cached_cards:
            key = by_id.get(card.get("oracle_id"))
            if key:
                for alias in metadata_aliases(card):
                    indexed_names[alias].add(key)
    return cards, {alias: next(iter(keys)) for alias, keys in indexed_names.items() if len(keys) == 1}


def card_faces(card):
    return card["card_faces"] or [card]


def card_document(card):
    """Use functional text, preserving face boundaries and printed mana tokens."""
    documents = []
    for face in card_faces(card):
        rules = face.get("oracle_text", "").lower()
        name = face.get("name", "").lower()
        if name:
            rules = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "this card", rules)
        mana = " ".join("mana_" + symbol.replace("/", "_")
                        for symbol in re.findall(r"\{([^}]+)\}", face.get("mana_cost", "").lower()))
        documents.append(f"{face.get('type_line', '')}\n{mana}\n{rules}")
    return "\nother_face\n".join(documents) + "\n" + " ".join(card["keywords"])
