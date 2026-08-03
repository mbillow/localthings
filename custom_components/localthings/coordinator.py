"""Coordinator for Local Things integration."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import threading
import time
import zlib
from datetime import timedelta
from typing import Any

import cbor2
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from smartthings_local.ocf.state_cache import StateCache
from smartthings_local.protocol.dtls_session import DtlsCoapSession

from .const import (
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DEVICE_SUPPORT_ISSUE_URL,
    DOMAIN,
    DTLS_LOCAL_PORT_BASE,
    SUMMARY_INTERVAL_S,
)
from .observe import GRACE_PERIOD_S, MODE_OBSERVE, MODE_POLL, ObserveManager
from .registry import CAPABILITIES
from .registry.adapter import flatten
from .registry.batch import parse_device0_batch
from .registry.by_type import resolve as resolve_registry
from .registry.capabilities.common import (
    merge_items_field,
    merge_options_field,
    remote_control_enabled,
    remote_control_required_for_write,
)
from .registry.discovery import BoundEntity
from .registry.identity import DeviceIdentity, read_identity
from .registry.subdevices import (
    Subdevice,
    canonical_view,
    discover_partitioned,
    enumerate_subdevices,
    normalize_seed_batch,
)

_LOGGER = logging.getLogger(__name__)

_SEED_PATH = ["device", "0"]


class _NoOpDescriptor:
    """StateCache requires a descriptor with an on_observation hook. This
    integration doesn't use per-capability observation hooks, so this is a
    deliberate no-op, not a placeholder for missing functionality."""

    def on_observation(self, state: dict, href: str, rep: dict) -> None:
        return None


_RECOVERY_RETRY_S = 600.0  # re-attempt observe mode this often while polling


def _local_source_port(host: str) -> int:
    """Deterministic UDP source port for this device's DTLS socket.

    Binding the same source port on every (re)connect keeps the client on one
    5-tuple, so the appliance evicts an orphaned session left by a previous run
    (unclean shutdown -> no DTLS close_notify) at handshake time per RFC 6347
    §4.2.8, instead of holding it for 5-15 min while the new session's reads
    hang. See DTLS_LOCAL_PORT_BASE in const.py. Requires smartthings-local
    >= 0.1.1 (the version that added DtlsCoapSession(local_port=...)).

    The port must be stable across restarts and unique per device on this HA
    host: the library's socket is unconnected (recvfrom), so two devices
    sharing a source port would mis-demux each other's datagrams. For the usual
    dotted-IPv4 host we use the last octet as the offset (unique on a /24);
    anything else folds a stable CRC32 into the same 256-wide window.
    """
    try:
        offset = int(ipaddress.IPv4Address(host)) & 0xFF
    except (ipaddress.AddressValueError, ValueError):
        offset = zlib.crc32(host.encode()) & 0xFF
    return DTLS_LOCAL_PORT_BASE + offset


def _is_placeholder_serial(serial: str) -> bool:
    """True for a non-empty serialNum that isn't actually a real identity.

    The ARTIK051_DONGLE_REF firmware family reports the literal string
    'Nothing(SVC)' for every unit -- non-empty, so the plain `if not
    serial` check below doesn't catch it, and `device_serial` feeds both
    the HA device-registry identifier and every entity's unique_id
    (entity.py), so two such units on the same install silently collide
    and the second one's entities get dropped (issue #83).

    Issue #189: the DA_WM_A51_20_COMMON (ARTIK051) laundry board family
    reports a flash-unset sentinel instead -- every character the same
    repeated hex digit (a washer and a dryer, two different physical
    units, both reported the literal serialNum 'FFFFFFFFFFFFFFF') -- which
    the 'nothing' check above doesn't catch either, so two such units
    collided on the config-entry unique_id and the second couldn't be
    added at all.

    Mirrors the identical helper in config_flow.py's `_probe_and_validate`
    -- kept separate rather than imported to avoid pulling the config-flow
    module into the runtime coordinator's import graph for a two-line
    check.
    """
    s = serial.strip()
    if s.lower().startswith("nothing"):
        return True
    upper = s.upper()
    return len(upper) >= 8 and len(set(upper)) == 1 and upper[0] in "0123456789ABCDEF"


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

    # A single reconnect is normal appliance-side behavior (see the
    # README's "Known device behavior" section) -- Samsung's firmware
    # drops the DTLS session briefly every now and then, and the
    # coordinator recovering from that on its own isn't something a user
    # needs to see at WARNING. Only escalate once reconnects pile up
    # within a trailing window (issue #119).
    #
    # The window can't be a literal 60s: consecutive reconnect attempts are
    # never closer together than one summary poll interval (SUMMARY_INTERVAL_S,
    # 30s) plus _RECONNECT_PAUSE_S, so at most ~2 can ever land inside a 60s
    # window regardless of how unhealthy the connection is -- a threshold of
    # 5 there could never fire, silently downgrading every reconnect
    # (including a persistently broken one) to INFO forever. 300s/3 instead:
    # reachable under normal polling, and 3 reconnects inside 5 minutes is
    # still a reasonable proxy for the README's "actually broken" case.
    _RECONNECT_WARN_WINDOW_S: float = 300.0
    _RECONNECT_WARN_THRESHOLD: int = 3

    # A block-level ACK timeout on the summary GET doesn't prove the
    # session is dead (see _poll_once) — require this many in a row
    # before treating it as one. A single slow transfer on an otherwise
    # fine session shouldn't tear down a working OBSERVE subscription.
    _POLL_TIMEOUT_LIMIT: int = 3

    # Timeouts for the two network round trips a write triggers: the PUT
    # itself (_do_put), then the confirming full /device/0 summary poll
    # async_send_command requests right after (_poll_once). Named here
    # (rather than left as inline literals) so the write-settle window
    # below can be sized to always outlast both — see async_send_command.
    _POST_TIMEOUT_S: float = 8.0
    _POLL_TIMEOUT_S: float = 35.0

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
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=SUMMARY_INTERVAL_S),
        )
        self._entry = entry
        self._session: DtlsCoapSession | None = None
        self._identity: DeviceIdentity | None = None
        self._discovered = False
        self.bound = []
        # Sibling indoor subdevices discovered on this connection (issue
        # #177) -- candidates set once, at first discovery, by
        # _enumerate_subdevices_blocking; narrowed by _run_discovery to the
        # ones that actually produced live primary state (see
        # subdevices.discover_partitioned). MAIN itself is never in this list
        # (see subdevices.canonical_view's docstring for why that's safe):
        # it's the *other* subdevices sharing this DTLS session, if any.
        self.subdevices: list[Subdevice] = []
        # Candidates _run_discovery's gate rejected (an unused SmartThings
        # slot that still answers its seed, e.g. the issue #177 reporter's
        # /device/2) -- surfaced in diagnostics alongside the materialized
        # ones so a report shows what was found and why it didn't become an
        # entity.
        self._skipped_subdevices: list = []
        # Those rejected candidates' raw reps, kept aside for diagnostics
        # only (see _live_subdevice_resources). They are deliberately not in the
        # state cache: nothing polls them again, so anything applied there
        # would sit frozen at its first-discovery value while looking as
        # live as every other href in `last_resources`.
        self._skipped_subdevice_resources: dict[str, dict] = {}
        # /multidevice/vs/0's rep, if this board answers it -- a plain
        # subdevice count that corroborates the liveness gate without deciding it.
        # Deliberately outside `resources`; see _enumerate_subdevices_blocking.
        self._multidevice: dict = {}
        # What each subdevice probe found, keyed by the seed href attempted --
        # surfaced in diagnostics so a report can tell "checked, nothing
        # there" apart from "never checked" (the same posture the
        # speculative-probe code this replaced documented in identity.py).
        self._subdevice_probes: dict[str, bool] = {}
        # canonical_resources() memo, keyed by (subdevice.kind, subdevice.key).
        # Invalidated in _on_cache_changed -- climate.py reads this on every
        # property access (is_legacy_board and friends), so it must not
        # rebuild an O(hrefs) view from scratch on every single property.
        self._canonical_cache: dict[tuple[str, str], dict] = {}
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
        self.one_ui_version: str = ""
        self._consecutive_poll_timeouts = 0
        self._unbound_hrefs: list[str] = []
        self._reconnect_times: list[float] = []

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

    def canonical_resources(self, subdevice: Subdevice) -> dict[str, dict]:
        """`subdevice`'s own view of the live snapshot, rewritten into the
        canonical hrefs (issue #177) the registry/platforms are written
        against -- see subdevices.canonical_view. A platform property that
        needs the *whole* resources dict (as opposed to one href via
        `resource()`/`last_resources.get(href)`) must use this instead of
        `last_resources`, or a sibling subdevice's own `/mode/vs/1` would leak
        into MAIN's canonical `/mode/vs/0` view (or vice versa) under
        exists_fn/is_legacy_board-style checks that scan the whole dict.

        Memoized per cache generation: climate.py calls this on every
        property read (is_legacy_board and friends), and building it is
        O(hrefs) -- _on_cache_changed clears the memo whenever the
        snapshot actually changes, not on every property access.
        """
        view_key = (subdevice.kind, subdevice.key)
        cached = self._canonical_cache.get(view_key)
        if cached is not None:
            return cached
        view = canonical_view(subdevice, self._cache.snapshot(), self.subdevices)
        self._canonical_cache[view_key] = view
        return view

    def device_info_for(self, subdevice: Subdevice) -> DeviceInfo:
        """DeviceInfo for one logical subdevice sharing this connection
        (issue #177) -- the master's own (unchanged) device_info for MAIN, or
        a linked child device for a discovered subdevice.

        Identifiers derive from the *master's* serial (device_serial) plus
        this subdevice's stable key, never from whatever serial the
        subdevice itself reports (or fails to) -- deterministic across
        reconnects whether or not this subdevice's own identity resource
        (/information/vs/<n>, or /<id>/information/vs/0) answered on the
        poll that first created the HA device. `serial_number` is set from
        that resource when present anyway -- it's informational, not an
        identifier.
        """
        if subdevice.kind == "main":
            return self.device_info
        info = self.canonical_resources(subdevice).get("/information/vs/0", {})
        model_num = info.get("x.com.samsung.da.modelNum", "")
        model = model_num.split("|", 1)[0] if model_num else ""
        serial = info.get("x.com.samsung.da.serialNum") or None
        base_name = self.device_info.get("name") or "Samsung Appliance"
        if model:
            label = model.replace("_", " ").title()
        else:
            # This poll never got (or never will get) the subdevice's own
            # identity resource -- fall back to a generic per-subdevice label
            # rather than leaving the device unnamed. 'Subdevice <n>' only
            # makes sense for an indexed subdevice (the key is a small
            # ordinal); UUID-prefixed subdevices are never more than one per
            # connection today, so there's no ordinal to show.
            label = (
                f"Subdevice {subdevice.key}"
                if subdevice.kind == "indexed"
                else "Secondary Subdevice"
            )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.device_serial}_{subdevice.key}")},
            via_device=(DOMAIN, self.device_serial),
            name=f"{base_name} {label}",
            manufacturer=self.device_info.get("manufacturer") or "Samsung",
            model=model or None,
            serial_number=serial,
        )

    @property
    def observe_mode(self) -> str:
        return self._observe.mode

    def _connect_session(self) -> None:
        host = self._entry.data[CONF_HOST]
        port = self._entry.data[CONF_PORT]
        cert_pem = self._entry.data[CONF_LEAF_CERT_PEM]
        key_pem = self._entry.data[CONF_LEAF_KEY_PEM]

        sess = DtlsCoapSession(
            host,
            port,
            cert_pem=cert_pem,
            key_pem=key_pem,
            on_notification=self._observe.on_notification,
            local_port=_local_source_port(host),
        )
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
            with contextlib.suppress(Exception):
                sess.close()

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
        self._canonical_cache.clear()
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
        assert sess is not None
        try:
            # A slow device can still be mid-transfer (block 8, block 11)
            # when a tighter deadline cuts it off — that's a poll that
            # would have succeeded, not a dead session. 35s gives a slow
            # blockwise transfer room to actually finish instead of
            # generating a TimeoutError every cycle.
            code, payload = sess.get(_SEED_PATH, timeout=self._POLL_TIMEOUT_S)
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
        result = parse_device0_batch(body) if isinstance(body, list) else {}
        # Refresh every already-enumerated sibling subdevice's seed collection
        # on this same summary poll (issue #177) -- without this, a subdevice's
        # climate card would show only its enumeration-time snapshot forever.
        for subdevice in self.subdevices:
            result.update(self._poll_subdevice_seed(subdevice))
        return result

    def _poll_subdevice_seed(self, subdevice: Subdevice) -> dict[str, dict]:
        """GET one subdevice's seed Collection and return its batch,
        normalized to real hrefs. A sibling failing to answer is a debug
        log, never a failed poll -- the master must not go unavailable
        because a sibling timed out or dropped off (e.g. the issue #177
        reporter's /device/2, a SmartThings-unused component that may not
        always respond). Blocking -- called from _poll_once, already in
        executor."""
        sess = self._session
        if sess is None:
            return {}
        if subdevice.flat_hrefs:
            return self._poll_subdevice_flat_hrefs(subdevice, sess)
        try:
            code, payload = sess.get(list(subdevice.seed_path), timeout=10.0)
            if code == 0x45 and payload:
                body = cbor2.loads(payload)
                if isinstance(body, list):
                    return normalize_seed_batch(subdevice, parse_device0_batch(body))
        except Exception as e:
            self._log.debug("subdevice %s seed poll failed: %s", subdevice.key, e)
        return {}

    def _poll_subdevice_flat_hrefs(self, subdevice: Subdevice, sess) -> dict[str, dict]:
        """Re-poll a flat-mode prefixed subdevice's hrefs individually
        (issue #205) -- it has no Collection endpoint to batch-refresh
        through (see enumerate_subdevices' fallback), so each canonical
        href assumed at enumeration time (cloned from the master's own
        state, issue #265 -- not yet confirmed live under this subdevice's
        own prefix) gets its own GET under the subdevice's prefix, which is
        what turns the clone into this sibling's real value once it
        answers. A href failing to answer this cycle just drops out of the
        result and leaves whatever value is already cached (the clone, until
        corrected) in place -- same "never let a sibling's flakiness fail
        the master's poll" posture as the Collection path above.

        Takes `sess` from the caller (already None-checked there) rather
        than re-reading self._session -- async_close() can null that
        without holding _session_lock, and pace()/get() both need a live
        session on every iteration, not just the first.

        Skips any href already covered by the hot/warm sub-poll tiers
        (self._hot_hrefs/_warm_hrefs, in the same actual/on-the-wire form
        this method builds) -- those are already refreshed every 3s/6s by
        _run_subpolls, strictly more current than this once-per-summary-poll
        pass could offer, so re-fetching them here would only add GETs
        without adding freshness. A subdevice with many assumed hrefs
        (unlike a Collection batch, which is always one GET regardless of
        count) is otherwise a summary-poll cost that scales with its href
        count."""
        skip = set(self._hot_hrefs) | set(self._warm_hrefs)
        result: dict[str, dict] = {}
        first = True
        for href in subdevice.flat_hrefs:
            actual = subdevice.to_actual(href)
            if actual in skip:
                continue
            try:
                if not first:
                    sess.pace()
                first = False
                path = actual.strip("/").split("/")
                code, payload = sess.get(path, timeout=10.0)
                if code == 0x45 and payload:
                    rep = cbor2.loads(payload)
                    if isinstance(rep, dict):
                        result[actual] = rep
            except Exception as e:
                self._log.debug(
                    "subdevice %s flat href %s poll failed: %s",
                    subdevice.key,
                    href,
                    e,
                )
        return result

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
                path = href.strip("/").split("/")
                code, payload = self._session.get(path, timeout=10.0)
                if code == 0x45 and payload:
                    rep = cbor2.loads(payload)
                    if isinstance(rep, dict):
                        self._observe.apply(href, rep, source="poll")
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
        for i in range(1, 10):  # slots 1..9  (T+3 s … T+27 s)
            await asyncio.sleep(step)
            hrefs = list(hot) + (list(warm) if i % 2 == 0 else [])
            async with self._session_lock:
                try:
                    await self.hass.async_add_executor_job(self._poll_hrefs_blocking, hrefs)
                except Exception as e:
                    self._log.debug("sub-poll batch failed: %s", e)

    # ------------------------------------------------------------------
    # Discovery (runs once on first successful poll)
    # ------------------------------------------------------------------

    def _enumerate_subdevices_blocking(self, resources: dict[str, dict]) -> dict[str, dict]:
        """One-time (first discovery only) probe for sibling indoor subdevices
        sharing this connection (issue #177) -- see
        registry.subdevices.enumerate_subdevices for the two detection
        patterns. Blocking -- runs in executor, under the session lock
        (shares the same DTLS session _poll_once just used this cycle).

        Sets self.subdevices to every *candidate* the probes turned up
        (self._subdevice_probes as a side effect too) and returns `resources`
        merged with whatever each candidate's seed returned, so this cycle's
        _run_discovery sees every candidate's state without a second poll
        round trip. `_run_discovery` is what narrows self.subdevices down to
        the ones that are actually live (see discover_partitioned) -- this
        method doesn't know how to tell an unused SmartThings slot (the
        issue #177 reporter's /device/2) from a real sibling, only that
        something answered.
        """
        if self._session is None:
            self._connect_session()
        sess = self._session
        if sess is None:
            return resources
        oic_res = self._identity.raw.get("/oic/res", []) if self._identity else []
        probes: dict[str, bool] = {}
        subdevices, extra = enumerate_subdevices(
            sess,
            resources,
            oic_res,
            probe_log=lambda href, found: probes.__setitem__(href, found),
        )
        self.subdevices = subdevices
        self._subdevice_probes = probes
        # /multidevice/vs/0 is corroborating metadata, not appliance state,
        # and it is probed on *every* device -- so it must not join the
        # returned resources dict. Two things go wrong if it does. It would
        # reach discovery on families whose registry doesn't ignore that
        # href (only the AC one does), binding to nothing and raising a
        # spurious "incomplete capability coverage" repair for every washer
        # or fridge whose firmware happens to answer it. And it is fetched
        # once here and never polled again, so applying it to the state
        # cache would freeze it there exactly like a rejected candidate's
        # reps (see _live_subdevice_resources). Kept aside for diagnostics and
        # for the numofsubdevice cross-check in _run_discovery instead.
        self._multidevice = extra.pop("/multidevice/vs/0", {})
        return {**resources, **extra}

    def _live_subdevice_resources(self, resources: dict[str, dict]) -> dict[str, dict]:
        """`resources` minus every href belonging to a candidate subdevice the
        liveness gate rejected (issue #177).

        Called once, between _run_discovery and the first cache apply, so a
        rejected slot's reps are seen by the gate and then dropped rather
        than frozen into the cache forever -- see the call site. The reps
        themselves are kept in _skipped_subdevice_resources for diagnostics,
        which is the only thing that still wants them.
        """
        if not self._skipped_subdevices:
            return resources
        kept: dict[str, dict] = {}
        skipped: dict[str, dict] = {}
        for href, rep in resources.items():
            bucket = (
                skipped
                if any(skip.subdevice.owns(href) for skip in self._skipped_subdevices)
                else kept
            )
            bucket[href] = rep
        self._skipped_subdevice_resources = skipped
        return kept

    def _run_discovery(self, resources: dict[str, dict]) -> None:
        # Reported for diagnostics only -- it names the firmware generation
        # ('7.0 Air conditioner' is Tizen Lite), which is useful when triaging
        # an issue. It does not route: only a minority of hardware reports it
        # at all, and every device that does is already typed by its modelNum.
        self.one_ui_version = (
            resources.get("/otninformation/vs/0", {})
            .get("swVersionInfo", {})
            .get("oneUiVersion", "")
        )
        info = resources.get("/information/vs/0", {})
        unbound: list[str] = []
        hot, warm = set(), set()

        def _tier_log(href: str, tier: str) -> None:
            if tier == "hot":
                hot.add(href)
            elif tier == "warm":
                warm.add(href)

        model_num = info.get("x.com.samsung.da.modelNum", "")
        description = info.get("x.com.samsung.da.description", "")

        # Partitioned discovery (issue #177): the main pass binds every href
        # owned by no subdevice; one further pass per *candidate* subdevice
        # binds its own canonical view, resolving its own device type from
        # its own /information/vs/0 when it reports one and falling back to
        # the master's registry otherwise. See subdevices.discover_partitioned
        # -- it also gates each candidate down to whether it actually
        # produced live primary state (the issue #177 reporter's /device/2,
        # an unused SmartThings slot, answers its seed but never does), so
        # self.subdevices below is narrowed to the ones that passed, not
        # every candidate _enumerate_subdevices_blocking found. For a device
        # with no candidates (self.subdevices == []) this is exactly the
        # single discover() call this method used to make.
        bound, device_type_name, materialized, skipped = discover_partitioned(
            resources,
            self.subdevices,
            resolve_registry,
            CAPABILITIES,
            log=unbound.append,
            tier_log=_tier_log,
            oic_device_types=self._identity.device_types if self._identity else (),
        )
        self.subdevices = materialized
        self._skipped_subdevices = skipped
        for skip in skipped:
            self._log.info(
                "subdevice %s (%s) answered its seed but produced no live "
                "primary state; not materialized (hrefs=%s)",
                skip.subdevice.key,
                skip.subdevice.kind,
                list(skip.hrefs),
            )
        # Corroborating signal, not a gate (DESIGN-177.md section 4):
        # /multidevice/vs/0's numofsubdevice is a plain count the issue
        # #177 reporter's board reports independently of the liveness gate
        # above. Log, don't raise, on a disagreement -- only this one board
        # family is known to expose the resource at all, so a mismatch is a
        # "look into this" signal for triage, not proof either side is
        # wrong.
        numofsubdevice = self._multidevice.get("x.com.samsung.da.numofsubdevice")
        if numofsubdevice is not None:
            try:
                reported = int(numofsubdevice)
            except (TypeError, ValueError):
                reported = None
            subdevice_count = len(materialized) + 1  # +1 for the master itself
            if reported is not None and reported != subdevice_count:
                self._log.debug(
                    "/multidevice/vs/0 reports numofsubdevice=%r but %d "
                    "subdevice(s) materialized (including the master)",
                    numofsubdevice,
                    subdevice_count,
                )
        if device_type_name is not None:
            self._log.debug("device type: %s (modelNum=%r)", device_type_name, model_num)
        else:
            # All three: detection reads each of them (oic device type, then
            # board token, then consumer-model code), and this line is what a
            # user pastes into an issue -- modelNum alone doesn't identify a
            # washer or dryer, and device_types is often empty even when
            # populated hardware exists for a type we don't map yet.
            self._log.warning(
                "unknown device type modelNum=%r description=%r device_types=%r; using common caps",
                model_num,
                description,
                self._identity.device_types if self._identity else (),
            )
        self.device_type_name = device_type_name
        self.bound = bound
        self._unbound_hrefs = unbound

        serial = info.get("x.com.samsung.da.serialNum", "")
        if not serial or _is_placeholder_serial(serial):
            serial = self._entry.data[CONF_HOST]
        self.device_serial = serial

        ident = self._identity
        device_type = (
            device_type_name.replace("_", " ").title() if device_type_name else "Appliance"
        )
        model = model_num.split("|", 1)[0] if model_num else (ident.model if ident else "")
        name = f"Samsung {device_type} ({model})" if model else f"Samsung {device_type}"
        mfr = (ident.manufacturer if ident else "") or "Samsung"

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=name,
            manufacturer=mfr,
            model=model,
        )
        self._update_coverage_gap_issue(device_type_name is None, unbound, name)

        self._hot_hrefs = sorted(hot)
        self._warm_hrefs = sorted(warm)

        self._discovered = True
        self._log.info(
            "discovered %d entities (serial=%s) hot=%s warm=%s subdevices=%s",
            len(bound),
            serial,
            self._hot_hrefs,
            self._warm_hrefs,
            [su.key for su in self.subdevices],
        )

    def _update_coverage_gap_issue(
        self,
        unknown_type: bool,
        unbound_hrefs: list[str],
        device_name: str,
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
                self.hass,
                DOMAIN,
                issue_id,
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
            self._observe.try_enter_observe_mode,
            sess,
            hrefs,
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

        Never defers before the first successful discovery (issue #254).
        Deferring is a *mid-session* judgement call — "keep the entities we
        already have and try again next cycle" — which is only coherent once
        there are entities to keep. Pre-discovery the same exception type
        means something else entirely: `_poll_once` calls `_connect_session`,
        so `connect()`'s own handshake timeout surfaces here as a
        `TimeoutError` too, and that is a dead connection, not a slow
        transfer. Deferring it returned an empty dict instead of raising,
        which `DataUpdateCoordinator` counts as a successful first refresh —
        and since platforms enumerate `bound` exactly once, the entry loaded
        with zero entities and stayed that way until a manual reload.
        """
        if not self._discovered:
            return False
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

    def _reconnect_is_frequent(self) -> bool:
        """True once this cycle's reconnect is the Nth within the trailing
        warn window -- see _RECONNECT_WARN_WINDOW_S/_RECONNECT_WARN_THRESHOLD."""
        now = time.monotonic()
        self._reconnect_times = [
            t for t in self._reconnect_times if now - t < self._RECONNECT_WARN_WINDOW_S
        ]
        self._reconnect_times.append(now)
        return len(self._reconnect_times) >= self._RECONNECT_WARN_THRESHOLD

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
                        type(e).__name__,
                        e,
                    )
                    return flatten(self.bound, self._cache.snapshot())
                self._consecutive_poll_timeouts = 0
                # One reconnect attempt — pause briefly so the device can
                # clean up its DTLS session state before we knock again.
                # A lone reconnect is routine (see the README's "Known
                # device behavior" section); only warn once they're piling
                # up within the trailing window.
                if self._reconnect_is_frequent():
                    self._log.warning("poll failed, reconnecting: %s", e)
                else:
                    self._log.info("poll failed, reconnecting: %s", e)
                await self.hass.async_add_executor_job(self._close_session)
                await asyncio.sleep(self._RECONNECT_PAUSE_S)
                try:
                    resources = await self.hass.async_add_executor_job(self._poll_once)
                except Exception as e2:
                    self._log.error("poll failed after reconnect: %s", e2)
                    snapshot = self._cache.snapshot()
                    # `self._discovered` is the same precondition
                    # `_defer_reconnect_for` applies (issue #254): returning
                    # degraded-but-successful data is only meaningful once
                    # there are bound entities to carry it. Pre-discovery the
                    # cache happens to always be empty -- every apply() site
                    # is gated on post-discovery state -- so this arm is
                    # unreachable then, but that is a non-local accident
                    # across four call sites, not something to rely on.
                    if self._discovered and snapshot:
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

        if not self._discovered:
            # One-time (issue #177): find out whether this connection has
            # sibling indoor subdevices before the first discovery pass, and
            # fold their seed resources into this cycle's snapshot so
            # discovery sees every subdevice's state on the very first poll
            # rather than waiting a cycle. Runs under its own session-lock
            # scope (the poll above already released the lock) since it
            # shares the same DTLS session.
            async with self._session_lock:
                resources = await self.hass.async_add_executor_job(
                    self._enumerate_subdevices_blocking, resources
                )

        source = "sweep" if self._discovered else "poll"
        first_cycle = not self._discovered
        if first_cycle:
            # Discovery runs *before* the apply loop below, not after it, so
            # a rejected candidate's resources never reach the state cache
            # at all (issue #177). Enumeration has to fetch every candidate's
            # seed to evaluate the liveness gate, but only the subdevices that
            # pass it are ever polled again -- applying the rest would freeze
            # ~14 hrefs per rejected slot into the cache on this one cycle
            # and leave them there forever, indistinguishable from live
            # state in `last_resources` and in the diagnostics dump built
            # from it. StateCache has no eviction, so the only way to keep
            # them out is to not put them in. Safe to reorder: _run_discovery
            # reads the dict passed to it and never the cache, and
            # log_sweep_discrepancies below can't fire on a first cycle
            # (observe mode is only ever attempted after discovery).
            self._run_discovery(resources)
            resources = self._live_subdevice_resources(resources)
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

        if first_cycle or just_downgraded_from_observe:
            await self._attempt_observe_mode()
        elif self._observe.mode == MODE_POLL:
            await self._maybe_retry_observe_mode()

        # Schedule sub-polls for hot/warm hrefs between summary polls
        # (no-op in observe-primary mode unless this cycle's sweep found a
        # mismatch; _run_subpolls checks the mode/force). A background task,
        # not async_create_task: this loop is self-limiting (cancelled and
        # recreated every refresh cycle, see the cancel() above) and owned
        # entirely by the coordinator, so it has no business being tracked by
        # HA's own startup/shutdown sequencing -- async_create_task ties it
        # in regardless, so a subpoll cycle in flight (up to ~27s,
        # _SUBPOLL_STEP_S x 9 slots) delays both (issue #207).
        if self._hot_hrefs or self._warm_hrefs:
            self._subpoll_task = self.hass.async_create_background_task(
                self._run_subpolls(force=sweep_mismatch), name="localthings_subpoll"
            )

        return flatten(self.bound, self._cache.snapshot())

    # ------------------------------------------------------------------
    # Command dispatch (called by entity platforms in Task 5)
    # ------------------------------------------------------------------

    async def async_send_command(self, bound_entity: BoundEntity, payload: Any) -> None:
        """Write a value to the device. Fire-and-forget style.

        A description-level validate_fn (currently SwitchDesc only) runs
        here rather than per-platform, so rejecting a write with a
        user-facing message -- as opposed to write_fn's silent no-op below
        -- is available to every platform for free. The remote-control
        check runs first and applies to every platform unconditionally,
        ahead of any description-specific validate_fn -- unless the user has
        opted this device out of it via CONF_BYPASS_REMOTE_CONTROL (issue
        #54: some devices accept certain writes, e.g. a washer's default
        dosing levels, even while reporting remote control off, so the
        block's assumption doesn't hold for every model), or the laundry
        firmware flag isModelSettingWithoutSC declares settings writable
        without Smart Control (cycle start/pause/stop on /operational/state
        still require it)."""
        desc = bound_entity.desc
        write_fn = getattr(desc, "write_fn", None)
        if write_fn is None:
            return
        href = bound_entity.href
        rep = self._cache.get(href or "") or {}
        resources = self._cache.snapshot()
        bypass_remote_control = self._entry.options.get(CONF_BYPASS_REMOTE_CONTROL, False)
        if (
            not bypass_remote_control
            and remote_control_required_for_write(resources, href or "")
            and not remote_control_enabled(resources)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="remote_control_disabled",
            )
        validate_fn = getattr(desc, "validate_fn", None)
        if validate_fn is not None:
            error = validate_fn(payload, rep, resources)
            if error:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key=error,
                )
        try:
            result = write_fn(payload, rep, href, resources)
        except TypeError:
            result = write_fn(payload, rep, href)
        if result is None:
            self._log.warning("write_fn rejected payload %r for %s", payload, href)
            return
        path_segs, body = result

        # The write's actual target, not necessarily bound_entity.href. Most
        # descriptors write to the same resource they're bound to, but a
        # composite entity -- the AC's ClimateDesc, bound to /mode/vs/0 --
        # drives writes to several sibling resources via path_segs
        # (/power/0, /temperature/desired/0, /wind/strength/vs/0, ...) that
        # write_fn picks per payload (see airconditioner._climate_write).
        # Applying the optimistic value and settle guard below to
        # bound_entity.href instead of this target protected the wrong
        # resource: /mode/vs/0 got the (nonsensical, wrong-shaped) optimistic
        # merge while the resource the climate entity actually displays from
        # (e.g. /power/0) never got one, so HA kept showing the pre-write
        # state until the next real read of that resource -- the 20-60s lag
        # in issues #17/#53, which survived the earlier optimistic-apply fix
        # (issue #27) because that fix applied to the wrong href too.
        #
        # write_fn's path_segs are canonical (issue #177) -- a subdevice's
        # ClimateDesc is bound to its own *actual* /mode/vs/1 (or
        # /<id>/mode/vs/0) href, but _climate_write only knows the canonical
        # sibling hrefs (e.g. ['power', 'vs', '0']). Translate through this
        # bound entity's own subdevice so the optimistic apply, the settle
        # guard and the POST below all target that subdevice's real resource --
        # to_actual is the identity transform for MAIN, so a device with no
        # subdevices writes exactly where it always did.
        write_href = bound_entity.subdevice.to_actual("/" + "/".join(path_segs))
        path_segs = [s for s in write_href.strip("/").split("/") if s]

        # Apply the write optimistically before starting the settle guard,
        # not after -- mark_write_pending gates every source (poll, sweep,
        # observe) through the same apply(), itself included, so flipping
        # this order would have the guard drop the one update it exists to
        # protect. Without an optimistic value in the cache for it to hold
        # onto, the settle window was just delaying the real device
        # confirmation for a few seconds on every write, which read exactly
        # like the write being silently reverted (issue #27).
        #
        # settle_s must outlast the PUT and the async_request_refresh()
        # below combined, not just DEFAULT_SETTLE_S's fixed few seconds --
        # that refresh is a full /device/0 summary poll, which
        # _POLL_TIMEOUT_S itself admits can legitimately take tens of
        # seconds on these devices (see _poll_once), and some writes settle
        # on the device itself well after that: issue #9's washer packs
        # cycle/detergent/softener selection into the same /course/vs/0
        # options[] array, and picking a new value there visibly needs a
        # few seconds of internal validation/dispenser movement before the
        # device's own state agrees -- while /washer/vs/0's temperature/
        # spin fields (plain flags, no device-side settling) confirm
        # instantly on the same device. A short fixed window expired while
        # the confirm poll was still in flight (or before the device had
        # caught up internally), so that stale read landed unprotected and
        # reverted the optimistic value, self-correcting again only once a
        # later poll finally saw the real change -- read by the user as the
        # write "reverting, then re-applying itself" a few seconds later.
        #
        # An earlier attempt at this also released the guard early, right
        # after the confirming refresh completed, to avoid shutting out
        # unrelated real updates (another automation, the physical remote)
        # for the rest of settle_s. That was reverted: releasing the guard
        # the moment one round trip finishes doesn't mean the device has
        # actually caught up (exactly the slow-settling case above), and it
        # introduced its own races around overlapping writes to the same
        # href. Simpler and safer to just hold the guard for the full,
        # generously-sized window and let it expire on its own.
        # write_fn bodies that touch x.com.samsung.da.options or
        # x.com.samsung.da.items carry only the changed token(s)/item now
        # (issue #54 for options; the AC vendor temperature write for items --
        # confirmed sufficient on the wire, the device merges the rest itself),
        # not the whole packed array. observe.apply()'s field-level
        # {**cached, **rep} merge doesn't know that -- handed the bare
        # partial value, it would replace the cached field outright and wipe
        # every sibling option/item for the rest of the settle window.
        # Pre-merge it here the same way the device does, so the optimistic
        # cache entry stays complete; the minimal `body` below is still
        # exactly what goes out over the wire.
        optimistic_body = body
        new_options = body.get("x.com.samsung.da.options")
        if isinstance(new_options, list):
            cached_options = (self._cache.get(write_href) or {}).get("x.com.samsung.da.options")
            optimistic_body = {
                **optimistic_body,
                "x.com.samsung.da.options": merge_options_field(cached_options, new_options),
            }
        # Same fact, items[] shape (e.g. airconditioner._climate_write's vendor
        # temperature write, which now carries only {id, desired} -- see that
        # module for the write-side half of this).
        new_items = body.get("x.com.samsung.da.items")
        if isinstance(new_items, list):
            cached_items = (self._cache.get(write_href) or {}).get("x.com.samsung.da.items")
            optimistic_body = {
                **optimistic_body,
                "x.com.samsung.da.items": merge_items_field(cached_items, new_items),
            }
        self._observe.apply(write_href, optimistic_body, source="optimistic")
        self._observe.mark_write_pending(
            write_href, settle_s=self._POST_TIMEOUT_S + self._POLL_TIMEOUT_S
        )

        def _do_put():
            sess = self._session
            if sess is None:
                raise RuntimeError("no session")
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=self._POST_TIMEOUT_S)
            self._log.info("PUT %s → code %#04x", write_href, code)

        try:
            await self.hass.async_add_executor_job(_do_put)
        except Exception as e:
            self._log.error("command failed for %s: %s", write_href, e)
        else:
            await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Debug raw write (issue #54): a power-user escape hatch for the
    # options-flow debug panel, letting a user POST an arbitrary partial
    # body to an arbitrary href to pin down device-specific write behavior
    # without waiting on a new release. Deliberately bypasses the
    # remote-control block and every write_fn/validate_fn above -- that's
    # the whole point, so use with care.
    # ------------------------------------------------------------------

    def _raw_write_blocking(self, path_segs: list[str], body: dict, href: str) -> tuple[int, dict]:
        """Debug primitive: POST an arbitrary patch, then read the href
        back for ground truth. Blocking -- runs in executor."""
        if self._session is None:
            self._connect_session()
        sess = self._session
        if sess is None:
            raise RuntimeError("no session")
        code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=self._POST_TIMEOUT_S)
        self._log.warning("DEBUG raw write POST %s %r → code %#04x", href, body, code)
        new_rep: dict = {}
        try:
            sess.pace()
            rcode, payload = sess.get(path_segs, timeout=10.0)
            if rcode == 0x45 and payload:
                rep = cbor2.loads(payload)
                if isinstance(rep, dict):
                    self._observe.apply(href, rep, source="poll")
                    new_rep = rep
        except Exception as e:
            self._log.debug("raw write follow-up read failed: %s", e)
        return code, new_rep

    async def async_raw_write(self, href: str, body: dict) -> tuple[int, dict]:
        """Debug-only arbitrary write (issue #54). Bypasses the
        remote-control block and all write_fn/validate_fn logic; sends
        `body` verbatim as a partial-rep PATCH to `href`. Returns
        (coap_code, new_rep) where new_rep is the href's value read back
        right after the write. Used by the options-flow debug panel to
        help users pin down device-specific write behavior without a new
        release."""
        if not isinstance(body, dict) or not body:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="debug_payload_empty",
            )
        path_segs = [s for s in href.strip("/").split("/") if s]
        if not path_segs:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="resource_href_required",
            )
        norm_href = "/" + "/".join(path_segs)
        async with self._session_lock:
            code, new_rep = await self.hass.async_add_executor_job(
                self._raw_write_blocking, path_segs, body, norm_href
            )
        # Hasten a full summary poll so entities on other resources catch
        # up too -- a debug write can affect siblings, not just its href.
        await self.async_request_refresh()
        return code, new_rep
