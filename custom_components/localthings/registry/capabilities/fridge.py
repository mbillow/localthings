"""Capabilities for the refrigerator family (Samsung RF9000B-class).

Resources verified against the dump at local-tools/dumps/10.0.0.254.json.

Temperature unit is read live from each resource, not assumed: the RF9000B
dump reports Fahrenheit ("units": "F" / "x.com.samsung.da.unit": "Fahrenheit"),
but a TP1X_REF_21K dump (issue #7) reports the same fields in Celsius for the
exact same resources — the device tells you which one it is, it's just never
been read before. See `_temp_unit`/`_temp_item_unit` below. Setpoints are
NumberDesc with direct-write write_fn — generic caps derive the CoAP PUT path
from href at write time.

Multi-instance note: the two door resources (/door/cooler/0 and
/door/freezer/0) and the two ice-maker resources (/icemaker/one/vs/0 and
/icemaker/two/vs/0) use named path segments, so they are modelled via
pattern capabilities that auto-derive distinct entity keys from href segments.
"""
import datetime

from ..capability import Capability
from ..entities import (
    BinarySensorDesc, ButtonDesc, NumberDesc, SelectDesc, SensorDesc,
    SwitchDesc, TimeDesc,
)
from .common import normalize_temp_unit

# Display names for the beverage zone, flex zone, ice type, and
# ice-making-status enums below live in strings.json / translations/en.json,
# keyed by the lowercased raw device value — select.py and SensorDesc.options
# normalize to lowercase for HA's translation lookup and map back to this
# original casing before writing to the device.


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _temp_unit(rep):
    """'units': 'C'/'F' (or 'Celsius'/'Fahrenheit') -> '°C'/'°F'. Defaults to
    °F (this module's original assumption) if the device omits the field."""
    return normalize_temp_unit(rep.get('units'))


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
                   device_class='temperature', unit_fn=_temp_unit,
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
                   name=None, device_class='temperature', unit_fn=_temp_unit,
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
                   device_class='enum',
                   options=('icestatus_stop', 'icestatus_run'),
                   translation_key='ice_making_status',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
        SwitchDesc(key='enabled', field='x.com.samsung.da.iceMaker.state',
                   name=None, icon='mdi:cube-outline',
                   value_fn=lambda v: v == 'On',
                   write_fn=_icemaker_write('x.com.samsung.da.iceMaker.state')),
        SelectDesc(key='type', field='x.com.samsung.da.iceType.desired',
                   name=None, icon='mdi:cube-outline',
                   translation_key='ice_type',
                   entity_category='config',
                   options_field='x.com.samsung.da.iceType.supported',
                   exists_fn=lambda rep, resources: bool(rep.get('x.com.samsung.da.iceType.supported')),
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
# Defrost delay / active-defrost status
#
# /defrost/delay/vs/0 is the writable toggle to postpone a scheduled
# defrost. /defrost/block/vs/0 is an unrelated, independently-varying
# status: despite its "block" naming (originally assumed to mean "defrost
# is being withheld"), live dumps confirm DEFROST_BLOCK_ON means the
# defrost cycle is *actively running* right now, seen with defrost_delay
# off -- i.e. "block" refers to the evaporator/coil block being defrosted,
# not a blocking/prevention state. Exposed as a read-only diagnostic
# binary sensor.
# ---------------------------------------------------------------------------

DEFROST_DELAY = Capability(
    href='/defrost/delay/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='defrost_delay', field='x.com.samsung.da.delayDefrost',
                   name='Defrost delay', icon='mdi:snowflake-off',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['defrost', 'delay', 'vs', '0'],
                       {'x.com.samsung.da.delayDefrost': 'On' if p else 'Off'})),
    ),
)

# OCF-native boolean mirror of DEFROST_DELAY.  The captured TP1X_REF_21K
# firmware publishes the same state on both hrefs, but only the vendor resource
# above has a confirmed write contract.  Bind the native mirror without another
# entity so discovery records it as an intentional duplicate.
DEFROST_DELAY_NATIVE_DUPLICATE = Capability(
    href='/defrost/delay/0',
)

DEFROST_BLOCK_STATUS = Capability(
    href='/defrost/block/vs/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(key='defrost_active', field='x.com.samsung.da.modes',
                         name='Defrost active', icon='mdi:snowflake-melt',
                         entity_category='diagnostic',
                         value_fn=lambda modes: bool(modes) and modes[0] == 'DEFROST_BLOCK_ON'),
    ),
)

# ---------------------------------------------------------------------------
# Self-check diagnostic
# ---------------------------------------------------------------------------

