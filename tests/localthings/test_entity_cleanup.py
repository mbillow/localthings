"""cleanup.py: which registry rows setup removes, and which it must keep."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import cleanup
from custom_components.localthings.cleanup import (
    REMOVED_ISSUE,
    RETIRED_KEYS,
    async_delete_removed_issues,
    async_notify_removed,
    async_reconcile_entities,
)
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import _key
from custom_components.localthings.registry.batch import is_stub_rep
from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY, resolve
from custom_components.localthings.registry.capabilities import common, fridge
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity, discover
from custom_components.localthings.registry.entities import (
    PLATFORM_OF,
    BinarySensorDesc,
    SensorDesc,
    SwitchDesc,
)
from tests.conftest import FIXTURES, _load_device

from .conftest import LEGACY_ENTRY_DATA, MOCK_SERIAL

PREFIX = f"{DOMAIN}_{MOCK_SERIAL}_"


def _coordinator(resources, capabilities, device_type=None) -> LocalThingsCoordinator:
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            bound=discover(resources, capabilities),
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            device_key=MOCK_SERIAL,
            device_type_name=device_type,
            subdevices=[],
        ),
    )


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4, title="Fridge")
    entry.add_to_hass(hass)
    return entry


def _row(hass, entry, platform, key):
    return (
        er.async_get(hass)
        .async_get_or_create(platform, DOMAIN, PREFIX + key, config_entry=entry)
        .entity_id
    )


def _alive(hass, entity_id) -> bool:
    return er.async_get(hass).async_get(entity_id) is not None


def _all_descriptors():
    for registry in _REGISTRY_BY_KEY.values():
        caps = [c for cs in registry.capabilities.values() for c in cs]
        for cap in caps + registry.pattern_capabilities:
            yield from cap.entities


# --- descriptor and registry invariants -------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dynamic_presence": True, "prune_when_excluded": True, "exists_fn": lambda r, s: True},
        {"dynamic_presence": True, "supersedes": (("sensor", "old"),)},
        {"prune_when_excluded": True},
        {"dynamic_presence": True, "replaces_siblings": True},
    ],
)
def test_descriptors_reject_removal_flags_that_could_drop_a_live_entity(kwargs):
    with pytest.raises(ValueError):
        SwitchDesc(key="x", **kwargs)


def test_retired_keys_collide_with_no_descriptor():
    """The retired rule tail-matches unique_ids, so no live key on that
    platform may equal or end with a retired one."""
    for desc in _all_descriptors():
        for platform, key in RETIRED_KEYS:
            if PLATFORM_OF[type(desc)] == platform:
                assert desc.key != key and not desc.key.endswith(f"_{key}"), desc.key


def test_no_descriptor_shares_a_key_with_the_unbound_sensors():
    """sensor.py adds these outside `bound`; a descriptor sharing the key
    would let the platform-move rule judge their rows."""
    assert not {d.key for d in _all_descriptors()} & {"connection_mode", "last_response"}


def _fixture_names():
    return sorted(p.name.removesuffix("_device.json") for p in FIXTURES.glob("*_device.json"))


@pytest.mark.parametrize("name", _fixture_names())
async def test_every_fixture_keeps_every_entity_it_registers(hass, name):
    """No rule may remove a row the same discovery would register."""
    resources = _load_device(name)
    registry = resolve(resources)
    if registry is None:
        pytest.skip("unrecognized fixture")
    coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            bound=discover(resources, registry.capabilities, registry.pattern_capabilities),
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            device_key=MOCK_SERIAL,
            device_type_name=registry.name,
            subdevices=[],
        ),
    )
    entry = _entry(hass)
    rows = {
        _row(hass, entry, PLATFORM_OF[type(b.desc)], _key(b))
        for b in coordinator.bound
        if _is_included(b, coordinator)
    }
    assert async_reconcile_entities(hass, entry, coordinator) == []
    assert all(_alive(hass, entity_id) for entity_id in rows)


# --- platform moves ----------------------------------------------------------


async def test_a_platform_no_registry_binds_the_key_on_is_removed(hass):
    """Judged from the code: nothing creates a sensor keyed rapid_fridge."""
    entry = _entry(hass)
    resources = {"/refrigeration/vs/0": {"x.com.samsung.da.rapidFridge": "Off"}}
    coordinator = _coordinator(resources, {"/refrigeration/vs/0": [fridge.REFRIGERATION]})
    stale = _row(hass, entry, "sensor", "rapid_fridge")
    live = _row(hass, entry, "switch", "rapid_fridge")

    assert async_reconcile_entities(hass, entry, coordinator) == [stale]
    assert _alive(hass, live)


async def test_a_key_only_expand_fn_creates_is_never_judged(hass):
    """No static descriptor lists its platforms, so none can be ruled out."""
    entry = _entry(hass)
    capability = Capability(href="/x/vs/0", entities=(SensorDesc(key="expanded_only", field="a"),))
    row = _row(hass, entry, "switch", "expanded_only")

    async_reconcile_entities(
        hass, entry, _coordinator({"/x/vs/0": {"a": 1}}, {"/x/vs/0": [capability]})
    )

    assert _alive(hass, row)


async def test_an_unbound_key_is_never_judged(hass):
    """A resource missing from this discovery is not a platform move."""
    entry = _entry(hass)
    row = _row(hass, entry, "sensor", "rapid_fridge")

    assert async_reconcile_entities(hass, entry, _coordinator({}, {})) == []
    assert _alive(hass, row)


# --- one key, several platforms ----------------------------------------------


def _pair(*, replaces: bool, dynamic: bool = False, split: bool = False):
    """`lamp` is bound as both a switch and a binary sensor elsewhere, so the
    platform-move rule leaves both alone and only the sibling rule acts. With
    `split`, each descriptor sits on its own href."""
    switch = SwitchDesc(
        key="lamp", field="a", exists_fn=lambda rep, resources: False, dynamic_presence=dynamic
    )
    sensor = BinarySensorDesc(
        key="lamp",
        field="a",
        exists_fn=lambda rep, resources: True,
        replaces_siblings=replaces,
    )
    if split:
        return {
            "/x/vs/0": [Capability(href="/x/vs/0", entities=(switch,))],
            "/x/0": [Capability(href="/x/0", entities=(sensor,))],
        }
    return {"/x/vs/0": [Capability(href="/x/vs/0", entities=(switch, sensor))]}


@pytest.mark.parametrize("replaces", [False, True])
async def test_only_an_opted_in_descriptor_replaces_its_siblings(hass, replaces):
    """Without the flag, an exclusion may be live state (a cooktop's hood
    lamp, an AC's AI energy levels), so the excluded row stays."""
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    sensor = _row(hass, entry, "binary_sensor", "lamp")

    async_reconcile_entities(
        hass, entry, _coordinator({"/x/vs/0": {"a": "On"}}, _pair(replaces=replaces))
    )

    assert _alive(hass, switch) is not replaces
    assert _alive(hass, sensor)


async def test_a_dynamic_sibling_is_never_replaced(hass):
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    capabilities = _pair(replaces=True, dynamic=True)

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": {"a": "On"}}, capabilities))

    assert _alive(hass, switch)


@pytest.mark.parametrize("rep", [{"href": "/x/vs/0"}, {}, {"b": 1}])
async def test_a_stub_empty_or_fieldless_rep_decides_nothing(hass, rep):
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": rep}, _pair(replaces=True)))

    assert _alive(hass, switch)


async def test_a_sibling_missing_from_discovery_keeps_its_row(hass):
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    capabilities = _pair(replaces=True, split=True)

    async_reconcile_entities(hass, entry, _coordinator({"/x/0": {"a": "On"}}, capabilities))

    assert _alive(hass, switch)


@pytest.mark.parametrize("sibling", [{"href": "/x/vs/0"}, {}])
async def test_a_sibling_on_a_stub_or_empty_rep_keeps_its_row(hass, sibling):
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    resources = {"/x/0": {"a": "On"}, "/x/vs/0": sibling}

    async_reconcile_entities(hass, entry, _coordinator(resources, _pair(replaces=True, split=True)))

    assert _alive(hass, switch)


@pytest.mark.parametrize(
    "setinfo,removed",
    [(None, None), ({}, None), ({"false": "x"}, None), ({"FIELD": "false"}, "switch")],
)
async def test_power_replaces_the_switch_only_on_the_models_own_flag(hass, setinfo, removed):
    """A discovery without setinfo makes the switch look writable; that must
    not remove the read-only sensor a laundry board really has."""
    entry = _entry(hass)
    resources: dict[str, dict] = {"/power/0": {"value": True}}
    if setinfo is not None:
        resources[common._SETINFO_HREF] = {
            common._POWER_ON_OFF_FIELD if k == "FIELD" else k: v for k, v in setinfo.items()
        }
    rows = {p: _row(hass, entry, p, "power_switch") for p in ("switch", "binary_sensor")}

    async_reconcile_entities(
        hass, entry, _coordinator(resources, {"/power/0": [common.POWER_GENERIC]})
    )

    assert {p for p, row in rows.items() if not _alive(hass, row)} == (
        {removed} if removed else set()
    )


async def test_a_prefixed_binding_counts_its_whole_keys_platforms(hass, monkeypatch):
    """A pattern binding's unique_id ends in its prefixed key, which another
    descriptor may bind on a different platform."""
    monkeypatch.setattr(
        cleanup, "KEY_PLATFORMS", {"open": frozenset({"sensor"}), "door_x_open": {"binary_sensor"}}
    )
    entry = _entry(hass)
    row = _row(hass, entry, "binary_sensor", "door_x_open")
    capability = Capability(href="/x/vs/0", entities=(SensorDesc(key="open", field="a"),))
    bound = BoundEntity(
        href="/x/vs/0",
        capability=capability,
        desc=capability.entities[0],
        key_override="door_x_open",
    )
    resources = {"/x/vs/0": {"a": 1}}
    coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            bound=[bound],
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            device_key=MOCK_SERIAL,
            device_type_name=None,
            subdevices=[],
        ),
    )

    async_reconcile_entities(hass, entry, coordinator)

    assert _alive(hass, row)


