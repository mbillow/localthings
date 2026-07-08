"""Write-handler unit tests for laundry-family capabilities."""
from custom_components.localthings.registry.capabilities import laundry
from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE


def test_start_button_writes_run():
    btn = next(e for e in OPERATIONAL_STATE.entities if e.key == 'start')
    assert btn.write_fn('Run', {}) == (
        ['operational', 'state', 'vs', '0'],
        {'x.com.samsung.da.state': 'Run'},
    )


def test_pause_button_writes_pause():
    btn = next(e for e in OPERATIONAL_STATE.entities if e.key == 'pause')
    assert btn.write_fn('Pause', {}) == (
        ['operational', 'state', 'vs', '0'],
        {'x.com.samsung.da.state': 'Pause'},
    )


def test_stop_button_writes_ready():
    btn = next(e for e in OPERATIONAL_STATE.entities if e.key == 'stop')
    assert btn.write_fn('Ready', {}) == (
        ['operational', 'state', 'vs', '0'],
        {'x.com.samsung.da.state': 'Ready'},
    )


def test_sound_mode_write_valid():
    desc = next(e for e in laundry.SOUND_MODE.entities if e.key == 'sound_mode')
    assert desc.write_fn('mute', {}) == (
        ['settings', 'sound', 'mode', 'vs', '0'],
        {'mode': 'mute'},
    )
    assert desc.write_fn('invalid', {}) is None


def test_led_brightness_write_valid():
    desc = next(e for e in laundry.DOOR_LED.entities if e.key == 'led_brightness')
    assert desc.write_fn('Low', {}) == (
        ['doorled', 'light', 'vs', '0'],
        {'setBrightness': 'Low'},
    )
    assert desc.write_fn('Medium', {}) is None


def test_led_night_light_write_valid():
    desc = next(e for e in laundry.DOOR_LED.entities if e.key == 'led_night_light')
    assert desc.write_fn('On', {}) == (
        ['doorled', 'light', 'vs', '0'],
        {'setNightLight': 'On'},
    )
    assert desc.write_fn('Off', {}) == (
        ['doorled', 'light', 'vs', '0'],
        {'setNightLight': 'Off'},
    )
    assert desc.write_fn('invalid', {}) is None