SELF_CHECK = Capability(
    href='/selfcheck/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='selfcheck_status', field='x.com.samsung.da.status',
                   name='Self-check status', icon='mdi:stethoscope',
                   entity_category='diagnostic'),
        SensorDesc(key='selfcheck_result', field='x.com.samsung.da.result',
                   name='Self-check result', icon='mdi:clipboard-check-outline',
                   entity_category='diagnostic'),
        # List of error codes from the last self-check; joined for display.
        # Not every fridge reports the field, hence the exists_fn.
        SensorDesc(key='selfcheck_error', field='x.com.samsung.da.error',
                   name='Self-check error', icon='mdi:alert-circle-outline',
                   entity_category='diagnostic',
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.error' in rep),
                   value_fn=lambda v: (', '.join(v) if v else None) if isinstance(v, list) else v),
        ButtonDesc(key='selfcheck_start', field='', name='Start self-check',
                   payload='Start', icon='mdi:play-circle-outline',
                   entity_category='diagnostic',
                   write_fn=lambda p, rep, href=None: (
                       ['selfcheck', 'vs', '0'], {'x.com.samsung.da.status': p})),
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
    return ['specialzone', 'one', 'vs', '0'], {'roomDesiredMode': p}


BEVERAGE_ZONE = Capability(
    href='/specialzone/one/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='beverage_zone_mode', field='roomDesiredMode',
                   name='Beverage zone mode', icon='mdi:glass-wine',
                   translation_key='beverage_zone_mode',
                   entity_category='config',
                   options_field='roomSupportedModes',
                   write_fn=_bzone_write),
    ),
)

# ---------------------------------------------------------------------------
# Pantry / Cool Select Zone -- a convertible compartment toggled between
# wine/deli/drinks temperature presets (issue #20). Same shape as
# BEVERAGE_ZONE (a controllable named sub-zone with a mode + supported-modes
# list) but a distinct resource/field set -- x.com.samsung.da.mode /
# x.com.samsung.da.supportedOptions on /status/pantry/one/vs/0, rather than
# roomDesiredMode/roomSupportedModes on /specialzone/one/vs/0. Only a "one"
# instance has been seen; not generalized to a pattern cap until a second
# instance turns up.
# ---------------------------------------------------------------------------

def _pantry_write(p, rep, href=None):
    return ['status', 'pantry', 'one', 'vs', '0'], {'x.com.samsung.da.mode': p}


PANTRY_ZONE = Capability(
    href='/status/pantry/one/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='pantry_zone_mode', field='x.com.samsung.da.mode',
                   name='Pantry zone mode', icon='mdi:glass-wine',
                   translation_key='pantry_zone_mode',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedOptions',
                   write_fn=_pantry_write),
    ),
)

# ---------------------------------------------------------------------------
# Flex zone (convertible drawer — /mode/vs/0 on RF9000-class fridges)
#
# x.com.samsung.da.modes holds multiple orthogonal flags in one list; the
# flex-zone entry is whichever item is also a member of supportedOptions --
# the other flags (WATERFILTER_*, DEFROST_BLOCK_*, the CVN_*_ZONE marker)
# never appear there. The prefix on that item varies by fridge family
# (CV_TTYPE_RF9000A_ on RF9000-class, CV_FDR_ on Bespoke-class -- issue #27 /
# #26, where the old CV_TTYPE_RF9000A_-only match left this entity bound but
# stuck on None), so match by list membership instead of a hardcoded prefix.
# Write replaces only that item; other flags are preserved.
# ---------------------------------------------------------------------------

def _flex_zone_supported(rep):
    return set(rep.get('x.com.samsung.da.supportedOptions') or ())


def _flex_zone_current(rep):
    # Every dump seen has at most one modes/supportedOptions overlap, so
    # "first match" and "strip all matches" (in the write below) agree. If a
    # future device ever reports two, this reads the first and the write
    # would drop both -- revisit if that turns up.
    modes = rep.get('x.com.samsung.da.modes') or []
    supported = _flex_zone_supported(rep)
    return next((m for m in modes if m in supported), None)


def _flex_zone_write(p, rep, href=None):
    supported = _flex_zone_supported(rep)
    modes = [m for m in (rep.get('x.com.samsung.da.modes') or []) if m not in supported]
    modes.append(p)
    return ['mode', 'vs', '0'], {'x.com.samsung.da.modes': modes}


FLEX_ZONE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='flex_zone_mode',
                   name='Flex zone mode', icon='mdi:thermostat',
                   translation_key='flex_zone_mode',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedOptions',
                   exists_fn=lambda rep, resources: bool(
                       rep.get('x.com.samsung.da.supportedOptions')),
                   rep_fn=_flex_zone_current,
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

# ---------------------------------------------------------------------------
# Aggregate-resource fallbacks
#
# /doors/vs/0, /temperatures/vs/0, and /icemaker/status/vs/0 each duplicate
# information exposed more precisely by per-instance hrefs (DOOR_GENERIC,
# TEMP_CURRENT_GENERIC/TEMP_SETPOINT_GENERIC, ICEMAKER_GENERIC) on hardware
# that has them. Not every fridge does — a simpler model may only ever
# advertise the aggregate resource. Each fallback's match_fn checks the
# full resource set for the richer sibling hrefs and only binds when
# they're absent, so it's a no-op (not a gap — see discovery.py) wherever
# the richer hrefs exist, and a real (if coarser) source of the same data
# where they don't.
# ---------------------------------------------------------------------------

