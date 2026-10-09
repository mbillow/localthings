"""AR18CYLZBGEN on the TP1X_DA-AC-RAC-01001 board (issue #569): a cool-only
RAC with an OpenADR resource and no energy meter."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import airconditioner, common
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

NAME = "airconditioner_tp1x_rac_01001_oadr"
DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _discover(resources, log=None):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "airconditioner"
    return discover(resources, reg.capabilities, reg.pattern_capabilities, log=log)


def test_binds_everything():
    unbound = []
    _discover(_load_device(NAME), log=unbound.append)
    assert unbound == []


def test_meterless_energy_reading_is_unknown():
    resources = _load_device(NAME)
    assert resources["/energy/consumption/vs/0"]["x.com.samsung.da.cumulativePower"] == "-1"
    assert flatten(_discover(resources), resources)["energy_kwh"] is None


@pytest.mark.parametrize("raw", ["-1", "0", None, "junk"])
def test_lifetime_energy_without_a_positive_reading_is_unknown(raw):
    assert common.lifetime_wh_to_kwh(raw) is None
    assert airconditioner._legacy_cumulative_power_kwh(raw) is None


def test_positive_lifetime_energy_still_converts():
    assert common.lifetime_wh_to_kwh("345088") == 345.09
    assert airconditioner._legacy_cumulative_power_kwh("117430000") == 1174.3
