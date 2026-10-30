"""Distributed pilot evidence measures real source work, excluding later analysis."""
from datetime import datetime, timedelta, timezone

from edhrec_affinity.build_styles import sources


def test_request_window_ends_at_response_instead_of_later_stats_snapshot(monkeypatch):
    start = datetime(2026, 10, 30, tzinfo=timezone.utc)
    wall, monotonic = [start], [0.0]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall[0]
    monkeypatch.setattr(sources, "datetime", Clock)
    limiter = sources.RequestLimiter(monotonic=lambda: monotonic[0], sleep=lambda _: None)
    assert limiter.stats["started_at"] is None and limiter.stats["completed_at"] is None
    limiter.wait()
    wall[0] += timedelta(seconds=2)
    monotonic[0] = 2
    limiter.record_success()
    response = wall[0].isoformat()
    wall[0] += timedelta(minutes=20)
    monotonic[0] += 1200
    assert limiter.stats["started_at"] == start.isoformat()
    assert limiter.stats["completed_at"] == response
    assert limiter.stats["elapsed_seconds"] == 1202
    limiter.wait()
    limiter.record_failure()
    assert limiter.stats["started_at"] == start.isoformat()
    assert limiter.stats["completed_at"] == wall[0].isoformat()


def test_throttle_records_last_real_source_event():
    limiter = sources.RequestLimiter(sleep=lambda _: None)
    limiter.wait()
    observed = datetime.now(timezone.utc)
    limiter.record_throttle(now=observed)
    assert limiter.stats["completed_at"] == observed.isoformat()
    assert limiter.stats["throttles"] == 1
