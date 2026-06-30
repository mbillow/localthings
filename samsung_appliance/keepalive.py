"""DTLS-layer liveness via CoAP empty-CON ping + poll-success watchdog.

Each interval_s the task:
  1. Sends a CoAP ping. This is fire-and-forget — Samsung's RT-OCF
     doesn't reliably reply with an RST, so the send itself is the
     keepalive (it tickles Samsung's observer state). The send only
     fails if the underlying socket is gone, in which case the failure
     counts toward fail_threshold.
  2. Calls liveness_fn() if provided. This is the real half-open
     detection: PollScheduler exposes last_success_ts, and the bridge
     wraps it as "did we get a 2.05 in the last 60s". If not, count
     the tick as a failure even though the ping send succeeded.

After fail_threshold consecutive failures, fires on_unreachable. First
success after a fail streak fires on_reachable. Bridge wires these to
MQTT availability.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from .coap_dtls import DtlsCoapSession


class KeepaliveTask:

    def __init__(self,
                 session: DtlsCoapSession,
                 interval_s: float = 25.0,
                 fail_threshold: int = 3,
                 on_reachable: Optional[Callable[[], None]] = None,
                 on_unreachable: Optional[Callable[[], None]] = None,
                 logger=None,
                 liveness_fn: Optional[Callable[[], bool]] = None):
        self.session = session
        self.interval_s = interval_s
        self.fail_threshold = fail_threshold
        self.on_reachable = on_reachable
        self.on_unreachable = on_unreachable
        self.log = logger
        self.liveness_fn = liveness_fn

        self._fail_streak = 0
        self._reachable = True
        self._ping_count = 0
        self._ping_fail_count = 0

    @property
    def ping_count(self) -> int:
        return self._ping_count

    @property
    def ping_fail_count(self) -> int:
        return self._ping_fail_count

    @property
    def reachable(self) -> bool:
        return self._reachable

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval_s):
            self._tick()

    def _tick(self) -> None:
        ok = False
        try:
            self.session.ping()
            ok = True
        except Exception as e:
            if self.log: self.log.warning("ping: %s", e)
        # Real half-open detection: ping sends can succeed against a
        # silently-wedged peer, but polls won't. If the scheduler
        # hasn't recorded a 2.05 inside the liveness window, treat
        # this tick as a failure even though the ping itself went out.
        if ok and self.liveness_fn is not None:
            try:
                alive = bool(self.liveness_fn())
            except Exception as e:
                if self.log: self.log.warning("liveness_fn: %s", e)
                alive = True
            if not alive:
                if self.log:
                    self.log.warning("liveness: no successful poll "
                                     "in the liveness window")
                ok = False
        self._ping_count += 1
        if ok:
            if not self._reachable:
                if self.log:
                    self.log.info("ping recovered after %d fails",
                                  self._fail_streak)
                self._reachable = True
                if self.on_reachable is not None:
                    try: self.on_reachable()
                    except Exception as e:
                        if self.log: self.log.warning("on_reachable: %s", e)
            self._fail_streak = 0
            return
        self._ping_fail_count += 1
        self._fail_streak += 1
        if self._reachable and self._fail_streak >= self.fail_threshold:
            if self.log:
                self.log.warning("device unreachable after %d ping failures",
                                 self._fail_streak)
            self._reachable = False
            if self.on_unreachable is not None:
                try: self.on_unreachable()
                except Exception as e:
                    if self.log: self.log.warning("on_unreachable: %s", e)
