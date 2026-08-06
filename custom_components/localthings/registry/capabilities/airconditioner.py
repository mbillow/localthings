"""Capabilities for the Samsung air-conditioner family (ARTIK051_PRAC-class).

Resources verified against the issue #17 diagnostics dump (model
ARTIK051_PRAC_20K). This is the first family whose core controls surface as a
single composite HA `climate` entity rather than a scatter of switches/selects:
power (on/off), HVAC mode, current/target temperature, fan (wind) strength,
swing (wind direction), and the convenient-mode preset all live on one climate
card. The climate platform (climate.py) reads those sibling resources from the
coordinator snapshot; here we bind the primary `/mode/vs/0` resource to the
`ClimateDesc` and mark the consumed siblings as covered.

None of these caps may go into the global `ALL`/`CAPABILITIES`: `/mode/vs/0`,
`/temperatures/vs/0`, `/humidity/*` collide with fridge/oven hrefs of a
different schema (see capabilities/__init__.py). They live only in the AC
by_type registry.
"""
from dataclasses import replace

from ..capability import Capability
from ..entities import (
    BinarySensorDesc, ClimateDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc,
)
from . import common
from .common import filter_usage_percent, normalize_temp_unit
from .laundry import option_write


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _beep_on(rep):
    """Beep on/off from the `Volume_*` option token: Volume_Mute = off,
    Volume_100 (and any non-Mute) = on. None when no Volume_ slot.

    _option_token (defined further below, alongside the legacy-board token
    helpers) returns the token's *value* half (e.g. 'Mute', '100'), not the
    full 'Volume_100' token -- shared with _option_token_num/_option_token_on,
    which this module's other options[] readers already rely on.
    """
    tok = _option_token(rep, 'Volume')
    if tok is None:
        return None
    return tok != 'Mute'


def _beep_write(payload, rep, href=None):
    """Toggle beep via a single-token /mode/vs/0 options write (option_write's
    one-token merge -- a full options RMW reverts on ARTIK051_PRAC). 'On'
    restores the last non-Mute level rather than forcing Volume_100, so a
    user's intermediate setting (e.g. Volume_50 set via the cloud) survives an
    off/on cycle; falls back to 100 when no prior level is known."""
    if payload not in ('On', 'Off'):
        return None
    if payload == 'Off':
        token = 'Mute'
    else:
        prev = _option_token(rep, 'Volume')
        token = prev if (prev and prev != 'Mute') else '100'
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': option_write('Volume', token),
    }


def _tropical_night_value(rep):
    """Tropical night mode level (0-16) from the `Sleep_<N>` option token.

    _option_token returns the token's value half already (e.g. '16' for
    'Sleep_16'), same convention as _beep_on above.
    """
    tok = _option_token(rep, 'Sleep')
    if tok is None:
        return None
    return _int(tok)


def _tropical_night_write(value, rep, href=None):
    """Set tropical night level via a single-token `Sleep_<N>` options write.
    Samsung cloud counterpart: custom.airConditionerTropicalNightMode (0-16)."""
    try:
        level = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if not 0 <= level <= 16:
        return None
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': option_write('Sleep', str(level)),
    }


def _filter_unit(rep):
    """Unit of the filter-usage fields, normalised from filterCapacityUnit
    ('Hour' -> 'h'). Wired through unit_fn so a board advertising a different
    unit doesn't silently mislabel a duration statistic."""
    u = rep.get('x.com.samsung.da.filterCapacityUnit')
    return {'Hour': 'h', 'Minute': 'min', 'Second': 's'}.get(u, u or 'h')


def _threshold_write(payload, rep, href=None):
    """filterDesiredUsage is locally writable: a plain scalar POST of the
    field to /filter/airdustfilter/vs/0 is 2.04-accepted and persists
    (confirmed live on ARTIK051_PRAC: POST 700 -> 2.04, read-back 700). The
    Select only surfaces where the device advertises
    supportedFilterDesiredUsage, so the valid options are known rather than
    guessed; boards without that enum leave this writable field unexposed."""
    return ['filter', 'airdustfilter', 'vs', '0'], {
        'x.com.samsung.da.filterDesiredUsage': payload,
    }


def _sensor_item_value(items, type_):
    """First value of the /sensors/vs/0 item with the given
    x.com.samsung.da.type. The resource exposes no unit, so no device_class is
    set until a populated reading + unit is observed (the 'don't guess' rule).

    Dust/FineDust/SuperFineDust report a 2-element array (['0','0']) while
    CleanLevel/Odor report a single element -- the second element's meaning is
    unconfirmed, so v[0] is taken as the reading and v[1] is dropped; left as
    a string rather than coerced numeric because only CleanLevel has
    corroborating evidence (a top-level x.com.samsung.da.cleanLevel scalar)."""
    for it in (items or []):
        if isinstance(it, dict) and it.get('x.com.samsung.da.type') == type_:
            v = it.get('x.com.samsung.da.value')
            if isinstance(v, list) and v:
                return str(v[0])
            return None
    return None


def _has_sensor_type(type_):
    """True when the /sensors/vs/0 items[] array lists an item of this type.

    This proves the type is *listed*, not that the reading is real: issue
    #166 (ARxxTXFCAWKNEU, board ARTIK051_PRAC_20K) lists all five item types
    with permanent zero values on both its units, and the reporter confirmed
    none of these sensors are physically present. A top-level
    x.com.samsung.da.cleanLevel scalar (separate from the CleanLevel item)
    looked like a corroborating "this reading is real" signal at first --
    present alongside genuinely populated readings on tp1x_da_ac_rac_01011
    and the tp1x_da_ac_air air purifier fixture, absent on every all-zero
    ARTIK051_PRAC_20K dump including both #166 units -- but it doesn't hold
    up as a general rule: air_purifier_device.json (ARTIK051_TVTL_18K),
    air_purifier_vtww_device.json, and range_hood_device.json all carry
    genuinely populated, non-AC-family Dust/FineDust/SuperFineDust readings
    with no such scalar. So this stays item-type presence only -- the
    entities are disabled by default instead (see AIR_QUALITY below) rather
    than existence-gated on a signal that would silently drop real readings
    on hardware this repo hasn't seen yet."""
    def fn(rep, resources):
        return any(isinstance(i, dict) and i.get('x.com.samsung.da.type') == type_
                   for i in (rep.get('x.com.samsung.da.items') or []))
    return fn

# ---------------------------------------------------------------------------
# Canonical AC resource hrefs. The climate entity (climate.py) binds the
# primary HREF_MODE via CLIMATE below and reads the CLIMATE_CONSUMED_HREFS
# siblings off the coordinator snapshot; those siblings are marked covered
# (no-entity caps) so discover() reports no gap. Declared once here and
# imported by climate.py, so a new sibling read can't drift out of sync with
# its coverage entry.
# ---------------------------------------------------------------------------
HREF_MODE = '/mode/vs/0'                          # primary (bound by CLIMATE)
HREF_POWER = '/power/0'                           # on/off -> HVACMode.OFF / TURN_ON/OFF
HREF_POWER_VS = '/power/vs/0'                     # vendor fallback for on/off
HREF_TEMP_CURRENT = '/temperature/current/0'      # current_temperature
HREF_TEMP_DESIRED = '/temperature/desired/0'      # target_temperature (write target)
HREF_TEMP_CONTROL = '/temperature/control/vs/0'   # target_temperature_step
HREF_WIND_STRENGTH = '/wind/strength/vs/0'        # fan_mode
HREF_WIND_DIRECTION = '/wind/direction/vs/0'      # swing_mode
# Newer boards (issue #126, TP1X_DA-AC-RAC-01011 WindFree variant) report no
# HREF_WIND_DIRECTION at all and instead carry a 2-axis oscillation resource
# (separate vertical/horizontal Swing|Fix toggles, each with its own angle).
# climate.py falls back to this when HREF_WIND_DIRECTION is absent, the same
# duality pattern as the OCF/vendor temperature channel below.
HREF_WIND_OSCILLATION = '/wind/oscillation/vs/0'  # swing_mode fallback
HREF_CONVENIENT = '/mode/convenient/vs/0'         # preset_mode
HREF_TEMPS_VS = '/temperatures/vs/0'              # vendor temp fallback (items[] array)
# Legacy ARTIK051 boards (ARTIK051_KRAC_18K, issue #136) carry no /wind/* resources
# at all: fan speed and vane direction sit together in this one resource, as
# x.com.samsung.da.speedLevel (the same 0-4 scale as _DEVICE_TO_FAN) and
# x.com.samsung.da.direction (the same codes as _DEVICE_TO_SWING). Their
# convenient-mode preset is a Comode_* token in /mode/vs/0's options instead of a
# resource of its own -- see climate.py's _legacy_airflow/_legacy_convenient.
HREF_AIRFLOW = '/airflow/vs/0'                    # legacy fan_mode + swing_mode

