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
and the owner reported Smart Control operation with the divider removed.
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
sequence has not been hardware-confirmed separately on Upper or the whole oven.
The owner reports having to reactivate Smart Control on the display after Stop;
the relationship between that requirement and the reported true flag is unknown.
The integration does not reactivate Smart Control. A permission read and write
are not atomic at the appliance, and three seconds is a tested delay, not a
measured minimum. No additional heating tests are required for this write-up.

## Settings and presentation

Setpoint and Cook time use native numeric boxes. On the measured board an unset
idle Cook time is blank; explicit zero and running values remain visible.
The native duration unit setting converts value, bounds, step and writes together,
so selecting hours preserves one-minute precision. This avoids the rounding of
integer-minute values in the development Core 2026.9.4 environment. The coordinator
still holds minutes and the appliance receives HH:MM:SS.

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

The [NV51K7770S manual][manual], pp.24, 29 and 34, supplies a 599-minute maximum;
divided Bake/convection/Healthy Cook limits of 175–480 F (80–250 C); and whole-oven
Bake/convection limits of 175–550 F (80–285 C). Steam starts at 230 F (110 C).
These fallbacks are scoped to the measured board; live modeSpecs take precedence.
Celsius recipe presets are not guessed. The paired-cavity restrictions on pp.27–28
are not interpolated, and the appliance can refuse incompatible combinations.
LowerConvectionRoast remains the earlier #572 fallback, but its physical menu
availability is unverified. Broil Hi/Lo and whole-oven recipe meanings need their
own evidence. A recorded omitted-duration Run refusal also remains unexplained;
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
  are Unknown. The owner confirmed operation after installation. Other existing
  fixtures with both tokens also bind the sensor, but their position mapping has
  not been independently hardware-tested. No reservoir actuator is added.
- **Write verification:** items match by ID and requested fields, allowing extra
  returned fields and equivalent finite numeric setpoints. IDs and units remain
  exact; ambiguous, missing or mismatched items fail. Individual and batch writes
  share the comparison, including merged options. A stale Run after Stop still
  fails verification until Ready is observed.

Cold idle readings remained at 175 F with desired 0. The existing display filter
therefore showed Unknown; that value is not a measured room temperature. This
change does not alter temperature filtering or claim to fix a physical sensor.

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

## Validation limits

Regression coverage includes both cavity paths, held settings, mode labels,
duration conversion, strict permission checks, timeout/refusal/no-replay paths,
status migration, reservoir states and partial-item verification. Passing tests
do not resolve the reported Smart Control incident. Revised-sequence Upper/whole
operation, timer completion, native hours presentation, simultaneous combinations,
all recipe starts and running adjustments remain hardware coverage gaps.

[manual]: https://images.thdstatic.com/catalog/pdfImages/99/9900cc56-8433-4e1b-ba18-4271b575791d.pdf
[research]: https://github.com/roweboat123/localthings/tree/117eade8c5e0ffea1a22799e42ad4c6f27eb3b08/docs/investigations
