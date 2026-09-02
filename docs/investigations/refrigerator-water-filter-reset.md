# Refrigerator water-filter reset: payload unverified

`/filter/waterfilter/vs/0` advertises `filterResetType`, but that metadata does
not establish a local reset command. A same-value `filterUsage` write was
accepted and read back unchanged on one refrigerator. That showed only that
the request was accepted; because the value did not change, it did not
establish reset semantics.

An exact follow-up test on another refrigerator sent
`{"x.com.samsung.da.filterUsage": "0"}`. The appliance returned `2.04 Changed`,
but an exact GET one second later still reported `filterUsage: "100"`. A
successful CoAP response is therefore not proof that this payload performs a
reset on this family.

Do not expose a reset button from `filterResetType` alone. The missing evidence
is a payload that produces a stable zero on a fresh readback without changing
unrelated fields. Until that is captured on hardware, LocalThings exposes the
filter status and usage but no reset command.
