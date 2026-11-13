"""The TV's thumbnail blob as the media player's fallback entity picture."""

from __future__ import annotations

import base64

from homeassistant.core import HomeAssistant

from custom_components.localthings.const import DOMAIN
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
_HREF = "/sec/tv/contentinfo/thumbnail"


async def test_thumbnail_blob_backs_the_media_player_picture(hass: HomeAssistant) -> None:
    import asyncio

    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    async def _wait_cache(href: str, field: str, value) -> None:
        # Wait for the value to land: a sub-poll delivers seeded fake-device
        # reps on its own cadence, and an optimistic apply would race it.
        for _ in range(100):
            if coordinator.resource(href).get(field) == value:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{href} {field} never became {value!r}")

    # The fixture carries no art anywhere: no picture at all.
    assert player.entity_picture is None

    async def _sweep():
        # The blob href is cold-tier: the next refresh must BE the sweep
        # cycle, or the seed arrives only after N-1 liveness polls.
        coordinator._flat_poll = coordinator._FLAT_FULL_EVERY_N_POLLS - 1
        await coordinator.async_refresh()

    # Seed the fake device's rep; the cold sweep delivers it like a poll.
    session.resources[_HREF].update({"x.com.samsung.tv.thumbnail": PNG_B64})
    await _sweep()
    await _wait_cache(_HREF, "x.com.samsung.tv.thumbnail", PNG_B64)
    await hass.async_block_till_done()

    # The blob rides HA's own media-player proxy: tokened URL, and a
    # content-keyed cache hash so artwork changes actually re-render.
    picture = player.entity_picture
    assert picture is not None
    assert picture.startswith(f"/api/media_player_proxy/{player.entity_id}?token=")
    assert "&cache=" in picture

    body, content_type = await player.async_get_media_image()
    assert body == PNG_BYTES
    assert content_type == "image/png"

    hash_with_blob = player.media_image_hash
    assert hash_with_blob is not None

    # Track art wins over the fallback slot.
    session.resources["/capability/videoMetaData/main/0"].update(
        {"title": "Show", "thumbnail": "https://cdn.example/show.jpg"}
    )
    await _wait_cache(
        "/capability/videoMetaData/main/0", "thumbnail", "https://cdn.example/show.jpg"
    )
    await hass.async_block_till_done()
    picture = player.entity_picture
    assert picture is not None
    assert picture.startswith(f"/api/media_player_proxy/{player.entity_id}?token=")

    assert player.media_image_hash != hash_with_blob

    # And back: no track URL, blob gone -> no picture at all.
    session.resources["/capability/videoMetaData/main/0"].update({"title": "", "thumbnail": ""})
    session.resources[_HREF].update({"x.com.samsung.tv.thumbnail": "not-base64!!"})
    await _sweep()
    await _wait_cache(_HREF, "x.com.samsung.tv.thumbnail", "not-base64!!")
    # The track rep's clearing must land before the media-image call too:
    # while the URL still reads, super() fetches it for real.
    await _wait_cache("/capability/videoMetaData/main/0", "thumbnail", "")
    await hass.async_block_till_done()
    body, _ct = await player.async_get_media_image()
    assert body is None


async def test_each_tv_entity_serves_its_own_art(hass: HomeAssistant) -> None:
    """Two TVs: nothing cross-serves, because the image lookup happens on
    the entity, not on a coordinator keyed by a URL."""
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.localthings.registry.identity import DeviceIdentity
    from tests.localthings.test_tv_media_player_tu7172 import (
        _IDENTITY,
        FakeTvSession,
    )
    from tests.localthings.test_tv_media_player_tu7172 import (
        ENTRY_DATA as TV_ENTRY_DATA,
    )

    entry_a = _entry(hass)
    await _setup_tv(hass, entry_a)
    coordinator_a = hass.data[DOMAIN][entry_a.entry_id]
    player_a = next(e for e in hass.data["media_player"].entities)

    di_b = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
    identity_b = DeviceIdentity(
        manufacturer=_IDENTITY.manufacturer,
        model=_IDENTITY.model,
        name="Bedroom TV",
        serial=None,
        device_id=di_b,
        device_types=_IDENTITY.device_types,
        raw=_IDENTITY.raw,
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={**TV_ENTRY_DATA, "device_key": di_b, "ocf_device_id": di_b},
        unique_id=f"localthings_{di_b}",
        version=4,
    )
    entry_b.add_to_hass(hass)
    with (
        patch(
            "custom_components.localthings.coordinator.create_transport",
            return_value=FakeTvSession(),
        ),
        patch(
            "custom_components.localthings.coordinator.read_identity",
            return_value=identity_b,
        ),
    ):
        await hass.config_entries.async_setup(entry_b.entry_id)
        await hass.async_block_till_done()
    coordinator_b = hass.data[DOMAIN][entry_b.entry_id]
    player_b = next(e for e in hass.data["media_player"].entities if e is not player_a)

    other_b64 = base64.b64encode(b"otherpng" * 4).decode()
    coordinator_a._observe.apply(
        _HREF, {"x.com.samsung.tv.thumbnail": PNG_B64}, source="optimistic"
    )
    coordinator_b._observe.apply(
        _HREF, {"x.com.samsung.tv.thumbnail": other_b64}, source="optimistic"
    )
    await hass.async_block_till_done()

    body_a, _ = await player_a.async_get_media_image()
    assert body_a == PNG_BYTES
    body_b, _ = await player_b.async_get_media_image()
    assert body_b == b"otherpng" * 4
    # And their proxy pictures cache-bust on their own content, not a
    # shared route's.
    assert player_a.entity_picture != player_b.entity_picture
