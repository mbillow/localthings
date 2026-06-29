"""Capabilities for the refrigerator family (Samsung RF9000B-class).

Resources verified against the dump at local-tools/dumps/10.0.0.254.json.

Temperature fields in all /temperature/* resources are in Fahrenheit on this
model. Setpoints are NumberDesc with direct-write write_fn (no RMW needed
because the individual /temperature/desired/* resources take a PUT with just
the 'temperature' field, unlike the aggregated /temperatures/vs/0 path).

Multi-instance note: the two door resources (/door/cooler/0 and
/door/freezer/0) and the two ice-maker resources (/icemaker/one/vs/0 and
/icemaker/two/vs/0) use named path segments, so they are modelled as separate
Capability objects with distinct entity keys rather than a single capability
replicated via instance_suffix.
"""
from ..capability import Capability
from ..entities import (
    BinarySensorDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc,
)

# Setpoint bounds (from /temperatures/vs/0 items on 2026-06-27)
FREEZER_MIN_F = -8.0
FREEZER_MAX_F = 5.0
FRIDGE_MIN_F  = 34.0
FRIDGE_MAX_F  = 44.0

# Whiskey ball ice sizes.
ICE2_MODES = ('Off', 'WHISKEY_ICEBALL_3', 'WHISKEY_ICEBALL_6', 'WHISKEY_ICEBALL_9')

# Beverage zone flex modes.
BZONE_MODES = ('SP_TTYPE_BEER_DRINKS', 'SP_TTYPE_WINE_DESSERT')


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Temperatures
# ---------------------------------------------------------------------------

def _fridge_setpoint_write(p, rep):
    try:
        temp = int(round(float(p)))
    except (TypeError, ValueError):
        return None
    if not (FRIDGE_MIN_F <= temp <= FRIDGE_MAX_F):
        return None
    return ['temperature', 'desired', 'cooler', '0'], {'temperature': temp}


def _freezer_setpoint_write(p, rep):
    try:
        temp = int(round(float(p)))
    except (TypeError, ValueError):
        return None
    if not (FREEZER_MIN_F <= temp <= FREEZER_MAX_F):
        return None
    return ['temperature', 'desired', 'freezer', '0'], {'temperature': temp}


TEMP_FRIDGE_CURRENT = Capability(
    href='/temperature/current/cooler/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='fridge_temp_f', field='temperature',
                   name='Fridge temperature', device_class='temperature',
                   state_class='measurement', unit='°F', value_fn=_int),
    ),
)

TEMP_FREEZER_CURRENT = Capability(
    href='/temperature/current/freezer/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='freezer_temp_f', field='temperature',
                   name='Freezer temperature', device_class='temperature',
                   state_class='measurement', unit='°F', value_fn=_int),
    ),
)

TEMP_FRIDGE_SETPOINT = Capability(
    href='/temperature/desired/cooler/0',
    poll_tier='warm',
    entities=(
        NumberDesc(key='fridge_setpoint_f', field='temperature',
                   name='Fridge setpoint', device_class='temperature',
                   unit='°F', native_min=FRIDGE_MIN_F, native_max=FRIDGE_MAX_F,
                   step=1.0, icon='mdi:thermometer-chevron-up',
                   value_fn=_int, write_fn=_fridge_setpoint_write),
    ),
)

TEMP_FREEZER_SETPOINT = Capability(
    href='/temperature/desired/freezer/0',
    poll_tier='warm',
    entities=(
        NumberDesc(key='freezer_setpoint_f', field='temperature',
                   name='Freezer setpoint', device_class='temperature',
                   unit='°F', native_min=FREEZER_MIN_F, native_max=FREEZER_MAX_F,
                   step=1.0, icon='mdi:thermometer-chevron-down',
                   value_fn=_int, write_fn=_freezer_setpoint_write),
    ),
)

# ---------------------------------------------------------------------------
# Doors
# ---------------------------------------------------------------------------

DOOR_FRIDGE = Capability(
    href='/door/cooler/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(key='door_fridge_open', field='openState',
                         name='Fridge door', device_class='door',
                         value_fn=lambda v: v == 'Open'),
    ),
)

DOOR_FREEZER = Capability(
    href='/door/freezer/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(key='door_freezer_open', field='openState',
                         name='Freezer door', device_class='door',
                         value_fn=lambda v: v == 'Open'),
    ),
)

DOORS_STATUS = Capability(
    href='/doors/vs/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(
            key='any_door_open',
            field='x.com.samsung.da.items',
            name='Any door open',
            device_class='door',
            value_fn=lambda items: any(
                it.get('x.com.samsung.da.openState') == 'Open'
                for it in (items or [])
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Ice makers
# ---------------------------------------------------------------------------

def _ice1_write(p, rep):
    if p not in ('On', 'Off'):
        return None
    return ['icemaker', 'one', 'vs', '0'], {'x.com.samsung.da.iceMaker.state': p}


def _ice2_type_write(p, rep):
    if p not in ICE2_MODES:
        return None
    return ['icemaker', 'two', 'vs', '0'], {'x.com.samsung.da.iceType.desired': p}


ICEMAKER_ONE = Capability(
    href='/icemaker/one/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='ice1_state', field='x.com.samsung.da.iceMaker.state',
                   name='Ice maker (cubed) state', icon='mdi:cube'),
        BinarySensorDesc(key='ice1_on', field='x.com.samsung.da.iceMaker.state',
                         name='Ice maker (cubed) on',
                         device_class='running',
                         value_fn=lambda v: v == 'On'),
        SensorDesc(key='ice1_making_status',
                   field='x.com.samsung.da.iceMaker.iceMakingStatus',
                   name='Ice maker (cubed) status', icon='mdi:cube'),
        SwitchDesc(key='ice1', field='x.com.samsung.da.iceMaker.state',
                   name='Ice maker (cubed)', icon='mdi:cube',
                   value_fn=lambda v: v == 'On',
                   write_fn=_ice1_write),
    ),
)

