"""Capabilities for the cooktop half of range/combo appliances (issue #44,
model TP1X_DA-KS-RANGE-0102X).

Not to be confused with PR #23's registry/capabilities/cooktop.py, which
covers an unrelated standalone-cooktop product (NA9300K-class) that encodes
burner state as strings inside /mode/vs/0's options array instead of the
structured /cooktop/status/vs/0 resource this module reads -- two different
OCF surfaces that happen to share the English word "cooktop".

Unlike the rest of the OCF surface, these hrefs use plain camelCase field
names (no `x.com.samsung.da.` prefix) -- `/cooktop/status/vs/0` already
looks like a vendor resource migrated onto OCF-standard-shaped field naming.

`/cooktop/status/vs/0` carries every burner's live state in one `burnerList`
array (indexed by `burnerNumber`, not by a separate href per burner like
fridge ice makers), so per-burner entities are hardcoded up to MAX_BURNERS
and gated by exists_fn against whichever indices the device actually
reports -- harmless over-declaration, per common.py's UNIVERSAL note, since
an index absent from burnerList just never binds.

Write surfaces here are unproven (no live device to verify against, same
caveat as oven.py's RMW writes) -- power level uses the same read-modify-
write pattern already proven safe elsewhere in this codebase (oven setpoint,
icemaker toggles).
"""
from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc

# Observed as high as 4 (this issue's dump); user-reported hardware with 5
# burners exists. Kept a little above both since exists_fn gates unused
# slots out -- see module docstring.
MAX_BURNERS = 6


def _burner(burner_list, i):
    for b in (burner_list or []):
        if b.get('burnerNumber') == i:
            return b
    return None


def _burner_exists(i):
    return lambda rep, resources: _burner(rep.get('burnerList'), i) is not None


def _burner_field_fn(i, field):
    return lambda burner_list: (_burner(burner_list, i) or {}).get(field)


def _burner_hot_surface_fn(i):
    get_state = _burner_field_fn(i, 'hotSurfaceState')
    return lambda burner_list: get_state(burner_list) not in (None, 'normal')


def _power_level_options(resources):
    spec = resources.get('/cooktop/spec/vs/0') or {}
    return list(spec.get('supportedPowerLevelList') or [])


def _burner_power_level_write(i):
    def write(p, rep, href=None):
        burner_list = rep.get('burnerList')
        if not burner_list:
            return None
        new_list = []
        found = False
        for b in burner_list:
            if b.get('burnerNumber') == i:
                b = dict(b)
                b['powerLevel'] = p
                found = True
            new_list.append(b)
        if not found:
            return None
        return ['cooktop', 'status', 'vs', '0'], {'burnerList': new_list}
    return write


def _burner_entities(i):
    exists = _burner_exists(i)
    n = i + 1
    return (
        SelectDesc(key=f'burner_{i}_power_level', field='burnerList',
                   name=f'Burner {n} power level', icon='mdi:knob',
                   options=_power_level_options,
                   exists_fn=exists,
                   value_fn=_burner_field_fn(i, 'powerLevel'),
                   write_fn=_burner_power_level_write(i)),
        SensorDesc(key=f'burner_{i}_state', field='burnerList',
                   name=f'Burner {n} state', icon='mdi:stove',
                   exists_fn=exists,
                   value_fn=_burner_field_fn(i, 'operationState')),
        BinarySensorDesc(key=f'burner_{i}_hot_surface', field='burnerList',
                         name=f'Burner {n} hot surface', device_class='heat',
                         exists_fn=exists,
                         value_fn=_burner_hot_surface_fn(i)),
    )


COOKTOP_STATUS = Capability(
    href='/cooktop/status/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='cooktop_state', field='operationState',
                   name='Cooktop state', icon='mdi:pot-steam'),
        *[e for i in range(MAX_BURNERS) for e in _burner_entities(i)],
    ),
)

# Static burner-count/power-level-list metadata, read directly by
# COOKTOP_STATUS's power-level select (options=_power_level_options) rather
# than exposed through its own entity -- same "informs another capability,
# no entity of its own" pattern as /wm/editcourse/vs/0 (ignored.py).
COOKTOP_SPEC = Capability(href='/cooktop/spec/vs/0')

# settingTime (seconds) is the hot-surface auto-shutoff timer's configured
# duration (1200s = 20 min in issue #44's dump); state on/off is whether the
# feature itself is enabled -- not a live "surface is hot right now" alert
# (that's COOKTOP_STATUS's per-burner hot_surface). No write contract
# verified, so read-only for now.
COOKTOP_SAFETY = Capability(
    href='/cooktop/settings/status/vs/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(key='cooktop_safety_shutoff_enabled', field='safetyAlert',
                         name='Hot surface auto-shutoff enabled',
                         entity_category='config',
                         value_fn=lambda v: (v or {}).get('state') == 'on'),
    ),
)
