#!/usr/bin/env python3
"""Export generated package decks, primers and per-card EDHREC ranking evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def export_decks(report: dict, output: Path) -> list[dict]:
    analysis = report["deck_analysis"]
    models = [("auto", analysis["packages"]),
              *[(f"{model['components']}-packages", model["packages"])
                for model in analysis.get("alternative_models", [])]]
    summaries = []
    for model_name, packages in models:
        for index, package in enumerate(packages, 1):
            deck = package.get("generated_deck", {})
            if deck.get("status") != "generated":
                continue
            folder = output / model_name
            folder.mkdir(parents=True, exist_ok=True)
            stem = folder / f"package-{index}"
            stem.with_suffix(".txt").write_text(deck["export_text"], encoding="utf-8")
            stem.with_suffix(".primer.md").write_text(deck["primer"], encoding="utf-8")
            with stem.with_suffix(".ranking.csv").open("w", encoding="utf-8", newline="") as handle:
                fields = ["name", "quantity", "average_inclusion", "average_lift", "member_inclusion", "tags"]
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in deck["cards"]:
                    writer.writerow({field: "; ".join(row.get("tags", [])) if field == "tags" else row.get(field)
                                     for field in fields})
            measured = [row for row in deck["cards"]
                        if "Commander" not in row.get("tags", []) and row.get("average_lift") is not None]
            highest = max(measured, key=lambda row: row["average_lift"], default=None)
            summaries.append({"model": model_name, "package": index, "card_count": deck["card_count"],
                              "highest_average_lift": {"name": highest["name"], "lift": highest["average_lift"]} if highest else None,
                              "deck_file": str(stem.with_suffix(".txt").resolve()),
                              "ranking_file": str(stem.with_suffix(".ranking.csv").resolve())})
    (output / "deck_summary.json").parent.mkdir(parents=True, exist_ok=True)
    (output / "deck_summary.json").write_text(json.dumps(summaries, indent=2, allow_nan=False), encoding="utf-8")
    return summaries


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("report", type=Path)
    cli.add_argument("--output-dir", type=Path)
    args = cli.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    summaries = export_decks(report, args.output_dir or args.report.parent / "decks")
    print(json.dumps(summaries, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
