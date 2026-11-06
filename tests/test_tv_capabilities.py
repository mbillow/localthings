"""Captured QN90A mode settings and synthetic edge cases (#388)."""

from copy import deepcopy
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import registry_for, resolve
from custom_components.localthings.registry.capabilities import tv
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.select import LocalThingsSelect
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.tv")
CAPABILITIES = (tv.INPUT, tv.PICTURE_MODE, tv.SOUND_MODE)


def _discovered(resources):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    return discover(resources, reg.capabilities, reg.pattern_capabilities)


def test_tv_routes_only_from_its_reported_type():
    resources = _load_device("tv_qn90a")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "tv"
    assert resolve(resources) is None
    assert registry_for("soundbar").name == "soundbar"
    assert reg.subscribe_tiers == ()


def test_selected_capture_is_bound_without_claiming_complete_tv_coverage():
    resources = _load_device("tv_qn90a")
    reg = registry_for("tv")
    unbound = []
    bound = discover(resources, reg.capabilities, log=unbound.append)
    assert unbound == []
    assert flatten(bound, resources) == {
        "tv_input": "HDMI1",
        "tv_picture_mode": "modeFilmmakerMode",
        "tv_sound_mode": "modeExternalAdaptivewPlus",
    }
    # Unimplemented useful resources must still appear in coverage repairs.
    discover({"/sec/tv/contentinfo": {}}, reg.capabilities, log=unbound.append)
    assert unbound


@pytest.mark.parametrize("cap", CAPABILITIES)
def test_every_advertised_mode_writes_the_raw_id(cap):
    resources = _load_device("tv_qn90a")
    rep = resources[cap.href]
    desc = cap.entities[0]
    write: Any = desc.write_fn
    for code in tv.mode_options(rep):
        assert write(code, rep, cap.href) == (
            cap.href.strip("/").split("/"),
            {tv.CURRENT_MODE: code},
        )
    assert desc.needs_remote_control is False
    assert desc.confirm_on_refusal is True


@pytest.mark.parametrize("value", ["HDMI2", "PC", "modeUnknown", None, [], {}])
def test_unadvertised_ids_and_labels_are_not_written(value):
    rep = _load_device("tv_qn90a")[tv.HREF_INPUT]
    write: Any = cast(SelectDesc, tv.INPUT.entities[0]).write_fn
    assert write(value, rep, tv.INPUT.href) is None


@pytest.mark.parametrize("rows", [None, {}, "invalid", [None, {}, {tv.PREFIX + "id": []}]])
def test_malformed_option_lists_produce_no_entity(rows):
    rep = {"if": ["oic.if.a"], tv.SUPPORTED_MODES: rows}
    assert tv.mode_options(rep) == {}
    assert flatten(_discovered({tv.INPUT.href: rep}), {tv.INPUT.href: rep}) == {}


def test_read_only_modes_are_not_exposed_as_controls():
    rep = _load_device("tv_qn90a")[tv.HREF_INPUT]
    rep["if"] = ["oic.if.r", "oic.if.baseline"]
    resources = {tv.INPUT.href: rep}
    assert flatten(_discovered(resources), resources) == {}
    write: Any = cast(SelectDesc, tv.INPUT.entities[0]).write_fn
    assert write("HDMI1", rep, tv.INPUT.href) is None


def test_display_label_collision_falls_back_to_unique_wire_ids():
    rep = _load_device("tv_qn90a")[tv.HREF_INPUT]
    rep[tv.SUPPORTED_MODES].append({tv.PREFIX + "id": "HDMI5", tv.PREFIX + "name": "PC (HDMI1)"})
    options = tv.mode_options(rep)
    assert options["HDMI1"] != options["HDMI5"]
    assert len(set(options.values())) == len(options)


async def test_select_disambiguates_duplicate_names_and_refreshes_its_options():
    resources = _load_device("tv_qn90a")
    bound = next(b for b in _discovered(resources) if b.desc.key == "tv_input")
    coordinator = MagicMock()
    coordinator.last_resources = resources
    coordinator.canonical_resources.return_value = resources
    coordinator.discovery_canonical.return_value = resources
    coordinator.entity_rep.side_effect = lambda href: resources.get(href, {})
    coordinator.data = flatten(_discovered(resources), resources)
    coordinator.async_send_command = AsyncMock()
    entity = LocalThingsSelect(cast(LocalThingsCoordinator, coordinator), bound)
    assert entity.current_option == "PC (HDMI1)"
    assert entity.options == ["TV", "PC (HDMI1)", "HDMI 3", "PC (HDMI4)"]
    await entity.async_select_option("PC (HDMI4)")
    coordinator.async_send_command.assert_awaited_once_with(bound, "HDMI4")

    # The list is live, not a hardcoded QN90A vocabulary.
    rep = deepcopy(resources[tv.HREF_INPUT])
    rep[tv.SUPPORTED_MODES] = [{tv.PREFIX + "id": "HDMI2", tv.PREFIX + "name": "Console"}]
    resources[tv.HREF_INPUT] = rep
    assert entity.options == ["Console"]
    await entity.async_select_option("Console")
    coordinator.async_send_command.assert_awaited_with(bound, "HDMI2")
