"""TV commands, notifications and Wake-on-LAN orchestration."""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

from . import wol
from .const import CONF_DEVICE_TYPE, CONF_HOST, CONF_MAC, DOMAIN
from .registry.capabilities import tv as tv_caps
from .registry.discovery import BoundEntity

if TYPE_CHECKING:
    from .coordinator import LocalThingsCoordinator

# What in a cast URL lets the TV in without logging in: Home Assistant's
# signature, or a camera_dash session token (only its start is kept).
_URL_CREDENTIALS = re.compile(r"(authSig=)[^&#]+|(/camera_dash/[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+")


def _without_credentials(url: str) -> str:
    """`url` for the log, without what would let someone else fetch it."""
    return _URL_CREDENTIALS.sub(lambda m: (m.group(1) or m.group(2)) + "…", url)


class AVControl:
    """Device-specific behavior using the coordinator's transport and cache."""

    _NOTIFY_REGISTER_SETTLE_S = 3.0
    _WAKE_WINDOW_S = 120.0
    _WAKE_REFRESH_S = 5.0

    def __init__(self, coordinator: LocalThingsCoordinator) -> None:
        self._coordinator = coordinator
        self.hass = coordinator.hass
        self._log = coordinator._log
        self._notify_registered = False
        self._notify_lock = asyncio.Lock()
        self._notify_seq = 0
        self._wake_lock = asyncio.Lock()
        self._wake_task: asyncio.Task | None = None
        self._closed = False

    def close(self) -> None:
        self._closed = True
        if self._wake_task is not None:
            self._wake_task.cancel()
            self._wake_task = None

    def _wake_mac(self) -> str | None:
        """The MAC a TV's magic packet goes to, or None off the TV family.

        The entry's own, else the device registry's (a MAC another source
        put on this device -- the entry only stores one a poll reported).
        """
        # The stored family too: an offline load knows no live one yet.
        if (
            self._coordinator.device_type_name
            or self._coordinator._entry.data.get(CONF_DEVICE_TYPE)
        ) != "tv":
            return None
        if mac := self._coordinator._entry.data.get(CONF_MAC):
            return mac
        device = async_get_device_registry(self.hass).async_get_device(
            identifiers={(DOMAIN, self._coordinator.device_key)}
        )
        if device is None:
            return None
        return next(
            (value for kind, value in device.connections if kind == CONNECTION_NETWORK_MAC),
            None,
        )

    def wakeable(self) -> bool:
        """True for a TV with a MAC to wake (async_power_on)."""
        return self._wake_mac() is not None

    def _answering(self) -> bool:
        """True while the last poll got through on a live session."""
        return (
            self._coordinator._session is not None
            and self._coordinator._failed_cycles == 0
            and self._coordinator._consecutive_poll_timeouts == 0
        )

    def unreachable(self) -> bool:
        """An AV board that stopped answering: switched off or gone, so its
        last state isn't carried on as an appliance's is."""
        return self._coordinator._flat and self._coordinator._failed_cycles > 0

    def reads_as_off(self) -> bool:
        """Keep a wakeable, unreachable TV callable by reporting it as off.

        Only failed cycles or handshakes qualify; a reconnect gap does not.
        Ping cannot tell power state because network standby may still answer.
        """
        if self._wake_mac() is None:
            return False
        return self._coordinator._failed_cycles > 0 or (
            self._coordinator._session is None and self._coordinator._handshake_failed
        )

    def _require_href(self, href: str, translation_key: str) -> None:
        """Refuse a command for a resource this device doesn't have."""
        if not self._coordinator.has_href(href):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
            )

    async def async_search(self, site: str, query: str) -> None:
        """Search on the TV (tv_caps.search_body): YouTube opens on the results."""
        self._require_href(tv_caps.HREF_SEARCH, "search_unsupported")
        async with self._coordinator.playback.async_change():
            await self._coordinator._async_put(
                ["sec", "tv", "searchonweb"],
                tv_caps.search_body(site, query),
                tv_caps.HREF_SEARCH,
            )

    async def async_notify(self, title: str | None, message: str) -> None:
        """Show a notification pop-up on the TV (tv_caps.notify_body)."""
        self._require_href(tv_caps.HREF_NATIVE_APP, "notify_unsupported")
        if not message.strip():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="notify_message_empty",
            )
        async with self._notify_lock:  # one registration, however many messages
            if not self._notify_registered:
                await self._coordinator._async_put(
                    tv_caps.NATIVE_APP_PATH,
                    tv_caps.notify_register_body(),
                    tv_caps.HREF_NATIVE_APP,
                )
                # Each write launches the app; give the registration time to land.
                await asyncio.sleep(self._NOTIFY_REGISTER_SETTLE_S)
                self._notify_registered = True
        # The app drops an id it has seen: seconds make a restart start past
        # the last one, and +1 keeps two in the same second apart.
        seq = max(int(time.time()), self._notify_seq + 1)
        self._notify_seq = seq
        await self._coordinator._async_put(
            tv_caps.NATIVE_APP_PATH,
            tv_caps.notify_body(seq, title or tv_caps.NOTIFY_DEFAULT_TITLE, message),
            tv_caps.HREF_NATIVE_APP,
        )

    async def async_show_menu(self, body: dict) -> None:
        """Open a TV settings page or set a setting (tv_caps.show_menu_body)."""
        self._require_href(tv_caps.HREF_SHOW_MENU, "settings_unsupported")
        await self._coordinator._async_put(tv_caps.SHOW_MENU_PATH, body, tv_caps.HREF_SHOW_MENU)

    def can_launch_gallery(self) -> bool:
        """True when casts can launch Gallery directly (tv_caps.gallery_body)."""
        return self._coordinator.has_href(tv_caps.HREF_NATIVE_APP)

    async def async_launch_gallery(self, kind: str, url: str) -> None:
        """Show an image or play a video in Gallery, skipping the prompt."""
        self._log.debug("Gallery %s: %s", kind, _without_credentials(url))
        await self._coordinator._async_put(
            tv_caps.NATIVE_APP_PATH, tv_caps.gallery_body(kind, url), tv_caps.HREF_NATIVE_APP
        )

    def can_launch_youtube(self) -> bool:
        """True when YouTube casts can start the app directly (tv_caps.youtube_launch_body)."""
        return self._coordinator.has_href(tv_caps.HREF_LAUNCH_APP)

    async def async_launch_youtube(self, video_id: str) -> None:
        """Play a YouTube video in the TV's YouTube app, skipping the prompt."""
        await self._coordinator._async_put(
            tv_caps.LAUNCH_APP_PATH, tv_caps.youtube_launch_body(video_id), tv_caps.HREF_LAUNCH_APP
        )

    async def async_cast(self, body: dict) -> None:
        """Hand an image or video to the TV's renderer (tv_caps)."""
        self._require_href(tv_caps.HREF_RENDERER, "cast_unsupported")
        await self._coordinator._async_put(["content", "renderer"], body, tv_caps.HREF_RENDERER)

    async def async_power_on(self, bound_entity: BoundEntity) -> None:
        """Wake a TV with a known MAC; other devices take an OCF power write.

        OCF can't turn a TV on (#834): its endpoint is gone in standby, and
        one whose switch answers on is on already.
        """
        mac = self._wake_mac()
        if mac is None:
            await self._coordinator.async_send_command(bound_entity, ("power", True))
            return
        if self._answering() and self._coordinator.resource(tv_caps.HREF_SWITCH).get("value"):
            return
        await self._async_wake_on_lan(mac)

    async def _async_wake_on_lan(self, mac: str) -> None:
        async with self._wake_lock:
            if self._closed or self.hass.is_stopping:
                return
            if self._wake_task is not None and not self._wake_task.done():
                self._log.debug("Wake-on-LAN already in progress; not sending again")
                return
            targets = await wol.async_targets(self.hass, self._coordinator._entry.data[CONF_HOST])
            if self._closed or self.hass.is_stopping:
                return
            try:
                sent = await self.hass.async_add_executor_job(wol.send_magic_packet, mac, targets)
            except (OSError, ValueError) as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="wake_on_lan_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
            if not sent:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="wake_on_lan_no_route",
                    translation_placeholders={"targets": ", ".join(targets)},
                )
            if self._closed or self.hass.is_stopping:
                return
            self._log.info("sent Wake-on-LAN to %s via %s", mac, ", ".join(targets))
            self._wake_task = self.hass.async_create_background_task(
                self._async_follow_wake(), name="localthings_wake"
            )

    async def _async_follow_wake(self) -> None:
        """Poll quickly until the woken TV answers, or the window closes.

        Drop the stale session and bypass rediscovery backoff: a TV may
        return on a new port (#435).
        """
        async with self._coordinator._session_lock:
            if self._coordinator._session is not None:
                await self.hass.async_add_executor_job(self._coordinator._close_session)
            self._coordinator._consecutive_poll_timeouts = 0
        deadline = time.monotonic() + self._WAKE_WINDOW_S
        while time.monotonic() < deadline:
            await asyncio.sleep(self._WAKE_REFRESH_S)
            self._coordinator._rediscovery_backoff_s = 0.0
            self._coordinator._next_rediscovery_ts = 0.0
            await self._coordinator.async_refresh()
            if self._answering():
                self._log.info("TV answered after Wake-on-LAN")
                return
        self._log.info(
            "TV did not answer within %.0f s of Wake-on-LAN; is Power On with Mobile enabled?",
            self._WAKE_WINDOW_S,
        )
