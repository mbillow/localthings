# What each course permits: decoding `supportedOptions`

`laundry.py`'s `course_option_mask` reads `/course/vs/0`'s `supportedOptions`
past the course codes and into the payload behind each one. The module
comment carries the conclusions and the caveats a caller needs; this file
carries the evidence behind the kind numbers, and the one board that does not
fit them.

## The format

```
supportedOptions = <1 nibble header> + one record per course
record           = <course code:1B> then 2-byte groups
group            = <kind nibble><default nibble> <mask>
```

Every shipped dump fits `1 + 2×groups` bytes per record — widths of 1, 3, 5,
7, 9 and 11 bytes, from an AirDresser carrying no groups to the WA8000T. The
mask indexes that option's own `supported<Option>` list, and so does the
default nibble — but into the list, not into the mask: the dishwasher reports
default `0` with a mask allowing only index `1`.

**The header is the group count**, so the record width is stated rather than
inferred: `1 + 2×hdr` bytes. It holds on all 17 dumps carrying a
`supportedOptions`, across all six widths, and the split it gives is the one
the original smallest-that-parses scan already produced on every one of them —
so adopting it moved no course list and no golden.

| hdr | 0 | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- | --- |
| width (bytes) | 1 | 3 | 5 | 7 | 9 | 11 |
| dumps | AirDresser | 5 | 2 | 5 | 3 | WA8000T |

`_course_records` keeps the scan as a fallback for a board that contradicts
this, and still checks the stated width against the guards the scan was
written for (distinct course bytes, the selected course present, and any live
`editCourseList` accounted for) rather than trusting it outright.

**The mask is one byte**, so it cannot address past index 7. Most lists are
comfortably shorter, but `supportedDryTime` is 11 entries on `dryer` and
`dryer_tp1_21_drum_clean` and 13 on `dryer_dv80h` and
`washer_dryer_onebody_awm` (as is `washer_wa8000t`'s 12-entry
`supportedWaterHeight`). None of those four boards
carries a group for it, so nothing here contradicts the format — but whatever
encodes dry time on them is not an 8-bit-mask group of this shape. The dryer's
`dry_time` select is gated on `0xE` and so simply does not narrow there; a
board that carried both a long list and a group for it would be the first to
need more than a byte.

## The five named kinds

| Kind | Named | Evidence |
| --- | --- | --- |
| `0x8` | water temperature | WW6500 panel reading; the only kind reaching bit 6 on that table, and `supportedWaterTemperature` its only seven-entry list |
| `0x9` | rinse | the same reading pins the set; `0xA` is ruled out as rinse below |
| `0xA` | spin | as above — the only one of the two that can address index 6 |
| `0xD` | dry | DV5000T owner's per-course panel report, corroborated by the DV6800N on a different board and code space |
| `0xE` | dry time | the DV6800N again: it complements `0xD` course for course, and decodes against `supportedDryTime` |

**`0xD`.** A DV5000T owner reported what their panel offers per course, and
its fourteen records reproduce that exactly. The DV6800N (`dryer_dv6800n`) is
the independent check — different board family, different code space, courses
labelled — and its policy lands course-for-course on the same shape: full
range on Cotton/Mixed/Synthetics, one fixed level on Wool and Iron Dry, a
different one on Bedding and Delicates, a timed dry instead on Cool Air/Warm
Air/Time Dry, neither on Quick Dry. It is also the only dump carrying both
`0xD` and `0xE`, and no course offers both.

**`0xE`.** Named on that one board, but on the complement rather than on
"it decodes against `supportedDryTime`" — which on its own is weak, since a
six-entry list absorbs most masks without complaint. Every one of the
DV6800N's fourteen records carries *both* a `0xD` and a `0xE` group, and the
exclusion above is carried by an empty mask rather than a missing group:

| courses | `0xD` | `0xE` |
| --- | --- | --- |
| the ten level-dried ones (Cotton, Wool, Iron Dry, Bedding, Delicates, …) | values | `E000` |
| the three timed ones (Cool Air, Warm Air, Time Dry) | `D000` | values |
| Quick Dry 35 | `D000` | `E000` |

