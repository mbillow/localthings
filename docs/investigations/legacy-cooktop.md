# NZ36K7570R timer and burner sizes

Hardware observations on 2026-10-16, using the TP6X_CT_16K legacy HTTPS
interface. The device reports DeviceType_NV9300K-/AA2. The observations
below do not establish units or ring-size meanings for other models, including
those using the same board. The mappings require both TP6X_CT_16K and the
exact DeviceType_NV9300K-/AA2 value; missing identity data keeps the generic profile.

## Timer

A timer set locally to ten minutes reported MainTimerCurrent_10,
MainTimerSet_10 and MainTimerState_Run. About two minutes later, both
numeric fields reported 8 while the state remained Run. Both fields therefore
report remaining minutes on this appliance; Set does not retain the original
duration. All burners were off during this test.

Keep the existing, enabled main_timer_set entity as Timer Remaining with
duration class and minute units, preserving its entity ID. Do not create a
second entity for main_timer_current on this family; remove its old registry
entry during setup once the replacement is available. Automations using the
removed entity must use Timer Remaining. Both raw fields remain in diagnostic
downloads. Timer Status continues to report the device value without deriving
completion from a zero countdown.

A diagnostic downloaded during the audible alert reported both time fields
as zero and the state as Ready, with an empty alarm list. The user separately
observed Finished on the Home Assistant timer-status sensor while beeping.
No capture contains that transient value, so it is a user-observed state,
not a raw fixture or a measured guarantee that polling always catches it.

## Burner sizes

| User-selected burner | Reported option | Observed operation |
| --- | --- | --- |
| 3, triple rings | PanSize3_Triple | Ready at power 0, then Run at power 3 |
| 5, larger size | PanSize5_Dual | Run at power 3 |

The user confirmed that the updated Home Assistant Ring Size sensors report
the selected sizes correctly.

Both slots also reported Single in earlier captures. Idle was present for
slot 3 in the original support fixture. FlexCoil_0 remained unchanged in
these tests; it cannot yet be interpreted as the selected ring count.
The user also reported trying every panel button while Flex Coil Code
remained 0. This observation does not establish a function for that field.
Physical front/back/left/right positions have not been verified.

Ring sizes use translated enum states idle/single/dual/triple, retaining the
existing entity IDs. Automations comparing the old capitalized raw values
must use the lowercase enum values. Unknown device values remain visible.
The Flex Coil diagnostic stays raw and is disabled by default for this
family. Existing entity enablement is preserved. No remote cooktop writes
are introduced.

## Paused

