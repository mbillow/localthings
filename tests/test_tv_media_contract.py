"""TV media writes are bounded, advertised and absolute, not blind toggles."""

import pytest

from custom_components.localthings.registry.capabilities import tv
from tests.conftest import _load_device


@pytest.mark.parametrize(
    "payload",
    [
        ("power", True),
        ("power", "off"),
        ("volume", -1),
        ("volume", 101),
        ("volume", float("nan")),
        ("volume", float("inf")),
        ("volume", True),
        ("mute", "false"),
        ("source", "PC"),
        ("source", "HDMI2"),
        ("sound_mode", "modeUnsupported"),
        ("playback", "play"),
        ("shuffle", True),
    ],
)
def test_unsupported_payloads_never_become_writes(payload):
    resources = _load_device("tv_qn90a")
    assert tv._validate_media(payload, {}, resources) == "media_option_not_supported"
    assert tv._media_write(payload, {}, resources=resources) is None
