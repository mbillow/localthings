"""Single source of truth for one appliance's state.

All writers (OBSERVE notify, poll, seed, optimistic) call apply_rep().
A registered on_change callback fires after any apply that mutated the
cache, which the bridge wires to its MQTT publish gate.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .appliances.base import ApplianceDescriptor


class StateCache:

    def __init__(self, descriptor: 'ApplianceDescriptor'):
        self.descriptor = descriptor
        self.links: dict[str, dict] = {}
        self.last_updated: dict[str, float] = {}
        self.source: dict[str, str] = {}
        self.descriptor_state: dict = {}
        self._on_change: Optional[Callable[[bool, str], None]] = None
        self._lock = threading.RLock()

    def set_on_change(self, cb: Callable[[bool, str], None]) -> None:
        self._on_change = cb

    def apply_rep(self, href: str, rep: dict, source: str) -> bool:
        if not isinstance(rep, dict):
            return False
        with self._lock:
            prior = self.links.get(href)
            changed = prior != rep
            self.links[href] = rep
            self.last_updated[href] = time.time()
            self.source[href] = source
        hook = self.descriptor.on_observation
        if hook is not None:
            try:
                hook(self.descriptor_state, href, rep)
            except Exception:
                pass
        if self._on_change is not None:
            try:
                self._on_change(changed, source)
            except Exception:
                pass
        return changed

    def apply_optimistic(self, href: str, body: dict) -> bool:
        if not isinstance(body, dict):
            return False
        with self._lock:
            merged = dict(self.links.get(href) or {})
            merged.update(body)
        return self.apply_rep(href, merged, source='optimistic')

    def get(self, href: str) -> Optional[dict]:
        with self._lock:
            return self.links.get(href)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return dict(self.links)

    def freshness_s(self, href: str) -> Optional[float]:
        ts = self.last_updated.get(href)
        return None if ts is None else (time.time() - ts)

    def stalest(self) -> Optional[tuple[str, float]]:
        with self._lock:
            if not self.last_updated:
                return None
            href = min(self.last_updated, key=self.last_updated.get)
            return href, time.time() - self.last_updated[href]
