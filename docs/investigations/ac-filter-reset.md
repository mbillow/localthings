# AC filter-time counter reset: solved

`registry/capabilities/airconditioner.py`'s `filter_time` sensor
(`FilterTime_<N>` option token, tenths of an hour) has a reset entity now
(`filter_time_reset`, issue-tracked as PR #289): a single-token options write
of `FilterCleanAlarm_Clear` to `/mode/vs/0`, the same merge every other
setting on that href uses. Measured on an ARTIK051_KRAC_18K: `FilterTime_95`
(9h30m) → `FilterTime_0`, still zero on a fresh DTLS session and every poll
after; none of the other 17 tokens moved and the `/alarms/vs/0` entries
stayed `Deleted`.

The rest of this file is kept as-is: the failed attempts below are still the
best record of what *doesn't* work on this generation, and the reasoning
that follows them explains why the reset looked cloud-only for as long as it
did — a genuine trap worth knowing about before the next reset-adjacent
mystery on this board family.

Scope: all of the above applies to boards that carry the counter as a
`FilterTime_<N>` option token on `/mode/vs/0`. Not every AC does. See
"Boards with no `FilterTime` token" at the end for an `ARTIK051_PRAC_20K`
that keeps the counter in its own resource, rejects both the token and a
direct write, and has no local reset at all.

## What the reset actually is

A **command**, not a value write. Samsung's cloud models it as capability
`custom.dustFilter`, command `resetDustFilter`, no arguments (implemented in
several SmartThings HA forks; not in the core integration) — that command
name is real, but see below for why POSTing it directly went nowhere.

## Tried, all against a live unit, all failed (before the token above was found)

- `FilterTime_0` via the single-token options merge that works for every
  other setting on this href — accepted with no error, then discarded. Tried
  on two units in opposite power states to rule out the obvious confound:
  5595 → back to 5595 after 69s (powered off, alarm active), 1925 → back to
  1925 after 65s (actively cooling).
- A full `options[]` read-modify-write with `FilterTime_0` substituted,
  instead of the single-token merge — zero fields changed anywhere.
- A write to `/consumable/vs/0`, the board's own filter resource
  (`items[{name: FilterProgress, state: N}]`) — discarded. `/oic/res`
  declares that resource `oic.if.s` (read-only), which fits.
- `/actions/vs/0` (`x.com.samsung.da.actions`, `oic.if.a`) is the obvious
  local command channel but publishes no schema: GET returns `{}` on
  baseline and on `oic.if.a`, and five POSTs probing the shape (empty map,
  empty string, empty array, invalid value, items shape) all returned 4.00
  with an empty body — no echo of accepted field names, unlike the laundry
  firmware's `"Control fail, <...>"`. Guessed action names were deliberately
  not enumerated against a live appliance: an unknown vocabulary on a
  channel called "actions" can hold a factory reset next to the one we want.
- `/hass/state/vs/0` and `/hass/command/vs/0` (advertised in `/oic/res`, and
  `/opt/data/hass.db` exists in `/file/list`) → 4.04 on every interface, so
  unimplemented scaffolding on this firmware.
- `/file/transfer/vs/0` serves only `/mnt/usage.db`; selecting another path
  returns 4.05/4.00, so the firmware can't be pulled that way to read the
  action vocabulary out of it.
- `/rm/micomdata/vs/0` (channel toward the MICOM board the physical panel
  talks to) stays empty even after successfully enabling remote management.

## What the failures are not

Not a transport, permission, or cert problem: a control write of `rmState`
on `/rm/state/vs/0` was accepted (2.04 Changed, value held, restored
afterwards), and `FilterAlarmTime_` is written through the very same options
merge and kept. Writes work; this one value just isn't driven that way.

## The token, and why the dead ends below missed it

`FilterCleanAlarm_Clear` is not derived from anything in this file's earlier
attempts — how it was originally identified isn't recorded here. What is
recorded is why the standard technique (diff the appliance's reported state
before/after triggering the action in Samsung's app) couldn't have found it
on its own: the token is a trigger, never stored and never echoed back in
`x.com.samsung.da.options`, so a before/after diff of stored state shows
only the *effects* (counter zeroing, alarm clearing) and never the token
that caused them.

## Dead ends tried before the token was known (kept for the next unrelated mystery)

- The `/actions/vs/0` action vocabulary from an independent source (a
  firmware image, or a capture of what the cloud sends the device).
- The IR path — the physical remote has a filter reset (Options → Filter
  Reset → SET), and IRremoteESP8266 decodes this AC family, though issue
  #1277's dump doesn't include that button.

## Evidence for the counter's direction and scale

Confirmed counting *up* (running time since last reset, not remaining time):
token 1710 matched the Samsung app's "171 hours 0 minutes" for the same
filter (pins the tenths-of-an-hour scale); seen rising while the unit ran
(171.0 → 171.5); and across two units on one site the `/alarms/vs/0` filter
alarm tracks the counter in the right direction — live (`FilterAlarm`,
`Created`) at `FilterTime_5595`, still the `FilterAlarm_OFF`/`Deleted`
placeholder at `FilterTime_1915`, matching the app's own 500-hour threshold
behavior. `FilterAlarmTime_` in the same options blob is that threshold (500
on every unit on record).

The entity key stays `filter_time` rather than `filter_time_elapsed`:
renaming it would change every existing unit's `entity_id`/`unique_id` for a
wording improvement only.

## Boards with no `FilterTime` token (`ARTIK051_PRAC_20K`)

Negative result, measured 2026-08-11 on integration v0.21.0 / HA 2026.8.1,
against one head of a three-head multi-split (`OptionCode_35880`,
`ExtendOptionCode_199181`). **There is no local reset on this board**, by
either route. Reset appears to be panel-only.

This generation does not put the counter in `/mode/vs/0` at all. Its
options blob carries no `FilterTime`, no `FilterAlarmTime` and no
`FilterCleanAlarm`:

```json
["Sleep_0", "ArtificialWorking_Off", "ComfortAICooling_Off",
 "AiTempChanged_Off", "AiTemp_240", "OutdoorTemp_77", "CoolCapa_25",
 "WarmCapa_32", "Light_Off", "Volume_100", "OptionCode_35880",
 "ExtendOptionCode_199181", "RacInfo_None", "UpdateAllow_NotAllowed",
 "DurationOn_0", "WelcomeCoolingState_Off"]
```

The counter lives in its own resource instead, as a **percentage** of a
500-hour interval rather than tenths of an hour —
`/filter/airdustfilter/vs/0`:

```json
{
  "x.com.samsung.da.filterUsage": "96",
  "x.com.samsung.da.filterUsageResolution": "1",
  "x.com.samsung.da.filterDesiredUsage": "500",
  "x.com.samsung.da.filterStatus": "normal",
  "x.com.samsung.da.filterCapacity": "500",
  "x.com.samsung.da.filterCapacityUnit": "Hour",
  "x.com.samsung.da.filterResetType": ["replaceable", "washable"]
}
```

Three attempts, all against a live unit deliberately put in `fan_only`
first — writes to a powered-off head on this board are dropped silently
with no error, which would otherwise be indistinguishable from a rejected
write:

| Target | Payload | Result |
|---|---|---|
| `/mode/vs/0` | `{"x.com.samsung.da.options": ["FilterCleanAlarm_Clear"]}` | 4.00, options blob byte-identical |
| `/filter/airdustfilter/vs/0` | `{"x.com.samsung.da.filterUsage": "0"}` | 4.00 |
| `/filter/airdustfilter/vs/0` | `{"x.com.samsung.da.filterUsage": 0}` (integer) | 5.00 |

`filterUsage` stayed at `96` throughout, verified by a live re-read after
each write rather than by the integration's optimistic state.

The last two rows are the informative pair. They differ only in JSON type
and return *different* codes, which rules out both boring explanations: an
unresolved href or an unrecognised field name would fail identically. The
board parses the field, faults on the wrong type, and still refuses the
value when typed as the string its own rep uses. The resource is
**read-only**, not mis-addressed.

One trap worth stating plainly: `filterResetType:
["replaceable","washable"]` describes what the filter *is*, not a reset
command that exists. It reads like a hint that a reset write is available
somewhere. It is not.

Since the integration cannot perform the reset here, it can still observe
it. The counter only climbs in normal use, so a downward crossing is
unambiguous: a `numeric_state` trigger with `below: 10` on
`sensor.<name>_filter_usage`, stamping an `input_datetime`, keeps an
honest "last cleaned" date without pretending a reset entity exists. The
blind spot is a reset performed while HA is down or the entry is
unloaded — no state transition, so that stamp has to be set by hand.
