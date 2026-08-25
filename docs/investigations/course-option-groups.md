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

## The four named kinds

| Kind | Named | Evidence |
| --- | --- | --- |
| `0x8` | water temperature | WW6500 panel reading; the only kind reaching bit 6 on that table, and `supportedWaterTemperature` its only seven-entry list |
| `0x9` | rinse | the same reading pins the set; `0xA` is ruled out as rinse below |
| `0xA` | spin | as above — the only one of the two that can address index 6 |
| `0xD` | dry | DV5000T owner's per-course panel report, corroborated by the DV6800N on a different board and code space |

**`0xD`.** A DV5000T owner reported what their panel offers per course, and
its fourteen records reproduce that exactly. The DV6800N (`dryer_dv6800n`) is
the independent check — different board family, different code space, courses
labelled — and its policy lands course-for-course on the same shape: full
range on Cotton/Mixed/Synthetics, one fixed level on Wool and Iron Dry, a
different one on Bedding and Delicates, a timed dry instead on Cool Air/Warm
Air/Time Dry, neither on Quick Dry. It is also the only dump carrying both
`0xD` and `0xE`, and no course offers both.

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
above. `0xE` behaves like dry time on the DV6800N — decodes against
`supportedDryTime`, mutually exclusive with `0xD` — but one board is not a
pin; name it in the change that consumes it.
