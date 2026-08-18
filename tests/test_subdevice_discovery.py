"""End-to-end discovery tests for issue #177's two composite-device
fixtures, against the real LocalThingsCoordinator (not the HA-free
registry-level helpers test_subdevices.py/test_unique_ids.py use) -- this is
what actually exercises _enumerate_subdevices_blocking + _run_discovery
together, including device_info_for/via_device and the "no phantom
/device/2 entities" guarantee.
"""

from __future__ import annotations

from typing import Any, cast

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartthings_local.protocol.dtls_session import DtlsCoapSession

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.entities import ClimateDesc
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FakeCoapSession, _load_device_full

ENTRY_DATA = {
    CONF_HOST: "10.0.0.177",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----",
    CONF_LEAF_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----",
}


def _coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id="localthings_SUBDEVICE-TEST",
    )
    entry.add_to_hass(hass)
    return LocalThingsCoordinator(hass, entry)


async def _discover_with(
    coordinator: LocalThingsCoordinator,
    resources: dict,
    oic_res,
    seeds: dict,
) -> None:
    """Run the same two-step sequence _async_update_data's first cycle does
    (enumerate, then discover) against arbitrary resources/oic_res/seeds,
    without the polling/reconnect machinery around it -- see coordinator.py's
    _enumerate_subdevices_blocking/_run_discovery. `_discover` below is the
    fixture-file-backed convenience wrapper most tests want."""
    coordinator._session = cast(DtlsCoapSession, FakeCoapSession(seeds))
    # _connect_session (skipped here -- the session is pre-set) is what
    # normally populates _identity via read_identity; set it directly with
    # the fixture's real /oic/res so enumeration sees the same links a live
    # read_identity call would have captured.
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="",
        name="",
        serial=None,
        device_types=(),
        raw={"/oic/p": {}, "/oic/d": {}, "/oic/res": oic_res},
    )
    merged = await coordinator.hass.async_add_executor_job(
        coordinator._enumerate_subdevices_blocking,
        resources,
    )
    # Mirror _async_update_data's first-cycle order exactly: discover, then
    # drop the candidates the liveness gate rejected, then apply what's left
    # to the observe/cache layer. The apply has to happen (canonical_resources
    # -- device_info_for, is_legacy_board, ... -- reads the cache, not the
    # dict passed to _run_discovery), but it has to happen *after* the gate,
    # or a rejected slot's reps get frozen into the cache forever. Applying
    # first here would leave this helper testing an ordering production no
    # longer uses.
    coordinator._run_discovery(merged)
    for href, rep in coordinator._live_subdevice_resources(merged).items():
        coordinator._observe.apply(href, rep, source="poll")


async def _discover(coordinator: LocalThingsCoordinator, name: str) -> None:
    """Fixture-file-backed convenience wrapper around _discover_with."""
    resources, oic_res, seeds = _load_device_full(name)
    await _discover_with(coordinator, resources, oic_res, seeds)


def _climate_bound(coordinator, subdevice_key: str | None):
    from custom_components.localthings.registry.subdevices import MAIN

    for b in coordinator.bound:
        if isinstance(b.desc, ClimateDesc):
            if subdevice_key is None and b.subdevice == MAIN:
                return b
            if subdevice_key is not None and b.subdevice.key == subdevice_key:
                return b
    return None


# ---------------------------------------------------------------------------
# Pattern A reporter -- ARTIK051_DONGLE_FAC_18K, indexed siblings
# ---------------------------------------------------------------------------


