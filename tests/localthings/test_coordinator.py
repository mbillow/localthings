"""Tests for the LocalThingsCoordinator."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.localthings.const import (
    DOMAIN, SUMMARY_INTERVAL_S,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator

from .conftest import ENTRY_DATA, MOCK_SERIAL


async def test_first_refresh_runs_discovery(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """After first refresh, coordinator.bound is populated."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.bound
    assert coordinator.data


async def test_device_info_populated(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """device_info is set from discovered resources after first refresh."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.device_info is not None
    assert any(
        DOMAIN in str(i)
        for i in coordinator.device_info.get('identifiers', set())
    )


async def test_summary_interval(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """Summary poll interval is always SUMMARY_INTERVAL_S."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.update_interval == timedelta(seconds=SUMMARY_INTERVAL_S)


async def test_update_failed_on_persistent_poll_error(
    hass: HomeAssistant, mock_entry
) -> None:
    """ConfigEntryNotReady raised when poll fails even after reconnect."""
    from homeassistant.exceptions import ConfigEntryNotReady

    with (
        patch('custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session'),
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            side_effect=RuntimeError('connection lost'),
        ),
        patch('custom_components.localthings.coordinator.LocalThingsCoordinator._close_session'),
    ):
        result = await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    # Setup fails — entry should not be in hass.data
    assert not result or mock_entry.entry_id not in hass.data.get(DOMAIN, {})
