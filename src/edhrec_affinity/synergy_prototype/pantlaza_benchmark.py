"""Simulate Pantlaza's opening mana and count proactive discover 4 hits."""

import argparse
import json
from pathlib import Path

from ..build_styles.deckbuilding import _is_land
from ..build_styles.sources import card_key
from .catalog import load_catalog
from .opening_benchmark import opening_turns
from .pantlaza import classify_spell

def benchmark(report, catalog, *, trials=6000, seed=197):
    """Model land drops, early mana and one ramp play per turn."""
    eligible = proactive = 0
    for row in report["deck"]["cards"]:
        if row.get("primary_role") == "Commander" or "Commander" in row.get("roles", ()):
            continue
        card = catalog[card_key(row["name"])]
        if not _is_land(card):
            eligible += int(card["cmc"] <= 4) * row["quantity"]
            proactive += ("Discover 4 plays" in classify_spell(card).roles) * row["quantity"]
    turns = opening_turns(report, catalog, classify_spell, creature_type="Dinosaur", trials=trials, seed=seed)
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