# --- supersedes --------------------------------------------------------------


async def test_supersedes_spares_a_key_that_is_still_bound_on_that_platform(hass):
    entry = _entry(hass)
    capabilities = {
        "/x/vs/0": [
            Capability(
                href="/x/vs/0",
                entities=(
                    SensorDesc(key="new_timer", field="a", supersedes=(("sensor", "old_timer"),)),
                    SensorDesc(key="old_timer", field="a"),
                ),
            )
        ]
    }
    old = _row(hass, entry, "sensor", "old_timer")

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": {"a": 1}}, capabilities))

    assert _alive(hass, old)


@pytest.mark.parametrize("rep", [{"href": "/x/vs/0"}, {}, {"b": 1}])
async def test_supersedes_waits_for_a_rep_that_decides(hass, rep):
    """An optimistic exists_fn includes a stub (#785's burners), which
    proves nothing about the model the old row came from."""
    entry = _entry(hass)
    capabilities = {
        "/x/vs/0": [
            Capability(
                href="/x/vs/0",
                entities=(
                    SensorDesc(
                        key="new_timer",
                        field="a",
                        exists_fn=lambda rep, resources: True,
                        supersedes=(("sensor", "old_timer"),),
                    ),
                ),
            )
        ]
    }
    old = _row(hass, entry, "sensor", "old_timer")

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": rep}, capabilities))
    assert _alive(hass, old)

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": {"a": 1}}, capabilities))
    assert not _alive(hass, old)


