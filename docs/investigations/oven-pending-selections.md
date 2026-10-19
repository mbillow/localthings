# Pending oven cooking selections

The NV51K7770S has no default cooking mode. Its temperature and cook-time
controls therefore need to accept preparation before a mode is selected.
Previously, those edits raised `cook_mode_required`; selecting a mode also
discarded earlier entries, even when the same mode was selected again.

## Behavior

- With no selected or usable default mode, a temperature/time combination
  is held only if at least one currently startable mode accepts it. This
  does not choose a mode or send an appliance command.
- The first mode selection validates the pending entries against that
  mode, including its temperature increment. A rejected selection keeps
  the earlier entries so they can be corrected or used with another mode.
- Selecting the same mode again preserves the pending recipe and checks
  it against the current limits. Selecting a different mode uses that
  mode's defaults, as before.
- While preparing a recipe, controls show held values or mode defaults.
  Unset values appear blank instead of displaying unused settings from a
  previous cook; an explicitly entered zero duration remains zero.
- Panel changes, a change of the oven's native temperature unit, or a cook
  starting clear pending selections. Start still validates the final
  recipe and existing Smart Control permission before writing.

This fixes the shared `HeldCook` preparation logic; no board-specific
command, entity migration, numeric-widget change or translation change
is needed. Ovens with a usable default mode continue to validate against
that default when no mode has been selected. Raw diagnostics and probe
items are preserved. Running-timer writes and the start sequence are
unchanged.

## Evidence and coverage

During the NV51K7770S investigation, the owner confirmed lower-cavity
time-first entry and persistence in the combined local build. The earlier
[investigation notes](https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md)
record those observations. This independent change uses the existing
redacted legacy fixture and OCF range fixtures for software coverage.

`tests/test_oven_pending_cook.py` covers all six mode/temperature/time entry
orders for Oven, Upper and Lower; repeated selection; first-mode rejection
without data loss; incompatible combinations; deferred rounding; changed
live limits; native-unit changes; blank versus explicit zero; preservation
of raw diagnostics and probe items; and routing of the final recipe to
the correct cavity. Existing cook tests retain coverage of defaults,
panel changes, active cooking, permission checks and start rejection.
Tests use simulated transports and send no commands to real appliances.

The separate cook-time-parts and Healthy Cook changes should be combined
with this branch during the final feature review to check their shared
duration and preset behavior.
