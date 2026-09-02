"""Tests for the range's write-only local clock synchronization (issue #404)."""

from datetime import datetime
from typing import cast
from unittest.mock import patch

from custom_components.localthings import button as button_platform
from custom_components.localthings.button import LocalThingsButton
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type.oven import REGISTRY as OVEN_REGISTRY
from custom_components.localthings.registry.by_type.range import REGISTRY
from custom_components.localthings.registry.capabilities import range as range_caps
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ButtonDesc


class _FakeCoordinator:
    device_key = "TEST-RANGE"

    def __init__(self):
        self.last_resources = {"/configuration/vs/0": {}}
        self.writes = []

    def canonical_resources(self, _subdevice):
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.writes.append((bound, payload))


def test_clock_sync_formats_supplied_local_time():
    fixed = datetime.fromisoformat("2026-08-31T12:52:46-07:00")
    desc = range_caps.RANGE_CLOCK_SYNC.entities[0]
    assert isinstance(desc, ButtonDesc)
    assert desc.payload_fn is not None
    assert desc.write_fn is not None

    result = desc.write_fn(desc.payload_fn(fixed), {})

    assert result == (
        ["configuration", "vs", "0"],
        {"x.com.samsung.da.currentTime": "2026-08-31T12:52:46"},
    )


async def test_clock_sync_button_uses_ha_local_time_at_press():
    fixed = datetime.fromisoformat("2026-08-31T12:52:46-07:00")
    bound = discover({"/configuration/vs/0": {}}, REGISTRY.capabilities)[0]
    coordinator = _FakeCoordinator()
    entity = LocalThingsButton(cast(LocalThingsCoordinator, coordinator), bound)

    with patch.object(button_platform.dt_util, "now", return_value=fixed):
        await entity.async_press()

    assert coordinator.writes == [(bound, "2026-08-31T12:52:46")]


def test_range_registry_replaces_ignored_configuration_with_sync_button():
    bound = discover({"/configuration/vs/0": {}}, REGISTRY.capabilities)

    assert [item.desc.key for item in bound] == ["sync_clock"]


def test_range_without_configuration_has_no_sync_button():
    bound = discover({"/cooktopmonitoring/vs/0": {}}, REGISTRY.capabilities)

    assert all(item.desc.key != "sync_clock" for item in bound)


def test_wall_oven_configuration_remains_ignored_without_write_evidence():
    bound = discover({"/configuration/vs/0": {}}, OVEN_REGISTRY.capabilities)

    assert bound == []