CLIMATE_CONSUMED_HREFS = [
    HREF_POWER, HREF_POWER_VS, HREF_TEMP_CURRENT, HREF_TEMP_DESIRED,
    HREF_TEMP_CONTROL, HREF_TEMPS_VS, HREF_WIND_STRENGTH, HREF_WIND_DIRECTION,
    HREF_WIND_OSCILLATION, HREF_CONVENIENT, HREF_AIRFLOW,
]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _temps_vs_item(rep):
    """First item of the vendor `/temperatures/vs/0` items[] array -- the
    Tizen Lite board's only current-temperature source (see climate.py's
    identical helper; duplicated rather than imported to avoid a
    capabilities<->platform import cycle)."""
    items = rep.get('x.com.samsung.da.items')
    if isinstance(items, (list, tuple)) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _temps_vs_current(rep):
    return _num(_temps_vs_item(rep).get('x.com.samsung.da.current'))


def _temps_vs_unit(rep):
    return normalize_temp_unit(_temps_vs_item(rep).get('x.com.samsung.da.unit'), '°C')


def _first_mode(rep):
    """Representative scalar for the climate entity in the flattened state
    (golden/regression). The real entity computes hvac_mode from power + mode."""
    modes = rep.get('x.com.samsung.da.modes')
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


# --- 커스텀 추가분: 청정/환기/스마트환기 모드 select ------------------------
# 모델 ACA-KR-TP2-21-AN9000 (vid DA-AC-DIFFUSER-01001, "시스템 청정환기")은
# /oic/d에서 자신을 oic.d.airconditioner로 선언해서 이 파일의 climate
# 엔티티로 라우팅되지만, /mode/vs/0의 실제 모드 어휘는 Purification/
# Ventilation/SmartVentilation -- climate.py의 _DEVICE_TO_HVAC 테이블에 없는
# 값들이라 hvac_mode가 전부 'Auto'로 뭉개져서 SmartThings 앱처럼 세 모드를
# 구분할 방법이 없었음. climate 엔티티와 같은 href를 공유하는 별도 select로
# 이 세 모드를 그대로 노출함 -- 실제 에어컨(Cool/Heat/Dry 등)에서는
# _is_ventilation_mode_device가 False를 반환해 바인딩되지 않으므로 기존
# 에어컨 사용자에게는 영향 없음.
_VENTILATION_MODE_VALUES = frozenset(('Purification', 'Ventilation', 'SmartVentilation'))


def _is_ventilation_mode_device(rep, resources):
    supported = rep.get('x.com.samsung.da.supportedModes')
    if not isinstance(supported, (list, tuple)) or not supported:
        return False
    return set(supported) <= _VENTILATION_MODE_VALUES


def _ventilation_mode_write(payload, rep, href=None):
    # 원본 rep의 modes 필드가 리스트([...])라서 쓰기도 동일하게 리스트로 감쌈.
    return ['mode', 'vs', '0'], {'x.com.samsung.da.modes': [payload]}
# --- 커스텀 추가분 끝 -------------------------------------------------------


def _mode_options(rep):
    opts = rep.get('x.com.samsung.da.options')
    return opts if isinstance(opts, (list, tuple)) else ()


def _has_display_light_option(rep, resources):
    """True when the panel light state is carried inside /mode/vs/0's options
    blob (a `Light_On`/`Light_Off` token) rather than a dedicated /light/vs/0
    switch. The two encodings are mutually exclusive across observed boards:
    models exposing the /light/vs/0 switch (bound by DISPLAY_LIGHT below)
    carry no Light_* option, so this entity only materialises on the boards
    that would otherwise have no display-light entity at all."""
    return any(isinstance(o, str) and o.startswith('Light_')
               for o in _mode_options(rep))


def _display_light_on(rep):
    """Panel display light state from /mode/vs/0's options blob. The token is
    INVERTED relative to its name -- confirmed by a live toggle test: with the
    panel lit the option reads `Light_Off`, and with it dark it reads
    `Light_On` (the flag really encodes "night/display-off mode active"). So
    `Light_Off` -> light on, `Light_On` -> light off. Read-only from here; the
    write below uses the device's own single-token merge (see option_write)."""
    for o in _mode_options(rep):
        if isinstance(o, str) and o.startswith('Light_'):
            return o == 'Light_Off'
    return None


def _display_light_write(payload, rep, href=None):
    """Toggle the panel light via a single-token /mode/vs/0 options write
    ('SingleCommand_1' is advertised in this family's /configuration/vs/0
    airconOptionList; option_write's one-token merge is the same mechanism
    air_purifier.py uses for its own Light switch). Polarity is inverted (see
    _display_light_on): switching the lamp ON writes 'Light_Off', OFF writes
    'Light_On'."""
    token = 'Off' if payload == 'On' else 'On'
    return (['mode', 'vs', '0'],
            {'x.com.samsung.da.options': option_write('Light', token)})


# ---------------------------------------------------------------------------
# Legacy ARTIK051 boards keep several settings that newer boards expose as their
# own resources (/option/*, /electriccurrent/vs/0, ...) as `<Prefix>_<value>`
# tokens inside /mode/vs/0's options blob instead. Reads pull the token apart;
# writes reuse option_write's single-token merge, exactly like the display light.
# ---------------------------------------------------------------------------


def _option_token(rep, prefix):
    """Value part of a `<prefix>_<value>` token in /mode/vs/0's options."""
    for option in _mode_options(rep):
        if isinstance(option, str) and option.startswith(prefix + '_'):
            return option.split('_', 1)[1]
    return None


def is_legacy_board(resources):
    """True for the board generation whose airflow lives in /airflow/vs/0.

    Newer families carry several of the same option tokens (Sleep, OutdoorTemp,
    Autoclean) *alongside* dedicated resources for those settings, so an
    ungated token entity would either duplicate an existing one or apply a
    scale calibrated elsewhere. (Volume used to be in this list too, back when
    it had its own gated buzzer_volume Number for this board generation --
    issue #136 replaced that with the unified 'beep' switch, which applies
    across every generation and isn't gated here at all.) Every AC dump on
    record has one shape or the other: /airflow/vs/0 with no /wind/* at all,
    or /wind/strength/vs/0 with no /airflow/vs/0. Same test as climate.py's
    _legacy_airflow(), so the entities below and the climate entity can never
    disagree about the generation.
    """
    return HREF_AIRFLOW in resources and HREF_WIND_STRENGTH not in resources


# This legacy ARTIK051 board generation (issue #193, model AR12NXWXCWKNEU /
# ARTIK051_KRAC_18K) reports /energy/consumption/vs/0's cumulativePower in
# centiwatt-hours -- raw value 100x the plain Wh every other AC board family
# (and common.wh_to_kwh's assumed unit) reports. Confirmed against the
# reporter's own SmartThings-app reading: raw '117430000' vs the app's
# authoritative 1,174.30 kWh is exactly a /100000 factor (i.e. /100 on top of
# wh_to_kwh's own /1000), not wh_to_kwh's plain /1000 alone. No other field in
# ENERGY_METER's entities is present on this board's dump, so only
# 'energy_kwh' needs a replacement value_fn here; the rest pass through
# unchanged in case a future legacy dump ever reports them.
def _legacy_cumulative_power_kwh(v):
    # float, not _int -- matches common.wh_to_kwh's own numeric parsing
    # (float via _num) rather than this module's integer-only _int, so a
    # decimal-formatted reading doesn't raise and silently go 'unknown'.
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return round(n / 100000.0, 2)


