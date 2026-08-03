"""Sanity check for diagnostics.py's issue #177 additions (subdevices,
subdevices_skipped, subdevice_probes) against a real composite-device
discovery run -- makes sure the new blocks are actually reachable/shaped
right, not just that the coordinator's own attributes look correct in
isolation (test_subdevice_discovery.py covers that)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.test_subdevice_discovery import _coordinator, _discover


async def test_diagnostics_reports_materialized_and_skipped_subdevices(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")
    hass.data.setdefault(DOMAIN, {})[coordinator._entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, coordinator._entry)

    subdevice_keys = {su["key"] for su in diag["subdevices"]}
    skipped_keys = {su["key"] for su in diag["subdevices_skipped"]}
    assert subdevice_keys == {"1"}
    assert skipped_keys == {"2"}
    assert diag["subdevices"][0]["bound_entity_count"] > 0
    assert diag["subdevices_skipped"][0]["hrefs"]
    assert "/multidevice/vs/0" in diag["subdevice_probes"]
    # The reporter's hand-read value, carried in the fixture's `probes` map
    # (it belongs to no batch -- see that fixture's seeds_note). Two real
    # subdevices, master + bedroom, independently corroborating the gate's
    # decision to skip /device/2. Reported on its own, never as a resource.
    assert diag["multidevice"] == {"x.com.samsung.da.numofsubdevice": "2"}
    assert "/multidevice/vs/0" not in diag["resources"]


async def test_top_level_resources_is_the_master_subdevice_only(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """`resources` reports this subdevice's own hrefs and nothing else.

    It used to be `last_resources` raw -- the union across every live subdevice,
    keyed by real hrefs -- so a sibling's /mode/vs/1 sat in it alongside the
    master's /mode/vs/0 with no attribution, contradicting both the module
    docstring and the adding-device-support skill ("the parsed /device/0
    snapshot"). Each sibling carries its own canonicalized block instead.
    """
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")
    hass.data.setdefault(DOMAIN, {})[coordinator._entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, coordinator._entry)

    assert not [h for h in diag["resources"] if h.endswith("/1")]
    assert "/mode/vs/0" in diag["resources"]
    # The sibling's own block carries its state, canonicalized -- '/mode/vs/0',
    # not the '/mode/vs/1' it actually answers on.
    sub1 = diag["subdevices"][0]
    assert "/mode/vs/0" in sub1["resources"]
    assert not [h for h in sub1["resources"] if h.endswith("/1")]
    assert sub1["resources"]["/power/vs/0"]["x.com.samsung.da.power"] == "On"


async def test_rejected_candidate_reps_reach_diagnostics_but_not_the_cache(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """A gate-rejected slot is never polled again, so anything applied to the
    state cache for it would sit frozen at its first-discovery value while
    looking as live as every other href. It's kept out of the cache entirely
    and reported only under its own subdevices_skipped entry -- which is also
    the only place a reader can go to second-guess the gate."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_artik051_dongle_fac_18k")
    hass.data.setdefault(DOMAIN, {})[coordinator._entry.entry_id] = coordinator

    assert not [h for h in coordinator.last_resources if h.endswith("/2")]

    diag = await async_get_config_entry_diagnostics(hass, coordinator._entry)
    skipped = diag["subdevices_skipped"][0]
    # Present, canonicalized, and visibly the empty state the gate rejected.
    assert skipped["resources"]["/power/vs/0"] == {}
    assert skipped["resources"]["/mode/vs/0"] == {}
    assert skipped["resources"]["/information/vs/0"]


async def test_diagnostics_reports_prefixed_subdevice(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora_2in1")
    hass.data.setdefault(DOMAIN, {})[coordinator._entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, coordinator._entry)

    assert len(diag["subdevices"]) == 1
    assert diag["subdevices"][0]["kind"] == "prefixed"
    # diagnostics reports the raw modelNum field verbatim (board revision/
    # capability-bitmap suffix included), unlike device_info_for's model
    # (split at '|') -- confirms it's still the wall subdevice's own identity,
    # not the master's TP2X_FAC_BORA_21K.
    assert diag["subdevices"][0]["model"].startswith("TP2X_FAC_BORA_RAC_21K")
    assert diag["subdevices_skipped"] == []


async def test_diagnostics_reports_flat_hrefs_for_materialized_prefixed_candidate(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """issue #205: a prefixed candidate found through the clone-the-master
    fallback (no working /<uuid>/device/0 Collection) has no meaningful
    seed_path -- diagnostics reports None there instead of the misleading
    bare "/" an empty tuple would otherwise join to, and lists the full set
    of hrefs the fallback assumed (issue #265 -- every href the master
    itself has, not just a confirmed few) instead."""
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora_205_flat")
    hass.data.setdefault(DOMAIN, {})[coordinator._entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, coordinator._entry)

    assert diag["subdevices_skipped"] == []
    assert len(diag["subdevices"]) == 1
    subdevice = diag["subdevices"][0]
    assert subdevice["kind"] == "prefixed"
    assert subdevice["seed_path"] is None
    assert subdevice["flat_hrefs"] == sorted(diag["resources"])
    assert subdevice["bound_entity_count"] > 0
