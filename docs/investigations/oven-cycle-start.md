# Starting an oven or microwave cycle

A cook start has been measured working on one board. The cook parameters
and the run command go to the device collection in a single write, not to
each resource separately, which is the one shape none of the attempts
below used.

This file keeps the earlier results, because they still bound what the
answer can be, and revises what they mean. The probe ladder at the end is
no longer aimed at "what value starts a cycle" -- that is answered -- but
at the part still open, which is whether the batch is *required* or only
the collection href matters.

Issues: #176 (the feature request), #183 (TP1X range), #300 (TP2X wall
oven), #473 (NE63A6111SS, "can't start preheating"), #470 (same board
family).

## What starts a cook

Measured 2026-09-20 on an NV7000BS/EUI wall oven, single cavity, Remote
Control on at the panel, with no other session open:

```
POST /device/0        CoAP UPDATE, Content-Format 60 (CBOR)

[
  {"href": "/devices/0"},
  {"href": "/mode/vs/0",
   "rep": {"x.com.samsung.da.modes": ["Defrost"]}},
  {"href": "/temperatures/vs/0",
   "rep": {"x.com.samsung.da.items": [{"x.com.samsung.da.desired": "30",
                                       "x.com.samsung.da.id":      "0",
                                       "x.com.samsung.da.unit":    "Celsius"}]}},
  {"href": "/operational/state/vs/0",
   "rep": {"x.com.samsung.da.operationTime": "00:01:00",
           "x.com.samsung.da.state":         "Run"}}
]

  ->  2.04, empty body

t+4s    /operational/state/vs/0   state = "Run", opTime = "00:01:00"
        /oven/vs/0                state = "Cooking"
        /temperatures/vs/0        desired = "30", current = "28"
```

Stopping is an ordinary single-resource write of `state: "Ready"`, which
is why stop has always worked while start has not.

**Remote Control has to be on at the panel, and is worth checking before
reading anything into a result.** With it off, the cook parameters are
still accepted and held -- mode, setpoint and cook time all stick -- and
only `state: "Run"` is silently dropped, both answering `2.04`. A batch
that "does not start" under those conditions says nothing about the
payload. That is this family's per-field gate reproducible on demand
rather than inferred, and it is worth knowing before spending a
reporter's hardware time: `/remotectrl/vs/0` must read `true`, and it
does not survive a power cycle.

Four things about that payload are easy to get wrong:

- **`Run` is not a separate step.** It rides inside the payload's own
  `/operational/state/vs/0` element, next to the cook time. Every attempt
  in the table below sent it on its own.
- **The write goes to `/device/0`, singular**, while the payload's first
  element is a bare `{"href": "/devices/0"}` marker, plural, carrying no
  `rep`. It is **not required**: the identical batch with that element
  deleted started the cook first time (2026-09-20). That is one oven
  though, so sending it is still the safer default -- it costs nothing
  and it is the payload that has been measured working.
- **Zero-pad the hour.** `0:10:00` for a ten-minute cook produced a
  roughly 609-minute one on hardware; `00:10:00` is correct.
- **Send no option tokens the mode does not support.** `Defrost` on this
  board has neither fast preheat nor steam, so the run above sent none.

The set duration *includes* preheat: the countdown runs from the moment of
Start rather than from reaching temperature, so a short cook at a high
setpoint is mostly preheat. `progressPercentage` is the same clock at
finer resolution -- its granularity is the duration over 100, against
`remainingTime`'s fixed 60s -- so it is the better field to drive a UI
from on any cook under 100 minutes.

Full write-up, including what a single run does not establish:
https://github.com/QuiteYellow/SmartThings-Local/blob/main/docs/oven-cook-start.md

## What is measured

