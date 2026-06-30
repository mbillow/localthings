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
from ..capability import Capability
from ..entities import (
    BinarySensorDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc,
)

# Beverage zone flex modes.
BZONE_MODES = ('SP_TTYPE_BEER_DRINKS', 'SP_TTYPE_WINE_DESSERT')


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
                   range_field='range',
                   write_fn=lambda p, rep, href=None: (
                       [s for s in href.strip('/').split('/') if s],
                       {'temperature': int(round(float(p)))}
                   ) if href else None),
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
                   name=None, icon='mdi:cube-outline'),
        SwitchDesc(key='enabled', field='x.com.samsung.da.iceMaker.state',
                   name=None, icon='mdi:cube-outline',
                   value_fn=lambda v: v == 'On',
                   write_fn=_icemaker_write('x.com.samsung.da.iceMaker.state')),
        SelectDesc(key='type', field='x.com.samsung.da.iceType.desired',
                   name=None, icon='mdi:cube-outline',
                   options_field='x.com.samsung.da.iceType.supported',
                   exists_fn=lambda rep: bool(rep.get('x.com.samsung.da.iceType.supported')),
                   write_fn=_icemaker_write('x.com.samsung.da.iceType.desired')),
    ),
)

# ---------------------------------------------------------------------------
# Doors
# ---------------------------------------------------------------------------

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

def _autofill_write(p, rep, href=None):
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
                   value_fn=lambda v: v == 'On',
                   write_fn=_sabbath_write),
    ),
)

# ---------------------------------------------------------------------------
# Beverage zone
# ---------------------------------------------------------------------------

def _bzone_write(p, rep, href=None):
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
