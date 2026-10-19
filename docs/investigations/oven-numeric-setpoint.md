# NV51K7770S numeric setpoint

The owner confirmed numeric Setpoint entry on the NV51K7770S in the
[original hardware investigation](https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md).
This change exposes that control as Home Assistant's native numeric box
for the exact `LCD_OV_WALL_16K` board token reported in `modelNum`.
The upper and lower cavities report the same token; the lower cavity's
`LCD_OV_WALL_16K_DIV` description does not require a broader family match.

The former combined patch applied boxes to shared oven descriptors. This
change opts in only the measured board's `oven_setpoint` descriptor.
Other oven models, ranges, microwaves and other appliances keep sliders.
Cook-time controls are a separate change.

The number platform uses the descriptor's `box_fn` predicate to enable
the numeric field for this model, using the entity's own cavity view.
The existing slider default remains. Entity identity, labels, temperature
conversion, bounds, increments, pending selections and write payloads are unchanged.
No entity migration or translation additions are needed.
Suggestions for future models are documentation only:
[Number control presentation](../number-controls.md).

`tests/test_oven_number_mode.py` covers whole-oven and divided discovery,
similar-board exclusions, representative other appliances, lower-cavity
identity updates, HA Celsius conversion for a Fahrenheit oven, and live
mode limits. The tests use the existing redacted fixtures and simulated
transports. During the final feature review, reconcile the shared number
descriptor with the separate cook-time-parts change.
