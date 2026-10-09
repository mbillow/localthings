"""AI Air Combo ventilator AJ020FERPBC2 (issue #551, TP1X_DA-AC-RHS-01001).
It self-reports oic.d.airconditioner, so it gets the AC registry and its
climate entity, whose modes are its own five ventilation ones."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ClimateDesc
from tests.conftest import _load_device
from tests.test_subdevice_discovery import _coordinator

NAME = "airconditioner_tp1x_rhs"
DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _registry(resources):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    return reg


def _bound(resources):
    reg = _registry(resources)
    return discover(resources, reg.capabilities, reg.pattern_capabilities)


def test_routes_by_oic_type_and_binds_everything():
    resources = _load_device(NAME)
    reg = _registry(resources)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert reg.name == "airconditioner"
    assert unbound == []


def test_ventilation_mode_select_offers_the_devices_own_modes():
    resources = _load_device(NAME)
    select = next(b for b in _bound(resources) if b.desc.key == "ventilation_mode")
    assert select.desc.options_field == "x.com.samsung.da.supportedModes"
    assert flatten(_bound(resources), resources)["ventilation_mode"] == "AIComfort"


@pytest.mark.parametrize(
    ("key", "href", "field"),
    [
        ("away_mode", "/option/outgoing/vs/0", "x.com.samsung.da.away"),
        ("interlock", "/interlock/vs/0", "x.com.samsung.da.interlock"),
    ],
)
def test_switches_read_and_write_their_own_flag(key, href, field):
    resources = _load_device(NAME)
    assert flatten(_bound(resources), resources)[key] is False
    desc = next(b.desc for b in _bound(resources) if b.desc.key == key)
    assert desc.write_fn("On", resources[href]) == (href.strip("/").split("/"), {field: "On"})


@pytest.fixture
def climate(hass: HomeAssistant, request) -> LocalThingsClimate:
    resources = _load_device(NAME)
    overrides = getattr(request, "param", {})
    for href, fields in overrides.items():
        resources[href] = {**resources[href], **fields}
    coordinator = _coordinator(hass)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    coordinator.bound = _bound(resources)
    coordinator.async_send_command = AsyncMock()
    bound = next(b for b in coordinator.bound if isinstance(b.desc, ClimateDesc))
    return LocalThingsClimate(coordinator, bound)


def test_every_mode_maps_to_an_hvac_mode(climate):
    """No plain Auto here, so AUTO stands for AIComfort."""
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.FAN_ONLY, HVACMode.DRY, HVACMode.AUTO]


@pytest.mark.parametrize(
    "climate", [{"/power/vs/0": {"x.com.samsung.da.power": "On"}}], indirect=True
)
def test_the_dumps_ai_comfort_mode_reads_as_an_offered_mode(climate):
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate.hvac_mode in climate.hvac_modes


async def test_auto_writes_ai_comfort(climate):
    await climate.async_set_hvac_mode(HVACMode.AUTO)

    sent = [call.args[1] for call in climate.coordinator.async_send_command.call_args_list]
    assert ("mode", "AIComfort") in sent


@pytest.mark.parametrize(
    "climate",
    [
        {
            "/power/vs/0": {"x.com.samsung.da.power": "On"},
            "/mode/vs/0": {"x.com.samsung.da.modes": ["FreshAirIntake"]},
        }
    ],
    indirect=True,
)
def test_a_ventilation_mode_reads_as_fan_only(climate):
    assert climate.hvac_mode == HVACMode.FAN_ONLY


async def test_dry_writes_the_devices_dehumidification_mode(climate):
    await climate.async_set_hvac_mode(HVACMode.DRY)

    sent = [call.args[1] for call in climate.coordinator.async_send_command.call_args_list]
    assert ("mode", "IndoorDehumidification") in sent
