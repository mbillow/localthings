"""Tests for LocalThingsCoordinator.async_send_command's optimistic-apply
step, specifically for x.com.samsung.da.options[] writes (issue #54).

The write itself only needs to carry the single changed token -- confirmed
on real hardware, the device merges by prefix and evicts the stale token
itself. But observe.ObserveManager.apply() does a shallow {**cached, **rep}
field merge, so handing it that same minimal single-token body would
overwrite the *whole* cached options[] field, wiping every sibling option
(other courses/levels/toggles packed into the same array) until the next
real poll lands. async_send_command must pre-merge the token into the
cached array itself before applying it optimistically, while still POSTing
only the minimal body over the wire.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import laundry
from custom_components.localthings.registry.capabilities.airconditioner import _climate_write
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import ClimateDesc
from custom_components.localthings.registry.subdevices import Subdevice
from custom_components.localthings.transport import Transport

ENTRY_DATA = {
    CONF_HOST: "10.0.0.199",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----",
    CONF_LEAF_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----",
}


class _FakeSendSession:
    def __init__(self):
        self.post_calls: list[tuple[list[str], dict]] = []

    def write(self, path_segs, body, timeout=None):
        self.post_calls.append((list(path_segs), body))
        return 0x44

    def pace(self):
        pass


@pytest.fixture
def coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id="localthings_SENDCMD-TEST",
    )
    entry.add_to_hass(hass)
    coord = LocalThingsCoordinator(hass, entry)
    coord.async_request_refresh = AsyncMock()
    coord._session = cast(Transport, _FakeSendSession())
    return coord


async def test_options_write_posts_only_the_changed_token(coordinator) -> None:
    href = "/course/vs/0"
    coordinator._observe.apply(
        href,
        {
            "x.com.samsung.da.options": ["DeviceType_0167", "Course_16", "GMT_04"],
        },
        source="poll",
    )

    desc = laundry.cycle_select(translation_key="dryer_cycle", icon="x")
    bound = BoundEntity(href=href, capability=Capability(), desc=desc)

    await coordinator.async_send_command(bound, "1D")

    posted_path, posted_body = coordinator._session.post_calls[0]
    assert posted_path == ["course", "vs", "0"]
    assert posted_body == {"x.com.samsung.da.options": ["Course_1D"]}


async def test_options_write_optimistic_cache_keeps_sibling_tokens(coordinator) -> None:
    """The regression this guards: applying the minimal wire body straight
    to the cache would wipe DeviceType_0167/GMT_04 until the next poll."""
    href = "/course/vs/0"
    coordinator._observe.apply(
        href,
        {
            "x.com.samsung.da.options": ["DeviceType_0167", "Course_16", "GMT_04"],
        },
        source="poll",
    )

    desc = laundry.cycle_select(translation_key="dryer_cycle", icon="x")
    bound = BoundEntity(href=href, capability=Capability(), desc=desc)

    await coordinator.async_send_command(bound, "1D")

    cached = coordinator._cache.get(href)
    assert cached["x.com.samsung.da.options"] == [
        "DeviceType_0167",
        "Course_1D",
        "GMT_04",
    ]


# ---------------------------------------------------------------------------
# Subdevice write translation (issue #177): a composite-entity write_fn (here
# airconditioner._climate_write) returns *canonical* path_segs
# (['power', 'vs', '0']) -- async_send_command must translate that through
# bound_entity.subdevice.to_actual before POSTing, applying the optimistic
# value, and starting the settle guard, or a subdevice's climate card would
# write to (and read confirmation from) the master's resource instead of
# its own.
# ---------------------------------------------------------------------------


def _climate_bound(href: str, subdevice: Subdevice) -> BoundEntity:
    desc = ClimateDesc(key="climate", translation_key="airconditioner", write_fn=_climate_write)
    return BoundEntity(href=href, capability=Capability(), desc=desc, subdevice=subdevice)


async def test_indexed_subdevice_write_posts_to_translated_path(coordinator) -> None:
    """A power write from the bedroom subdevice's (indexed '1') climate entity
    must POST to /power/vs/1, not the master's /power/vs/0."""
    sub1 = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    bound = _climate_bound("/mode/vs/1", sub1)

    await coordinator.async_send_command(bound, ("power", True))

    posted_path, posted_body = coordinator._session.post_calls[0]
    assert posted_path == ["power", "vs", "1"]
    assert posted_body == {"x.com.samsung.da.power": "On"}


