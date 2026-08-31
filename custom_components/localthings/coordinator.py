"""Coordinator for Local Things integration."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import threading
import time
import zlib
from dataclasses import asdict
from datetime import timedelta
from typing import Any, cast

import cbor2
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from smartthings_local.errors import AuthenticationError
from smartthings_local.ocf.state_cache import StateCache
from smartthings_local.protocol.dtls_session import DtlsCoapSession

from . import cloudcourse
from . import session as session_factory
from .cloudcourse import CloudCourses
from .cloudcourse import persist as cloud_persist
from .const import (
    AUTH_OWNER_PSK,
    CONF_AUTH_TYPE,
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_CLOUD_COURSES,
    CONF_CLOUD_COURSES_ENABLED,
    CONF_DEVICE_KEY,
    CONF_DEVICE_TYPE,
    CONF_HOST,
    CONF_LEARN_MODES,
    CONF_LEARNED_MODES,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_PORT,
    CONF_SERIAL,
    DEFAULT_CLOUD_COURSES_ENABLED,
    DEFAULT_LEARN_MODES,
    DEVICE_SUPPORT_ISSUE_URL,
    DOMAIN,
    DTLS_LOCAL_PORT_BASE,
    SUMMARY_INTERVAL_S,
)
from .credentials import (
    InvalidCredentialConfig,
    OwnerPskDeviceMismatch,
    require_matching_ocf_uuid,
)
from .devices import set_via_device
from .learned import LEARNABLE, LearnedModes, persist
from .observe import GRACE_PERIOD_S, MODE_OBSERVE, MODE_POLL, ObserveManager
from .registry import CAPABILITIES
from .registry.adapter import _key, flatten
from .registry.batch import parse_device0_batch
from .registry.by_type import resolve as resolve_registry
from .registry.capabilities.common import (
    merge_items_field,
    merge_options_field,
    remote_control_enabled,
    remote_control_required_for_write,
)
from .registry.capabilities.laundry import cycle_options
from .registry.discovery import BoundEntity
from .registry.encode import from_json_safe, json_safe
from .registry.entities import ClimateDesc
from .registry.identity import (
    DeviceIdentity,
    device_display_name,
    ocf_device_key,
    read_identity,
    read_ocf_device_id,
    resolve_model,
    resolve_serial,
)
from .registry.subdevices import (
    MAIN,
    Subdevice,
    canonical_view,
    discover_partitioned,
    enumerate_subdevices,
    normalize_seed_batch,
)
from .rekey import rekey_entry

# Sentinel for apply_cloud_courses: "leave this field as it is",
# distinct from None which means "clear it".
_KEEP = object()

_LOGGER = logging.getLogger(__name__)

_SEED_PATH = ["device", "0"]

# Discovery snapshot (issue #295): exactly what the last successful first
# cycle fed _run_discovery, so a restart can register the same entities
# while the appliance is unreachable. Kept in .storage rather than on the
# config entry -- it's device state, not configuration, and runs to tens of
# kilobytes.
_SNAPSHOT_VERSION = 1


def snapshot_store(hass: HomeAssistant, entry: ConfigEntry) -> Store[dict[str, Any]]:
    """This entry's discovery-snapshot store. A free function so
    `async_remove_entry` can delete the file without standing up a whole
    coordinator to reach it."""
    return Store(hass, _SNAPSHOT_VERSION, f"{DOMAIN}.{entry.entry_id}.discovery")


class _NoOpDescriptor:
    """No-op: StateCache requires an on_observation hook; this integration
    doesn't use per-capability observation hooks."""

    def on_observation(self, state: dict, href: str, rep: dict) -> None:
        return None


_RECOVERY_RETRY_S = 600.0  # re-attempt observe mode this often while polling


def _local_source_port(host: str) -> int:
    """Deterministic UDP source port for this device's DTLS socket.

    Binding the same source port across reconnects lets the appliance evict
    an orphaned session (unclean shutdown, no close_notify) at handshake
    time per RFC 6347 §4.2.8, instead of holding it 5-15 min. See
    DTLS_LOCAL_PORT_BASE. Requires smartthings-local >= 0.1.1.

    Must stay unique per device on this host too. That used to be load-
    bearing for demuxing: an unconnected socket handed every device's
    datagrams to whichever recvfrom() happened to be listening on their
    shared port. smartthings-local >= 0.1.3 connect()s its UDP socket
    instead (see endpoint.py's open_connected_udp_socket), so the kernel
    already filters incoming datagrams to each session's own resolved peer
    -- but a distinct port per device keeps that guarantee from ever
    depending on it, and keeps captures/logs unambiguous. Last IPv4 octet
    as offset for the common case; a stable CRC32 fold otherwise.
    """
    try:
        offset = int(ipaddress.IPv4Address(host)) & 0xFF
    except (ipaddress.AddressValueError, ValueError):
        offset = zlib.crc32(host.encode()) & 0xFF
    return DTLS_LOCAL_PORT_BASE + offset


# Debug raw write/read caps (issue #300) -- generous enough for a real
# probing session (the wall-oven reporter's own sequences run well under
# 10 steps) while bounding how long one service call can hold up polling.
_DEBUG_MAX_WRITES = 10
_DEBUG_MAX_SETTLE_S = 30.0
_DEBUG_MAX_VERIFY_AFTER_S = 60.0


def _href_to_path_segs(href: str) -> list[str]:
    """'/mode/vs/0' -> ['mode', 'vs', '0'], the shape `sess.get`/`sess.post`
    take. Shared by every raw debug read/write path."""
    return [s for s in str(href).strip("/").split("/") if s]


def normalize_href(href: str) -> str:
    """A user-typed href in one canonical spelling. Public because
    services.py must normalize before `Subdevice.to_actual`, which rewrites
    only a trailing '0' segment: '/mode/vs/0/' slips through it unchanged
    and would land on the master's resource, not the subdevice's."""
    return "/" + "/".join(_href_to_path_segs(href))


def _coap_code_str(code: int) -> str:
    """Raw CoAP response code -> its 'C.DD' rendering (e.g. 0x44 -> '2.04'),
    the class/detail split RFC 7252 §12.1.2 defines. `raw_code` is kept
    alongside it in every debug response so a caller can format it
    differently."""
    return f"{code >> 5}.{code & 0x1F:02d}"


def _coap_accepted(code: int) -> bool:
    """True for a 2.xx class CoAP response."""
    return (code >> 5) == 2


def _validate_debug_write_item(item: dict) -> tuple[list[str], str, dict, float]:
    """The checks `async_raw_write` has always applied to a single write
    (issue #54), reused per-item by `async_raw_write_sequence` (issue
    #300). Payload is checked before href, matching the original
    single-write order -- not load-bearing for any test, just avoiding a
    silent behavior change in the refactor."""
    payload = item.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="debug_payload_empty",
        )
    path_segs = _href_to_path_segs(item.get("href", ""))
    if not path_segs:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="resource_href_required",
        )
    settle = item.get("settle") or 0.0
    if not 0 <= settle <= _DEBUG_MAX_SETTLE_S:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="debug_settle_out_of_range",
        )
    return path_segs, "/" + "/".join(path_segs), payload, settle


class LocalThingsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages one Samsung appliance: session, discovery, polling."""

    bound: list[BoundEntity]
    device_info: DeviceInfo
    device_key: str

    # Class-level so tests can shrink these via patch.object() without
    # touching the production defaults.
    _SUBPOLL_STEP_S: float = SUMMARY_INTERVAL_S / 10  # 3.0 s
    _OBSERVE_GRACE_PERIOD_S: float = GRACE_PERIOD_S
    _RECONNECT_PAUSE_S: float = 5.0

    # A single reconnect is normal appliance behavior (README's "Known
    # device behavior"); only escalate once they pile up in a trailing
    # window (issue #119). Can't be a literal 60s: consecutive attempts are
    # always >= one summary interval + _RECONNECT_PAUSE_S apart, so at most
    # ~2 could ever land in 60s regardless of how unhealthy the connection
    # is. 300s/3 is reachable under normal polling and still a reasonable
    # "actually broken" proxy.
    _RECONNECT_WARN_WINDOW_S: float = 300.0
    _RECONNECT_WARN_THRESHOLD: int = 3

    # A block-level ACK timeout on the summary GET doesn't prove the session
    # is dead (see _poll_once) -- require this many in a row before treating
    # it as one, so one slow transfer doesn't tear down a working OBSERVE
    # subscription. Only covers that ambiguous case: smartthings-local
    # >= 0.1.6 raises a distinct SessionClosedError, not a TimeoutError, the
    # moment a dead reader thread is confirmed, and _defer_reconnect_for
    # never defers that -- see its docstring for what changed there.
    _POLL_TIMEOUT_LIMIT: int = 3

    # Named (not inline literals) so the write-settle window in
    # async_send_command can be sized to outlast both round trips a write
    # triggers: the PUT itself, then the confirming summary poll.
    _POST_TIMEOUT_S: float = 8.0
    _POLL_TIMEOUT_S: float = 35.0

    # First-discovery subdevice enumeration is part of config-entry setup, so
    # it must have a finite wall-clock cost. A UUID-prefixed AC whose
    # /<uuid>/device/0 Collection is absent falls back to individual property
    # probes; some firmware silently drops unknown prefixed paths instead of
    # returning 4.04, making the old 10s-per-href scan take several minutes.
    # Keep enough time for a real blockwise Collection response, then use
    # short timeouts for the small Property resources, all under one budget.
    _SUBDEVICE_ENUMERATION_BUDGET_S: float = 15.0
    _SUBDEVICE_COLLECTION_TIMEOUT_S: float = 10.0
    _SUBDEVICE_PROPERTY_TIMEOUT_S: float = 1.0

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Per-device logger so every log line (including the base
        # coordinator's and ObserveManager's) identifies which device it's
        # about, instead of a shared module-level logger.
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
        self._snapshot_store = snapshot_store(hass, entry)
        # Both set only when this entry loaded from a snapshot instead of a
        # live poll -- see async_rehydrate.
        self._rehydrate_resources: dict[str, dict] | None = None
        self._rehydrated_keys: frozenset[tuple[str, str]] | None = None
        # Sibling indoor subdevices on this connection (issue #177); set
        # once at first discovery, narrowed to the ones with live state (see
        # subdevices.discover_partitioned). Never includes MAIN itself.
        self.subdevices: list[Subdevice] = []
        # Candidates the liveness gate rejected (e.g. an unused SmartThings
        # slot that still answers its seed) -- surfaced in diagnostics.
        self._skipped_subdevices: list = []
        # Rejected candidates' raw reps, kept for diagnostics only (see
        # _live_subdevice_resources) -- never applied to the state cache, or
        # they'd sit frozen at first-discovery value looking live.
        self._skipped_subdevice_resources: dict[str, dict] = {}
        # /multidevice/vs/0's rep if this board answers it -- corroborates
        # the liveness gate without deciding it; kept outside `resources`.
        self._multidevice: dict = {}
        # What each subdevice probe found, keyed by seed href -- lets
        # diagnostics distinguish "checked, nothing there" from "never
        # checked".
        self._subdevice_probes: dict[str, bool] = {}
        # canonical_resources() memo; invalidated in _on_cache_changed so
        # climate.py's frequent per-property reads don't rebuild it from
        # scratch each time.
        self._canonical_cache: dict[tuple[str, str], dict] = {}
        self._cache = StateCache(_NoOpDescriptor())
        self._cache.set_on_change(self._on_cache_changed)
        self._observe = ObserveManager(self._cache, logger=self._log)
        # Modes this device reported itself in but never advertised as
        # supported (issue #327), restored from the entry so one learned
        # last week is still offered today. See learned.py.
        self._learned = LearnedModes(entry.data.get(CONF_LEARNED_MODES))
        # Cloud "Download" programs discovered on this device (issue #342).
        # Same restore-from-entry shape as _learned above; see cloudcourse.py.
        self._cloud = CloudCourses(entry.data.get(CONF_CLOUD_COURSES))
        # Narrowed to this device's own climate hrefs once discovery has
        # run -- see _refresh_learnable_hrefs.
        self._learnable_hrefs: set[str] = set()
        self._observe.set_on_applied(self._on_rep_applied)
        self._push_pending = False
        self._push_pending_lock = threading.Lock()
        # Identity is resolved once by the config flow's probe (issue #236).
        # device_key mints permanent registry keys, so it must be correct
        # before the first entity registers -- a placeholder corrected once
        # the first poll lands orphans the first device/entity pair instead.
        # Ordered so an entry that has not polled since upgrading still
        # loads under the key its registry rows already carry: the v4 UUID
        # (issue #381), else the pre-v4 serial, else the host (#83/#189).
        self.device_key = (
            entry.data.get(CONF_DEVICE_KEY) or entry.data.get(CONF_SERIAL) or entry.data[CONF_HOST]
        )
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_key)},
            name=device_display_name(
                entry.data.get(CONF_DEVICE_TYPE), entry.data.get(CONF_MODEL) or ""
            ),
            manufacturer=entry.data.get(CONF_MANUFACTURER) or "Samsung",
            model=entry.data.get(CONF_MODEL) or None,
        )
        self._session_lock = asyncio.Lock()
        self._subpoll_task: asyncio.Task | None = None
        self._hot_hrefs: list[str] = []
        self._warm_hrefs: list[str] = []
        self.device_type_name: str | None = None
        self.one_ui_version: str = ""
        self._consecutive_poll_timeouts = 0
        # Set by _poll_once when the failure was the DTLS handshake itself.
        # A switched-off appliance fails there every cycle, and there is no
        # session to tear down and re-establish -- see _async_update_data.
        self._handshake_failed = False
        # Consecutive cycles that ended with no data from the device, so an
        # outage is reported once rather than once per poll (issue #269).
        self._failed_cycles = 0
        self._unbound_hrefs: list[str] = []
        self._reconnect_times: list[float] = []
        # See _maybe_retry_observe_mode: last_mode_change_ts alone doesn't
        # move on a failed attempt, so this tracks attempts too.
        self._last_observe_attempt_ts = 0.0
        # Set by both reconnect paths (poll and command) that hand back a
        # session with zero OBSERVE registrations while mode was still
        # observe; consumed once to trigger an immediate resubscribe
        # instead of waiting out _RECOVERY_RETRY_S.
        self._resubscribe_due = False

    # ------------------------------------------------------------------
    # Session management (all blocking — must run in executor)
    # ------------------------------------------------------------------

    @property
    def last_resources(self) -> dict:
        return self._cache.snapshot()

    def resource(self, href: str) -> dict:
        """A single href's rep. Cheaper than `last_resources.get(href)`,
        which copies every tracked href to build the snapshot dict."""
        return self._cache.get(href) or {}

    def entity_resources(self) -> dict[str, dict]:
        """The live snapshot as entity descriptors should see it: the device's
        own reps, plus this integration's discovered cloud programs merged
        onto /course/vs/0 under cloudcourse.FIELD (issue #342).

        Merged at read time rather than applied to the state cache, so the
        synthetic field can never be polled over or written to the device --
        `last_resources` stays exactly what the appliance reported. It rides
        on the rep instead of a resource of its own because rep_fn receives
        only its own href's rep: a sibling href would be invisible to it, and
        /course/vs/0 is the one resource every consumer of this data is
        already bound to.

        MAIN only, by construction: cloudcourse.COURSE_HREF is a canonical
        href and this snapshot is keyed by actual ones, so a composite
        appliance's second course resource (/<uuid>/course/vs/0 on the
        one-body washer-dryer) is not merged and not learned from. No device
        seen so far advertises cloud programs on anything but MAIN; making
        this per-subdevice means keying the store by actual href the way
        LearnedModes does, and migrating the persisted shape.
        """
        snapshot = self.last_resources
        rep = snapshot.get(cloudcourse.COURSE_HREF)
        if rep is None:
            return snapshot
        view = self._cloud_view()
        if not view:
            return snapshot
        snapshot[cloudcourse.COURSE_HREF] = {**rep, cloudcourse.FIELD: view}
        return snapshot

    def _cloud_view(self) -> dict:
        """`self._cloud.view()`, or nothing while cloud_courses_enabled is
        off (issue #364) -- entity_resources/entity_rep's one gate so a
        previously-named program stops being offered the moment the option
        is turned off, symmetric with how it starts being offered again the
        moment it's turned back on. The store itself is untouched either
        way; only what these two hand to the registry changes."""
        return self._cloud.view() if self.cloud_courses_enabled else {}

    def entity_rep(self, href: str) -> dict:
        """One href's rep as descriptors see it -- `resource()` plus the
        merge `entity_resources` would have applied. Exists so the write path
        doesn't copy every tracked href to read one rep, which is the very
        thing `resource()` was added to avoid."""
        rep = self.resource(href)
        if href != cloudcourse.COURSE_HREF or not rep:
            return rep
        view = self._cloud_view()
        return {**rep, cloudcourse.FIELD: view} if view else rep

    def device_resources(self, subdevice: Subdevice) -> dict[str, dict]:
        """`subdevice`'s canonical view of exactly what the appliance
        reported -- no integration state merged in.

        The counterpart to canonical_resources for everything that *exports*
        resources rather than rendering entities from them: diagnostics and
        the debug read service. Keeping this a separate call rather than
        filtering the merged view downstream is what makes "a dump is what the
        device said" a property of which method you call, instead of a
        convention every future exporter has to remember.
        """
        return canonical_view(subdevice, self.last_resources, self.subdevices)

    def canonical_resources(self, subdevice: Subdevice) -> dict[str, dict]:
        """`subdevice`'s view of the live snapshot, rewritten to canonical
        hrefs (issue #177, see subdevices.canonical_view). Any platform
        property that scans the whole resources dict (exists_fn,
        is_legacy_board, ...) must use this instead of `last_resources`, or a
        sibling subdevice's own `/mode/vs/1` could leak into MAIN's canonical
        `/mode/vs/0` view. Memoized per cache generation -- see
        _canonical_cache.
        """
        view_key = (subdevice.kind, subdevice.key)
        cached = self._canonical_cache.get(view_key)
        if cached is not None:
            return cached
        view = canonical_view(subdevice, self.entity_resources(), self.subdevices)
        self._canonical_cache[view_key] = view
        return view

    @property
    def rehydrated(self) -> bool:
        """True while this entry's entities came from a snapshot rather than
        a live poll (issue #295)."""
        return self._rehydrate_resources is not None

    @property
    def discovery_resources(self) -> dict[str, dict]:
        """What entity._is_included should judge an entity's existence
        against: the rehydration snapshot on an offline load, the live cache
        otherwise.

        Deliberately separate from `last_resources`, which stays empty until
        the device answers -- that emptiness is what keeps a rehydrated
        entity `unavailable` instead of rendering a snapshot's stale value.
        Only read while platforms are being forwarded; nothing consults it
        once the entities exist.
        """
        if self._rehydrate_resources is None:
            return self.last_resources
        return self._rehydrate_resources

    def discovery_canonical(self, subdevice: Subdevice) -> dict[str, dict]:
        """`discovery_resources` in `subdevice`'s canonical view -- the
        exists_fn counterpart to canonical_resources."""
        if self._rehydrate_resources is None:
            return self.canonical_resources(subdevice)
        return canonical_view(subdevice, self._rehydrate_resources, self.subdevices)

    # ------------------------------------------------------------------
    # Learned modes (issue #327)
    # ------------------------------------------------------------------

    @property
    def learning_enabled(self) -> bool:
        return bool(self._entry.options.get(CONF_LEARN_MODES, DEFAULT_LEARN_MODES))

    def learned_modes(self, actual_href: str) -> list[str]:
        """Codes learned for `actual_href`, or [] while the option is off.

        Gating the read here rather than only the write is what makes the
        option a single switch: turning it off restores stock behavior
        immediately, without also throwing away what was already learned
        (the options flow's reset step is for that)."""
        if not self.learning_enabled:
            return []
        return self._learned.codes(actual_href)

    def learned_snapshot(self) -> dict[str, list[str]]:
        """Everything learned, option state ignored -- for diagnostics and
        the options flow, both of which need to show what is remembered
        even when it isn't currently being offered."""
        return self._learned.snapshot()

    def forget_learned_modes(self) -> None:
        """Drop every learned mode, here and on the entry.

        Persists even when the in-memory store was already empty: a record
        _coerce rejected at startup exists only on the entry, and this is
        the one control that can clear it."""
        self._learned.clear()
        if self._entry.data.get(CONF_LEARNED_MODES):
            self._persist_learned()

    def _refresh_learnable_hrefs(self) -> None:
        """The actual hrefs learning applies to on this device: LEARNABLE's
        canonical set, narrowed to the ones a climate entity is bound to
        read back (climate._supported is the only consumer) and translated
        through that entity's own subdevice (issue #177).

        The href alone isn't a sufficient key here. `/mode/convenient/vs/0`
        is also declared by the dehumidifier registry (explicitly
        unmodeled) and the air purifier's (empty), so a global match would
        persist a code for a resource that family will never offer.
        """
        self._learnable_hrefs = {
            bound.subdevice.to_actual(href)
            for bound in self.bound
            if isinstance(bound.desc, ClimateDesc)
            for href in LEARNABLE
        }

    def _on_rep_applied(self, href: str, rep: dict, source: str) -> None:
        """ObserveManager.set_on_applied hook. Runs on whichever thread
        applied the update, so the persist goes through hass.add_job the
        same way _on_cache_changed marshals its state push.

        An 'optimistic' rep is the value this integration just wrote, not
        one the device reported, so there is nothing to learn from it."""
        if source == "optimistic":
            return
        if href == cloudcourse.COURSE_HREF:
            self._observe_cloud_courses(rep)
        if href not in self._learnable_hrefs:
            return
        if not self.learning_enabled:
            return
        if new := self._learned.observe(href, rep):
            self._log.info(
                "%s reported mode(s) it does not advertise as supported; "
                "remembering %s so they stay selectable (issue #327)",
                href,
                new,
            )
            self.hass.add_job(self._persist_learned)

    @callback
    def _persist_learned(self) -> None:
        persist(self.hass, self._entry, self._learned.snapshot())

    # ------------------------------------------------------------------
    # Cloud "Download" programs (issue #342) -- see cloudcourse.py
    # ------------------------------------------------------------------

    @property
    def cloud_courses_enabled(self) -> bool:
        """Whether downloaded programs are offered as cycles and nagged
        about via the "not set up yet" Repair (issue #364).

        Deliberately does NOT gate `_observe_cloud_courses` below -- see
        CONF_CLOUD_COURSES_ENABLED's own comment for why passive recording
        keeps running (cheap, and what guided/manual setup depend on)
        while only the Repair and the entity offering stop.
        """
        return bool(
            self._entry.options.get(CONF_CLOUD_COURSES_ENABLED, DEFAULT_CLOUD_COURSES_ENABLED)
        )

    def _observe_cloud_courses(self, rep: dict) -> None:
        """Learn a downloaded program's replay payload from one applied
        /course/vs/0 rep. Runs on whichever thread applied the update, so
        both the persist and the Repairs refresh go through hass.add_job.

        Always records, regardless of cloud_courses_enabled: it records only
        what the appliance itself reports about programs it itself
        advertises, and nothing is offered in the UI until both the option
        is on and the user has named it."""
        if not self._cloud.observe(rep):
            return
        self.hass.add_job(self._persist_cloud_courses)

    @callback
    def _persist_cloud_courses(self) -> None:
        cloud_persist(self.hass, self._entry, self._cloud.snapshot())
        self._on_cloud_courses_changed()

    @callback
    def _on_cloud_courses_changed(self) -> None:
        """Refresh everything that depends on the cloud-course store or the
        cloud_courses_enabled option: the canonical-view cache (a newly
        learned/named program, or the option flipping, changes what
        entity_resources() hands out), the live entities, and the Repair.

        Also the __init__.py options-update listener's one hook (issue
        #364): toggling cloud_courses_enabled changes _cloud_view()'s
        answer without touching last_resources, so a canonical view cached
        from before the toggle would otherwise keep answering with it.

        _push_cache_snapshot is what actually gets a change here in front
        of a user, not just correct on the next read: select.py's
        current_option comes from coordinator.data, which only moves on
        async_set_updated_data, and nothing prompts Home Assistant to
        re-read a live `options` property (cycle's callable options included)
        without the state-changed signal that call sends. Without it, both
        this and a name applied through apply_cloud_courses -- which has
        called this same method since before this option existed -- would
        sit stale until whatever poll or observe happened to run next.
        """
        self._canonical_cache.clear()
        self._refresh_cloud_course_issue()
        self._push_cache_snapshot()

    @property
    def cloud_courses(self) -> CloudCourses:
        """The discovered-program store, for reads (snapshot/view/candidates).

        Mutations go through apply_cloud_courses below, not through this --
        the store itself doesn't persist or invalidate, and `_canonical_cache`
        now depends on its contents, so a caller that mutates it directly
        leaves entity options stale with no error to say so.
        """
        return self._cloud

    def cloud_course_rep(self) -> dict:
        """/course/vs/0's live rep -- what advertises the slot list."""
        return self.resource(cloudcourse.COURSE_HREF)

    async def async_probe_cloud_courses(self) -> str | None:
        """Live-read /course/vs/0, apply it, and report which slot is loaded.

        /course/vs/0 is cold-tier, so passively a program selection can take
        a whole poll interval to show up -- far too slow for the guided setup
        flow, which is a person standing at the appliance waiting for Home
        Assistant to notice. The read goes through the normal apply path, so
        learning and persistence still happen in exactly one place.

        Returns None when the read fails; the caller is a retry loop and a
        single missed read is not worth surfacing.
        """
        try:
            code, rep, _body = await self.async_raw_read(cloudcourse.COURSE_HREF)
        except Exception:
            # One missed probe; the caller is a retry loop.
            self._log.debug("cloud-course probe failed", exc_info=True)
            return None
        if not _coap_accepted(code) or not rep:
            return None
        self._observe.apply(cloudcourse.COURSE_HREF, rep, source="poll")
        return cloudcourse.loaded_slot(rep)

    def apply_cloud_courses(self, names: dict[str, str], download_course: object = _KEEP) -> None:
        """The one mutation path for the cloud-program store (issue #342).

        Takes the whole submission at once so a nine-program naming pass is
        one config-entry write rather than nine, and so persistence, the
        canonical-view invalidation and the Repairs refresh can't be done for
        one half of a change and skipped for the other.

        `download_course` defaults to "leave it alone" rather than None.
        Guided setup submits one name at a time and says nothing about the
        course; with None as the default that silently cleared a course the
        user had already confirmed, and since a program is only offerable
        once both are set, naming things made them disappear.
        """
        for slot, name in names.items():
            self._cloud.set_name(slot, name)
        if download_course is not _KEEP:
            self._cloud.set_download_course(cast("str | None", download_course))
        self._persist_cloud_courses()

    @callback
    def _refresh_cloud_course_issue(self) -> None:
        """Raise or clear the "you have downloaded programs Home Assistant
        can't offer yet" Repairs issue (issue #342).

        A downloaded program is only usable once the appliance has been seen
        sitting on it (that is the only time its replay payload is visible)
        and the user has given it a name. The device advertises how many it
        has, so the gap between that and what's usable is knowable -- and
        it can only be closed by the user walking the appliance through its
        own Download list, which is exactly what a Repair is for.

        Gated on having seen at least one payload, which is the only
        available evidence that this household uses downloaded programs at
        all. Without it, an appliance that merely advertises slots -- the
        DW5000C fixture reports four and has never loaded any -- would carry
        a permanent warning about a feature its owner may never touch, and
        nothing they do in Home Assistant could clear it. Running one
        downloaded program is what turns the nudge on, and naming them is
        what turns it off.

        Also gated on cloud_courses_enabled (issue #364): a device can seed a
        slot's payload before the owner has ever meant to use the feature --
        reporters observed one auto-populate from a SmartThings-provided
        example -- so "at least one payload seen" alone isn't proof of
        intent to use it. Checked here rather than skipping
        _observe_cloud_courses' recording, so turning the option back on
        re-evaluates against everything already learned instead of only
        what arrives afterward.

        Also a no-op with an empty cloud_course_rep (issue #364): this now
        runs from __init__.py's options-update listener on every entry
        save, including an unrelated one (CONF_BYPASS_REMOTE_CONTROL, say)
        made before this device's first poll or while it's rehydrated
        offline. /course/vs/0 unpolled reads as no advertised slots, which
        would otherwise delete a Repair a real poll had every reason to
        raise, on evidence that only means "haven't asked the device yet."
        Leaves whatever issue state already exists untouched rather than
        guess either way; the next real poll re-evaluates for real.
        """
        issue_id = f"cloud_courses_{self._entry.entry_id}"
        if not self.cloud_courses_enabled:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        rep = self.cloud_course_rep()
        if not rep:
            return
        record = self._cloud.snapshot()
        courses = cycle_options(self.canonical_resources(MAIN))
        pending = cloudcourse.undiscovered(rep, record, courses)
        slots = cloudcourse.cloud_slots(rep, courses)
        needs_course = bool(slots) and not record["download_course"]
        if record["slots"] and (pending or needs_course):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="cloud_courses_undiscovered",
                translation_placeholders={
                    "device_name": self.device_info.get("name") or "This appliance",
                    "pending": str(len(pending)),
                    "total": str(len(slots)),
                },
                learn_more_url=DEVICE_SUPPORT_ISSUE_URL,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def device_info_for(self, subdevice: Subdevice) -> DeviceInfo:
        """DeviceInfo for one logical subdevice on this connection (issue
        #177): the master's own device_info for MAIN, or a linked child
        device otherwise.

        Identifiers derive from the master's serial plus this subdevice's
        stable key, never the subdevice's own reported serial -- deterministic
        across reconnects regardless of whether its identity resource
        answered yet. `serial_number` is set from it when present anyway,
        but is informational only, not an identifier.
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
            # No identity resource yet (or ever) for this subdevice -- fall
            # back to a generic label. 'Subdevice <n>' only applies to an
            # indexed subdevice; UUID-prefixed ones are never more than one
            # per connection today.
            label = (
                f"Subdevice {subdevice.key}"
                if subdevice.kind == "indexed"
                else "Secondary Subdevice"
            )
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self.device_key}_{subdevice.key}")},
            name=f"{base_name} {label}",
            manufacturer=self.device_info.get("manufacturer") or "Samsung",
            model=model or None,
            serial_number=serial,
        )
        set_via_device(self.hass, self._entry.entry_id, info, (DOMAIN, self.device_key))
        return info

    @property
    def observe_mode(self) -> str:
        return self._observe.mode

    def _connect_session(self) -> None:
        host = self._entry.data[CONF_HOST]
        port = self._entry.data[CONF_PORT]
        auth_type = self._entry.data.get(CONF_AUTH_TYPE)

        sess = session_factory.create_entry_session(
            self._entry.data,
            on_notification=self._observe.on_notification,
            local_port=_local_source_port(host),
        )
        try:
            sess.connect()
            sess.start_reader()
            if auth_type == AUTH_OWNER_PSK:
                reported_device_id = read_ocf_device_id(sess)
                require_matching_ocf_uuid(
                    self._entry.data[CONF_DEVICE_KEY],
                    reported_device_id,
                )
        except Exception:
            with contextlib.suppress(Exception):
                sess.close()
            raise
        self._session = sess
        self._log.debug("DTLS connected to %s:%d", host, port)
        try:
            self._identity = read_identity(sess, None)
        except Exception as e:
            self._log.debug("read_identity failed: %s", e)
            self._identity = None

    def _close_session(self) -> None:
        # Blocking, run in an executor. As of smartthings-local 0.1.12,
        # close() actually puts the DTLS close_notify on the wire instead of
        # just building it (issue #417), and paces the OBSERVE deregisters
        # it now sends through the session rate limiter -- a few seconds for
        # an appliance with a dozen relations at the 5 req/s default.
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
            self.async_set_updated_data(flatten(self.bound, self.entity_resources()))

    def _poll_once(self) -> dict[str, dict]:
        """GET /device/0, return parsed resources. Blocking.

        A `TimeoutError` here means one block's ACK didn't arrive in time --
        not that the session is dead (earlier blocks succeeded). Left open;
        `_async_update_data` decides whether repeated timeouts warrant a
        reconnect. Any other exception is unambiguous -- close immediately.

        smartthings-local >= 0.1.6 tells those two cases apart itself now:
        a reader thread that has actually died raises `SessionClosedError`
        (a ConnectionError, not a TimeoutError) the moment the next request
        notices, instead of the old behavior of quietly hanging out to this
        call's own timeout and surfacing as an ambiguous `TimeoutError`.
        See `_defer_reconnect_for` for what that changes about how soon a
        confirmed-dead session gets reconnected.

        Sets `_handshake_failed` so `_async_update_data` can tell a broken
        session from one that never opened -- a switched-off appliance fails
        in `_connect_session` every cycle, with nothing to reconnect.
        """
        if self._session is None:
            try:
                self._connect_session()
            except Exception:
                self._handshake_failed = True
                raise
        self._handshake_failed = False
        sess = self._session
        assert sess is not None
        try:
            # 35s gives a slow blockwise transfer room to finish instead of
            # raising TimeoutError every cycle on an otherwise-fine device.
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
        # Refresh every enumerated sibling's seed on this same poll (issue
        # #177) so its state doesn't freeze at enumeration time.
        for subdevice in self.subdevices:
            result.update(self._poll_subdevice_seed(subdevice))
        return result

    def _poll_subdevice_seed(self, subdevice: Subdevice) -> dict[str, dict]:
        """GET one subdevice's seed Collection, normalized to real hrefs. A
        sibling failing to answer is a debug log, never a failed poll -- the
        issue #177 reporter's /device/2 (an unused SmartThings slot) may not
        always respond, and the master must not go unavailable for that.
        Blocking -- called from _poll_once, already in executor."""
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
        """Re-poll a flat-mode subdevice's hrefs individually (issue #205) --
        it has no Collection endpoint to batch-refresh through (see
        enumerate_subdevices' fallback), so each confirmed href gets its own
        GET under the subdevice's prefix. A failing href just drops out of
        the result, same posture as the Collection path above.

        Takes `sess` from the caller rather than re-reading self._session --
        async_close() can null it without holding _session_lock. Skips hrefs
        already covered by the hot/warm sub-poll tiers, which
        _run_subpolls refreshes every 3s/6s, strictly more current than
        this once-per-summary-poll pass could offer."""
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
        """Poll hot/warm hrefs in the gaps between summary polls.

        In observe-primary mode this is a no-op for hrefs the device is
        actually pushing, unless `force` is set (sweep disagreed with the
        cache -- see log_sweep_discrepancies). Hrefs that were subscribed
        but stayed silent through the grace period (issue #92) stay on
        the hot/warm cadence via `fallback_hrefs`.
        """
        if self._observe.mode == MODE_OBSERVE and not force:
            silent = self._observe.fallback_hrefs
            if not silent:
                return
            hot = [h for h in self._hot_hrefs if h in silent]
            warm = [h for h in self._warm_hrefs if h in silent]
        else:
            hot = self._hot_hrefs
            warm = self._warm_hrefs
        if not hot and not warm:
            return
        step = self._SUBPOLL_STEP_S
        for i in range(1, 10):  # slots 1..9  (T+3 s … T+27 s)
            await asyncio.sleep(step)
            hrefs = list(hot) + (list(warm) if i % 2 == 0 else [])
            if not hrefs:
                continue
            async with self._session_lock:
                try:
                    await self.hass.async_add_executor_job(self._poll_hrefs_blocking, hrefs)
                except Exception as e:
                    self._log.debug("sub-poll batch failed: %s", e)

    # ------------------------------------------------------------------
    # Discovery (runs once on first successful poll)
    # ------------------------------------------------------------------

    def _subdevice_probe_priority(
        self,
        resources: dict[str, dict],
    ) -> tuple[str, ...]:
        """Return live primary-entity hrefs in hot/warm-first order.

        A prefixed subdevice without a Collection endpoint has to be probed
        one Property href at a time. Resolve the master through the same
        registry used by discovery and put resources that produce primary
        entities first. This is metadata-driven rather than an AC-specific
        list: a future composite appliance gets the priority its own registry
        declares, while unknown devices simply retain the normal href order.
        """
        registry = resolve_registry(
            resources,
            device_types=self._identity.device_types if self._identity else (),
        )
        if registry is None:
            return ()

        tier_rank = {"hot": 0, "warm": 1, "cold": 2}
        ranked = []
        for order, href in enumerate(resources):
            primary_caps = [
                capability
                for capability in registry.capabilities.get(href, ())
                if any(desc.entity_category is None for desc in capability.entities)
            ]
            if not primary_caps:
                continue
            rank = min(tier_rank.get(capability.poll_tier, 2) for capability in primary_caps)
            ranked.append((rank, order, href))
        return tuple(href for _, _, href in sorted(ranked))

    def _enumerate_subdevices_blocking(self, resources: dict[str, dict]) -> dict[str, dict]:
        """One-time (first discovery only) probe for sibling indoor
        subdevices on this connection (issue #177) -- see
        registry.subdevices.enumerate_subdevices for the two detection
        patterns. Runs in executor, under the session lock.

        Sets self.subdevices to every candidate found and returns
        `resources` merged with each candidate's seed, so this cycle's
        _run_discovery sees every candidate without a second round trip.
        _run_discovery is what narrows this down to the ones actually live
        (see discover_partitioned) -- this method can't tell an unused
        SmartThings slot from a real sibling, only that something answered.
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
            preferred_hrefs=self._subdevice_probe_priority(resources),
            time_budget=self._SUBDEVICE_ENUMERATION_BUDGET_S,
            collection_timeout=self._SUBDEVICE_COLLECTION_TIMEOUT_S,
            property_timeout=self._SUBDEVICE_PROPERTY_TIMEOUT_S,
        )
        self.subdevices = subdevices
        self._subdevice_probes = probes
        # /multidevice/vs/0 is corroborating metadata, not appliance state,
        # and is probed on every device -- it must not join `resources`, or
        # it would bind to nothing on families that don't ignore the href
        # (raising a spurious coverage-gap repair) and freeze in the cache
        # since it's never polled again (see _live_subdevice_resources).
        # Kept aside for diagnostics and the numofsubdevice cross-check in
        # _run_discovery instead.
        self._multidevice = extra.pop("/multidevice/vs/0", {})
        return {**resources, **extra}

    def _live_subdevice_resources(self, resources: dict[str, dict]) -> dict[str, dict]:
        """`resources` minus every href belonging to a rejected subdevice
        candidate (issue #177). Called once, between _run_discovery and the
        first cache apply, so a rejected slot's reps are seen by the gate
        and then dropped rather than frozen into the cache forever. Kept in
        _skipped_subdevice_resources for diagnostics.
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

    def _resolve_identity(self, polled_serial: str, *, from_snapshot: bool) -> tuple[str, str]:
        """The (key, serial) this entry should be registered under, re-keying
        the registries if the key has moved.

        Key and serial are returned together because they have to agree: the
        serial corroborates a later change of key, so adopting it while
        *defending* the stored key would hand a different appliance the
        corroboration it needs to win the next poll.

        Nothing changes a key without calling `rekey_entry` in the same
        breath, or the existing registry rows are orphaned (issue #236).
        Two things are therefore never treated as a change of identity: a
        snapshot replay (issue #295), which never reached the device, and a
        poll that read no UUID, since the device saying nothing is not the
        device saying something different.

        A genuine difference is either the registered appliance under a new
        identity -- the move onto the OCF device UUID (issue #381), or a
        `di` regenerated by a hard reset -- or a different appliance on this
        address, and the serialNum is what tells them apart. That test is
        deliberately the same before and after an entry has adopted a UUID:
        a pre-v4 entry has been running longest, so it is the last one that
        should lose its rows to whatever now answers at its address.
        """
        host = self._entry.data[CONF_HOST]
        stored_serial = self._entry.data.get(CONF_SERIAL)
        if from_snapshot:
            return self.device_key, stored_serial or polled_serial

        polled_ocf = ocf_device_key(self._identity)
        stored_key = self._entry.data.get(CONF_DEVICE_KEY)
        # What this entry's rows carry today: a pre-v4 entry has no
        # CONF_DEVICE_KEY, so it is whatever __init__ fell back to.
        current_key = stored_key if stored_key is not None else (stored_serial or host)
        polled_key = polled_ocf or polled_serial

        if polled_key == current_key:
            # Returned rather than short-circuited so a pre-v4 entry records
            # the key it has always had, and the next poll sees no change.
            return current_key, polled_serial

        if stored_key is not None and polled_ocf is None:
            return stored_key, stored_serial or polled_serial

        # Excluding the host answer keeps this from firing on two *different*
        # placeholder-serial units: an address is not an identity and can't
        # corroborate anything.
        same_unit = polled_serial == stored_serial and polled_serial != host
        # A host-keyed entry (issues #83/#189) is registered against whatever
        # answers at this address, so it has no claim to defend and needs no
        # corroboration -- demanding it would strand exactly the
        # placeholder-serial boards this exists to rescue. Same for an entry
        # with no stored serial to compare against.
        if not (current_key == host or same_unit or stored_serial is None):
            self._log.warning(
                "device at %s identifies as %r but this entry is registered as %r "
                "(serial %r, registered %r); keeping the registered identity",
                host,
                polled_key,
                current_key,
                polled_serial,
                stored_serial,
            )
            return current_key, stored_serial or polled_serial

        # Corroborated: a pre-v4 entry moving onto its UUID, the same unit
        # with a regenerated `di`, or a host-keyed entry learning a real
        # identity. Following it keeps the user's entity_ids and history.
        self._log.info(
            "device %s (serial %r) changed key from %r to %r; following it",
            host,
            polled_serial,
            current_key,
            polled_key,
        )
        rekey_entry(self.hass, self._entry, current_key, polled_key)
        return polled_key, polled_serial

    def _persist_identity(
        self,
        device_key: str | None,
        serial: str,
        model: str,
        manufacturer: str,
        device_type_name: str | None,
    ) -> None:
        """Write this device's resolved identity back onto the config entry.

        A no-op for an entry the current config flow already fully stored.
        Matters for an entry migrated from before identity was stored: the
        first poll is where model/type become known, and persisting them
        means the next restart names the device fully instead of renaming it
        again once a poll lands.

        `device_key` is what the registries are keyed on; `serial` is stored
        alongside it rather than replaced by it, because it is what
        `_resolve_identity` corroborates a changed UUID against on a later
        poll.
        None leaves whatever key is already stored untouched -- see the
        caller for why a snapshot replay must not write one.

        Runs on the event loop, which async_update_entry requires.
        """
        identity = {
            **({CONF_DEVICE_KEY: device_key} if device_key is not None else {}),
            CONF_SERIAL: serial,
            CONF_MODEL: model,
            CONF_MANUFACTURER: manufacturer,
            CONF_DEVICE_TYPE: device_type_name,
        }
        if all(self._entry.data.get(k) == v for k, v in identity.items()):
            return
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, **identity}
        )

    # ------------------------------------------------------------------
    # Discovery snapshot (issue #295)
    # ------------------------------------------------------------------

    def _bound_keys(self) -> frozenset[tuple[str, str]]:
        """This entity set's identity: one (subdevice, state key) pair per
        bound entity. `_key` is the unique_id suffix, so two discoveries that
        agree here would register byte-identical entities."""
        return frozenset((b.subdevice.key, _key(b)) for b in self.bound)

    async def _async_save_snapshot(
        self, resources: dict[str, dict], candidates: list[Subdevice]
    ) -> None:
        """Record what this first cycle handed `_run_discovery`, so the next
        restart can replay it while the appliance is unreachable.

        `candidates` is the pre-narrowing subdevice list (issue #177):
        `discover_partitioned` takes candidates and returns the live ones, so
        replaying against the narrowed list would rediscover nothing for a
        composite appliance's siblings.

        Written now rather than through `async_delay_save`, because a pending
        delayed write outlives whatever queued it: it lands after
        `async_remove_entry` has deleted the file and recreates it orphaned,
        and a reload scheduled by `_reconcile_rehydrated` would read the
        pre-reload snapshot back off disk. This runs once per entry load, so
        the immediate write costs nothing worth deferring.
        """
        ident = self._identity
        try:
            await self._snapshot_store.async_save(
                {
                    # json_safe/from_json_safe on the way out and back in
                    # (registry/encode.py): a rep the JSON encoder rejects
                    # used to fail this write entirely, and the only
                    # symptom was this entry losing its offline load.
                    "resources": json_safe(dict(resources)),
                    "subdevice_candidates": [asdict(su) for su in candidates],
                    "identity": asdict(ident) if ident is not None else None,
                }
            )
        except Exception as e:
            # Never fail a poll over the snapshot -- a board reporting
            # something the JSON encoder rejects would otherwise break
            # polling outright. Worst case this entry can't load offline,
            # which is where it was before any of this existed.
            self._log.warning("could not write discovery snapshot: %s", e)

    async def async_rehydrate(self) -> bool:
        """Register the last known entity set without reaching the device.

        Replays the stored snapshot through `_run_discovery`, which is what
        makes this faithful: same code path, same registry resolution, so an
        offline load produces the entity set the device last actually
        reported rather than a guess reconstructed from a parallel format.

        Returns False when there's nothing stored (an entry that has never
        polled successfully) or the replay produced nothing usable -- the
        caller raises ConfigEntryNotReady in that case, exactly as before.
        """
        try:
            stored = await self._snapshot_store.async_load()
        except Exception as e:  # corrupt or unreadable store
            self._log.warning("could not read discovery snapshot: %s", e)
            return False
        if not stored or not stored.get("resources"):
            return False

        resources = from_json_safe(stored["resources"])
        try:
            ident = stored.get("identity")
            if ident is not None:
                self._identity = DeviceIdentity(
                    manufacturer=ident.get("manufacturer") or "",
                    model=ident.get("model") or "",
                    name=ident.get("name") or "",
                    serial=ident.get("serial"),
                    # Absent from a snapshot written before these fields
                    # existed; _resolve_identity never re-keys from a
                    # replay, so a missing UUID here costs nothing beyond
                    # diagnostics.
                    device_id=ident.get("device_id"),
                    platform_id=ident.get("platform_id"),
                    device_types=tuple(ident.get("device_types") or ()),
                    raw=ident.get("raw") or {},
                )
            # JSON gives lists back where Subdevice declares tuples, and it's
            # a frozen (hashable) dataclass used as a dict key in flatten().
            self.subdevices = [
                Subdevice(
                    kind=su["kind"],
                    key=su["key"],
                    seed_path=tuple(su.get("seed_path") or ()),
                    flat_hrefs=tuple(su.get("flat_hrefs") or ()),
                )
                for su in stored.get("subdevice_candidates") or ()
            ]
            self._run_discovery(resources, from_snapshot=True)
        except Exception as e:
            # A snapshot written by an older release can outlive both the
            # stored shape and the registry it was discovered against. This
            # has to catch the rebuild as well as the replay: an exception
            # escaping here reaches async_setup_entry, which only handles
            # ConfigEntryNotReady, so the entry would land in SETUP_ERROR --
            # never retried, and with its session left open.
            self._log.warning("discovery snapshot could not be replayed: %s", e, exc_info=True)
            self.bound = []
            self.subdevices = []
            return False

        # _run_discovery sets _discovered; put it back. The snapshot only
        # supplied an entity set to register -- the first live poll must
        # still enumerate subdevices and rediscover for real.
        self._discovered = False
        self._rehydrate_resources = resources
        self._rehydrated_keys = self._bound_keys()
        if not self.bound:
            return False
        self._log.info(
            "device unreachable; restored %d entities from the last discovery "
            "snapshot and will keep retrying every %ds",
            len(self.bound),
            SUMMARY_INTERVAL_S,
        )
        return True

    @callback
    def _reconcile_rehydrated(self) -> None:
        """Reload the entry when a live discovery disagrees with the snapshot
        this load registered from.

        Platforms enumerate `bound` exactly once, at forward time, so a
        firmware update, a newly-answering sibling subdevice or a different
        appliance at the same IP can't be picked up in place -- the entry has
        to come back up against the live set.
        """
        if self._rehydrated_keys is None:
            return
        stale = self._rehydrated_keys
        self._rehydrated_keys = None
        live = self._bound_keys()
        if live == stale:
            return
        self._log.info(
            "live discovery differs from the snapshot this entry loaded from "
            "(%d entities gone, %d new); reloading",
            len(stale - live),
            len(live - stale),
        )
        self.hass.config_entries.async_schedule_reload(self._entry.entry_id)

    def _run_discovery(self, resources: dict[str, dict], from_snapshot: bool = False) -> None:
        # Diagnostics only -- names the firmware generation (e.g. '7.0 Air
        # conditioner' is Tizen Lite); doesn't route, since every device
        # that reports it is already typed by modelNum.
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
        # owned by no subdevice; one further pass per candidate subdevice
        # binds its own canonical view (see subdevices.discover_partitioned),
        # gated on whether it actually produced live primary state (an
        # unused SmartThings slot answers its seed but never does). A device
        # with no candidates behaves exactly like the old single discover()
        # call.
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
        # Corroborating signal, not a gate: log, don't raise, on a
        # disagreement -- only one known board family exposes
        # numofsubdevice at all, so a mismatch is a triage signal, not proof
        # either side is wrong.
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
            # modelNum alone doesn't identify every type, and device_types
            # is often empty even on hardware we don't map yet -- log all
            # three so a user can paste this into an issue.
            self._log.warning(
                "unknown device type modelNum=%r description=%r device_types=%r; using common caps",
                model_num,
                description,
                self._identity.device_types if self._identity else (),
            )
        self.device_type_name = device_type_name
        self.bound = bound
        self._unbound_hrefs = unbound
        self._refresh_learnable_hrefs()

        polled_serial = resolve_serial(
            info.get("x.com.samsung.da.serialNum"), self._entry.data[CONF_HOST]
        )
        key, serial = self._resolve_identity(polled_serial, from_snapshot=from_snapshot)
        self.device_key = key

        ident = self._identity
        model = resolve_model(model_num, ident)
        name = device_display_name(device_type_name, model)
        mfr = (ident.manufacturer if ident else "") or "Samsung"

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, key)},
            name=name,
            manufacturer=mfr,
            model=model,
        )
        # A snapshot replay passes None: it never reached the device, so
        # writing a key here would freeze a pre-v4 entry's legacy key in as
        # if a poll had confirmed it, and the real UUID would later look
        # like an identity to defend against rather than one to adopt.
        self._persist_identity(None if from_snapshot else key, serial, model, mfr, device_type_name)
        if not from_snapshot:
            # A coverage gap is a claim about what the device reports, so only
            # a live poll gets to make it. Replaying a snapshot would restate
            # last run's conclusion while pointing the user at a diagnostics
            # download that is empty until the appliance answers, and any
            # drift in the device name between the two would churn the issue.
            self._update_coverage_gap_issue(device_type_name is None, unbound, name)

        self._hot_hrefs = sorted(hot)
        self._warm_hrefs = sorted(warm)

        self._discovered = True
        self._log.info(
            "discovered %d entities (key=%s) hot=%s warm=%s subdevices=%s",
            len(bound),
            key,
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
        """Raise or clear a Repairs issue when capability coverage is
        incomplete -- unrecognized device type or unbound resources.
        Diagnostics (diagnostics.py) is what a user downloads to help; this
        just tells them there's something to send.
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
        """Called once, right after first discovery, after a reconnect
        downgrades from observe, and periodically while polling. Two
        phases: subscribing holds `_session_lock` (each send is fire-and-
        forget, not a network round trip); the grace wait that follows
        does not, so a concurrent command write isn't blocked for the
        whole ~15s wait (issue #294) -- only the brief subscribe burst.

        The wait can outlast a reconnect elsewhere (the poll path's own
        recovery, or a command retry), which would otherwise let a stale
        success commit observe mode against a session that's already been
        replaced -- claiming "Push" with nothing left to notice it's dead.
        `self._session is sess` re-checked under the lock right before
        committing closes that: `sess` keeps the old object alive, so
        identity can't be recycled onto a new one.
        """
        hrefs = self._hot_hrefs + self._warm_hrefs
        if not hrefs:
            return
        self._last_observe_attempt_ts = time.monotonic()
        async with self._session_lock:
            if self._session is None:
                # _poll_once already connects on a real poll; only fires if
                # the session was closed out from under us concurrently.
                try:
                    await self.hass.async_add_executor_job(self._connect_session)
                except Exception as e:
                    # Not a poll failure -- the poll that reached this line
                    # already succeeded, and observe mode is only an
                    # optimization on top of it. Give up on push this cycle
                    # the same way the two branches below do, rather than
                    # letting this escape _async_update_data uncaught: none
                    # of this integration's reconnect bookkeeping would run,
                    # and the base coordinator logs an ERROR traceback in
                    # place of the deliberately quiet "poll failed,
                    # reconnecting" voice used everywhere else in this file.
                    #
                    # Not counted by _reconnect_is_frequent() (that window
                    # records reconnects the poll path itself performed --
                    # feeding it a secondary path's failure would push the
                    # next routine poll reconnect over the warn threshold),
                    # and nothing to _close_session(): _connect_session only
                    # publishes self._session once connect() and
                    # start_reader() have both already succeeded, so it's
                    # still None here. No _resubscribe_due either -- that
                    # flag means "a live session nothing has tried yet",
                    # and setting it would re-enter this doomed handshake
                    # every cycle; _last_observe_attempt_ts (stamped above)
                    # already paces the retry to _RECOVERY_RETRY_S.
                    self._log.info(
                        "observe-mode reconnect failed (%s), staying on polling: %s",
                        type(e).__name__,
                        e,
                    )
                    self._observe.abandon_observe_attempt()
                    return
            sess = self._session
            if sess is None:
                return
            subscribed = await self.hass.async_add_executor_job(
                self._observe.subscribe_hrefs, sess, hrefs
            )
        if not subscribed:
            self._observe.abandon_observe_attempt()
            return
        reached = await self.hass.async_add_executor_job(
            self._observe.await_observe_notifies, subscribed, self._OBSERVE_GRACE_PERIOD_S
        )
        async with self._session_lock:
            stale_session = self._session is not sess
            if not reached or stale_session:
                self._observe.abandon_observe_attempt()
                if stale_session:
                    # A reconnect elsewhere replaced the session while this
                    # attempt waited -- that session has never been tried,
                    # so retry it next cycle instead of leaving it
                    # unsubscribed for up to _RECOVERY_RETRY_S, which
                    # _last_observe_attempt_ts (already stamped above, for
                    # the now-abandoned session) would otherwise throttle
                    # for (issue #294).
                    self._resubscribe_due = True
                return
            self._observe.enter_observe_mode(sess, subscribed)

    async def _maybe_retry_observe_mode(self) -> None:
        """While in poll-only mode, periodically re-attempt observe mode
        so a device that gains internet access recovers push automatically.

        Gated on the more recent of the two timestamps, not just
        `last_mode_change_ts`: `_set_mode` only stamps that on an actual
        transition, so a device that never successfully enters observe
        mode would otherwise leave this throttle open forever after the
        first `_RECOVERY_RETRY_S` window -- re-attempting (and paying the
        subscribe-burst lock) on every single poll cycle instead of every
        `_RECOVERY_RETRY_S`.
        """
        last_attempt = max(self._observe.last_mode_change_ts, self._last_observe_attempt_ts)
        if time.monotonic() - last_attempt < _RECOVERY_RETRY_S:
            return
        await self._attempt_observe_mode()

    def _defer_reconnect_for(self, e: Exception) -> bool:
        """True if this poll failure should NOT trigger a reconnect this
        cycle.

        A `TimeoutError` means one block's ACK was late, not that the
        session is dead (see `_poll_once`). A recent OBSERVE notify is proof
        the channel is live, so always defer then. Otherwise defer until
        `_POLL_TIMEOUT_LIMIT` consecutive timeouts pile up. Any other
        exception reconnects immediately.

        That includes `SessionClosedError`, which is the point: before
        smartthings-local 0.1.6, a reader thread that had actually died was
        indistinguishable from a slow transfer -- both surfaced here only as
        a `TimeoutError`, so this tolerance was the only thing standing
        between a truly dead session and a reconnect, worst case about
        `_POLL_TIMEOUT_LIMIT` poll cycles (~2 minutes at the default 30s
        interval). 0.1.6 confirms reader death directly and raises a
        ConnectionError subclass for it instead, which isn't a TimeoutError
        and so skips this tolerance entirely -- a genuinely dead session now
        reconnects on the very first occurrence. Intended (see this repo's
        README, "Known device behavior"), not a regression, but a real
        change in observed reconnect timing for that one failure mode.

        Never defers before first discovery (issue #254): deferring returns
        an empty dict, which the base coordinator treats as a successful
        first refresh -- and since platforms enumerate `bound` once, the
        entry would load with zero entities and stay that way.
        """
        if not self._discovered:
            return False
        if not isinstance(e, TimeoutError):
            return False
        if self._observe.mode == MODE_OBSERVE and self._observe.recently_notified():
            # Recent push is proof of life -- reset the counter too, so
            # timeouts from an earlier quiet stretch don't carry over and
            # trigger a false reconnect once the device goes quiet again.
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

    def _mark_device_answered(self) -> None:
        """Clear the bookkeeping a poll getting through invalidates."""
        self._consecutive_poll_timeouts = 0
        if self._failed_cycles:
            self._log.info("device answered again after %d failed cycles", self._failed_cycles)
            self._failed_cycles = 0

    def _device_unreachable(self, what: str, e: Exception) -> dict[str, Any]:
        """End a cycle that got no data, either degraded or as a failure.

        Reported once per outage rather than once per cycle: an appliance
        that is switched off fails identically every 30s for as long as it
        stays off (issue #269), and this integration is built to sit through
        exactly that (issue #295). Home Assistant logs the transition into
        and out of a failed update on its own.

        Raises `UpdateFailed` unless there are bound entities and cached
        state to carry the last-known values on -- same precondition as
        `_defer_reconnect_for` (issue #254).
        """
        self._failed_cycles += 1
        if self._failed_cycles == 1:
            self._log.error("%s: %s", what, e)
        else:
            self._log.debug("%s (%d cycles): %s", what, self._failed_cycles, e)
        # Without this, a fully unreachable device left the connection-mode
        # sensor stuck on "Push" forever -- only a successful poll ever
        # downgraded it (issue #287). No just_downgraded_from_observe here:
        # there's no live session this cycle to resubscribe on.
        if self._observe.mode == MODE_OBSERVE:
            self._observe.downgrade_to_poll()
        if self._discovered and self._cache.snapshot():
            self._log.debug("Full error:", exc_info=e)
            return flatten(self.bound, self.entity_resources())
        raise UpdateFailed(f"{what}: {e}") from e

    def _raise_if_owner_psk_auth_failed(self, error: Exception) -> None:
        """Translate only definitive OwnerPSK failures into HA reauthentication.

        A generic ``SessionError`` is deliberately not sufficient: current
        smartthings-local releases use it for both a rejected handshake and
        endpoint/backend failures. Treating every one as bad credentials would
        turn an ordinary network outage into a reauthentication prompt.
        """
        if self._entry.data.get(CONF_AUTH_TYPE) != AUTH_OWNER_PSK:
            return
        if isinstance(error, InvalidCredentialConfig):
            raise ConfigEntryAuthFailed("stored OwnerPSK credentials are invalid") from error
        if isinstance(error, OwnerPskDeviceMismatch):
            raise ConfigEntryAuthFailed("OwnerPSK device identity did not match") from error
        if isinstance(error, AuthenticationError):
            raise ConfigEntryAuthFailed("OwnerPSK authentication failed") from error

    # ------------------------------------------------------------------
    # DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        # Stop any in-flight sub-poll before taking the session lock.
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None

        async with self._session_lock:
            try:
                resources = await self.hass.async_add_executor_job(self._poll_once)
                self._mark_device_answered()
            except Exception as e:
                self._raise_if_owner_psk_auth_failed(e)
                if self._defer_reconnect_for(e):
                    self._log.debug(
                        "poll failed (%s), not yet treated as session "
                        "death; skipping this cycle: %s",
                        type(e).__name__,
                        e,
                    )
                    return flatten(self.bound, self.entity_resources())
                self._consecutive_poll_timeouts = 0
                if self._handshake_failed:
                    # The handshake never completed, so there is no session
                    # to close and no association for the device to clean
                    # up: reconnecting would just repeat the same doomed
                    # handshake five seconds later. That doubled what a
                    # switched-off appliance costs -- two handshake timeouts
                    # per cycle, and the same wait again on every setup
                    # attempt while it stays dark (issue #269).
                    return self._device_unreachable("device unreachable", e)
                # A lone reconnect is routine (README's "Known device
                # behavior"); only warn once they pile up. Pause first so
                # the device can clean up its DTLS state before we knock
                # again.
                if self._reconnect_is_frequent():
                    self._log.warning("poll failed, reconnecting: %s", e)
                else:
                    self._log.info("poll failed, reconnecting: %s", e)
                await self.hass.async_add_executor_job(self._close_session)
                await asyncio.sleep(self._RECONNECT_PAUSE_S)
                try:
                    resources = await self.hass.async_add_executor_job(self._poll_once)
                except Exception as e2:
                    self._raise_if_owner_psk_auth_failed(e2)
                    return self._device_unreachable("poll failed after reconnect", e2)
                else:
                    self._mark_device_answered()
                    # A fresh session has zero OBSERVE registrations; if we
                    # were in observe mode that state is now stale. Tear it
                    # down and resubscribe immediately below instead of
                    # waiting for the poll-mode retry timer, which exists to
                    # throttle devices that never had observe working at all
                    # -- a reconnect just proved this session is healthy.
                    if self._observe.mode == MODE_OBSERVE:
                        self._log.debug(
                            "reconnect while in observe mode; downgrading to "
                            "poll and resubscribing on the new session"
                        )
                        self._observe.downgrade_to_poll()
                        self._resubscribe_due = True

        if not self._discovered:
            # One-time (issue #177): find sibling subdevices before the
            # first discovery pass, folding their seed resources into this
            # cycle's snapshot so discovery sees every subdevice on the
            # first poll rather than waiting a cycle.
            async with self._session_lock:
                try:
                    resources = await self.hass.async_add_executor_job(
                        self._enumerate_subdevices_blocking, resources
                    )
                except Exception as e:
                    # _connect_session() inside here only runs at all if the
                    # session the poll above just used got closed out from
                    # under us within this same cycle -- rare, but not
                    # impossible, and unguarded before this. Losing the
                    # subdevice probe isn't losing first discovery: `resources`
                    # keeps the value _poll_once already returned, so
                    # discovery below still runs on the master's own data,
                    # same posture _poll_subdevice_seed takes for one sibling
                    # going quiet.
                    #
                    # Not a one-cycle blip, though: `_run_discovery` a few
                    # lines below sets `self._discovered = True`
                    # unconditionally this same cycle, which is what gates
                    # this whole block -- there is no next cycle where this
                    # is retried. A composite appliance whose enumeration
                    # fails here loses its sibling subdevices' entities for
                    # this config entry's lifetime (a reload probes again).
                    # warning, not debug, because of that: it's silent and
                    # permanent otherwise, with nothing in the log pointing
                    # at why a device is missing entities it should have.
                    self._log.warning(
                        "subdevice enumeration failed on first discovery; "
                        "any sibling subdevices will be missing until this "
                        "config entry is reloaded: %s",
                        e,
                    )

        source = "sweep" if self._discovered else "poll"
        first_cycle = not self._discovered
        if first_cycle:
            # Discovery runs before the apply loop so a rejected candidate's
            # resources never reach the state cache (issue #177) --
            # StateCache has no eviction, so the only way to keep them out
            # is to not put them in. Safe to reorder: _run_discovery reads
            # the passed dict, never the cache.
            candidates = list(self.subdevices)
            self._run_discovery(resources)
            # Banked before the reconcile below, so a reload it schedules
            # comes up against this discovery rather than the one that is
            # being replaced.
            await self._async_save_snapshot(resources, candidates)
            self._reconcile_rehydrated()
            resources = self._live_subdevice_resources(resources)
        sweep_mismatch = False
        if self._observe.mode == MODE_OBSERVE:
            # A mismatch never tears down a still-live OBSERVE session (see
            # log_sweep_discrepancies) -- the sweep below re-applies
            # authoritative state regardless. It only triggers extra
            # hot/warm subpolls this cycle so a channel gone silent without
            # a reconnect still gets fresher-than-30s data.
            sweep_mismatch = self._observe.log_sweep_discrepancies(resources)
        for href, rep in resources.items():
            self._observe.apply(href, rep, source=source)

        if first_cycle:
            # The apply loop above has just fed this poll's /course/vs/0 to
            # the cloud-program store, so the gap is knowable now. Explicit
            # rather than left to _persist_cloud_courses: a device whose
            # programs are all already named learns nothing on this cycle and
            # would otherwise never get its stale Repair cleared.
            self._refresh_cloud_course_issue()

        if first_cycle or self._resubscribe_due:
            self._resubscribe_due = False
            await self._attempt_observe_mode()
        elif self._observe.mode == MODE_POLL:
            await self._maybe_retry_observe_mode()

        # Background task, not async_create_task: self-limiting (cancelled
        # and recreated every cycle, see above) and owned entirely by the
        # coordinator, so it shouldn't be tied into HA's startup/shutdown
        # sequencing -- a subpoll in flight (up to ~27s) would delay both
        # (issue #207).
        if self._hot_hrefs or self._warm_hrefs:
            self._subpoll_task = self.hass.async_create_background_task(
                self._run_subpolls(force=sweep_mismatch), name="localthings_subpoll"
            )

        return flatten(self.bound, self.entity_resources())

    # ------------------------------------------------------------------
    # Command dispatch (called by entity platforms in Task 5)
    # ------------------------------------------------------------------

    async def async_send_command(self, bound_entity: BoundEntity, payload: Any) -> None:
        """Write a value to the device. Retries once on a dead session
        (issue #294); raises HomeAssistantError if that retry fails too.

        A description-level validate_fn (SwitchDesc only, currently) rejects
        a write with a user-facing message ahead of write_fn's silent
        no-op. The remote-control check runs first, unconditionally, unless
        the user opted out via CONF_BYPASS_REMOTE_CONTROL (issue #54: some
        devices accept some writes even while reporting remote control off)
        or the laundry firmware declares itself writable without Smart
        Control."""
        desc = bound_entity.desc
        write_fn = getattr(desc, "write_fn", None)
        if write_fn is None:
            return
        href = bound_entity.href
        # Through entity_rep(), not the bare cache: write_fn must see the
        # same rep exists_fn/rep_fn were handed, including the merged
        # cloud-program field (issue #342). Identical to the cache entry for
        # every href that field doesn't touch, and without copying the whole
        # tree to read one rep.
        rep = self.entity_rep(href or "")
        # The remote-control gate below keys off the raw on-the-wire href
        # and a raw snapshot -- /remotectrl/* is a shared, MAIN-only
        # resource that a subdevice's canonical_resources() view (owned
        # hrefs only) would drop entirely. write_fn/validate_fn, by
        # contrast, are written against canonical hrefs (same convention as
        # exists_fn/rep_fn -- see entity.py's _resources), so they get this
        # entity's own subdevice view instead of the raw snapshot: without
        # it, a composite device's write_fn reading resources.get(some
        # canonical href) (e.g. airconditioner._temperature_step) would
        # silently see the master's resource instead of its own subdevice's.
        raw_resources = self._cache.snapshot()
        bypass_remote_control = self._entry.options.get(CONF_BYPASS_REMOTE_CONTROL, False)
        if (
            not bypass_remote_control
            and remote_control_required_for_write(raw_resources, href or "")
            and not remote_control_enabled(raw_resources)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="remote_control_disabled",
            )
        resources = self.canonical_resources(bound_entity.subdevice)
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

        # The write's actual target, not necessarily bound_entity.href -- a
        # composite entity (the AC's ClimateDesc) drives writes to sibling
        # resources via path_segs (see airconditioner._climate_write).
        # Applying the optimistic value to bound_entity.href instead caused
        # the 20-60s lag in issues #17/#53: the wrong resource got the
        # optimistic merge while the one HA actually displays from never
        # did.
        #
        # path_segs are canonical (issue #177); translate through this
        # entity's own subdevice so a subdevice's actual href (e.g.
        # /mode/vs/1) is targeted instead -- identity transform for MAIN.
        write_href = bound_entity.subdevice.to_actual("/" + "/".join(path_segs))
        path_segs = [s for s in write_href.strip("/").split("/") if s]

        # Apply optimistically before starting the settle guard -- guard and
        # apply share the same gate (mark_write_pending), so reversing the
        # order would drop the very update it exists to protect (issue #27).
        #
        # settle_s must outlast the PUT plus the confirming refresh, not
        # DEFAULT_SETTLE_S's fixed few seconds: the refresh is a full
        # summary poll that can legitimately take tens of seconds (see
        # _poll_once), and some writes (issue #9's washer course/detergent/
        # softener selection) settle on-device well after that. A short
        # fixed window let a stale confirm poll land unprotected and revert
        # the optimistic value, read by users as the write "reverting, then
        # re-applying" itself a few seconds later. Releasing the guard early
        # (right after the first confirming refresh) was tried and reverted
        # for the same reason, plus races on overlapping writes to the same
        # href.
        #
        # write_fn bodies touching options/items now carry only the changed
        # token(s) (issue #54), not the whole array -- but apply()'s
        # field-level merge doesn't know that and would wipe every sibling
        # option/item for the settle window. Pre-merge here the way the
        # device does, so the optimistic cache entry stays complete; the
        # wire `body` stays minimal.
        optimistic_body = body
        new_options = body.get("x.com.samsung.da.options")
        if isinstance(new_options, list):
            cached_options = (self._cache.get(write_href) or {}).get("x.com.samsung.da.options")
            optimistic_body = {
                **optimistic_body,
                "x.com.samsung.da.options": merge_options_field(cached_options, new_options),
            }
        # Same fact, items[] shape (see airconditioner._climate_write's
        # vendor temperature write).
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
            if self._session is None:
                self._connect_session()
            sess = self._session
            if sess is None:
                raise RuntimeError("no session")
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=self._POST_TIMEOUT_S)
            self._log.info("PUT %s → code %#04x", write_href, code)

        # Mirrors the poll path's reconnect-and-retry (issue #294): a PUT
        # landing on a session Samsung's firmware closed between polls used
        # to be silently lost -- no retry, no user-facing error.
        async with self._session_lock:
            try:
                await self.hass.async_add_executor_job(_do_put)
            except Exception as e:
                self._log.warning("command failed for %s, reconnecting: %s", write_href, e)
                await self.hass.async_add_executor_job(self._close_session)
                # The session is dead the moment it's closed, so any OBSERVE
                # subscriptions on it are too -- downgrade here, before the
                # retry, so a retry that also fails doesn't leave mode
                # claiming "Push" on a session that no longer exists
                # (issue #294; the poll path handles the same fact for its
                # own reconnect the same way, unconditionally on close).
                if self._observe.mode == MODE_OBSERVE:
                    self._observe.downgrade_to_poll()
                    self._resubscribe_due = True
                await asyncio.sleep(self._RECONNECT_PAUSE_S)
                try:
                    await self.hass.async_add_executor_job(_do_put)
                except Exception as e2:
                    self._log.error("command failed for %s after reconnect: %s", write_href, e2)
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="command_failed",
                        translation_placeholders={"href": write_href, "error": str(e2)},
                    ) from e2
                # The retry's own reconnect pause + second PUT can eat well
                # into the settle window armed above, leaving too little of
                # it for the confirming poll below and reviving the
                # revert-then-reapply symptom settle_s exists to prevent
                # (issue #9). Re-arm it fresh now that the write actually
                # landed.
                self._observe.mark_write_pending(
                    write_href, settle_s=self._POST_TIMEOUT_S + self._POLL_TIMEOUT_S
                )
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Debug raw write/read (issue #54, extended for issue #300): a
    # power-user escape hatch shared by the options-flow debug panel and
    # the write_resource/read_resource services (services.py) for probing
    # a device's write contract directly. Deliberately bypasses the
    # remote-control block and all write_fn/validate_fn above -- use with
    # care.
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

    def _raw_read_blocking(self, path_segs: list[str], href: str) -> tuple[int, dict, Any]:
        """Debug primitive: a live GET, deliberately bypassing the cache
        (issue #300) -- the cache can be up to a poll interval stale,
        exactly the staleness that makes testing whether a write held or
        got silently reverted by the board unreliable. Blocking -- runs in
        executor.

        Returns `(code, rep, body)`. `rep` is the decoded body only when it
        is a Property map, since that's the shape the observe cache and
        every capability are written against. `body` is whatever CBOR
        actually decoded to, and exists because a Collection answers a
        *list*, not a map: `/device/0` and its `x.com.samsung.devcol`
        siblings return the `[devcol rep, {href, rep}, ...]` batch
        `parse_device0_batch` reads. Reporting only `rep` rendered those as
        an accepted-but-empty `2.05 {}`, which reads as "the resource is
        there and has nothing in it" -- the opposite of what a full batch
        means, and how issue #335's `/sec/devices` was nearly written off.
        """
        if self._session is None:
            self._connect_session()
        sess = self._session
        if sess is None:
            raise RuntimeError("no session")
        code, payload = sess.get(path_segs, timeout=10.0)
        rep: dict = {}
        body: Any = None
        if code == 0x45 and payload:
            try:
                body = cbor2.loads(payload)
            except Exception as e:
                self._log.debug("raw read decode failed for %s: %s", href, e)
                body = None
            if isinstance(body, dict):
                self._observe.apply(href, body, source="poll")
                rep = body
        return code, rep, body

    async def async_raw_read(self, href: str) -> tuple[int, dict, Any]:
        """Debug-only live GET (issue #300, backs the read_resource
        service). Same href validation as async_raw_write. Three-tuple --
        see `_raw_read_blocking` for why the raw body comes back alongside
        the Property-map `rep`."""
        path_segs = _href_to_path_segs(href)
        if not path_segs:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="resource_href_required",
            )
        norm_href = "/" + "/".join(path_segs)
        async with self._session_lock:
            try:
                return await self.hass.async_add_executor_job(
                    self._raw_read_blocking, path_segs, norm_href
                )
            except Exception as e:
                # Unlike async_raw_write_sequence, there's nothing to
                # reconnect-and-retry here -- a live debug read either lands
                # or it doesn't, and a service call is the one place on this
                # path a raw session exception would otherwise reach a user
                # untranslated (write_resource already goes through
                # HomeAssistantError; this brings read_resource in line).
                if not isinstance(e, TimeoutError):
                    # Same TimeoutError-vs-anything-else split as
                    # _poll_once: a block-ACK timeout alone doesn't prove
                    # the session is dead, but anything else does -- and
                    # leaving a confirmed-dead one installed would fail
                    # every read/write identically until the next real
                    # poll cycle's own reconnect notices.
                    await self.hass.async_add_executor_job(self._close_session)
                self._log.warning("debug read failed for %s: %s", norm_href, e)
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="debug_read_failed",
                    translation_placeholders={"href": norm_href, "error": str(e)},
                ) from e

    async def async_raw_write_sequence(
        self,
        writes: list[dict],
        *,
        verify_after: float = 0.0,
        hold_session_lock: bool = True,
    ) -> dict[str, Any]:
        """Debug-only ordered multi-write (issue #300): a Samsung wall oven
        board discards settings writes while idle and only keeps them once
        a cycle is already running, which no single-write debug pass can
        probe for.

        `hold_session_lock` (default) keeps `_session_lock` for the whole
        sequence, settle waits included, so nothing interleaves between
        steps and blurs which write the appliance reacted to -- at the cost
        of blocking polls and entity writes for the sequence's full length
        (up to 10 x 30s). Pass False to take the lock per write and release
        it across the waits, for a long sequence where a stalled poll costs
        more than an interleaved read.

        `writes` are already on-the-wire hrefs: subdevice translation
        (canonical -> actual) is services.py's job, not this method's --
        this primitive has no notion of subdevices, same as the original
        single-write async_raw_write never did.

        `async_raw_write` below delegates here with a one-item sequence, so
        its signature/return and tests/test_coordinator_raw_write.py stay
        unchanged.
        """
        if not writes or len(writes) > _DEBUG_MAX_WRITES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="debug_too_many_writes",
            )
        if not 0 <= verify_after <= _DEBUG_MAX_VERIFY_AFTER_S:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="debug_verify_after_out_of_range",
            )
        # Validate every item before touching the session -- a rejected
        # call must fail before any write goes out (same posture the
        # single-write path has always had; see
        # test_raw_write_validation_errors_do_not_touch_the_session).
        parsed = [_validate_debug_write_item(w) for w in writes]

        results: list[dict[str, Any]] = []
        last_payload_by_href: dict[str, dict] = {}
        # Exactly one of these is the real lock, never both -- asyncio.Lock
        # isn't reentrant, so nesting the same one would deadlock.
        outer_lock = self._session_lock if hold_session_lock else contextlib.nullcontext()
        try:
            async with outer_lock:
                for i, (path_segs, href, payload, settle) in enumerate(parsed):
                    per_write_lock = (
                        contextlib.nullcontext() if hold_session_lock else self._session_lock
                    )
                    async with per_write_lock:
                        before = self.resource(href)
                        code, after = await self.hass.async_add_executor_job(
                            self._raw_write_blocking, path_segs, payload, href
                        )
                    last_payload_by_href[href] = payload
                    results.append(
                        {
                            "href": href,
                            "code": _coap_code_str(code),
                            "raw_code": code,
                            "accepted": _coap_accepted(code),
                            "before": before,
                            "after": after,
                            "changed": all(after.get(k) == v for k, v in payload.items()),
                        }
                    )
                    # Under the default this wait happens inside the lock, so
                    # nothing lands between two writes to blur which one the
                    # appliance reacted to; see hold_session_lock above.
                    if settle and i < len(parsed) - 1:
                        await asyncio.sleep(settle)
        except Exception as err:
            # A drop partway leaves the appliance holding whatever already
            # landed, so the error has to say which writes got through.
            done = ", ".join(r["href"] for r in results) or "none"
            self._log.warning(
                "raw write sequence failed after %d of %d writes (completed: %s): %s",
                len(results),
                len(parsed),
                done,
                err,
            )
            await self.async_request_refresh()
            raise HomeAssistantError(
                f"Raw write sequence failed after {len(results)} of {len(parsed)} writes "
                f"(completed: {done}). The appliance may be holding a partial sequence."
            ) from err

        response: dict[str, Any] = {"results": results}
        if verify_after > 0:
            # Released, not held, across this wait: holding _session_lock
            # through up to 60s would stall the summary poll for that whole
            # window (same reasoning as _attempt_observe_mode's grace wait,
            # issue #294). A poll interleaving here is harmless -- just
            # another read of the same hrefs.
            await asyncio.sleep(verify_after)
            verified: dict[str, Any] = {}
            async with self._session_lock:
                for href in dict.fromkeys(r["href"] for r in results):
                    read_error: str | None = None
                    try:
                        vcode, vrep, _vbody = await self.hass.async_add_executor_job(
                            self._raw_read_blocking, _href_to_path_segs(href), href
                        )
                    except Exception as e:
                        # The write already landed -- see `results` above,
                        # built before this wait ever started. A failed
                        # confirmation read (the session dying in the gap
                        # verify_after just waited out, say) must not lose
                        # that outcome behind a raised exception here, and
                        # one href's failure shouldn't stop the rest of the
                        # batch from being checked. Same "couldn't verify"
                        # posture as a 4.04/empty read below: held stays
                        # None, not False -- but raw_code 0 alone is also
                        # what a 4.04 produces, so read_error is what tells
                        # the two apart for a caller inspecting the response.
                        self._log.debug("raw write verification read failed for %s: %s", href, e)
                        vcode, vrep = 0, {}
                        read_error = str(e)
                    # None, not False, when the re-read brought back nothing
                    # to compare: every comparison against an empty rep is
                    # False, which would report a 4.04 as a revert -- the one
                    # distinction verify_after exists to draw.
                    read_ok = _coap_accepted(vcode) and bool(vrep)
                    verified[href] = {
                        "code": _coap_code_str(vcode),
                        "raw_code": vcode,
                        "rep": vrep,
                        "held": (
                            all(vrep.get(k) == v for k, v in last_payload_by_href[href].items())
                            if read_ok
                            else None
                        ),
                        "read_error": read_error,
                    }
            response["verified"] = verified

        # Hasten a summary poll so entities on other resources catch up
        # too -- a debug write can affect siblings, not just its href. Once
        # per sequence, not per write: the whole point of ordering writes
        # under one lock hold is to control exactly what the device sees
        # and when, which a refresh racing in mid-sequence would undermine.
        await self.async_request_refresh()
        return response

    async def async_raw_write(self, href: str, body: dict) -> tuple[int, dict]:
        """Debug-only arbitrary write (issue #54). Bypasses remote-control
        and write_fn/validate_fn; sends `body` verbatim as a partial-rep
        PATCH to `href`. Returns (coap_code, new_rep) read back right
        after -- a thin single-write wrapper over async_raw_write_sequence
        (issue #300)."""
        sequence = await self.async_raw_write_sequence([{"href": href, "payload": body}])
        only = sequence["results"][0]
        return only["raw_code"], only["after"]