async def test_pattern_a_materializes_master_and_bedroom_subdevice(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")

    assert [su.key for su in coordinator.subdevices] == ["1"]

    main_climate = _climate_bound(coordinator, None)
    sub1_climate = _climate_bound(coordinator, "1")
    assert main_climate is not None
    assert sub1_climate is not None
    assert main_climate.href == "/mode/vs/0"
    assert sub1_climate.href == "/mode/vs/1"


async def test_pattern_a_device_2_produces_no_entities_at_all(hass: HomeAssistant):
    """The reporter's /device/2 is the unused SmartThings slot
    (DESIGN-177.md section 4): it answers its seed with a full-shaped
    batch, but every climate-state rep on it is empty. It must be recorded
    as skipped, not materialized, and must contribute zero bound entities."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")

    assert "2" not in [su.key for su in coordinator.subdevices]
    assert any(
        skip.subdevice.kind == "indexed" and skip.subdevice.key == "2"
        for skip in coordinator._skipped_subdevices
    )
    assert not any(b.subdevice.key == "2" for b in coordinator.bound)
    assert not any(href.endswith("/2") for href in coordinator._hot_hrefs)
    assert not any(href.endswith("/2") for href in coordinator._warm_hrefs)


async def test_pattern_a_sub1_device_info_links_via_device_to_master(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")

    sub1 = next(su for su in coordinator.subdevices if su.key == "1")
    info = coordinator.device_info_for(sub1)

    master_serial = coordinator.device_key
    assert info["identifiers"] == {(DOMAIN, f"{master_serial}_1")}
    assert info["via_device"] == (DOMAIN, master_serial)
    # The subdevice's own /information/vs/1 (real, ARTIK051_DONGLE_FAC_RAC_18K)
    # is what models this device, not the master's -- but it never reaches
    # the name, which HA slugifies into this subdevice's entity_ids.
    assert info["model"] == "ARTIK051_DONGLE_FAC_RAC_18K"
    assert info["name"] == "Samsung Air Conditioner Unit 2"


# ---------------------------------------------------------------------------
# Issue #214 -- a *non-composite* board whose speculative /device/1 probe
# answers with an unused slot that reports the appliance's energy counter.
# ---------------------------------------------------------------------------


async def test_krac_18k_energy_only_slot_is_not_materialized(hass: HomeAssistant):
    """The issue #214 reporter's single-split AR12NXWXCWKNEU (ARTIK051_KRAC_18K,
    one indoor unit) answers /device/1 with the Pattern A /device/2 shape --
    every operational rep empty {} -- plus a populated
    /energy/consumption/vs/1 carrying a lifetime cumulativePower. That one
    counter was the only primary entity the candidate flattened to a value
    for, and it was enough to materialize a phantom second air conditioner
    device in HA (the duplicate the reporter saw). A cumulative meter is a
    whole-appliance total, not evidence that hardware is installed at this
    slot, so the candidate must now be recorded as skipped and contribute
    nothing: no bound entities, no HA device, no hot/warm hrefs."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_krac_18k_slot")

    assert coordinator.subdevices == []
    assert [s.subdevice.key for s in coordinator._skipped_subdevices] == ["1"]
    # The gate ran against real bindings, not against nothing -- these are the
    # six hrefs the reporter's own diagnostics reported for the (then
    # materialized) subdevice, /energy/consumption/vs/1 among them.
    assert coordinator._skipped_subdevices[0].hrefs == (
        "/alarms/vs/1",
        "/diagnosis/vs/1",
        "/energy/consumption/vs/1",
        "/humidity/vs/1",
        "/mode/vs/1",
        "/temperature/current/1",
    )
    assert coordinator._subdevice_probes["/device/1"] is True

    assert not any(b.subdevice.key == "1" for b in coordinator.bound)
    assert not any(href.endswith("/1") for href in coordinator._hot_hrefs)
    assert not any(href.endswith("/1") for href in coordinator._warm_hrefs)

    # The master is untouched -- one climate entity, on the master's own
    # device, exactly as this board behaved before subdevice support existed.
    assert _climate_bound(coordinator, None) is not None
    assert _climate_bound(coordinator, "1") is None


