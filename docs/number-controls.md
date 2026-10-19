# Number control presentation

These suggestions describe how future contributions could give
LocalThings controls a more consistent appearance. The accompanying
code change enables numeric Setpoint entry only on the measured
NV51K7770S (`LCD_OV_WALL_16K`).

| Input style | Suggested use |
| --- | --- |
| Numeric box | Choosing an exact value, such as temperature or duration |
| Slider | Relative adjustment across a range, such as brightness |

For future model support, identify the affected control and use the
model's own reported identity to scope the change. Keep model checks
with the capability rather than placing board names in the generic
number platform. The current oven's `box_fn` predicate is a narrow
example: it checks the exact board token in that cavity's canonical
information resource, and defaults to the existing slider if identity
is absent or does not match.

Adopt presentation changes for further models in separate, reviewed
contributions. A shared descriptor may serve several appliance types,
so changing it unconditionally can affect more devices than intended.
Keep units, conversion, limits, increments, entity IDs and command
handling intact; a change in appearance should not change their meaning.

For a model-specific change, cover the affected model and its subdevices,
similar model identifiers that must not match, and other models sharing
the descriptor. Preserve existing names and translations when only the
input presentation changes. Custom dashboards may choose their own UI.
