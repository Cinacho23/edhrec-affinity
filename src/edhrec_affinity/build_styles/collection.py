"""Resumable enumeration of EDHREC's public deck index, with no count ceiling."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import random
import time

import httpx

from .sources import SourceBlocked, checked_identifier, parse_preview


def parse_sample_decks(value: str) -> int | None:
    if value.casefold() == "all":
        return None
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use all or a nonnegative preview count") from error
    if number < 0:
        raise argparse.ArgumentTypeError("Preview count cannot be negative")
    return number


def collect_previews(cache, slug: str, *, limit: int | None, seed: int, progress_path: Path,
                     max_seconds: float = 0, monotonic=time.monotonic) -> tuple[list[dict], dict]:
    """Keep a frozen, shuffled frame and parsed lists across jobs, not just raw HTML.

    Counts describe cumulative attempts against this snapshot. Time slicing never
    discards already collected decks or silently reduces the model's input sample.
    """
    started = monotonic()
    if progress_path.exists():
        with gzip.open(progress_path, "rt", encoding="utf-8") as handle:
            state = json.load(handle)
        if (state.get("schema_version") != 1 or state.get("commander_slug") != slug
                or state.get("seed") != seed or not isinstance(state.get("results"), dict)
                or not isinstance(state.get("frame"), list)):
            raise ValueError("Collection checkpoint identity or schema mismatch")
    else:
        table = cache.get("decks.json", f"https://json.edhrec.com/pages/decks/{slug}.json")["table"]
        rows = {checked_identifier(row["urlhash"], deck=True): row for row in table}
        frame = list(rows.values())
        random.Random(seed).shuffle(frame)  # Before inspecting cards or source tags.
        state = {"schema_version": 1, "commander_slug": slug, "seed": seed, "frame": frame, "results": {}}

    def save():
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = progress_path.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as handle:
            json.dump(state, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        temporary.replace(progress_path)

    target = state["frame"] if limit is None else state["frame"][:limit]
    failures, visited, newly_attempted = [], 0, 0
    try:
        for row in target:
            identifier = checked_identifier(row["urlhash"], deck=True)
            if identifier in state["results"]:
                visited += 1
                continue
            if max_seconds and monotonic() - started >= max_seconds:
                break
            try:
                data = cache.get(f"previews/{identifier}.html", f"https://edhrec.com/deckpreview/{identifier}", html=True)
                deck = parse_preview(data, identifier)
                deck["source_bracket"] = row.get("bracket")
                state["results"][identifier] = {"deck": deck, "provenance": list(cache.provenance[-1:])}
            except SourceBlocked as error:
                failures.append({"deck": identifier, "error": str(error)})
                break  # Retry this ID after the shared access cooldown.
            except httpx.HTTPError as error:
                failures.append({"deck": identifier, "error": str(error)})
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {404, 410}:
                    state["results"][identifier] = {"error": str(error)}
                else:
                    break  # Transient network/server failures are not permanent exclusions.
            except FileNotFoundError as error:
                failures.append({"deck": identifier, "error": str(error)})
                break  # Cache-only runs must not mark missing pages as attempted forever.
            except (ValueError, KeyError, TypeError) as error:
                state["results"][identifier] = {"error": str(error)}
            visited += 1
            newly_attempted += 1
            if newly_attempted % 10 == 0:
                save()
                print(f"Read {visited}/{len(target)} deck previews in this snapshot", flush=True)
    finally:
        save()
    records = [state["results"][row["urlhash"]] for row in target if row["urlhash"] in state["results"]]
    decks = [record["deck"] for record in records if "deck" in record]
    # Include source hashes/timestamps for lists loaded by earlier jobs too.
    known_urls = {item.get("url") for item in cache.provenance}
    for record in records:
        for item in record.get("provenance", []):
            if item.get("url") not in known_urls:
                cache.provenance.append(item)
                known_urls.add(item.get("url"))
    failures.extend({"deck": row["urlhash"], "error": state["results"][row["urlhash"]]["error"]}
                    for row in target if "error" in state["results"].get(row["urlhash"], {}))
    return decks, {
        "sample_limit": limit, "target_mode": "all" if limit is None else "sample",
        "frame_decks": len(state["frame"]), "target_decks": len(target),
        "previews_attempted": len(records), "previews_loaded": len(decks),
        "new_previews_attempted": newly_attempted, "complete": len(records) == len(target),
        "failures": failures,
    }