ENERGY_METER_LEGACY = replace(
    common.ENERGY_METER,
    match_fn=lambda rep, resources: is_legacy_board(resources),
    entities=tuple(
        replace(e, value_fn=_legacy_cumulative_power_kwh)
        if e.key == 'energy_kwh' else e
        for e in common.ENERGY_METER.entities
    ),
)

# The non-legacy counterpart to ENERGY_METER_LEGACY above -- identical to
# common.ENERGY_METER, just excluding the legacy board generation so the two
# capabilities can share /energy/consumption/vs/0 in this registry without
# the 'multiple caps need a discriminator' build check tripping (common.
# ENERGY_METER itself has no match_fn, since every *other* registry includes
# it unconditionally and alone).
ENERGY_METER_GENERIC = replace(
    common.ENERGY_METER,
    match_fn=lambda rep, resources: not is_legacy_board(resources),
)


def _has_option_token(prefix):
    return lambda rep, resources: (
        is_legacy_board(resources) and _option_token(rep, prefix) is not None)


def _option_token_on(prefix):
    return lambda rep: _option_token(rep, prefix) == 'On'


def _option_token_num(prefix, offset=0, divisor=1):
    def read(rep):
        raw = _option_token(rep, prefix)
        try:
            return (float(raw) - offset) / divisor
        except (TypeError, ValueError):
            return None
    return read


def _option_switch_write(prefix):
    def write(payload, rep, href=None):
        return (['mode', 'vs', '0'],
                {'x.com.samsung.da.options': option_write(prefix, payload)})
    return write


def _option_number_write(prefix):
    def write(payload, rep, href=None):
        return (['mode', 'vs', '0'],
                {'x.com.samsung.da.options':
                 option_write(prefix, str(int(round(float(payload)))))})
    return write


def _odor_controller_active(rep):
    """Odor-controller self-clean on/off, from the `SmartCoolClean_<On/Off>`
    /mode/vs/0 option token -- corroborated against the SmartThings cloud
    custom.airConditionerOdorController capability's airConditionerOdorController
    State field, same on/off vocabulary and a matching name ("Smart Cool
    Clean" mirrors "odor controller"). Unconditional across board
    generations, same as beep above -- no evidence ties this token to the
    legacy-vs-newer split _has_option_token gates on. Read-only: no command
    capability is confirmed, so this stays a sensor rather than a guessed
    write (the 'don't guess' rule)."""
    tok = _option_token(rep, 'SmartCoolClean')
    if tok is None:
        return None
    return tok == 'On'


def _odor_controller_progress(rep):
    """0-100 progress of the odor-controller self-clean cycle, from the
    `ProgressSmartClean_<N>` option token -- mirrors the cloud
    airConditionerOdorControllerProgress field."""
    return _int(_option_token(rep, 'ProgressSmartClean'))


def _humidity(rep):
    """Relative humidity, preferring the 5%-rounded field where it exists.

    ARTIK051 boards have no fivepercentHumidity field at all and report the
    plain x.com.samsung.da.humidity instead -- and only populate it while the
    Air monitoring option is on: the unit measures for roughly half a minute
    (51% observed, matching what the same unit's cloud integration reported at
    that moment), then zeroes the field and switches Air monitoring back off by
    itself. So 0 reads as "not measuring" and is reported as unknown rather than
    as 0% humidity, which would poison long-term history.

    That zero-as-"not measuring" carve-out is specific to the ARTIK051
    fallback field's hardware quirk -- every other board's fivepercentHumidity
    has never been documented getting stuck at zero, and collapsing a
    genuine 0% reading there to unknown is a regression, not a safeguard
    (issue #160). So fivepercentHumidity passes 0 through unchanged; only the
    humidity fallback applies the zero-collapse.
    """
    if 'x.com.samsung.da.fivepercentHumidity' in rep:
        return _num(rep['x.com.samsung.da.fivepercentHumidity'])
    if 'x.com.samsung.da.humidity' in rep:
        value = _num(rep['x.com.samsung.da.humidity'])
        return value if value else None
    return None


def _climate_write(payload, rep, href=None):
    """Map a (kind, value) command from the climate platform to the
    (path_segs, body) for that one sub-write. `value` is already the raw device
    code (the platform maps HA<->device). async_send_command POSTs to path_segs,
    so a single desc drives writes across the power/mode/temperature/wind
    resources.

    Power goes to the vendor `/power/vs/0` (the OCF `/power/0` is absent on
    most boards and a non-authoritative mirror where present -- vendor works
    on every board). Temperature is board-dependent, so the platform picks the
    channel and sends `temperature_ocf` (-> OCF `/temperature/desired/0`,
    boards with the full OCF current+desired pair) or `temperature` (-> vendor
    `/temperatures/vs/0`, boards without it). Mode/fan/swing/preset are always
    the vendor `/x/vs/0` resources.

    Each write sends only its own field(s), leaving the resource's other
    fields (e.g. /mode/vs/0's opaque `options` blob, or the vendor temperature
    item's current/minimum/maximum/unit) untouched -- the device merges the
    rest itself, same contract as the options[] array (see
    common.merge_items_field / merge_options_field, which keep the
    coordinator's optimistic cache complete for the settle window instead of
    this write echoing those fields back).
    """
    kind, value = payload
    if kind == 'power':
        return (['power', 'vs', '0'],
                {'x.com.samsung.da.power': 'On' if value else 'Off'})
    if kind == 'mode':
        return (['mode', 'vs', '0'], {'x.com.samsung.da.modes': [value]})
    if kind == 'temperature_ocf':
        return (['temperature', 'desired', '0'],
                {'temperature': int(round(float(value)))})
    if kind == 'temperature':
        # Vendor items[] array; only one item observed on every AC dump, id
        # '0'. See the docstring above for why this doesn't echo current/
        # minimum/maximum/unit back at the unit.
        return (['temperatures', 'vs', '0'],
                {'x.com.samsung.da.items': [
                    {'x.com.samsung.da.id': '0',
                     'x.com.samsung.da.desired': str(int(round(float(value))))}]})
    if kind == 'fan':
        return (['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': value})
    if kind == 'swing':
        return (['wind', 'direction', 'vs', '0'], {'x.com.samsung.da.modes': value})
    if kind == 'oscillation':
        # value is the HA swing_mode string ('off'/'vertical'/'horizontal'/
        # 'both'); both axes are independent Swing|Fix toggles on this
        # resource, so one HA value maps to a pair of fields written
        # together (see climate.py's oscillation fallback).
        return (['wind', 'oscillation', 'vs', '0'], {
            'vertical': 'Swing' if value in ('vertical', 'both') else 'Fix',
            'horizontal': 'Swing' if value in ('horizontal', 'both') else 'Fix',
        })
    if kind == 'fan_legacy':
        return (['airflow', 'vs', '0'],
                {'x.com.samsung.da.speedLevel': str(value)})
    if kind == 'swing_legacy':
        return (['airflow', 'vs', '0'],
                {'x.com.samsung.da.direction': value})
    if kind == 'preset_legacy':
        # Single-token options merge, same mechanism as _display_light_write.
        return (['mode', 'vs', '0'],
                {'x.com.samsung.da.options': option_write('Comode', value)})
    if kind == 'preset':
        return (['mode', 'convenient', 'vs', '0'], {'x.com.samsung.da.modes': value})
    return None


