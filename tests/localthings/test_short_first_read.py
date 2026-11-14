"""A short first read catches up once the full set answers (#807).

For every fixture, the first live read after a start misses one resource,
and a later cycle reads everything. By then the run must provide what a run
whose first read was full provides, polled in the same tiers, and the
snapshot must still register all of it on the next start.

At least that, not exactly that: growth is never undone in a run
(registration.py), so what the short read bound differently -- the twin of
a missing href, a common-capability entity bound before the type resolved
-- stays until the next start.
"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import (
    _TYPE_RELOADS,
    LocalThingsCoordinator,
    bound_id,
)
from custom_components.localthings.registry.encode import json_safe
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FIXTURES, _discover_full, _load_device_full

_NAMES = sorted(path.name[: -len("_device.json")] for path in FIXTURES.glob("*_device.json"))


def _store_key(entry) -> str:
    return f"{DOMAIN}.{entry.entry_id}.discovery"


def _bank(hass_storage, entry, resources, subdevices, oic_res) -> None:
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": _store_key(entry),
        "data": {
            "resources": json_safe(resources),
            "subdevice_candidates": [asdict(s) for s in subdevices],
            "identity": asdict(
                DeviceIdentity(
                    manufacturer="Samsung Electronics",
                    model="",
                    name="",
                    serial=None,
                    device_types=(),
                    raw={"/oic/res": oic_res},
                )
            ),
        },
    }


async def _run(hass, entry, reads, subdevices) -> LocalThingsCoordinator | None:
    """Start on the snapshot, then poll `reads` one cycle each. None when
    the first read resolved another device type, which reloads instead."""
    hass.data.pop(_TYPE_RELOADS, None)
    coordinator = LocalThingsCoordinator(hass, entry)
    assert await coordinator.async_rehydrate()

    def _enumerate(resources):
        coordinator.subdevices = list(subdevices)
        return resources

    with (
        patch.object(coordinator, "_poll_once", side_effect=reads),
        patch.object(coordinator, "_enumerate_subdevices_blocking", side_effect=_enumerate),
        patch.object(coordinator, "_attempt_observe_mode"),
        patch.object(coordinator, "_maybe_retry_observe_mode"),
        patch.object(coordinator, "_run_subpolls"),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        await coordinator._async_update_data()
        if reload.called:
            return None
        for _ in reads[1:]:
            await coordinator._async_update_data()
        await hass.async_block_till_done(wait_background_tasks=True)
    return coordinator


def _outcome(coordinator: LocalThingsCoordinator) -> dict:
    return {
        "provided": {bound_id(b) for b in coordinator.bound if coordinator.provides(b)},
        "hot": set(coordinator._hot_hrefs),
        "warm": set(coordinator._warm_hrefs),
    }


@pytest.mark.parametrize("name", _NAMES)
async def test_a_short_first_read_catches_up(
    hass: HomeAssistant, mock_entry, hass_storage, name: str
) -> None:
    main, oic_res, seeds = _load_device_full(name)
    bound, subdevices, _, full, _ = _discover_full(main, oic_res, seeds)
    if not bound:
        pytest.skip("binds only with its /oic/d device type; nothing to register")

    _bank(hass_storage, mock_entry, full, subdevices, oic_res)
    reference = await _run(hass, mock_entry, [full, full], subdevices)
    assert reference is not None
    expected = _outcome(reference)
    banked = set(full)

    for href in sorted(full):
        _bank(hass_storage, mock_entry, full, subdevices, oic_res)
        short = {h: rep for h, rep in full.items() if h != href}
        coordinator = await _run(hass, mock_entry, [short, full], subdevices)
        if coordinator is None:
            # A range missing its oven resource reads as a cooktop: once per
            # run, the entry reloads onto it (coordinator._reload_for_type).
            continue
        got = _outcome(coordinator)
        missed = {key: expected[key] - got[key] for key in got if expected[key] - got[key]}
        assert not missed, href
        assert banked <= set(hass_storage[_store_key(mock_entry)]["data"]["resources"]), href
