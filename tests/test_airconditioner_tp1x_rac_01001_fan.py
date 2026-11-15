"""TP1X_DA-AC-RAC-01001_0000 fan-strength codes (model AR07C9150HZN, issue
#155).

Its /wind/strength/vs/0 reports supportedModes "0"/"31"/"32"/"33"/"34"/"35"
instead of the "0"-"4" scale climate.py's _DEVICE_TO_FAN was built from
(every other AC fixture in this repo uses "0"-"4", some with a 6th "5" --
see airconditioner_window_ac_device.json). Only "0" matched _DEVICE_TO_FAN,
so fan_modes silently dropped every speed but Auto. The fix reads the
device's own modesName labels (parallel-indexed with supportedModes) for
any code _DEVICE_TO_FAN doesn't already cover, instead of hardcoding a
second numeric scale.
"""

from typing import ClassVar, cast

from custom_components.localthings.climate import (
    _DEVICE_TO_FAN,
    LocalThingsClimate,
    _wind_strength_label,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry import by_type
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ClimateDesc
from tests.conftest import _load_device

FIXTURE = "airconditioner_tp1x_rac_01001"


class _FakeCoordinator:
    device_key = "TEST-RAC-01001-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def capability_canonical(self, subdevice):
        return self.canonical_resources(subdevice)

    def canonical_resources(self, subdevice):
        # Every bound entity in this test uses the default MAIN
        # subdevice, so the canonical view is just the raw snapshot
        # (issue #177 -- see LocalThingsEntity._resources).
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))

    def learned_modes(self, href):
        return []


def _climate(resources, coordinator=None):
    info = resources["/information/vs/0"]
    reg = by_type.for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    climate_bound = next(item for item in bound if isinstance(item.desc, ClimateDesc))
    return LocalThingsClimate(
        cast(LocalThingsCoordinator, coordinator or _FakeCoordinator(resources)), climate_bound
    )


def test_wind_strength_label_reads_the_devices_own_modes_name():
    rep = {
        "x.com.samsung.da.supportedModes": ["0", "31", "32", "33", "34", "35"],
        "x.com.samsung.da.modesName": ["Auto", "1", "2", "3", "4", "MAX"],
    }
    assert _wind_strength_label("32", rep) == "2"
    assert _wind_strength_label("35", rep) == "max"


def test_wind_strength_label_falls_back_to_raw_code_when_names_absent():
    assert _wind_strength_label("32", {}) == "32"


def test_fan_modes_include_every_supported_speed_not_just_auto():
    """Before the fix, only '0' matched _DEVICE_TO_FAN and fan_modes was
    ['auto'] -- exactly the reported symptom."""
    resources = _load_device(FIXTURE)
    entity = _climate(resources)
    assert entity.fan_modes == ["auto", "1", "2", "3", "4", "max"]


def test_fan_mode_reads_the_current_dynamic_code():
    """Fixture's /wind/strength/vs/0 modes is '32' -> modesName '2'."""
    resources = _load_device(FIXTURE)
    entity = _climate(resources)
    assert entity.fan_mode == "2"


def test_standard_scale_codes_still_use_device_to_fan():
    """A code _DEVICE_TO_FAN already covers keeps its existing friendly
    label rather than falling through to the device's own (blunter) one --
    no regression for boards using the standard "0"-"4" scale."""
    resources = _load_device(FIXTURE)
    resources["/wind/strength/vs/0"]["x.com.samsung.da.modes"] = "0"
    entity = _climate(resources)
    assert entity.fan_mode == _DEVICE_TO_FAN["0"] == "auto"


async def test_set_fan_mode_resolves_a_dynamic_label_back_to_its_code():
    resources = _load_device(FIXTURE)
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    await entity.async_set_fan_mode("max")

    assert coordinator.commands[-1][1] == ("fan", "35")


async def test_set_fan_mode_still_resolves_standard_scale_labels():
    resources = _load_device(FIXTURE)
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    await entity.async_set_fan_mode("auto")

    assert coordinator.commands[-1][1] == ("fan", "0")


async def test_set_fan_mode_does_not_misroute_when_static_map_and_live_codes_collide():
    """A board can use non-standard codes ('31'-'33') while modesName still
    spells a standard-looking label ('Low'/'High') that _FAN_TO_DEVICE's
    static reverse map also happens to have an entry for ('1'/'3') -- but
    that entry is for a *different* code this unit never advertises at all.
    Resolving the static hit without checking it against this unit's own
    supportedModes would silently write a code the device doesn't have.
    """
    resources = _load_device(FIXTURE)
    resources["/wind/strength/vs/0"] = {
        "x.com.samsung.da.modes": "0",
        "x.com.samsung.da.supportedModes": ["0", "31", "32", "33"],
        "x.com.samsung.da.modesName": ["Auto", "Low", "High", "Turbo"],
    }
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    assert entity.fan_modes == ["auto", "low", "high", "turbo"]

    await entity.async_set_fan_mode("high")
    assert coordinator.commands[-1][1] == ("fan", "32")

    await entity.async_set_fan_mode("low")
    assert coordinator.commands[-1][1] == ("fan", "31")

    await entity.async_set_fan_mode("turbo")
    assert coordinator.commands[-1][1] == ("fan", "33")
