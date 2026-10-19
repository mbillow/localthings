# Oven temperature probe detection

The existing read-only probe sensors now appear during normal coordinator
updates when the probe is connected after setup. Unplugging makes them
unavailable; reconnecting reuses the same entities without reloading
LocalThings. Existing entity IDs, custom names and disabled settings are kept.

The sensor platform rechecks presence only for descriptors that opt in. This
change opts in the existing vendor-array and OCF fallback probe sensors. It
does not discover unadvertised resources or change other sensors' setup rules.
The labels are **Temperature Probe** and **Temperature Probe Target**, with
localized names checked in all ten catalogs. Internal keys remain
`food_probe_temp` and `food_probe_setpoint`.

## Why detection happens during normal updates

A temperature probe is a removable accessory. Plugging it in while preparing
food should be enough for its sensors to appear; the user should not have to
reload the integration or remember to connect it before Home Assistant starts.
Detection follows the normal coordinator updates, so it depends on when the
appliance's new socket state reaches LocalThings.

Once created, the sensors remain registered when the probe is unplugged. Marking
them unavailable makes the missing accessory clear while keeping dashboard
references, entity IDs and custom names intact for the next use. Socket state
is essential here: this oven can report a zero temperature both with an empty
socket and with a connected, idle probe, so a zero reading alone cannot decide
whether the sensor is present.

| Probe lifecycle | Home Assistant behavior |
| --- | --- |
| First setup with no probe connected | Wait to create the probe sensors. |
| Connect the probe after setup | Create them when an update confirms presence. |
| Unplug after detection | Keep the entities, marked unavailable. |
| Reconnect | Reuse those entities and retain user settings, including deliberate disabling. |

## Hardware evidence

On October 15, 2026, the NV51K7770SG/AA reported `meatprobe_connected` and
temperature item `id: "1"`. Connected idle readings were zero. During a
panel-started Bake cycle with a 150°F probe target, item 1 reported the 150°F
target and a current reading of 107°F in the resource snapshot. This confirms
the existing mapping on that model; it does not establish remote probe writes
or the accuracy of readings below 100°F.

The owner confirmed unplug/reconnect behavior without a reload in the earlier
combined-device build. The [original investigation][original] records those
observations. This independent extraction preserves the two-device layout;
that layout and the OCF paths have software coverage rather than new hardware
confirmation. Connected idle zero readings remain unchanged.

## Regression coverage

Tests exercise startup with and without a probe, detection after setup,
repeated updates, unplug/reconnect, unload and reload, and label upgrades in
all ten languages. They cover legacy HTTPS, OCF vendor arrays and OCF fallback
resources, preserve custom names and disabled entities, and require both a
probe item and a socket state for the vendor-array sensors. Native-speaker
translation review is not claimed.

## Contributor guide

| File | What to inspect or change |
| --- | --- |
| [`registry/entities.py`](../../custom_components/localthings/registry/entities.py) | `SensorDesc.dynamic_presence` is an opt-in flag, defaulting to `False`. |
| [`sensor.py`](../../custom_components/localthings/sensor.py) | `async_setup_entry` retains opted-in descriptors that are initially absent. Its update listener creates each detected sensor once; `LocalThingsSensor.available` checks presence after creation. The listener is removed when the integration entry unloads. |
| [`entity.py`](../../custom_components/localthings/entity.py) | `_is_included` evaluates the descriptor's `exists_fn` using its own subdevice's canonical resources. |
| [`registry/capabilities/oven.py`](../../custom_components/localthings/registry/capabilities/oven.py) | `OVEN_SETPOINT` contains the vendor-array probe sensors; `OVEN_PROBE_CURRENT_OCF` and `OVEN_PROBE_DESIRED_OCF` provide the OCF fallback. `_probe_connected` and `_ocf_probe_connected` check socket state; `_probe_item` finds the vendor item by ID. |
| [`tests/localthings/test_oven_probe.py`](../../tests/localthings/test_oven_probe.py) | Lifecycle tests cover detection, repeated updates, reconnects, unload, custom names and disabled entities. |

For another removable sensor, first capture its connected and disconnected
resources and define an `exists_fn` in that appliance's capability module.
Use a reported connection state when available; do not treat zero or a missing
temperature as a universal disconnection signal. Add `dynamic_presence=True`
only to the relevant `SensorDesc` definitions and preserve their existing keys
if they already have entities. Model-specific presence rules belong in the
capability, rather than in the shared sensor platform.

This mechanism rechecks descriptors already bound during discovery. It does
not search for new resource paths. If an accessory's resource is not discovered
until connection, that requires separate discovery support. Likewise, keep
the existing vendor/OCF fallback selection so one physical reading does not
create duplicate sensors.

For a focused check after setting up the development environment:

```sh
.venv/bin/pytest tests/localthings/test_oven_probe.py -q
```

For a newly supported accessory, verify first connection after setup, unplug
and reconnect without a reload, and repeated updates without duplicate entities.
Include software coverage for a renamed or disabled entity and for unloading
before first connection. Record the hardware evidence separately from simulated
cases and follow [CONTRIBUTING.md](../../CONTRIBUTING.md) for the full checks.

[original]: https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md#temperature-probe-follow-up
