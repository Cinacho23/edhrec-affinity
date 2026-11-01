#!/usr/bin/env python3
"""Review an expired collection access stop once, without discarding its audit.

This is an explicit, single-machine recovery step, never an automatic retry.
It requests each ordinary service route at most once and changes only saved
collection stop controls after every required response passes normal parsing.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

import httpx

from edhrec_affinity.build_styles.card_metadata import COLLECTION_URL
from edhrec_affinity.build_styles.sources import (
    Cache, SourceBlocked, checked_identifier, parse_preview, parse_profile,
)
from edhrec_affinity.tag_scraper import DEFAULT_USER_AGENT

STATE = "_collection/state.json"
DEFAULT_COMMANDER = "theoden-strength-restored"
STOP_KEYS = ("blocked", "blocked_until", "block_reason", "source_stop", "source_stops")
CHALLENGE_MARKERS = (
    "/cdn-cgi/challenge-platform/", "cf_chl_opt", 'id="challenge-form"',
    "id='challenge-form'", "<title>just a moment", "<title>attention required! | cloudflare",
)


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def read_json(path):
    def reject(value):
        raise ValueError(f"Nonfinite JSON: {value}")
    return json.loads(Path(path).read_text(), parse_constant=reject)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stop_controls(state):
    """Snapshot only collection controls, never external power-check queues."""
    result = {key: deepcopy(state[key]) for key in STOP_KEYS if key in state}
    result["source_rate"] = deepcopy(state.get("source_rate", {}))
    if isinstance(state.get("progress"), dict):
        result["progress"] = {key: deepcopy(state["progress"][key])
                              for key in STOP_KEYS if key in state["progress"]}
    distributed = state.get("distributed")
    if isinstance(distributed, dict):
        result["distributed"] = {"systemic_block": deepcopy(distributed.get("systemic_block")), "workers": {}}
        for worker, control in distributed.get("workers", {}).items():
            if not isinstance(control, dict):
                raise ValueError("Invalid saved worker controls")
            result["distributed"]["workers"][worker] = deepcopy(control)
    return result


def validate_review(state, *, now, source_run_id, restored_run_id=None, summary=None, expected_state_sha256=None, state_bytes=None):
    if (not isinstance(source_run_id, str) or not re.fullmatch(r"[1-9][0-9]*", source_run_id)
            or not isinstance(state, dict) or state.get("schema_version") != 1
            or not isinstance(state.get("commanders"), dict)):
        raise ValueError("Review requires a valid exact source run and collection checkpoint")
    if restored_run_id is not None and restored_run_id != source_run_id:
        raise ValueError("Restored artifact provenance differs from the reviewed source run")
    if expected_state_sha256 is not None:
        if not re.fullmatch(r"[a-f0-9]{64}", expected_state_sha256) or state_bytes is None or sha256(state_bytes) != expected_state_sha256:
            raise ValueError("Restored state does not match the pinned checkpoint hash")
    if summary is not None:
        if not isinstance(summary, dict) or summary.get("blocked") is not True or summary.get("block_reason") != "access_restriction":
            raise ValueError("Restored summary must retain the reviewed access restriction")
        # The trusted restore action supplies provenance; check an explicit run
        # identity as well whenever the summary includes one.
        recorded = summary.get("source_run_id") or summary.get("run_id")
        if recorded is not None and str(recorded) != source_run_id:
            raise ValueError("Restored summary belongs to another source run")
    distributed = state.get("distributed", {})
    if not isinstance(distributed, dict):
        raise ValueError("Invalid saved distributed controls")
    workers = distributed.get("workers", {})
    if not isinstance(workers, dict):
        raise ValueError("Invalid saved distributed worker controls")
    controls = [state, *workers.values()]
    if not any(isinstance(item, dict) and item.get("block_reason") == "access_restriction" for item in controls):
        raise ValueError("No saved collection access stop is available for explicit review")
    for control in controls + ([summary] if summary is not None else []):
        if not isinstance(control, dict):
            raise ValueError("Invalid saved collection controls")
        expiry = timestamp(control.get("blocked_until"))
        stopped = control.get("block_reason") in {"access_restriction", "rate_limit"} or control.get("blocked") is True
        if stopped and expiry is None:
            raise ValueError("A saved collection stop has no valid cooldown expiry")
        if control.get("blocked_until") is not None and expiry is None:
            raise ValueError("Invalid saved collection cooldown")
        if expiry is not None and expiry > now:
            raise ValueError("A saved collection cooldown is still active")
        if isinstance(stop := control.get("source_stop"), dict) and stop.get("provider") not in {"scryfall", "edhrec"}:
            raise ValueError("This review cannot release another provider's collection stop")
        source_rate = control.get("source_rate", {})
        if not isinstance(source_rate, dict):
            raise ValueError("Invalid saved source request controls")
        rate = source_rate.get("learned_rate")
        if rate is not None and (type(rate) not in {int, float} or not math.isfinite(rate) or rate <= 0):
            raise ValueError("Invalid saved source request rate")
    return stop_controls(state)


def known_preview(root, commander):
    sources = root / "_collection/sources"
    candidates = [sources / commander / "decks.json"]
    candidates.extend(path for path in sorted(sources.glob("*/decks.json")) if path not in candidates)
    for path in candidates:
        if path.is_symlink():
            raise ValueError("Review source inputs cannot be symlinks")
        try:
            payload = read_json(path)
            rows = payload.get("table")
            if not isinstance(rows, list):
                continue
            for row in rows:
                try:
                    identifier = checked_identifier(row.get("urlhash"), deck=True)
                except (AttributeError, TypeError, ValueError):
                    continue
                return {"identifier": identifier, "commander_slug": path.parent.name,
                        "index_file": path.relative_to(root).as_posix(), "index_sha256": sha256(path.read_bytes())}
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return None


class ReviewStopped(RuntimeError):
    pass


class Recorder:
    """An ordinary client with a durable-safe receipt, no retries or redirects."""
    def __init__(self, client, checks, *, now=utcnow):
        self.client, self.checks, self.now = client, checks, now

    def request(self, method, url, **kwargs):
        check = {"provider": "scryfall" if httpx.URL(url).host == "api.scryfall.com" else "edhrec",
                 "domain": httpx.URL(url).host, "url": url, "method": method,
                 "checked_at": self.now().isoformat(), "status_code": None, "valid": False}
        self.checks.append(check)
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.TransportError as error:
            check.update(outcome="transport_error", error=type(error).__name__)
            raise ReviewStopped("Service check was inconclusive because of a transport error") from error
        check.update(status_code=response.status_code, response_sha256=sha256(response.content),
                     content_type=response.headers.get("content-type"), retry_after=response.headers.get("retry-after"),
                     completed_at=self.now().isoformat())
        lower = response.text.lower()
        challenge = response.headers.get("cf-mitigated", "").lower() == "challenge" or (
            response.text.lstrip().startswith("<") and any(marker in lower for marker in CHALLENGE_MARKERS))
        check["challenge"] = challenge
        if challenge or response.status_code != 200:
            check["outcome"] = ("access_restriction" if challenge or response.status_code in {401, 403}
                                else "rate_limit" if response.status_code == 429 else "unexpected_status")
            raise ReviewStopped("Normal service check did not return an unrestricted HTTP200 response")
        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        expected_type = {"text/html", "application/xhtml+xml"} if "/deckpreview/" in url else {"application/json"}
        if content_type not in expected_type:
            check["outcome"] = "unexpected_content_type"
            raise ReviewStopped("Normal service check returned an unexpected response content type")
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def close(self):
        pass  # The caller owns the single shared HTTP client.


def check_services(root, commander, rate, *, client=None, now=utcnow):
    """Return proof from at most three serial ordinary requests."""
    checks, preview = [], known_preview(root, commander)
    owned_client = client is None
    client = client or httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                                    timeout=30, follow_redirects=False, trust_env=False)
    recorder = Recorder(client, checks, now=now)
    try:
        response = recorder.request("POST", COLLECTION_URL, json={"identifiers": [{"name": "Sol Ring"}]})
        payload = response.json()
        if (not isinstance(payload, dict) or payload.get("object") != "list"
                or not isinstance(payload.get("data"), list) or len(payload["data"]) != 1
                or not isinstance(payload["data"][0], dict)
                or payload["data"][0].get("object") != "card"
                or payload["data"][0].get("name") != "Sol Ring" or payload.get("not_found")):
            raise ValueError("Scryfall response does not match the requested known card")
        checks[-1].update(valid=True, outcome="verified")
        with tempfile.TemporaryDirectory(prefix="build-style-access-review-") as scratch:
            cache = Cache(Path(scratch), fetch=True, delay=max(1, 1 / rate), now=now)
            cache.client.close()
            cache.client = recorder
            try:
                def validate_base(data):
                    card = data["container"]["json_dict"]["card"]
                    if not isinstance(card, dict) or card.get("url") != f"/commanders/{commander}":
                        raise ValueError("EDHREC response belongs to another commander")
                    parse_profile(data, "all", "All builds", allow_empty=True)
                cache.get("base.json", f"https://json.edhrec.com/pages/commanders/{commander}.json", validate=validate_base)
                checks[-1].update(valid=True, outcome="verified")
                if preview is not None:
                    def validate_preview(data):
                        parsed = parse_preview(data, preview["identifier"])
                        if not parsed["commanders"] or not parsed["cards"]:
                            raise ValueError("Deck preview has no commanders or mainboard cards")
                    cache.get("preview.html", f"https://edhrec.com/deckpreview/{preview['identifier']}",
                              html=True, validate=validate_preview)
                    checks[-1].update(valid=True, outcome="verified")
            finally:
                cache.close()
    except (ReviewStopped, SourceBlocked, httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as error:
        if checks and "outcome" not in checks[-1]:
            checks[-1].update(outcome="invalid_schema", error=type(error).__name__)
        return {"passed": False, "checks": checks, "preview": preview, "reason": str(error)[:500]}
    finally:
        if owned_client:
            client.close()
    return {"passed": True, "checks": checks, "preview": preview,
            "limitations": [] if preview else ["No known preview identifier was available in the restored source indexes"]}


def release_controls(state, proof, *, now):
    """Audit before releasing collection stops; retain every non-stop field."""
    checks = proof.get("checks", [])
    if (proof.get("passed") is not True or not isinstance(checks, list) or len(checks) not in {2, 3}
            or any(not isinstance(check, dict) for check in checks)
            or any(check.get("valid") is not True or check.get("status_code") != 200
                   or check.get("challenge") is not False or not re.fullmatch(r"[a-f0-9]{64}", check.get("response_sha256", ""))
                   or (at := timestamp(check.get("checked_at"))) is None or not 0 <= (now - at).total_seconds() <= 900
                   for check in checks)):
        raise ValueError("Fresh unrestricted normal-service proof is required before releasing collection controls")
    expected = [("scryfall", "POST", COLLECTION_URL),
                ("edhrec", "GET", f"https://json.edhrec.com/pages/commanders/{proof['commander_slug']}.json")]
    if proof.get("preview") is not None:
        expected.append(("edhrec", "GET", f"https://edhrec.com/deckpreview/{proof['preview']['identifier']}"))
    if [(c.get("provider"), c.get("method"), c.get("url")) for c in checks] != expected:
        raise ValueError("Review proof does not cover the exact required service routes")
    original = validate_review(state, now=now, source_run_id=proof["source_run_id"])
    released = deepcopy(state)
    for key in STOP_KEYS:
        released.pop(key, None)
    if isinstance(released.get("progress"), dict):
        for key in STOP_KEYS:
            released["progress"].pop(key, None)
    if isinstance(released.get("distributed"), dict):
        released["distributed"]["systemic_block"] = False
        for control in released["distributed"].get("workers", {}).values():
            for key in STOP_KEYS:
                control.pop(key, None)
    previous = released.get("_access_review")
    released["_access_review"] = {"schema_version": 1, "source_run_id": proof["source_run_id"],
        "reviewed_at": now.isoformat(), "mode": "one_machine_bounded", "automatic_continuation_allowed": False,
        "original_controls": original, "proof": deepcopy(proof),
        "previous_review": deepcopy(previous) if previous is not None else None}
    return released


def review(root, source_run_id, output, *, restored_run_id, summary_file=None, expected_state_sha256=None,
           commander=DEFAULT_COMMANDER, client=None, now=utcnow):
    root, output = Path(root), Path(output)
    state_path = root / STATE
    if root.resolve() in output.resolve().parents or output.resolve() == root.resolve():
        raise ValueError("Review proof must be outside the canonical checkpoint")
    if summary_file and output.resolve() == Path(summary_file).resolve():
        raise ValueError("Review proof cannot replace the restored source summary")
    proof = {"schema_version": 1, "source_run_id": str(source_run_id), "commander_slug": checked_identifier(commander),
             "started_at": now().isoformat(), "passed": False, "released": False, "checks": []}
    try:
        if state_path.is_symlink() or root.is_symlink():
            raise ValueError("Review checkpoint cannot be a symlink")
        state_bytes = state_path.read_bytes()
        state = read_json(state_path)
        validate_review(state, now=now(), source_run_id=str(source_run_id),
                        restored_run_id=str(restored_run_id),
                        summary=read_json(summary_file) if summary_file else None,
                        expected_state_sha256=expected_state_sha256, state_bytes=state_bytes)
        proof["state_sha256_before"] = sha256(state_bytes)
        rates = [control.get("source_rate", {}).get("learned_rate") for control in
                 [state, *state.get("distributed", {}).get("workers", {}).values()]]
        rate = min([1.0, *[value for value in rates if value is not None]])
        proof.update(check_services(root, commander, rate, client=client, now=now))
        proof["completed_at"] = now().isoformat()
        if proof["passed"]:
            if state_path.read_bytes() != state_bytes:
                raise ValueError("Collection checkpoint changed during review; controls remain unchanged")
            proof["released"] = True
            released = release_controls(state, proof, now=now())
            proof.update(released=True, state_sha256_after=sha256(encoded(released)))
            # Write the proof first. A subsequent failed state write cannot
            # leave an unaudited release; the state itself embeds the proof.
            atomic_json(output, proof)
            atomic_json(state_path, released)
    except (OSError, ValueError, TypeError, KeyError) as error:
        proof.update(passed=False, released=False, reason=str(error)[:500], completed_at=now().isoformat())
    atomic_json(output, proof)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-styles-dir", required=True, type=Path)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--restored-run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--expected-state-sha256")
    parser.add_argument("--commander", default=DEFAULT_COMMANDER)
    args = parser.parse_args()
    result = review(args.build_styles_dir, args.source_run_id, args.output, restored_run_id=args.restored_run_id,
                    summary_file=args.summary_file,
                    expected_state_sha256=args.expected_state_sha256, commander=args.commander)
    print(json.dumps(result, allow_nan=False))
    return 0 if result["released"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
