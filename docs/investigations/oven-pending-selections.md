# Pending oven cooking selections

The NV51K7770S has no default cooking mode. Its temperature and cook-time
controls therefore need to accept preparation before a mode is selected.
Previously, those edits raised `cook_mode_required`; selecting a mode also
discarded earlier entries, even when the same mode was selected again.

## Why entry order should not discard settings

Users may enter a recipe in the order they read it: time first, then temperature,
then cooking mode. Requiring a particular order adds an unnecessary correction
step, and silently dropping earlier entries makes it harder to tell what Start
will actually send. Pending selections let the controls retain a valid recipe
while it is being assembled.

For example, on an idle cavity with no default mode, enter **20 minutes**, then
**400°F**, then choose a Bake mode that accepts both. The time and temperature
remain selected. Choosing that same Bake mode again keeps them; deliberately
switching to another mode loads its defaults. Before a mode is chosen, the
combination is retained only if at least one available startable mode accepts
it. This example prepares settings without starting the oven.

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

## Contributor guide

The change belongs in the shared pending-recipe logic, where mode, temperature
and duration can be validated together. Keeping separate copies in individual
number or select entities would make edit order and cavity isolation harder
to maintain.

| File | What to inspect or change |
| --- | --- |
| [`registry/capabilities/cook.py`](../../custom_components/localthings/registry/capabilities/cook.py) | `HeldCook.hold` validates edits before replacing stored values. `overlay` presents held choices and mode defaults; `_expire` clears them after relevant appliance changes; `plan` performs final recipe validation through `plan_start`. `_device_values` includes the oven's native unit so a unit change invalidates earlier inputs. |
| [`coordinator.py`](../../custom_components/localthings/coordinator.py) | `_holds_cook` chooses when edits are held, `_hold_cook` keeps one `HeldCook` per cavity and converts duration input from minutes to seconds, and `_with_held_cooks` supplies the displayed view. `async_start_cooking` applies the existing permission check and sends the final recipe. |
| [`tests/test_oven_pending_cook.py`](../../tests/test_oven_pending_cook.py) and [`tests/test_cook_start.py`](../../tests/test_cook_start.py) | Cover entry permutations, defaults, rejected edits, reselecting or changing a mode, changed limits, panel changes and final start validation. |

When changing this behavior, preserve these rules:

1. Validate the whole pending combination before committing an edit. With no
   usable default or chosen mode, one candidate must accept both temperature
   and duration; acceptance by two different modes is insufficient. A candidate
   check must not silently select a mode or apply its temperature rounding.
2. Apply the explicitly chosen mode's increment and limits on first selection.
   Keep earlier entries if validation fails. Recheck the same mode against
   current limits when it is selected again, and retain the existing behavior
   of loading defaults when the user chooses a different mode.
3. Build the displayed overlay without changing the raw appliance snapshot or
   unrelated temperature items such as the probe. Distinguish an unset value
   from explicit zero. Preserve the native-unit expiry check so a pending
   Fahrenheit value cannot later be interpreted as Celsius.

For a focused check after setting up the development environment:

```sh
.venv/bin/pytest tests/test_oven_pending_cook.py tests/test_cook_start.py -q
```

Use the six entry-order cases as a template when adding another cooking control.
Check that preparation makes no appliance writes, each cavity retains only its
own values, rejected edits preserve the prior recipe, and Start still validates
the final combination. These rules apply to ovens using `HeldCook`; they do not
change other appliances' input handling. Follow
[CONTRIBUTING.md](../../CONTRIBUTING.md) for the full checks before submission.
