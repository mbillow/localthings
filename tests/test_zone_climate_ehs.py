"""Climate entities for EHS space-heating zones (issue #581)."""

from typing import ClassVar, cast

from homeassistant.components.climate import HVACMode

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import ehs
from custom_components.localthings.registry.capabilities import ehs as ehs_caps
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ZoneClimateDesc
from custom_components.localthings.zone_climate import LocalThingsZoneClimate
from tests.conftest import _load_device


class _FakeCoordinator:
    device_key = "TEST-EHS-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _two_zone_resources():
    """Issue #581's two-zone TP1X_DA_AC_EHS_01002_0000 dump."""
    return dict(_load_device("ehs_01002"))


def _bound(resources):
    return discover(resources, ehs.REGISTRY.capabilities, ehs.REGISTRY.pattern_capabilities)


def _entity(resources, key):
    coordinator = _FakeCoordinator(resources)
    bound = next(b for b in _bound(resources) if b.desc.key == key)
    entity = LocalThingsZoneClimate(cast(LocalThingsCoordinator, coordinator), bound)
    return entity, coordinator, bound


def _write(bound, payload):
    desc = cast(ZoneClimateDesc, bound.desc)
    assert desc.write_fn is not None
    return desc.write_fn(payload, {})


def test_zone1_reads_power_mode_and_temperatures():
    resources = dict(_load_device("ehs"))
    entity, _, _ = _entity(resources, "zone_climate")
    assert entity.hvac_mode == HVACMode.OFF  # the dump's zone power is Off
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.AUTO]
    assert entity.current_temperature == 30.0
    assert entity.target_temperature == 5.0
    assert (entity.min_temp, entity.max_temp, entity.target_temperature_step) == (5.0, 25.0, 0.5)

    resources["/power/vs/0"] = {"x.com.samsung.da.power": "On"}
    assert entity.hvac_mode == HVACMode.COOL


async def test_setting_a_mode_on_an_off_zone_powers_it_on_first():
    entity, coordinator, bound = _entity(dict(_load_device("ehs")), "zone_climate")
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    assert [payload for _, payload in coordinator.commands] == [("power", True), ("mode", "Heat")]
    assert _write(bound, ("power", True)) == (
        ["power", "vs", "0"],
        {"x.com.samsung.da.power": "On"},
    )
    assert _write(bound, ("mode", "Heat")) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.modes": ["Heat"]},
    )


async def test_off_turns_the_zone_off():
    entity, coordinator, _ = _entity(dict(_load_device("ehs")), "zone_climate")
    await entity.async_set_hvac_mode(HVACMode.OFF)
    assert [payload for _, payload in coordinator.commands] == [("power", False)]


def test_zone2_binds_with_no_unbound_hrefs():
    resources = _two_zone_resources()
    unbound = []
    bound = discover(
        resources, ehs.REGISTRY.capabilities, ehs.REGISTRY.pattern_capabilities, log=unbound.append
    )
    assert unbound == []
    state = flatten(bound, resources)
    assert state["zone2_temperature"] == 23.0
    assert state["zone2_climate"] == 18.0


def test_zone2_writes_its_own_power_and_setpoint_but_the_shared_mode():
    resources = _two_zone_resources()
    entity, _, bound = _entity(resources, "zone2_climate")
    assert entity.hvac_mode == HVACMode.OFF  # zone2 is off on the dump
    assert (entity.current_temperature, entity.target_temperature) == (23.0, 18.0)
    # zone2's temperature resource carries no unit field; Celsius is the fallback.
    assert entity.temperature_unit == "°C"
    resources["/power/zone2/vs/0"] = {"x.com.samsung.da.power": "On"}
    assert entity.hvac_mode == HVACMode.COOL
    assert _write(bound, ("power", False)) == (
        ["power", "zone2", "vs", "0"],
        {"x.com.samsung.da.power": "Off"},
    )
    assert _write(bound, ("temperature", 22)) == (
        ["temperatures", "zone2", "indoor", "vs", "0"],
        {"x.com.samsung.da.desired": "22.0"},
    )
    assert _write(bound, ("mode", "Auto")) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.modes": ["Auto"]},
    )


def test_zone1_controls_replaced_by_the_climate_entity_are_disabled_by_default():
    """Kept for existing installs, hidden from new ones."""
    descs = {
        d.key: d
        for cap in (ehs_caps.ZONE_POWER, ehs_caps.ZONE_MODE, ehs_caps.ZONE_TEMPERATURE)
        for d in cap.entities
    }
    for key in ("zone_power", "zone_mode", "zone_target_temperature"):
        assert descs[key].enabled_default is False, key
    for key in ("zone_temperature", "zone_climate"):
        assert descs[key].enabled_default is True, key
