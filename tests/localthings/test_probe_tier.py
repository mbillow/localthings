"""The probe tier: reading hrefs the /device/0 batch never carries.

Issue #301. `/file/transfer/vs/0` holds the appliance's own usage history
and appears in no batch on record -- 83 fixtures, none of them -- so
`discover()` can never reach it and a capability declaring it would never
bind. `Capability.poll_tier="probe"` puts an href on `registry.PROBE_HREFS`,
which the coordinator GETs directly and folds into the resources dict before
discovery runs.

`/file/list/vs/0` binds nothing -- it exists so the probed rep reaches
diagnostics and so the href is not a coverage gap. `/file/transfer/vs/0`
binds one gated fallback; see test_usage_energy_fallback.py.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.localthings.const import DOMAIN, SUMMARY_INTERVAL_S
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve as resolve_registry
from custom_components.localthings.registry.registry import PROBE_HREFS
from custom_components.localthings.registry.subdevices import MAIN, Subdevice

from .conftest import _load_fridge_resources as _load_fridge


async def _advance_one_cycle(hass):
    """Past one summary interval, so the coordinator polls again.

    `wait_background_tasks` is load-bearing here for the same reason as in
    test_offline_setup: the interval refresh runs as a background task that a
    plain block_till_done does not await.
    """
    from homeassistant.util import dt as dt_util

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SUMMARY_INTERVAL_S + 1))
    await hass.async_block_till_done(wait_background_tasks=True)


FILE_LIST = "/file/list/vs/0"
FILE_TRANSFER = "/file/transfer/vs/0"

# A real capture off a dishwasher: one 12-byte record, decoding as
# <uint32 LE ts=1788037200><uint32 LE 1631 tenths of a kWh><uint32 LE 0>.
BLOB = bytes.fromhex("504893 6a") + bytes.fromhex("5f060000") + bytes.fromhex("00000000")

PROBE_REPS = {
    FILE_LIST: {
        "x.com.samsung.items": [
            {"x.com.samsung.id": "0", "x.com.samsung.name": "/opt/data/energy.db"},
            {"x.com.samsung.id": "1", "x.com.samsung.name": "/opt/data/hass.db"},
        ]
    },
    FILE_TRANSFER: {
        "x.com.samsung.items": [{"x.com.samsung.name": "/mnt/usage.db", "x.com.samsung.blob": BLOB}]
    },
}


def test_probe_hrefs_are_exactly_the_capabilities_that_asked_for_it():
    assert set(PROBE_HREFS) == {FILE_LIST, FILE_TRANSFER}


def test_probe_hrefs_are_absent_from_every_batch_fixture(all_device_fixtures):
    """The premise of the whole tier. If a batch ever starts carrying one of
    these, it no longer needs probing and this tier's cost is unjustified."""
    for name, resources in all_device_fixtures.items():
        for href in PROBE_HREFS:
            assert href not in resources, f"{name} carries {href} in its batch"


def test_a_probed_href_is_registered_so_it_is_not_a_coverage_gap():
    """Probing folds these into `resources`, where an unregistered href
    becomes an unbound-coverage-gap Repair. Every by_type registry has to
    know about them, which `common.UNIVERSAL` is what guarantees."""
    resources = _load_fridge()
    registry = resolve_registry(resources)
    assert registry is not None
    for href in PROBE_HREFS:
        assert href in registry.capabilities


def test_the_file_list_probe_binds_nothing():
    """It is read for the census and for gap coverage, not for an entity --
    the filename it reports is a firmware default that identifies nothing."""
    registry = resolve_registry(_load_fridge())
    assert registry is not None
    for cap in registry.capabilities[FILE_LIST]:
        assert cap.entities == ()


class _ProbeSession:
    """Answers PROBE_HREFS and 4.04s everything else, recording each GET."""

    def __init__(self, answers=None):
        self.answers = PROBE_REPS if answers is None else answers
        self.gets: list[str] = []

    def pace(self):
        pass

    def get(self, path, timeout=None):
        import cbor2

        href = "/" + "/".join(path)
        self.gets.append(href)
        if href in self.answers:
            return 0x45, cbor2.dumps(self.answers[href])
        return 0x84, b""


