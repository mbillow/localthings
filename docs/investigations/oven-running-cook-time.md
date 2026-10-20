# NV51K7770S running cook-time updates

Changing the cook time during a running Bake returned `4.00 the format of
request json is wrong`. The existing writer sent both `operationTime` and
`remainingTime`. Sending only `operationTime` allows the duration to be changed
or the countdown to be cleared while cooking continues.

The fix applies to the `LCD_OV_WALL_16K` board reported by the NV51K7770SG/AA.
For example, a three-minute duration uses the existing cavity endpoint with:

```json
{"Device":{"Operation":{"operationTime":"00:03:00"}}}
```

Zero sends `"00:00:00"`; the oven displays **None** for the timer and continues
cooking. This changes an already-running cook. Starting without a cook time is
a separate feature.

## Evidence

On October 15, 2026, Lower Bake was started with two minutes, increased to three,
then set to zero. Both edits worked, and Bake continued after the timer showed
None. The [original investigation][original] records the confirmation and the
matching operationTime-only helper in Samsung's older oven plug-in.

## Implementation and checks

[`_cook_time_write` in the oven capability](../../custom_components/localthings/registry/capabilities/oven.py)
uses the model from the cavity's canonical resources to choose the request
fields. It retains the existing minutes-to-`HH:MM:SS` conversion and routing.
`remainingTime` stays appliance-reported telemetry rather than being overwritten
with the requested duration. The model check uses the complete board token;
similar names keep the existing request body.

[`tests/test_oven_running_cook_time.py`](../../tests/test_oven_running_cook_time.py)
covers positive and zero edits for Upper, Lower and Oven routing, including
conversion across an hour. It checks that the write carries no Run/Stop command,
preserves remaining-time telemetry, reports appliance refusals without replay,
and retains the Smart Control requirement. Idle edits still use pending
selections. Upper and Oven routing are covered by simulated responses.

For a focused check after setting up the development environment:

```sh
.venv/bin/pytest tests/test_oven_running_cook_time.py -q
```

When adapting this fix to another model, capture an accepted timer edit before
changing its request fields. Follow [CONTRIBUTING.md](../../CONTRIBUTING.md) for
the full checks. The hours/minutes controls use this same writer and will be
checked with this change during the final feature review.

[original]: https://github.com/roweboat123/localthings/blob/ce0724b8ee209fd244ddded7ac6d46eb38c90f73/docs/investigations/legacy-oven-controls.md#running-cook-time-edits
