# NV51K7770S Sound control

The Sound switch appeared as Off because this oven's options list contains no
`Sound_*` token. Toggling it did not control the panel's Volume setting.

On 14 October 2026, the owner supplied diagnostics with panel volume at 0 and
again at 15. Both cavities' resource data and raw legacy responses were identical
at those settings. The panel has a volume slider, but the captured local API
provides no corresponding value or verified volume command. The original
[configuration investigation](https://github.com/roweboat123/localthings/blob/b9c485da9606f2dab5c8b1a4f35a6325582e56f8/docs/investigations/legacy-oven-controls.md#configuration-switches)
records those comparisons and the successful Energy Saving check.

## Change and scope

For the exact `LCD_OV_WALL_16K` board, Sound is offered only when the cavity
reports a Sound token. Energy Saving retains its existing descriptor and command.
This change does not add a volume slider or change Start Diagnosis.

`registry/capabilities/oven.py` specializes `OVEN_MODE` with the existing
`_has_option("Sound")` predicate. `registry/by_type/oven.py` selects that
specialization for the measured board; other oven boards use the original
entity descriptions. The shared `OVEN_MODE` remains unchanged because ranges
and microwave/oven combinations also use it.

That separation addresses the regression identified in the
[review of #637](https://github.com/mbillow/localthings/pull/637#issuecomment-6255169643),
where applying Sound gating to the shared capability removed controls from
other appliances without supporting hardware evidence.

## Existing installations

Setup removes the old Sound switch only when this board reports a complete
options list without a Sound token. A missing or incomplete response does not
trigger removal. The lookup uses the integration, switch platform, full unique
ID and owning config entry, so it also finds renamed or disabled entries without
removing another device's control.

Dashboard and automation references to the removed switch need deleting. Each
removal is logged. Existing translations are retained for appliances that still
expose Sound; there are no new labels.

## Validation

`tests/test_oven_sound_control.py` checks the captured oven, explicit Sound tokens,
model boundaries, incomplete responses, offline snapshots, and exact-identity
cleanup. It also checks that the NW9300MD microwave, TP1X range and TP2X wall oven
retain their Sound controls. The existing golden entity lists are unchanged.
