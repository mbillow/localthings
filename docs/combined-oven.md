# One Home Assistant device for a dual-cavity oven

The `feature/legacy-oven-single-device` branch builds on the accepted fixes in
PR #637. It automatically presents a declared Upper/Lower cavity pair as one
physical oven. There is no layout option. Grouping uses the oven/range registry,
two oven resource sets and the existing Lower cavity declaration, including
modeSpec-derived declarations. It does not depend on the marketing model or
legacy HTTPS versus OCF transport.

The available regression data covers the NV51K7770S legacy pair, the NV75N
Dual Cook OCF oven, the NE9801T Flex Duo range, and the newer NV7B6665IAA
Dual Cook's modeSpec-based resources from issues #597 and #600. A differently
branded oven reporting the same layout follows the same path. This is not a
claim that every Bespoke oven has been tested or can authenticate locally.
Single-cavity appliances, unidentified siblings and ambiguous multi-sibling
layouts retain their existing layout.

## Controls and sensors

With the divider inserted, each cavity has its own Cooking Mode, Setpoint,
Cook Time Hours, Cook Time Minutes, Start and Stop on the NV51K7770S. Other
families retain their existing Cook Time entity and model-specific limits.
Names begin with Upper or Lower. Each cavity also retains its own temperature, running and cooking state,
progress, operation time, estimated finish, alarm code and diagnosis status.

When the divider is removed, the main cavity's labels change from Upper to
Oven. Lower cooking entities remain registered but unavailable, then
become available again after the divider is reinserted. Home Assistant's device
page may still show those unavailable entities. Entity IDs do not change with
the divider, and user-customized names take precedence over generated labels.

On the verified NV51K7770S pair, Door, Divider, Reservoir, Power and Smart Control appear once. Child Lock,
Energy Saving and connection diagnostics also remain unprefixed shared entities.
The owner confirmed on October 15, 2026 that Power is shared and Smart Control
is a single physical dial. The shared displays use the primary record;
Start and Stop retain their existing cavity-specific command and permission
checks. Raw readings and downloaded diagnostics retain both protocol records.

Other models retain separately reported Door, Power, Smart Control and option
entities with cavity labels. Equal readings in one capture do not establish
physical sharing. A singleton connection diagnostic and the Lower Divider
remain unprefixed. No controls, limits or restrictions from the legacy oven are
copied onto another model.

Home Assistant's native device page sorts entity names alphabetically, so Lower
appears before Upper in English. The integration cannot specify a separate sort
order. No numbering or invisible sort characters are inserted into names;
a dashboard can arrange an Upper card above a Lower card.

The duration controls set cooking time. They do not expose the oven's separate
kitchen timer. This feature adds no light, volume, Gourmet Cook or cleaning
controls.

## Upgrade and fallback

The existing main HA device is retained. Lower entities move onto it without
changing their unique IDs, entity IDs or custom names. The emptied Lower device
is removed. Existing user-assigned device names are preserved; an old name such
as “FlexDuo All / Upper” can be renamed to “FlexDuo” in HA.

Only the verified NV51K7770S pair removes redundant shared entities:

| Shared entity | Retained identity | Removed copy |
| --- | --- | --- |
| Door | Upper | Lower |
| Divider | Lower | Upper |
| Reservoir | Upper | Lower |
| Power | Upper | Lower |
| Smart Control | Upper | Lower |

Dashboards and automations referencing removed copies need to use the retained
entity. Automations targeting the old Lower **device ID** need the combined
device and a cavity selection, or the existing Lower entity ID. The migration
does not edit automations, dashboards or history.

Create a Home Assistant configuration backup before installing, since this
changes the device and entity registries as well as the integration files.
The accepted two-device runtime build is
`411802144f055ad2513761afc767fc7b59bd78c1`; the fixes branch at
`7122a33c8fe3a58f35bf5ed70af037e15cc723e9` adds only its acceptance notes.
Neither receives the combined-device commits.

HA retains deleted registry identities. The regression suite simulates returning
to the old layout and verifies restoration of the original Lower device ID,
device name and entity IDs, including the removed shared copies. This has been
tested with HA 2026.9.4; a configuration backup remains the recovery path if
registry behavior differs on another HA version.

## Start Cooking action

The action keeps its existing `localthings.start_cooking` identifier. For the
combined oven, its optional `cavity` field accepts `upper` or `lower`. Upper also
means the whole oven with the divider removed. Omitting the field preserves
the existing default of the selected device, which is the main cavity for the
combined oven.

For example, to start the Lower cavity using the settings held by its entities:

```yaml
action: localthings.start_cooking
data:
  device_id: REPLACE_WITH_COMBINED_OVEN_DEVICE_ID
  cavity: lower
```

The usual Smart Control, idle-state and supported-mode checks still apply.
Lower Start is unavailable with the divider removed. An explicit cavity on an
appliance without an identified combined pair is rejected rather than routed
to a different device. The separate Start buttons already know their cavity.

Direct resource actions target the primary protocol view of the combined
device. To inspect Lower resources, use their actual hrefs: `/mode/vs/1` on the
captured pairs, or the index/prefix reported in another oven's diagnostics.
This does not change the protocol's two-record layout.

## Verification

The initial October 15 test build passed all 3,259 tests on Python 3.14.8 and HA
2026.9.4, plus `ruff format --check`, `ruff check` and `ty check`.
The generalized build passes all 3,278 tests (60.67 seconds) and the same three
formatting, lint and type checks. All ten translation catalogs are covered.

