# NV51K7770S control follow-up

These changes use LocalThings' existing entities and legacy HTTPS transport.
Hardware evidence is from one NV51K7770SG/AA with `LCD_OV_WALL_16K`, the
`LCD_OV_WALL_16K_DIV` lower record, software `20201110.0138` and firmware
`17071200,17041300`. Other NV51K7770S variants have not been independently tested.

## Cooking controls

The measured start sequence loads settings without `Operation.state`, waits
three seconds, then sends Run to the same cavity. The existing Start button
performs the sequence; users do not need separate setup and start actions.
See [the protocol measurements](oven-cycle-start.md#legacy-https-flex-duo-settings-then-run).

The current sequence requires a fresh, explicit Smart Control enabled reading
from the target cavity immediately before each write. Off, missing, malformed
or failed readings prevent the next write; cached permission and the generic
write-override option cannot waive these transport checks. The coordinator
lock covers the sequence and every request shares the original timeout budget.
A settings refusal prevents Run. Cooking starts on all transports are never
automatically replayed after a transport exception: the user is told to check
the appliance before retrying. Stop and ordinary-command retries are unchanged.

**The cause of one unexpected Smart Control transition remains unresolved.**
The owner reported actual heating after pressing HA Start while the oven display
requested Smart Control activation, with the physical dial untouched. There
is no simultaneous authorization capture and the event was not reproduced.
The fresh checks and no-replay behavior address weaknesses found in code review;
they are not evidence that the reported incident's cause has been fixed.

Before those safeguards, normal Start/Stop worked on both divided cavities,
and the owner confirmed mode visibility with the divider removed. The owner
later clarified that whole-oven cooking itself still needed confirmation at that stage.
After installing the safeguards, one supervised lower-cavity test on October 14
confirmed normal heating and Stop, with the dial remaining on Smart Control:

| Local time | Stage | Result |
| --- | --- | --- |
| 09:05:23.290 | Fresh permission before settings | True |
| 09:05:23.408 | Settings | HTTP 204 |
| 09:05:26.479 | Fresh permission before Run | True |
| 09:05:26.546 | Run | HTTP 204 |
| 09:06:35.622 | Stop | 2.04 |

The supplied raw reading was after Stop: Ready, NoOperation, zero setpoint and
times, and remote control true. It is not an active-cook reading. The revised
sequence has not been hardware-confirmed separately on the divided Upper cavity.
The owner reports having to reactivate Smart Control on the display after Stop;
the relationship between that requirement and the reported true flag is unknown.
The integration does not reactivate Smart Control. A permission read and write
are not atomic at the appliance, and three seconds is a tested delay, not a
measured minimum. No additional heating tests are required for this write-up.

Later on October 14 the owner visually confirmed whole-oven Start and Stop
from HA, using Upper with the divider removed, Bake, 350 F and five minutes.
The accompanying diagnostic reports Bake, desired 350 F, both time fields
00:05:00, Smart Control true and lower divider Off. Both operation and cavity
states are Ready: this records the settings, not an active-cook sample or a
request/response trace. Whole-oven Start/Stop evidence is the owner's observation.

## Settings and presentation

Setpoint and cooking-duration controls use native numeric boxes. On the measured
board an unset idle timer is blank; explicit zero and running values remain visible.
Other oven families retain their total Cook time number and native duration-unit
conversion, including one-minute precision when displaying decimal hours. The
coordinator holds one duration and the appliance receives HH:MM:SS.

The LCD_OV_WALL legacy wall-oven family exposes **Cook time hours** and **Cook time minutes**
as two native number controls per cavity. They replace one component of the
same duration: 1 h plus 30 min means 90 minutes, not two independent timers.
Hours are limited to whole numbers from 0 to 9 and minutes from 0 to 59 in
both entity bounds and command validation. Live modeSpecs can further restrict
the combined duration. Each edit preserves the other
component, and idle edits remain held until Start. Invalid components and an
unreadable current duration are rejected without guessing a replacement.

The pair replaces the original **Cook time** total-duration entity for that family.
Setup removes its old number registry entry only where both replacements exist,
including a renamed entry, and only within the owning config entry. Automations
using that old entity need the paired controls or the existing start_cooking
action's duration parameter. Other oven families keep their original control.

The pair uses fixed units, not HA duration-unit conversion. On the measured board,
both are blank for an unset idle timer and show zero once explicitly set. HA
determines their row layout; no custom frontend is added. Only the NV51K7770S has hardware evidence
so far. Tests cover sibling family identifiers without claiming additional
hardware captures. The owner confirmed both controls appear in Home Assistant
and later reported normal operation after installing the final build. Individual
timer boundaries and the complete entry-order matrix remain software-tested;
no separate hardware captures of those checks were supplied.

Pending temperature and time may be entered before choosing a mode if at least
one available mode accepts the combination. The first mode selection revalidates
and retains those values; selecting the same held mode again preserves them.
Changing mode restores its defaults. Preparation does not select a mode or send
commands on the measured board. The entity view clears previous-cook values
that Start would not use, while raw diagnostics remain unchanged. Validation
errors use the same translated mode labels as the Cooking mode selector.

The owner confirmed numeric Setpoint/Cook time, the blank idle timer, lower-cavity
time-first entry and persistence, revised recipe labels, and removal of the old
Power control. All six entry orders and repeated selection are covered in software
for Upper, Lower and whole-oven configurations; those complete combinations are
not separately hardware-confirmed. Clearing a numeric field can still cause HA's
native service schema to reject an empty string before LocalThings receives it.
This change does not add custom cards, frontend code or a typed-character limit.

## Modes and limits

Panel-started captures verified all eight divided Healthy Cook readback mappings:

| Recipe | Upper code | Lower code | Fahrenheit preset |
| --- | --- | --- | --- |
| Baked potato | UpperHealthycook1 | LowerHealthycook1 | 375 |
| Grilled chicken | UpperHealthycook2 | LowerHealthycook2 | 375 |
| Salmon steak | UpperHealthycook3 | LowerHealthycook3 | 375 |
| White fish fillet | UpperHealthycook4 | LowerHealthycook4 | 400 |

The measured board's lower fallback now includes those four recipes. Recipe
labels omit redundant cavity prefixes and are translated in all ten catalogs;
Samsung's command codes stay unchanged. Other models and unverified whole-oven
Healthy Cook codes retain their numbered labels. Removing the divider correctly
exposes whole-oven modes on Upper and no cooking modes on Lower, as owner-confirmed.

On this board, the Cooking mode selector omits Drain, Autocook, SelfClean,
PyroFree and Descale, and its mode write rejects those choices. The owner
requested excluding cleaning and Gourmet Cook controls from this submission.
The manual explicitly prohibits remote Self Clean; the other exclusions do
not establish a universal firmware prohibition. The raw advertised modes and
reported states remain available in diagnostics, and other models are unchanged.

Whole-oven captures advertise Healthycook1 through Healthycook6. Two additional
panel-started recipes are verified:

| Recipe | Whole-oven code | Reported Fahrenheit target | Adjustment |
| --- | --- | --- | --- |
| Dehydrate | Healthycook5 | 150 | 100–225 F (40–105 C), manual p.34 |
| Bread Proof | Healthycook6 | 100 | Fixed; the panel shows no temperature control |

On October 14 the owner started Dehydrate from the panel with the divider
removed and confirmed its 150 F preset. The capture identifies Healthycook5,
desired 150 F, Operation Run and Oven Cooking. Both cook-duration fields are
00:00:00 and Smart Control is false. This verifies the whole-oven recipe code
and preset, not a remote start or the reported current-temperature accuracy.
The subsequent Bread Proof capture reports Healthycook6, desired 100 F,
Operation Run and Oven Cooking, with zero cook-duration fields, Smart Control
false and the divider out. The owner confirms that neither setup nor cooking
shows a temperature. Both captures report current 175 F; it is not evidence of
a measured 100 F cavity temperature.

The model-specific labels are translated in all ten catalogs. Dehydrate uses
its documented range and captured preset. Bread Proof's HA setpoint displays
100 F with both bounds fixed to 100; other requested targets are rejected.
Neither Celsius preset is guessed: Dehydrate requires a chosen temperature,
and Bread Proof's setpoint control is unavailable in Celsius without a live
modeSpec declaring usable bounds. These are mapping and software checks;
remote starts for the two new recipes remain unverified. Healthycook1–4 still
need whole-oven code-to-recipe confirmation and retain numbered labels.

The [NV51K7770S manual][manual], pp.24, 29 and 34, supplies a 599-minute maximum;
divided Bake/convection/Healthy Cook limits of 175–480 F (80–250 C); and whole-oven
Bake/convection limits of 175–550 F (80–285 C). Steam starts at 230 F (110 C).
These fallbacks are scoped to the measured board; live modeSpecs take precedence.
Celsius recipe presets are not guessed. The paired-cavity restrictions on pp.27–28
are not interpolated, and the appliance can refuse incompatible combinations.
LowerConvectionRoast remains the earlier #572 fallback, but its physical menu
availability is unverified. Broil Hi/Lo and whole-oven Healthycook1–4 meanings
need their own evidence. A recorded omitted-duration Run refusal also remains unexplained;
omission is not silently replaced with zero.

## Status and verification

- **Power:** the LCD_OV_WALL family's ineffective switch becomes read-only status,
  consistent with this owner's observations and #572. Setup removes only an old
  switch whose exact identity/config entry now has a discovered Power sensor.
  Power On does not establish heating.
- **Door:** on the measured board with its connected lower record, the entity
  view shares the upper door reading when the lower items list is empty. This
  was hardware-confirmed. An existing lower reading is preserved, and raw reads
  and diagnostics remain unchanged.
- **Reservoir:** the owner confirmed Closed = In On / Out Off, and Open = In Off /
  Out On, while the main door stayed open. The read-only opening sensor belongs
  to the cavity reporting both switch tokens; missing or contradictory readings
  are Unknown. On LCD_OV_WALL_16K with its matching lower record, the entity view
  also shows this shared reservoir on Lower. Any lower tank-switch tokens take
  precedence over the shared reading. The owner confirmed the original sensor's
  operation; the additional Lower display is software-tested and awaits installation.
  Other existing fixtures with both tokens also bind the sensor, but their position mapping has
  not been independently hardware-tested. No reservoir actuator is added.
- **Divider:** on that same measured board pair, both device pages display the
  divider state from the lower record, including when it is removed. The Upper
  copy uses a separate entity-view value, preserving Upper's connectivity flag.
  It reuses the existing translated label; unknown source readings remain Unknown.
  Both new copies are read-only, preserve existing entity identities and leave
  raw diagnostics, polling and control routing unchanged. Their display in HA
  awaits owner confirmation.
- **Discovery with the divider removed:** the `OV` board token identifies the
  oven even when Lower offers only NoOperation. Previously its `WALL` description
  could fall through to the washer-prefix match during startup. Regression tests
  now verify both shared sensor copies from divider-in and divider-out startup.
- **Write verification:** items match by ID and requested fields, allowing extra
  returned fields and equivalent finite numeric setpoints. IDs and units remain
  exact; ambiguous, missing or mismatched items fail. Individual and batch writes
  share the comparison, including merged options. A stale Run after Stop still
  fails verification until Ready is observed.

Cold idle readings remained at 175 F with desired 0. The existing display filter
therefore showed Unknown; that value is not a measured room temperature. This
change does not alter temperature filtering or claim to fix a physical sensor.

## Configuration switches

The October 14 diagnostic and divided-cavity fixture report `EnergySaving_On`
but no `Sound_*` token in the upper record; the lower has no options array.
Sound was bound whenever the options array existed,
making an absent option appear as an Off switch. It now uses the same
token-presence gate as the other oven option switches. Devices reporting Sound
keep their control. Setup removes the old LCD_OV_WALL Sound registry entry only
when that cavity reports an options list without Sound; unknown resources and
other config entries are preserved.

The owner's panel photos confirm a separate Volume slider spanning 0–15.
On October 14, diagnostics were supplied after setting it to 0 and again to 15,
following instructions to reload the integration. Each download contained both
cavities. The complete main resources, lower resources and raw legacy transport
bodies were identical at both settings; only HA's internal Observe mode-change
timestamp differed. No named Sound/Volume field or indirectly changing value was
found. This rules out a volume reading in those captured responses, not every
possible firmware command or unqueried endpoint. No functional volume slider can
be added from this evidence, and no guessed volume write is introduced.

Energy saving already has that presence gate. Its command sends one
`EnergySaving_On` or `EnergySaving_Off` token to the selected cavity's Mode
resource through the existing legacy transport. On October 14 the owner confirmed
that the existing HA toggle works, in response to an idle HA-Off/panel-readback
check. This is owner-confirmed hardware behavior; no request/response log was
supplied and the Smart Control state at the time of the write was not established.
The existing switch is retained without a command change.

The panel identifies this setting as **12 hour energy saving**: automatic
shutoff after 12 hours of baking or 3 hours of broiling. This is separate from
the maximum 9:59 Cook Time countdown documented on [manual][manual] p.24;
baking can also be started with Cook Time unset. The shutoff setting does not
establish support for a longer countdown.

The coordinator initially displays writes optimistically. An explicit immediate
readback check is currently conditional on Remote Control being off; otherwise
the normal refresh/settle path applies. A brief switch movement or HTTP success
alone is therefore insufficient evidence. The Sound write confirmed in
[the earlier investigation](oven-cycle-start.md#what-is-measured) was on a
different TP2X board and does not prove legacy wall-oven support.

The oven registry reuses the shared `diagnosisStart: Start` command. On October
14, the owner's first logged attempt at 17:59:27 was blocked by LocalThings'
Remote Control guard before transmission. After enabling Smart Connect, the
18:34:49 attempt logged `PUT /diagnosis/vs/0` with success code `0x44`. The legacy
transport sends this as a `Diagnosis` object inside `Device` to `/devices/0`;
the success acknowledgement alone does not confirm execution. The immediate
refresh was suppressed by the coordinator's settling guard, and the supplied
log contains no subsequent diagnosis result. At approximately 18:36 the owner's
HA screenshot showed `Ready`, and the owner reported no oven-screen response.
These are the log/UI's local times. No diagnostic run or result is verified.

The inspected Samsung Oven plug-in APK (v1.1040.8.0) includes generic
`getDeviceDiagnosisById` and `putDeviceDiagnosisById` helpers targeting
`/devices/{id}/diagnosis`, and a `DiagnosisJs` model containing `diagnosisStart`.
The inspected DEX contains no oven-specific caller of those helpers or verified
start/result sequence. That narrower endpoint differs from LocalThings'
aggregate write, but a shared app helper does not establish which write this
oven implements. No alternative diagnosis write was attempted. The Start
diagnosis button is withdrawn specifically for `LCD_OV_WALL_16K`, whose tested
command provides no verified function or useful feedback. This is a conservative
UI decision, not proof that the firmware cannot run diagnostics. Diagnosis status
and Alarm code remain read-only sensors; `Ready` is not evidence of a completed
or passed diagnostic. Other oven boards retain their existing diagnosis button.
Setup removes the withdrawn button's registry row by exact unique ID and config
entry, including renamed rows, so upgrades do not leave an unavailable control.
Automations or dashboards referencing that button need to be updated.

## Lamp investigation closed for this submission

All 21 native resource responses were identical with the lamp on and off.
The app-defined `/devices/0/light` and `/devices/1/light` reads returned HTTP 405
with `Not supported resource`. The inspected Samsung apps supplied no verified
oven-specific lamp command. The exact-model service manual shows a lamp relay,
but a relay on the board does not establish a network API.

An October 14 `/devices` subscription recovered complete door Open/Close events
on `/devices/0/doors`, with no lamp fields or notifications during manual Lamp
changes. The callback needed a bounded body read after a header-parser defect.
The temporary subscription was removed and its absence confirmed. This establishes
local notification delivery, not lamp support or production push-update support.
The standalone capture scripts are research tools, excluded from this submission.

No lamp entity or guessed write is added. Native LocalThings lamp support is an
optional future feature if new protocol evidence emerges; external sensors and
actuators are outside the owner's requested scope. Detailed research is retained
on the [investigation branch][research].

## Kitchen timer investigation

The owner supplied an idle capture and another with the separate kitchen timer
running on October 14. The raw upper and lower bodies expose no countdown,
timer-active flag or additional resource; cooking operationTime and remainingTime
stay 00:00:00. The only device-body change was remoteControlEnabled becoming
false. This provides no timer sensor or control through the examined local
interface, without proving that no undiscovered endpoint exists. Cook duration
must not be presented as the independent kitchen timer.

## Final build acceptance

On October 14 the owner installed build
`a23a0109152ef2754c715b5e3a2199b3a2bcf9c3`, reported that everything seemed to
work normally, and approved preparing the changes for submission. The supplied
HA screenshot confirms that Start diagnosis is gone while Alarm code,
Connection mode and Diagnosis status remain. This is general owner acceptance,
not a separate hardware test of every changed feature or cooking combination.

That build passed all 3,202 tests on Python 3.14.8, plus formatting, lint and
type checks. Subsequent changes add the shared Divider and Reservoir displays
described above; the installed build does not yet include those two copies.
The updated branch, including the five mode exclusions and the verified
Dehydrate/Bread Proof mappings, passes all 3,222 tests, plus formatting, lint
and type checks. All ten translation catalogs are checked for matching keys
and placeholders; the new recipe labels reuse the existing localized terms.
These additions also await installation and owner confirmation.

This test build retains the two-device layout. The requested combined-device
presentation is deferred until the remaining pre-merger checks are finished.
It will use a separate feature branch and pull request, preserving this fixes
branch as the fallback. No device-grouping migration is included here.

## Validation limits

Regression coverage includes both cavity paths, held settings, mode labels,
duration conversion, paired hours/minutes in all 24 setting-entry orders,
strict permission checks, timeout/refusal/no-replay paths,
status migration, Sound capability gating, diagnosis-button cleanup, reservoir
states and partial-item verification. Passing tests do not resolve the reported
Smart Control incident. Revised-sequence divided Upper operation, timer completion,
the complete paired-timer entry-order and boundary matrix, simultaneous cavity
combinations, all recipe starts and running adjustments remain hardware
coverage gaps.

[manual]: https://images.thdstatic.com/catalog/pdfImages/99/9900cc56-8433-4e1b-ba18-4271b575791d.pdf
[research]: https://github.com/roweboat123/localthings/tree/117eade8c5e0ffea1a22799e42ad4c6f27eb3b08/docs/investigations