async def test_krac_18k_slot_state_never_reaches_the_cache(hass: HomeAssistant):
    """A rejected candidate's reps must not be applied to the state cache
    either (the same guarantee _live_subdevice_resources gives the Pattern A
    /device/2 slot): the reporter's /energy/consumption/vs/1 would otherwise
    sit frozen in `last_resources` -- and in every diagnostics dump built
    from it -- at its first-discovery value forever, since nothing polls the
    slot again."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_krac_18k_slot")

    assert not any(href.endswith("/1") for href in coordinator.last_resources)
    # It's kept aside for diagnostics only, which is where a reader can still
    # check the gate's call for themselves.
    assert "/energy/consumption/vs/1" in coordinator._skipped_subdevice_resources


# ---------------------------------------------------------------------------
# Issue #177's Pattern B reporter -- TP2X_FAC_BORA_21K, UUID-prefixed tree
# ---------------------------------------------------------------------------

_SUB_UUID = "6c2dff6d-ee5c-dad1-6a5e-000000000001"


async def test_fac_bora_2in1_materializes_prefixed_wall_subdevice(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora_2in1")

    assert [su.key for su in coordinator.subdevices] == [_SUB_UUID]
    assert coordinator.subdevices[0].kind == "prefixed"

    main_climate = _climate_bound(coordinator, None)
    sub_climate = _climate_bound(coordinator, _SUB_UUID)
    assert main_climate is not None
    assert sub_climate is not None
    assert main_climate.href == "/mode/vs/0"
    assert sub_climate.href == f"/{_SUB_UUID}/mode/vs/0"


async def test_fac_bora_2in1_subdevice_device_info(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora_2in1")

    subdevice = coordinator.subdevices[0]
    info = coordinator.device_info_for(subdevice)

    master_serial = coordinator.device_key
    assert info["identifiers"] == {(DOMAIN, f"{master_serial}_{_SUB_UUID}")}
    assert info["via_device"] == (DOMAIN, master_serial)
    # Confirmed live by the reporter (DESIGN-177.md section 1): the wall
    # subdevice's own identity, distinct from the master's TP2X_FAC_BORA_21K.
    assert info["model"] == "TP2X_FAC_BORA_RAC_21K"
    # Neither the board string nor the subdevice UUID is a name a user
    # wants slugified into `climate.<this>_...`.
    assert info["name"] == "Samsung Air Conditioner Unit 2"


async def test_fac_bora_2in1_unique_ids_include_subdevice_prefix(hass: HomeAssistant):
    """The prefixed subdevice's unique_id carries the full subdevice UUID
    (non-alphanumerics stripped), not a truncation or an ordinal -- see
    Subdevice.key_prefix."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora_2in1")

    from custom_components.localthings.entity import LocalThingsEntity

    sub_climate = _climate_bound(coordinator, _SUB_UUID)
    entity = LocalThingsEntity(coordinator, sub_climate)
    expected_slug = _SUB_UUID.replace("-", "")
    assert entity._attr_unique_id == (
        f"{DOMAIN}_{coordinator.device_key}_subdevice_{expected_slug}_climate"
    )


# ---------------------------------------------------------------------------
# Same reporter and physical unit/UUID again -- issue #205, but this
# time /<uuid>/device/0 doesn't answer. Exercises enumerate_subdevices'
# per-href flat-probe fallback (registry/subdevices.py) against a real
# capture instead of the synthetic sessions test_subdevices.py uses.
# ---------------------------------------------------------------------------


async def test_flat_probe_priority_puts_live_climate_state_before_cold_metrics(
    hass: HomeAssistant,
):
    """Registry metadata drives fallback order without a model-specific list."""
    resources, _oic_res, _seeds = _load_device_full("airconditioner_fac_bora_205_flat")
    coordinator = _coordinator(hass)

    priority = coordinator._subdevice_probe_priority(resources)

    assert "/mode/vs/0" in priority[:4]
    assert priority.index("/mode/vs/0") < priority.index("/energy/consumption/vs/0")


