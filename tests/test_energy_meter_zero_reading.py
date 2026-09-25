"""A lifetime energy counter reading 0 is unknown, not a reset (issue #488).

The TP2X_RAC_20K reports cumulativePower "0" (with cumulativeDate snapped
to midnight UTC) for a minute or two after its connection drops, then its
real total again. Published as 0.0, Home Assistant's total_increasing
statistics book the drop as a meter reset and count the whole lifetime
total a second time on recovery.
"""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import airconditioner
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

ENERGY = "/energy/consumption/vs/0"

# Issue #488's working and failure dumps, verbatim.
WORKING = {
    "x.com.samsung.da.instantaneousPower": "0.000000",
    "x.com.samsung.da.cumulativePower": "345088",
    "x.com.samsung.da.cumulativeUnit": "Wh",
    "x.com.samsung.da.cumulativePowerType": "total",
    "x.com.samsung.da.instantaneousPowerUnit": "W",
    "x.com.samsung.da.cumulativeDate": "1790046000",
    "x.com.samsung.da.cumulativeDateUTC": "1790046000",
}
FAILURE = {
    **WORKING,
    "x.com.samsung.da.cumulativePower": "0",
    "x.com.samsung.da.cumulativeDate": "1790035200",
    "x.com.samsung.da.cumulativeDateUTC": "1790035200",
}


def _energy(energy_rep):
    resources = {**_load_device("airconditioner_tp2x_rac_20k"), ENERGY: energy_rep}
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)["energy_kwh"]


def test_the_working_reading_publishes_the_lifetime_total():
    assert _energy(WORKING) == 345.09


def test_the_transient_zero_publishes_unknown_not_a_reset():
    assert _energy(FAILURE) is None


@pytest.mark.parametrize(("raw", "expected"), [("0", None), ("117430000", 1174.3)])
def test_the_legacy_centiwatt_hour_scale_treats_zero_the_same(raw, expected):
    desc = next(e for e in airconditioner.ENERGY_METER_LEGACY.entities if e.key == "energy_kwh")
    assert desc.value_fn is not None
    assert desc.value_fn(raw) == expected


def test_other_totals_still_publish_a_real_zero():
    """Only the lifetime counter is guarded: cumulativeSavedPower sits at a
    genuine 0 on boards that never saved anything."""
    saved = next(
        e for e in airconditioner.ENERGY_METER_GENERIC.entities if e.key == "energy_saved_kwh"
    )
    assert saved.value_fn is not None
    assert saved.value_fn("0") == 0.0
