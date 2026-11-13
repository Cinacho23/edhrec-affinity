#!/usr/bin/env python3
"""Exercise production WebKit collection on one gallery and one known preview.

This explicit diagnostic never reads, releases or replaces collection controls,
and never dispatches a collector. Any denied or invalid response ends the test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import time

from edhrec_affinity.build_styles.sources import Cache, RequestLimiter, parse_preview

COMMANDER_SLUG = "the-tenth-doctor-rose-tyler"
PREVIEW_ID = "KgsZXJRpedAVHaE_bqT2UA"
EXPECTED_COMMANDERS = {"The Tenth Doctor", "Rose Tyler"}
GALLERY_URL = f"https://edhrec.com/commanders/{COMMANDER_SLUG}/decks"
PREVIEW_URL = f"https://edhrec.com/deckpreview/{PREVIEW_ID}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_gallery(payload: dict) -> dict:
    if (payload.get("commander_slug") != COMMANDER_SLUG or payload.get("index_complete") is not True
            or isinstance(payload.get("displayed_total"), bool)
            or not isinstance(payload.get("displayed_total"), int)):
        raise ValueError("The gallery's commander identity and displayed population are not verified")
    rows = payload.get("table")
    if not isinstance(rows, list) or not rows:
        raise ValueError("The commander gallery contains no deck identifiers")
    identifiers = [row.get("urlhash") for row in rows if isinstance(row, dict)]
    if (len(identifiers) != len(rows) or any(not isinstance(value, str) or not value for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError("The commander gallery contains invalid or duplicate identifiers")
    if PREVIEW_ID not in identifiers:
        raise ValueError("The known preview is missing from its commander's gallery")
    if len(rows) != payload["displayed_total"]:
        raise ValueError("The gallery does not contain every deck in the displayed population")
    return {"index_rows": len(rows), "unique_deck_ids": len(set(identifiers)),
            "known_preview_in_gallery": True, "source_commander_verified": True,
            "displayed_total": payload["displayed_total"], "complete_gallery_index_verified": True}


def check_preview(payload: dict) -> tuple[dict, dict]:
    # The source ID is checked before parse_preview, whose optional deck_id
    # argument can otherwise replace a wrong source ID in normalized output.
    if payload.get("urlhash") != PREVIEW_ID:
        raise ValueError("The source preview identifier differs from the requested deck")
    deck = parse_preview(payload)
    if set(deck["commanders"]) != EXPECTED_COMMANDERS or len(deck["commanders"]) != 2:
        raise ValueError("The source preview does not contain the intended commander pair")
    cards = deck["cards"]
    if not cards or any(isinstance(row.get("quantity"), bool)
                        or not isinstance(row.get("quantity"), int) or row["quantity"] <= 0 for row in cards):
        raise ValueError("The source preview has invalid mainboard quantities")
    total = sum(row["quantity"] for row in cards) + len(deck["commanders"])
    if total != 100:
        raise ValueError(f"The known deck has {total} cards including commanders, expected 100")
    return {"source_preview_id_verified": True, "commanders": deck["commanders"],
            "mainboard_entries": len(cards), "cards_including_commanders": total,
            "source_url": deck["source_url"]}, deck


def run_test(output: Path, *, cache_factory=Cache, now=utcnow) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    receipt = {
        "started_at": now(), "transport": "webkit", "passed": False,
        "scope": "one commander gallery and one known individual preview",
        "configured_requests_per_second": 0.1,
        "retries": 0, "checkpoint_controls_changed": False, "collector_dispatched": False,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_commit": os.environ.get("GITHUB_SHA"), "checks": [],
        "limitations": ["This test does not establish a sustainable rate or all-commanders coverage.",
                        "It tests document HTML with background resources blocked, not full page rendering."],
    }
    receipt_path = output / "receipt.json"

    def save() -> None:
        receipt_path.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    cache = None
    limiter = RequestLimiter(max_rate=0.1, start_rate=0.1, deadline=time.monotonic() + 120)
    save()
    try:
        if os.environ.get("BUILD_STYLE_EDHREC_TRANSPORT") != "webkit":
            raise ValueError("Set BUILD_STYLE_EDHREC_TRANSPORT=webkit for this explicit browser diagnostic")
        receipt["playwright_version"] = importlib.metadata.version("playwright")
        cache = cache_factory(output / "source-cache", fetch=True, limiter=limiter)
        gallery = {"kind": "gallery", "url": GALLERY_URL, "started_at": now()}
        receipt["checks"].append(gallery)
        save()
        gallery.update(check_gallery(cache.get_deck_index(COMMANDER_SLUG, force_refresh=True)))
        gallery.update(valid=True, completed_at=now(), provenance=cache.provenance_for(GALLERY_URL))
        save()
        preview = {"kind": "preview", "url": PREVIEW_URL, "started_at": now()}
        receipt["checks"].append(preview)
        save()
        validation, deck = check_preview(cache.get_preview(PREVIEW_ID, force_refresh=True))
        preview.update(validation, valid=True, completed_at=now(), provenance=cache.provenance_for(PREVIEW_URL))
        (output / "sample-deck.json").write_text(json.dumps(deck, indent=2) + "\n", encoding="utf-8")
        lines = [f"1 {name}" for name in deck["commanders"]]
        lines.extend(f"{row['quantity']} {row['name']}" for row in deck["cards"])
        (output / "sample-deck.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        receipt["passed"] = True
    except Exception as error:
        receipt.update(error_type=type(error).__name__, error=str(error)[:700])
    finally:
        if cache is not None:
            try:
                cache.close()
            except Exception as error:
                receipt.update(passed=False, close_error_type=type(error).__name__, close_error=str(error)[:300])
        receipt.update(completed_at=now(), request_statistics=limiter.stats)
        save()
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = run_test(args.output)
    print(json.dumps(receipt, indent=2, allow_nan=False))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