def _supersede(old_exists=None, old_dynamic=False, mode_gate=False):
    """`new_timer` replaces `old_timer` in the same capability, like #785's
    decoded burners and raw mask. With `mode_gate`, the replacement's
    exists_fn reads /mode/vs/0 and is optimistic on a stub, as #785's is."""

    def new_exists(rep, resources):
        if not mode_gate:
            return True
        mode = resources.get("/mode/vs/0") or {}
        return is_stub_rep(mode) or mode.get("fuel") == "Gas"

    return {
        "/x/vs/0": [
            Capability(
                href="/x/vs/0",
                entities=(
                    SensorDesc(
                        key="new_timer",
                        field="a",
                        exists_fn=new_exists,
                        supersedes=(("sensor", "old_timer"),),
                    ),
                    SensorDesc(
                        key="old_timer",
                        field="a",
                        exists_fn=old_exists,
                        dynamic_presence=old_dynamic,
                    ),
                ),
            )
        ]
    }


@pytest.mark.parametrize(
    "old_exists,old_dynamic,removed",
    [
        (lambda rep, resources: True, False, False),
        (lambda rep, resources: False, False, True),
        (lambda rep, resources: False, True, False),
    ],
    ids=["old_included", "old_excluded", "old_dynamic"],
)
async def test_supersedes_acts_on_an_excluded_key_in_the_same_capability(
    hass, old_exists, old_dynamic, removed
):
    entry = _entry(hass)
    old = _row(hass, entry, "sensor", "old_timer")
    capabilities = _supersede(old_exists, old_dynamic)

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": {"a": 1}}, capabilities))

    assert _alive(hass, old) is not removed


