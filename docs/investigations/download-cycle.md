# Laundry cloud "Download" cycles: solved, with one dead end

`registry/capabilities/laundry.py`'s cycle select offers a washer's
downloaded ("Download" / "Downloaded") programs alongside its ordinary
courses now, driven by the store in `cloudcourse.py` (issue #342). This file
records the byte-level work behind it, including a decode that fit one
device perfectly and collapsed on the second — the reason nothing in the
shipped code interprets a program payload at all.

Four devices in the corpus carry these tokens (a survey of every laundry
diagnostics dump attached to an issue turned up 14 devices; the other 10 have
no cloud tokens at all, so this is a minority feature):

| dump | model | slots advertised | payloads seen | blob width |
| --- | --- | --- | --- | --- |
| `washer_ww5000c_cloud` | WW5000C `_B06C`, `DA_WM_TP1_21_COMMON`, Table_02 | 9 | 2 | 20 bytes |
| `washer_wa55a7700av` | WA55A7700AV, `DA_WM_TP1_21_COMMON`, Table_02 | 2 | 1 | 16 bytes |
| `dishwasher_dw5000c_cloud` | DW5000C, `DA_DW_TP1_21_COMMON` | 4 (but see below) | **0** | — |
| (not fixtured) | WW5000C `_B048`, issues #259/#343, Table_02 | 9 | 1 | 20 bytes |

Three things follow immediately from that table:

- **`CloudExtraCourse_` does not mean the same thing on every family.** On
  the DW5000C all four of its bytes (`8E 8D 8F 02`) are course codes in that
  dishwasher's *own* course list, three already translated (Plastic, Pots and
  pans, Baby Care). There it tags which ordinary courses came from the cloud;
  they select with a plain `Course_` write and need no payload — consistent
  with it carrying no payload token at all. It also has a
  `DownloadCourseList_8F` token the washers lack.
  On both washers the slots share **zero** overlap with the course list and a
  payload is required. Subtracting the course list is what tells the two
  apart (`cloudcourse.cloud_slots`), so the feature engages on the washers
  and correctly does nothing on the dishwasher.
- **A device can advertise a slot it has never loaded.** True on the washers
  too — nothing about a program is learnable until its owner runs it, which
  is what the Repairs issue exists to explain.
- **Both WW5000C units advertise the byte-identical slot list**
  (`0A5C286B2D0C55301A`, same nine slots in the same order) despite different
  firmware builds. Either the set is a factory/regional default rather than
  something each owner curates, or the two dumps share an owner — unresolved,
  but worth knowing before assuming a user picked their own programs.

## The three tokens

All on `/course/vs/0`'s `x.com.samsung.da.options` array, same
prefix-match/replace merge as every other token there.

- `CloudExtraCourse_<slot><slot>…` — the device's own list of downloaded
  program slots, one byte each. The cloud counterpart of `EditCourseList_`.
- `CloudCourse_<blob>` — the persisted default program.
- `OneTimeCloudCourse_<blob>` — a this-run-only override.

### `CloudExtraCourse_` is an enumeration, and byte 2 of a blob is its slot

The WW5000C reports `CloudExtraCourse_0A5C286B2D0C55301A` — nine bytes for
its nine downloaded programs. Byte 2 of each of the nine blobs its owner
captured is exactly one of those nine, no repeats, sets equal:

```
        blob                                       byte2  program
        00 21 55 04 49 28 4D 13 4A A0 4C 00 …       55    Sports
        00 20 28 04 49 00 4D 00 4A B8 4C 00 …       28    Spin only
        00 02 5C 04 49 28 4D 13 4A B0 4C 00 …       5C    Outdoor
        00 1F 6B 04 49 28 4D 13 4A A0 4C 00 …       6B    Jeans
        00 2E 2D 04 49 30 4D 12 4A A0 4C 00 …       2D    Super quiet
        00 2F 0C 04 49 58 4D 14 4A B8 4C 00 …       0C    Baby care intensive
        00 0D 30 04 49 30 4D 12 4A B8 4C 00 …       30    Cloudy heaven
        00 30 1A 04 49 28 4D 12 4A A0 4C 00 …       1A    Shirts
        00 04 0A 04 49 40 4D 13 4A B8 4C 00 …       0A    Towels

CloudExtraCourse_  0A 5C 28 6B 2D 0C 55 30 1A
```

