"""Capabilities for the Samsung dehumidifier family (TP1X_DA_AC_DHM-class,
issue #88, model AY18CG7500GED).

Same DA_AC_ board family as the room-AC models in airconditioner.py (shared
power/energy/filter/auto-clean/mute-once resource shapes), but target
humidity -- not temperature -- is this device's primary control, and there
is no climate composite: power, mode, and humidity are exposed as separate
entities rather than folded into one card.
"""
from ..capability import Capability
from ..entities import NumberDesc, SelectDesc, SensorDesc
from .common import int_or_none


def _first_mode(rep):
    """Representative scalar for the operating-mode select. `modes` is a
    single-element list on every dump seen so far, mirroring
    airconditioner._first_mode's handling of the same field shape."""
    modes = rep.get('x.com.samsung.da.modes')
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='operating_mode', rep_fn=_first_mode,
                   icon='mdi:tune-variant',
                   options_field='x.com.samsung.da.supportedModes',
                   write_fn=lambda p, rep, href=None: (
                       ['mode', 'vs', '0'], {'x.com.samsung.da.modes': [p]})),
    ),
)

# Target humidity is this device's primary control (the issue-#88 dump's
# equivalent of a thermostat setpoint). No min/max range field is present in
# any dump seen so far -- native_min/native_max are deliberately left unset
# so the number entity falls back to HA's own 0-100 default, the natural
# bound for a percentage field, rather than a bound guessed from one unit's
# spec sheet (see the adding-device-support skill's "never hard-code the one
# dump's values" section). Step comes live from the device's own `increment`
# field.
HUMIDITY = Capability(
    href='/humidity/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='humidity', field='x.com.samsung.da.humidity',
                   device_class='humidity', unit='%', state_class='measurement',
                   value_fn=int_or_none),
        NumberDesc(key='target_humidity', field='x.com.samsung.da.desiredHumidity',
                   device_class='humidity', unit='%',
                   icon='mdi:water-percent',
                   entity_category='config',
                   value_fn=int_or_none,
                   step_fn=lambda rep: int_or_none(rep.get('increment')) or 1,
                   write_fn=lambda p, rep, href=None: (
                       ['humidity', 'vs', '0'],
                       {'x.com.samsung.da.desiredHumidity': str(int(round(float(p))))})),
    ),
)

# ---------------------------------------------------------------------------
# Dehumidifier-scoped coverage: vendor plumbing with no user-actionable state
# or no documented write contract, following the same 'don't guess' rule as
# airconditioner._AC_IGNORED (this is the same DA_AC_ board family). Not in
# the global ignored.IGNORED since some of these hrefs collide with other
# families' schemas.
# ---------------------------------------------------------------------------
_DHM_IGNORED = [
    '/availablecontrolsets/vs/0',  # opaque hex-encoded control-set bitmap (id: DHM)
    '/da/softreset/vs/0',          # soft-reset trigger plumbing
    '/keepnormalstate/vs/0',       # internal keep-normal flag
    '/personality/presence/vs/0',  # presence-personalization plumbing (empty item value)
    '/reserverulesets/vs/0',       # opaque hex-encoded schedule reservation blob
    '/sensors/vs/0',               # empty {} on this dump
    '/welcome/humidity/vs/0',      # welcome-mode plumbing (requestId/operatingStatus, inert)
    # Only supportedModes ([Off, Sleep]) is present -- no live "current
    # value" field on this dump to confirm the read/write contract, so per
    # the 'don't guess' rule this is left unmodeled rather than assumed.
    '/mode/convenient/vs/0',
]

COVERAGE = [Capability(href=h) for h in _DHM_IGNORED]
