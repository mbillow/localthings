"""How a board is read is decided at setup and stored in the entry
(CONF_READ_MODE): per href for a board without a /device/0 Collection.

The coordinator never switches to per-href reads on its own, so a batch
board answering 4.04 while it boots, or a legacy bridge's 404, stays a
failed poll. It switches back only on proof: a /device/0 answering 2.05 to
the one GET it makes each time it starts.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_READ_MODE,
    DOMAIN,
    READ_MODE_PER_HREF,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator

from .test_flat_polling import _AV_LINKS, _RESOURCES, FakeFlatSession, _identity

_PER_HREF = {CONF_READ_MODE: READ_MODE_PER_HREF}


def _coordinator(hass: HomeAssistant, **extra) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.0.2.5", CONF_PORT: 5684, **extra}, version=4
    )
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    coordinator._identity = _identity(_AV_LINKS)
    return coordinator


_COLLECTION = [{"rt": ["x.com.samsung.devcol"]}, {"href": "/sec/audio", "rep": {"volume": 3}}]


class _Batch(FakeFlatSession):
    """/device/0 answers 2.05 after all."""

    collection: ClassVar[list] = _COLLECTION

    def read(self, path, timeout=None):
        if list(path) == ["device", "0"]:
            self.reads.append("/device/0")
            return 0x45, self.collection
        return super().read(path, timeout)


class _EmptyCollection(_Batch):
    collection: ClassVar[list] = [{"rt": ["x.com.samsung.devcol"]}]


class _Silent(FakeFlatSession):
    """/device/0 gets no answer; everything else does."""

    def read(self, path, timeout=None):
        if list(path) == ["device", "0"]:
            self.reads.append("/device/0")
            raise TimeoutError("no answer")
        return super().read(path, timeout)


@pytest.mark.parametrize(("extra", "flat"), [({}, False), (_PER_HREF, True)])
async def test_the_entry_decides_the_read_mode(hass: HomeAssistant, extra, flat) -> None:
    assert _coordinator(hass, **extra)._flat is flat


async def test_an_entry_without_the_mode_never_goes_per_href(hass: HomeAssistant) -> None:
    """A 4.04 from a board still booting is a failed poll, whatever its family."""
    coordinator = _coordinator(hass)
    sess = FakeFlatSession(links=_AV_LINKS)
    coordinator._session = cast(Any, sess)

    with pytest.raises(RuntimeError, match="0x84"):
        coordinator._poll_once()
    assert coordinator._flat is False
    assert sess.reads == ["/device/0"]


@pytest.mark.parametrize("session", [FakeFlatSession, _Silent, _EmptyCollection])
async def test_a_per_href_entry_stays_so_without_proof(hass: HomeAssistant, session) -> None:
    """A 4.04, no answer or an empty Collection keeps it per href, and
    /device/0 is asked once."""
    coordinator = _coordinator(hass, **_PER_HREF)
    sess = session(links=_AV_LINKS)
    coordinator._session = cast(Any, sess)

    assert coordinator._poll_once() == _RESOURCES
    coordinator._mark_device_answered()
    coordinator._poll_once()

    assert coordinator._flat is True
    assert sess.reads.count("/device/0") == 1
    assert coordinator._entry.data[CONF_READ_MODE] == READ_MODE_PER_HREF


async def test_a_2_05_moves_the_entry_back_to_the_collection(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, **_PER_HREF)
    sess = _Batch(links=_AV_LINKS)
    coordinator._session = cast(Any, sess)

    assert coordinator._poll_once() == {"/sec/audio": {"volume": 3}}
    assert coordinator._flat is False
    # Saved only once a poll has got through, as a moved port is.
    assert CONF_READ_MODE in coordinator._entry.data
    coordinator._mark_device_answered()
    assert CONF_READ_MODE not in coordinator._entry.data
    # Polled through the Collection from then on, with no per-href reads.
    coordinator._poll_once()
    assert set(sess.reads) == {"/device/0"}


async def test_a_board_off_at_startup_is_still_read_per_href(hass: HomeAssistant) -> None:
    """The entry decides before the first answer: an off board fails its
    cycles as a per-href board does, its last state not carried forward."""
    coordinator = _coordinator(hass, **_PER_HREF)
    coordinator._discovered = True
    assert coordinator._defer_reconnect_for(TimeoutError("handshake timed out")) is False