CLIMATE = Capability(
    href=HREF_MODE,
    poll_tier='warm',
    entities=(
        ClimateDesc(key='climate', translation_key='airconditioner',
                    rep_fn=_first_mode, write_fn=_climate_write),
        # --- 커스텀 추가분: 청정/환기/스마트환기 모드 select ----------------
        # _is_ventilation_mode_device 게이팅으로 이 어휘를 쓰는 기기에서만
        # 바인딩됨 (일반 에어컨엔 영향 없음).
        SelectDesc(key='ventilation_mode', translation_key='ventilation_mode',
                   rep_fn=_first_mode,
                   exists_fn=_is_ventilation_mode_device,
                   options_field='x.com.samsung.da.supportedModes',
                   icon='mdi:air-filter',
                   write_fn=_ventilation_mode_write),
        # --- 커스텀 추가분 끝 -------------------------------------------------
        # Display (panel) light switch, only on boards that encode it in
        # /mode/vs/0's options instead of a /light/vs/0 switch (see
        # _has_display_light_option). Shares the /mode/vs/0 href with the
        # climate entity above -- same Capability, so no multi-cap
        # discriminator is needed. /mode/vs/0 is OBSERVE-subscribed, so state
        # updates on push; writes go through the single-token merge in
        # _display_light_write. Shares the switch.display_light translation
        # with DISPLAY_LIGHT (the /light/vs/0 switch on other boards) --
        # mutually exclusive per href, so only one ever binds for a given unit.
        SwitchDesc(key='display_light', rep_fn=_display_light_on,
                   exists_fn=_has_display_light_option,
                   write_fn=_display_light_write,
                   icon='mdi:led-on', entity_category='config'),
        # Beep on/off from the `Volume_*` option token (Volume_Mute/Volume_100).
        # Single-token option_write; a full options RMW reverts on ARTIK051_PRAC.
        #
        # Applies uniformly across board generations, including legacy
        # ARTIK051 boards -- issue #136: this token was originally modeled as
        # a 0-100 buzzer_volume Number for legacy boards on the assumption it
        # was a real graduated volume level, but every unit confirmed on
        # hardware (three units across two reporters) only ever carries
        # Volume_100 or Volume_Mute, never an intermediate value, and the
        # Number's write path (a plain integer string) could never produce
        # the literal 'Mute' token needed to actually turn it off -- writing
        # '0' is simply not a token this firmware recognizes. A switch is
        # both the correct model and the actual fix.
        SwitchDesc(key='beep', rep_fn=_beep_on,
                   exists_fn=lambda rep, resources: _option_token(rep, 'Volume') is not None,
                   write_fn=_beep_write,
                   icon='mdi:volume-high', entity_category='config'),
        # Tropical night mode level (0-16) from the `Sleep_<N>` option token.
        # Single-token option_write. Cloud: custom.airConditionerTropicalNightMode.
        # Gated off the legacy board for the same reason as beep above -- its
        # Sleep_ token is already the good_sleep Number below.
        #
        # exists_fn only proves the Sleep_ token slot is present, not that
        # tropical night mode is a real feature of the unit: issue #166
        # (ARxxTXFCAWKNEU) reports Sleep_0 in every dump -- the exact same
        # always-there-at-zero shape as the issue #17 dump #164 was verified
        # against -- yet the reporter confirmed their remote/app has no
        # tropical night mode control at all. Samsung's OCF options[] blob
        # carries this scaffolding token regardless of physical capability,
        # so there's no reliable signal here to gate on (same 'don't guess'
        # rule as elsewhere in this file, just with no signal to guess from).
        # Registered but disabled by default, same precedent as
        # fridge.rack_count / cooktop.paired_hood_* -- units that do have the
        # feature can enable it themselves.
        NumberDesc(key='tropical_night_mode', rep_fn=_tropical_night_value,
                   exists_fn=lambda rep, resources: (
                       not is_legacy_board(resources)
                       and _option_token(rep, 'Sleep') is not None),
                   write_fn=_tropical_night_write,
                   native_min=0, native_max=16, step=1,
                   enabled_default=False,
                   icon='mdi:weather-night', entity_category='config'),
        # Settings that this board generation keeps as options[] tokens.
        SwitchDesc(key='spi', rep_fn=_option_token_on('Spi'),
                   exists_fn=_has_option_token('Spi'),
                   write_fn=_option_switch_write('Spi'),
                   icon='mdi:air-purifier', entity_category='config'),
        # Shares AUTO_CLEAN's catalog entry rather than duplicating it: same
        # feature, different board generation (that one is a /option/autoclean/
        # vs/0 field, absent here). Distinct key, so nothing collides if some
        # future board ever reported both.
        SwitchDesc(key='auto_clean_legacy', translation_key='auto_clean',
                   rep_fn=_option_token_on('Autoclean'),
                   exists_fn=_has_option_token('Autoclean'),
                   write_fn=_option_switch_write('Autoclean'),
                   icon='mdi:fan-auto', entity_category='config'),
        SwitchDesc(key='air_monitoring', rep_fn=_option_token_on('AirMonitoring'),
                   exists_fn=_has_option_token('AirMonitoring'),
                   write_fn=_option_switch_write('AirMonitoring'),
                   icon='mdi:air-filter', entity_category='config'),
        # "Good Sleep" timer. 0 = off; the upper bound is a guess (the token
        # carries no range hint and only 0 has been observed on hardware), so a
        # write above 0 is unverified.
        NumberDesc(key='good_sleep', rep_fn=_option_token_num('Sleep'),
                   exists_fn=_has_option_token('Sleep'),
                   write_fn=_option_number_write('Sleep'),
                   native_min=0, native_max=12, step=1, unit='h',
                   icon='mdi:sleep', entity_category='config'),
        # Outdoor temperature, offset by 55. Two calibration points on one unit:
        # token 75 while an independent outdoor thermometer in the same install
        # read 20.3 C, and token 74 against a 19.4 C forecast. Fahrenheit fits far
        # worse (74 F = 23.3 C); the issue #136 unit's 81 gives 26 C in a warmer
        # climate, which is also plausible.
        SensorDesc(key='outdoor_temperature',
                   rep_fn=_option_token_num('OutdoorTemp', offset=55),
                   exists_fn=_has_option_token('OutdoorTemp'),
                   device_class='temperature', state_class='measurement',
                   unit='°C', icon='mdi:home-thermometer-outline'),
        # Filter time in tenths of an hour: the token read 1710 while the official
        # Samsung app displayed "171 hours 0 minutes" for the filter on the same
        # unit, and the .0 matching the app's "0 minutes" pins the scale.
        #
        # It counts UP -- running time accumulated since the last filter reset,
        # not time remaining. An earlier revision of this comment called the
        # direction unestablished; three independent things now settle it. It was
        # seen rising while the unit ran (171.0 -> 171.5). FilterAlarmTime_ in the
        # same options[] blob is the threshold it is measured against (500 on
        # every unit on record). And across two units on one site, the alarm
        # tracks the counter in the right direction: at FilterTime_5595 the
        # /alarms/vs/0 filter entry is live (unsuffixed code 'FilterAlarm', state
        # 'Created'), while at FilterTime_1915 it is still the
        # 'FilterAlarm_OFF'/'Deleted' placeholder. That also matches what the app
        # does at 500 hours -- ask the user to clean the filter and reset it.
        #
        # The key stays 'filter_time' rather than becoming something like
        # filter_time_elapsed: renaming it would change every existing unit's
        # entity_id and unique_id for a wording improvement.
        #
        # (The alarm clearing on its own is the causal proof of the direction
        # above: the board recomputes it the moment the counter crosses the
        # threshold, rather than the app clearing counter and alarm separately.)
        #
        # ---------------------------------------------------------------------
        # RESETTING THIS COUNTER: NOT SOLVED YET -- no reset entity here, and
        # the notes below are so the next attempt starts from the evidence
        # rather than from scratch. I could not work out how to drive the reset
        # locally; that is not the same as it being impossible, and none of the
        # results below rule out a mechanism I simply haven't found.
        #
        # What the reset actually is: a *command*, not a value write. Samsung
        # models it as capability `custom.dustFilter`, command
        # `resetDustFilter`, no arguments (implemented in several SmartThings HA
        # forks; not in the core integration). That reframes every failed
        # attempt below -- nothing changes the counter by writing to it,
        # because the board zeroes it itself on receiving a command.
        #
        # Tried, all against a live unit, all failed:
        #   * FilterTime_0 via the single-token options merge that works for
        #     every other setting on this href -- accepted with no error, then
        #     discarded. Two units, opposite power states, to rule out the
        #     obvious confound: 5595 -> back to 5595 after 69 s (powered off,
        #     alarm active) and 1925 -> back to 1925 after 65 s (actively
        #     cooling).
        #   * A full options[] read-modify-write with FilterTime_0 substituted,
        #     instead of the single-token merge -- zero fields changed anywhere.
        #   * A write to /consumable/vs/0, the board's own filter resource
        #     (items[{name: FilterProgress, state: N}]) -- discarded. /oic/res
        #     declares that resource oic.if.s, i.e. read-only, which fits.
        #   * /actions/vs/0 (x.com.samsung.da.actions, oic.if.a) is the obvious
        #     local command channel, but publishes no schema: GET returns {} on
        #     baseline and on oic.if.a, and five POSTs probing the *shape*
        #     (empty map, empty string, empty array, invalid value, items shape)
        #     all returned 4.00 with an empty body -- no echo of accepted field
        #     names, unlike the laundry firmware's "Control fail, <...>". I
        #     deliberately did not enumerate guessed action names against a live
        #     appliance: an unknown vocabulary on a channel called "actions" can
        #     hold a factory reset next to the one we want.
        #   * /hass/state/vs/0 and /hass/command/vs/0 (advertised in /oic/res,
        #     and /opt/data/hass.db exists in /file/list) -> 4.04 on every
        #     interface, so unimplemented scaffolding on this firmware.
        #   * /file/transfer/vs/0 serves only /mnt/usage.db; selecting another
        #     path returns 4.05/4.00, so the firmware can't be pulled that way
        #     to read the action vocabulary out of it.
        #   * /rm/micomdata/vs/0 (channel toward the MICOM board the physical
        #     panel talks to) stays empty even after successfully enabling
        #     remote management.
        #
        # What the failures are NOT: a transport, permission or cert problem.
        # A control write of rmState on /rm/state/vs/0 was accepted (2.04
        # Changed, value held, restored afterwards), and FilterAlarmTime_ below
        # is written through the very same options merge and kept. So writes
        # work; this one value just isn't driven that way.
        #
        # Where I'd look next: the action vocabulary for /actions/vs/0 from an
        # independent source (firmware image, or a capture of what the cloud
        # sends the device), or the IR path -- the physical remote has a filter
        # reset (Options -> Filter Reset -> SET), and IRremoteESP8266 decodes
        # this AC family, though issue #1277's dump doesn't include that button.
        # ---------------------------------------------------------------------
        SensorDesc(key='filter_time',
                   rep_fn=_option_token_num('FilterTime', divisor=10),
                   exists_fn=_has_option_token('FilterTime'),
                   device_class='duration', unit='h',
                   state_class='measurement', icon='mdi:air-filter'),
        # The interval FilterTime_ is measured against -- the app offers it as a
        # four-way radio (180/300/500/700 hours, default 500) next to the filter
        # reminder. The token value is the hour count verbatim: captured by
        # watching all 19 resources while the owner stepped through every radio
        # position, one field changing per step and nothing else moving.
        #
        # Static options here, unlike air_filter_threshold on the newer boards
        # which reads supportedFilterDesiredUsage: options[] tokens carry no
        # supported-values list anywhere in this board's dump, so there is
        # nothing to read them from. The four values are the app's own radio
        # rather than a guess extrapolated from one observed value -- but a
        # board offering different steps would need this revisited, which is
        # why the tuple is documented rather than just written down.
        SelectDesc(key='filter_alarm_time',
                   rep_fn=lambda rep: _option_token(rep, 'FilterAlarmTime'),
                   exists_fn=_has_option_token('FilterAlarmTime'),
                   options=('180', '300', '500', '700'),
                   write_fn=_option_switch_write('FilterAlarmTime'),
                   icon='mdi:alarm', entity_category='config'),
        # Odor-controller ("Smart Cool Clean") state + progress -- see
        # _odor_controller_active's docstring for the cloud-capability
        # correspondence. Present on TP1X_DA-AC-RAC-01001 (issue reporter's
        # dump); gating is token presence only, not board generation.
        BinarySensorDesc(key='odor_controller_active',
                          rep_fn=_odor_controller_active,
                          exists_fn=lambda rep, resources: (
                              _option_token(rep, 'SmartCoolClean') is not None),
                          icon='mdi:air-filter', entity_category='diagnostic'),
        SensorDesc(key='odor_controller_progress',
                   rep_fn=_odor_controller_progress,
                   exists_fn=lambda rep, resources: (
                       _option_token(rep, 'ProgressSmartClean') is not None),
                   unit='%', state_class='measurement',
                   icon='mdi:progress-check', entity_category='diagnostic'),
    ),
)

