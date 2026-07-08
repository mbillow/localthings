"""Tests for the LocalThingsCoordinator."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

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


# ---------------------------------------------------------------------------
# Coverage-gap detection and the Repairs issue it raises
# ---------------------------------------------------------------------------

async def test_discovery_populates_device_type_with_no_unbound_hrefs(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """The real refrigerator fixture is a recognized type, fully covered.

    Every href in this fixture is either mapped to a capability or listed
    in capabilities.ignored — see tests/test_registry_ignored.py for the
    noise-suppression checks. If this starts failing because a genuinely
    new gap appeared, map it or ignore it rather than deleting the assertion.
    """
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.device_type_name == 'refrigerator'
    assert coordinator.one_ui_version == '7.0 Refrigerator'
    assert coordinator._unbound_hrefs == []


async def test_coverage_gap_issue_absent_for_fully_covered_real_fixture(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """A recognized device with no unbound hrefs does not raise the issue."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_update_coverage_gap_issue_creates_issue_for_unknown_type(
    hass: HomeAssistant, mock_entry
) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._update_coverage_gap_issue(True, [], 'Test Appliance')

    issue_id = f"device_gap_{mock_entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == 'device_gap'
    assert issue.translation_placeholders == {'device_name': 'Test Appliance'}
    assert issue.is_fixable is False


def test_update_coverage_gap_issue_creates_issue_for_unbound_hrefs(
    hass: HomeAssistant, mock_entry
) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._update_coverage_gap_issue(False, ['/mystery/vs/0'], 'Test Appliance')

    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


def test_update_coverage_gap_issue_absent_when_fully_covered(
    hass: HomeAssistant, mock_entry
) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._update_coverage_gap_issue(False, [], 'Test Appliance')

    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_update_coverage_gap_issue_clears_previous_issue(
    hass: HomeAssistant, mock_entry
) -> None:
    """A later discovery run with no gap deletes an issue raised earlier."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    issue_id = f"device_gap_{mock_entry.entry_id}"

    coordinator._update_coverage_gap_issue(True, [], 'Test Appliance')
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    coordinator._update_coverage_gap_issue(False, [], 'Test Appliance')
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