async def test_indexed_subdevice_write_applies_optimistic_value_to_translated_href(
    coordinator,
) -> None:
    sub1 = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    bound = _climate_bound("/mode/vs/1", sub1)

    await coordinator.async_send_command(bound, ("power", True))

    # The optimistic apply + settle guard must land on /power/vs/1 -- the
    # translated href a real device confirms this write against -- not on
    # /mode/vs/1 (bound_entity.href) or /power/vs/0 (the master's resource).
    assert coordinator._cache.get("/power/vs/1") == {"x.com.samsung.da.power": "On"}
    assert coordinator._cache.get("/power/vs/0") is None
    # The settle guard is armed on that same translated href: a stale poll
    # reporting the pre-write value must be dropped, not allowed to revert
    # the optimistic 'On' (issue #27's regression, translated to a
    # subdevice's own resource).
    applied = coordinator._observe.apply(
        "/power/vs/1",
        {"x.com.samsung.da.power": "Off"},
        source="poll",
    )
    assert applied is False
    assert coordinator._cache.get("/power/vs/1") == {"x.com.samsung.da.power": "On"}


async def test_prefixed_subdevice_write_posts_to_translated_path(coordinator) -> None:
    """A power write from a UUID-prefixed subdevice's climate entity must POST
    to /<uuid>/power/vs/0, not the bare canonical href."""
    sub_id = "6c2dff6d-ee5c-dad1-6a5e-000000000001"
    subdevice = Subdevice(kind="prefixed", key=sub_id, seed_path=(sub_id, "device", "0"))
    bound = _climate_bound(f"/{sub_id}/mode/vs/0", subdevice)

    await coordinator.async_send_command(bound, ("power", True))

    posted_path, posted_body = coordinator._session.post_calls[0]
    assert posted_path == [sub_id, "power", "vs", "0"]
    assert posted_body == {"x.com.samsung.da.power": "On"}
    assert coordinator._cache.get(f"/{sub_id}/power/vs/0") == {
        "x.com.samsung.da.power": "On",
    }


async def test_main_climate_write_unaffected_by_subdevice_translation(coordinator) -> None:
    """MAIN's to_actual is the identity transform -- a device with no
    subdevices must keep posting to the exact same path as before this
    translation step existed."""
    from custom_components.localthings.registry.subdevices import MAIN

    bound = _climate_bound("/mode/vs/0", MAIN)

    await coordinator.async_send_command(bound, ("power", True))

    posted_path, _ = coordinator._session.post_calls[0]
    assert posted_path == ["power", "vs", "0"]


async def test_indexed_subdevice_climate_write_quantizes_with_its_own_temperature_step(
    coordinator,
) -> None:
    """A composite AC's write_fn (airconditioner._climate_write) reads the
    temperature step off write_fn's own `resources` argument -- if
    async_send_command handed it the raw cache snapshot (real, on-the-wire
    hrefs) instead of this subdevice's canonical_resources() view, the
    subdevice's own /temperature/control/vs/1 would be invisible under the
    canonical HREF_TEMP_CONTROL key, and the write would quantize against
    the master's step (or none at all) instead of its own."""
    sub1 = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator._observe.apply(
        "/temperature/control/vs/0",
        {"x.com.samsung.da.increment": "1"},
        source="poll",
    )
    coordinator._observe.apply(
        "/temperature/control/vs/1",
        {"x.com.samsung.da.increment": "0.5"},
        source="poll",
    )
    bound = _climate_bound("/mode/vs/1", sub1)

    await coordinator.async_send_command(bound, ("temperature_ocf", 24.5))

    posted_path, posted_body = coordinator._session.post_calls[0]
    assert posted_path == ["temperature", "desired", "1"]
    assert posted_body == {"temperature": 24.5}
