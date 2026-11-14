"""Explicitly bounded diagnostic pacing; never imported by production collectors."""
from __future__ import annotations

import math
import threading
import time

from edhrec_affinity.build_styles.sources import BudgetExpired, SourceBlocked


class ProbeLimiter:
    """One machine-wide pacing gate for the requested test, capped at 5 RPS.

    Every browser on this diagnostic VM shares this gate and stop event. No
    source stop can be cleared by updating the stage or creating a browser.
    """

    def __init__(self, *, max_rate, deadline=None, monotonic=time.monotonic, sleep=None):
        if type(max_rate) not in {float, int} or not math.isfinite(max_rate) or not 0 < max_rate <= 5:
            raise ValueError("Diagnostic rates must be positive and at most 5 RPS")
        self.rate, self.deadline, self.monotonic = max_rate, deadline, monotonic
        self._gate = threading.Lock()
        self._stopped = threading.Event()
        self._sleep = sleep or self._stopped.wait
        self._next = 0.0
        self.reason = self.kind = self.details = None

    @property
    def blocked(self):
        return self._stopped.is_set()

    def check_budget(self):
        if self.blocked:
            raise SourceBlocked(self.reason or "Diagnostic stopped")
        if self.deadline is not None and self.monotonic() >= self.deadline:
            raise BudgetExpired("Diagnostic stage ended")

    def defer(self, seconds):
        with self._gate:
            self._next = max(self._next, self.monotonic() + max(0, seconds))

    def wait(self):
        with self._gate:
            self.check_budget()
            delay = max(0, self._next - self.monotonic())
            if self.deadline is not None:
                delay = min(delay, max(0, self.deadline - self.monotonic()))
            if delay:
                self._sleep(delay)
            self.check_budget()
            self._next = self.monotonic() + 1 / self.rate

    def block(self, reason, *, kind=None, details=None):
        # A browser response callback can stop other waiting browsers without
        # waiting for the pacing gate itself. The first stop remains evidence.
        if not self._stopped.is_set():
            self.reason, self.kind, self.details = reason, kind, details
            self._stopped.set()

    def record_success(self):
        pass  # No automatic ramp and no writes to a production learned rate.
