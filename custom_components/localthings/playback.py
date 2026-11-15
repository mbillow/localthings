"""Playback serialization and camera ownership for one AV device."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING

from . import camera_dash

if TYPE_CHECKING:
    from .coordinator import LocalThingsCoordinator

CameraCast = tuple[str, Callable[[str], Awaitable[None]]]


class PlaybackController:
    """Keep camera claims and retries consistent across all device entities."""

    def __init__(self, coordinator: LocalThingsCoordinator) -> None:
        self._coordinator = coordinator
        self.lock = asyncio.Lock()
        self._generation = object()
        self.camera_cast: CameraCast | None = None

    @asynccontextmanager
    async def async_change(self) -> AsyncIterator[None]:
        """Release the old camera only after replacement playback succeeds."""
        async with self.lock:
            generation = self._generation
            previous = self._invalidate()
            try:
                yield
            except BaseException:
                failed = self.camera_cast
                self._generation, self.camera_cast = generation, previous
                await self._release(failed)
                raise
            await self._release(previous)

    def _invalidate(self) -> CameraCast | None:
        self._generation = object()
        previous, self.camera_cast = self.camera_cast, None
        return previous

    async def _release(self, cast: CameraCast | None) -> None:
        if cast is not None:
            await camera_dash.async_release(self._coordinator.hass, *cast)

    async def async_close(self) -> None:
        async with self.lock:
            await self._release(self._invalidate())

    async def async_cast_camera(self, entity_id: str, source: str) -> bool:
        """Start a camera within async_change; return False if DASH is unavailable."""
        hass = self._coordinator.hass
        relaunch = partial(self._async_relaunch, self._generation)
        if (token := await camera_dash.async_start(hass, entity_id, relaunch, source)) is None:
            return False
        self.camera_cast = token, relaunch
        await self._coordinator.av.async_launch_gallery(
            "video", camera_dash.manifest_url(hass, token)
        )
        return True

    async def _async_relaunch(self, generation: object, token: str) -> None:
        # A retry queued behind newer playback must not reopen the old camera.
        async with self.lock:
            if generation is self._generation:
                await self._coordinator.av.async_launch_gallery(
                    "video", camera_dash.manifest_url(self._coordinator.hass, token)
                )
