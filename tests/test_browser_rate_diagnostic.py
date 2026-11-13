"""The requested experiment is bounded, paced, and stops on source failures."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("browser_rate_diagnostic", ROOT / "scripts/test_browser_rate.py")
rate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rate)
REAL_LIMITER = rate.RequestLimiter
NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = 0.0
    def monotonic(self):
        return self.value
    def sleep(self, value):
        self.value += value


def html_gallery():
    rows = [{"urlhash": f"deck-{index}", "savedate": "2026-11-13", "lands": 35, "creatures": 20}
            for index in range(100)]
    data = {"route": {"section": "commandersFeatured", "tab": "decks", "cardSlug": rate.SLUG}, "decks": rows}
    return '<p>Showing 1 to 50 of 100 decks</p><script>self.__next_f.push(' + json.dumps([1, '0:' + json.dumps(data) + '\n']) + ')</script>'


def html_preview(identifier):
    payload = {"urlhash": identifier, "deck": {"commander": ["The Tenth Doctor", "Rose Tyler"],
                "cards": {"Land": [["Island", 98]]}}}
    return '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"data": payload}}}) + '</script>'


def experiment(tmp_path, monkeypatch, *, denial=None, failure=None, cap=None):
    clock, instances = Clock(), []
    monkeypatch.setattr(rate, "RequestLimiter", lambda **kwargs: REAL_LIMITER(**kwargs, sleep=clock.sleep))
    if cap is not None:
        monkeypatch.setattr(rate, "MAX_REQUESTS", cap)
    class Transport:
        def __init__(self, limiter):
            self.limiter, self.calls, self.closed = limiter, [], False
            instances.append(self)
        @property
        def stats(self):
            return {"request_starts": self.calls, "redirects": [], "identity_overrides": False}
        def get_response(self, url):
            self.limiter.wait()
            self.calls.append({"url": url, "monotonic": clock.monotonic()})
            clock.sleep(.05)
            if failure and len(self.calls) == 3:
                raise failure
            status = denial if denial and len(self.calls) == 3 else 200
            body = html_gallery() if url.endswith('/decks') else html_preview(url.rsplit('/', 1)[1])
            if status != 200:
                self.limiter.block("Source denied this request", kind="rate_limit" if status == 429 else "access_restriction")
                body = "Banned - Too many requests"
            return httpx.Response(status, text=body, request=httpx.Request("GET", url))
        def close(self):
            self.closed = True
    test = rate.RateTest(tmp_path, transport_factory=Transport, monotonic=clock.monotonic,
                        now=lambda: NOW.isoformat(), stages=[(1, 8), (.5, 12)])
    result = test.execute()
    return result, instances[0]


def test_success_keeps_unique_preview_ids_pacing_and_production_untouched(tmp_path, monkeypatch):
    result, transport = experiment(tmp_path, monkeypatch)
    assert result["outcome"] == "completed_without_throttle"
    assert transport.closed and not result["production_settings_changed"]
    assert result == json.loads((tmp_path / "receipt.json").read_text())
    urls = [row["url"] for row in transport.calls]
    assert len(urls) == len(set(urls))
    assert all(right["monotonic"] - left["monotonic"] >= 1 - 1e-9 for left, right in zip(transport.calls, transport.calls[1:]))
    assert result["highest_completed_target_rps"] == 1
    assert result["stages"][0]["valid_responses"] >= 5
    assert result["stages"][1]["minimum_start_gap_seconds"] >= 2 - 1e-9


@pytest.mark.parametrize("status", [401, 403, 429])
def test_first_denial_stops_all_later_stages_without_retry(tmp_path, monkeypatch, status):
    result, transport = experiment(tmp_path, monkeypatch, denial=status)
    assert len(transport.calls) == 3 and transport.closed
    assert result["outcome"] == "stopped_on_source_denial"
    assert len(result["stages"]) == 1 and not result["stages"][0]["complete"]
    assert result["stop"]["status_code"] == status
    assert (tmp_path / "stop-response.txt").exists()
    assert result["highest_completed_target_rps"] is None


def test_transport_failure_is_inconclusive_and_not_retried(tmp_path, monkeypatch):
    result, transport = experiment(tmp_path, monkeypatch, failure=httpx.ConnectError("offline"))
    assert result["outcome"] == "inconclusive" and len(transport.calls) == 3
    assert not result["stages"][0]["complete"] and transport.closed


def test_request_cap_stops_before_another_request(tmp_path, monkeypatch):
    result, transport = experiment(tmp_path, monkeypatch, cap=3)
    assert len(transport.calls) == 3 and result["outcome"] == "inconclusive"
    assert "request cap" in result["reason"]


@pytest.mark.parametrize("stages", [[(1.01, 30)], [(float('nan'), 30)], [(True, 30)], [(0, 30)], [(1, 301)], [(1, 300)] * 4, []])
def test_over_budget_or_invalid_ladders_never_create_browser(tmp_path, stages):
    with pytest.raises(ValueError):
        rate.RateTest(tmp_path, stages=stages, transport_factory=lambda _: pytest.fail("No browser expected"))


@pytest.mark.parametrize("control", [
    {"blocked": True}, {"block_reason": "access_restriction", "blocked_until": (NOW - timedelta(days=1)).isoformat()},
    {"blocked_until": (NOW + timedelta(seconds=1)).isoformat()}, {"blocked_until": "bad"},
    {"measured_request_rate": {"blocked": True}},
])
def test_saved_global_and_worker_stops_prevent_test(control):
    with pytest.raises(ValueError):
        rate.check_controls(control, NOW)
    with pytest.raises(ValueError):
        rate.check_controls({"distributed": {"workers": [{"source_control": control}]}}, NOW)


def test_current_controls_allow_benchmark_without_clearing_any_fields():
    summary = {"blocked": False, "distributed": {"systemic_block": False,
                "workers": [{"source_control": {"source_rate": {"learned_rate": .1, "throttles": 3}}}]}}
    before = json.dumps(summary, sort_keys=True)
    rate.check_controls(summary, NOW)
    assert json.dumps(summary, sort_keys=True) == before


def test_summary_reports_actual_observed_rate_and_complete_flags(tmp_path, monkeypatch):
    result, _ = experiment(tmp_path, monkeypatch)
    path = tmp_path / "summary.md"
    rate.write_summary(result, path)
    text = path.read_text()
    assert "completed_without_throttle" in text and "1.000" in text and "0.500" in text
    assert "not a permanent quota" in text


def test_workflow_is_only_one_temporary_read_only_diagnostic():
    workflow = (ROOT / ".github/workflows/test-browser-rate.yml").read_text()
    assert "branches: [codex/browser-rate-pilot]" in workflow
    assert "actions: read" in workflow and "actions: write" not in workflow
    assert "timeout-minutes: 25" in workflow
    assert "if: always()" in workflow
    assert "install --with-deps webkit" in workflow
