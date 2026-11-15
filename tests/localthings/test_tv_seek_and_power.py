"""TV media player seek gating, remote power fallback and thumbnail types."""

from __future__ import annotations

import asyncio
import base64

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.registry.entities import MediaPlayerDesc
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv

_PLAYBACK = "/capability/mediaPlayback/main/0"
_VIDEO = "/capability/videoMetaData/main/0"
_THUMBNAIL = "/sec/tv/contentinfo/thumbnail"


async def test_live_tv_offers_no_seek_until_the_playback_feed_has_a_position(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    # The fixture is plain DTV: no track, the playback feed idles at 0.
    assert not player.supported_features & MediaPlayerEntityFeature.SEEK
    await player.async_media_seek(30)
    assert not any(body.get("modes") == ["seek"] for _path, body in session.writes)

    session.resources[_VIDEO].update({"title": "Clip"})
    session.resources[_PLAYBACK].update({"modes": ["play"], "position": 10_000})
    await coordinator.async_refresh()
    for _ in range(100):
        if coordinator.resource(_PLAYBACK).get("position") == 10_000:
            break
        await asyncio.sleep(0.01)
    await hass.async_block_till_done()

    assert player.supported_features & MediaPlayerEntityFeature.SEEK


async def test_remote_power_without_a_media_player_is_a_clear_error(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.bound = [b for b in coordinator.bound if not isinstance(b.desc, MediaPlayerDesc)]
    remote_id = hass.states.async_all("remote")[0].entity_id

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call("remote", "turn_on", {"entity_id": remote_id}, blocking=True)
    assert err.value.translation_key == "remote_power_unavailable"


async def test_jpeg_thumbnail_is_served_as_jpeg(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    coordinator._observe.apply(
        _THUMBNAIL,
        {"x.com.samsung.tv.thumbnail": base64.b64encode(jpeg).decode()},
        source="optimistic",
    )
    await hass.async_block_till_done()

    assert await player.async_get_media_image() == (jpeg, "image/jpeg")
