# NV51K7770S Healthy Cook

Scope: the `LCD_OV_WALL_16K` board reported by the NV51K7770S. Its legacy
8888 API supplies no `modeSpec` and reports a static mode list that omits
the lower cavity's Healthy Cook codes.

## Recipe mappings

Owner-confirmed mappings for Oven, Upper and Lower cooking:

| Recipe | Oven code | Upper/lower codes | Fahrenheit preset | Celsius preset |
| --- | --- | --- | --- | --- |
| Baked Potato | `Healthycook1` | `UpperHealthycook1` / `LowerHealthycook1` | 375°F | 190°C |
| Grilled Chicken | `Healthycook2` | `UpperHealthycook2` / `LowerHealthycook2` | 375°F | 190°C |
| Salmon Steak | `Healthycook3` | `UpperHealthycook3` / `LowerHealthycook3` | 375°F | 190°C |
| White Fish Fillet | `Healthycook4` | `UpperHealthycook4` / `LowerHealthycook4` | 400°F | 205°C |
| Dehydrate | `Healthycook5` | — | 150°F | 65°C |
| Bread Proof | `Healthycook6` | — | Fixed internal target: 100°F | Fixed fallback: 40°C (inferred) |

Panel-started captures on 14 October 2026 identified all eight Upper/Lower
recipe codes and their presets. Divider-removed captures identified
`Healthycook5` at 150°F and `Healthycook6` at 100°F, both reporting
Operation `Run` and Oven `Cooking`. The owner confirmed the recipe labels
in Home Assistant. These captures identify the settings; they do not
establish remote-start support for Dehydrate or Bread Proof.

Panel photographs supplied on 19 October 2026 show the native Celsius
presets for recipes 1–5: `1086.jpg` for Upper, `1087.jpg` for Lower, and `1088.jpg`
for Oven. Recipes 1–4 have the same presets in all three views; Dehydrate
and Bread Proof appear only in the Oven view.

Bread Proof has no temperature adjustment or temperature readout on the
oven's screen. Its 100°F target is an internal setting, not a measured
cavity temperature. At the owner's request, the native Celsius fallback
is inferred as 40°C: 100°F converts to about 37.8°C, rounded to the nearest
five degrees. This matches the rounding in the observed preset pairs
375°F/190°C, 400°F/205°C and 150°F/65°C. The 40°C value is an estimate,
not a panel readout or measured cavity temperature. Home Assistant offers
only the fixed target for the oven's configured unit.

## Limits and units

The [Samsung NV51K7770S manual](https://images.thdstatic.com/catalog/pdfImages/99/9900cc56-8433-4e1b-ba18-4271b575791d.pdf),
pages 34–35, gives these ranges and the Fahrenheit presets above:

| Modes | Oven range | Upper/lower range |
| --- | --- | --- |
| Recipes 1–4 | 175–550°F / 80–285°C | 175–480°F / 80–250°C |
| Dehydrate | 100–225°F / 40–105°C | Not offered |
| Bread Proof | Factory-set, not adjustable | Not offered |

Adjustable recipes use the existing five-degree step. Their native Celsius
presets come from the panel photographs. HA still converts Fahrenheit
values for display and input when HA's preferred unit is Celsius; that
display conversion is separate from the native Celsius fallback.

These are fallbacks only. A live `modeSpec` takes precedence, and other
board tokens retain their existing labels and limits. Cross-cavity
temperature combinations remain subject to the oven's own restrictions.

## Regression coverage

`tests/test_oven_healthy_cook.py` checks translated selections against raw
codes, presets in both units, each cavity's limits, fixed Bread Proof,
HA unit conversion, preservation of probe items in writes, live
`modeSpec` precedence, and isolation from other boards. The tests use the
existing redacted legacy fixture and simulated responses; they do not send
commands to an appliance.