async def test_supersedes_spares_an_old_key_excluded_on_a_stub(hass):
    entry = _entry(hass)
    old = _row(hass, entry, "sensor", "old_timer")
    resources = {"/x/vs/0": {"a": 1}, "/info/vs/0": {"href": "/info/vs/0"}}
    capabilities = _supersede(lambda rep, resources: _info_ok(resources))

    async_reconcile_entities(hass, entry, _coordinator(resources, capabilities))

    assert _alive(hass, old)


@pytest.mark.parametrize(
    "mode,removed",
    [({"href": "/mode/vs/0"}, False), ({}, False), (None, False), ({"fuel": "Gas"}, True)],
    ids=["stub", "empty", "absent", "gas"],
)
async def test_supersedes_needs_every_resource_its_exists_fn_read(hass, mode, removed):
    """#785: a stub /mode/vs/0 includes the decoded burners on any range."""
    entry = _entry(hass)
    old = _row(hass, entry, "sensor", "old_timer")
    resources: dict[str, dict] = {"/x/vs/0": {"a": 1}}
    if mode is not None:
        resources["/mode/vs/0"] = mode
    capabilities = _supersede(lambda rep, resources: False, mode_gate=True)

    async_reconcile_entities(hass, entry, _coordinator(resources, capabilities))

    assert _alive(hass, old) is not removed


def _probe(read):
    return {
        "/x/vs/0": [
            Capability(
                href="/x/vs/0",
                entities=(
                    SwitchDesc(
                        key="probe",
                        field="a",
                        exists_fn=lambda rep, resources: read(resources),
                        prune_when_excluded=True,
                    ),
                ),
            )
        ]
    }


def _info_ok(resources):
    return (resources.get("/info/vs/0") or {}).get("ok") == 1


@pytest.mark.parametrize(
    "info,removed",
    [({"href": "/info/vs/0"}, False), ({}, False), (None, True), ({"ok": 0}, True)],
    ids=["stub", "empty", "absent", "reported"],
)
async def test_prune_counts_an_absent_resource_but_not_a_stub(hass, info, removed):
    entry = _entry(hass)
    row = _row(hass, entry, "switch", "probe")
    resources: dict[str, dict] = {"/x/vs/0": {"a": 1}}
    if info is not None:
        resources["/info/vs/0"] = info

    async_reconcile_entities(hass, entry, _coordinator(resources, _probe(_info_ok)))

    assert _alive(hass, row) is not removed