@pytest.fixture
def probing_entry(hass: HomeAssistant, mock_entry):
    """An entry whose device answers the batch and the probe hrefs.

    Yields `(entry, session, polls)` where `polls` counts summary polls, so a
    cadence assertion can prove the coordinator actually ran the cycles it is
    being credited with rather than passing because nothing happened at all.
    """
    session = _ProbeSession()
    resources = _load_fridge()
    polls: list[int] = []

    def _connect(self):
        self._session = session

    def _poll(self):
        polls.append(1)
        return resources

    with (
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
            _connect,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            _poll,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        yield mock_entry, session, polls


async def test_first_discovery_probes_and_caches_the_result(hass, probing_entry):
    entry, session, _polls = probing_entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert set(PROBE_HREFS) <= set(session.gets)

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    listed = coordinator.resource(FILE_LIST)["x.com.samsung.items"]
    assert [i["x.com.samsung.name"] for i in listed] == [
        "/opt/data/energy.db",
        "/opt/data/hass.db",
    ]
    # The blob reaches the cache as the bytes the appliance sent; making that
    # safe to serialize onward is registry/encode.py's job, not the probe's.
    served = coordinator.resource(FILE_TRANSFER)["x.com.samsung.items"][0]
    assert served["x.com.samsung.blob"] == BLOB


async def test_a_probed_href_does_not_raise_a_coverage_gap(hass, probing_entry):
    """The failure this would otherwise cause: probing makes two hrefs appear
    in `resources` that were never there before, and an unregistered href
    raises the incomplete-coverage Repair at the user."""
    entry, _session, _polls = probing_entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    assert not set(PROBE_HREFS) & set(coordinator._unbound_hrefs)


async def test_a_device_that_404s_the_probe_still_sets_up(hass, mock_entry):
    """Most appliances will answer 4.04, and that must cost nothing."""
    session = _ProbeSession(answers={})
    resources = _load_fridge()

    def _connect(self):
        self._session = session

    with (
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
            _connect,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.bound
    assert coordinator.resource(FILE_TRANSFER) == {}


async def test_the_probe_does_not_run_on_every_summary_poll(hass, probing_entry):
    """A usage file gains one record a day and costs a multi-KB blockwise
    transfer. Riding the 30 s summary interval would be pure waste."""
    entry, session, polls = probing_entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert session.gets.count(FILE_TRANSFER) == 1

    for _ in range(3):
        await _advance_one_cycle(hass)

    # The cycles really ran -- without this the assertion below would pass on
    # a coordinator that had simply stopped polling.
    assert len(polls) >= 4
    assert session.gets.count(FILE_TRANSFER) == 1


async def test_the_probe_runs_again_once_its_cycle_count_is_reached(hass, probing_entry):
    """The other half of the throttle: a file that gains a record a day still
    has to be re-read, or the cache freezes at whatever setup happened to
    see."""
    entry, session, polls = probing_entry
    with patch.object(LocalThingsCoordinator, "_PROBE_EVERY_N_CYCLES", 2):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert session.gets.count(FILE_TRANSFER) == 1

        for _ in range(2):
            await _advance_one_cycle(hass)

    assert len(polls) >= 3
    assert session.gets.count(FILE_TRANSFER) == 2


# ---------------------------------------------------------------------------
# Composite appliances (issue #177): one IP, several indoor subdevices
# ---------------------------------------------------------------------------


def _probe_only_coordinator(hass, entry, session):
    """A coordinator with a session installed and nothing else running, for
    exercising _probe_blocking directly."""
    coordinator = LocalThingsCoordinator(hass, entry)
    coordinator._session = session
    return coordinator


def test_probe_reads_each_subdevices_own_copy_of_the_file(hass, mock_entry):
    """The per-head file is the point of the whole feature. Issue #329 read
    three heads of one multi-split and got three distinct blobs, each pairing
    the shared outdoor-unit energy counter with *that head's* runtime hours.

    Those heads were three config entries on three IPs, so MAIN alone would
    have reached them -- but a composite board puts the same several indoor
    units behind one IP as subdevices, and /oic/res on the FAC_BORA fixtures
    advertises /<uuid>/file/transfer/vs/0 for exactly that. Probing MAIN only
    would read the master's file and silently miss every sibling's.
    """
    uuid = "6c2dff6d-ee5c-dad1-6a5e-000000000001"
    sibling = Subdevice(kind="prefixed", key=uuid, seed_path=(uuid, "device", "0"))
    session = _ProbeSession(answers={})
    coordinator = _probe_only_coordinator(hass, mock_entry, session)

    coordinator._probe_blocking([MAIN, sibling])

    assert session.gets == [
        FILE_LIST,
        FILE_TRANSFER,
        f"/{uuid}{FILE_LIST}",
        f"/{uuid}{FILE_TRANSFER}",
    ]


def test_probe_of_an_indexed_sibling_uses_its_own_index(hass, mock_entry):
    """Pattern A (issue #177's ARTIK051_DONGLE_FAC_18K): siblings are indexed
    rather than UUID-prefixed, and to_actual rewrites the trailing segment."""
    sibling = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    session = _ProbeSession(answers={})
    coordinator = _probe_only_coordinator(hass, mock_entry, session)

    coordinator._probe_blocking([sibling])

    assert session.gets == ["/file/list/vs/1", "/file/transfer/vs/1"]


def test_a_single_subdevice_device_probes_exactly_the_bare_hrefs(hass, mock_entry):
    """MAIN's to_actual is the identity transform, so the ordinary appliance
    is unaffected by any of the above."""
    session = _ProbeSession(answers={})
    coordinator = _probe_only_coordinator(hass, mock_entry, session)

    coordinator._probe_blocking([MAIN])

    assert session.gets == [FILE_LIST, FILE_TRANSFER]


async def test_the_periodic_probe_covers_main_and_every_live_subdevice(hass, probing_entry):
    """The call site, not the primitive above: a sibling materialized at
    discovery has to keep being re-read, not just read once."""
    entry, _session, _polls = probing_entry
    seen: list[list] = []

    with patch.object(LocalThingsCoordinator, "_PROBE_EVERY_N_CYCLES", 1):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
        sibling = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
        coordinator.subdevices = [sibling]

        with patch.object(
            LocalThingsCoordinator,
            "_probe_blocking",
            lambda self, subdevices: seen.append(list(subdevices)) or {},
        ):
            await _advance_one_cycle(hass)

    assert seen == [[MAIN, sibling]]


async def test_the_probe_reaches_discovery_rather_than_only_the_cache(hass, mock_entry):
    """The ordering the whole tier depends on, and the one that was wrong.

    `_run_discovery` is what binds an href to an entity and it runs exactly
    once. A probe landing after it would populate the state cache forever and
    never produce an entity -- which on a composite board means the per-head
    runtime hours of issue #329 silently never appear. So the probed reps,
    MAIN's *and* every candidate subdevice's, must be in the dict discovery
    is handed.
    """
    uuid = "6c2dff6d-ee5c-dad1-6a5e-000000000001"
    sibling = Subdevice(kind="prefixed", key=uuid, seed_path=(uuid, "device", "0"))
    answers = {f"/{uuid}{href}": PROBE_REPS[href] for href in PROBE_HREFS}
    answers.update(PROBE_REPS)
    session = _ProbeSession(answers=answers)
    resources = _load_fridge()
    seen: dict = {}

    def _connect(self):
        self._session = session

    def _enumerate(self, res):
        # The real one opens the session as a side effect; _poll_once is
        # patched out here, so nothing else would.
        self._session = session
        self.subdevices = [sibling]
        return res

    real_discovery = LocalThingsCoordinator._run_discovery

    def _capture(self, res, from_snapshot=False):
        seen.update(res)
        return real_discovery(self, res, from_snapshot)

    with (
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
            _connect,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=resources,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator"
            "._enumerate_subdevices_blocking",
            _enumerate,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._run_discovery",
            _capture,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    for href in PROBE_HREFS:
        assert href in seen, f"MAIN's {href} never reached discovery"
        assert f"/{uuid}{href}" in seen, f"the sibling's {href} never reached discovery"
