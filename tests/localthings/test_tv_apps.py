"""Launching TV apps by id, and by the name of one the TV has run.

Measured on the UE50TU7172: /sec/tv/launchapp launches any app id written
as x.com.samsung.tv.targetAppId; /sec/tv/contentinfo names the app in front
(input 'webapp', title = name, subtitle = id).
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import tvapps
from custom_components.localthings.const import CONF_KNOWN_APPS, DOMAIN
from tests.localthings.test_tv_media_player_tu7172 import ENTRY_DATA, MOCK_TV_DI, _setup_tv

_LAUNCH = ["sec", "tv", "launchapp"]
# What the fixture's /sec/tv/launchapp advertises.
_NATIVE = [
    "apps",
    "browser",
    "channel list",
    "connection guide",
    "guide",
    "home",
    "menu",
    "schedule manager",
    "search all",
    "setup remote controller",
    "sources",
    "tvplus",
    "usb",
]
_PLEX = {
    "x.com.samsung.tv.input": "webapp",
    "x.com.samsung.tv.title": "Plex",
    "x.com.samsung.tv.subtitle": "kIciSQlYEM.plex",
}


# --- tvapps ----------------------------------------------------------------------


def test_the_running_app_is_read_from_contentinfo() -> None:
    assert tvapps.running_app(_PLEX) == ("kIciSQlYEM.plex", "Plex")


@pytest.mark.parametrize(
    "rep",
    [
        {**_PLEX, "x.com.samsung.tv.input": "dtv"},  # a broadcast's subtitle is no app
        {**_PLEX, "x.com.samsung.tv.subtitle": ""},
        {**_PLEX, "x.com.samsung.tv.subtitle": "Episode 3 of 8"},
        {**_PLEX, "x.com.samsung.tv.title": ""},
        {},
    ],
)
def test_nothing_else_reads_as_a_running_app(rep) -> None:
    assert tvapps.running_app(rep) is None


def test_known_apps_report_only_what_is_new_or_renamed() -> None:
    known = tvapps.KnownApps({"bad": 1, "": "x", "kIciSQlYEM.plex": "Plex"})
    assert known.snapshot() == {"kIciSQlYEM.plex": "Plex"}
    assert known.observe(_PLEX) is None
    assert known.observe({**_PLEX, "x.com.samsung.tv.title": "Plex TV"}) == (
        "kIciSQlYEM.plex",
        "Plex TV",
    )


_ID = "x.com.samsung.tv.targetAppId"
_NAME = "x.com.samsung.tv.targetAppName"


@pytest.mark.parametrize(
    ("value", "body"),
    [
        # A known app, by name or id, launches by its id.
        ("plex", {_ID: "kIciSQlYEM.plex"}),
        ("kIciSQlYEM.plex", {_ID: "kIciSQlYEM.plex"}),
        # Shaped like an id: a store number or a package id.
        ("3201512006963", {_ID: "3201512006963"}),
        ("111299001912", {_ID: "111299001912"}),
        # Built-in screens launch by name, in the TV's own spelling.
        ("Guide", {_NAME: "guide"}),
        ("channel list", {_NAME: "channel list"}),
        # An app named but never seen: the TV resolves the name itself.
        ("Netflix", {_NAME: "Netflix"}),
    ],
)
def test_the_launch_body_picks_id_or_name(value, body) -> None:
    rep = {
        tvapps.NATIVE_APPS_FIELD: ["home", "guide", "channel list"],
        tvapps.FIELD: {"apps": {"kIciSQlYEM.plex": "Plex"}, "current": None},
    }
    assert tvapps.launch_body(rep, value) == body


def test_the_select_offers_built_in_screens_then_known_apps() -> None:
    rep = {
        tvapps.NATIVE_APPS_FIELD: ["home", "guide"],
        tvapps.FIELD: {"apps": {"b.app": "Zattoo", "a.app": "plex", "c.app": "Home"}},
    }
    # Device order for the screens; a known app named like one is not repeated.
    assert tvapps.app_names(rep) == ["home", "guide", "plex", "Zattoo"]


@pytest.mark.parametrize(
    ("native", "apps", "expected"),
    [
        (["gallery"], {"a": "Gallery"}, ["gallery"]),
        (["gallery", "Gallery", " gallery "], {"a": "Gallery"}, ["gallery"]),
        ([], {"a": "Gallery", "b": "gallery", "c": " Gallery "}, ["Gallery"]),
        ([" gallery ", "home", " "], {"a": " Gallery "}, ["gallery", "home"]),
    ],
)
def test_app_names_merge_case_and_whitespace_duplicates(native, apps, expected) -> None:
    rep = {
        tvapps.NATIVE_APPS_FIELD: native,
        tvapps.FIELD: {"apps": apps, "current": "a"},
    }
    assert tvapps.app_names(rep) == expected
    assert tvapps.current_name(rep) == expected[0]
    assert tvapps.launch_body(rep, expected[0]) == {_ID: "a"}


def test_current_app_uses_the_retained_option_spelling() -> None:
    rep = {tvapps.FIELD: {"apps": {"a": "Gallery", "b": "gallery"}, "current": "b"}}
    assert tvapps.app_names(rep) == ["Gallery"]
    assert tvapps.current_name(rep) == "Gallery"


# --- through Home Assistant --------------------------------------------------------


def _entry(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **extra},
        unique_id=f"localthings_{MOCK_TV_DI}",
        version=4,
    )
    entry.add_to_hass(hass)
    return entry


def _open_app(hass: HomeAssistant):
    return next(s for s in hass.states.async_all("select") if s.entity_id.endswith("open_app"))


async def _show(hass: HomeAssistant, coordinator, rep: dict) -> None:
    """The TV reports `rep` as what is on, the way a poll would."""
    # Seed the fake device's rep too, or a sub-poll re-applies the fixture's
    # broadcast rep over this one before the state renders.
    coordinator._session.resources[tvapps.CONTENT_HREF].update(rep)
    coordinator._observe.apply(tvapps.CONTENT_HREF, rep, source="poll")
    # known_apps lands first; the open_app select's STATE re-reads the
    # resources snapshot a push later. Wait on the user-visible invariant.
    name = rep.get("x.com.samsung.tv.title")
    for _ in range(100):
        if coordinator.known_apps().get(rep.get("x.com.samsung.tv.subtitle")):
            state = _open_app(hass)
            if state.attributes.get("options") and name in state.attributes["options"]:
                break
        await asyncio.sleep(0.01)
    assert coordinator.known_apps().get(rep.get("x.com.samsung.tv.subtitle")), "app never learned"
    assert name in _open_app(hass).attributes["options"], "select never offered the app"
    await hass.async_block_till_done()


async def test_an_app_the_tv_ran_is_remembered_and_offered(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # The fixture shows live TV, so only the built-in screens are offered.
    assert _open_app(hass).attributes["options"] == _NATIVE

    await _show(hass, coordinator, _PLEX)

    assert entry.data[CONF_KNOWN_APPS] == {"kIciSQlYEM.plex": "Plex"}
    state = _open_app(hass)
    assert state.attributes["options"] == [*_NATIVE, "Plex"]
    assert state.state == "Plex"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": state.entity_id, "option": "Plex"}, blocking=True
    )
    assert (_LAUNCH, {"x.com.samsung.tv.targetAppId": "kIciSQlYEM.plex"}) in session.writes


async def test_known_apps_survive_a_restart(hass: HomeAssistant) -> None:
    entry = _entry(hass, **{CONF_KNOWN_APPS: {"kIciSQlYEM.plex": "Plex"}})
    await _setup_tv(hass, entry)

    assert _open_app(hass).attributes["options"] == [*_NATIVE, "Plex"]


async def test_a_built_in_screen_launches_by_name(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _open_app(hass).entity_id, "option": "guide"},
        blocking=True,
    )

    assert (_LAUNCH, {"x.com.samsung.tv.targetAppName": "guide"}) in session.writes


@pytest.mark.parametrize(
    ("media_id", "app_id"),
    [
        ("kIciSQlYEM.plex", "kIciSQlYEM.plex"),
        ("Plex", "kIciSQlYEM.plex"),
        ("3201512006963", "3201512006963"),
    ],
)
async def test_play_media_app_launches_by_id_or_known_name(
    hass: HomeAssistant, media_id, app_id
) -> None:
    entry = _entry(hass, **{CONF_KNOWN_APPS: {"kIciSQlYEM.plex": "Plex"}})
    session = await _setup_tv(hass, entry)
    player_id = hass.states.async_all("media_player")[0].entity_id

    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": player_id, "media_content_type": "app", "media_content_id": media_id},
        blocking=True,
    )

    assert (_LAUNCH, {"x.com.samsung.tv.targetAppId": app_id}) in session.writes


async def test_an_empty_app_id_is_refused(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    player = next(iter(hass.data["media_player"].entities))

    with pytest.raises(ServiceValidationError) as err:
        await player.async_play_media("app", "  ")
    assert err.value.translation_key == "invalid_app_id"
    assert not any(path == _LAUNCH for path, _body in session.writes)


async def test_forget_known_apps_keeps_only_the_built_in_screens(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr

    entry = _entry(hass, **{CONF_KNOWN_APPS: {"kIciSQlYEM.plex": "Plex"}})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, coordinator.device_key)})
    assert device is not None

    await hass.services.async_call(
        DOMAIN, "forget_known_apps", {"device_id": device.id}, blocking=True
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_KNOWN_APPS] == {}
    assert coordinator.known_apps() == {}
    assert _open_app(hass).attributes["options"] == _NATIVE
