"""A poll or write still in flight when the coordinator closes must not open
a new session: nothing would ever close it after unload or shutdown."""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import coordinator as coordinator_mod
from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator

ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "TEST-LEAF-CERT",
    CONF_LEAF_KEY_PEM: "TEST-LEAF-KEY",
}


class _FakeSession:
    def __init__(
        self,
        on_connect: Callable[[], None] | None = None,
        on_read: Callable[[], None] | None = None,
    ) -> None:
        self._on_connect = on_connect
        self._on_read = on_read
        self.closed = False

    def connect(self) -> None:
        if self._on_connect is not None:
            self._on_connect()

    def read(self, *_args, **_kwargs):
        if self._on_read is not None:
            self._on_read()
        raise ConnectionError("session closed")

    def close(self) -> None:
        self.closed = True


class _Transports:
    """Every session the coordinator opens, built by `make`."""

    def __init__(self) -> None:
        self.make: Callable[[], _FakeSession] = _FakeSession
        self.opened: list[_FakeSession] = []

    def create(self, *_args, **_kwargs) -> _FakeSession:
        sess = self.make()
        self.opened.append(sess)
        return sess


@pytest.fixture
def transports(monkeypatch: pytest.MonkeyPatch) -> _Transports:
    fake = _Transports()
    monkeypatch.setattr(coordinator_mod, "create_transport", fake.create)
    monkeypatch.setattr(coordinator_mod, "read_identity", lambda *_a: None)
    return fake


def _coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_CLOSE-TEST")
    entry.add_to_hass(hass)
    return LocalThingsCoordinator(hass, entry)


async def test_poll_failing_during_close_does_not_reconnect(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    transports: _Transports,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Close lands while the poll's read is in flight; the read fails because
    the session went away, and the reconnect path must not open another or
    report the device as failed."""
    coordinator = _coordinator(hass)
    monkeypatch.setattr(coordinator, "_RECONNECT_PAUSE_S", 0)

    def _close_mid_read() -> None:
        coordinator._closed = True

    transports.make = lambda: _FakeSession(on_read=_close_mid_read)

    await coordinator._async_update_data()

    assert len(transports.opened) == 1
    assert coordinator._session is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_handshake_finishing_after_close_closes_its_session(
    hass: HomeAssistant, transports: _Transports
) -> None:
    """async_close found no session to close because the handshake had not
    finished yet; the session that handshake produces must not survive."""
    coordinator = _coordinator(hass)

    def _close_mid_handshake() -> None:
        coordinator._closed = True

    transports.make = lambda: _FakeSession(on_connect=_close_mid_handshake)

    with pytest.raises(RuntimeError, match="closed"):
        coordinator._connect_session()

    assert transports.opened[0].closed
    assert coordinator._session is None
