"""Tests for LocalThingsCoordinator.async_raw_write -- the debug-only
arbitrary-href write primitive backing the options-flow debug panel
(issue #54). It deliberately bypasses the remote-control block and every
write_fn/validate_fn, so this only exercises the primitive itself: it
POSTs exactly the caller's body, reads the href back, and validates its
inputs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator

ENTRY_DATA = {
    CONF_HOST: "10.0.0.199",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----",
    CONF_LEAF_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----",
}


class _FakeRawWriteSession:
    """Stand-in for a Transport: records every write verbatim and answers
    the follow-up read with a canned representation -- no real
    DTLS/network involved."""

    def __init__(self, post_code: int = 0x44, get_rep: dict | None = None):
        self.post_calls: list[tuple[list[str], dict]] = []
        self.get_calls: list[list[str]] = []
        self._post_code = post_code
        self._get_rep = {} if get_rep is None else get_rep

    def write(self, path_segs, body, timeout=None):
        self.post_calls.append((list(path_segs), body))
        return self._post_code

    def read(self, path_segs, timeout=None):
        self.get_calls.append(list(path_segs))
        return 0x45, self._get_rep

    def pace(self):
        pass


@pytest.fixture
def coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id="localthings_RAWWRITE-TEST",
    )
    entry.add_to_hass(hass)
    coord = LocalThingsCoordinator(hass, entry)
    # async_raw_write kicks a full refresh after the write; a real refresh
    # would try to poll a session that doesn't exist for this unit test, so
    # replace it with a no-op the same way the reference test setup does.
    coord.async_request_refresh = AsyncMock()
    return coord


async def test_raw_write_splits_href_and_posts_exact_body(coordinator) -> None:
    fake = _FakeRawWriteSession(post_code=0x44, get_rep={"x.field": "after"})
    coordinator._session = fake

    body = {"x.com.samsung.da.field": "value", "n": 1}
    code, new_rep = await coordinator.async_raw_write("/course/vs/0", body)

    assert len(fake.post_calls) == 1
    posted_path, posted_body = fake.post_calls[0]
    assert posted_path == ["course", "vs", "0"]
    assert posted_body == body

    assert code == 0x44
    assert new_rep == {"x.field": "after"}
    coordinator.async_request_refresh.assert_awaited_once()


async def test_raw_write_reads_href_back_for_ground_truth(coordinator) -> None:
    fake = _FakeRawWriteSession(post_code=0x45, get_rep={"value": "confirmed"})
    coordinator._session = fake

    code, new_rep = await coordinator.async_raw_write("/washer/vs/0", {"a": 1})

    assert fake.get_calls == [["washer", "vs", "0"]]
    assert code == 0x45
    assert new_rep == {"value": "confirmed"}


async def test_raw_write_returns_coap_code_from_post_not_get(coordinator) -> None:
    """The returned code reflects the write's own response, even though a
    follow-up GET (which could carry a different code in principle) runs
    right after it."""
    fake = _FakeRawWriteSession(post_code=0x80, get_rep={"value": 1})
    coordinator._session = fake

    code, _ = await coordinator.async_raw_write("/test/vs/0", {"a": 1})

    assert code == 0x80


async def test_raw_write_rejects_empty_dict(coordinator) -> None:
    with pytest.raises(ServiceValidationError):
        await coordinator.async_raw_write("/washer/vs/0", {})


async def test_raw_write_rejects_non_dict_payload(coordinator) -> None:
    with pytest.raises(ServiceValidationError):
        await coordinator.async_raw_write("/washer/vs/0", ["not", "a", "dict"])  # type: ignore[arg-type]


async def test_raw_write_rejects_empty_href(coordinator) -> None:
    with pytest.raises(ServiceValidationError):
        await coordinator.async_raw_write("", {"a": 1})


async def test_raw_write_rejects_root_href(coordinator) -> None:
    with pytest.raises(ServiceValidationError):
        await coordinator.async_raw_write("/", {"a": 1})


async def test_raw_write_validation_errors_do_not_touch_the_session(coordinator) -> None:
    """A rejected call must fail before ever reaching the network -- no
    session needs to be connected at all for validation to run."""
    assert coordinator._session is None

    with pytest.raises(ServiceValidationError):
        await coordinator.async_raw_write("/washer/vs/0", {})

    assert coordinator._session is None
    coordinator.async_request_refresh.assert_not_awaited()
