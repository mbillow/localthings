# Checking partial item writes

An appliance can accept a setting and return more information than was sent.
For example, a temperature write sends the requested temperature and item ID;
the reply can also include the current temperature, limits and another probe.
Comparing the entire returned list to the small write incorrectly reports that
the setting did not take effect.

The existing `merge_items_field` helper already accounts for this device behavior
in the optimistic cache. The captured `oven_device.json` fixture includes the
extra temperature fields, and [oven-cycle-start.md](oven-cycle-start.md#what-starts-a-cook)
records a successful partial temperature write and its later readback.

## Verification rules

- Match items by their exact, nonempty string ID, regardless of list order.
- Check every requested field. Extra returned fields and other items are allowed.
- Require unique IDs in both lists. Missing IDs, duplicate IDs, missing requested
  fields and different values do not confirm the write.
- Compare finite numeric setpoints by value: `"350"`, `350` and `350.0` agree.
  This accommodates numeric legacy HTTPS replies. IDs and units are not converted.
- An empty item list still requires an empty returned list.

The same checks apply to individual resources and each resource in a batch.
The existing option-token comparison also applies consistently to both paths.
Other lists and nested values still compare in full.

Stop still requires the requested operational state in a successful confirming
read. A reply saying `Run`, or omitting the requested state, cannot confirm a
request for `Ready`. Connection errors, retries, remote-control permissions and
the existing handling of an unavailable confirmation read are unchanged.

## Regression coverage

`tests/test_partial_item_write_verification.py` covers full replies to partial
writes, reordered items, numeric representations, wrong or ambiguous items,
exact units, immediate and delayed verification, batch results and Stop with
Remote Control off. These are simulated transport tests; this change adds no
new live-appliance verification.
