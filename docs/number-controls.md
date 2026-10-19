# Numeric temperature entry: rationale and contributor guide

This guide explains why a numeric box is useful for temperature setpoints
and how a contributor can enable it for another verified model. The code
on this branch enables it only for the NV51K7770S board `LCD_OV_WALL_16K`.
Follow the steps below when proposing support for another model.

## Why use a numeric box for a temperature setpoint?

When setting an oven to 375°F, the user has a specific number in mind.
A slider requires finding that value along a track. On a phone, small
finger movements can overshoot the desired setting, and the finger can
obscure the slider thumb. Selecting an exact temperature can therefore
require repeated adjustments even when the desired number is already known.

The oven's fallback Fahrenheit range is 175–550°F in 5°F increments:
76 selectable values along one slider. A numeric box lets the user type
`375` directly and check the displayed value, without repeatedly adjusting
the slider. Individual cooking modes can further restrict that range.
This makes numeric entry a better fit for an exact temperature target.

The box still uses the existing temperature units, increments and mode
limits. It does not make unsupported temperatures valid: conversion and
validation can still round or reject an entry. A consistent
interface can use numeric entry for exact targets without making every
number control look the same.

## Find the code that owns the control

Start from a redacted diagnostics download for the model being added.
Identify its temperature resource and the reported model identity. Then
find the matching `NumberDesc` in the capability registry. From the
repository root, the oven example can be located with:

```sh
rg -n 'key="oven_setpoint"|box_fn|_setpoint_uses_box' custom_components/localthings
```

| File | Relevant code |
| --- | --- |
| [`registry/capabilities/oven.py`](../custom_components/localthings/registry/capabilities/oven.py) | `OVEN_SETPOINT`, its `NumberDesc`, and `_setpoint_uses_box` |
| [`registry/entities.py`](../custom_components/localthings/registry/entities.py) | `NumberDesc.box_fn`, an optional predicate returning whether to use a box |
| [`number.py`](../custom_components/localthings/number.py) | `LocalThingsNumber.mode`, which returns `BOX` when the predicate matches and otherwise retains the slider |
| [`tests/test_oven_number_mode.py`](../tests/test_oven_number_mode.py) | Model scope, cavity handling, unit conversion and limit checks |

This guide assumes the `box_fn` support from this branch is present.
The oven's `/temperatures/vs/0` descriptor is already wired with
`box_fn=_setpoint_uses_box`. Other oven resources can have the same
`oven_setpoint` key, so match the actual resource as well as the key.
The oven and range registries also share capabilities; a change to a
shared descriptor can affect more than one appliance type.

## Enable another verified model

For another oven using `OVEN_SETPOINT`, the relevant code to change is
`_setpoint_uses_box` in `registry/capabilities/oven.py`. Its current body is:

```python
def _setpoint_uses_box(resources):
    model = (resources.get("/information/vs/0") or {}).get("x.com.samsung.da.modelNum")
    return isinstance(model, str) and model.split("|", 1)[0] == "LCD_OV_WALL_16K"
```

If the additional model uses the same pipe-separated `modelNum` format,
extend the exact-token match while retaining the existing board:

```python
def _setpoint_uses_box(resources):
    model = (resources.get("/information/vs/0") or {}).get("x.com.samsung.da.modelNum")
    return isinstance(model, str) and model.split("|", 1)[0] in {
        "LCD_OV_WALL_16K",
        "REPLACE_WITH_VERIFIED_BOARD_TOKEN",
    }
```

Replace the placeholder with the token from the new model's diagnostics;
do not commit the placeholder. Confirm that the identifier is specific
enough for the intended scope. If several consumer models share a board
token, use an additional reported identity field when needed to avoid
including models outside that scope. Do not broaden the check to a
partial prefix such as `LCD_OV_WALL`.

For a control owned by a different capability, add a model-specific
predicate beside that capability and assign it to that control's
`NumberDesc.box_fn`. Use the actual identity fields reported by that
appliance. A predicate receives the entity's own canonical resources:
the lower cavity's information is available as `/information/vs/0` in
that view, even though its wire address ends in `/1`.

Keep board names in the capability. Do not change
`LocalThingsNumber._attr_mode` from `SLIDER` to `BOX`, or add a model
check to the generic number platform. Once `box_fn` support is present,
an additional model does not need changes to `number.py` or `entities.py`.
Leave the descriptor's key, unit functions, bounds, step, write function
and translations intact when the change is presentation only.

## Verify the change

Use `tests/test_oven_number_mode.py` as the starting example. Add the new
model's redacted fixture and check that its target uses a box. Check that
missing identity data, similar identifiers and unrelated models still
use their original presentation. Include each supported cavity or
compartment so the predicate cannot accidentally use another one's identity.

Confirm that the existing Celsius/Fahrenheit conversion, allowed range,
increments and mode-specific limits still apply. In Home Assistant,
check numeric entry on a phone, enter an allowed target and confirm the
displayed value and existing command behavior. Numeric entry does not
change whether the integration holds an idle setting or sends it
immediately. Custom dashboard cards may override the default presentation.

Run the focused checks while developing:

```sh
.venv/bin/pytest tests/test_oven_number_mode.py tests/test_number_bounds.py -q
```

Before submitting a code change, run the formatting, lint, type and full
test checks in [CONTRIBUTING.md](../CONTRIBUTING.md). In the PR, identify
the model and control, explain the exact-entry problem on a touchscreen,
and state how unchanged behavior on other models was checked.
