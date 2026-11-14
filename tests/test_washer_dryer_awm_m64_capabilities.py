"""AWM-US-M64 washer-dryers: the -24 board's sound settings (issue #784) and
both boards' dry surface and phase progress (issue #786)."""

from datetime import UTC, datetime

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


# The combo's dry surface and phase progress (issue #786). The -25 dump was
# taken mid-cycle (spinning), the -24 one idle.
_COMBOS = ("washer_dryer_awm_m64", "washer_dryer_awm_m64_25")


def _state(name):
    resources = _load_device(name)
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    assert reg is not None
    unbound: list[str] = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return resources, bound, unbound, flatten(bound, resources)


@pytest.mark.parametrize("name", _COMBOS)
def test_combo_no_unbound_hrefs(name):
    _, _, unbound, _ = _state(name)
    assert unbound == []


def test_running_combo_dry_surface_and_phases():
    _, _, _, state = _state("washer_dryer_awm_m64_25")
    assert state["combo_dry_temperature"] == "Medium"
    assert state["dry_time"] == "00:00:00"
    assert state["wrinkle_prevent"] is False
    assert state["wrinkle_prevent_active"] is False
    assert state["wash_progress_percentage"] == 70
    assert state["dry_progress_percentage"] == 0
    assert state["wash_remaining_minutes"] == 27
    assert state["combo_dry_clean_cycles_remaining"] == 30
    assert state["combo_dry_clean_last_cleaned"] is None  # DrumComboDryCleanLog_Empty


def test_idle_combo_zeroes_phase_progress_and_reads_dry_clean_log():
    _, _, _, state = _state("washer_dryer_awm_m64")
    assert state["machine_state"] == "idle"
    assert state["wash_progress_percentage"] == 0
    assert state["dry_progress_percentage"] == 0
    # 30 proposed, WashingComboDryingTimes_01.
    assert state["combo_dry_clean_cycles_remaining"] == 29
    assert state["combo_dry_clean_last_cleaned"] == datetime(2025, 1, 12, 4, 23, 21, tzinfo=UTC)
    # The -24 board reports no comboDryTemperature or remainingWashTime.
    assert "combo_dry_temperature" not in state
    assert "wash_remaining_minutes" not in state


@pytest.mark.parametrize(
    ("key", "value", "payload"),
    [
        ("combo_dry_temperature", "High", {"x.com.samsung.da.comboDryTemperature": "High"}),
        ("dry_time", "00:30:00", {"x.com.samsung.da.dryTime": "00:30:00"}),
        ("wrinkle_prevent", "On", {"x.com.samsung.da.wrinklePrevent": "On"}),
    ],
)
def test_combo_dry_writes(key, value, payload):
    resources, bound, _, _ = _state("washer_dryer_awm_m64_25")
    desc = next(b.desc for b in bound if b.desc.key == key)
    write_fn = getattr(desc, "write_fn", None)
    assert write_fn is not None
    assert write_fn(value, resources["/washer/vs/0"]) == (["washer", "vs", "0"], payload)


@pytest.mark.parametrize("name", ["washer", "washer_wf80h"])
def test_plain_washer_gets_no_combo_entities(name):
    """The WF80H reports progressPercentageWashing and remainingWashTime
    with no dry phase to split them from."""
    _, _, _, state = _state_plain(name)
    keys = set(state)
    combo_only = {
        "combo_dry_temperature",
        "dry_time",
        "wrinkle_prevent",
        "wrinkle_prevent_active",
        "wash_progress_percentage",
        "dry_progress_percentage",
        "wash_remaining_minutes",
        "combo_dry_clean_cycles_remaining",
        "combo_dry_clean_last_cleaned",
    }
    assert not keys & combo_only


def _state_plain(name):
    resources = _load_device(name)
    reg = resolve(resources)
    assert reg is not None and reg.name == "washer"
    unbound: list[str] = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return resources, bound, unbound, flatten(bound, resources)