async def test_prune_judges_its_own_empty_rep_as_before(hass):
    """The own rep is not cross-resource evidence: an {} still prunes, as
    the oven Sound switch's did, even when exists_fn reads it via resources."""
    entry = _entry(hass)
    row = _row(hass, entry, "switch", "probe")
    own = _probe(lambda resources: bool((resources.get("/x/vs/0") or {}).get("a")))

    async_reconcile_entities(hass, entry, _coordinator({"/x/vs/0": {}}, own))

    assert not _alive(hass, row)


async def test_an_exists_fn_that_scans_resources_reads_all_of_them(hass):
    entry = _entry(hass)
    row = _row(hass, entry, "switch", "probe")
    resources = {"/x/vs/0": {"a": 1}, "/other/vs/0": {"href": "/other/vs/0"}}
    scan = _probe(lambda resources: any(h.startswith("/t/") for h in resources))

    async_reconcile_entities(hass, entry, _coordinator(resources, scan))

    assert _alive(hass, row)


async def test_replaces_siblings_needs_every_resource_its_exists_fn_read(hass):
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    sensor = BinarySensorDesc(
        key="lamp",
        field="a",
        exists_fn=lambda rep, resources: _info_ok(resources) or True,
        replaces_siblings=True,
    )
    loser = SwitchDesc(key="lamp", field="a", exists_fn=lambda rep, resources: False)
    capabilities = {"/x/vs/0": [Capability(href="/x/vs/0", entities=(loser, sensor))]}
    resources = {"/x/vs/0": {"a": "On"}, "/info/vs/0": {"href": "/info/vs/0"}}

    async_reconcile_entities(hass, entry, _coordinator(resources, capabilities))
    assert _alive(hass, switch)

    resources["/info/vs/0"] = {"ok": 1}
    async_reconcile_entities(hass, entry, _coordinator(resources, capabilities))
    assert not _alive(hass, switch)


# --- the Repair --------------------------------------------------------------


def _issues(hass):
    return {
        issue_id: issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    }


async def test_a_removed_entity_an_automation_used_raises_a_repair(hass):
    entry = _entry(hass)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "dryness_alert",
                "alias": "Dryness alert",
                "triggers": [{"trigger": "state", "entity_id": "sensor.dryer_dry_level"}],
                "actions": [{"delay": 0}],
            }
        },
    )
    await hass.async_block_till_done()

    async_notify_removed(hass, entry, ["sensor.dryer_dry_level", "sensor.unused"])
    await hass.async_block_till_done()

    (issue,) = _issues(hass).values()
    assert issue.issue_id.startswith(f"{REMOVED_ISSUE}_{entry.entry_id}_")
    assert issue.is_persistent
    assert issue.translation_placeholders == {
        "device_name": "Fridge",
        "entities": (
            "- `sensor.dryer_dry_level`: [Dryness alert](/config/automation/edit/dryness_alert)"
        ),
    }

    async_delete_removed_issues(hass, entry)
    assert not _issues(hass)


async def test_no_repair_when_nothing_used_the_removed_entities(hass):
    entry = _entry(hass)

    async_notify_removed(hass, entry, ["sensor.dryer_dry_level"])
    await hass.async_block_till_done()

    assert not _issues(hass)


async def test_a_reload_before_startup_keeps_the_pending_check(hass):
    """The rows are gone after the first pass, so the reloaded entry's own
    pass removes nothing; the earlier removals must still be checked."""
    entry = _entry(hass)
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "dry_check": {
                    "sequence": [{"condition": "state", "entity_id": "sensor.old", "state": "x"}]
                }
            }
        },
    )
    await hass.async_block_till_done()
    hass.set_state(CoreState.starting)

    async_notify_removed(hass, entry, ["sensor.old"])
    await entry._async_process_on_unload(hass)
    async_notify_removed(hass, entry, [])
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    (issue,) = _issues(hass).values()
    assert issue.translation_placeholders is not None
    assert "/config/script/edit/dry_check" in issue.translation_placeholders["entities"]
