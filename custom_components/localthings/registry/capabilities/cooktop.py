"""Read-only capabilities for Samsung cooktops.

The first verified device is an NA9300K-class five-burner gas cooktop.  Its
local OCF API reports burner state as strings embedded in the
``x.com.samsung.da.options`` array on ``/mode/vs/0``.  Heat-producing controls
are intentionally not exposed: the local write contract is unverified and a
cooktop must not be remotely ignited by an automation.
"""

import re

from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc


_INACTIVE_OPERATION_STATES = {'Off', 'Ready'}


def _option_value(options, prefix):
    """Return the value from the first ``<prefix>_<value>`` option."""
    marker = prefix + '_'
    for option in options or ():
        if isinstance(option, str) and option.startswith(marker):
            return option[len(marker):]
    return None


def _operation_slots(options) -> tuple[int, ...]:
    """Return the numeric burner slots advertised in an options array."""
    slots = set()
    for option in options or ():
        if not isinstance(option, str):
            continue
        match = re.match(r'^OperationState(\d+)_', option)
        if match:
            slots.add(int(match.group(1)))
    return tuple(sorted(slots))


def _any_burner_active(options):
    """True when any advertised burner slot is not idle."""
    states = [
        _option_value(options, f'OperationState{slot}')
        for slot in _operation_slots(options)
    ]
    states = [state for state in states if state is not None]
    return any(state not in _INACTIVE_OPERATION_STATES for state in states)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


COOKTOP_POWER = Capability(
    href='/power/vs/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(
            key='power_state',
            field='x.com.samsung.da.power',
            name='Power state',
            device_class='power',
            icon='mdi:stove',
            value_fn=lambda value: str(value).lower() == 'on',
        ),
    ),
)


# The verified NA9300K exposes physical slots 0, 1, 3, 4, and 5.  Declare a
# generous static superset so variants with other layouts are not silently
# omitted; exists_fn hides every slot the live options array does not report.
# Replace this bound with data-driven entity generation when #31 lands.
_SUPPORTED_OPERATION_SLOTS = tuple(range(8))

COOKTOP_MODE = Capability(
    href='/mode/vs/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(
            key='any_burner_active',
            field='x.com.samsung.da.options',
            name='Any burner active',
            device_class='running',
            icon='mdi:fire',
            value_fn=_any_burner_active,
        ),
        *(
            SensorDesc(
                key=f'burner_{slot}_state',
                field='x.com.samsung.da.options',
                name=f'Burner {slot} state',
                icon='mdi:gas-burner',
                value_fn=lambda options, slot=slot: _option_value(
                    options, f'OperationState{slot}'
                ),
                exists_fn=lambda rep, resources, slot=slot: (
                    not rep or _option_value(
                        rep.get('x.com.samsung.da.options'),
                        f'OperationState{slot}',
                    ) is not None
                ),
            )
            for slot in _SUPPORTED_OPERATION_SLOTS
        ),
        SensorDesc(
            key='main_timer_state',
            field='x.com.samsung.da.options',
            name='Timer state',
            icon='mdi:timer-outline',
            value_fn=lambda options: _option_value(options, 'MainTimerState'),
        ),
        SensorDesc(
            key='main_timer_current',
            field='x.com.samsung.da.options',
            name='Timer current value',
            icon='mdi:timer-sand',
            enabled_default=False,
            value_fn=lambda options: _int_or_none(
                _option_value(options, 'MainTimerCurrent')
            ),
        ),
    ),
)


COOKTOP_CONNECTED = Capability(
    href='/connected/vs/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(
            key='cloud_connected',
            field='x.com.samsung.da.connected',
            name='Cloud connected',
            device_class='connectivity',
            entity_category='diagnostic',
            value_fn=lambda value: str(value).lower() == 'on',
        ),
    ),
)


PAIRED_HOOD_STATUS = Capability(
    href='/bluetooth/hood/status/vs/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(
            key='paired_hood_connected',
            field='connectionState',
            name='Paired hood connected',
            device_class='connectivity',
            value_fn=lambda value: str(value).lower() == 'connected',
        ),
        BinarySensorDesc(
            key='paired_hood_power',
            field='power',
            name='Paired hood power',
            device_class='running',
            value_fn=lambda value: str(value).lower() == 'on',
        ),
        SensorDesc(
            key='paired_hood_fan_speed',
            field='fanSpeed',
            name='Paired hood fan speed',
            icon='mdi:fan',
            value_fn=_int_or_none,
        ),
        BinarySensorDesc(
            key='paired_hood_light',
            field='lampState',
            name='Paired hood light',
            device_class='light',
            value_fn=lambda value: str(value).lower() == 'on',
        ),
        SensorDesc(
            key='paired_hood_model',
            field='micomModelId',
            name='Paired hood model',
            icon='mdi:information-outline',
            entity_category='diagnostic',
            enabled_default=False,
        ),
        SensorDesc(
            key='paired_hood_firmware',
            field='firmwareVersion',
            name='Paired hood firmware',
            icon='mdi:chip',
            entity_category='diagnostic',
            enabled_default=False,
        ),
    ),
)
