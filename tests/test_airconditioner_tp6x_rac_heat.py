"""TP6X_RAC heat pumps offer Heat although supportedModes omits it (issue #589).

The 8888 bridge lists Cool/Dry/Wind/Auto on every TP6X_RAC dump on record,
yet each rates a heating capacity (`WarmCapa_<n>`), and the #589 unit reports
`Heat` while heating from its own remote. Cool-only units report WarmCapa_0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.legacy_http import TP6X_RAC, to_resources, to_write
from custom_components.localthings.registry.capabilities.airconditioner import (
    _climate_write,
    has_heating_capacity,
)
from custom_components.localthings.registry.entities import ClimateDesc
from tests.test_subdevice_discovery import _coordinator, _discover, _discover_with

_FIXTURES = Path(__file__).parent / "fixtures"


async def _climate(coordinator) -> LocalThingsClimate:
    bound = next(b for b in coordinator.bound if isinstance(b.desc, ClimateDesc))
    return LocalThingsClimate(coordinator, bound)


async def _tp6x_climate(hass: HomeAssistant, name: str) -> LocalThingsClimate:
    bodies = json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["bodies"]
    coordinator = _coordinator(hass)
    await _discover_with(coordinator, to_resources(bodies, TP6X_RAC), [], {})
    return await _climate(coordinator)


@pytest.mark.parametrize(
    "name",
    [
        "airconditioner_tp6x_rac_16k_8888",
        "airconditioner_tp6x_rac_17k_8888",
        "airconditioner_tp6x_rac_16k_heat_8888",
    ],
)
async def test_tp6x_heat_pump_offers_heat(hass: HomeAssistant, name):
    climate = await _tp6x_climate(hass, name)

    assert HVACMode.HEAT in climate.hvac_modes
    assert climate._device_code_for_hvac(HVACMode.HEAT) == "Heat"


async def test_tp6x_reports_heat_while_heating(hass: HomeAssistant):
    climate = await _tp6x_climate(hass, "airconditioner_tp6x_rac_16k_heat_8888")

    assert climate.hvac_mode == HVACMode.HEAT


async def test_heat_reaches_the_mode_wrapper(hass: HomeAssistant):
    climate = await _tp6x_climate(hass, "airconditioner_tp6x_rac_16k_8888")
    rep = climate._rep("/mode/vs/0")

    segs, body = _climate_write(("mode", "Heat"), rep, resources={})

    wire = to_write([("/" + "/".join(segs), body)], TP6X_RAC)["Device"]
    assert wire == {"Mode": {"modes": ["Heat"]}}


async def test_cool_only_unit_offers_no_heat(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_tp1x_rac_coolonly")
    climate = await _climate(coordinator)

    assert HVACMode.HEAT not in climate.hvac_modes


def test_heating_capacity_reads_the_warmcapa_token():
    def rep(*options):
        return {"x.com.samsung.da.options": list(options)}

    assert has_heating_capacity(rep("CoolCapa_35", "WarmCapa_38"))
    assert not has_heating_capacity(rep("CoolCapa_35", "WarmCapa_0"))
    assert not has_heating_capacity(rep("CoolCapa_35"))
    assert not has_heating_capacity(rep("WarmCapa_x"))