AIR_PURIFY = Capability(
    href='/option/airpurify/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='air_purify', field='x.com.samsung.da.modes',
                   icon='mdi:air-purifier',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'airpurify', 'vs', '0'],
                       {'x.com.samsung.da.modes': 'On' if p == 'On' else 'Off'})),
    ),
)

AUTO_CLEAN = Capability(
    href='/option/autoclean/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='auto_clean', field='x.com.samsung.da.settingStatus',
                   icon='mdi:spray-bottle',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'autoclean', 'vs', '0'],
                       {'x.com.samsung.da.settingStatus': 'On' if p == 'On' else 'Off'})),
    ),
)

# --- 커스텀 추가분: 모델명 진단 센서 ----------------------------------------
# /information/vs/0은 원래 기기타입 판별에만 쓰이고 전역적으로 무시되는
# href인데(ignored.py), 대시보드/자동화에서 모델명을 바로 볼 수 있게
# 진단(diagnostic) 센서로 승격함. description 필드가 modelNum보다 짧고
# 깔끔해서(예: 'ACA-KR-TP2-21-AN9000') 그걸 우선 쓰고, 없으면 modelNum의
# '|' 구분 첫 토큰을 씀. 시리얼/OTN 같은 개인식별 필드는 노출하지 않음.
def _model_name(rep):
    desc = rep.get('x.com.samsung.da.description')
    if desc:
        return desc
    model_num = rep.get('x.com.samsung.da.modelNum')
    if model_num:
        return model_num.split('|')[0]
    return None


MODEL_INFO = Capability(
    href='/information/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='model_name', translation_key='model_name',
                   icon='mdi:identifier',
                   entity_category='diagnostic',
                   rep_fn=_model_name),
    ),
)
# --- 커스텀 추가분 끝 -------------------------------------------------------

# --- 커스텀 추가분: 팬모드(풍량)/회전모드(풍향) 독립 select --------------
# HREF_WIND_STRENGTH/HREF_WIND_DIRECTION은 원래 climate 엔티티의
# fan_mode/swing_mode 속성으로만 "흡수"되고 있었음 (COVERAGE에서 no-entity
# 처리). 기기 "제어" 목록에서 바로 보이는 별도 컨트롤을 원해서 진짜 select
# 엔티티로 승격함. modes 필드가 /mode/vs/0와 달리 리스트가 아니라 그냥
# 문자열이라 rep_fn/write_fn도 그에 맞게 단순함.
# 이 href는 airconditioner 레지스트리 전체가 공유하므로, 이 패치는 이
# 기기뿐 아니라 이 통합구성요소를 쓰는 다른 모든 에어컨에도 똑같이
# 적용됨 (기존 climate 카드 안의 팬/스윙 컨트롤은 그대로 유지되고, 이건
# 추가 노출일 뿐이라 회귀 위험 없음-- 22개 기존 fixture로 확인함).
def _wind_scalar_mode(rep):
    return rep.get('x.com.samsung.da.modes')


def _wind_strength_write(payload, rep, href=None):
    return ['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': payload}


def _wind_direction_write(payload, rep, href=None):
    return ['wind', 'direction', 'vs', '0'], {'x.com.samsung.da.modes': payload}


FAN_SPEED_SELECT = Capability(
    href=HREF_WIND_STRENGTH,
    poll_tier='warm',
    entities=(
        SelectDesc(key='fan_speed_select', translation_key='fan_speed_select',
                   icon='mdi:fan',
                   rep_fn=_wind_scalar_mode,
                   options_field='x.com.samsung.da.supportedModes',
                   write_fn=_wind_strength_write),
    ),
)

SWING_DIRECTION_SELECT = Capability(
    href=HREF_WIND_DIRECTION,
    poll_tier='warm',
    entities=(
        SelectDesc(key='swing_direction_select', translation_key='swing_direction_select',
                   icon='mdi:arrow-expand-vertical',
                   rep_fn=_wind_scalar_mode,
                   options_field='x.com.samsung.da.supportedModes',
                   write_fn=_wind_direction_write),
    ),
)
# --- 커스텀 추가분 끝 -------------------------------------------------------

