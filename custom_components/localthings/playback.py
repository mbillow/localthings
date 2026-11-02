"""Serialize playback changes for one AV device."""

import asyncio


class PlaybackController:
    """Share one lock across media-player commands."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()

    def async_change(self) -> asyncio.Lock:
        return self.lock
