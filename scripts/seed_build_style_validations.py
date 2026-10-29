#!/usr/bin/env python3
"""Add repository observations to a restored checkpoint without losing newer checks."""

import argparse
import json
from pathlib import Path
import re

from rebuild_generated_decks import atomic_json, read_json


def seed_validations(source: Path, destination: Path) -> int:
    copied = 0
    for path in sorted(source.glob("*.json")):
        if not re.fullmatch(r"[0-9a-f]{64}(?:\.[0-9a-f]{64})?\.json", path.name):
            continue
        observed = read_json(path)
        target = destination / path.name
        if target.exists():
            # Artifact observations can be newer than the merge that carries
            # a regression example. Never roll an observation backward.
            previous = read_json(target)
            from datetime import datetime
            try:
                checked = datetime.fromisoformat(observed["checked_at"].replace("Z", "+00:00"))
                existing = datetime.fromisoformat(previous["checked_at"].replace("Z", "+00:00"))
                if checked.tzinfo is None or existing.tzinfo is None or checked <= existing:
                    continue
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        atomic_json(target, observed)
        copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps({"seeded_observations": seed_validations(args.source, args.destination)}))


if __name__ == "__main__":
    main()
