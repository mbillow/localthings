"""Remote platform for Local Things (TVs).

One remote entity per TV, sending keys to /sec/tv/remotecontrol. Commands are
firmware key names ('KEY_CHUP') or the aliases in _ALIASES; any other
KEY_* passes through. Turning it on or off uses the media player's power write.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from contextlib import nullcontext
from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, async_add_bound_entities
from .registry.capabilities.tv import HREF_SWITCH, KEY_PRESS, KEY_RELEASE
from .registry.entities import MediaPlayerDesc, RemoteDesc

_COMMAND_RE = re.compile(r"^KEY_[A-Z0-9_]+$")
_CONTENT_KEYS = frozenset(
    {
        "KEY_POWER",
        "KEY_POWEROFF",
        "KEY_HOME",
        "KEY_SOURCE",
        "KEY_TV",
        "KEY_DTV",
        "KEY_CHUP",
        "KEY_CHDOWN",
        "KEY_CH_LIST",
        "KEY_GUIDE",
        "KEY_EXIT",
        "KEY_RETURN",
        "KEY_SEARCH",
        "KEY_CONTENTS",
        "KEY_SMART_HUB",
        "KEY_STOP",
    }
)

# Named after the SmartThings plugin's remote labels.
_ALIASES: dict[str, str] = {
    "power": "KEY_POWER",
    "volume_up": "KEY_VOLUP",
    "volume_down": "KEY_VOLDOWN",
    "mute": "KEY_MUTE",
    "channel_up": "KEY_CHUP",
    "channel_down": "KEY_CHDOWN",
    "home": "KEY_HOME",
    "guide": "KEY_GUIDE",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "ok": "KEY_ENTER",
    "enter": "KEY_ENTER",
    "back": "KEY_RETURN",
    "return": "KEY_RETURN",
    "menu": "KEY_MENU",
    "exit": "KEY_EXIT",
    "source": "KEY_SOURCE",
    "info": "KEY_INFO",
    "channel_list": "KEY_CH_LIST",
    "caption": "KEY_CAPTION",
    "aspect": "KEY_ASPECT",
    "search": "KEY_SEARCH",
    "mts": "KEY_MTS",
    "audio_description": "KEY_AD",
}
for _i in range(10):
    _ALIASES[str(_i)] = f"KEY_{_i}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_bound_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda b: isinstance(b.desc, RemoteDesc),
        lambda b: LocalThingsRemote(coordinator, b),
    )


class LocalThingsRemote(LocalThingsEntity, RemoteEntity):
    """Remote entity issuing KEY_* commands over OCF."""

    @property
    def available(self) -> bool:
        # Turn-on still works on a TV that is off (see the media player).
        return self.coordinator.av.reads_as_off() or super().available

    @property
    def is_on(self) -> bool | None:
        """The TV's power state; without it the remote shows "unknown"."""
        if self.coordinator.av.reads_as_off():
            return False
        value = (self.coordinator.resource(HREF_SWITCH) or {}).get("value")
        return value if isinstance(value, bool) else None

    def _media_bound(self):
        """The media player's binding: the remote powers the TV through it,
        never KEY_POWER, which would only toggle blindly."""
        media_bound = next(
            (b for b in self.coordinator.bound if isinstance(b.desc, MediaPlayerDesc)), None
        )
        if media_bound is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="remote_power_unavailable",
            )
        return media_bound

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.av.async_turn_on(self._media_bound())

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.av.async_turn_off(self._media_bound())

    def _resolve_key(self, command: str) -> str:
        norm = command.strip()
        alias = _ALIASES.get(norm) or _ALIASES.get(norm.lower().replace(" ", "_"))
        if alias is not None:
            return alias
        if _COMMAND_RE.fullmatch(norm):
            return norm
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_remote_command",
            translation_placeholders={"command": command},
        )

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        num_repeats = int(kwargs.get("num_repeats", 1)) if kwargs.get("num_repeats") else 1
        delay_secs = float(kwargs.get("delay_secs", 0.0) or 0.0)
        # A held key is a press, the wait, then a release (the plugin's
        # VAL_PRESSED/VAL_RELEASED); otherwise a single tap.
        hold_secs = float(kwargs.get("hold_secs", 0.0) or 0.0)
        keys = [self._resolve_key(c) for c in command]
        await self.coordinator.av.async_check_keys()
        first = True
        for _ in range(num_repeats):
            for key in keys:
                if not first and delay_secs:
                    await asyncio.sleep(delay_secs)
                if hold_secs > 0:
                    await self._send_key(key, KEY_PRESS)
                    try:
                        await asyncio.sleep(hold_secs)
                    finally:
                        await self.coordinator.async_send_command(self._bound, (key, KEY_RELEASE))
                else:
                    await self._send_key(key)
                first = False

    async def _send_key(self, key: str, status: str | None = None) -> None:
        changes_content = key in _CONTENT_KEYS or key.startswith(
            ("KEY_HDMI", "KEY_AV", "KEY_COMPONENT")
        )
        async with self.coordinator.playback.async_change() if changes_content else nullcontext():
            await self.coordinator.async_send_command(
                self._bound, (key, status) if status is not None else key
            )
