# NV51K7770S Power status

The oven reports Power, but its existing switch did not operate the appliance
in the owner's hardware tests. A read-only Power sensor preserves that report
without presenting an ineffective control.

## What the reading means

`On` is the oven's reported power state. It does not establish that a cavity
is heating: both cavities reported Power On alongside Ready and NoOperation
while idle. Use the cavity's Running or Machine State entity to follow cooking.
This is not a power-consumption measurement.

The sensor reads `x.com.samsung.da.power` from `/power/vs/0` (or the discovered
cavity's indexed equivalent). `On` and `Off` become the corresponding binary
states; absent or unrecognized values remain unknown.

## Scope and implementation

The change applies only in the oven registry when the first `modelNum` component
is exactly `LCD_OV_WALL_16K`, the board reported by the tested NV51K7770S.
It does not match every `LCD_OV_WALL` board. A native `/power/0` resource retains
its existing precedence and behavior.

- `registry/capabilities/oven.py` specializes the existing
  `common.POWER_VS_FALLBACK` capability. It keeps the polling tier, field and
  entity key, selecting its binary sensor for the measured board.
- `registry/by_type/oven.py` registers that specialization. Other appliance
  registries continue using their existing Power capabilities.
- `__init__.py` removes an old switch only when discovery includes its
  replacement from this capability. The lookup matches the integration,
  platform, complete unique ID and owning config entry, including the cavity
  prefix. It works with renamed or disabled switches and can run again safely.

The replacement uses the existing `power_switch` binary-sensor translation in
all ten languages. No translation changes are required.

## Evidence and regression checks

The owner's NV51K7770S observations are recorded in the
[original hardware investigation](https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md).
The existing `oven_lcd_ov_wall_16k_divided_8888.json` fixture captures idle Power On.

The [review of #637](https://github.com/mbillow/localthings/pull/637#issuecomment-6255169643)
identified an overly broad Power change across `LCD_OV_WALL` boards. It also
identified separate Sound and Reservoir changes affecting the NW9300MD microwave,
a TP1X range and a TP2X wall oven. This feature leaves those entity lists intact.

`tests/test_oven_power_status.py` covers cavity readings, exact model boundaries,
registry cleanup, offline snapshot restoration, native Power precedence, and the
shared laundry Power rules. It explicitly checks that the three appliances named
in the review keep their Power switches and that cleanup leaves them alone.
The existing golden files are unchanged.

Lower-cavity discovery after starting without a divider is handled separately by
the divider-recovery feature. This change follows the cavities already discovered.