Confirmed independently on the WA55A7700AV: `CloudExtraCourse_5958`, and its
`CloudCourse` blob `00 1C 59 05 …` has byte 2 = `59`. Its
`OneTimeCloudCourse` is `FF FF 01 …` — byte 2 = `01`, which is *not* an
advertised slot, and the `FFFF` prefix marks it as "nothing loaded" rather
than naming a program. That sentinel is why `cloudcourse.is_loaded` exists.

This is what makes "3 of 9 discovered" answerable, and it is why no catalog
of program ids is hardcoded anywhere: the appliance already knows which
programs it has.

### The advertised list isn't always complete (issue #780)

A WA54CG7150 (`DA_WM_TP1_21_COMMON`, Table_02) reports
`CloudExtraCourse_5958`, the same two slots as the WA55A7700AV above. Its
owner's SmartThings "Download cycles" screen offers five programs (Perm Press,
Wool, Eco Cold, Denim, Power Rinse) and holds one at a time. The downloaded
Eco Cold sits in `CloudCourse_00 13 86 …`, slot `86`, which the list never
mentions, and the list stays `5958` whichever program is downloaded.

The owner then downloaded Eco Cold and selected Wool from Home Assistant. The
washer took Wool's settings and ran it, SmartThings showed "Wool" while it ran,
and it went back to Eco Cold afterwards. So on this family the list is a fixed
subset, not the set of programs the washer can run. Any loaded payload is
learned whatever its slot, and the counts use the advertised slots plus the
learned ones (`cloudcourse.known_slots`). That means the "of N" total only
grows as programs are seen; the device gives no way to know the real total.

## Writing: the two-token rule

Confirmed on hardware by the issue #342 reporter. Writing
`OneTimeCloudCourse_<blob>` alone while some other course is selected is
accepted at the protocol level (no error) and then silently ignored by the
machine. It takes effect only when the same write also switches `Course_` to
the Download course — which is what `laundry._cloud_cycle_write` does, and
the only two-token options write in the codebase:

```yaml
x.com.samsung.da.options:
  - Course_87
  - OneTimeCloudCourse_001F6B0449284D134AA04C0035F004F005F0AC00
```

`CloudCourse_` was separately confirmed writable on its own: set while on
Download it changes the running program; set from another course it becomes
what gets preselected the next time Download is chosen. The integration
doesn't write it today — a "default download cycle" control is a possible
follow-up, deliberately left out of the first pass.

### There is no single "Download" course code

The WW5000C's Download is `Course_87`; the WA55A7700AV's is `17`
("Downloaded" in `washer_cycle_table_02`). **Same course table, different
code.** Any per-table lookup of "the Download code" would have been wrong on
one of the only two devices available to check it against, which is why the
code is learned by observation and confirmed by the user in the options flow
instead of tabled.

The observation signal is "whatever `Course_` reads at the moment a
non-sentinel `OneTimeCloudCourse_` *appears or changes*" — a transition that
was actually watched, not a state. Tokens in this array are replaced by
prefix and never evicted, so a payload merely sitting there says nothing
about when it got there; on the first rep after a restart it is equally
consistent with "just loaded" and "left over from last week". Believing it
would propose whatever ordinary course the appliance happens to be sitting
on, and accepting that prefill starts a real wash cycle. Even a genuine
transition is only ever a *candidate*, confirmed by the user before use.

