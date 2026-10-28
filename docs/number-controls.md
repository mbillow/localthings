# Numeric temperature entry

A numeric box lets an oven user type a target such as 375°F directly.
The default 175–550°F range has 76 values in 5°F increments, making an
exact setting difficult to select with a short touchscreen slider.
Numeric entry retains the existing units, increments and mode limits.

The implementation is limited to the NV51K7770S's exact `LCD_OV_WALL_16K`
board token with `WallOven` subtype. The NW9300MD combination oven shares
that board but reports `CombiOven`, so it keeps its slider. Missing or
unrecognized identity also keeps the existing presentation.

## Code locations

| File | Purpose |
| --- | --- |
| [`registry/capabilities/oven.py`](../custom_components/localthings/registry/capabilities/oven.py) | `_setpoint_uses_box` limits the `OVEN_SETPOINT` number to the verified model. |
| [`registry/entities.py`](../custom_components/localthings/registry/entities.py) | `NumberDesc.box_fn` optionally selects numeric entry from live resources. |
| [`number.py`](../custom_components/localthings/number.py) | `LocalThingsNumber.mode` evaluates the predicate using the entity's own cavity view, otherwise retaining its default. |
| [`tests/test_oven_number_mode.py`](../tests/test_oven_number_mode.py) | Checks scope, cavities, temperature conversion and limits. |

The oven predicate uses both reported identity fields:

```python
def _setpoint_uses_box(resources):
    info = resources.get("/information/vs/0") or {}
    return (
        is_lcd_ov_wall_16k(info.get("x.com.samsung.da.modelNum"))
        and info.get("x.com.samsung.da.deviceSubType") == "WallOven"
    )
```

## Supporting another model

Start with redacted diagnostics and identify the capability owning the
number. Shared oven and range descriptors can affect several appliances;
a similar board name alone is insufficient evidence. Extend the relevant
predicate only after verifying the new model's identity and behavior.
Keep unrelated model names out of the generic number platform.

Predicates receive canonical resources for the entity's own cavity:
the lower cavity's information appears at `/information/vs/0` in that
view even when its wire address ends in `/1`. Preserve the descriptor's
key, units, bounds, increments, write function and translations when
only changing presentation. No entity migration is needed.

Test the new model and every supported cavity, missing identity, similar
identifiers and unrelated appliances. Confirm Celsius/Fahrenheit
conversion and live mode limits, and check exact numeric entry in Home
Assistant on a phone. Existing pending-selection and write behavior
remain unchanged; custom dashboard cards may override presentation.

Run the focused checks while developing:

```sh
.venv/bin/pytest tests/test_oven_number_mode.py tests/test_number_bounds.py -q
```

Before submission, run the required checks in [CONTRIBUTING.md](../CONTRIBUTING.md).
Record hardware observations and software-only coverage separately in the PR.
