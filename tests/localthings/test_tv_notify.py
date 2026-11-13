"""Notifications on the TV through its notification service app.

Measured on the UE50TU7172: launchnativeapp with an "ocf_noti" bundle for
com.samsung.tv.notification-serviceapp registers a service, and a
notification under that service then pops up with its title and message.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.const import DOMAIN
from tests.localthings import test_tv_media_player_tu7172 as test_tv_media_player
from tests.localthings.test_tv_media_player_tu7172 import _entry

_NATIVE_APP_HREF = "/sec/tv/launchnativeapp"


async def _setup_tv(hass: HomeAssistant):
    """The TV, with launchnativeapp in /oic/res as the real unit lists it
    (it never answers a read, so the captured fixture has no rep for it)."""
    identity = test_tv_media_player._IDENTITY
    links = [*identity.raw["/oic/res"], {"href": _NATIVE_APP_HREF}]
    with patch.object(
        test_tv_media_player, "_IDENTITY", replace(identity, raw={"/oic/res": links})
    ):
        return await test_tv_media_player._setup_tv(hass, _entry(hass))


_NATIVE_APP = ["sec", "tv", "launchnativeapp"]


def _payloads(session) -> list[dict]:
    out = []
    for path, body in session.writes:
        if path != _NATIVE_APP:
            continue
        assert body["x.com.samsung.tv.targetAppId"] == "com.samsung.tv.notification-serviceapp"
        (param,) = body["x.com.samsung.tv.bundleParam"]
        assert param["x.com.samsung.tv.bundle.key"] == "ocf_noti"
        out.append(json.loads(param["x.com.samsung.tv.bundle.value"]))
    return out


async def _notify(hass: HomeAssistant, message: str, title: str | None = None) -> None:
    data = {"entity_id": hass.states.async_all("notify")[0].entity_id, "message": message}
    if title is not None:
        data["title"] = title
    with patch(
        "custom_components.localthings.av.AVControl._NOTIFY_REGISTER_SETTLE_S",
        0,
    ):
        await hass.services.async_call("notify", "send_message", data, blocking=True)


async def test_the_first_message_registers_the_service_then_notifies(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass)

    await _notify(hass, "Washing is done", "Laundry")
    await _notify(hass, "Dryer is done")

    register, first, second = _payloads(session)
    action = register["x.com.samsung.data"]["x.com.samsung.tv.action"]
    assert action["action_type"] == "service_register"
    assert action["service_info"]["service_id"] == "io.home-assistant.localthings"
    assert action["service_info"]["service_default_option"] is True

    noti = first["x.com.samsung.data"]["x.com.samsung.tv.notification"]
    assert first["x.com.samsung.data"]["x.com.samsung.tv.message_type"] == "notification"
    assert noti["service_info"] == {"service_id": "io.home-assistant.localthings"}
    assert (noti["title"], noti["message"]) == ("Laundry", "Washing is done")
    assert noti["buttons"] == [{"text_sid": "COM_SID_OK"}]
    # Without a title the pop-up still needs one to show.
    second_noti = second["x.com.samsung.data"]["x.com.samsung.tv.notification"]
    assert (second_noti["title"], second_noti["message"]) == ("Home Assistant", "Dryer is done")
    assert second_noti["message_id"] != noti["message_id"]


async def test_an_empty_message_is_refused(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass)
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.av.async_notify(None, "  ")
    assert err.value.translation_key == "notify_message_empty"
    assert not _payloads(session)


async def test_a_tv_without_the_resource_refuses(hass: HomeAssistant) -> None:
    session = await test_tv_media_player._setup_tv(hass, _entry(hass))
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.av.async_notify(None, "Hello")
    assert err.value.translation_key == "notify_unsupported"
    assert not _payloads(session)


async def test_the_soundbar_has_no_notify_entity(hass: HomeAssistant) -> None:
    from tests.localthings.test_soundbar import _mock_entry, _setup_soundbar

    await _setup_soundbar(hass, _mock_entry(hass))

    assert hass.states.async_all("notify") == []


async def test_cancelled_registration_must_settle_again(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass)
    coordinator = next(iter(hass.data["media_player"].entities)).coordinator
    av = coordinator.av
    registered = asyncio.Event()

    async def put(path, body, href):
        session.writes.append((path, body))
        registered.set()

    with patch.object(coordinator, "_async_put", AsyncMock(side_effect=put)):
        with patch.object(av, "_NOTIFY_REGISTER_SETTLE_S", 3600):
            first = asyncio.create_task(av.async_notify(None, "first"))
            await registered.wait()
            assert len(_payloads(session)) == 1
            assert not av._notify_registered
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        registered.clear()
        with patch.object(av, "_NOTIFY_REGISTER_SETTLE_S", 0.01):
            second = asyncio.create_task(av.async_notify(None, "second"))
            await registered.wait()
            assert len(_payloads(session)) == 2
            assert not av._notify_registered
            assert not second.done()
            await second
    assert av._notify_registered
    register, repeated, message = _payloads(session)
    assert register == repeated
    assert message["x.com.samsung.data"]["x.com.samsung.tv.notification"]["message"] == "second"


@pytest.mark.parametrize("advertised", [False, True])
async def test_notify_and_screen_off_exist_only_with_their_resources(
    hass: HomeAssistant, advertised: bool
) -> None:
    """launchnativeapp and showmenu never answer a read: /oic/res decides."""
    identity = test_tv_media_player._IDENTITY
    extra = [{"href": _NATIVE_APP_HREF}, {"href": "/sec/tv/showmenu"}] if advertised else []
    links = [*identity.raw["/oic/res"], *extra]
    with patch.object(
        test_tv_media_player, "_IDENTITY", replace(identity, raw={"/oic/res": links})
    ):
        await test_tv_media_player._setup_tv(hass, _entry(hass))

    assert bool(hass.states.async_all("notify")) is advertised
    screen_off = [s for s in hass.states.async_all("button") if s.entity_id.endswith("screen_off")]
    assert bool(screen_off) is advertised