Both dumps in the corpus taken while off the Download course
(`washer_wa55a7700av` on `Course_01`, the `_B048` washer on `Course_1C`)
show the appliance clearing its one-time token to the `FFFF` sentinel, so
the saved default persists but the one-shot does not. That makes the stale
case unlikely on these boards — which is a reason to expect it to behave,
not a reason to depend on it.

## The dead end: bytes 5/7/9 do not decode portably

With the nine WW5000C programs and their app-reported settings side by side,
three of the varying bytes fit perfectly:

| byte | meaning | formula | fit |
| --- | --- | --- | --- |
| 5 | wash temperature | `(b - 0x10) / 0.8` °C, `0x00` = n/a | 9/9 |
| 7 | rinse count | `b - 0x10`, `0x00` = off | 9/9 |
| 9 | spin level | `(b - 0x90) / 8` | 9/9 |

Nine for nine, including the internally consistent case: "Spin only" is the
only program with `0x00` in *both* byte 5 and byte 7, matching a cycle that
skips washing entirely while still reporting a spin level.

It does not survive the second device. Against the WA55A7700AV's
`CloudCourse` blob `00 1C 59 05 49 16 4D 11 4A 22 4C 20 37 F0 AC 22`:

- temperature: `(0x16 - 0x10) / 0.8` = **7.5 °C**
- spin: `(0x22 - 0x90) / 8` = **negative**
- rinse: `0x11 - 0x10` = 1 — the only plausible one

### What *does* survive is the grammar

The two boards' payloads are different lengths (20 vs 16 bytes) but not a
different format — same header, same leading fields, two fewer optional
trailing ones:

```
WW5000C  00 | 2155 | 04 | 49:28  4D:13  4A:A0  4C:00 | 35:F0  04:F0  05:F0 | AC:00
WA55     00 | 1C59 | 05 | 49:16  4D:11  4A:22  4C:20 | 37:F0               | AC:22
```

- `00`, then the 2-byte program id, then one byte (`04` vs `05`) — identical
  layout on both.
- Then a tag/value stream whose **first four tags are the same, in the same
  order, at the same offsets**: `49`, `4D`, `4A`, `4C`. These are exactly the
  four whose values vary per program.
- Then a fixed tail, terminated on both by an `AC:<value>` pair. The entire
  width difference is two trailing pairs the WA55 doesn't carry.

The tail is not program data. Across all nine WW5000C programs bytes 12–19
are byte-identical — every trailing pair carries value `F0` except the `AC`
terminator, and `35` vs `37` looks like a board or profile marker rather than
a field. (It is not a field count either: the board with the *higher* leading
byte has *fewer* pairs.)

### Byte 3 is not part of a program's identity

Worth its own heading, because it is the single strongest argument against
ever shipping a table of payloads. The second WW5000C (issues #259/#343,
firmware `_B048`) has its saved `CloudCourse` set to the same program as the
first one's "Towels" capture — and the two payloads differ at exactly one
byte:

```
_B06C  "Towels"      00 04 0A 04 49 40 4D 13 4A B8 4C 00 35 F0 04 F0 05 F0 AC 00
_B048  CloudCourse   00 04 0A 06 49 40 4D 13 4A B8 4C 00 35 F0 04 F0 05 F0 AC 00
                              ^^
```

Same program id, same slot, same values on all four varying tags, same tail.
Only byte 3 moves, `04` → `06`. So it is neither a per-board constant (both
are WW5000C) nor a property of the program (identical in every other
respect) — most likely a download revision or sequence counter.

A hardcoded catalog keyed on program id would therefore have shipped one
unit's byte 3 to the other unit. Whether the appliance would reject that, or
accept it and do something unintended, is untested and does not need to be:
every payload is learned from the device it will be replayed to.

### Sentinels

