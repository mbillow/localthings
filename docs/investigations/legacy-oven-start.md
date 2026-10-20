# NV51K7770S remote start

Measured on Samsung NV51K7770SG/AA, board `LCD_OV_WALL_16K` with
`LCD_OV_WALL_16K_DIV` as its lower record, software `20201110.0138`,
firmware `17071200,17041300`, using local HTTPS on port 8888.
Related: [#572](https://github.com/mbillow/localthings/issues/572).

The oven refused a combined mode/temperature/duration/Run write with
`4.00 Control fail`. Both cavities started when settings were sent first,
followed three seconds later by Run to the same `/devices/<index>` endpoint:

```json
{"Device":{"Mode":{"modes":["LowerBake"]},"Temperatures":[{"id":"0","desired":350,"unit":"Fahrenheit"}],"Operation":{"operationTime":"00:05:00"}}}
```

```json
{"Device":{"Operation":{"state":"Run"}}}
```

Both writes returned success. Delayed reads showed `Run`, `Preheat`, 350 F
and five minutes for Lower Bake and Upper Convection Bake. The owner later
confirmed whole-oven Bake with the divider removed, simultaneous upper and
lower cooking, and independent Stop buttons. Those later checks used the
sequence with fresh permission checks described below; the original test
had no intervening read. The owner subsequently confirmed an ordinary
timed start with a 0.5-second settings-to-Run pause on their combined-device
installation. This fix uses that pause.

Smart Control off, or the dial on Bake awaiting a panel start, allowed
settings but refused Run. A fresh read of the target cavity's
`/devices/<index>/configuration` therefore must explicitly report
`remoteControlEnabled: true` before settings and again before Run. Missing,
invalid or unreadable permission stops the sequence even if the cached
state is enabled or the integration's remote-control bypass is selected.
The coordinator holds its session lock across the sequence, and every
request uses the remaining portion of one timeout budget.

If a response is lost, the start is not automatically replayed: Run may
already have reached the oven. Home Assistant reports the uncertainty so
the user can check the appliance. Explicit appliance refusals are returned
without another attempt. Stop remains available with Smart Control off.

This sequence is limited to `LCD_OV_WALL_16K`. Other legacy oven boards,
OCF starts, settings-only writes and bare Run probes keep their existing
behavior. Entity lists, labels, power controls and cooking limits do not
change. The one-minute-then-clear workaround is excluded pending the
maintainer's requested discussion; use a positive cook time for this fix.