# --- 커스텀 추가분: 인공지능 청정 (AI Clean / airlevelcheck) --------------
# 모델 ACA-KR-TP2-21-AN9000 덤프에서 확인된 원본 rep:
#   x.com.samsung.da.periodicSensingActivationState: 'On'/'Off'
#   x.com.samsung.da.autoExeState: 'Sensing' (현재 상태)
#   x.com.samsung.da.supportedAutoExeState: ['Sensing','Airpurify','Alarm']
# SmartThings 앱의 "인공지능 청정" 토글 + "감지만 하기" 서브텍스트가 바로
# 이 두 필드에 대응됨 (스위치=전체 on/off, select=Sensing/Airpurify/Alarm
# 중 어떤 동작을 할지). 원래 이 프로젝트는 이 href 전체를 opaque plumbing
# 으로 보고 무시했었는데, 실제로는 유의미한 사용자 제어값이라 승격시킴.
# ⚠ 쓰기(write)는 추정 -- 단일 필드 교체 방식으로 만들었고 실기기 확인 전.
def _ai_clean_active(rep):
    return rep.get('x.com.samsung.da.periodicSensingActivationState') == 'On'


def _ai_clean_active_write(payload, rep, href=None):
    return ['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.periodicSensingActivationState':
            'On' if payload == 'On' else 'Off',
    }


def _ai_clean_mode_write(payload, rep, href=None):
    return ['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.autoExeState': payload,
    }


AI_CLEAN = Capability(
    href='/airlevelcheck/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='ai_clean_active',
                   icon='mdi:air-purifier',
                   entity_category='config',
                   rep_fn=_ai_clean_active,
                   write_fn=_ai_clean_active_write),
        SelectDesc(key='ai_clean_mode',
                   icon='mdi:magnify-scan',
                   entity_category='config',
                   rep_fn=lambda rep: rep.get('x.com.samsung.da.autoExeState'),
                   options_field='x.com.samsung.da.supportedAutoExeState',
                   write_fn=_ai_clean_mode_write),
    ),
)
# --- 커스텀 추가분 끝 -------------------------------------------------------

# --- 커스텀 추가분: 무풍(WindFree) / 취침(WindSleep) 모드 스위치 -----------
# 모델 ACA-KR-TP2-21-AN9000 (vid: DA-AC-DIFFUSER-01001, "시스템 청정환기")
# 진단 덤프에서 확인된 두 href. 둘 다 동일한 형태:
#   {"x.com.samsung.da.windfree|windsleep": "On"/"Off",
#    "x.com.samsung.da.displaycondition": "normal"}  -- displaycondition은
#   UI 표시 여부용 읽기전용 필드로 보여서 모델링하지 않음.
#
# ⚠ 쓰기(write) 계약은 이 파일의 다른 단순 On/Off 필드들(AIR_PURIFY,
# AUTO_CLEAN)과 동일한 패턴으로 "추정"한 것이며, 실기기로 확인된 값은
# 아니다. localthings 프로젝트의 "확인 안 된 추측은 명시하고 리포터에게
# 확인을 요청한다"는 관례(adding-device-support 스킬 5절)를 따라 여기 남김.
# 실제 기기에서 스위치를 켜고 꺼봤을 때 정상 반영되는지 확인 필요.
WINDFREE = Capability(
    href='/modeoption/windfree/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='windfree', field='x.com.samsung.da.windfree',
                   icon='mdi:leaf',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['modeoption', 'windfree', 'vs', '0'],
                       {'x.com.samsung.da.windfree': 'On' if p == 'On' else 'Off'})),
    ),
)

WINDSLEEP = Capability(
    href='/modeoption/windsleep/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='windsleep', field='x.com.samsung.da.windsleep',
                   icon='mdi:sleep',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['modeoption', 'windsleep', 'vs', '0'],
                       {'x.com.samsung.da.windsleep': 'On' if p == 'On' else 'Off'})),
    ),
)
# --- 커스텀 추가분 끝 -------------------------------------------------------

AIR_FILTER = Capability(
    href='/filter/airdustfilter/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='air_filter_usage', rep_fn=filter_usage_percent,
                   unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic'),
        # filterUsage is a lifetime hour counter that only resets on filter
        # replacement -- total_increasing so HA's long-term statistics handle
        # the reset rather than treating it as a bounded measurement.
        SensorDesc(key='air_filter_usage_hours',
                   field='x.com.samsung.da.filterUsage',
                   device_class='duration',
                   state_class='total_increasing',
                   unit_fn=_filter_unit,
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=_int),
        # The alarm threshold (filterDesiredUsage) is a locally writable option:
        # see _threshold_write. Surfaces as a Select only where the device
        # advertises supportedFilterDesiredUsage; boards without that enum
        # leave it unexposed rather than guess the valid set.
        SelectDesc(key='air_filter_threshold',
                   field='x.com.samsung.da.filterDesiredUsage',
                   options_field='x.com.samsung.da.supportedFilterDesiredUsage',
                   exists_fn=lambda rep, res: bool(
                       rep.get('x.com.samsung.da.supportedFilterDesiredUsage')),
                   icon='mdi:alarm', entity_category='config',
                   write_fn=_threshold_write,
                   value_fn=lambda v: str(v) if v is not None else None),
        SensorDesc(key='air_filter_status', field='x.com.samsung.da.filterStatus',
                   device_class='enum',
                   options=('normal', 'wash', 'replace'),
                   translation_key='filter_status',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
    ),
)

DISPLAY_LIGHT = Capability(
    href='/light/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='display_light', field='mode',
                   icon='mdi:led-on',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['light', 'vs', '0'],
                       {'mode': 'On' if p == 'On' else 'Off'})),
    ),
)

# Confirmed against issue #38's dump (TP1X_DA-AC-RAC-01001_0000): a single
# boolean field, no vendor prefix, mirroring the On/Off convention used
# throughout the rest of this API.
MUTE_ONCE = Capability(
    href='/option/muteonce/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='mute_once', field='muteonce',
                   icon='mdi:volume-mute',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'muteonce', 'vs', '0'],
                       {'muteonce': 'On' if p == 'On' else 'Off'})),
    ),
)

# Circuit-breaker current-limit setting (issue #38, TP1X board): `operation`
# toggles the limiter and `modes` picks a level out of `supportedModes`
# (seen as '3'..'9'). No vendor field-name prefix and no unit/label in the
# dump to confirm what the levels mean (amps vs. an abstract tier) --
# exposed read-only per the 'don't guess' rule rather than risking an
# unverified write to live HVAC hardware.
CURRENT_LIMIT = Capability(
    href='/electriccurrent/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(key='current_limit_enabled', field='operation',
                          icon='mdi:current-ac',
                          entity_category='diagnostic',
                          value_fn=lambda v: v == 'On'),
        SensorDesc(key='current_limit_level', field='modes',
                   icon='mdi:current-ac',
                   entity_category='diagnostic'),
    ),
)

# Overload-response setting (issue #126, TP1X_DA-AC-RAC-01011 WindFree
# variant): `operation` toggles the feature and `mode` picks 'Alarm' vs
# 'PowerSaving' out of `supportedModes`, with a `savingTime` duration
# ('20'/'40'/'60' minutes) alongside. Plausible shape (compressor-overload
# alarm vs. automatic power throttling), but nothing in the dump confirms
# the exact behavioral difference between the two modes or whether writing
# 'operation' is safe on live HVAC hardware -- exposed read-only per the
# 'don't guess' rule, same precedent as CURRENT_LIMIT above.
ANOMALY_LOAD = Capability(
    href='/anomalyload/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(key='overload_protection_active', field='operation',
                          icon='mdi:flash-alert',
                          entity_category='diagnostic',
                          value_fn=lambda v: v == 'On'),
        SensorDesc(key='overload_protection_mode', field='mode',
                   device_class='enum',
                   options=('alarm', 'powersaving'),
                   translation_key='overload_protection_mode',
                   icon='mdi:flash-alert', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
    ),
)

