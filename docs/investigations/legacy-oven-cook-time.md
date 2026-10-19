# NV51K7770S cook-time controls

This change replaces the total Cook Time number with **Cook Time Hours** and
**Cook Time Minutes** for `LCD_OV_WALL_16K`, the board reported by the owner's
NV51K7770SG/AA. The upper and lower cavities each retain one duration. Other
board identifiers keep their existing total-duration control.

The inputs accept whole hours from 0 to 9 and minutes from 0 to 59. Editing
either component preserves the other. The combined maximum is 9:59, further
restricted by the selected mode's reported limit when present. Invalid or
unreadable values are rejected before writing. The pair uses fixed `h` and
`min` units and feeds the existing `HH:MM:SS` command conversion.

An unset idle timer appears blank; an explicitly held zero appears as zero.
The controls use the existing pending settings path: select the cooking mode
before entering the time. Changes to pending-value retention, remote start,
and running-timer writes are separate work.

Setup removes the original number's registry entry only within its own config
entry and only when both replacement numbers are included. Matching uses the
unique ID, so a renamed entity is handled too. Automations or dashboards using
the removed entity must use the paired controls or the existing
`localthings.start_cooking` duration parameter.

## Evidence and verification

The owner confirmed both inputs appeared and worked normally in the earlier
combined build. The [original investigation][original] records those observations
and the manual reference for the 9:59 limit. No additional hardware tests were
performed for this extraction, and no other board is claimed as verified.

Regression tests cover both edit orders, upper/lower and undivided routing,
combined limits, blank versus explicit zero, unreadable durations, preservation
of other models, and registry cleanup. Boundary values and the edit-order
matrix are software-tested rather than additional hardware observations.
Names and validation errors are included in all ten translation catalogs.

[original]: https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md#settings-and-presentation
