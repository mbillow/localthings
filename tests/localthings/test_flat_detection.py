"""Which boards a pre-discovery 4.04 on /device/0 may switch to flat polling.

Only a board its registry marks flat (the soundbar's oic.d.networkaudio)
polls href by href. A batch board answering 4.04 while it boots, or a
legacy bridge's 404, stays a failed poll -- even one whose /oic/res lists
no /device/0, as many do.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_LEGACY_HTTP,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FIXTURES


def _coordinator(hass: HomeAssistant, device_types, **extra) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.0.2.5", CONF_PORT: 5684, **extra}, version=4
    )
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    if device_types is not None:
        coordinator._identity = DeviceIdentity(
            manufacturer="Samsung", model="", name="", serial=None, device_types=device_types
        )
    return coordinator


@pytest.mark.parametrize(
    ("device_types", "extra", "flat"),
    [
        (("oic.wk.d", "oic.d.networkaudio"), {}, True),
        (("oic.wk.d", "oic.d.microwave"), {}, False),
        ((), {}, False),
        (None, {}, False),
        (("oic.d.networkaudio",), {CONF_TRANSPORT: TRANSPORT_LEGACY_HTTP}, False),
    ],
)
async def test_only_an_av_board_goes_flat(hass: HomeAssistant, device_types, extra, flat) -> None:
    assert _coordinator(hass, device_types, **extra)._is_flat_board() is flat


def test_a_microwave_listing_no_device0_is_not_an_av_board() -> None:
    """The NW9300MD's /oic/res lists no /device/0; it is still a batch board."""
    from custom_components.localthings.registry.by_type import is_flat_board

    dump = json.loads((FIXTURES / "microwave_nw9300md_device.json").read_text())
    assert not any(link.get("href") == "/device/0" for link in dump["oic_res"])
    assert not is_flat_board(("oic.wk.d", "oic.d.microwave"))