async def test_fac_bora_205_flat_fallback_finds_candidate_but_gate_holds_it_back(
    hass: HomeAssistant,
):
    """The fixture's only seeded UUID-prefixed href is /information/vs/0 --
    the one href ever actually confirmed live under this prefix (issue #177
    comment 5113518087) -- since nothing else has been confirmed yet for
    this unit. That's enough for the flat-probe fallback to find a
    candidate, but /information/vs/0 binds no entity on its own (it's only
    ever read for device-type resolution, never bound as a capability), so
    discover_partitioned's liveness gate correctly holds it back rather than
    materializing a phantom climate card from unconfirmed hrefs. This is the
    honest current state of issue #205, not a guess at its resolution."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora_205_flat")

    assert coordinator.subdevices == []
    assert [s.subdevice.key for s in coordinator._skipped_subdevices] == [_SUB_UUID]
    skipped = coordinator._skipped_subdevices[0].subdevice
    assert skipped.kind == "prefixed"
    assert skipped.seed_path == ()
    assert skipped.flat_hrefs == ("/information/vs/0",)

    assert coordinator._subdevice_probes[f"/{_SUB_UUID}/device/0"] is False
    assert coordinator._subdevice_probes[f"/{_SUB_UUID}/information/vs/0"] is True

    # Confirms the master itself is completely unaffected by its sibling's
    # Collection endpoint not answering -- same guarantee every other
    # subdevice test in this file relies on.
    assert _climate_bound(coordinator, None) is not None


async def test_flat_subdevice_materializes_and_repolls_end_to_end(hass: HomeAssistant):
    """Synthetic (not a real capture, unlike the fixture-driven test above) --
    exercises the one path nothing else covers: a flat-mode prefixed
    subdevice with *enough* confirmed hrefs to actually pass
    discover_partitioned's liveness gate and materialize a real climate
    entity, then a subsequent _poll_subdevice_seed re-poll refreshing its
    state all the way through to canonical_resources -- the path a real
    resolution of issue #205 (once more hrefs are confirmed live for some
    unit) would actually need.

    Also pins _poll_subdevice_flat_hrefs' hot/warm skip: climate-critical
    hrefs (power/mode/temperature) land on the warm tier by discovery's own
    rules, so they're already kept fresh every few seconds by _run_subpolls
    -- re-fetching them again on this once-per-summary-poll pass would only
    add GETs, not freshness, so they're the ones this method must skip.
    /option/autoclean/vs/0 is cold-tier and is what actually needs this
    path."""
    resources, oic_res, _real_seeds = _load_device_full("airconditioner_fac_bora_2in1")
    seeds = {
        # No (_SUB_UUID, 'device', '0') entry -- forces the flat fallback,
        # same as the real issue #205 capture above, but this time with
        # enough hrefs answering to actually materialize. power/mode/
        # temperature values copied verbatim from that fixture's own (real)
        # Collection-batch seed, just served individually instead of
        # batched, to isolate "does flat mode produce the same result as
        # Collection mode" as the only variable.
        f"/{_SUB_UUID}/power/vs/0": {"x.com.samsung.da.power": "On"},
        f"/{_SUB_UUID}/mode/vs/0": {
            "x.com.samsung.da.supportedModes": ["Cool", "Dry", "Wind", "Auto"],
            "x.com.samsung.da.modes": ["Cool"],
            "x.com.samsung.da.options": [],
        },
        f"/{_SUB_UUID}/temperature/current/0": {
            "range": [18.0, 30.0],
            "units": "C",
            "temperature": 26.0,
        },
        f"/{_SUB_UUID}/temperature/desired/0": {
            "range": [18.0, 30.0],
            "units": "C",
            "temperature": 24.0,
        },
        # Cold-tier -- not covered by _run_subpolls, so this is the href
        # that actually depends on _poll_subdevice_flat_hrefs to ever
        # refresh at all.
        f"/{_SUB_UUID}/option/autoclean/vs/0": {
            "x.com.samsung.da.settingStatus": "Off",
        },
    }
    coordinator = _coordinator(hass)
    await _discover_with(coordinator, resources, oic_res, seeds)

    assert [su.key for su in coordinator.subdevices] == [_SUB_UUID]
    subdevice = coordinator.subdevices[0]
    assert subdevice.seed_path == ()
    assert subdevice.flat_hrefs != ()

    sub_climate = _climate_bound(coordinator, _SUB_UUID)
    assert sub_climate is not None

    # Re-poll: a fresh reading under the prefix should reach
    # canonical_resources through _poll_subdevice_seed's flat-mode branch,
    # not just sit frozen at the one-time enumeration snapshot.
    # FakeCoapSession's `seeds` is typed `dict[str, list]` for the common
    # batch-list shape, but (per its own docstring) also legitimately holds
    # plain Property maps for probe-style hrefs like these two.
    seeds_map = cast("dict[str, Any]", cast(FakeCoapSession, coordinator._session).seeds)
    seeds_map[f"/{_SUB_UUID}/temperature/current/0"] = {
        "range": [18.0, 30.0],
        "units": "C",
        "temperature": 27.5,
    }
    seeds_map[f"/{_SUB_UUID}/option/autoclean/vs/0"] = {
        "x.com.samsung.da.settingStatus": "On",
    }
    refreshed = coordinator._poll_subdevice_seed(subdevice)

    # The warm-tier temperature href is skipped here -- already covered by
    # _run_subpolls at a faster cadence -- so it does NOT show up refreshed
    # through this path.
    assert f"/{_SUB_UUID}/temperature/current/0" not in refreshed
    assert refreshed == {
        f"/{_SUB_UUID}/option/autoclean/vs/0": {"x.com.samsung.da.settingStatus": "On"},
    }

    for href, rep in refreshed.items():
        coordinator._observe.apply(href, rep, source="poll")
    res = coordinator.canonical_resources(subdevice)
    assert res["/option/autoclean/vs/0"]["x.com.samsung.da.settingStatus"] == "On"
    # Confirms the skip is about redundant re-fetching, not stale data --
    # the warm-tier value from initial discovery is still there, untouched.
    assert res["/temperature/current/0"]["temperature"] == 26.0


class _FakeCollectionSession:
    """Minimal session that only ever answers a Collection GET -- used to
    prove the flat-mode re-poll path (issue #205) is only taken when
    flat_hrefs is actually set, not whenever seed_path happens to be
    unusual."""

    def __init__(self, table):
        self.table = table
        self.calls: list[tuple[str, ...]] = []

    def get(self, path, timeout=10.0):
        self.calls.append(tuple(path))
        body = self.table.get(tuple(path))
        if body is None:
            return 0x84, b""
        import cbor2

        return 0x45, cbor2.dumps(body)

    def pace(self):
        pass


def test_poll_subdevice_seed_collection_mode_unaffected_by_flat_fallback(
    hass: HomeAssistant,
):
    """A subdevice with a working Collection endpoint (flat_hrefs empty)
    keeps re-polling it with a single Collection GET, unchanged by issue
    #205's fallback."""
    from custom_components.localthings.registry.subdevices import Subdevice

    coordinator = _coordinator(hass)
    devcol_rep = {"rt": ["x.com.samsung.devcol", "oic.wk.col"]}
    sess = _FakeCollectionSession(
        {
            (_SUB_UUID, "device", "0"): [
                devcol_rep,
                {"href": "/mode/vs/0", "rep": {"mode": "cool"}},
            ],
        }
    )
    coordinator._session = cast(DtlsCoapSession, sess)
    subdevice = Subdevice(kind="prefixed", key=_SUB_UUID, seed_path=(_SUB_UUID, "device", "0"))

    result = coordinator._poll_subdevice_seed(subdevice)

    assert result == {f"/{_SUB_UUID}/mode/vs/0": {"mode": "cool"}}
    assert sess.calls == [(_SUB_UUID, "device", "0")]


def test_poll_subdevice_seed_flat_mode_polls_each_href_individually(
    hass: HomeAssistant,
):
    """A flat-mode subdevice (issue #205) has no Collection to batch-refresh
    through, so each confirmed href is GET individually under the prefix on
    every re-poll -- a href that stops answering just drops out, same
    "never fail the master's poll over a sibling" posture as the Collection
    path."""
    from custom_components.localthings.registry.subdevices import Subdevice

    coordinator = _coordinator(hass)
    sess = _FakeCollectionSession(
        {
            (_SUB_UUID, "mode", "vs", "0"): {"mode": "cool"},
            # (_SUB_UUID, 'power', 'vs', '0') deliberately absent -> drops out.
        }
    )
    coordinator._session = cast(DtlsCoapSession, sess)
    subdevice = Subdevice(
        kind="prefixed",
        key=_SUB_UUID,
        seed_path=(),
        flat_hrefs=("/mode/vs/0", "/power/vs/0"),
    )

    result = coordinator._poll_subdevice_seed(subdevice)

    assert result == {f"/{_SUB_UUID}/mode/vs/0": {"mode": "cool"}}
    assert sess.calls == [
        (_SUB_UUID, "mode", "vs", "0"),
        (_SUB_UUID, "power", "vs", "0"),
    ]


def test_poll_subdevice_seed_flat_mode_skips_hrefs_covered_by_hot_warm_subpolls(
    hass: HomeAssistant,
):
    """A flat href already in the hot/warm sub-poll tiers is refreshed every
    few seconds by _run_subpolls -- re-fetching it again on this
    once-per-summary-poll pass would only add GETs, not freshness, so
    _poll_subdevice_flat_hrefs must not even attempt it."""
    from custom_components.localthings.registry.subdevices import Subdevice

    coordinator = _coordinator(hass)
    sess = _FakeCollectionSession(
        {
            (_SUB_UUID, "mode", "vs", "0"): {"mode": "cool"},
            (_SUB_UUID, "power", "vs", "0"): {"power": "On"},
        }
    )
    coordinator._session = cast(DtlsCoapSession, sess)
    coordinator._warm_hrefs = [f"/{_SUB_UUID}/mode/vs/0"]
    subdevice = Subdevice(
        kind="prefixed",
        key=_SUB_UUID,
        seed_path=(),
        flat_hrefs=("/mode/vs/0", "/power/vs/0"),
    )

    result = coordinator._poll_subdevice_seed(subdevice)

    assert result == {f"/{_SUB_UUID}/power/vs/0": {"power": "On"}}
    assert sess.calls == [(_SUB_UUID, "power", "vs", "0")]


async def test_multidevice_probe_never_reaches_discovery_or_the_cache(
    hass: HomeAssistant,
):
    """/multidevice/vs/0 is probed on every device but is metadata, not state.

    It has to stay out of the resources dict on both counts. Discovery would
    otherwise report it as an unbound href on every family whose registry
    doesn't ignore that path -- only the AC one does -- raising a spurious
    "incomplete capability coverage" repair for, say, a washer whose
    firmware happens to answer it. And nothing polls it after discovery, so
    anything applied to the state cache would sit frozen there forever.

    Driven with a washer fixture precisely because the AC registry's own
    ignore entry would mask the coverage half of this on an AC.
    """
    resources, _oic, _seeds = _load_device_full("washer_flexwash")
    coordinator = _coordinator(hass)
    # /multidevice/vs/0 here is a plain Property map, not a batch list -- see
    # FakeCoapSession's own docstring on the two shapes its `seeds` values can
    # take; its `seeds` param type only names the more common (list) shape.
    coordinator._session = cast(
        DtlsCoapSession,
        FakeCoapSession(
            cast(
                "dict[str, list]",
                {
                    "/multidevice/vs/0": {"x.com.samsung.da.numofsubdevice": "2"},
                },
            )
        ),
    )
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="",
        name="",
        serial=None,
        device_types=(),
        raw={"/oic/p": {}, "/oic/d": {}, "/oic/res": []},
    )
    merged = await hass.async_add_executor_job(
        coordinator._enumerate_subdevices_blocking,
        resources,
    )
    coordinator._run_discovery(merged)
    for href, rep in coordinator._live_subdevice_resources(merged).items():
        coordinator._observe.apply(href, rep, source="poll")

    assert "/multidevice/vs/0" not in merged
    assert "/multidevice/vs/0" not in coordinator._unbound_hrefs
    assert "/multidevice/vs/0" not in coordinator.last_resources
    # Still captured, just not as device state.
    assert coordinator._multidevice == {"x.com.samsung.da.numofsubdevice": "2"}
