"""The TV's Camera casting option (CONF_CAMERA_CASTING): off by default, it
keeps camera_dash/camera_stream's HTTP views out of Home Assistant."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, patch

from homeassistant.components.media_source import PlayMedia
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.localthings import camera_dash, camera_stream
from custom_components.localthings.const import CONF_CAMERA_CASTING, DOMAIN
from tests.localthings.test_soundbar import _mock_entry, _setup_soundbar
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv
from tests.localthings.test_tv_notify import _setup_tv as _setup_tv_with_native_app


async def _menu(hass: HomeAssistant, entry) -> list[str]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    hass.config_entries.options.async_abort(result["flow_id"])
    return list(cast(list[str], result.get("menu_options")))


async def _set_option(hass: HomeAssistant, entry, enabled: bool) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "camera_casting"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CAMERA_CASTING: enabled}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_off_by_default_registers_no_views(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = _entry(hass)
    await _setup_tv(hass, entry)

    assert not hass.data[DOMAIN][entry.entry_id].camera_casting_enabled
    assert not camera_dash.is_available(hass)
    assert not camera_stream.is_available(hass)
    assert not camera_stream.casting_enabled(hass)


async def test_off_casts_a_camera_through_home_assistants_stream(hass: HomeAssistant) -> None:
    """No ffmpeg of ours: the camera resolves through media_source, even with
    the views registered by another TV."""
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    playlist = "/api/hls/abc/master_playlist.m3u8"

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch("custom_components.localthings.camera_dash.async_start", AsyncMock()) as start,
        patch(
            "custom_components.localthings.camera_stream.async_stream_source", AsyncMock()
        ) as source,
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            AsyncMock(return_value=PlayMedia(playlist, "application/vnd.apple.mpegurl")),
        ) as resolve,
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}?authSig=x",
        ),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")

    start.assert_not_awaited()
    source.assert_not_awaited()
    resolve.assert_awaited_once()
    assert not any(
        "camera_dash" in str(body) or "camera_stream" in str(body) for _path, body in session.writes
    )


async def test_turning_it_on_registers_the_views_and_off_closes_them(
    hass: HomeAssistant,
) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert "camera_casting" in await _menu(hass, entry)

    await _set_option(hass, entry, True)
    assert coordinator.camera_casting_enabled
    assert camera_dash.is_available(hass) and camera_stream.is_available(hass)
    assert camera_stream.casting_enabled(hass)

    with patch.object(coordinator.playback, "async_close", AsyncMock()) as close:
        await _set_option(hass, entry, False)
    close.assert_awaited_once()
    # Home Assistant keeps a route once added; the views refuse from now on.
    assert camera_dash.is_available(hass)
    assert not camera_stream.casting_enabled(hass)


async def test_an_entry_with_it_on_registers_the_views_at_setup(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = _entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_CAMERA_CASTING: True})
    await _setup_tv(hass, entry)

    assert camera_dash.is_available(hass) and camera_stream.is_available(hass)
    assert camera_stream.casting_enabled(hass)


async def test_only_a_tv_offers_the_option(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    assert "camera_casting" not in await _menu(hass, entry)
    hass.config_entries.async_update_entry(entry, options={CONF_CAMERA_CASTING: True})
    assert not hass.data[DOMAIN][entry.entry_id].camera_casting_enabled
