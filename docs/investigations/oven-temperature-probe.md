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

[original]: https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md#temperature-probe-follow-up
