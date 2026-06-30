"""Capabilities for the refrigerator family (Samsung RF9000B-class).

Resources verified against the dump at local-tools/dumps/10.0.0.254.json.

Temperature fields in all /temperature/* resources are in Fahrenheit on this
model. Setpoints are NumberDesc with direct-write write_fn — generic caps
derive the CoAP PUT path from href at write time.

Multi-instance note: the two door resources (/door/cooler/0 and
/door/freezer/0) and the two ice-maker resources (/icemaker/one/vs/0 and
/icemaker/two/vs/0) use named path segments, so they are modelled via
pattern capabilities that auto-derive distinct entity keys from href segments.
"""
import datetime

from ..capability import Capability
from ..entities import (
    BinarySensorDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc, TimeDesc,
)

# Beverage zone flex modes.
_BZONE_MODES = ('SP_TTYPE_BEER_DRINKS', 'SP_TTYPE_WINE_DESSERT')


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Temperature (generic — covers /temperature/current/* and /temperature/desired/*)
# ---------------------------------------------------------------------------

TEMP_CURRENT_GENERIC = Capability(
    href=None,
    href_prefix='/temperature/current/',
    strip_prefix_in_key=True,
    poll_tier='warm',
    entities=(
        SensorDesc(key='temperature', field='temperature',
                   name=None, icon='mdi:thermometer',
                   device_class='temperature', unit='°F',
                   state_class='measurement'),
    ),
)

TEMP_SETPOINT_GENERIC = Capability(
    href=None,
    href_prefix='/temperature/desired/',
    strip_prefix_in_key=True,
    poll_tier='warm',
    entities=(
        NumberDesc(key='setpoint', field='temperature',
                   name=None, device_class='temperature', unit='°F',
                   native_min=-20.0, native_max=50.0,
                   range_field='range', entity_category='config',
                   write_fn=lambda p, rep, href=None: (
                       [s for s in href.strip('/').split('/') if s],
                       {'temperature': int(round(float(p)))}
                   ) if href else None),
    ),
)

# ---------------------------------------------------------------------------
# Icemaker nighttime quiet mode
# ---------------------------------------------------------------------------

ICEMAKER_NIGHTTIME = Capability(
    href='/icemaker/nighttime/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='ice_night_mode', field='ice.night.status',
                   name='Nighttime ice quiet mode', icon='mdi:weather-night',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['icemaker', 'nighttime', 'vs', '0'],
                       {'ice.night.status': 'On' if p else 'Off'})),
    ),
)

# ---------------------------------------------------------------------------
# Icemaker (generic — covers /icemaker/one/vs/0, /icemaker/two/vs/0)
# /icemaker/status/vs/0 is kept as exact-href cap and binds first.
# /icemaker/nighttime/vs/0 is excluded by match_fn (lacks iceMaker.state).
# ---------------------------------------------------------------------------

def _icemaker_write(field):
    return lambda p, rep, href=None: (
        [s for s in href.strip('/').split('/') if s],
        {field: p}
    ) if href else None

ICEMAKER_GENERIC = Capability(
    href=None,
    href_prefix='/icemaker/',
    match_fn=lambda rep, resources: 'x.com.samsung.da.iceMaker.state' in rep,
    poll_tier='warm',
    entities=(
        SensorDesc(key='making_status',
                   field='x.com.samsung.da.iceMaker.iceMakingStatus',
                   name=None, icon='mdi:cube-outline',
                   translation_key='ice_making_status'),
        SwitchDesc(key='enabled', field='x.com.samsung.da.iceMaker.state',
                   name=None, icon='mdi:cube-outline',
                   value_fn=lambda v: v == 'On',
                   write_fn=_icemaker_write('x.com.samsung.da.iceMaker.state')),
        SelectDesc(key='type', field='x.com.samsung.da.iceType.desired',
                   name=None, icon='mdi:cube-outline',
                   translation_key='ice_type',
                   entity_category='config',
                   options_field='x.com.samsung.da.iceType.supported',
                   exists_fn=lambda rep: bool(rep.get('x.com.samsung.da.iceType.supported')),
                   write_fn=_icemaker_write('x.com.samsung.da.iceType.desired')),
    ),
)

# ---------------------------------------------------------------------------
# Door alert tone
# ---------------------------------------------------------------------------

DOOR_ALERT = Capability(
    href='/settings/sound/alert/door/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='door_alert', field='alert.door',
                   name='Door alarm', icon='mdi:bell-alert',
                   translation_key='door_alert',
                   entity_category='config',
                   options_field='supportedAlert.door',
                   write_fn=lambda p, rep, href=None: (
                       ['settings', 'sound', 'alert', 'door', 'vs', '0'],
                       {'alert.door': p})),
    ),
)

# ---------------------------------------------------------------------------
# Status/lock — auto door opener and fridge sound
# ---------------------------------------------------------------------------

def _status_lock_write(field):
    return lambda p, rep, href=None: (
        ['status', 'lock', 'vs', '0'],
        {field: 'On' if p else 'Off'}
    )


STATUS_LOCK = Capability(
    href='/status/lock/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='auto_door_opener', field='x.com.samsung.da.ado.devicecontrol',
                   name='Auto door opener', icon='mdi:door-open',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_status_lock_write('x.com.samsung.da.ado.devicecontrol')),
        SwitchDesc(key='fridge_sound', field='x.com.samsung.da.device.sound',
                   name='Sound', icon='mdi:volume-high',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_status_lock_write('x.com.samsung.da.device.sound')),
    ),
)

# ---------------------------------------------------------------------------
# Refrigeration modes (rapid cooling)
# ---------------------------------------------------------------------------

def _refrigeration_write(field_name):
    def _write(p, rep, href=None):
        if p not in ('On', 'Off'):
            return None
        return ['refrigeration', 'vs', '0'], {field_name: p}
    return _write


REFRIGERATION = Capability(
    href='/refrigeration/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='rapid_fridge', field='x.com.samsung.da.rapidFridge',
                   name='Rapid fridge', icon='mdi:fridge-industrial',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_refrigeration_write('x.com.samsung.da.rapidFridge')),
        SwitchDesc(key='rapid_freezing', field='x.com.samsung.da.rapidFreezing',
                   name='Rapid freezing', icon='mdi:snowflake',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_refrigeration_write('x.com.samsung.da.rapidFreezing')),
    ),
)

# ---------------------------------------------------------------------------
# Autofill
# ---------------------------------------------------------------------------

def _autofill_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['autofill', 'vs', '0'], {'x.com.samsung.da.autofill': p}


AUTOFILL = Capability(
    href='/autofill/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='autofill', field='x.com.samsung.da.autofill',
                   name='Autofill pitcher', icon='mdi:cup-water',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_autofill_write),
    ),
)

# ---------------------------------------------------------------------------
# Welcome lighting (proximity-triggered cabinet light)
# ---------------------------------------------------------------------------

WELCOME_LIGHTING = Capability(
    href='/proximity/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='welcome_lighting', field='status',
                   name='Welcome lighting', icon='mdi:motion-sensor',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['proximity', 'vs', '0'],
                       {'status': 'On' if p else 'Off'})),
    ),
)

# ---------------------------------------------------------------------------
# Enhanced cabinet light — nighttime lighting schedule
#
# night.starttime is an ISO datetime; only the time portion is meaningful.
# night.duration.minute encodes the window length.  End time is derived so
# both time entities write back to the same resource without stepping on each
# other: writing start preserves duration; writing end recalculates duration.
# ---------------------------------------------------------------------------

_NIGHT_BRIGHTNESS_OPTIONS = ('33', '66', '100')


def _tz_offset(rep) -> datetime.timedelta:
    s = rep.get('timezone.offset', '+00:00')
    try:
        sign = 1 if s[0] == '+' else -1
        h, m = map(int, s[1:].split(':'))
        return datetime.timedelta(hours=h, minutes=m) * sign
    except (ValueError, TypeError, IndexError):
        return datetime.timedelta(0)


def _parse_night_time(iso_str, offset=datetime.timedelta(0)) -> 'datetime.time | None':
    """Convert a UTC ISO datetime string to local time using offset."""
    if not iso_str:
        return None
    try:
        return (datetime.datetime.fromisoformat(iso_str) + offset).time().replace(second=0)
    except (ValueError, TypeError):
        return None


def _night_start_value(rep) -> 'datetime.time | None':
    return _parse_night_time(rep.get('night.starttime'), _tz_offset(rep))


def _night_end_value(rep) -> 'datetime.time | None':
    start_t = _parse_night_time(rep.get('night.starttime'), _tz_offset(rep))
    duration_str = rep.get('night.duration.minute')
    if start_t is None or duration_str is None:
        return None
    try:
        duration = int(duration_str)
    except (ValueError, TypeError):
        return None
    return (datetime.datetime.combine(datetime.date.today(), start_t)
            + datetime.timedelta(minutes=duration)).time().replace(second=0)


def _night_start_write(p, rep, href=None):
    # p is local time; subtract offset to get UTC for storage
    offset = _tz_offset(rep)
    utc_dt = datetime.datetime.combine(datetime.date.today(), p) - offset
    old_iso = rep.get('night.starttime') or ''
    try:
        new_dt = datetime.datetime.fromisoformat(old_iso).replace(
            hour=utc_dt.hour, minute=utc_dt.minute, second=0)
    except (ValueError, TypeError):
        new_dt = utc_dt
    return ['cabinet', 'light', 'enhanced', 'vs', '0'], {
        'night.starttime': new_dt.strftime('%Y-%m-%dT%H:%M:%S'),
    }


def _night_end_write(p, rep, href=None):
    # duration = end_local - start_local; offset cancels so work in local time
    start_t = _parse_night_time(rep.get('night.starttime'), _tz_offset(rep))
    if start_t is None:
        return None
    start_min = start_t.hour * 60 + start_t.minute
    end_min = p.hour * 60 + p.minute
    duration = (end_min - start_min) % (24 * 60)
    return ['cabinet', 'light', 'enhanced', 'vs', '0'], {
        'night.duration.minute': str(duration),
    }


CABINET_LIGHT_ENHANCED = Capability(
    href='/cabinet/light/enhanced/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='day_brightness', field='level.brightness.daytime',
                   name='Cabinet brightness', icon='mdi:brightness-5',
                   translation_key='brightness_level',
                   entity_category='config',
                   options=_NIGHT_BRIGHTNESS_OPTIONS,
                   write_fn=lambda p, rep, href=None: (
                       ['cabinet', 'light', 'enhanced', 'vs', '0'],
                       {'level.brightness.daytime': p})),
        SelectDesc(key='brightness_level', field='level.brightness.nighttime',
                   name='Night brightness', icon='mdi:brightness-4',
                   translation_key='brightness_level',
                   entity_category='config',
                   options=_NIGHT_BRIGHTNESS_OPTIONS,
                   write_fn=lambda p, rep, href=None: (
                       ['cabinet', 'light', 'enhanced', 'vs', '0'],
                       {'level.brightness.nighttime': p})),
        TimeDesc(key='night_start', field='',
                 name='Night light start', icon='mdi:clock-start',
                 entity_category='config',
                 rep_fn=_night_start_value,
                 write_fn=_night_start_write),
        TimeDesc(key='night_end', field='',
                 name='Night light end', icon='mdi:clock-end',
                 entity_category='config',
                 rep_fn=_night_end_value,
                 write_fn=_night_end_write),
    ),
)

# ---------------------------------------------------------------------------
# Cabinet light
# ---------------------------------------------------------------------------

def _cabinet_light_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['cabinet', 'light', 'total', 'vs', '0'], {
        'x.com.samsung.da.lightControl': p,
    }


CABINET_LIGHT = Capability(
    href='/cabinet/light/total/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='cabinet_light_switch', field='x.com.samsung.da.lightControl',
                   name='Cabinet light', icon='mdi:fridge-outline',
                   value_fn=lambda v: v == 'On',
                   write_fn=_cabinet_light_write),
        SwitchDesc(key='cabinet_light_dim', field='light.dimming.status',
                   name='Brighten gradually', icon='mdi:brightness-auto',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['cabinet', 'light', 'total', 'vs', '0'],
                       {'light.dimming.status': 'On' if p else 'Off'})),
    ),
)

# ---------------------------------------------------------------------------
# Sabbath mode
# ---------------------------------------------------------------------------

def _sabbath_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['sabbath', 'vs', '0'], {'x.com.samsung.da.sabbathMode': p}


SABBATH = Capability(
    href='/sabbath/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='sabbath_mode', field='x.com.samsung.da.sabbathMode',
                   name='Sabbath mode', icon='mdi:hands-pray',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_sabbath_write),
    ),
)

# ---------------------------------------------------------------------------
# Beverage zone
# ---------------------------------------------------------------------------

def _bzone_write(p, rep, href=None):
    if p not in _BZONE_MODES:
        return None
    return ['specialzone', 'one', 'vs', '0'], {'roomDesiredMode': p}


BEVERAGE_ZONE = Capability(
    href='/specialzone/one/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='beverage_zone_mode', field='roomDesiredMode',
                   name='Beverage zone mode', icon='mdi:glass-wine',
                   translation_key='beverage_zone_mode',
                   entity_category='config',
                   options=_BZONE_MODES, write_fn=_bzone_write),
    ),
)

# ---------------------------------------------------------------------------
# Flex zone (convertible drawer — /mode/vs/0 on RF9000-class fridges)
#
# x.com.samsung.da.modes holds multiple orthogonal flags in one list.
# The flex zone entry is identified by the CV_TTYPE_RF9000A_ prefix.
# Write replaces only that item; other flags are preserved.
# ---------------------------------------------------------------------------

def _flex_zone_write(p, rep, href=None):
    modes = list(rep.get('x.com.samsung.da.modes') or [])
    modes = [m for m in modes if not m.startswith('CV_TTYPE_RF9000A_')]
    modes.append(p)
    return ['mode', 'vs', '0'], {'x.com.samsung.da.modes': modes}


FLEX_ZONE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='flex_zone_mode',
                   field='x.com.samsung.da.modes',
                   name='Flex zone mode', icon='mdi:thermostat',
                   translation_key='flex_zone_mode',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedOptions',
                   exists_fn=lambda rep: bool(
                       rep.get('x.com.samsung.da.supportedOptions')),
                   value_fn=lambda modes: next(
                       (m for m in (modes or [])
                        if m.startswith('CV_TTYPE_RF9000A_')), None),
                   write_fn=_flex_zone_write),
    ),
)

# ---------------------------------------------------------------------------
# Firmware update
# ---------------------------------------------------------------------------

FIRMWARE_UPDATE = Capability(
    href='/otninformation/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(
            key='firmware_update',
            field='x.com.samsung.da.newVersionAvailable',
            name='Firmware update available',
            device_class='update',
            entity_category='diagnostic',
            value_fn=lambda v: str(v).lower() == 'true' if v is not None else None,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Generic door pattern capability (href=None — use as pattern_cap only)
# ---------------------------------------------------------------------------

DOOR_GENERIC = Capability(
    href=None,
    href_prefix='/door/',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(key='open', field='openState',
                         name=None, device_class='door',
                         value_fn=lambda v: v == 'Open'),
    ),
)
