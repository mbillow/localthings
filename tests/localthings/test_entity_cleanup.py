"""cleanup.py: which registry rows setup removes, and which it must keep."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY, resolve
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import discover
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


async def test_an_unbound_key_is_never_judged(hass):
    """A resource missing from this discovery is not a platform move."""
    entry = _entry(hass)
    row = _row(hass, entry, "sensor", "rapid_fridge")

    assert async_reconcile_entities(hass, entry, _coordinator({}, {})) == []
    assert _alive(hass, row)


# --- one key, several platforms ----------------------------------------------

BOTH = {
    "/refrigeration/vs/0": [fridge.REFRIGERATION],
    "/refrigeration/0": [fridge.REFRIGERATION_FALLBACK],
}
SWITCH_REP = {"x.com.samsung.da.rapidFridge": "Off", "x.com.samsung.da.rapidFreezing": "Off"}
FALLBACK_REP = {"rapidCool": False, "rapidFreeze": False}


async def test_the_included_platform_replaces_its_siblings(hass):
    entry = _entry(hass)
    resources = {"/refrigeration/vs/0": SWITCH_REP, "/refrigeration/0": FALLBACK_REP}
    old = _row(hass, entry, "binary_sensor", "rapid_fridge")
    live = _row(hass, entry, "switch", "rapid_fridge")

    assert old in async_reconcile_entities(hass, entry, _coordinator(resources, BOTH))
    assert not _alive(hass, old)
    assert _alive(hass, live)


async def test_a_sibling_missing_from_discovery_keeps_its_row(hass):
    """The switch's resource is absent, so the binary sensor wins only by
    default; its row and the switch's must both survive."""
    entry = _entry(hass)
    rows = [_row(hass, entry, p, "rapid_fridge") for p in ("switch", "binary_sensor")]

    async_reconcile_entities(hass, entry, _coordinator({"/refrigeration/0": FALLBACK_REP}, BOTH))

    assert all(_alive(hass, row) for row in rows)


@pytest.mark.parametrize("winner", [{"href": "/refrigeration/vs/0"}, {}])
async def test_a_stub_or_empty_rep_decides_nothing(hass, winner):
    entry = _entry(hass)
    resources = {"/refrigeration/vs/0": winner, "/refrigeration/0": FALLBACK_REP}
    old = _row(hass, entry, "binary_sensor", "rapid_fridge")

    async_reconcile_entities(hass, entry, _coordinator(resources, BOTH))

    assert _alive(hass, old)


def _lamp(*, dynamic: bool) -> dict[str, list[Capability]]:
    """`lamp` is bound as both a switch and a binary sensor elsewhere, so
    the platform-move rule leaves both alone and only the sibling rule acts."""
    return {
        "/x/vs/0": [
            Capability(
                href="/x/vs/0",
                entities=(
                    SwitchDesc(
                        key="lamp",
                        field="a",
                        exists_fn=lambda rep, resources: False,
                        dynamic_presence=dynamic,
                    ),
                    BinarySensorDesc(key="lamp", field="a", exists_fn=lambda rep, resources: True),
                ),
            )
        ]
    }


@pytest.mark.parametrize("dynamic", [False, True])
async def test_a_dynamic_sibling_is_never_replaced(hass, dynamic):
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    coordinator = _coordinator({"/x/vs/0": {"a": "On"}}, _lamp(dynamic=dynamic))

    async_reconcile_entities(hass, entry, coordinator)

    assert _alive(hass, switch) is dynamic


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
