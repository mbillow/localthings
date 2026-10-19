# NV51K7770S Healthy Cook

Scope: the `LCD_OV_WALL_16K` board reported by the NV51K7770S. Its legacy
8888 API supplies no `modeSpec` and reports a static mode list that omits
the lower cavity's Healthy Cook codes.

## Recipe mappings

Owner-confirmed mappings for Oven, Upper and Lower cooking:

| Recipe | Oven code | Upper/lower codes | Fahrenheit preset |
| --- | --- | --- | --- |
| Baked Potato | `Healthycook1` | `UpperHealthycook1` / `LowerHealthycook1` | 375°F |
| Grilled Chicken | `Healthycook2` | `UpperHealthycook2` / `LowerHealthycook2` | 375°F |
| Salmon Steak | `Healthycook3` | `UpperHealthycook3` / `LowerHealthycook3` | 375°F |
| White Fish Fillet | `Healthycook4` | `UpperHealthycook4` / `LowerHealthycook4` | 400°F |
| Dehydrate | `Healthycook5` | — | 150°F |
| Bread Proof | `Healthycook6` | — | Fixed internal target: 100°F |

Panel-started captures on 14 October 2026 identified all eight Upper/Lower
recipe codes and their presets. Divider-removed captures identified
`Healthycook5` at 150°F and `Healthycook6` at 100°F, both reporting
Operation `Run` and Oven `Cooking`. The owner confirmed the recipe labels
in Home Assistant. These captures identify the settings; they do not
establish remote-start support for Dehydrate or Bread Proof.

Bread Proof has no temperature adjustment or temperature readout on the
oven's screen. Its 100°F target is an internal setting, not a measured
cavity temperature. Home Assistant therefore offers only that fixed value.

## Limits and units

The [Samsung NV51K7770S manual](https://images.thdstatic.com/catalog/pdfImages/99/9900cc56-8433-4e1b-ba18-4271b575791d.pdf),
pages 34–35, gives these ranges and the Fahrenheit presets above:

| Modes | Oven range | Upper/lower range |
| --- | --- | --- |
| Recipes 1–4 | 175–550°F / 80–285°C | 175–480°F / 80–250°C |
| Dehydrate | 100–225°F / 40–105°C | Not offered |
| Bread Proof | Factory-set, not adjustable | Not offered |

Adjustable recipes use the existing five-degree step. Celsius presets are
left unspecified: the owner uses Fahrenheit and the manual does not list
the native Celsius presets. The English and Canadian French sections of
the [2018 revision](https://content.syndigo.com/asset/6e33f7a4-87cf-4a27-9be2-ffe895f73977/original.pdf)
also show only Fahrenheit presets (page 35 in each language).
A Celsius-configured oven requires an explicit
target for adjustable recipes; Bread Proof's setpoint is unavailable unless
a live `modeSpec` declares a usable target. HA can still convert Fahrenheit
values for display and input when HA's preferred unit is Celsius.

These are fallbacks only. A live `modeSpec` takes precedence, and other
board tokens retain their existing labels and limits. Cross-cavity
temperature combinations remain subject to the oven's own restrictions.

## Regression coverage

`tests/test_oven_healthy_cook.py` checks translated selections against raw
codes, presets, each cavity's limits, fixed Bread Proof, native Celsius
ranges, HA unit conversion, preservation of probe items in writes, live
`modeSpec` precedence, and isolation from other boards. The tests use the
existing redacted legacy fixture and simulated responses; they do not send
commands to an appliance.
