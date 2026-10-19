# NV51K7770S cook-time controls

This change replaces the total Cook Time number with **Cook Time Hours** and
**Cook Time Minutes** for `LCD_OV_WALL_16K`, the board reported by the owner's
NV51K7770SG/AA. The upper and lower cavities each retain one duration.

## Why separate hours and minutes

A recipe that calls for 1 hour 30 minutes should be entered as **1 h** and
**30 min**, without first converting it to 90 minutes. The same applies when
reading or adjusting the setting: changing the minutes to 45 should keep the
hour already entered.

These seperated numerica entries also keep it consistent with the OSD UI. Numeric entry also makes it easier to choose an exact time on a phone. A slider
spanning hundreds of minutes puts many values into a short track, where a small
finger movement can overshoot the intended setting. Two whole-number boxes let
the user type the desired hours and minutes directly. Keeping the minute field
between 0 and 59 makes the pair read like a familiar hours-and-minutes timer.

These are two parts of one cook duration, not two independent timers. Keeping
the original total-minute control alongside them would present three editable
views of the same setting, so the pair replaces it.

## Behavior and existing installations

The inputs accept whole hours from 0 to 9 and minutes from 0 to 59. Editing
either component preserves the other. The combined maximum is 9:59, further
restricted by the selected mode's reported limit when present. Invalid or
unreadable values are rejected before writing. The pair uses fixed `h` and
`min` units and feeds the existing `HH:MM:SS` command conversion.

For example, after selecting a mode that accepts these durations:

| Edit | Hours | Minutes | Combined duration |
| --- | --- | --- | --- |
| Enter 1 hour and 30 minutes | 1 | 30 | `01:30:00` |
| Change minutes to 45 | 1 | 45 | `01:45:00` |
| Change hours to 0 | 0 | 45 | `00:45:00` |

An unset idle timer appears blank; an explicitly held zero appears as zero.
The controls use the existing pending settings path: select the cooking mode
before entering the time. Changes to pending-value retention, remote start,
and running-timer writes are separate work. Accepting zero in these inputs
does not establish that the appliance can start remotely without a cook time.

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

## Contributor guide

The following locations describe this implementation. Start with a redacted
diagnostics capture and the target model's timer limits before applying the
same presentation to another model.

| File | What to inspect or change |
| --- | --- |
| [`registry/capabilities/oven.py`](../../custom_components/localthings/registry/capabilities/oven.py) | `OVEN_OPERATIONAL_STATE` contains the `cook_time_hours` and `cook_time_minutes` descriptors. `_cook_time_part_exists` and `_cook_time_total_exists` choose the paired or original control. `_cook_hours`, `_cook_minutes` and `_cook_hours_bounds` provide their values and limits. |
| [`registry/capabilities/cook.py`](../../custom_components/localthings/registry/capabilities/cook.py) | `is_lcd_wall_16k` identifies the verified board. `duration_with_part` preserves the other component and returns total minutes; `duration_parts_max` checks the combined limit against the current mode. |
| [`coordinator.py`](../../custom_components/localthings/coordinator.py) | `async_send_command` combines a component edit using that cavity's current and pending settings, then passes the result through the existing duration handling. `cook_time_is_unset` distinguishes an idle unset timer from an explicitly entered zero. |
| [`registry/entities.py`](../../custom_components/localthings/registry/entities.py) and [`number.py`](../../custom_components/localthings/number.py) | `NumberDesc.mode` selects the widget; `LocalThingsNumber` applies it and displays unset components as blank. The descriptor default remains `slider`. |
| [`__init__.py`](../../custom_components/localthings/__init__.py) | `_drop_cook_times_superseded_by_parts` removes the old number only when both replacements are included for that cavity and the old entry belongs to the same integration entry. |
| [`translations/`](../../custom_components/localthings/translations/) | The two entity names and the three duration-validation errors must be present in every catalog. |
| [`tests/test_oven_duration_parts.py`](../../tests/test_oven_duration_parts.py) | Examples of component edits, combined limits, cavity routing, model isolation, blank/zero handling and registry cleanup. |

To adapt the controls for another verified model:

1. Add a narrowly scoped eligibility check for duration components and use it
   in both existence predicates. Review the model check in `cook_time_is_unset`
   as well. Do not broaden `is_lcd_wall_16k` to mean unrelated boards or assume
   that a similar model name has the same limits.
2. Keep `mode="box"`, `step=1.0`, and fixed `h`/`min` units in the paired
   descriptors. They intentionally have no duration device class: automatic
   conversion of a minute remainder into fractional hours would make the pair
   misleading. Use `value_fn` to display each component of `operationTime`,
   not `remainingTime`.
3. Check limits in both the descriptors and `duration_with_part` /
   `duration_parts_max`. This implementation caps hours at 9, minutes at 59
   and the combined duration at 599 minutes. Another model needs its own
   verified maximum if that differs. Any shorter mode-specific maximum must
   still apply: with a 2-hour maximum, changing 1 h 30 min to
   2 h 30 min must fail even though each field is individually in range.
4. Preserve the single-duration command path and the other component on every
   edit. The descriptors use `cook.PARAM_DURATION_HOURS` and
   `cook.PARAM_DURATION_MINUTES` as their `cook_param`; the coordinator combines
   them before passing total minutes to the existing duration handling. Keep
   the coordinator's cavity view so a lower edit cannot borrow the
   upper cavity's settings. Keep the cleanup checks, document the old entity's
   removal, and add a test showing unrelated models retain their existing
   control.

For a focused software check, run from the repository root after setting up
the development environment:

```sh
.venv/bin/pytest tests/test_oven_duration_parts.py -q
```

For a newly supported model, verify on a phone that both boxes accept exact
values and preserve the other field in either edit order. Check each supported
cavity, blank versus explicit zero, and an upgrade with the old total-duration
entity present. Record which behavior was seen on hardware and which was
covered only by tests. Follow [CONTRIBUTING.md](../../CONTRIBUTING.md) for the
full checks before submitting a code change.

[original]: https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md#settings-and-presentation
