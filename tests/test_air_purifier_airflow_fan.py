"""HA fan-entity mapping tests for the ARTIK051_TVTL air purifier (issue #56)."""

from typing import ClassVar, cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.fan import LocalThingsAirflowFan
from custom_components.localthings.registry.by_type import air_purifier
from custom_components.localthings.registry.capabilities.air_purifier import HREF_AIRFLOW
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import FanDesc
from tests.conftest import _load_device


class _FakeCoordinator:
    device_key = "TEST-AIRFLOW-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        # Every bound entity in this test uses the default MAIN
        # subdevice, so the canonical view is just the raw snapshot
        # (issue #177 -- see LocalThingsEntity._resources).
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _entity(resources, coordinator=None):
    bound = discover(
        resources,
        air_purifier.REGISTRY.capabilities,
        air_purifier.REGISTRY.pattern_capabilities,
    )
    fan_bound = next(
        item for item in bound if isinstance(item.desc, FanDesc) and item.href == HREF_AIRFLOW
    )
    return LocalThingsAirflowFan(
        cast(LocalThingsCoordinator, coordinator or _FakeCoordinator(resources)),
        fan_bound,
    )


def test_power_off_maps_to_zero_percent_and_no_preset():
    entity = _entity(_load_device("air_purifier"))
    assert entity.is_on is False
    assert entity.speed_count == 3
    assert entity.preset_modes == ["auto", "sleep"]
    assert entity.percentage == 0
    assert entity.preset_mode is None


def test_codes_map_to_presets_and_ordered_speeds():
    """/airflow/0's speed is Auto=0, Sleep=1, Low=2, Medium=3, High=4
    (issue #56). Auto and Sleep are presets, the rest speeds (#658)."""
    resources = _load_device("air_purifier")
    resources["/power/0"]["value"] = True

    for code, preset, percentage in (
        (0, "auto", None),
        (1, "sleep", None),
        (2, None, 33),
        (3, None, 66),
        (4, None, 100),
    ):
        resources["/airflow/0"]["speed"] = code
        entity = _entity(resources)
        assert (entity.preset_mode, entity.percentage) == (preset, percentage), code


async def test_power_write_prefers_standard_resource_when_both_exist():
    """Both /power/0 and /power/vs/0 are present on this family's dumps --
    /power/0 must win, matching common.POWER_GENERIC's own preference (the
    power_switch entity is unconditionally bound to /power/0 whenever it's
    present, so writing here to /power/vs/0 first would leave the two
    entities disagreeing until the next poll)."""
    resources = _load_device("air_purifier")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()

    assert coordinator.commands[-1][1] == ("power", True, "/power/0")


async def test_power_write_falls_back_to_vendor_resource():
    resources = _load_device("air_purifier")
    resources.pop("/power/0")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_off()

    assert coordinator.commands[-1][1] == ("power", False, "/power/vs/0")


async def test_set_percentage_writes_raw_speed_code():
    resources = _load_device("air_purifier")
    resources["/power/0"]["value"] = True
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_percentage(60)

    assert coordinator.commands[-1][1] == ("speed", 3)


async def test_set_percentage_zero_turns_off():
    resources = _load_device("air_purifier")
    resources["/power/0"]["value"] = True
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_percentage(0)

    assert coordinator.commands[-1][1] == ("power", False, "/power/0")


async def test_set_preset_mode_writes_its_code():
    resources = _load_device("air_purifier")
    resources["/power/0"]["value"] = True
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_preset_mode("auto")

    assert [c[1] for c in coordinator.commands] == [("speed", 0)]


async def test_a_preset_from_off_powers_on_first():
    resources = _load_device("air_purifier")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on(preset_mode="sleep")

    assert [c[1] for c in coordinator.commands] == [("power", True, "/power/0"), ("speed", 1)]


async def test_an_unknown_preset_writes_nothing():
    resources = _load_device("air_purifier")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_preset_mode("turbo")

    assert coordinator.commands == []


async def test_a_bare_turn_on_leaves_an_active_preset():
    """HomeKit turns a preset switch off with turn_on and no arguments."""
    resources = _load_device("air_purifier")
    resources["/power/0"]["value"] = True
    resources["/airflow/0"]["speed"] = 0
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()

    assert [c[1] for c in coordinator.commands] == [("speed", 2)]


async def test_a_bare_turn_on_from_off_only_powers_on():
    resources = _load_device("air_purifier")
    resources["/airflow/0"]["speed"] = 0
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()

    assert [c[1] for c in coordinator.commands] == [("power", True, "/power/0")]
