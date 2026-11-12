"""The native TV profile through HA's existing AV media-player platform."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaType,
)
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature as F,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.localthings.const import CONF_MODEL, DOMAIN
from custom_components.localthings.registry.capabilities import tv
from tests.conftest import _load_device

from .test_soundbar import (
    _IDENTITY,
    ENTRY_DATA,
    FakeSoundbarSession,
    _freeze_polls,
    _mock_entry,
    _push,
)


async def _setup_tv(hass):
    session = FakeSoundbarSession()
    session.resources = _load_device("tv_qn90a")
    identity = replace(
        _IDENTITY,
        model="QE50QN90AATXXU",
        device_types=("oic.wk.d", "oic.d.tv"),
        raw={"/oic/res": [{"href": href} for href in session.resources]},
    )
    entry = _mock_entry(hass)
    hass.config_entries.async_update_entry(entry, data={**ENTRY_DATA, CONF_MODEL: "QE50QN90AATXXU"})
    with (
        patch("custom_components.localthings.coordinator.create_transport", return_value=session),
        patch("custom_components.localthings.coordinator.read_identity", return_value=identity),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _freeze_polls(coordinator)
    await hass.async_block_till_done()
    player = next(iter(hass.data["media_player"].entities))
    return session, coordinator, player


async def test_tv_state_and_only_reported_controls(hass: HomeAssistant):
    _, _, player = await _setup_tv(hass)
    assert player.device_class is MediaPlayerDeviceClass.TV
    assert player.media_content_type is MediaType.VIDEO
    assert player.state == "on"
    assert player.volume_level == 0.37
    assert player.is_volume_muted is False
    assert player.source == "PC (HDMI1)"
    assert player.source_list == ["TV", "PC (HDMI1)", "HDMI 3", "PC (HDMI4)"]
    assert player.sound_mode == "Game"
    assert player.sound_mode_list == ["Amplify", "Standard", "Game"]
    assert (
        player.supported_features
        == F.TURN_OFF | F.VOLUME_SET | F.VOLUME_MUTE | F.SELECT_SOURCE | F.SELECT_SOUND_MODE
    )
    assert player.media_title is None
    assert player.media_position is None


async def test_ha_services_write_native_tv_fields_and_correct_input_id(hass: HomeAssistant):
    session, _, player = await _setup_tv(hass)

    async def call(service, **fields):
        await hass.services.async_call(
            "media_player", service, {"entity_id": player.entity_id, **fields}, blocking=True
        )

    await call("volume_set", volume_level=0.38)
    await call("volume_mute", is_volume_muted=True)
    await call("select_source", source="PC (HDMI4)")
    await call("select_sound_mode", sound_mode="Standard")
    await call("turn_off")
    assert session.writes == [
        (["sec", "tv", "audio"], {"volume": 38}),
        (["sec", "tv", "audio"], {"mute": True}),
        (["sec", "tv", "inputsource"], {tv.CURRENT_MODE: "HDMI4"}),
        (["sec", "tv", "mode", "sound"], {tv.CURRENT_MODE: "modeStandard"}),
        (["sec", "tv", "switch", "binary"], {"value": False}),
    ]


async def test_options_change_live_and_disappeared_labels_are_rejected(hass: HomeAssistant):
    session, coordinator, player = await _setup_tv(hass)
    _push(
        coordinator,
        tv.HREF_INPUT,
        {
            tv.SUPPORTED_MODES: [{tv.PREFIX + "id": "HDMI2", tv.PREFIX + "name": "Console"}],
            tv.CURRENT_MODE: "HDMI1",
        },
    )
    await hass.async_block_till_done()
    assert player.source_list == ["Console"]
    assert player.source == "HDMI1"  # real current ID, not a fabricated selectable option
    with pytest.raises(ServiceValidationError):
        await player.async_select_source("PC (HDMI4)")
    assert session.writes == []
    await player.async_select_source("Console")
    assert session.writes == [(["sec", "tv", "inputsource"], {tv.CURRENT_MODE: "HDMI2"})]


async def test_no_ocf_wake_or_blind_relative_playback_commands(hass: HomeAssistant):
    session, _, player = await _setup_tv(hass)
    for command in (
        player.async_turn_on,
        player.async_media_play,
        player.async_media_pause,
        player.async_media_stop,
    ):
        with pytest.raises(ServiceValidationError):
            await command()
    assert session.writes == []
    assert not player.supported_features & (F.TURN_ON | F.PLAY | F.PAUSE | F.STOP | F.SEEK)


async def test_writable_fields_control_features_and_direct_writes(hass: HomeAssistant):
    session, coordinator, player = await _setup_tv(hass)
    _push(coordinator, tv.HREF_AUDIO, {"if": ["oic.if.baseline"]})
    _push(coordinator, tv.HREF_INPUT, {"if": ["oic.if.baseline"]})
    await hass.async_block_till_done()
    assert not player.supported_features & (F.VOLUME_SET | F.VOLUME_MUTE | F.SELECT_SOURCE)
    for command in (
        player.async_set_volume_level(0.4),
        player.async_mute_volume(True),
        player.async_select_source("TV"),
    ):
        with pytest.raises(ServiceValidationError):
            await command
    assert session.writes == []


@pytest.mark.parametrize("mode,confirmed", [("Game", True), ("Standard", False)])
async def test_tv_refusal_needs_fresh_matching_native_resource(hass, mode, confirmed):
    session, _, player = await _setup_tv(hass)
    session.reads.clear()

    def refused(path, body, timeout=None):
        session.writes.append((list(path), body))
        return 0x44, {"controlResponse": {"result": False, "errorCode": "unchanged"}}

    with patch.object(session, "write", side_effect=refused) as write:
        if confirmed:
            await player.async_select_sound_mode(mode)
        else:
            with pytest.raises(HomeAssistantError) as error:
                await player.async_select_sound_mode(mode)
            assert error.value.translation_key == "command_refused"
        assert write.call_count == 1  # explicit refusals are not resent
    assert tv.HREF_SOUND in session.reads
    assert session.resources[tv.HREF_SOUND][tv.CURRENT_MODE] == "modeGame"
