"""Explicit access review never retries or discards an unreviewed pause."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_build_style_access", ROOT / "scripts/review_build_style_access.py")
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
RUN = "41264797842"


@pytest.fixture(autouse=True)
def no_pacing_wait(monkeypatch):
    monkeypatch.setattr("edhrec_affinity.build_styles.sources.time.sleep", lambda _seconds: None)


def checkpoint(tmp_path, *, preview=True):
    root = tmp_path / "styles"
    state = {"schema_version": 1, "commanders": {"theoden-strength-restored": {
        "status": "blocked", "retry_after": (NOW - timedelta(hours=4)).isoformat(), "current_previews": 151}},
        "block_reason": "access_restriction", "blocked_until": (NOW - timedelta(hours=4)).isoformat(),
        "source_rate": {"learned_rate": 0.75, "throttles": 3, "last_throttle": {"at": "2026-10-23T00:00:00+00:00"}},
        "progress": {"blocked": True, "block_reason": "access_restriction", "blocked_until": (NOW - timedelta(hours=4)).isoformat(),
                     "capacity": {"remaining_previews": 1000000}},
        "official_validation": {"source_blocked": True, "retry_at": "2099-01-01T00:00:00+00:00"},
        "distributed": {"runners": 16, "systemic_block": True, "workers": {
            "0": {"commanders": ["theoden-strength-restored"], "block_reason": "access_restriction",
                  "blocked_until": (NOW - timedelta(hours=4)).isoformat(), "source_rate": {"learned_rate": 0.65, "throttles": 3},
                  "pipeline": {"collection_workers": {"0": "theoden-strength-restored"}}},
            "1": {"commanders": ["numot-the-devastator"], "source_rate": {"learned_rate": 1.1, "throttles": 0}}}}}
    reviewer.atomic_json(root / reviewer.STATE, state)
    reviewer.atomic_json(root / "theoden-strength-restored.json", {"commander_slug": "theoden-strength-restored", "packages": []})
    reviewer.atomic_json(root / "_collection/sources/theoden-strength-restored/base.json", {"unchanged": True})
    if preview:
        reviewer.atomic_json(root / "_collection/sources/theoden-strength-restored/decks.json", {"table": [{"urlhash": "known-preview"}]})
    summary = tmp_path / "build_style_batch.json"
    reviewer.atomic_json(summary, {"blocked": True, "block_reason": "access_restriction", "blocked_until": state["blocked_until"],
                                   "automatic_continuation_allowed": True, "distributed": {"generation": RUN}})
    return root, summary, state


def base_payload():
    return {"container": {"json_dict": {"card": {"name": "Theoden, Strength Restored", "num_decks": 500,
        "url": "/commanders/theoden-strength-restored"}, "cardlists": []}}, "num_decks_avg": 500}


def good_response(request):
    if request.url.host == "api.scryfall.com":
        assert request.method == "POST"
        assert json.loads(request.content) == {"identifiers": [{"name": "Sol Ring"}]}
        return httpx.Response(200, json={"object": "list", "data": [{"object": "card", "name": "Sol Ring"}], "not_found": []})
    if request.url.host == "json.edhrec.com":
        return httpx.Response(200, json=base_payload())
    data = {"deck": {"commander": ["Theoden, Strength Restored"], "cards": {"Creature": [["Sol Ring", 1]]}}}
    return httpx.Response(200, headers={"content-type": "text/html"}, text=(
        '<html><script id="__NEXT_DATA__" type="application/json">' +
        json.dumps({"props": {"pageProps": {"data": data}}}) + '</script></html>'))


def execute(tmp_path, handler=good_response, *, preview=True, **kwargs):
    root, summary, state = checkpoint(tmp_path, preview=preview)
    requests = []
    def receive(request):
        requests.append(request)
        return handler(request)
    with httpx.Client(transport=httpx.MockTransport(receive), follow_redirects=False,
                      headers={"User-Agent": reviewer.DEFAULT_USER_AGENT}) as client:
        proof = reviewer.review(root, RUN, tmp_path / "proof.json", restored_run_id=kwargs.pop("restored_run_id", RUN),
            summary_file=summary, client=client, now=lambda: NOW, **kwargs)
    return root, summary, state, proof, requests


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_success_reviews_three_ordinary_routes_once_and_preserves_all_data_and_power_controls(tmp_path):
    root, summary, state = checkpoint(tmp_path)
    before = files(root)
    summary_before = summary.read_bytes()
    requests = []
    def receive(request):
        requests.append(request)
        return good_response(request)
    with httpx.Client(transport=httpx.MockTransport(receive), follow_redirects=False) as client:
        proof = reviewer.review(root, RUN, tmp_path / "proof.json", restored_run_id=RUN,
                                summary_file=summary, client=client, now=lambda: NOW)
    assert proof["passed"] and proof["released"] and len(requests) == 3
    assert [str(r.url) for r in requests] == [reviewer.COLLECTION_URL,
        "https://json.edhrec.com/pages/commanders/theoden-strength-restored.json", "https://edhrec.com/deckpreview/known-preview"]
    assert all(row["status_code"] == 200 and row["valid"] and len(row["response_sha256"]) == 64 for row in proof["checks"])
    after = files(root)
    assert {k: v for k, v in before.items() if k != reviewer.STATE} == {k: v for k, v in after.items() if k != reviewer.STATE}
    assert summary.read_bytes() == summary_before
    saved = reviewer.read_json(root / reviewer.STATE)
    assert saved["commanders"] == state["commanders"]
    assert saved["source_rate"] == state["source_rate"]
    assert saved["official_validation"] == state["official_validation"]
    assert "blocked_until" not in saved and "block_reason" not in saved
    assert saved["distributed"]["systemic_block"] is False
    for worker, original in state["distributed"]["workers"].items():
        assert saved["distributed"]["workers"][worker] == {k: v for k, v in original.items() if k not in reviewer.STOP_KEYS}
    audit = saved["_access_review"]
    assert audit["source_run_id"] == RUN and audit["original_controls"] == reviewer.stop_controls(state)
    assert audit["proof"]["state_sha256_before"] == reviewer.sha256(before[reviewer.STATE])
    assert audit["automatic_continuation_allowed"] is False


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500, 503])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_non200_stops_immediately_without_retry_or_control_changes(tmp_path, status, position):
    calls = 0
    def response(request):
        nonlocal calls
        calls += 1
        return (httpx.Response(status, headers={"retry-after": "120", "location": "https://example.com/"}, text="stop")
                if calls == position + 1 else good_response(request))
    root, summary, state, proof, requests = execute(tmp_path, response)
    assert not proof["passed"] and not proof["released"]
    assert len(requests) == position + 1
    assert reviewer.read_json(root / reviewer.STATE) == state
    assert proof["checks"][-1]["status_code"] == status
    assert proof["checks"][-1]["retry_after"] == "120"
    assert reviewer.read_json(summary)["blocked"] is True


@pytest.mark.parametrize("response", [
    httpx.Response(200, headers={"cf-mitigated": "challenge", "content-type": "application/json"}, text='{}'),
    httpx.Response(200, headers={"content-type": "text/html"}, text='<title>Just a moment</title>'),
    httpx.Response(200, json={"object": "list", "data": []}),
    httpx.Response(200, json={"object": "list", "data": [None]}),
    httpx.Response(200, json={"object": "list", "data": ["Sol Ring"]}),
    httpx.Response(200, headers={"content-type": "application/json"}, text='not JSON'),
    httpx.Response(200, headers={"content-type": "text/plain"}, text='{}'),
])
def test_challenges_bad_schema_or_content_type_never_release(tmp_path, response):
    root, _, state, proof, requests = execute(tmp_path, lambda _request: response)
    assert len(requests) == 1 and not proof["passed"]
    assert reviewer.read_json(root / reviewer.STATE) == state


def test_transport_failure_is_inconclusive_and_never_retried(tmp_path):
    def fail(request):
        raise httpx.ConnectError("offline", request=request)
    root, _, state, proof, requests = execute(tmp_path, fail)
    assert len(requests) == 1 and proof["checks"][-1]["outcome"] == "transport_error"
    assert reviewer.read_json(root / reviewer.STATE) == state


def test_missing_preview_index_is_an_explicit_proof_limitation(tmp_path):
    _, _, _, proof, requests = execute(tmp_path, preview=False)
    assert proof["released"] and len(requests) == 2 and proof["limitations"]


@pytest.mark.parametrize("mutation", ["future", "missing_expiry", "invalid_expiry", "wrong_rate", "wrong_provider", "not_blocked", "invalid_distributed", "invalid_rate_dict"])
def test_invalid_checkpoint_makes_no_requests_and_stays_byte_identical(tmp_path, mutation):
    root, summary, state = checkpoint(tmp_path)
    if mutation == "future": state["distributed"]["workers"]["0"]["blocked_until"] = (NOW + timedelta(hours=1)).isoformat()
    if mutation == "missing_expiry": state.pop("blocked_until")
    if mutation == "invalid_expiry": state["blocked_until"] = "unknown"
    if mutation == "wrong_rate": state["source_rate"]["learned_rate"] = -1
    if mutation == "wrong_provider": state["source_stop"] = {"provider": "edhpowerlevel"}
    if mutation == "invalid_distributed": state["distributed"] = None
    if mutation == "invalid_rate_dict": state["source_rate"] = None
    if mutation == "not_blocked":
        state.pop("block_reason");state["distributed"]["workers"]["0"].pop("block_reason")
    reviewer.atomic_json(root / reviewer.STATE, state)
    before = files(root)
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected service request"))) as client:
        proof = reviewer.review(root, RUN, tmp_path / "proof.json", restored_run_id=RUN, summary_file=summary, client=client, now=lambda: NOW)
    assert not proof["released"] and not proof["checks"] and files(root) == before


def test_wrong_restored_run_or_state_hash_never_requests_services(tmp_path):
    for number, kwargs in enumerate(({"restored_run_id": "123"}, {"expected_state_sha256": "0" * 64})):
        root, _, state, proof, requests = execute(tmp_path / str(number), **kwargs)
        assert not requests and not proof["released"] and reviewer.read_json(root / reviewer.STATE) == state


def test_changed_checkpoint_during_checks_is_not_overwritten(tmp_path):
    root, summary, state = checkpoint(tmp_path)
    changed = deepcopy(state)
    changed["commanders"]["new-commander"] = {"current_previews": 200}
    def response(request):
        if request.url.host == "edhrec.com":
            reviewer.atomic_json(root / reviewer.STATE, changed)
        return good_response(request)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        proof = reviewer.review(root, RUN, tmp_path / "proof.json", restored_run_id=RUN, summary_file=summary, client=client, now=lambda: NOW)
    assert not proof["released"] and "changed during review" in proof["reason"]
    assert reviewer.read_json(root / reviewer.STATE) == changed


def test_stale_proof_or_missing_route_cannot_release_controls(tmp_path):
    _, _, state, proof, _ = execute(tmp_path)
    with pytest.raises(ValueError, match="Fresh"):
        reviewer.release_controls(state, proof, now=NOW + timedelta(minutes=16))
    bad = deepcopy(proof)
    bad["checks"][-1]["url"] = "https://example.com/"
    with pytest.raises(ValueError, match="exact required"):
        reviewer.release_controls(state, bad, now=NOW)