A kind unrelated to the dry dial would not go quiet exactly where the dry
dial speaks and speak exactly where it goes quiet, across fourteen courses.
The one course offering neither is Quick Dry, which takes no dry setting at
all — so the complement is total wherever there is anything to complement.

Corroborated the same way the WW6500's record shape is: this dump's
`/course/vs/0` options carry a `MostUsed_9AD20EE000` token, byte-identical
to course `9A`'s record, which pins the 5-byte width and both groups
independently of the header.

A second board carrying `0xE` would settle it outright. Until then this is
one board's structure, and the four boards reporting a `supportedDryTime`
with no `0xE` group anywhere decode to "no opinion" and keep their full
list — so naming it narrows nothing that was not described.

**`0x8` / `0x9` / `0xA`.** A WW6500 owner read one course's three dials off
the panel: Cold/20/30/40 with no "None", every rinse count, every spin
including rinse-hold. Two of its courses carry exactly those sets:

```
5C  841E 923F A53F
61  841E 943F A43F
```

`0x1E` skips bit 0, the `None` the panel indeed omits. The reading pins the
*sets*, not which of `0x9`/`0xA` is which — their masks are `0x3F` alike, so
swapping the two constants leaves it passing. What separates them is list
length: on the `washer` dump `0xA` addresses index 6, which a six-entry
`supportedRinseCycles` cannot hold, while `0x9` tops out at index 5.

**Record shape.** The WW6500 also carries a `QuickWashSet_5B847E933FA53F`
token — byte-identical to course `5B`'s record. A standalone copy in a
separate field pins the width and group structure independently of the
divisor scan that recovers them.

**Corroboration.** Across every dump, each live `waterTemperature` /
`rinseCycles` / `spinLevel` / `dryLevel` sits inside its course's decoded set,
and no dry mask addresses past the end of `supportedDryLevel`.

## The board that uses `0xB` instead of `0xD`

Six dumps carry a `supportedDryLevel`; five route to the dryer registry.
`washer_dryer_combo` — a WW6600R on `DA_WM_TP2_20_COMMON`, eight entries
(None, Cupboard, 30, 60, 90, 120, 180, 240) — is the one routing to the
**washer** registry, so `washer.py`'s `dry_level` select is the entity at
stake. **It carries no `0xD` group at all**, and its `0xB` is unmistakably the
dry dial:

| courses | default | allowed |
| --- | --- | --- |
| wash+dry (`1C`, `1B`, `1E`, …) | None | None / Cupboard / 30 / 60 / 90 |
| dry-only (`36`, `38`, `39`) | Cupboard | Cupboard / 30 / 60 / 90 |
| wash-only (`24`, `30`, `32`, …) | None | *(empty)* |

Dry-only courses dropping `None` and defaulting to `Cupboard` is the
semantics you would want, the default matches the live `dryLevel` on the
selected course, and there is no competing list it could be indexing — this
board has no `supportedDryTime`. Two details for whoever gates it: the list
mixes a dryness level with durations (`Cupboard`, then 30…240), and no course
addresses past index 4, so `120`, `180` and `240` are advertised and offered
by nothing.

`0xB` stays unnamed anyway, because the corpus supports something narrower
than "`0xB` is dry":

- Not a combo rule — `washer_dryer_onebody_awm` is also a combo and uses
  `0xD`.
- Not a board-family rule — the DV5000T that `0xD` was named on is
  `DA_WM_TP2_20_COMMON`, this WW6600R's own family.
- The dishwasher cannot arbitrate: it carries `0xB` and `0xD` with
  byte-identical payloads in every record (`B102 D102`, `B002 D002`; the cloud
  board adds a matching `0xC`), so it reads as confirming `0xD` while being
  unable to discriminate it.

**The consequence:** a gate keyed on `0xD` alone silently no-ops on the one
board whose `dry_level` select it would be narrowing.

## Unnamed

`0x0`, `0x5`, `0x6`, `0x7`, `0xB` and `0xC` all occur, none pinned beyond the
above.

`0x6` is the one kind that reaches bit 7 anywhere in the corpus
(`washer_wa8000t`, mask `0xA1` on eight of its thirteen courses). No named
kind does.