`tests/localthings/test_combined_oven.py` exercises actual HA platform setup,
registry migration, stable IDs and custom names, divider changes without a
reload, offline snapshot setup, and restoring the old layout. Start-action
tests verify the exact Upper and Lower request paths and separate settings.
Unidentified siblings and entities owned by another entry are protected.
The generalized build adds actual HA registration/migration checks across the
three OCF cases above, independent command paths and remote permissions,
offline modeSpec restoration, and preservation of other appliance layouts.

The suite loads entity names in all ten shipped locales and checks that mode
state translations match the existing catalog. English buttons use Upper Start,
Lower Start and Oven Start; other languages use their localized Start
label. Key and placeholder checks are automated, not native-speaker review.

At 11:04 America/Chicago on October 15, the owner reported that installed build
`4cf9fffbcf2f9d9964aa56cbe8bd3e42c70b9ab0` appeared to contain the expected
controls and sensors, with testing still pending. At 11:15 the owner confirmed
testing all controls and sensors and reported they functioned as expected.
This is acceptance of that installed build, without a new per-control test log;
it does not verify the subsequent generalization or the shorter Oven label.
The previous two-device build's hardware acceptance remains documented in
[legacy-oven-controls.md](investigations/legacy-oven-controls.md). Software
tests here send no commands to an appliance.

### Food probe hardware check

On October 15, the owner connected the probe and the existing sensor appeared.
The 11:34 and 11:43 America/Chicago captures report `meatprobe_connected` with
temperature item `id: "1"` at zero for both current and desired temperature,
in Fahrenheit, while the oven reports Ready / NoOperation. The second capture
still reports those values with Bake 350°F and a probe target of 150°F selected
on the panel before Start; the oven target also remains zero in that capture.

The 11:46 capture reports Bake / Run / Preheat, cavity target 350°F, and item
`id: "1"` with desired 150°F. Its cached transport body contains current 100°F;
the current resource snapshot contains 107°F. This confirms the existing probe
temperature and target mapping on the NV51K7770S during probe cooking, including
the `meatprobe_connected` spelling. The divider reports Off throughout.

Both sensors remain read-only. These observations do not verify remote target
adjustment, probe-specific remote starts, measurement accuracy, or reporting
below 100°F. The capture verification itself changed no integration code and
does not establish hardware acceptance of the later generalized device layout.

The subsequent sensor update adds a discovered probe sensor when its connection
token and reading first become available during a normal coordinator update. No
integration reload is needed. After creation, unplugging makes the sensors
unavailable; reconnecting reuses the same entity IDs and custom names. Sensors
that have not been detected remain absent. The same inclusion checks support
legacy HTTPS, OCF vendor temperature arrays and the OCF probe fallback resources.
Idle zero handling is unchanged when the probe remains connected.

Regression tests start with the probe disconnected, connect it after HA setup,
then exercise repeated unplug/reconnect updates and platform unload. They also
cover a probe already connected at startup and identity retention after an
unplugged restart. All 3,285 tests pass (67.44 seconds), along with formatting,
lint and type checks. No probe writes are introduced. At 12:14 America/Chicago
on October 15, the owner confirmed that unplugging the probe makes its sensor
unavailable. At 12:26 the owner confirmed that reconnecting restores availability
after the next poll without reloading LocalThings. Both lifecycle transitions
are hardware-confirmed on build `34de099`; the earlier capture verified the
readings. The later label update has not yet been confirmed installed.

English labels are Temperature Probe and Temperature Probe Target. The
NV51K7770S main probe uses these appliance-level names without an Oven/Upper
prefix; other paired oven families retain their cavity labels. The internal
keys `food_probe_temp` and `food_probe_setpoint`, existing entity IDs and custom
names are unchanged. All ten catalogs use the corresponding localized labels.
The label update passes all 3,295 tests (63.08 seconds), formatting, lint and
type checks. Ten locale-specific upgrade cases preserve pre-existing probe IDs.

## NV51K7770S feature audit

Compared with the submitted fixes commit `7122a33`, the merger preserves the
capability definitions, cooking mode mappings, pending settings, time limits,
write verification and protocol implementation. The migration test compares
the complete registered entity sets in both divider positions: only Upper
Divider and Lower Door, Reservoir, Power and Smart Control are removed, with
one retained copy of each. Cavity entity IDs and command destinations remain
unchanged. The lower device target and removed duplicate IDs need the migration
adjustments described above.

The audit found no previously implemented oven function omitted by the merger.
The audit also checked the conditional probe sensors and fields in the captures
that neither version exposes as standalone entities:

| Captured field or capability | Evidence and remaining limit |
| --- | --- |
| Food probe | Existing conditional temperature/target sensors are verified during Bake with the probe connected; see the hardware check above. Sensors appear on detection without a reload, become unavailable on unplugging, and reuse their identities on reconnecting. Remote target control remains unverified. |
| Steam/descale information | Options include `descaleAlarm_Normal`, `steamState_Standby`, `steamGeneratorLevel_Empty`, `descaleState_Standby`, `drainRequired_00` and related tokens. These are candidates for read-only diagnostic entities; the current captures do not demonstrate state transitions or establish all token meanings. |
| Sabbath and temperature adjustment | `Sabbath_Off` and `AdjustingTemp_0` are present. No working remote setting or hardware-verified sensor mapping was established. |
| Steam level and preheat options | `steamAddLevel_NULL` and `NoPreheat_Off` are present. `NULL` is not a measured steam setting, and these names alone do not establish writable controls. |

Light, panel volume and the separate kitchen timer remain without a verified
local entity source in the investigation captures. Cleaning and Gourmet Cook
were explicitly excluded; Broil is excluded under the existing remote-start
policy. The ineffective Start Diagnosis button was already withdrawn before
the merger. Alarm Code and Diagnosis Status remain available. None of these
is a function removed by combining the devices, and no speculative writes are
introduced by this audit.
