"""Shared fixtures for localthings component tests."""

from __future__ import annotations

import json
from inspect import signature
from pathlib import Path
from typing import Any
from unittest.mock import patch

import cbor2
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_DEVICE_KEY,
    CONF_DEVICE_TYPE,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_PORT,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator

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


@pytest.fixture(autouse=True)
def _fast_coordinator_timers():
    """Shrink the coordinator's real-time delays for every localthings test.

    `_run_subpolls` and `_attempt_observe_mode` are, by design, blocking/
    real-time operations in production (a real sub-poll cadence and a real
    CoAP OBSERVE grace period). Tests that drive them through a full
    `hass.config_entries.async_setup(...)` + `hass.async_block_till_done()`
    would otherwise burn tens of real seconds per test waiting them out.

    This patches only the *class* attributes coordinator.py exposes for
    this purpose (`_SUBPOLL_STEP_S`, `_OBSERVE_GRACE_PERIOD_S`) — the
    production defaults they're computed from (`SUMMARY_INTERVAL_S`,
    `GRACE_PERIOD_S`) are untouched. Tests that need to exercise the real
    grace-period race (e.g. test_observe.py's own `ObserveManager` tests)
    still pass `grace_period_s=` explicitly and are unaffected.
    """
    with (
        patch.object(LocalThingsCoordinator, "_SUBPOLL_STEP_S", 0.001),
        patch.object(LocalThingsCoordinator, "_OBSERVE_GRACE_PERIOD_S", 0.02),
        patch.object(LocalThingsCoordinator, "_RECONNECT_PAUSE_S", 0.01),
    ):
        yield


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

MOCK_HOST = "10.0.0.254"
MOCK_PORT = 49154
# Matches the identity in tests/fixtures/refrigerator_device.json, which is
# what mock_coordinator_session polls -- so an entry built from ENTRY_DATA and
# the device it "reaches" agree on who they are, the same as in production.
MOCK_SERIAL = "TEST-SERIAL-0000"
# The OCF device UUID (/oic/d's `di`) the probe resolves the entry's key from
# (issue #381). Distinct from MOCK_SERIAL so a test that confuses the two
# fails rather than passing by coincidence.
MOCK_DEVICE_KEY = "7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31"
MOCK_MODEL = "TEST-MODEL"
MOCK_DEVICE_TYPE = "refrigerator"
MOCK_CA_CERT_PEM = "-----BEGIN CERTIFICATE-----\nTEST-CA\n-----END CERTIFICATE-----"
MOCK_CA_KEY_PEM = "-----BEGIN PRIVATE KEY-----\nTEST-CA-KEY\n-----END PRIVATE KEY-----"
MOCK_LEAF_CERT_PEM = "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----"
MOCK_LEAF_KEY_PEM = "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----"

ENTRY_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_PORT: MOCK_PORT,
    CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
    CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM: MOCK_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM: MOCK_LEAF_KEY_PEM,
    # Identity the config flow's probe resolved (issue #236) -- what the
    # coordinator keys its devices and entities on from construction.
    CONF_DEVICE_KEY: MOCK_DEVICE_KEY,
    CONF_SERIAL: MOCK_SERIAL,
    CONF_MODEL: MOCK_MODEL,
    CONF_MANUFACTURER: "Samsung",
    CONF_DEVICE_TYPE: MOCK_DEVICE_TYPE,
}

# A pre-identity entry, as a real install upgrading through the v1 -> v2
# migration still has it on disk.
LEGACY_ENTRY_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_PORT: MOCK_PORT,
    CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
    CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM: MOCK_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM: MOCK_LEAF_KEY_PEM,
}


def entry_has_identifier(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: tuple[str, str]
) -> bool:
    """True if any device row belonging to `entry` carries `identifier`.

    Not `dev_reg.async_get_device`, which HA 2026.9 deprecates (removed in
    2027.8) because identifiers are no longer unique across config entries --
    and one entry's own rows are what these assertions mean anyway.
    """
    return any(
        identifier in row.identifiers
        for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    )


