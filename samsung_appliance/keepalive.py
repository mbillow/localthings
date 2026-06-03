"""DTLS-layer liveness via CoAP empty-CON ping.

Pings the appliance every interval_s. After fail_threshold consecutive
failures, fires on_unreachable. First success after a fail streak fires
on_reachable. Bridge wires these to MQTT availability.
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
                 logger=None):
        self.session = session
        self.interval_s = interval_s
        self.fail_threshold = fail_threshold
        self.on_reachable = on_reachable
        self.on_unreachable = on_unreachable
        self.log = logger

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