def _any_door_generic(resources):
    return any(h.startswith('/door/') for h in resources)


DOORS_FALLBACK = Capability(
    href='/doors/vs/0',
    match_fn=lambda rep, resources: not _any_door_generic(resources),
    poll_tier='hot',
    entities=(
        BinarySensorDesc(key='door_open', field='x.com.samsung.da.items',
                         name='Door', device_class='door',
                         value_fn=lambda items: any(
                             i.get('x.com.samsung.da.openState') == 'Open'
                             for i in (items or []))),
    ),
)


def _any_temperature_generic(resources):
    return any(h.startswith('/temperature/') for h in resources)


def _temp_item_value(items, keyword):
    for item in (items or []):
        if keyword.lower() in (item.get('x.com.samsung.da.description') or '').lower():
            return _int(item.get('x.com.samsung.da.current'))
    return None


def _temp_item_unit(items, keyword):
    for item in (items or []):
        if keyword.lower() in (item.get('x.com.samsung.da.description') or '').lower():
            return normalize_temp_unit(item.get('x.com.samsung.da.unit'))
    return '°F'


TEMPERATURES_FALLBACK = Capability(
    href='/temperatures/vs/0',
    match_fn=lambda rep, resources: not _any_temperature_generic(resources),
    poll_tier='warm',
    entities=(
        SensorDesc(key='freezer_temperature', field='x.com.samsung.da.items',
                   name='Freezer temperature', icon='mdi:thermometer',
                   device_class='temperature', state_class='measurement',
                   unit_fn=lambda rep: _temp_item_unit(
                       rep.get('x.com.samsung.da.items'), 'Freezer'),
                   value_fn=lambda items: _temp_item_value(items, 'Freezer')),
        SensorDesc(key='fridge_temperature', field='x.com.samsung.da.items',
                   name='Fridge temperature', icon='mdi:thermometer',
                   device_class='temperature', state_class='measurement',
                   unit_fn=lambda rep: _temp_item_unit(
                       rep.get('x.com.samsung.da.items'), 'Fridge'),
                   value_fn=lambda items: _temp_item_value(items, 'Fridge')),
    ),
)


def _any_icemaker_unit_generic(resources):
    return any(
        h.startswith('/icemaker/') and isinstance(r, dict)
        and 'x.com.samsung.da.iceMaker.state' in r
        for h, r in resources.items()
    )


ICEMAKER_STATUS_FALLBACK = Capability(
    href='/icemaker/status/vs/0',
    match_fn=lambda rep, resources: not _any_icemaker_unit_generic(resources),
    poll_tier='warm',
    entities=(
        SwitchDesc(key='ice_maker_enabled', field='x.com.samsung.da.iceMaker',
                   name='Ice maker', icon='mdi:cube-outline',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['icemaker', 'status', 'vs', '0'],
                       {'x.com.samsung.da.iceMaker': 'On' if p else 'Off'})),
    ),
)

# OCF-native aggregate mirror of ICEMAKER_STATUS_FALLBACK.  On the captured
# TP1X_REF_21K it duplicates both the vendor aggregate and the richer per-unit
# /icemaker/one|two/vs/0 resources.  Its write contract is not advertised, so
# keep the proven per-unit/vendor controls and bind this as a duplicate only.
ICEMAKER_STATUS_NATIVE_DUPLICATE = Capability(
    href='/icemaker/status/0',
)

# OCF-native /refrigeration/0 (issue #7's unbound_hrefs) -- the odd one out
# in this section: its three fields duplicate two *different* richer
# hrefs (REFRIGERATION's rapidFridge/rapidFreezing and
# DEFROST_BLOCK_STATUS's defrost_active), each absent independently, so a
# single capability-level match_fn can't express it. Gated per-entity
# (exists_fn) instead: rapid_fridge/rapid_freezing back off only when
# REFRIGERATION's href is present; defrost_active only when
# DEFROST_BLOCK_STATUS's is. No write path confirmed for this href, so
# these are read-only, unlike REFRIGERATION's switches.
REFRIGERATION_FALLBACK = Capability(
    href='/refrigeration/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(key='defrost_active', field='defrost',
                         name='Defrost active', icon='mdi:snowflake-melt',
                         entity_category='diagnostic',
                         value_fn=lambda v: bool(v),
                         exists_fn=lambda rep, resources: '/defrost/block/vs/0' not in resources),
        BinarySensorDesc(key='rapid_fridge', field='rapidCool',
                         name='Rapid fridge', icon='mdi:fridge-industrial',
                         entity_category='config',
                         value_fn=lambda v: bool(v),
                         exists_fn=lambda rep, resources: '/refrigeration/vs/0' not in resources),
        BinarySensorDesc(key='rapid_freezing', field='rapidFreeze',
                         name='Rapid freezing', icon='mdi:snowflake',
                         entity_category='config',
                         value_fn=lambda v: bool(v),
                         exists_fn=lambda rep, resources: '/refrigeration/vs/0' not in resources),
    ),
)