| Board | Model | Tried | Result |
|---|---|---|---|
| `TP1X_DA-KS-RANGE-0101X` | NE9801T-/AA0 (#183) | `{state: "Run", operationTime: "00:30:00"}` on `/operational/state/vs/0` | `2.04`, rep unchanged: `state` still `Ready`, times still `00:00:00` |
| same | | `{state: "Preheat", operationTime, remainingTime}` | `2.04`, nothing starts -- **but the oven chimed** |
| same | | `{desired: "350"}` on `/temperatures/vs/0` -- the bare field, not the `items` RMW | `2.04`, `desired` still `0` |
| `TP2X_DA-KS-WALLOVEN-000002` | NW9000KD/AA1 (#300) | five sequences: mode / setpoint / `Run` separately, in both orders, and `state`+times in one POST | every write `2.04`, `changed: false`, `held: false` after 20 s |
| same | | `/oven/vs/0` | `4.05` -- the resource is `oic.if.s` |
| same | | `Sound_Off` / `Sound_On` on `/mode/vs/0` | `2.04`, **held**, panel followed |
| `TP2X_DA-KS-RANGE-0101X` | NX9302T-/AA0, gas (#176/#183) | Bake, then 350, then `Run`, from `Ready` | all `2.04`, none held, `/oven/vs/0` stayed `Ready`, panel silent |
| same | | the *same* setpoint and cook-time writes 3 minutes into a panel-started Bake | `2.04`, **held**, panel followed within seconds |

Three constraints fall out of that, and every hypothesis has to fit all
three:

1. **Non-cook settings write fine from `Ready`.** `Sound`, `EnergySaving`
   and friends stick on the same resource, over the same session, in the
   same state where the cook fields do not.
2. **Cook settings write fine mid-cook.** Setpoint and cook time both hold
   and the panel follows -- that is the whole of what the shipped entities
   do today, and it is real remote control.
3. **Cook settings from `Ready` are accepted and then discarded.** `2.04`,
   then the old value is back.

So this is not a transport problem and not a remote-control-flag problem.
It is a **per-field gate conditioned on a job already existing**. It is
probably not an OCF access-control denial either -- that would be a `4.03`,
not an accepted-then-reverted write -- but "probably" is doing work there,
and probe 12 settles it with one read.

**These results are confounded, and less damning than they look.** Cross
the table above against what each board declares in its own `modeSpec`:

| Board | What was tried | Modes it declares startable |
|---|---|---|
| NE9801T-/AA0 (#183) | `state` plus `operationTime`, no mode or setpoint write | 9 of 15 |
| NW9000KD/AA1 (#300) | mode, setpoint and `Run` separately, both orders | no `modeSpec` at all |
| NX9302T-/AA0, gas (#176) | mode, then setpoint, then `Run` | 0 of 8 |

The two boards that received the full sequence are the two that declare
nothing startable, so neither result says anything about payload shape --
nothing would have started them. The board that *does* declare startable
modes was never given a mode write: it was told to run without being told
what to cook. **The full separate-write sequence has never been run on a
board that declares a startable mode**, so the leaf path is not refuted by
anything here; it is untested where it matters.

That also means the batch is not yet shown to be *necessary*. It is shown
to be *sufficient*, on one board.

## The board tells you whether a remote start is in the contract at all

`/mode/vs/0`'s `x.com.samsung.da.modeSpec` carries a per-mode `control`
field with exactly three values in the corpus: `Start&Setting`, `Setting`,
`NotSupported`. Read as "remote may start this mode" / "remote may only
adjust it while it runs" / "neither", it predicts both boards where a start
was measured to be impossible:

| Fixture | DeviceType | `Start&Setting` modes | `SettingPossible_` |
|---|---|---|---|
| `range_ne6516a` | NE6516A-/AA0 | Bake, ConvectionBake, ConvectionRoast, AirFryer, Dehydrate | `_0` |
| `range_ne8300d` | NE8300D-/AA0 | ConvectionBake, ConvectionRoast, Bake, AirFryer, Dehydrate | `_0` |
| `range_no_info` | NE8411B-/AC0 | Bake, ConvectionBake, ConvectionRoast, AirFryer, Dehydrate | `_0` |
| `range_tp1x_da_ks_range_0101x` | NE9801T-/AA0 (#183) | nine, incl. the Upper/Lower flex modes | `_7` |
| `range_device` | NI9100D-/AA0 | Bake only | `_5` |
| `range_nx60t8311ss` | NX9302T-/AA0, **gas** (#176) | **none** -- all seven are `Setting` | `_2` |
| `oven_tp2x_ks_walloven` | NW9000KD/AA1 (#300) | **no `modeSpec` at all** | -- |
| `oven_device` | NV7000BS/ET5 | no `modeSpec` | -- |
| `microwave_mw7300b` | MW7300B-/EU1 | Convection, AirFryer, Grill, Deodorization | -- |
| `qooker_mw7500a` | MW7500A-/KO0 | **none** -- all nine are `Setting` | -- |
| `microwave_me7500d` | ME7500D-/AA1 | no `modeSpec` | -- |

The gas range declares no startable mode, its manual says "For safety you
cannot turn the gas oven ON remotely", and the measurement agrees. The wall
oven declares no `modeSpec` at all and nothing works on it either. That is
two independent confirmations, so:

- **Do not spend a reporter's hardware time on a board with no
  `Start&Setting` mode.** Read `modeSpec` first (probe 0) and say so in the
  issue instead.
- **It is necessary, not sufficient.** The NE9801T declares nine startable
  modes and still ignores every local start attempted so far. `control`
  describes Samsung's *command* contract, which the cloud reaches and we so
  far do not.
- **On a microwave it will never be the magnetron.** Every `MicroWave*`
  mode in the corpus is `Setting`-only; the startable ones are the
  convection/grill/air-fry/deodorize modes. Any future control must follow
  the device's own declaration rather than offering a start for every mode.

`SettingPossible_<n>` is the other unexplained token on these boards (`0`,
`2`, `5`, `7` across six units, `_0` on three different NE-series ranges).
It does not track the number of startable modes. Whether it is a static
capability bitmask or a live gate is answerable cheaply -- see probe 6 --
and if it is live it is the best candidate for the thing that makes a cook
write stick.

## What `filter-reset.md` transfers to this problem

1. **`2.04` is not evidence of anything.** This firmware ACKs field names it
   does not recognise. Every negative result in the table above is a
   `2.04`, which means all of them are equally consistent with "the cook
   fields never reach a handler from `Ready`". Stop varying values; first
   establish whether a handler is reached at all.
2. **The near-miss field name is the decisive control.** `filterResetZZZ`
   was swallowed with `2.04` while `filterReset: "zzz"` faulted `5.00`, and
   that asymmetry is the entire reason we know the field exists. The same
   pair on `state` / `modes` / `desired` is probe 1, and nobody has run it
   on an oven.
3. **A `5.00` is closer to a hit than a `2.04`.** It means you reached real
   code and missed the vocabulary, which collapses the search to a handful
   of command words.
4. **Wrong type looks like rejection and isn't.** A non-string fails
   `oc_rep_get_string()` before the dispatch runs, so it returns an inert
   `2.04`. Send the type the rep already uses -- `modes` is an *array* of
   strings, `state` and `desired` are bare strings.
5. **The trigger can be a field no rep ever reports.** `filterReset` appears
   in no representation, no `/oic/res`, no `/device/0` batch. The three
   dumps in #300 (before / after / 10 s into an app-started cook) therefore
   *could not* have found a trigger of that shape, and did not. A diff is
   evidence about state, never about commands.
6. **Boards lag their own commands.** The fridge reflected a reset in ~2 s,
   the AC in #449 took about a minute. Use `verify_after: 20` or more, and
   never call a candidate dead on one immediate readback.
7. **One CoAP/DTLS session per device.** `hold_session_lock` defaults to on
   and should stay on for these; nothing else may land between two steps.
8. **`changed` is not "something changed".** It is "are my payload's values
   present in the readback", so writing a field its current value reports
   `changed: true` having done nothing. Read `held` from `verified`.
9. **A physical reaction counts as a fault code.** The chime the NE9801T
   produced for `state: "Preheat"` -- and for nothing else tried -- is this
   family's `5.00`: the board reacted to a value it did not accept.
   `Preheat` reached something `Run` did not, on a board whose
   `/oven/vs/0` reports exactly `Preheat` during a real cook.

## A reframe worth holding while you probe

From #183: with Smart Control **on**, setting mode/temp/time from the app
starts the cook by itself; with Smart Control **off**, the same settings are
programmed and somebody presses Start on the panel. If that is how the board
sees it, **there is no separate start command to find** -- the start is what
the board does when it accepts a *cook setting* while Smart Control is on,
and the open question is only why an accepted cook setting is thrown away
from `Ready` over the local path when the cloud's is not.

That is a different search than "guess the start token", and probes 1-3 are
aimed at it: they ask whether the cook fields are parsed at all from
`Ready`, not what value would start a cycle.

Both readings share one suspect: the Wi-Fi module holds a shadow rep and
forwards recognised commands to the MICOM board that actually owns cook
state (the channel `ac-filter-reset.md` found as `/rm/micomdata/vs/0` on
another family). An accepted-then-reverted write is exactly what a shadow
overwritten by the next MICOM sync looks like -- i.e. the local POST never
became a MICOM message. Mid-cook writes do become one, which is why they
hold.

## What every other open-source integration does about it

Four projects were surveyed. None of them starts an oven any way we could
copy, but the survey is not a dead end: it pins down the cloud contract's
*shape*, and one of them hands over two mechanical facts we did not have.

**Home Assistant core's own `smartthings` integration (cloud).** Its entire
oven write surface is one button: `ovenOperatingState` -> `stop`. There is
no start, no mode, no setpoint, no operation time on an oven anywhere in
`button.py`, `number.py`, `select.py`, `time.py` or `climate.py`. The same
file *does* ship start / pause / resume for a dishwasher
(`samsungce.dishwasherOperation`), gated on `remoteControlStatus`. So the
most mainstream integration there is treats "stop the oven" as supported
and "start the oven" as not, over the cloud, deliberately.

**HubiThings Replica's `ReplicaSamsungOven` (cloud, Hubitat).** The most
complete open-source Samsung oven driver in existence, and worth reading in
full. Three things carry over:

- There are **two capability generations**, and they differ in a way that
  matters here. Newer boards expose `samsungce.ovenOperatingState` with
  `start` (no arguments), `stop`, `pause` and `setOperationTime(hh:mm:ss)`.
  Legacy boards expose `ovenOperatingState`, which has **no
  `setOperationTime` at all** -- the driver sets a cook time there by
  calling `start` with a map, `[time: <seconds>]`. On that generation,
  writing the time *is* the start command.
- The **ordering is explicit and documented**: "Set Oven Setpoint: requires
  mode set first"; "Set Operation Time: requires mode and oven setpoint set
  first". `start(mode, opTime, setpoint)` issues setOvenMode ->
  setOvenSetpoint -> setOperationTime -> bare `start`, with a **2 second**
  pause between each.
- Its readme states plainly: "Samsung has chosen to disable non-SmartThings
  access to Start, Pause, and Set Operation Time functions for *safety*
  reasons", and tags `setOperationTime` with "CAUTION: MAY START OVEN ON
  SOME OVENS". That matches the SmartThings staff line that OCF appliances
  are driven at plugin level rather than through the public API.

**No microwave driver exists in any of them.** Replica has drivers for the
oven, oven cavity, washer, dryer, dishwasher and fridge, and none for a
microwave. Nothing in the open-source world starts a microwave remotely,
which is the same conclusion the corpus's own `modeSpec` reaches from the
other direction.

**`smartthings-local` (aceindy), the library this integration is built
on.** Same local CoAP-DTLS surface, different codebase, same wall: its
readme calls oven cavity remote-start "the marquee open example ... the
write is accepted (`2.04`) but the cavity never engages". Two mechanics from
it that we did not have:

1. **"The bridge deliberately does not fetch-back right after a write; that
   GET is itself what triggers the revert."** Our write path always GETed
   the href immediately after the POST -- that is where `after` and
   `changed` come from -- so **every measurement in #183 and #300 has that
   GET inside it**. If their observation generalises, some of what we have
   recorded as "accepted then reverted" may be "accepted, then knocked down
   by our own verification read". It does not generalise to everything (the
   fridge's filter reset held through exactly this path), but for cook
   fields it was untested, and it is why `write_resource` now takes
   `readback: false` (see below).
2. **`UpperTimer*` on `/mode/vs/0` populates when set through the API**,
   though a timer set on the panel never appears there. That is a
   job-shaped write sticking from idle on an oven board, which is the whole
   premise of probe 7.

What the survey changes about the probes: match the cloud's exact pacing
and order rather than our own (2 s, mode -> setpoint -> time -> start, per
Replica), try the **legacy shape** where the time write is the start
(probe 11), and treat the readback as a variable rather than a constant.

Sources: `homeassistant/components/smartthings/button.py` in home-assistant/core;
`Drivers/ReplicaSamsungOven.groovy` and `Docs/SamsungOvenReadme.md` in
DaveGut/HubithingsReplica; the `README.md` of aceindy/smartthings-local;
the capability list in pySmartThings/pysmartthings. The SmartThings staff
statement the Replica readme cites is community.smartthings.com thread
251558.

## The probe ladder

**Re-aimed.** These were written to find what value starts a cycle, which
is answered above. What is still open is narrower: whether the batch is
required, or whether `/device/N` is simply a handler the leaf resources do
not reach.

`write_resource` now sends a Collection batch (issue #473), so the
measured payload itself is runnable rather than only its leaf
decomposition -- probe A0 below. Its inner hrefs are **never** rewritten,
not even a subdevice's canonical -> actual translation, which the write's
own href still gets: which spelling a board wants inside a batch is under
test, and a helpful correction would discard the permutation. On a
composite oven that means you write the cavity's hrefs yourself.

### Probe A0 -- the measured payload, on other hardware

The first thing to run, because it is the only sequence known to have
started a cook. Verbatim from "What starts a cook" above; swap the mode,
setpoint and time for ones your board's own `modeSpec` declares.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /device/0
      readback: false
      payload:
        - href: /devices/0
        - href: /mode/vs/0
          rep:
            x.com.samsung.da.modes: ["Defrost"]
        - href: /temperatures/vs/0
          rep:
            x.com.samsung.da.items:
              - x.com.samsung.da.desired: "30"
                x.com.samsung.da.id: "0"
                x.com.samsung.da.unit: "Celsius"
        - href: /operational/state/vs/0
          rep:
            x.com.samsung.da.operationTime: "00:01:00"
            x.com.samsung.da.state: "Run"
```

`verified` reports this one per element as well as overall, under
`elements` -- a batch whose mode stuck and whose `Run` was discarded is a
different finding from a flat refusal, and that distinction is the whole
value of running it.

The `{"href": "/devices/0"}` marker is plural and carries no `rep`. It is
not required (the identical batch without it started the cook first time)
but it is what was measured, so send it first and drop it on a second run
if the first works -- that settles a question one line of payload wide.

### Probe A -- is the batch required, or only the collection href?

On a board that declares a `Start&Setting` mode, from `Ready`, write mode,
then setpoint, then `operationTime` with `state: "Run"`, as three separate
`localthings.write_resource` calls two seconds apart in one session. This
is probe 11's shape on a board that can actually start, which is the
combination nobody has run.

Run it *after* A0, and it means something either way:

- A0 starts the oven and A does not: the batch is the mechanism, and the
  leaf path can be dropped from this investigation.
- Both start it: the collection href was never the variable, and the
  earlier failures were board-specific.
- Neither: this board is not startable by any route found so far, and the
  rungs below are what is left.

Everything below remains useful for a board that will not start by either
route, and for the microwave case.

Rules for whoever runs these:

- **The oven must be empty and you should be standing in front of it.**
  These are genuine attempts to make an appliance heat.
- Run them **one service call at a time**, paste the whole response, and say
  what the panel did -- including any beep. Per trap 9, a noise is data.
- Read `response_body` in every result, not just the code: it is the
  board's own answer to the POST, and on some Samsung firmware that is
  where a `"Control fail, <...>"` diagnostic lives.
- Stop at the first rung that answers; each rung below assumes the one above.
- `verified` is keyed by href and compares only the **last** payload written
  to that href, so a call containing two writes to the same href reports
  `held` for the second one only. For the near-miss pairs, read the
  per-write `code` out of `results[]` and ignore `verified`.
- Everything below writes canonical hrefs; if your oven has two cavities,
  pick the cavity's device and keep the hrefs as written. The exception is
  a batch payload (probe A0), whose inner hrefs are sent exactly as typed
  -- on a second cavity those are yours to get right.

### Probe 0 -- ask the board before touching it

```yaml
action: localthings.read_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  href: /mode/vs/0
```

In the response: `x.com.samsung.da.modeSpec` (does any mode carry
`"control":"Start&Setting"`, and what are its `tempMinF`/`timeMin`?), the
`SettingPossible_<n>` token, and `supportedModes`. If no mode is
`Start&Setting`, stop here and record it in the issue -- on the two boards
where that was true, nothing else worked either.

### Probe 1 -- the near-miss control on `/operational/state/vs/0`

The single most informative experiment in this file, and the direct
translation of how the filter reset was found. Neither write can start
anything.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 20
  writes:
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Zzzz"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.stateZzzz: "Run"
```

| First write | Second write | Reading |
|---|---|---|
| `5.00` | `2.04` | The handler is reachable from `Ready` and we are only missing the word. Go to probe 4. |
| `2.04` | `2.04` | `state` is swallowed exactly like an unknown field name: there is no start handler on this href in this state. Go to probe 2, then 5. |
| `4.00`/`4.03` | anything | Different firmware posture from the fridge's; note the code, it is new information. |

### Probe 2 -- the same control on `/mode/vs/0` and `/temperatures/vs/0`

Run as two separate calls (one href each, so `verified` stays meaningful).
The third write in each is the type probe from trap 4: a string where the
rep uses an array tells you whether a `2.04` came from the getter failing
rather than from the value being rejected.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 20
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Zzzz"]
      settle: 5
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modesZzzz: ["Bake"]
      settle: 5
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: "Bake"
```

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 20
  writes:
    - href: /temperatures/vs/0
      payload:
        x.com.samsung.da.items:
          - x.com.samsung.da.id: "0"
            x.com.samsung.da.desired: "Zzzz"
      settle: 5
    - href: /temperatures/vs/0
      payload:
        x.com.samsung.da.itemsZzzz:
          - x.com.samsung.da.id: "0"
            x.com.samsung.da.desired: "350"
```

### Probe 3 -- the same control, mid-cook

The control that makes probes 1-2 interpretable. Start a cook **from the
panel** (any mode, lowest temperature, 20 minutes), wait until it is
running, then run probe 1 again unchanged.

Mid-cook is the one state where we know cook writes reach the MICOM. So:

- garbage value `5.00` mid-cook but `2.04` from `Ready` -> the handler
  genuinely only exists while a job exists, and "start" is not a field on
  this resource. That result closes the direct-write line of attack and
  points the remaining work at how a job gets created at all.
- garbage value `5.00` in both states -> the handler is always there and
  probe 4's vocabulary sweep is worth running properly.
- `2.04` in both states even though real values held mid-cook -> this
  firmware never faults on this resource, the near-miss signal is not
  available here, and only `held` can be believed. Say so and skip to
  probe 5.

### Probe 4 -- the vocabulary, only once probe 1 or 3 gave a `5.00`

Do not run this speculatively; run it when a fault code is available to
score the guesses with. One word per write, `5.00` on all of them means
none is right, a `2.04` in the middle of a run of `5.00`s is the hit
(the value parsed and dispatched cleanly) -- the exact inversion the fridge
taught.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Start"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Preheat"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Cooking"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Operate"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Run"
```

`Preheat` is in the list deliberately: it is the one value that has ever
produced a physical reaction (#183), and it is what `/oven/vs/0` reports
during a real cook.

### Probe 5 -- fields the rep never reports

The filter reset was an unadvertised field on an advertised resource. Two
candidates on `/operational/state/vs/0` have some claim to being real
rather than invented:

`causeSource.state` is a genuine field on this href -- `range_device`,
`range_ne8300d` and `microwave_mw7300b` all report `"SETB_Ready"` -- and its
name says the board tracks *what caused* the current state. It is the only
field in the corpus that looks like a command-provenance marker.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /operational/state/vs/0
      payload:
        causeSource.state: "Zzzz"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Run"
        causeSource.state: "SETB_Run"
```

The first write is the near-miss control again: if a garbage value on
`causeSource.state` faults while a garbage *name* does not, this field is
parsed and worth a vocabulary of its own.

### Probe 6 -- is `SettingPossible_` a live gate?

Free to answer, and it needs no writes at all. Run

```yaml
action: localthings.read_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  href: /mode/vs/0
```

at four moments and record the token each time: Smart Control off, Smart
Control just switched on, immediately after the app starts a cook, and
after the cook ends. A token that moves with Smart Control is the gate we
are looking for; one that never moves is a static capability bitmask and
can be dropped from the investigation.

If it moves, this is the write worth trying (a single-token options merge,
the same shape every other setting on this href uses, and non-thermal):

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 20
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.options: ["SettingPossible_1"]
      settle: 5
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Bake"]
```

### Probe 7 -- start a job that cannot heat anything

NV7000BS-class boards carry `UpperTimerSet_`, `UpperTimerCurrent_` and
`UpperTimerState_Ready` in `/mode/vs/0`'s options: a panel kitchen timer,
which is a *job* with a start, a countdown and an end, and no element
behind it. If a local write can start that timer, the board does accept a
job start over the local path and the blocker is specific to cooking; if it
cannot, the blocker is job creation itself. Either answer is worth more
than another `2.04` on `state`.

Only on a board whose options actually carry those tokens (check probe 0):

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 20
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.options: ["UpperTimerSet_5"]
      settle: 5
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.options: ["UpperTimerState_Run"]
```

`keepWarmReservation_Off` on the #300 wall oven is the same shape of
experiment for that board: a reservation is a scheduled job, so whether
`keepWarmReservation_On` *holds* from `Ready` separates "no cook parameter
sticks" from "nothing job-shaped sticks".

### Probe 8 -- an unenumerated command resource

`read_resource` is a live GET and answers `4.04` for a resource that is not
there, `4.05` for one that is there and unreadable, `2.05` for one that is.
That makes it a safe existence scanner -- no writes, no vocabulary
guessing. One call each:

```yaml
action: localthings.read_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  href: /actions/vs/0
```

...and the same for `/rm/micomdata/vs/0`, `/quickcontrol/vs/0`,
`/reservation/vs/0`, `/cooking/vs/0`, `/oven/cook/vs/0`,
`/oven/spec/vs/0`.

Anything that answers `2.05` is new surface. **Do not then guess action
names at it**: `ac-filter-reset.md` deliberately stopped at that line,
because an unknown vocabulary on a channel called "actions" can hold a
factory reset next to the thing you want. Report what exists and stop.

### Probe 9 -- everything in one message

Cheap, unlikely, and worth having on record because the AC boards do have
settings that only stick when written alongside the thing they belong to
(`Sleep_<n>` needs `Comode_Sleep` in the same options write). Unknown field
names are swallowed, so the cost of being wrong here is one `2.04`.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Bake"]
        x.com.samsung.da.operationTime: "00:20:00"
        x.com.samsung.da.state: "Run"
```

### Probe 10 -- the actual start, on a board that declares one

Only after probe 0 shows a `Start&Setting` mode. Pick the gentlest one it
declares -- `Dehydrate` or `BreadProof` where present, not `Broil` -- and
use that mode's own `tempDefaultF`/`timeDefault` from `modeSpec` rather
than a number from this file.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Dehydrate"]
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.operationTime: "00:20:00"
        x.com.samsung.da.remainingTime: "00:20:00"
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Run"
```

This is #300's sequence B with a mode the board says it will start. It is
last because it is the one already known to fail on three boards; running
it first is how this investigation spent three threads learning nothing.

### Probe 11 -- the legacy shape: the time write *is* the start

From Replica's legacy path, where `ovenOperatingState.start` carries
`[time: <seconds>]` and no separate time command exists. Locally that is
`operationTime` written **alone** -- no `remainingTime` alongside it, no
`state` write at all, which is not a combination anybody has tried. #183
wrote all three together; #300 wrote `state` with both times. Use the
cloud's pacing exactly: 2 s between steps, mode then setpoint then time.

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Bake"]
      settle: 2
    - href: /temperatures/vs/0
      payload:
        x.com.samsung.da.items:
          - x.com.samsung.da.id: "0"
            x.com.samsung.da.desired: "350"
            x.com.samsung.da.unit: "Fahrenheit"
      settle: 2
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.operationTime: "00:20:00"
```

### Probe 12 -- rule the OCF access-control layer in or out

Read-only, and it answers a question this investigation has been assuming
the answer to. The boards advertise `/oic/sec/doxm` and `/oic/sec/pstat` in
`/oic/res`; the standard sibling `/oic/sec/acl2` holds the ACEs that say
which subject may do what to which resource. If our minted identity's ACE
covers these hrefs with full permissions, the whole "the cloud is
privileged and we are not" branch dies cleanly; if it is restricted to a
subset, that is the answer and no amount of payload guessing would have
found it.

```yaml
action: localthings.read_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  href: /oic/sec/acl2
```

Also worth reading once each: `/oic/sec/doxm`, `/oic/sec/pstat`,
`/oic/sec/cred`. Report the codes even when they are `4.03` -- a security
resource refusing us is itself informative.

## What ships if one of these lands

A start control would follow `common.filter_reset_button`'s shape exactly:
bound to `/operational/state/vs/0`, **gated on the device's own
declaration** -- here `modeSpec[selected_mode].control` containing `Start`,
the way the filter button is gated on `filterResetType` naming a reset the
device claims to support. A bare presence check would be the same mistake
`notresetable` was: `Setting` and `NotSupported` both mean no.

The gate has to be per *mode*, not per device, because the same board
declares `Start&Setting` for Bake and `Setting` for Broil. On microwaves
that is what keeps a start control off every `MicroWave*` mode.

One wrinkle the shape above forces: the three cook parameters cannot write
through on change, because the appliance will not take them individually
from `Ready`. They have to be staged and sent together. A worked version of
that -- staged program entities plus a Start button that assembles the
batch, with the bounds read from `modeSpec` and a refusal carrying a reason
rather than a bare `4.xx` -- is in
https://github.com/QuiteYellow/SmartThings-Local/blob/main/docs/bridge-demo.md
if it is useful as a reference.

## Not the answer, and why

- **`state: "Run"` alone, from `Ready`** -- `2.04` and reverted on three
  boards across two generations. It is what every other family uses, and it
  is not enough here. The measurement above says why: on this firmware
  `Run` is not a separate step at all. It travels inside the collection
  write, alongside the mode and the cook time, and a job is created by
  that write or not at all.
- **Ordering.** #300 ran settings-then-`Run` and `Run`-then-settings with
  5 s settles under one session lock. Neither order changed anything.
- **`state` + times in one POST.** Same result (#300 sequence D).
- **`/oven/vs/0`.** `4.05` on the wall oven, and declared `oic.if.s` in
  every fixture that reports an `if`. It is a read-only mirror of cavity
  state, not a control surface.
- **Diffing dumps around an app-started cook.** #300 captured before /
  after / 10 s in, and #183 captured idle vs cook-started. Both show the
  *effect* (`desired`, `operationTime`, `state`, `/oven/vs/0` -> `Preheat`)
  and neither can show a trigger that is never stored -- see transfer 5.
- **The remote-control flag.** #300's reporter enabled Smart Control per
  cavity and confirmed the sensor read on; writes still reverted. The
  integration's own block was bypassed too (`write_resource` ignores it).
- **The DAWIT 3.0 microwave generation (#433).** Every resource is
  `oic.if.s` and answers `4.05` to a write. There is no local write path to
  find there; none of the probes above apply.

## Three knobs the probes above rely on

All were added for this investigation, and each changes what a probe can
see.

**A list payload** -- `write_resource`'s `payload` takes the
`[{href, rep}, ...]` Collection batch as well as a single resource's
Property map, so the one sequence measured to start a cook is runnable
instead of only describable (probe A0). It goes on the wire verbatim:
element order, the rep-less marker, and the inner hrefs, which nothing
rewrites. `after`, `changed` and `held` decompose per element against the
batch the collection reads back, so the answer names which resource held
rather than making an unfalsifiable claim about the collection.

**`response_body`** -- every write result now carries the POST's own decoded
body. The fridge answers with a verbatim echo worth nothing
(`filter-reset.md`, trap 2), but the laundry firmware answers
`"Control fail, <...>"` and no oven board had ever been checked, because
the write path did `code, _ = sess.post(...)` and dropped it. If one of
these boards states its reason for discarding a cook write, it now arrives
with the `2.04` instead of being thrown away. Nothing has to be passed to
get it; read it on every probe above.

**`readback: false`**, per write -- skips the immediate follow-up GET, on
the `smartthings-local` observation that the fetch-back is itself what
trips some boards' revert. With it, `after` is `{}` and `changed` is
`null` (nothing was compared -- not the same claim as "the value isn't
there"), and `verify_after`'s delayed read is the only check, which is the
point. Worth re-running probes 1, 10 and 11 with it:

```yaml
action: localthings.write_resource
data:
  device_id: PUT_YOUR_DEVICE_ID_HERE
  verify_after: 30
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Bake"]
      readback: false
      settle: 2
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Run"
      readback: false
```

If a cook parameter holds at `verify_after` here and did not in #300's
sequence B, the readback was the confound and a chunk of the table at the
top of this file needs re-measuring.
