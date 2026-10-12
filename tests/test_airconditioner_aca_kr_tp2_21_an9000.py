"""System Fresh Air Ventilator ACA-KR-TP2-21-AN9000 (issue #522), the
first real dump of the model PR #316 described. It self-reports
oic.d.airconditioner, so it gets the AC registry and its climate entity,
whose only modes are Purification/Ventilation/SmartVentilation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ClimateDesc
from tests.conftest import _load_device
from tests.test_subdevice_discovery import _coordinator

NAME = "airconditioner_aca_kr_tp2_21_an9000"
DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _registry(resources):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    return reg


def test_routes_by_oic_type_and_binds_everything():
    resources = _load_device(NAME)
    reg = _registry(resources)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert reg.name == "airconditioner"
    assert unbound == []


@pytest.fixture
def climate(hass: HomeAssistant, request) -> LocalThingsClimate:
    resources = _load_device(NAME)
    overrides = getattr(request, "param", {})
    for href, fields in overrides.items():
        resources[href] = {**resources[href], **fields}
    coordinator = _coordinator(hass)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    reg = _registry(resources)
    coordinator.bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    coordinator.async_send_command = AsyncMock()
    bound = next(b for b in coordinator.bound if isinstance(b.desc, ClimateDesc))
    return LocalThingsClimate(coordinator, bound)


def test_it_can_be_turned_on(climate):
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.FAN_ONLY]


def test_it_reads_off_while_powered_off(climate):
    assert climate.hvac_mode == HVACMode.OFF


@pytest.mark.parametrize(
    "climate", [{"/power/vs/0": {"x.com.samsung.da.power": "On"}}], indirect=True
)
def test_any_ventilation_mode_reads_as_fan_only(climate):
    assert climate.hvac_mode == HVACMode.FAN_ONLY


async def test_turning_on_keeps_the_chosen_ventilation_mode(climate):
    """The dump's mode is SmartVentilation; Purification is listed first."""
    await climate.async_set_hvac_mode(HVACMode.FAN_ONLY)

    sent = [call.args[1] for call in climate.coordinator.async_send_command.call_args_list]
    assert sent == [("power", True)]
