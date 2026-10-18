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

No new hood mapping or control is included: no compatible hood is available
for hardware verification. The existing upstream hood resources for other
appliances are unchanged.

The fixture legacy_cooktop_observations.json contains only the relevant
options arrays extracted from the user's diagnostics. Its state reflects
the coordinator resource snapshot; the transport's discovery cache can be
older and is not a second synchronized measurement.
