# RF23DB9750QLAA refrigerator

Hardware observations from the RF23DB9750QLAA owner, 2026-10-17. The
diagnostics identify a TP1X_REF_21K board. These observations apply to this
model; they do not establish the layout of other refrigerators using that
board.

## Cabinet lighting

The owner confirmed that the Cabinet Light control works as expected when
away from the refrigerator. An open door or active approach sensor keeps
the light on despite an off command. These automatic lighting conditions
take priority over manual control.

The Cabinet Light switch now becomes unavailable while a reported door is
open and available again when the doors close. Individual door resources
take precedence over the duplicate aggregate report. Brightness and other
settings remain available. The approach sensor is not used for this gate.

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
