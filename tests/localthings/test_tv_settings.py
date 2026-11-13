"""TV settings through /sec/tv/showmenu: open a page, set the sleep timer,
turn the screen off.

Measured on the UE50TU7172: targetMenuId picturemode with an empty param
opens Picture Mode; pictureOff "on" blanks the screen (sound stays; a key
press brings it back); sleeptimer "60" showed 59 min a minute later.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.registry.capabilities import tv as tv_caps
from tests.localthings import test_tv_media_player_tu7172 as test_tv_media_player
from tests.localthings.test_tv_media_player_tu7172 import _entry

_SHOW_MENU = ["sec", "tv", "showmenu"]


async def _setup_tv(hass: HomeAssistant, *, advertised: bool = True):
    """The TV, with showmenu in /oic/res as the real unit lists it (it never
    answers a read, so the captured fixture has no rep for it)."""
    identity = test_tv_media_player._IDENTITY
    links = list(identity.raw["/oic/res"])
    if advertised:
        links.append({"href": tv_caps.HREF_SHOW_MENU})
    with patch.object(
        test_tv_media_player, "_IDENTITY", replace(identity, raw={"/oic/res": links})
    ):
        return await test_tv_media_player._setup_tv(hass, _entry(hass))


def _device_id(hass: HomeAssistant) -> str:
    return next(iter(dr.async_get(hass).devices.values())).id


def _menu(menu_id: str, param: str, name: str | None = None) -> tuple[list[str], dict]:
    return (
        _SHOW_MENU,
        {
            "x.com.samsung.tv.targetMenuId": menu_id,
            "x.com.samsung.tv.targetMenuName": name or menu_id,
            "x.com.samsung.tv.targetMenuParam": param,
        },
    )


async def test_open_settings_opens_the_page(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass)

    await hass.services.async_call(
        DOMAIN,
        "open_settings",
        {"device_id": _device_id(hass), "page": " picturemode "},
        blocking=True,
    )

    assert _menu("picturemode", "") in session.writes


@pytest.mark.parametrize(("minutes", "param"), [(60, "60"), ("90", "90"), (0, "0")])
async def test_set_sleep_timer_sends_minutes_like_the_app(
    hass: HomeAssistant, minutes, param
) -> None:
    session = await _setup_tv(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_sleep_timer",
        {"device_id": _device_id(hass), "minutes": minutes},
        blocking=True,
    )

    assert _menu("sleeptimer", param, "Sleep Timer") in session.writes


@pytest.mark.parametrize("minutes", [-1, 241, "soon"])
async def test_a_bad_sleep_timer_is_refused(hass: HomeAssistant, minutes) -> None:
    session = await _setup_tv(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_sleep_timer",
            {"device_id": _device_id(hass), "minutes": minutes},
            blocking=True,
        )
    assert not any(path == _SHOW_MENU for path, _body in session.writes)


async def test_the_screen_off_button(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass)
    button = next(s.entity_id for s in hass.states.async_all("button") if "screen" in s.entity_id)

    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)

    assert _menu("pictureOff", "on") in session.writes


async def test_a_tv_without_showmenu_refuses(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, advertised=False)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_sleep_timer",
            {"device_id": _device_id(hass), "minutes": 30},
            blocking=True,
        )
    assert err.value.translation_key == "settings_unsupported"
    assert not any(path == _SHOW_MENU for path, _body in session.writes)


def test_the_settings_selector_accepts_other_firmware_pages() -> None:
    services = Path(tv_caps.__file__).parents[2] / "services.yaml"
    field = yaml.safe_load(services.read_text())["open_settings"]["fields"]["page"]
    selector = field["selector"]["select"]
    assert selector["custom_value"] is True
    assert field["example"] in selector["options"]
