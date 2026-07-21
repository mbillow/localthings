"""Coordinator for Local Things integration."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import timedelta
from typing import Any

import cbor2

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo

from smartthings_local.protocol.dtls_session import DtlsCoapSession
from smartthings_local.ocf.state_cache import StateCache

from .registry.batch import parse_device0_batch
from .registry.by_type import for_device, for_device_by_model, for_device_by_resources
from .registry.discovery import discover, BoundEntity
from .registry import CAPABILITIES
from .registry.adapter import flatten
from .registry.identity import read_identity, DeviceIdentity
from .observe import ObserveManager, MODE_OBSERVE, MODE_POLL, GRACE_PERIOD_S

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM,
    DEVICE_SUPPORT_ISSUE_URL, SUMMARY_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)

_SEED_PATH = ['device', '0']


class _NoOpDescriptor:
    """StateCache requires a descriptor with an on_observation hook. This
    integration doesn't use per-capability observation hooks, so this is a
    deliberate no-op, not a placeholder for missing functionality."""
    def on_observation(self, state: dict, href: str, rep: dict) -> None:
        return None


_RECOVERY_RETRY_S = 600.0  # re-attempt observe mode this often while polling


class LocalThingsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages one Samsung appliance: session, discovery, polling."""

    bound: list[BoundEntity]
    device_info: DeviceInfo
    device_serial: str

    # Class-level knobs (not instance attrs) so tests can shrink real-time
    # delays via `patch.object(LocalThingsCoordinator, ...)` without
    # touching the production defaults these are computed from. Production
    # code always sees these two values; only tests override them.
    _SUBPOLL_STEP_S: float = SUMMARY_INTERVAL_S / 10  # 3.0 s
    _OBSERVE_GRACE_PERIOD_S: float = GRACE_PERIOD_S
    _RECONNECT_PAUSE_S: float = 5.0

    # A block-level ACK timeout on the summary GET doesn't prove the
    # session is dead (see _poll_once) — require this many in a row
    # before treating it as one. A single slow transfer on an otherwise
    # fine session shouldn't tear down a working OBSERVE subscription.
    _POLL_TIMEOUT_LIMIT: int = 3

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Per-device logger (module logger scoped to this device's host) so
        # every log line — including the base DataUpdateCoordinator's own
        # messages and ObserveManager's — identifies which device it's
        # about. A bare module-level logger is shared across every
        # configured device, which makes multi-device logs ambiguous.
        self._log = logging.getLogger(f"{__name__}.{entry.data[CONF_HOST]}")
        super().__init__(
            hass,
            self._log,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=SUMMARY_INTERVAL_S),
        )
        self._entry = entry
        self._session: DtlsCoapSession | None = None
        self._identity: DeviceIdentity | None = None
        self._discovered = False
        self.bound = []
        self._cache = StateCache(_NoOpDescriptor())
        self._cache.set_on_change(self._on_cache_changed)
        self._observe = ObserveManager(self._cache, logger=self._log)
        self._push_pending = False
        self._push_pending_lock = threading.Lock()
        self.device_serial = entry.data[CONF_HOST]  # placeholder until first poll
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_HOST])},
            name=f"Samsung Appliance ({entry.data[CONF_HOST]})",
            manufacturer="Samsung",
        )
        self._session_lock = asyncio.Lock()
        self._subpoll_task: asyncio.Task | None = None
        self._hot_hrefs: list[str] = []
        self._warm_hrefs: list[str] = []
        self.device_type_name: str | None = None
        self.one_ui_version: str = ''
        self._consecutive_poll_timeouts = 0
        self._unbound_hrefs: list[str] = []

    # ------------------------------------------------------------------
    # Session management (all blocking — must run in executor)
    # ------------------------------------------------------------------

    @property
    def last_resources(self) -> dict:
        return self._cache.snapshot()

    def resource(self, href: str) -> dict:
        """A single href's current rep. Cheaper than `last_resources.get(href)`
        for callers that only need one href — `last_resources` copies every
        tracked href's rep to build the snapshot dict, while this is a
        direct O(1) cache lookup."""
        return self._cache.get(href) or {}

    @property
    def observe_mode(self) -> str:
        return self._observe.mode

    def _connect_session(self) -> None:
        host     = self._entry.data[CONF_HOST]
        port     = self._entry.data[CONF_PORT]
        cert_pem = self._entry.data[CONF_LEAF_CERT_PEM]
        key_pem  = self._entry.data[CONF_LEAF_KEY_PEM]

        sess = DtlsCoapSession(host, port, cert_pem=cert_pem, key_pem=key_pem,
                               on_notification=self._observe.on_notification)
        sess.connect()
        sess.start_reader()
        self._session = sess
        self._log.debug("DTLS connected to %s:%d", host, port)
        try:
            self._identity = read_identity(sess, None)
        except Exception as e:
            self._log.debug("read_identity failed: %s", e)
            self._identity = None

    def _close_session(self) -> None:
        sess = self._session
        self._session = None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

    async def async_close(self) -> None:
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None
        self._observe.close()
        await self.hass.async_add_executor_job(self._close_session)

    def _on_cache_changed(self, changed: bool, source: str) -> None:
        """StateCache.set_on_change callback. Runs on whatever thread
        applied the update (DTLS reader thread for observe notifications,
        an executor thread for poll/sweep) — never the event loop, so the
        HA push must be scheduled thread-safely. A sweep/poll cycle can
        call apply_rep for dozens of hrefs in a tight loop; coalesce those
        into a single push instead of one hass.add_job per href."""
        if not changed:
            return
        with self._push_pending_lock:
            if self._push_pending:
                return
            self._push_pending = True
        self.hass.add_job(self._push_cache_snapshot)

    @callback
    def _push_cache_snapshot(self) -> None:
        with self._push_pending_lock:
            self._push_pending = False
        if self.bound:
            self.async_set_updated_data(flatten(self.bound, self._cache.snapshot()))

    def _poll_once(self) -> dict[str, dict]:
        """GET /device/0, return parsed resources. Blocking.

        `sess.get()` raises `TimeoutError` when one block's ACK doesn't
        arrive in time — the transfer was progressing (earlier blocks
        succeeded) and just didn't finish before the deadline on a slow
        device. That does NOT prove the session is dead, so it's left
        open here; `_async_update_data` decides whether repeated timeouts
        (or a lack of them) warrant a reconnect. Anything else (a
        `ConnectionError` from an explicitly closed/broken session, a bad
        response code) is unambiguous — close immediately.
        """
        if self._session is None:
            self._connect_session()
        sess = self._session
        try:
            # A slow device can still be mid-transfer (block 8, block 11)
            # when a tighter deadline cuts it off — that's a poll that
            # would have succeeded, not a dead session. 35s gives a slow
            # blockwise transfer room to actually finish instead of
            # generating a TimeoutError every cycle.
            code, payload = sess.get(_SEED_PATH, timeout=35.0)
        except TimeoutError:
            raise
        except Exception as e:
            self._close_session()
            raise RuntimeError(f"poll GET failed: {e}") from e
        if code != 0x45 or not payload:
            self._close_session()
            raise RuntimeError(f"poll: unexpected code {code:#04x}")
        try:
            body = cbor2.loads(payload)
        except Exception as e:
            raise RuntimeError(f"poll cbor decode: {e}") from e
        return parse_device0_batch(body) if isinstance(body, list) else {}

    def _poll_hrefs_blocking(self, hrefs: list[str]) -> dict[str, dict]:
        """GET individual hrefs sequentially. Does not reconnect on failure. Blocking."""
        if self._session is None:
            return {}
        results = {}
        first = True
        for href in hrefs:
            if not first:
                self._session.pace()
            first = False
            try:
                path = [s for s in href.strip('/').split('/')]
                code, payload = self._session.get(path, timeout=10.0)
                if code == 0x45 and payload:
                    rep = cbor2.loads(payload)
                    if isinstance(rep, dict):
                        self._observe.apply(href, rep, source='poll')
                        results[href] = rep
            except Exception as e:
                self._log.debug("sub-poll %s: %s", href, e)
        return results

    # ------------------------------------------------------------------
    # Sub-poll loop (runs between summary polls)
    # ------------------------------------------------------------------

    async def _run_subpolls(self, force: bool = False) -> None:
        """Poll hot/warm hrefs in the gaps between summary polls. Only
        runs in poll-only mode — in observe-primary mode those hrefs are
        already covered by push notifications — unless `force` is set,
        which this cycle's sweep found disagreeing with the cache on a
        still-live observe session (see log_sweep_discrepancies): a
        bounded, self-limiting fallback for a channel that's gone silent
        without a reconnect, without tearing down subscriptions that
        would otherwise recover on their own once notifies resume."""
        if self._observe.mode == MODE_OBSERVE and not force:
            return
        hot = self._hot_hrefs
        warm = self._warm_hrefs
        if not hot and not warm:
            return
        step = self._SUBPOLL_STEP_S
        for i in range(1, 10):          # slots 1..9  (T+3 s … T+27 s)
            await asyncio.sleep(step)
            hrefs = list(hot) + (list(warm) if i % 2 == 0 else [])
            async with self._session_lock:
                try:
                    await self.hass.async_add_executor_job(
                        self._poll_hrefs_blocking, hrefs
                    )
                except Exception as e:
                    self._log.debug("sub-poll batch failed: %s", e)

    # ------------------------------------------------------------------
    # Discovery (runs once on first successful poll)
    # ------------------------------------------------------------------

    def _run_discovery(self, resources: dict[str, dict]) -> None:
        one_ui = (resources.get('/otninformation/vs/0', {})
                  .get('swVersionInfo', {})
                  .get('oneUiVersion', ''))
        self.one_ui_version = one_ui
        info = resources.get('/information/vs/0', {})
        reg = for_device(one_ui) if one_ui else None
        if reg is None:
            reg = for_device_by_model(
                info.get('x.com.samsung.da.modelNum', ''),
                info.get('x.com.samsung.da.description', ''),
            )
        if reg is None:
            reg = for_device_by_resources(resources)
        unbound: list[str] = []
        if reg is not None:
            self._log.debug("device type: %s (oneUiVersion=%r)", reg.name, one_ui)
            bound = discover(resources, reg.capabilities, reg.pattern_capabilities,
                              log=unbound.append)
            self.device_type_name = reg.name
        else:
            self._log.warning("unknown device type oneUiVersion=%r; using common caps", one_ui)
            bound = discover(resources, CAPABILITIES, log=unbound.append)
            self.device_type_name = None
        self.bound = bound
        self._unbound_hrefs = unbound

        serial = info.get('x.com.samsung.da.serialNum', '')
        if not serial:
            serial = self._entry.data[CONF_HOST]
        self.device_serial = serial

        ident = self._identity
        device_type = reg.name.replace('_', ' ').title() if reg else 'Appliance'
        model_num = info.get('x.com.samsung.da.modelNum', '')
        model = model_num.split('|', 1)[0] if model_num else (ident.model if ident else '')
        name  = f"Samsung {device_type} ({model})" if model else f"Samsung {device_type}"
        mfr   = (ident.manufacturer if ident else '') or 'Samsung'

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=name,
            manufacturer=mfr,
            model=model,
        )
        self._update_coverage_gap_issue(reg is None, unbound, name)

        hot, warm = set(), set()
        for be in bound:
            tier = be.capability.poll_tier
            if tier == 'hot':
                hot.add(be.href)
            elif tier == 'warm':
                warm.add(be.href)
        self._hot_hrefs = sorted(hot)
        self._warm_hrefs = sorted(warm)

        self._discovered = True
        self._log.info(
            "discovered %d entities (serial=%s) hot=%s warm=%s",
            len(bound), serial, self._hot_hrefs, self._warm_hrefs,
        )

    def _update_coverage_gap_issue(
        self, unknown_type: bool, unbound_hrefs: list[str], device_name: str,
    ) -> None:
        """Raise or clear a Repairs issue when capability coverage is incomplete.

        Fires once, at discovery time, either because the device type itself
        wasn't recognized or because some of its resources didn't bind to
        any capability. Diagnostics (diagnostics.py) is what a user actually
        downloads to help; this just tells them there's something to send.
        """
        issue_id = f"device_gap_{self._entry.entry_id}"
        if unknown_type or unbound_hrefs:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="device_gap",
                translation_placeholders={"device_name": device_name},
                learn_more_url=DEVICE_SUPPORT_ISSUE_URL,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _attempt_observe_mode(self) -> None:
        """Called once, right after first discovery. Blocking (sleeps for
        the whole grace period) — must run in an executor."""
        hrefs = self._hot_hrefs + self._warm_hrefs
        if not hrefs:
            return
        if self._session is None:
            # _poll_once already connects on a real poll; this only fires
            # if the session was closed out from under us concurrently.
            await self.hass.async_add_executor_job(self._connect_session)
        sess = self._session
        if sess is None:
            return
        await self.hass.async_add_executor_job(
            self._observe.try_enter_observe_mode, sess, hrefs,
            self._OBSERVE_GRACE_PERIOD_S,
        )

    async def _maybe_retry_observe_mode(self) -> None:
        """While in poll-only mode, periodically re-attempt observe mode
        so a device that gains internet access recovers push automatically."""
        if time.monotonic() - self._observe.last_mode_change_ts < _RECOVERY_RETRY_S:
            return
        await self._attempt_observe_mode()

    def _defer_reconnect_for(self, e: Exception) -> bool:
        """True if this poll failure should NOT trigger a reconnect this
        cycle.

        A `TimeoutError` (see `_poll_once`) means one block's ACK didn't
        arrive in time — not that the session is dead. A recent OBSERVE
        notify is direct proof the channel is still live, so always defer
        in that case. Otherwise, defer until `_POLL_TIMEOUT_LIMIT`
        consecutive timeouts have piled up — a single slow transfer is
        normal on a flaky device; a run of them is a real problem. Any
        other exception (a `ConnectionError`, an explicitly closed
        session) is unambiguous and always reconnects immediately.
        """
        if not isinstance(e, TimeoutError):
            return False
        if self._observe.mode == MODE_OBSERVE and self._observe.recently_notified():
            # Recent push is proof of life — reset the counter too, so
            # timeouts from an earlier quiet stretch don't carry over and
            # trigger a reconnect once the device goes quiet again. The
            # counter should mean "consecutive timeouts with no push
            # activity to vouch for the session," not just "consecutive
            # timeouts" — otherwise an intermittently-active device could
            # still accumulate its way into a false reconnect.
            self._consecutive_poll_timeouts = 0
            return True
        self._consecutive_poll_timeouts += 1
        return self._consecutive_poll_timeouts < self._POLL_TIMEOUT_LIMIT

    # ------------------------------------------------------------------
    # DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        # Stop any in-flight sub-poll before taking the session lock.
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None

        just_downgraded_from_observe = False
        async with self._session_lock:
            try:
                resources = await self.hass.async_add_executor_job(self._poll_once)
                self._consecutive_poll_timeouts = 0
            except Exception as e:
                if self._defer_reconnect_for(e):
                    self._log.debug(
                        "poll failed (%s), not yet treated as session "
                        "death; skipping this cycle: %s",
                        type(e).__name__, e,
                    )
                    return flatten(self.bound, self._cache.snapshot())
                self._consecutive_poll_timeouts = 0
                # One reconnect attempt — pause briefly so the device can
                # clean up its DTLS session state before we knock again.
                self._log.warning("poll failed, reconnecting: %s", e)
                await self.hass.async_add_executor_job(self._close_session)
                await asyncio.sleep(self._RECONNECT_PAUSE_S)
                try:
                    resources = await self.hass.async_add_executor_job(self._poll_once)
                except Exception as e2:
                    self._log.error("poll failed after reconnect: %s", e2)
                    snapshot = self._cache.snapshot()
                    if snapshot:
                        self._log.debug("Full error:", exc_info=e2)
                        return flatten(self.bound, snapshot)
                    raise UpdateFailed(f"poll failed after reconnect: {e2}") from e2
                else:
                    # The reconnect gave us a brand-new session with zero
                    # OBSERVE registrations. If we were in observe mode,
                    # that state is now stale — the refresh task is still
                    # pinned to the old (closed) session and nothing will
                    # ever re-subscribe on the new one. Tear it down and
                    # try to resubscribe immediately below rather than
                    # waiting for the poll-mode retry timer — that timer
                    # exists to throttle devices that never had observe
                    # working at all, but a reconnect just proved this
                    # session is healthy, so there's no reason to wait.
                    if self._observe.mode == MODE_OBSERVE:
                        self._log.debug(
                            "reconnect while in observe mode; downgrading to "
                            "poll and resubscribing on the new session"
                        )
                        self._observe.downgrade_to_poll()
                        just_downgraded_from_observe = True

        source = 'sweep' if self._discovered else 'poll'
        sweep_mismatch = False
        if self._observe.mode == MODE_OBSERVE:
            # A sweep/cache mismatch never tears down a still-live OBSERVE
            # session (see log_sweep_discrepancies) — the sweep below
            # re-applies the authoritative state to the cache regardless,
            # so there's nothing to correct by downgrading. Only a
            # reconnect (above) proves subscriptions are actually gone.
            # Instead, a mismatch triggers extra hot/warm subpolls this
            # cycle below, so a channel gone silent without a reconnect
            # (e.g. lost internet on an otherwise-live local session)
            # still gets fresher-than-30s data.
            sweep_mismatch = self._observe.log_sweep_discrepancies(resources)
        for href, rep in resources.items():
            self._observe.apply(href, rep, source=source)

        if not self._discovered:
            self._run_discovery(resources)
            await self._attempt_observe_mode()
        elif just_downgraded_from_observe:
            await self._attempt_observe_mode()
        elif self._observe.mode == MODE_POLL:
            await self._maybe_retry_observe_mode()

        # Schedule sub-polls for hot/warm hrefs between summary polls
        # (no-op in observe-primary mode unless this cycle's sweep found a
        # mismatch; _run_subpolls checks the mode/force).
        if self._hot_hrefs or self._warm_hrefs:
            self._subpoll_task = self.hass.async_create_task(
                self._run_subpolls(force=sweep_mismatch), name="localthings_subpoll"
            )

        return flatten(self.bound, self._cache.snapshot())

    # ------------------------------------------------------------------
    # Command dispatch (called by entity platforms in Task 5)
    # ------------------------------------------------------------------

    async def async_send_command(self, bound_entity: BoundEntity,
                                 payload: Any) -> None:
        """Write a value to the device. Fire-and-forget style."""
        desc = bound_entity.desc
        write_fn = getattr(desc, 'write_fn', None)
        if write_fn is None:
            return
        href = bound_entity.href
        rep = self._cache.get(href or '') or {}
        result = write_fn(payload, rep, href)
        if result is None:
            self._log.warning("write_fn rejected payload %r for %s", payload, href)
            return
        path_segs, body = result

        def _do_put():
            sess = self._session
            if sess is None:
                raise RuntimeError("no session")
            self._observe.mark_write_pending(href)
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=8.0)
            self._log.info("PUT %s → code %#04x", href, code)

        try:
            await self.hass.async_add_executor_job(_do_put)
        except Exception as e:
            self._log.error("command failed for %s: %s", href, e)
        else:
            await self.async_request_refresh()
