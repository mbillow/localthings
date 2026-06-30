"""Periodic OBSERVE re-subscribe.

CoAP OBSERVE (RFC 7641) has no built-in TTL, but real-world peers age
out observer state on their own schedule — Samsung's RT-OCF is known
to silently drop notify delivery during cloud auth blips even though
the DTLS session stays healthy. Without a re-subscribe, recovery from
such a blip requires a full session reconnect.

This task derregisters the current observer tokens and re-subscribes
every `interval_s`. Cheap (one register CON per path), idempotent
(Samsung silently no-ops a register on an already-active token), and
resilient — individual subscribe failures are logged but don't abort
the task.
"""
from __future__ import annotations

import threading
from typing import Optional

from .coap_dtls import DtlsCoapSession


class ObserveRefreshTask:

    def __init__(self,
                 session: DtlsCoapSession,
                 paths,
                 interval_s: float = 6 * 3600.0,
                 logger=None):
        self.session = session
        self.paths = [list(p) for p in paths]
        self.interval_s = interval_s
        self.log = logger
        self._refresh_count = 0

    @property
    def refresh_count(self) -> int:
        return self._refresh_count

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval_s):
            try:
                self.session.refresh_observes(self.paths)
                self._refresh_count += 1
                if self.log:
                    self.log.info("OBSERVE refresh #%d (%d paths)",
                                  self._refresh_count, len(self.paths))
            except Exception as e:
                if self.log:
                    self.log.warning("OBSERVE refresh: %s", e)
