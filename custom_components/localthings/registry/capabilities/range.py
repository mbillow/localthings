"""Capabilities for range/combo appliances.

Cooktop coverage comes from issue #44's TP1X_DA-KS-RANGE-0102X; the
write-only clock resource was verified on issue #404's
TP1X_DA-KS-RANGE-0101X.

Not to be confused with registry/capabilities/cooktop.py, which covers an
unrelated standalone-cooktop product (NA9300K-class) that encodes burner
state as strings inside /mode/vs/0's options array instead of the
structured /cooktop/status/vs/0 resource this module reads -- two
different OCF surfaces that happen to share the English word "cooktop".

Unlike the rest of the OCF surface, these hrefs use plain camelCase field
names (no `x.com.samsung.da.` prefix).

`/cooktop/status/vs/0` carries every burner's live state in one
`burnerList` array (indexed by `burnerNumber`), so per-burner entities are
hardcoded up to MAX_BURNERS and gated by exists_fn against whichever
indices the device actually reports -- an index absent from burnerList
just never binds.

Cooktop write surfaces here are unproven (no live device to verify against,
same caveat as oven.py's RMW writes) -- power level uses the same
read-modify-write pattern already proven safe elsewhere in this codebase.
"""

from datetime import datetime

from ..capability import Capability
from ..entities import BinarySensorDesc, ButtonDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import normalize_temp_unit

# Observed as high as 4; user-reported hardware with 5 burners exists.
# Kept a little above both since exists_fn gates unused slots out.
MAX_BURNERS = 6


def _clock_sync_payload(now: datetime) -> str:
    """Format Home Assistant's local wall clock for the range."""
    return now.strftime("%Y-%m-%dT%H:%M:%S")


def _clock_sync_write(payload, _rep, href=None):
    """Write the generated wall clock to the range's write-only resource."""
    return ["configuration", "vs", "0"], {"x.com.samsung.da.currentTime": payload}


# The TP1X range family accepts a local wall-clock timestamp here but does not
# echo it on GET. Keeping this as a button prevents arbitrary timestamps from
# being supplied or cached and only generates the current timestamp on press.
RANGE_CLOCK_SYNC = Capability(
    href="/configuration/vs/0",
    entities=(
        ButtonDesc(
            key="sync_clock",
            icon="mdi:clock-check-outline",
            entity_category="config",
            payload_fn=_clock_sync_payload,
            write_fn=_clock_sync_write,
            write_only=True,
        ),
    ),
)


def _burner(burner_list, i):
    for b in burner_list or []:
        if b.get("burnerNumber") == i:
            return b
    return None


def _burner_exists(i):
    return lambda rep, resources: _burner(rep.get("burnerList"), i) is not None


def _burner_field_fn(i, field):
    return lambda burner_list: (_burner(burner_list, i) or {}).get(field)


def _burner_hot_surface_fn(i):
    get_state = _burner_field_fn(i, "hotSurfaceState")
    return lambda burner_list: get_state(burner_list) not in (None, "normal")


def _burner_pan_detection_fn(i):
    return lambda burner_list: bool(_burner_field_fn(i, "panDetection")(burner_list))


def _power_level_options(resources):
    spec = resources.get("/cooktop/spec/vs/0") or {}
    return list(spec.get("supportedPowerLevelList") or [])


def _burner_power_level_write(i):
    def write(p, rep, href=None):
        burner_list = rep.get("burnerList")
        if not burner_list:
            return None
        new_list = []
        found = False
        for b in burner_list:
            if b.get("burnerNumber") == i:
                b = dict(b)
                b["powerLevel"] = p
                found = True
            new_list.append(b)
        if not found:
            return None
        return ["cooktop", "status", "vs", "0"], {"burnerList": new_list}

    return write


def _burner_entities(i):
    exists = _burner_exists(i)
    n = i + 1
    return (
        SelectDesc(
            key=f"burner_{i}_power_level",
            field="burnerList",
            icon="mdi:knob",
            translation_key="range_burner_power_level",
            translation_placeholders={"number": str(n)},
            options=_power_level_options,
            exists_fn=exists,
            value_fn=_burner_field_fn(i, "powerLevel"),
            write_fn=_burner_power_level_write(i),
        ),
        SensorDesc(
            key=f"burner_{i}_state",
            field="burnerList",
            icon="mdi:stove",
            translation_key="burner_state",
            translation_placeholders={"number": str(n)},
            exists_fn=exists,
            value_fn=_burner_field_fn(i, "operationState"),
        ),
        BinarySensorDesc(
            key=f"burner_{i}_hot_surface",
            field="burnerList",
            device_class="heat",
            translation_key="burner_hot_surface",
            translation_placeholders={"number": str(n)},
            exists_fn=exists,
            value_fn=_burner_hot_surface_fn(i),
        ),
        BinarySensorDesc(
            key=f"burner_{i}_pan_detected",
            field="burnerList",
            icon="mdi:pot",
            translation_key="burner_pan_detected",
            translation_placeholders={"number": str(n)},
            entity_category="diagnostic",
            exists_fn=exists,
            value_fn=_burner_pan_detection_fn(i),
        ),
    )