ICEMAKER_TWO = Capability(
    href='/icemaker/two/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='ice2_making_status',
                   field='x.com.samsung.da.iceMaker.iceMakingStatus',
                   name='Ice maker (whiskey) status', icon='mdi:circle'),
        SelectDesc(key='ice2_type', field='x.com.samsung.da.iceType.desired',
                   name='Whiskey ball ice type', icon='mdi:circle',
                   options=ICE2_MODES, write_fn=_ice2_type_write),
    ),
)

ICEMAKER_STATUS = Capability(
    href='/icemaker/status/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='ice_maker_enabled', field='x.com.samsung.da.iceMaker',
                   name='Ice maker enabled', icon='mdi:snowflake'),
    ),
)

# ---------------------------------------------------------------------------
# Refrigeration modes (rapid cooling)
# ---------------------------------------------------------------------------

def _refrigeration_write(field_name):
    def _write(p, rep):
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
                   value_fn=lambda v: v == 'On',
                   write_fn=_refrigeration_write('x.com.samsung.da.rapidFridge')),
        SwitchDesc(key='rapid_freezing', field='x.com.samsung.da.rapidFreezing',
                   name='Rapid freezing', icon='mdi:snowflake',
                   value_fn=lambda v: v == 'On',
                   write_fn=_refrigeration_write('x.com.samsung.da.rapidFreezing')),
    ),
)

# ---------------------------------------------------------------------------
# Autofill
# ---------------------------------------------------------------------------

def _autofill_write(p, rep):
    if p not in ('On', 'Off'):
        return None
    return ['autofill', 'vs', '0'], {'x.com.samsung.da.autofill': p}


AUTOFILL = Capability(
    href='/autofill/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='autofill', field='x.com.samsung.da.autofill',
                   name='Autofill', icon='mdi:cup-water',
                   value_fn=lambda v: v == 'On',
                   write_fn=_autofill_write),
    ),
)

# ---------------------------------------------------------------------------
# Cabinet light
# ---------------------------------------------------------------------------

def _cabinet_light_write(p, rep):
    if p not in ('On', 'Off'):
        return None
    return ['cabinet', 'light', 'total', 'vs', '0'], {
        'x.com.samsung.da.lightControl': p,
    }


CABINET_LIGHT = Capability(
    href='/cabinet/light/total/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='cabinet_light', field='x.com.samsung.da.lightControl',
                   name='Cabinet light level', icon='mdi:fridge-outline'),
        BinarySensorDesc(key='cabinet_light_on', field='x.com.samsung.da.lightControl',
                         name='Cabinet light on', icon='mdi:fridge-outline',
                         value_fn=lambda v: v == 'On'),
        SwitchDesc(key='cabinet_light_switch', field='x.com.samsung.da.lightControl',
                   name='Cabinet light', icon='mdi:fridge-outline',
                   value_fn=lambda v: v == 'On',
                   write_fn=_cabinet_light_write),
    ),
)

# ---------------------------------------------------------------------------
# Sabbath mode
# ---------------------------------------------------------------------------

def _sabbath_write(p, rep):
    if p not in ('On', 'Off'):
        return None
    return ['sabbath', 'vs', '0'], {'x.com.samsung.da.sabbathMode': p}


SABBATH = Capability(
    href='/sabbath/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='sabbath_mode', field='x.com.samsung.da.sabbathMode',
                   name='Sabbath mode', icon='mdi:star-david',
                   value_fn=lambda v: v == 'On',
                   write_fn=_sabbath_write),
    ),
)

# ---------------------------------------------------------------------------
# Beverage zone
# ---------------------------------------------------------------------------

def _bzone_write(p, rep):
    if p not in BZONE_MODES:
        return None
    return ['specialzone', 'one', 'vs', '0'], {'roomDesiredMode': p}


BEVERAGE_ZONE = Capability(
    href='/specialzone/one/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='beverage_zone_mode', field='roomDesiredMode',
                   name='Beverage zone mode', icon='mdi:glass-wine',
                   options=BZONE_MODES, write_fn=_bzone_write),
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

def _door_key(href: str) -> str:
    # /door/cooler/0 -> door_cooler_open ; /door/wine/0 -> door_wine_open
    segs = [s for s in href.strip('/').split('/') if s and not s.isdigit()]
    return '_'.join(segs) + '_open'


DOOR_GENERIC = Capability(
    href=None,
    href_prefix='/door/',
    poll_tier='hot',
    key_fn=_door_key,
    entities=(
        BinarySensorDesc(key='door_open', field='openState',
                         name=None, device_class='door',
                         value_fn=lambda v: v == 'Open'),
    ),
)