def link_to_parent(parent: dr.DeviceEntry) -> dict[str, Any]:
    """`async_get_or_create` kwargs linking a new row under `parent`.

    HA 2026.9 replaced `via_device` (an identifier) with `via_device_id` (a
    device id) and errors on the old one; cores back to this integration's
    2025.1 floor accept only `via_device`. Both store the same link.
    """
    if "via_device_id" in signature(dr.DeviceRegistry.async_get_or_create).parameters:
        return {"via_device_id": parent.id}
    return {"via_device": next(iter(parent.identifiers))}


def _load_fridge_resources() -> dict:
    from custom_components.localthings.registry.batch import parse_device0_batch

    data = json.loads((FIXTURES / "refrigerator_device.json").read_text())
    return parse_device0_batch(data["device0"])


@pytest.fixture
def fridge_resources():
    return _load_fridge_resources()


def _probe_result(*, recognized: bool) -> dict:
    return {
        "port": MOCK_PORT,
        "device_key": MOCK_DEVICE_KEY,
        "serial": MOCK_SERIAL,
        "model": MOCK_MODEL,
        "manufacturer": "Samsung",
        "device_type_name": MOCK_DEVICE_TYPE if recognized else None,
        "device_type_recognized": recognized,
        "leaf_cert_pem": MOCK_LEAF_CERT_PEM,
        "leaf_key_pem": MOCK_LEAF_KEY_PEM,
    }


@pytest.fixture
def mock_probe():
    """Patch _probe_and_validate to succeed (recognized type) without a real DTLS connection."""
    with patch(
        "custom_components.localthings.config_flow._probe_and_validate",
        return_value=_probe_result(recognized=True),
    ) as m:
        yield m


@pytest.fixture
def mock_probe_unknown_type():
    """Patch _probe_and_validate to succeed, but with an unrecognized device type."""
    with patch(
        "custom_components.localthings.config_flow._probe_and_validate",
        return_value=_probe_result(recognized=False),
    ) as m:
        yield m


@pytest.fixture
def mock_coordinator_session(fridge_resources):
    """Patch coordinator's blocking session methods so no real DTLS is needed."""
    with (
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session"),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        yield


class FakeObserveSession:
    """Stand-in for DtlsCoapSession that supports subscribe()/on_notification
    for coordinator-level observe tests, without a real DTLS connection."""

    def __init__(self, on_notification=None):
        self.on_notification = on_notification
        self.subscribed: list[str] = []
        self.fail_hrefs: set[str] = set()
        self.closed = False
        # When set to a rep dict, subscribe() immediately delivers that rep
        # as an OBSERVE notification for the href — what a real device does
        # when it answers a subscription with the current representation.
        # `try_enter_observe_mode` clears its notified set before it
        # subscribes, so this is the only way to deliver a notify that
        # reliably counts: it lands synchronously via on_notification,
        # after that clear and before the post-sleep fraction check — a
        # notify raced in from another thread can be wiped by the clear
        # (or arrive after the check) depending on scheduling. Set to
        # None to model a device that answers subscriptions but never
        # notifies.
        self.notify_on_subscribe: dict[str, Any] | None = None

    def subscribe(self, path_segs):
        href = "/" + "/".join(path_segs)
        if href in self.fail_hrefs:
            raise ConnectionError("subscribe failed")
        self.subscribed.append(href)
        if self.notify_on_subscribe is not None and self.on_notification is not None:
            self.on_notification(href, cbor2.dumps(self.notify_on_subscribe))
        return b"\x01"

    def refresh_observes(self, paths):
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def mock_coordinator_observe_session(fridge_resources):
    """Like mock_coordinator_session, but _connect_session installs a
    FakeObserveSession on coordinator._session so subscribe()/on_notification
    wiring can be exercised."""
    fake = FakeObserveSession()

    def _connect(self):
        fake.on_notification = self._observe.on_notification
        self._session = fake

    with (
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
            _connect,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        yield fake


@pytest.fixture
def mock_entry(hass):
    """A MockConfigEntry added to hass, ready for async_setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f"localthings_{MOCK_DEVICE_KEY}",
        version=5,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def legacy_entry(hass):
    """A v1 entry with no stored identity, as an upgrading install has it.

    Its device identity is only knowable from the first poll, which is the
    one case where _run_discovery still adopts what the device reports.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=LEGACY_ENTRY_DATA,
        unique_id=f"localthings_{MOCK_SERIAL}",
        version=1,
    )
    entry.add_to_hass(hass)
    return entry
