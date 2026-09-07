# `delayEndTime` is a delay until the cycle *ends*

Raised by #427, which asked whether LocalThings exposes Samsung's native
"Delay End" and whether `delay_start_hours` maps to
`x.com.samsung.da.delayEndTime`. It does map to it, and that mapping was
built on the assumption that `delayEndTime` is just another name for
`delayStartTime`. It is not.

## What the two fields are

| Field | Meaning | Who reports it |
|---|---|---|
| `x.com.samsung.da.delayStartTime` | duration until the cycle **starts** | dishwashers |
| `x.com.samsung.da.delayEndTime` | duration until the cycle **ends** | washers, dryers, air dressers |

Both hold a duration, not a wall-clock time: `"01:00:00"` means one hour
from now, not 1 AM. That part of the original reading was right, and it is
worth keeping stated because the field names invite the other reading.

No dump in the corpus carries both fields, so the split is clean by family.

## Evidence that `delayEndTime` runs to the end

1. `tests/fixtures/washer_wf80h_device.json` is the only dump caught
   mid-delay (`state: Run`, `progress: Delaywash`). It reports
   `delayEndTime` and `remainingTime` as the *same* `09:26:00`.
   `remainingTime` counts to cycle completion, so whatever `delayEndTime`
   counts to, it is the same instant.
2. The field is named `delayEnd`, and Samsung's own app calls the feature
   "Delay End" and asks for a finish time.
3. #308 reported that a delay set to 1 h on a `DA_WM_TP1_21_COMMON`
   washer/dryer came back as ~9 h and would not go lower, and that other
   values landed "off by one or two hours". That is the expected shape of a
   delay-until-end write: an appliance cannot finish sooner than its cycle
   takes, so any requested end sooner than the cycle length clamps up to it.
   Read as delay-until-start, that behaviour looks like a broken slider.

## What this does *not* establish

**That the appliance accepts minute-granular delays.** The `09:26:00`
above is the appliance's own countdown, not a value anyone set. It shows
the field *reports* minute resolution; it says nothing about what a write
is rounded to. #308's hardware increments its panel control by whole hours,
which hints the opposite. Anyone adding minute precision to the entity
should measure a minute-granular write and re-read it first, on hardware.

**The exact arithmetic to convert between the two.** Making
`delay_start_hours` mean delay-until-start on laundry would require writing
`requested_delay + cycle_duration`. `remainingTime` while `Ready` looks
like the cycle estimate, but the appliance re-estimates after load sensing,
and during `Delaywash` `remainingTime` is no longer separable into delay
and cycle. That conversion is not safe to guess.

## What changed here

Only what the evidence supports:

- `_delay_field` now prefers `delayStartTime` and is used for **both** the
  read and the write. The number previously read `delayStartTime` while
  writing `delayEndTime`, so a device reporting both would have displayed a
  value the write never touched -- a set that looks like it did nothing.
  Unreachable today (nothing reports both), but the two keys mean different
  things and must not be mixed inside one entity.
- The comments asserting the fields are interchangeable are corrected.

The entity keeps its `delay_start_hours` key and hour granularity. On
laundry it is really "finish in N hours", and renaming it would change
every existing user's `entity_id` for a wording fix while the underlying
conversion question is still open.
