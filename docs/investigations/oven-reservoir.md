# NV51K7770S reservoir position sensor

The oven reports two reservoir position switches in Upper's mode options, but
LocalThings does not expose them as a sensor. This change adds a read-only
Reservoir entity with Open/Closed states. It measures the reservoir's position,
not its water level.

## Mapping

The owner opened and closed the reservoir while the oven door stayed open.
The `/mode/vs/0` options reported these pairs:

| `waterTankInSwitch` | `waterTankOutSwitch` | Reservoir |
| --- | --- | --- |
| `On` | `Off` | Closed |
| `Off` | `On` | Open |

Missing, contradictory or unrecognized pairs report Unknown. Both tokens must
be present to create the entity from a populated mode resource. A discovery
stub follows the existing options-sensor discovery convention.

## Implementation

`registry/capabilities/oven.py` adds the binary sensor to `OVEN_MODE`, using
the `opening` device class and entity key `reservoir_open`. The model check
requires the exact `LCD_OV_WALL_16K` board token. The sensor reads that cavity's
own options; showing it on the other cavity is a separate sharing change.

The existing mode-resource updates supply its state. All ten translation
catalogs include its name.

## Evidence and verification

The 14 October 2026 observations are preserved in
`tests/fixtures/oven_lcd_ov_wall_16k_reservoir.json` and the
[original investigation](https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md).

`tests/test_oven_reservoir.py` covers both measured positions independently of
the door, inserted/removed divider startup, missing and conflicting switch
values, model exclusions, raw-data preservation and all ten names. This branch
is based directly on upstream and has no dependency on the shared-sensor or
divider-recovery changes.