The `FFFF` "nothing loaded" payload takes its board's own width (16 bytes on
the WA55, 20 on the WW5000C `_B048`) and always carries byte 3 = `00`. Its
byte 2 is *not* reliably meaningful: it equals the currently selected course
on the WA55 (`01`, on `Course_01`) and does not on the `_B048` (`1B`, on
`Course_1C`). Nothing keys off it — a sentinel is rejected on its `FFFF`
prefix, and its byte 2 is not an advertised slot in either dump anyway.

So the payload is tag/value, not fixed offsets — but knowing the grammar
doesn't recover the values. The same four tags carry non-overlapping ranges
between the two boards:

| tag | WW5000C (9 programs) | WA55 |
| --- | --- | --- |
| `49` | `00, 28, 30, 40, 58` | `16` |
| `4D` | `00, 12, 13, 14` | `11` |
| `4A` | `A0, B0, B8` | `22` |
| `4C` | `00` (all nine) | `20` |

Same field, board-specific encoding. Decoding it properly needs a third
device; one device's fit is a coincidence-shaped hypothesis, not a format.

**A trap for whoever picks this up:** the WA55's `/washer/vs/0` reads
Warm / High / 1, which looks like it could confirm a decode of that unit's
`CloudCourse`. It can't — that appliance is sitting on `Course_01` (Normal),
not on its cloud course, so those values describe the local cycle it has
selected, not the saved cloud program. A cross-check like this is only
evidence when the machine is actually loaded with the program being decoded.

So the shipped code never interprets a payload: a blob is recorded whole and
replayed byte-for-byte, exactly as the device reported it, and never
decomposed or rebuilt. The read-only "loaded program's temperature/spin"
sensors this decode would have enabled were dropped for the same reason.

## What is deliberately not done

- **No hardcoded program catalog.** Blobs are cloud-assigned per
  account/region. One owner's captured payload is not evidence about anyone
  else's appliance, and a table of them would offer options that write
  another household's wash settings.
- **No invented names.** The appliance reports an opaque slot id and nothing
  else. Names come from the user in the options flow, the same rule that
  stops an unrecognized local course code from getting a made-up English
  label (PR #251 review).
- **No blob synthesis.** Even with the byte 5/7/9 decode in hand, nothing
  builds a payload from parts — the device was never tested with one, and a
  fabricated blob is an untested write to a wash cycle.

## Open questions for the next dump

1. Does `OneTimeCloudCourse_` clear itself when a cycle finishes, or when the
   course changes? Behavior suggests the appliance falls back to
   `CloudCourse_` when Download is re-entered, but the token's own lifecycle
   is unconfirmed. `laundry.cloud_current` is written to be correct either
   way.
2. What does byte 1 mean? It is distinct per program and *sometimes*
   coincides with a plausible local course code for that program (`2E` Baby
   Care for "Baby care intensive", `30` Cloudy Day for "Cloudy heaven") and
   sometimes doesn't (`21` Colors for "Sports"). Probably a base-course
   reference; not reliable enough to use.
3. What does byte 3 count? It moves between two units holding the identical
   program (`04` vs `06`) and is `00` on every sentinel. A revision or
   download counter is the obvious guess; a dump taken before and after
   re-downloading the same program would confirm it.
4. **The value encoding is still open, and a third *washer* won't
   necessarily settle it.** The survey found one, but its saved program is a
   duplicate of one already captured, so it adds no new tag values. What is
   actually needed is a dump from a board whose `/washer/vs/0` speaks in
   named levels (Cold/Warm/Hot, Low/High) *while that unit is sitting on a
   downloaded program* — then the payload's `49`/`4A` values can be read
   against settings that describe the same program. The WA55 is such a
   board but was captured on a local course, which is why it can't be used
   (see the trap above).
5. The DW5000C's four slots (`8E 8D 8F 02`) are in the same numeric range as
   dishwasher course codes (its selected course is `86`), unlike the
   washers' slots. One payload from that machine would show whether slot ids
   are drawn from the course-code space on some boards.
