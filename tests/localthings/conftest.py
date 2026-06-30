"""Shared fixtures for localthings component tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Point HA's config dir at the repo root so the loader mounts custom_components/
# from the project into sys.path — otherwise IntegrationNotFound is raised.
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


@pytest.fixture
def hass_config_dir() -> str:
    """Override to let HA's loader find custom_components/localthings."""
    return PROJECT_ROOT


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Automatically enable custom integrations for all localthings tests.

    pytest-homeassistant-custom-component caches an empty custom-component
    dict during HA startup. This autouse fixture calls the upstream
    enable_custom_integrations fixture which pops that cache entry, forcing
    a re-discovery that finds custom_components/localthings.
    """
    return enable_custom_integrations


from custom_components.localthings.const import (
    CONF_CERT_PEM, CONF_HOST, CONF_KEY_PEM, CONF_PORT, DOMAIN,
)

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'

MOCK_HOST = '10.0.0.254'
MOCK_PORT = 49154
MOCK_SERIAL = 'TEST-SERIAL-001'
MOCK_CERT_PEM = '-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----'
MOCK_KEY_PEM = '-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----'

ENTRY_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_PORT: MOCK_PORT,
    CONF_CERT_PEM: MOCK_CERT_PEM,
    CONF_KEY_PEM: MOCK_KEY_PEM,
}


def _load_fridge_resources() -> dict:
    from samsung_appliance.batch import parse_device0_batch
    data = json.loads((FIXTURES / 'refrigerator_device.json').read_text())
    return parse_device0_batch(data['device0'])


@pytest.fixture
def fridge_resources():
    return _load_fridge_resources()


@pytest.fixture
def mock_probe():
    """Patch _probe_and_validate to succeed without a real DTLS connection."""
    with patch(
        'custom_components.localthings.config_flow._probe_and_validate',
        return_value={'port': MOCK_PORT, 'serial': MOCK_SERIAL},
    ) as m:
        yield m


@pytest.fixture
def mock_coordinator_session(fridge_resources):
    """Patch coordinator's blocking session methods so no real DTLS is needed."""
    with (
        patch('custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session'),
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            return_value=fridge_resources,
        ),
        patch('custom_components.localthings.coordinator.LocalThingsCoordinator._close_session'),
    ):
        yield


@pytest.fixture
def mock_entry(hass):
    """A MockConfigEntry added to hass, ready for async_setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f'localthings_{MOCK_SERIAL}',
    )
    entry.add_to_hass(hass)
    return entry
