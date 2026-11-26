"""The coverage repair follows a sibling that materializes after first
discovery (#911).

An 8888 dual wall oven answered its sibling cavities' hrefs at startup, but
the cavities joined only on a later read. First discovery reported every
sibling href unbound, and growth never replaced that list, so the repair
stayed open with every entity working.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.localthings.const import DOMAIN
from tests.conftest import _discover_full, _load_device_full

DEVICE_TYPES = ("oic.wk.d", "oic.d.oven")


async def test_a_sibling_that_joins_later_clears_its_unbound_hrefs(
    hass: HomeAssistant, mock_entry
) -> None:
    from custom_components.localthings.coordinator import LocalThingsCoordinator

    resources, oic_res, seeds = _load_device_full("oven_nv7000bs_dual_cook")
    _bound, (lower,), _skipped, full, _name = _discover_full(
        resources, oic_res, seeds, DEVICE_TYPES
    )
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._run_discovery(full)
    coordinator._judge_registration(full)
    issue_id = f"device_gap_{mock_entry.entry_id}"
    first_pass = list(coordinator._unbound_hrefs)
    assert first_pass and all(lower.owns(href) for href in first_pass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    assert await coordinator._grow(full, [lower], bank=full, unit=lower)

    assert coordinator._unbound_hrefs == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_growth_leaves_a_skipped_slot_out_of_the_repair(
    hass: HomeAssistant, mock_entry
) -> None:
    """An unused slot first discovery skipped stays out of the unbound list
    when a later read grows the run without it."""
    from custom_components.localthings.coordinator import LocalThingsCoordinator
    from custom_components.localthings.registry.subdevices import enumerate_subdevices
    from tests.conftest import FakeCoapSession

    resources, oic_res, seeds = _load_device_full("airconditioner_artik051_dongle_fac_18k")
    candidates, extra = enumerate_subdevices(FakeCoapSession(seeds), resources, oic_res)
    full = {**resources, **extra}
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator.subdevices = list(candidates)
    coordinator._run_discovery(full)
    coordinator._judge_registration(full)
    assert coordinator._skipped_subdevices and coordinator._unbound_hrefs == []

    await coordinator._grow(full, list(coordinator.subdevices), bank=full)

    assert coordinator._unbound_hrefs == []
    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
