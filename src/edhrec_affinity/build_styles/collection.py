"""Resumable enumeration of EDHREC's public deck index, with no count ceiling."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import random
import time

import httpx

from .sources import SourceBlocked, SourceFormatError, card_key, checked_identifier, parse_preview, source_is_stale, source_time


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


def _row_fingerprint(row: dict) -> str:
    # Price/salt can change independently of the list. EDHREC's savedate is only
    # date precision, so this hint never replaces periodic preview revalidation.
    content = {key: value for key, value in row.items() if key not in {"price", "salt"}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _record_source_at(record: dict):
    return record.get("source_at") or next((item.get("fetched_at") for item in record.get("provenance", [])
                                           if item.get("fetched_at")), None)


def deck_dataset_sha256(decks: list[dict]) -> str:
    """Hash actual analysis inputs, independent of observation dates or prices."""
    digest = hashlib.sha256()
    for deck in sorted(decks, key=lambda item: item.get("id", "")):
        normalized = {
            "id": deck.get("id"), "commanders": sorted(card_key(name) for name in deck.get("commanders", [])),
            "source_bracket": deck.get("source_bracket"),
            "cards": sorted((card_key(card["name"]), card["quantity"], card.get("type_line", ""))
                            for card in deck["cards"]),
        }
        digest.update(json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def collect_previews(cache, slug: str, *, limit: int | None, seed: int, progress_path: Path,
                     max_seconds: float = 0, monotonic=time.monotonic,
                     index_max_age_seconds: float | None = None,
                     preview_max_age_seconds: float | None = None,
                     freshness_target_hours: float = 96, now: datetime | None = None,
                     preview_workers: int = 1, trust_index_versions: bool = False) -> tuple[list[dict], dict]:
    """Keep parsed lists across jobs and optionally reconcile a changing source.

    Counts describe cumulative attempts against this snapshot. Time slicing never
    discards already collected decks or silently reduces the model's input sample.
    With TTLs, unchanged IDs retain their lists until their own observation ages
    out; new/changed IDs take priority and removed IDs leave the analysis input.
    Omitting TTLs preserves the original frozen-snapshot behavior.
    """
    started = monotonic()
    if type(preview_workers) is not int or preview_workers < 1:
        raise ValueError("Preview workers must be a positive integer")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Collection time must include a timezone")
    for age in (index_max_age_seconds, preview_max_age_seconds, freshness_target_hours * 3600):
        if age is not None and (not isinstance(age, (int, float)) or not 0 <= age < float("inf")):
            raise ValueError("Source age limits must be finite and nonnegative")
    failures = []
    reconciliation = {"added": 0, "removed": 0, "changed": 0, "refreshed_index": False}
    state = None
    if progress_path.exists():
        with gzip.open(progress_path, "rt", encoding="utf-8") as handle:
            state = json.load(handle)
        if (state.get("schema_version") != 1 or state.get("commander_slug") != slug
                or state.get("seed") != seed or not isinstance(state.get("results"), dict)
                or not isinstance(state.get("frame"), list)):
            raise ValueError("Collection checkpoint identity or schema mismatch")
        # Upgrade older checkpoint records without inventing an observation date.
        previous_rows = {row["urlhash"]: row for row in state["frame"]}
        for identifier, record in state["results"].items():
            if identifier in previous_rows:
                record.setdefault("row_fingerprint", _row_fingerprint(previous_rows[identifier]))
    should_read_index = state is None or (index_max_age_seconds is not None and source_is_stale(
        state.get("index_source_at"), index_max_age_seconds, now))
    if should_read_index:
        try:
            kwargs = {} if index_max_age_seconds is None else {"max_age_seconds": index_max_age_seconds}
            table = cache.get("decks.json", f"https://json.edhrec.com/pages/decks/{slug}.json", **kwargs)["table"]
            rows = {checked_identifier(row["urlhash"], deck=True): row for row in table}
            if state is None:
                frame = list(rows.values())
                random.Random(seed).shuffle(frame)  # Before inspecting cards or source tags.
                state = {"schema_version": 1, "commander_slug": slug, "seed": seed, "frame": frame, "results": {}}
            else:
                previous = {row["urlhash"]: row for row in state["frame"]}
                added = sorted(set(rows) - set(previous))
                random.Random(seed).shuffle(added)
                reconciliation.update(
                    added=len(added), removed=len(set(previous) - set(rows)),
                    changed=sum(_row_fingerprint(rows[key]) != _row_fingerprint(previous[key])
                                for key in set(rows) & set(previous)),
                )
                state["frame"] = [rows[row["urlhash"]] for row in state["frame"] if row["urlhash"] in rows]
                state["frame"].extend(rows[key] for key in added)
                state["results"] = {key: record for key, record in state["results"].items() if key in rows}
            observation = cache.provenance[-1] if cache.provenance else {}
            state["index_source_at"] = observation.get("fetched_at")
            state["index_reconciled_at"] = now.isoformat()
            reconciliation["refreshed_index"] = True
        except (SourceBlocked, FileNotFoundError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            if state is None:
                raise
            failures.append({"deck_index": slug, "error": str(error)})

    def save():
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = progress_path.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as handle:
            json.dump(state, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        temporary.replace(progress_path)

    target = state["frame"] if limit is None else state["frame"][:limit]
    visited, newly_attempted, revalidated = 0, 0, 0
    # Rewriting a large compressed snapshot every 50 requests would eventually
    # spend more time serializing historical lists than downloading new ones.
    # Raw successful responses are already durable in Cache between snapshots.
    checkpoint_every = min(1000, max(50, len(target) // 20)) if preview_workers > 1 else 10

    def verified_at(record):
        observed = _record_source_at(record)
        if not trust_index_versions:
            return observed
        candidates = [value for value in (record.get("index_verified_at"), observed) if source_time(value) is not None]
        return max(candidates, key=source_time) if candidates else None

    if trust_index_versions and state.get("index_source_at"):
        for row in target:
            record = state["results"].get(row["urlhash"])
            if record and "deck" in record and record.get("row_fingerprint") == _row_fingerprint(row):
                record["index_verified_at"] = state["index_source_at"]

    def needs_refresh(row, record):
        return (record is None or (preview_max_age_seconds is not None and (
            record.get("row_fingerprint") != _row_fingerprint(row)
            or source_is_stale(verified_at(record), preview_max_age_seconds, now))))

    # A source edit discovered in the latest index or an unobserved ID should
    # not wait behind an entire historical backfill on every refresh cycle.
    work = sorted(target, key=lambda row: (
        0 if (record := state["results"].get(row["urlhash"])) is None else
        1 if record.get("row_fingerprint") != _row_fingerprint(row) else 2,
        _record_source_at(record) or "" if record else "",
    )) if preview_max_age_seconds is not None else target
    def read(row):
        identifier = checked_identifier(row["urlhash"], deck=True)
        previous_record = state["results"].get(identifier)
        try:
            kwargs = {}
            if preview_max_age_seconds is not None:
                kwargs["max_age_seconds"] = preview_max_age_seconds
                if previous_record and previous_record.get("row_fingerprint") != _row_fingerprint(row):
                    kwargs["force_refresh"] = True
            url = f"https://edhrec.com/deckpreview/{identifier}"
            provenance_start = len(cache.provenance)
            if hasattr(cache, "get_preview"):
                data = cache.get_preview(identifier, **kwargs)
            else:
                data = cache.get(f"previews/{identifier}.html", url, html=True, **kwargs)
            deck = parse_preview(data, identifier)
            deck["source_bracket"] = row.get("bracket")
            provenance = cache.provenance_for(url) if hasattr(cache, "provenance_for") else [
                item for item in cache.provenance[provenance_start:] if item.get("url") == url]
            offline_unavailable = any(item.get("refresh_unavailable_offline") for item in provenance)
            record = {
                "deck": deck, "provenance": provenance,
                "source_at": next((item.get("fetched_at") for item in provenance if item.get("fetched_at")), None),
                "row_fingerprint": previous_record.get("row_fingerprint")
                if previous_record and offline_unavailable else _row_fingerprint(row),
            }
            return identifier, record, None, False
        except (SourceBlocked, httpx.HTTPError, FileNotFoundError, ValueError, KeyError, TypeError) as error:
            permanent = (isinstance(error, (ValueError, KeyError, TypeError))
                         and not isinstance(error, SourceFormatError)) or (
                isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {404, 410})
            record = {"error": str(error), "source_at": now.isoformat(), "row_fingerprint": _row_fingerprint(row)} if permanent else None
            return identifier, record, str(error), not permanent

    def accept(result):
        nonlocal newly_attempted, visited, revalidated
        identifier, record, error, stop = result
        if error is not None:
            failures.append({"deck": identifier, "error": error})
        if record is not None:
            revalidated += identifier in state["results"] and "deck" in record
            state["results"][identifier] = record
            newly_attempted += 1
            visited += 1
            if newly_attempted % checkpoint_every == 0:
                save()
                print(f"Read {len(state['results'])}/{len(target)} deck previews in this snapshot", flush=True)
        return stop

    def out_of_time():
        return getattr(cache, "blocked", False) or (max_seconds and monotonic() - started >= max_seconds)

    try:
        if preview_workers == 1:
            for row in work:
                if not needs_refresh(row, state["results"].get(row["urlhash"])):
                    visited += 1
                    continue
                if out_of_time() or accept(read(row)):
                    break
        else:
            pending_rows = iter(row for row in work if needs_refresh(row, state["results"].get(row["urlhash"])))
            with ThreadPoolExecutor(max_workers=preview_workers, thread_name_prefix="edhrec-preview") as executor:
                pending = set()
                stopped = False
                while True:
                    while not stopped and len(pending) < preview_workers and not out_of_time():
                        row = next(pending_rows, None)
                        if row is None:
                            break
                        pending.add(executor.submit(read, row))
                    if not pending:
                        break
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        stopped = accept(future.result()) or stopped
                    # In-flight responses are retained; never submit another ID
                    # after a challenge, outage, or deadline has stopped this pass.
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
    complete = len(records) == len(target) and not any(needs_refresh(row, state["results"].get(row["urlhash"])) for row in target)
    successful = [(row, state["results"][row["urlhash"]]) for row in target
                  if "deck" in state["results"].get(row["urlhash"], {})]
    age_limit = preview_max_age_seconds if preview_max_age_seconds is not None else freshness_target_hours * 3600
    dates = [source_time(_record_source_at(record)) for _, record in successful]
    known_dates = [date for date in dates if date is not None and (date - now).total_seconds() <= 300]
    verified_dates = [source_time(verified_at(record)) for _, record in successful]
    known_verified = [date for date in verified_dates if date is not None and (date - now).total_seconds() <= 300]
    index_stale = source_is_stale(state.get("index_source_at"), index_max_age_seconds
                                 if index_max_age_seconds is not None else freshness_target_hours * 3600, now)
    stale = sum(source_is_stale(verified_at(record), age_limit, now)
                or record.get("row_fingerprint") != _row_fingerprint(row) for row, record in successful)
    freshness = {
        "target_hours": freshness_target_hours,
        "max_age_hours": preview_max_age_seconds / 3600 if preview_max_age_seconds is not None else None,
        "oldest_source_at": min(known_dates).isoformat() if known_dates else None,
        "newest_source_at": max(known_dates).isoformat() if known_dates else None,
        "oldest_verified_at": min(known_verified).isoformat() if known_verified else None,
        "newest_verified_at": max(known_verified).isoformat() if known_verified else None,
        "basis": "current_index_versions" if trust_index_versions else "decklist_observations",
        "unchanged_verified": sum(bool(record.get("index_verified_at")) for _, record in successful) if trust_index_versions else 0,
        "source_dated_previews": len(known_dates), "source_undated_previews": len(successful) - len(known_dates),
        "observed_previews": len(successful), "dated_previews": len(known_verified),
        "undated_previews": len(successful) - len(known_verified), "stale_previews": stale,
        "fresh_previews": len(successful) - stale,
        "index_source_at": state.get("index_source_at"), "index_stale": index_stale,
        "source_complete": limit is None and complete and not index_stale and stale == 0,
    }
    return decks, {
        "sample_limit": limit, "target_mode": "all" if limit is None else "sample",
        "frame_decks": len(state["frame"]), "target_decks": len(target),
        "previews_attempted": len(records), "previews_loaded": len(decks),
        "unavailable_previews": sum("error" in record for record in records),
        "fresh_previews": len(successful) - stale,
        "new_previews_attempted": newly_attempted, "revalidated_previews": revalidated,
        "complete": complete, "failures": failures, "freshness": freshness,
        "reconciliation": reconciliation, "dataset_sha256": deck_dataset_sha256(decks),
    }