Every captured options array reports Pause_Off. Samsung's
[2022 product catalog](https://image-us.samsung.com/SamsungUS/samsungbusiness/builder/product-catalog/catalog.pdf)
lists no All-Pause feature for the NZ36K7570R. Paused therefore defaults to
disabled in the legacy radiant profile; other cooktop profiles are unchanged.
Existing entity enablement is preserved, so an already enabled Paused entity
must be disabled once in Home Assistant. The raw field remains available in
diagnostic downloads.

## Sync Control and hood scope

The existing Sync Control sensor reads SyncFlex_On/Off. Samsung describes
Sync Control as operating the two left elements together for large cookware
or a griddle, separately from Bluetooth hood connectivity in its
[product catalog](https://image-us.samsung.com/SamsungUS/samsungbusiness/samsung-builder/pdfs/Samsung_Home_Appliances_2019_Product_Catalog.pdf).
The raw captures contain only SyncFlex_Off. The user subsequently identified
Sync Control with burners 1 and 2 and supplied a Home Assistant screenshot
showing Sync Control On; no raw capture of that active state is available.
FlexCoil is a separate raw field with an unverified meaning.

All six captures also expose /device/1 with description TP6X_CT_16K_HOOD,
DeviceType_NULL, BTState_Idle and Bluetooth_Off, despite no hood being paired.
FanSpeed and both shutoff timer values are 0, Lamp and Power are Off, and
ShutOffTimerState is Ready. Generic power/remote-control readings previously
caused this placeholder to appear as a second cooktop with eight entities.

The cooktop registry excludes entities for this exact empty-slot signature,
so the existing subdevice liveness check skips the placeholder. Missing or different
identity/state fields keep the existing discovery behavior. The skipped
resources remain in diagnostic downloads, and Home Assistant can remove an
existing placeholder device after reload using the normal Delete device action.
The fixture legacy_cooktop_empty_hood.json retains the captured canonical
resources without connection or account identifiers. No paired hood was tested.

No new hood mapping or control was included at the time: no compatible hood
was available for hardware verification. The existing upstream hood
resources for other appliances are unchanged.

An NZ30K7570RS with a paired NK9300KM hood (issue #676) later showed the
populated slot: DeviceType_NK9300KM, Bluetooth_On, FanSpeed_1 and Lamp_Off,
with no BTState option. Its fan speed, light, Bluetooth link and model now
read as paired-hood sensors, and the burner and timer entities no longer bind
on that slot. A second capture with the hood running showed power On,
FanSpeed_2, Lamp_On, ShutOffTimerSet_10, ShutOffTimerCurrent_9 and
ShutOffTimerState_Run. Three more captures over the next seven minutes
showed the timer counting down in minutes (9 to 4 over five minutes) and the
fan at speeds 1 to 3. Once the hood turned off, power read Off while
FanSpeed_3 and Lamp_On held, so the slot's power reads as the paired hood's
power. The reporter confirmed the light runs on its own and the fan offers
Low, Med, High and Boost, with FanSpeed_2 as Med. A later capture with the
fan on Boost showed power On and FanSpeed_4, with the shutoff timer at Ready
and the last set value of 10 retained. The fan reads as a named setting.

With the reporter watching the hood on 2026-10-23, every write below was a
one-token aggregate `PUT /devices/1`, accepted with 204:

| Write | What the hood did |
| --- | --- |
| Lamp_On, Lamp_Off | Light on and off, with the fan off |
| power On | Fan started on Low, even with FanSpeed_4 stored |
| power Off | Fan stopped; FanSpeed kept its value |
| FanSpeed_2, 3, 4 while running | Med, High, Boost |
| FanSpeed_2 while off | Accepted, ignored |
| power On and FanSpeed_2 in one body | Started on Low; the speed was ignored |
| power On, then FanSpeed_3 0.4 s later | Started, then High |
| power On while running on Med | Dropped to Low |

The hood is therefore exposed as a fan whose speed is written after power
On, and a light.

A second NZ30K7570RS (TP6X_CT_16K, NA9300K-/AA1) paired to an NK9300KM
confirmed those fan and light writes, and tested the shutoff timer (PR #677).
`ShutOffTimerSet_N`, as an aggregate or `PUT /devices/1/mode`, sets N minutes
while the fan runs:

| Write | What the hood did |
| --- | --- |
| ShutOffTimerSet_1, 5, 10, 20, 35, 99 | Panel showed the minutes; read back Set_N, Current_N, Run |
| ShutOffTimerSet_0 | Timer cancelled, fan kept running; read back Set_0, Current_0, Ready |
| ShutOffTimerSet_100 to 255 | Counted down, but the two-digit panel showed only the last two digits |
| ShutOffTimerSet_256, 999 | Wrapped to one byte: 0 (cancelled) and 231 |
| ShutOffTimerState_Ready | Read back Ready, but the countdown continued |
| ShutOffTimerCurrent_7 idle, _3 running (#676's hood) | Rejected with 400; panel unchanged |
| power Off | Cleared a running timer |

On issue #676's own hood on 2026-10-26, power On then ShutOffTimerSet_5
from off started the fan on Low with 5 on the panel, and the 20 and 0 writes
behaved as above. ShutOffTimerSet_0 with the fan off left it off. A timer of
1 turned the hood off about 30 seconds later, reading back Set_1, Current_0
and Ready, so Current counts whole minutes rounded up.

The timer is therefore exposed as a number from 0 to 99 minutes, 0
cancelling. Setting it while the fan is off is refused with an error
rather than turning the fan on, which a number entity shouldn't do as a
side effect; cancelling always goes through. It reads
ShutOffTimerCurrent, because ShutOffTimerSet keeps its last value after the
timer ends. The second reporter found the hood reads back Set_N, Current_N
and Run within 5 seconds of a write, while Home Assistant kept showing the
old value for about 45 seconds, until the write's settle window ended. A
timer write therefore also shows Current_N and Run (or Current_0 and Ready
when cancelled) in Home Assistant right away. Only Set is sent to the hood,
which rejects a written Current.

A 204 and a changed read-back don't prove the hood acted. A few minutes after
the cooktop was power-cycled, a Lamp_Off/Lamp_On pair did both without the
light moving, and the same writes worked later. The Bluetooth link to the hood
may not have been back yet.

The fixture legacy_cooktop_observations.json contains only the relevant
options arrays extracted from the user's diagnostics. Its state reflects
the coordinator resource snapshot; the transport's discovery cache can be
older and is not a second synchronized measurement.