def _child_lock_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["cooktop", "status", "vs", "0"], {"childLock": p.lower()}


COOKTOP_STATUS = Capability(
    href="/cooktop/status/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(key="cooktop_state", field="operationState", icon="mdi:pot-steam"),
        # The cooktop section's own on/off (issue #86), distinct from
        # common.POWER's whole-appliance switch. Read-only: no live device
        # to confirm remotely turning it on wouldn't leave a burner active
        # unattended.
        BinarySensorDesc(
            key="cooktop_power",
            field="power",
            device_class="power",
            icon="mdi:pot-steam",
            value_fn=lambda v: str(v).lower() == "on",
        ),
        # Safe to write -- a lock toggle, not a heat control -- via a
        # direct single-field PUT, no RMW needed. No device_class:
        # SwitchDeviceClass only has 'outlet'/'switch', not 'lock' --
        # passing it crashed switch platform setup for the whole device
        # (issue #349, same bug as water_purifier.py's lock switches).
        SwitchDesc(
            key="cooktop_child_lock",
            field="childLock",
            entity_category="config",
            icon="mdi:lock",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_child_lock_write,
        ),
        *[e for i in range(MAX_BURNERS) for e in _burner_entities(i)],
    ),
)

# Static burner-count/power-level-list metadata, read directly by
# COOKTOP_STATUS's power-level select (options=_power_level_options)
# rather than exposed through its own entity.
COOKTOP_SPEC = Capability(href="/cooktop/spec/vs/0")

# settingTime (seconds) is the hot-surface auto-shutoff timer's configured
# duration; state on/off is whether the feature itself is enabled -- not a
# live "surface is hot right now" alert (that's COOKTOP_STATUS's per-burner
# hot_surface). No write contract verified, so read-only for now.
COOKTOP_SAFETY = Capability(
    href="/cooktop/settings/status/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="cooktop_safety_shutoff_enabled",
            field="safetyAlert",
            entity_category="diagnostic",
            value_fn=lambda v: (v or {}).get("state") == "on",
        ),
    ),
)

# Bluetooth meat probe (issue #86). All-idle sentinel values when
# disconnected (operationBurnerNumber -1, temperatures 0) -- no special
# gating, matching cooktop.PAIRED_HOOD_STATUS's precedent of showing a
# disconnected accessory's fields plainly rather than hiding the capability.
PROBE_STATUS = Capability(
    href="/bluetooth/probe/status/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="probe_connected",
            field="connectionState",
            device_class="connectivity",
            value_fn=lambda v: str(v).lower() == "connected",
        ),
        SensorDesc(
            key="probe_battery",
            field="batteryPercentage",
            device_class="battery",
            state_class="measurement",
            unit="%",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="probe_temperature",
            field="currentTemperature",
            device_class="temperature",
            state_class="measurement",
            unit_fn=lambda rep: normalize_temp_unit(rep.get("temperatureUnit"), "°C"),
        ),
        SensorDesc(
            key="probe_target_temperature",
            field="targetTemperature",
            device_class="temperature",
            entity_category="diagnostic",
            unit_fn=lambda rep: normalize_temp_unit(rep.get("temperatureUnit"), "°C"),
        ),
    ),
)

# Some range boards (issue #74) report no /cooktop/status/vs/0 burner
# array at all -- their local API only exposes this coarse monitoring
# resource, with no per-burner detail. Meaning of `cooktopMonitoring`
# (bare "0" on the only dump seen) and `warmingCenterState`'s full value
# set aren't confirmed, so both are plain sensors rather than a guessed
# switch/select.
COOKTOP_MONITORING = Capability(
    href="/cooktopmonitoring/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="cooktop_running_state",
            field="x.com.samsung.da.cooktopRunningState",
            icon="mdi:pot-steam-outline",
        ),
        SensorDesc(
            key="warming_center_state",
            field="x.com.samsung.da.warmingCenterState",
            icon="mdi:heat-wave",
            entity_category="diagnostic",
        ),
    ),
)
