"""localthings.search: the TV plugin's searchOnWeb.

Measured on the UE50TU7172: site youtube with a query opens YouTube on the
results.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.devices import find_entry_device
from tests.localthings.test_soundbar import _mock_entry, _setup_soundbar
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv

_SEARCH = ["sec", "tv", "searchonweb"]


def _device_id(hass: HomeAssistant) -> str:
    coordinator = next(iter(hass.data[DOMAIN].values()))
    device = find_entry_device(
        hass, coordinator._entry.entry_id, {(DOMAIN, coordinator.device_key)}
    )
    assert device is not None
    return device.id


@pytest.mark.parametrize(
    ("data", "body"),
    [
        (
            {"query": "big buck bunny"},
            {
                "x.com.samsung.tv.searchSite": "youtube",
                "x.com.samsung.tv.searchQuery": "big buck bunny",
            },
        ),
        (
            {"query": "Paris", "site": "wikipedia"},
            {
                "x.com.samsung.tv.searchSite": "wikipedia",
                "x.com.samsung.tv.searchQuery": "Paris",
                "x.com.samsung.tv.searchUrl": "https://en.wikipedia.org/w/index.php?search=",
            },
        ),
    ],
)
async def test_search_opens_on_the_tv(hass: HomeAssistant, data, body) -> None:
    session = await _setup_tv(hass, _entry(hass))

    await hass.services.async_call(
        DOMAIN, "search", {"device_id": _device_id(hass), **data}, blocking=True
    )

    assert (_SEARCH, body) in session.writes


async def test_a_soundbar_cannot_search(hass: HomeAssistant) -> None:
    session = await _setup_soundbar(hass, _mock_entry(hass))

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "search", {"device_id": _device_id(hass), "query": "x"}, blocking=True
        )
    assert err.value.translation_key == "search_unsupported"
    assert not any(path == _SEARCH for path, _body in session.writes)
