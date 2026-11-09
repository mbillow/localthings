"""AWM-US-M64-24-WD80 washer-dryer (issue #784): full coverage, including
the /settings/sound/* resources the washer registry didn't bind before."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import laundry
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc, SelectDesc
from tests.conftest import _load_device

_DEVICE_TYPES = ("oic.wk.d", "oic.d.washer")


@pytest.fixture(scope="module")
def resources():
    return _load_device("washer_dryer_awm_m64")


def _bind(resources):
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    assert reg is not None
    unbound: list[str] = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return reg, bound, unbound


def test_routes_to_washer(resources):
    by_oic = resolve(resources, device_types=_DEVICE_TYPES)
    # The consumer-model prefix ('WD8000DK') routes it without /oic/d too.
    by_model = resolve(resources)
    assert by_oic is not None and by_oic.name == "washer"
    assert by_model is not None and by_model.name == "washer"


def test_no_unbound_hrefs(resources):
    _, _, unbound = _bind(resources)
    assert unbound == []


def test_sound_entities(resources):
    _, bound, _ = _bind(resources)
    state = flatten(bound, resources)
    assert state["sound_mode"] == "tone"
    assert state["sound_tone"] == "11"
    assert state["sound_volume"] == 8
    assert state["sound_output"] == "speaker"
    assert state["alarm_in_mute"] is True


def test_sound_volume_uses_device_resolution(resources):
    """The board reports resolution 1; laundry.SOUND_VOLUME's fixed step 5
    would leave its current level 8 off the slider's grid."""
    _, bound, _ = _bind(resources)
    desc = next(b.desc for b in bound if b.desc.key == "sound_volume")
    assert isinstance(desc, NumberDesc)
    assert desc.native_min_fn and desc.native_max_fn and desc.step_fn
    rep = resources["/settings/sound/volume/vs/0"]
    assert (desc.native_min_fn(rep), desc.native_max_fn(rep), desc.step_fn(rep)) == (0, 15, 1)


def test_sound_tone_write_checks_device_list(resources):
    desc = laundry.SOUND_TONE.entities[0]
    assert isinstance(desc, SelectDesc) and desc.write_fn is not None
    rep = resources["/settings/sound/tone/vs/0"]
    assert desc.write_fn("3", rep) == (["settings", "sound", "tone", "vs", "0"], {"tone": "3"})
    assert desc.write_fn("12", rep) is None


def test_sound_mode_write_checks_device_list():
    desc = laundry.SOUND_MODE.entities[0]
    assert isinstance(desc, SelectDesc) and desc.write_fn is not None
    rep = {"mode": "mute", "supportedModes": ["mute", "buzzer"]}
    assert desc.write_fn("buzzer", rep) == (
        ["settings", "sound", "mode", "vs", "0"],
        {"mode": "buzzer"},
    )
    assert desc.write_fn("voice", rep) is None
