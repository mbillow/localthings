# NV51K7770S remote cooking-mode choices

The Cooking Mode selector offered programs that were excluded from this oven's
remote controls. Broil could be selected but then failed LocalThings' existing
start validation. Cleaning and Gourmet Cook also remained in the menu after the
owner requested their exclusion.

This change removes seven choices on the exact `LCD_OV_WALL_16K` board:

| Raw code | Reason |
| --- | --- |
| `Broil`, `UpperBroil` | Existing LocalThings start policy rejects the Broil family. The owner's test encountered this validation error, not a captured firmware rejection. |
| `Drain`, `SelfClean`, `PyroFree`, `Descale` | Cleaning and maintenance controls excluded at the owner's request. |
| `Autocook` | Gourmet Cook requires an involved panel setup and was excluded at the owner's request. |

These exclusions do not establish that every cleaning mode is blocked by the
firmware. The original observations are in the
[preserved oven investigation](https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md).

## Implementation

`registry/capabilities/oven.py` specializes the existing mode capability for this
board, selected only by `registry/by_type/oven.py`. Other wall ovens use the
original descriptors; ranges and microwaves retain their existing registries.
The exact board token before the first `|` in `modelNum` must match. The lower
cavity's `_DIV` description does not broaden this model check.

The specialization filters the existing options and rejects the same seven
codes in its mode writer. This covers both live choices and the static fallback
used without a reported list. It preserves mode order, idle-state handling,
raw command codes and the existing start policy. Healthy Cook labels, sorting,
Sound controls and Start Diagnosis are separate changes.

The transport and resource cache retain the reported modes. Running, cavity
state and diagnostics continue to report a program started at the panel.
Home Assistant requires a selector's state to be one of its choices, so the
Cooking Mode selector itself reads **Unknown** while an excluded mode is active.
The raw mode remains in diagnostics; it is not changed to No Operation.

Entity IDs and translation keys are unchanged. No registry migration or new
translation strings are needed.

## Verification

- Replay the captured legacy oven with the divider inserted and removed;
  compare the selector against each cavity's unmodified supported-mode list.
- Check that excluded modes cannot be held for a start or written while running,
  and that the static fallback also excludes Broil.
- Preserve allowed mode codes, order and idle validation, including Healthy Cook
  and unrecognized future modes without implying new remote-start support.
- Retain raw mode and running status during panel-started Broil and Self Clean.
- Check exact model boundaries and the NW9300MD microwave, TP1X range, TP2X
  wall oven and NV75N dual-cook fixtures without updating their golden files.

This branch is independent of the other prepared oven features. The combined
review must compose its mode descriptor changes with the separate Sound gate
and Healthy Cook labels before preparing a cumulative test build.
