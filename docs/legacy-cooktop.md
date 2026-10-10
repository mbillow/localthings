# Legacy cooktop support

This document records read-only support for the NZ36K7570R radiant electric
cooktop on the legacy HTTPS (TCP 8888) interface. Its TP6X_CT_16K board
reports model `NV9300K-/AA2`.

## Physical controls

The owner's photographs show **Sync Control** for the left pair, **Pan
Size** controls for the center and right-front elements, and a **Smart
Connect** button. The owner calls the synchronized pair burners 1 and 2
and identifies pan-size controls on burners 3 and 5.

[Samsung's specification sheet](https://image-us.samsung.com/SamsungUS/home/home-appliances/cooktops-and-hoods/gas/pd/03202018/NZ36K7570RS.pdf)
lists the following physical layout for NZ36K7570RS/RG:

| Position | Element sizes and controls |
| --- | --- |
| Left front and left rear | Two 7-inch elements with Sync Control |
| Center | 6, 9 and 12 inches; three total pan sizes |
| Right front | 6 and 9 inches; two total pan sizes |
| Right rear | 6 inches; Melt control |

These physical controls do not establish the protocol slot-to-position
mapping or the complete `PanSize` value vocabulary. Keep the reported
slot identifiers and values until observed state changes confirm them.

## Interface

The cooktop serves no OCF at all — only the 8888 REST bridge that
`legacy_http.py` translates into the canonical `{href: rep}` shape. Its
resources follow the `TP6X_CT` row in `legacy_http.py`:

```text
/power/vs/0            (kidsLock fan-out on this unit)
/mode/vs/0             burner state and options
/remotectrl/vs/0       remote-control gate
/information/vs/0      model identity
/diagnosis/vs/0        diagnosis status
/alarms/vs/0           active alarms, as items
```

`/mode/vs/0` carries `x.com.samsung.da.options` — the same options
vocabulary the existing cooktop registry models for the NV9300K/NV8000T
class. The TP6X board extends that shared surface with per-zone
`PanSize` values, `FlexCoil`/`SyncFlex` zone modes, a `Pause` flag and a
`MainTimerSet` option.

## Capabilities

`registry/capabilities/legacy_cooktop.py` binds the options surface:

- per-burner `_state` (with power level, hot surface and pan size as
  attributes where reported), `_power_level`, `_hot_surface`, `_pan_size`
- `cooktop_model`, `cooktop_paused`, `cooktop_sync_flex`,
  `cooktop_flex_coil`
- `main_timer_state`, `main_timer_current`, `main_timer_set`
- shared diagnosis capabilities from `dishwasher.DIAGNOSIS`

Everything is read-only, matching the existing cooktop stance: the local
write contract is unverified and a cooktop must not be remotely ignited
by an automation.

## Verified and not

The reporting unit provides state, power level and hot-surface values for
burner slots 0, 1, 3, 4 and 5. Pan-size values are reported only for slots
3 and 5; no slot 2 is advertised.

Not verified — values are kept raw and no commands are exposed:

- the mapping from protocol slots to physical burner positions
- the complete `PanSize` value vocabulary and its size mapping
- timer units
- flex-coil encoding
- hood writes

## Hood bridge

The hood-bridge mapping handles devices that share the cooktop's `CT` model
token but advertise Bluetooth and fan/lamp options in place of burner
options. `for_device_by_resources` routes this surface to the range-hood
registry, where `HOOD_*` capabilities bind `/mode/vs/0` and `/power/vs/0`.
The reporting unit has no paired physical hood, so these mappings have not
been verified on a live hood.
