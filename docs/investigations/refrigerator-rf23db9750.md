# RF23DB9750QLAA refrigerator

Hardware observations from the RF23DB9750QLAA owner, 2026-10-17. The
diagnostics identify a TP1X_REF_21K board. These observations apply to this
model; they do not establish the layout of other refrigerators using that
board.

## Door reporting

The owner checked the physical doors against the existing Home Assistant
sensors. The resource paths below come from the corresponding LocalThings
capabilities and diagnostics.

| Entity label | Resource | Physical doors reported |
| --- | --- | --- |
| Fridge Door | `/door/cooler/0` | Both upper doors and the Beverage Center door share this sensor. |
| Freezer Door | `/door/freezer/0` | Bottom-left freezer door. |
| Flex Zone Door | `/door/cvroom/0` | Bottom-right FlexZone door. |

Use the door name as the entity label; the door binary sensor already
displays Open or Closed as its state. The existing compartment-name
translations map `cvroom` to Flex Zone. The shared Fridge Door state cannot
identify which of its three doors is open.

## Cabinet lighting

The owner confirmed that the Cabinet Light control works as expected when
away from the refrigerator. An open door or active approach sensor keeps
the light on despite an off command. These automatic lighting conditions
take priority over manual control.

The owner also confirmed that the Cabinet Brightness modes work as expected.

The Cabinet Light switch now becomes unavailable while a reported door is
open and available again when the doors close. Individual door resources
take precedence over the duplicate aggregate report. Brightness and other
settings remain available. The approach sensor is not used for this gate:
its reported sense level has not been verified as a live motion signal.

On 2026-10-17, the owner confirmed the test build's door labels, Cabinet
Light availability after opening and closing a door, and continued access
to brightness settings all worked as intended.

## Auto Door Open

The owner confirmed that the Auto Door Opener switch disables and re-enables
the appliance's automatic opening feature. It writes
`x.com.samsung.da.ado.devicecontrol` at `/status/lock/vs/0`; this is a setting,
not a command to open a door.

## Water filter percentage

The supplied diagnostics report `filterUsage: "82"` and
`filterStatus: "normal"`. LocalThings currently exposes that value as Filter Usage, a
percentage used rather than remaining. The recorded RF29DB9750QLAA reset
in [filter-reset.md](filter-reset.md) changed usage from 100 to 0 and status
from replace to normal, supporting that interpretation on the same board
family. No reset was performed on this RF23DB9750QLAA during this review.

The owner chose to retain the existing Filter Usage sensor and its
increasing percentage. No filter conversion or sensor change is planned.

## Welcome lighting proximity

The owner reports a Far setting alongside a Near sense-level diagnostic,
matching the supplied capture: `currentLevel: "3"` and
`desiredSenseLevel: "1"`. LocalThings uses `currentLevel` for the writable distance setting and
`desiredSenseLevel` for the separate, disabled-by-default diagnostic.
The two fields also advertise different supported lists. Their unequal
values do not establish that the distance setting failed; the sense field
has not been verified as a live detection signal on this appliance.

With Welcome Lighting enabled, the owner stepped away until the light
went out and reported that the sense-level diagnostic remained Near. Its
Home Assistant history also showed Near continuously since the previous
day. This test does not support using it for presence detection or to gate
the Cabinet Light control. Retain the existing disabled-by-default
diagnostic; the working distance selector remains available.

The supplied capture contains 47 tracked resources, with no explicit
motion or occupancy field identified. It already lists `/proximity/vs/0`
among the active Observe subscriptions. The owner subsequently captured
`/proximity/vs/0` and `/cabinet/light/total/vs/0` with `observe_resource`.
Both returned a successful GET, `already_observed: true`,
`session_dropped: false`, and no notifications. The proximity GET reported
`currentLevel: "3"` and `desiredSenseLevel: "1"`; the light GET reported
`lightControl: "Off"` and `lightLevel: "33"`.

These captures found no pushed motion or automatic-light events. The owner
then supplied direct `/device/0?if=oic.if.b` reads labelled light Off and
On. Both succeeded with code 2.05 and contained 45 resources plus the
collection header. A comparison of every returned field found only three
changes, all cumulative energy counters. All three doors reported Close
in both captures.

The proximity values remained `currentLevel: "3"` and
`desiredSenseLevel: "1"`. Cabinet light control remained Off and its level
remained 33; the enhanced lighting resource was also identical. Neither
the pushed events nor these direct reads exposed a motion state or a
reliable indication of automatic welcome-light activity. Do not derive a
motion sensor from these fields or the cumulative energy
changes. This conclusion covers the resources tested, not hypothetical
undocumented interfaces.