# Absence-detection power-saving (issue #173, TP1X_LNX-AC-RAC-01001 --
# Lennox-branded heat pump on the RAC board family): `status` is a bare
# On/Off boolean with no vendor prefix, the same shape already shipped
# writable elsewhere in this file (MUTE_ONCE, AUTO_CLEAN, AIR_PURIFY,
# DISPLAY_LIGHT) despite none of those having a live-confirmed write either
# -- worst case a wrong token no-ops, same risk profile as that family, so
# it's a switch rather than a sensor. `switchPowerSaveMode` picks the save
# intensity out of its own supportedSwitchPowerSaveMode list, but *what*
# writing it actually does to a running compressor isn't knowable from the
# dump -- same 'don't guess' read-only treatment as CURRENT_LIMIT/
# ANOMALY_LOAD's mode fields. A third field, `motionState`, also carries a
# supportedMotionState list but its role (a live sensor readout vs. a
# sensitivity setting) isn't distinguishable from the dump, so it's left
# unmodeled entirely.
ABSENCE_POWER_SAVING = Capability(
    href='/mds/absencepowersaving/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='absence_power_saving_active', field='status',
                   icon='mdi:human-greeting-proximity',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['mds', 'absencepowersaving', 'vs', '0'],
                       {'status': 'On' if p == 'On' else 'Off'})),
        SensorDesc(key='absence_power_saving_mode', field='switchPowerSaveMode',
                   device_class='enum',
                   options=('eco', 'normal', 'comfort'),
                   translation_key='absence_power_saving_mode',
                   icon='mdi:leaf', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
    ),
)

# Avoid-direct-wind-on-motion, a sibling AI feature to ABSENCE_POWER_SAVING
# above on the same dump: `status` is the same bare On/Off shape, promoted to
# a switch for the same reason. `modes` (Direct/Indirect airflow out of
# `supportedModes`) stays read-only -- same reasoning as
# absence_power_saving_mode above.
MOTION_DETECT_WIND = Capability(
    href='/option/motiondetectwind/stateful/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='motion_detect_wind_active', field='status',
                   icon='mdi:motion-sensor',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'motiondetectwind', 'stateful', 'vs', '0'],
                       {'status': 'On' if p == 'On' else 'Off'})),
        SensorDesc(key='motion_detect_wind_mode', field='modes',
                   device_class='enum',
                   options=('direct', 'indirect'),
                   translation_key='motion_detect_wind_mode',
                   icon='mdi:weather-windy', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
    ),
)

# The climate entity already surfaces current_temperature as a card
# attribute, but that's not enough for history graphs/automations/
# statistics -- issue #75 asked for a standalone sensor. Same OCF-standard-
# with-vendor-fallback shape as common.py's POWER_GENERIC/POWER_VS_FALLBACK
# pair: both share key='current_temperature_c' so only one ever binds
# (match_fn gates the vendor one off when the OCF resource is present).
CURRENT_TEMPERATURE = Capability(
    href=HREF_TEMP_CURRENT,
    poll_tier='warm',
    entities=(
        SensorDesc(key='current_temperature_c', field='temperature',
                   device_class='temperature', state_class='measurement',
                   unit_fn=lambda rep: normalize_temp_unit(rep.get('units'), '°C')),
    ),
)

CURRENT_TEMPERATURE_VS = Capability(
    href=HREF_TEMPS_VS,
    poll_tier='warm',
    match_fn=lambda rep, resources: HREF_TEMP_CURRENT not in resources,
    entities=(
        SensorDesc(key='current_temperature_c', rep_fn=_temps_vs_current,
                   device_class='temperature', state_class='measurement',
                   unit_fn=_temps_vs_unit),
    ),
)

# Only the vendor resource's `fivepercentHumidity` (current reading, rounded
# to the nearest 5%) has live data on the issue #75 dump -- its `humidity`
# field, and the OCF-standard /humidity/0 resource entirely, both read a
# stuck "0" there, so /humidity/0 stays ignored per the 'don't guess' rule
# (see _AC_IGNORED below).
#
# ARTIK051 boards (issue #136) have no fivepercentHumidity field at all, and
# there the plain `humidity` field is not stuck: it carries a real reading
# (51%, matching the same unit's cloud integration at that moment) for as long
# as the Air monitoring option is on, which the unit itself switches back off
# after roughly a minute -- so most dumps catch it at 0. Hence _humidity's
# fallback, and hence 0 reading as "not measuring" rather than 0%: on both
# board generations a zero here means no measurement, never dry air.
# /humidity/0 stayed 0 throughout that same observation too.
HUMIDITY = Capability(
    href='/humidity/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='humidity', rep_fn=_humidity,
                   device_class='humidity', state_class='measurement', unit='%'),
    ),
)

# /sensors/vs/0 items[] carry live air-quality readings. Removed from
# _AC_IGNORED below so AIR_QUALITY is the sole cap on the href. CleanLevel is
# corroborated as numeric by a top-level x.com.samsung.da.cleanLevel scalar
# (tp1x_da_ac_rac_01011 reports both as '1'), so it's a measurement; the others
# are 1- or 2-element arrays with no corroborating scalar, so they stay string
# diagnostics (see _sensor_item_value for the 2-element ambiguity and why only
# v[0] is taken). No unit is advertised on the resource, so no device_class.
#
# exists_fn (_has_sensor_type) only proves the item *type* is listed, not
# that the unit actually carries that sensor: issue #166 (ARxxTXFCAWKNEU,
# board ARTIK051_PRAC_20K) reports all five item types on both its units,
# values permanently '0'/['0','0'] -- yet the reporter confirmed none apply
# to their model. A tighter existence gate was tried (requiring the
# corroborating cleanLevel scalar above) but doesn't hold up as a general
# rule -- see _has_sensor_type's docstring -- and risks silently dropping
# real readings on hardware that reports them without that scalar. So these
# stay bound whenever the type is listed, same as before #166, and disabled
# by default instead (same precedent as fridge.rack_count /
# cooktop.paired_hood_model / this file's own tropical_night_mode): units
# that do have the sensor can enable it themselves.
AIR_QUALITY = Capability(
    href='/sensors/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='clean_level', field='x.com.samsung.da.items',
                   icon='mdi:broom', entity_category='diagnostic',
                   state_class='measurement',
                   exists_fn=_has_sensor_type('CleanLevel'),
                   enabled_default=False,
                   value_fn=lambda items: _int(_sensor_item_value(items, 'CleanLevel'))),
        # --- 커스텀 추가분: 단위(unit)/device_class ---------------------
        # 사용자가 SmartThings 앱 스크린샷으로 실측값을 보내줘서 단위를
        # 확인함: 미세먼지류는 "46 µg/m³" 형태, 생활가스(Odor)는 "1 Lv"
        # 형태로 표시됨. HA 표준 device_class(pm10/pm25/pm1)가 있는 항목은
        # 그걸 붙여서 공기질 카드에 자동으로 좋음/나쁨 색상까지 나오게 함.
        # Odor는 HA에 대응하는 device_class가 없어서 단위만 붙임.
        *tuple(
            SensorDesc(key=key, field='x.com.samsung.da.items',
                       icon=icon, entity_category='diagnostic',
                       unit=unit, device_class=device_class,
                       state_class='measurement' if device_class else None,
                       exists_fn=_has_sensor_type(type_),
                       enabled_default=False,
                       value_fn=lambda items, t=type_: _int(_sensor_item_value(items, t)))
            for key, icon, type_, unit, device_class in (
                ('odor', 'mdi:weather-windy', 'Odor', 'Lv', None),
                ('dust', 'mdi:cloud', 'Dust', 'µg/m³', 'pm10'),
                ('fine_dust', 'mdi:cloud-outline', 'FineDust', 'µg/m³', 'pm25'),
                ('super_fine_dust', 'mdi:weather-fog', 'SuperFineDust', 'µg/m³', 'pm1'),
            )
        ),
        # --- 커스텀 추가분 끝 ---------------------------------------------
        # --- 커스텀 추가분: CO2 센서 --------------------------------------
        # 모델 ACA-KR-TP2-21-AN9000 덤프에서 /sensors/vs/0에 type='CO2'
        # 항목이 실제로 존재하고(값 예: ['1000','1']), SmartThings 앱에도
        # "이산화탄소: 보통"으로 표시되는 것을 확인함 -- 이 모델에서는
        # 확인된 값이지만, 이 캡퍼빌리티는 AC 계열 전체가 공유하므로
        # 다른 모델은 미확인 상태 -- 기존 항목들과 동일하게
        # enabled_default=False로 두고 exists_fn으로만 게이팅함.
        # air_monitor.py의 CO2 센서(device_class='carbon_dioxide', unit='ppm')
        # 와 동일한 컨벤션 재사용.
        SensorDesc(key='co2', field='x.com.samsung.da.items',
                   icon='mdi:molecule-co2', entity_category='diagnostic',
                   device_class='carbon_dioxide', state_class='measurement',
                   unit='ppm',
                   exists_fn=_has_sensor_type('CO2'),
                   enabled_default=False,
                   value_fn=lambda items: _int(_sensor_item_value(items, 'CO2'))),
        # --- 커스텀 추가분 끝 -----------------------------------------------
    ),
)


