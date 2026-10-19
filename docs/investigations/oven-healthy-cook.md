# NV51K7770S Healthy Cook

Scope: the `LCD_OV_WALL_16K` board reported by the NV51K7770S. Its legacy
8888 API supplies no `modeSpec` and reports a static mode list that omits
the lower cavity's Healthy Cook codes.

## Why map the recipes

A numbered Healthy Cook option does not tell the user which food it prepares.
For this oven, `Healthycook3` means **Healthy Cook Salmon Steak**. Showing that
name lets the user match Home Assistant to the oven's menu without memorizing
recipe numbers. The command still uses the original code.

The matching temperature presets and limits matter as much as the label. A
named recipe should begin with its intended preset, and its setpoint control
should reflect the range that recipe permits in the selected cavity. Bread
Proof has a fixed internal target, so presenting the ordinary adjustable Bake
range would imply a temperature choice the oven does not offer.

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

## Contributor guide

| File | What to inspect or change |
| --- | --- |
| [`registry/capabilities/oven.py`](../../custom_components/localthings/registry/capabilities/oven.py) | `OVEN_MODE` selects the `oven_mode_lcd_wall_16k` translation key for the verified board. `_mode_setpoint_bounds`, `_oven_setpoint_available` and `_oven_setpoint_write` keep the displayed limits, availability and writes consistent. |
| [`translations/en.json`](../../custom_components/localthings/translations/en.json) and the other [`translation catalogs`](../../custom_components/localthings/translations/) | `entity.select.oven_mode_lcd_wall_16k.state` maps normalized option keys such as `healthycook3` and `lower_healthycook3` to readable recipe names. |
| [`registry/capabilities/cook.py`](../../custom_components/localthings/registry/capabilities/cook.py) | `healthy_cook_temps` supplies the per-recipe `TempSpec` values. `_specless_specs` uses them when a board has no live `modeSpec`; `mode_specs` gives live declarations precedence. |
| [`legacy_http.py`](../../custom_components/localthings/legacy_http.py) | `with_cavity_modes` adds the four verified Lower Healthy Cook codes missing from this board's static list. This is a model-specific correction, not a general rule for ovens. |
| [`tests/test_oven_healthy_cook.py`](../../tests/test_oven_healthy_cook.py) | Checks labels against raw codes, presets and limits in both units, cavity routing, probe preservation, live declarations and other-model isolation. |

For another model, record a mapping table like the one above from panel choices
and corresponding resource captures. Confirm which cavities offer each recipe,
its preset, permitted range and whether its target is adjustable. Keep native
Celsius settings separate from Home Assistant's conversion of Fahrenheit
readings. Record the source of each value and clearly identify any inference.

Then make the corresponding changes in these locations:

1. Add a model-specific translation key selected by the capability and supply
   its recipe names in every catalog. Preserve the raw mode codes and existing
   entity key. Do not give another board this oven's recipe meanings merely
   because both report `Healthycook1` through `Healthycook6`.
2. Add verified fallback temperatures in the cooking capability. `TempSpec`
   takes minimum, maximum, default and step, in that order. For example,
   `TempSpec(80, 285, 190, 5)` describes the native Celsius fallback for this
   oven's undivided recipes 1–3. Keep a fixed target fixed and allow a live
   `modeSpec` to override the fallback.
3. Change `with_cavity_modes` only if captures establish that the model's
   reported list omits a real mode. Do not infer remote-start support from a
   label or a panel-started capture; that needs its own control evidence.

Use the existing tests as a template for each raw code and cavity. Include a
nearby model that must retain its original labels and limits, a live declaration
that overrides the fallback, and a probe item that must survive a setpoint
write. For a focused check after setting up the development environment:

```sh
.venv/bin/pytest tests/test_oven_healthy_cook.py -q
```

Compare the displayed recipe names and presets with the target oven's panel,
record any hardware checks, and follow [CONTRIBUTING.md](../../CONTRIBUTING.md)
for the full checks before submitting a code change.
