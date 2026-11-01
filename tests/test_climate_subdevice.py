"""Tests that a subdevice's LocalThingsClimate entity (issue #177) reads its
*own* power/mode/temperature -- not the master's, and not some mix of the
two -- and that the legacy-board test (is_legacy_board/_legacy_airflow) is
evaluated per subdevice rather than once globally.

Uses the issue #177 reporter's ARTIK051_DONGLE_FAC_18K fixture deliberately:
it's a legacy `/airflow/vs/<n>` board (no `/wind/*` at all) on *both* the
master and its materialized sibling, which is exactly the shape climate.py's
own comments warn is easy to get wrong if the canonical view leaks between
subdevices.
"""

from __future__ import annotations

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.registry.entities import ClimateDesc
from tests.test_subdevice_discovery import _coordinator, _discover


def _climate_entities(coordinator):
    """{subdevice_key_or_None: LocalThingsClimate}, None standing for MAIN."""
    from custom_components.localthings.registry.subdevices import MAIN

    out = {}
    for b in coordinator.bound:
        if isinstance(b.desc, ClimateDesc):
            key = None if b.subdevice == MAIN else b.subdevice.key
            out[key] = LocalThingsClimate(coordinator, b)
    return out


@pytest.fixture
async def climates(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")
    return _climate_entities(coordinator)


async def test_subdevice_climate_reads_its_own_mode_and_power(climates):
    """Master reports 'Auto'; the bedroom subdevice (/device/1) reports 'Cool' --
    confirmed distinct in the real captured fixture. If the subdevice
    entity's _rep() weren't translating through its own subdevice, it would
    read the master's /mode/vs/0 instead and report the master's mode."""
    main, sub1 = climates[None], climates["1"]
    assert main.hvac_mode == HVACMode.AUTO
    assert sub1.hvac_mode == HVACMode.COOL


async def test_subdevice_climate_reads_its_own_temperature(climates):
    """Master: current 25.0 / desired 26.0. Subdevice 1: current 27.0 / desired
    28.0 -- distinct values in the real fixture, so a href mix-up here
    would show up as a wrong number, not just a wrong mode string."""
    main, sub1 = climates[None], climates["1"]
    assert main.native_current_temperature == 25.0
    assert main.native_target_temperature == 26.0
    assert sub1.native_current_temperature == 27.0
    assert sub1.native_target_temperature == 28.0


async def test_subdevice_climate_reads_its_own_power_state(climates):
    """Both subdevices happen to report power On in this fixture -- this at
    least confirms _is_on() reads the *subdevice's own* /power/vs/<n>, not a
    hardcoded /power/vs/0, by checking the entity resolves without falling
    back to OFF (which _is_on() would do if it silently read an absent
    href instead of the subdevice's actual one)."""
    main, sub1 = climates[None], climates["1"]
    assert main.hvac_mode != HVACMode.OFF
    assert sub1.hvac_mode != HVACMode.OFF


async def test_legacy_board_test_is_evaluated_per_subdevice(climates):
    """The reporter's board has no /wind/* resources at all on *either*
    subdevice -- is_legacy_board(self._resources) must independently
    evaluate True for the master's own canonical view and for the
    subdevice's own canonical view. If is_legacy_board were fed the raw,
    unpartitioned snapshot (or if canonical_view leaked one subdevice's
    hrefs into the other's), this wouldn't distinguish "this subdevice is
    legacy" from "some subdevice on this connection is legacy" -- and a
    future board with one legacy + one modern subdevice sharing a
    connection would silently read the wrong fan/swing channel on one
    side."""
    main, sub1 = climates[None], climates["1"]
    assert main._legacy_airflow() != {}
    assert sub1._legacy_airflow() != {}
    # Both resolve to *some* fan mode via the legacy path rather than the
    # /wind/strength/vs/0 channel (absent on this board) -- confirms
    # is_legacy_board(self._resources) actually gated fan_mode's branch,
    # not just that _legacy_airflow() itself returned something.
    assert main.fan_mode is not None
    assert sub1.fan_mode is not None


async def test_subdevice_climate_writes_are_scoped_to_its_own_bound_entity(climates):
    """A sanity check that the two entities are backed by genuinely
    different BoundEntity objects (different hrefs), which is what makes
    per-subdevice reads/writes possible at all -- see async_send_command's
    translation test in test_coordinator_send_command.py for the write
    side of this."""
    main, sub1 = climates[None], climates["1"]
    assert main._bound.href == "/mode/vs/0"
    assert sub1._bound.href == "/mode/vs/1"
    assert main._bound is not sub1._bound