# ---------------------------------------------------------------------------
# AC-scoped coverage: the CLIMATE_CONSUMED_HREFS above (read by the climate
# entity) plus vendor duplicates / all-zero-ambiguous / plumbing resources.
# These are NOT in the global ignored.IGNORED because several of them
# (/mode/vs/0 handled above, /temperatures/vs/0, /humidity/*) collide with
# other families' schemas. A no-entity Capability still marks the href as
# bound so discover() reports no coverage gap.
#
# CLIMATE_CONSUMED_HREFS carry the climate card's actual displayed state
# (power, current/target temp, fan, swing, preset) -- the coordinator only
# OBSERVE-subscribes and sub-polls 'hot'/'warm' hrefs (see coordinator.py),
# so leaving these at the Capability default of 'cold' meant every state
# change was invisible until the next full /device/0 summary sweep
# (~30s -- issue #17: instant device response, 20-30s HA lag). Pin them to
# 'warm' -- same tier as CLIMATE's own primary href -- so they get push
# notifications (or, in poll-only mode, the warm sub-poll cadence) instead
# of waiting on the summary sweep.
# ---------------------------------------------------------------------------
_AC_IGNORED = [
    # Stuck at "0" on every dump seen -- HUMIDITY above reads the vendor
    # resource's usable fivepercentHumidity field instead; this OCF-standard
    # one has no corresponding live value confirmed yet.
    '/humidity/0',
    # Presence-personalization plumbing (empty item list here).
    '/personality/presence/vs/0',
    # OCF-standard mirror of /airflow/vs/0 ({speed, direction} vs
    # {speedLevel, direction}, identical values). The climate entity reads and
    # writes the vendor form, which is the one confirmed on hardware here --
    # note that air_purifier.py found the opposite ordering on its family, so
    # neither form is reliable sight-unseen and this one stays unmodeled.
    '/airflow/0',
    # --- TP1X/TP2X-class housekeeping / opaque blobs. These carry no
    # user-actionable state or no documented write contract, so per the
    # 'don't guess' rule they are ignored rather than modeled.
    # /option/muteonce/vs/0 and /selfcheck/vs/0 are deliberately NOT here --
    # see MUTE_ONCE above and common.SELF_CHECK (via common.UNIVERSAL) in
    # the by_type registry, both of which have a confirmed, cleanly
    # modelable contract.
    # '/airlevelcheck/vs/0'는 커스텀 추가분(AI_CLEAN, 아래)에서 실제
    # 엔티티로 승격했으므로 여기서 제거함 -- 원본에서는 plumbing으로
    # 무시되고 있었음.
    '/aisleep/vs/0',               # AI-sleep feedback state (no actionable control)
    '/availablecontrolsets/vs/0',  # opaque hex-encoded control-set bitmap
    '/da/softreset/vs/0',          # soft-reset trigger plumbing
    '/keepnormalstate/vs/0',       # internal keep-normal flag
    '/mds/absencemonitoring/vs/0', # motion-detection sensor plumbing (empty here)
    '/mds/absencestate/vs/0',      # motion-detection state (empty here)
    '/remotedatacontrol/vs/0',     # remote data-control session status
    '/remotedeviceinfo/vs/0',      # remote paired-device id list (empty didList here)
    '/remotetemperature/vs/0',     # external temp-sensor feed (unset on this unit)
    # Manual airflow-step position (supportedModes Off/80/60/40/Power).
    # Overlaps the /wind/direction swing control already on the climate card,
    # and the meaning of the numeric steps vs. 'Power' isn't documented in the
    # dump -- ignored per the 'don't guess' rule rather than modeled as a
    # select whose write could confuse live HVAC hardware.
    '/stepcontrol/vs/0',
    '/reserverulesets/vs/0',       # opaque hex-encoded schedule reservation blob
    '/welcome/temperature/vs/0',   # welcome-cooling plumbing
    # System-AC-only (multi-indoor-subdevice commercial installs, e.g.
    # A-CAWW-TP2-20-COMMON, issue #52): opaque hex-encoded installation
    # topology -- indoor/outdoor unit pairing, per-subdevice serials, MCU
    # info. Commissioning-time plumbing, not user-actionable appliance state.
    '/sac/installationinfo/vs/0',
    # Wind-Free 2-in-1 systems (one outdoor unit driving a floor-standing
    # *and* a wall-mounted indoor subdevice over one shared local IP, e.g.
    # TP2X_FAC_BORA_21K, issues #150/#153): an opaque paired-subdevice id
    # list, same "remote device ids, not user-actionable locally" role as
    # /remotedeviceinfo/vs/0 above -- true whenever this field is redacted
    # (the shipped airconditioner_fac_bora fixture) or absent. When it does
    # carry a real id (issue #177's airconditioner_fac_bora_2in1 fixture),
    # registry/subdevices.py's enumerate_subdevices reads this same
    # subdeviceIdList to reach the second indoor subdevice over this same
    # connection instead -- see that module's Pattern B.
    '/subdevices/vs/0',
    # Undocumented single int (runningMode: 0 on every dump seen), no
    # supported-values list to interpret it against -- 'don't guess'.
    '/runn/vs/0',
    # 2-in-1/multi-indoor-subdevice systems (issue #177, the reporter's
    # ARTIK051_DONGLE_FAC_18K): x.com.samsung.da.numofsubdevice, a plain
    # corroborating count of indoor subdevices on this connection. Confirmed
    # read-only (a write attempt returned CoAP 4.00). Absent from
    # /device/0's batch entirely -- registry.subdevices.enumerate_subdevices
    # fetches it with its own RETRIEVE and folds it into the resources dict
    # for diagnostics, which is why it needs an entry here rather than
    # surfacing as an unbound-href gap on every board that has it.
    '/multidevice/vs/0',
]

# Built as bare no-entity caps; folded into the AC registry (not global).
# HREF_TEMP_CURRENT and HREF_TEMPS_VS are excluded here -- CURRENT_TEMPERATURE
# / CURRENT_TEMPERATURE_VS above already cover those two with real entities.
# --- 커스텀 추가분: HREF_POWER / HREF_POWER_VS도 제외 -----------------------
# 사용자 요청으로 climate 카드와 별개로 대시보드에서 바로 켜고 끌 수 있는
# 독립 Power 스위치가 필요해서, common.POWER_GENERIC/POWER_VS_FALLBACK을
# 진짜 엔티티로 등록함 (by_type/airconditioner.py 참고). 원래 이 프로젝트가
# 일부러 안 만든 이유(coverage.py 주석 참고: climate 엔티티가 이미 전원을
# 소유하고 있어서)는 여전히 유효하지만, 같은 href를 두 UI 컨트롤(스위치 +
# climate 카드)이 같이 쓰는 것 자체는 문제 없음 -- beep/display_light가
# climate과 href를 공유하는 것과 동일한 패턴.
# --- 커스텀 추가분: HREF_WIND_STRENGTH / HREF_WIND_DIRECTION도 제외 -------
# 사용자 요청으로 팬모드(풍량)/회전모드(풍향)를 climate 카드 안에 숨겨진
# fan_mode/swing_mode 속성이 아니라 기기 "제어" 목록에 바로 보이는 독립
# select 엔티티로도 꺼냄 (FAN_SPEED_SELECT / SWING_DIRECTION_SELECT, 아래).
# climate 카드 자체의 팬/스윙 컨트롤은 그대로 남아있고 이건 추가 노출임.
COVERAGE = [
    Capability(href=h, poll_tier='warm') for h in CLIMATE_CONSUMED_HREFS
    if h not in (HREF_TEMP_CURRENT, HREF_TEMPS_VS, HREF_POWER, HREF_POWER_VS,
                 HREF_WIND_STRENGTH, HREF_WIND_DIRECTION)
] + [
    Capability(href=h) for h in _AC_IGNORED
]
