# One Home Assistant device for the Flex Duo oven

The `feature/legacy-oven-single-device` branch builds on the accepted fixes in
PR #637. It automatically presents the confirmed `LCD_OV_WALL_16K` and
`LCD_OV_WALL_16K_DIV` pair as one physical oven. There is no layout option.
Other oven pairs and other appliance families keep their existing layout.

## Controls and sensors

With the divider inserted, each cavity has its own Cooking Mode, Setpoint,
Cook Time Hours, Cook Time Minutes, Start and Stop. Names begin with Upper or
Lower. Each cavity also retains its own temperature, running and cooking state,
progress, operation time, estimated finish, alarm code and diagnosis status.

When the divider is removed, the main cavity's labels change from Upper to
Whole Oven. Lower cooking entities remain registered but unavailable, then
become available again after the divider is reinserted. Home Assistant's device
page may still show those unavailable entities. Entity IDs do not change with
the divider, and user-customized names take precedence over generated labels.

Door, Divider, Reservoir, Power and Smart Control appear once. Child Lock,
Energy Saving and connection diagnostics also remain unprefixed shared entities.
The owner confirmed on October 15, 2026 that Power is shared and Smart Control
is a single physical dial. The shared displays use the primary record;
Start and Stop retain their existing cavity-specific command and permission
checks. Raw readings and downloaded diagnostics retain both protocol records.

The duration controls set cooking time. They do not expose the oven's separate
kitchen timer. This feature adds no light, volume, Gourmet Cook or cleaning
controls.

## Upgrade and fallback

The existing main HA device is retained. Lower entities move onto it without
changing their unique IDs, entity IDs or custom names. The emptied Lower device
is removed. Existing user-assigned device names are preserved; an old name such
as “FlexDuo All / Upper” can be renamed to “FlexDuo” in HA.

Only the redundant copies of shared entities are removed:

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
appliance without the confirmed combined pair is rejected rather than routed
to a different device. The separate Start buttons already know their cavity.

Direct resource actions target the primary protocol view of the combined
device. To inspect Lower resources, use their actual `/1` hrefs, such as
`/mode/vs/1`. This does not change the protocol's two-record layout.

## Verification

The October 15 test build passes all 3,259 tests on Python 3.14.8 and HA
2026.9.4, plus `ruff format --check`, `ruff check` and `ty check`.

`tests/localthings/test_combined_oven.py` exercises actual HA platform setup,
registry migration, stable IDs and custom names, divider changes without a
reload, offline snapshot setup, and restoring the old layout. Start-action
tests verify the exact Upper and Lower request paths and separate settings.
Unverified model pairs and entities owned by another entry are protected.

The suite loads entity names in all ten shipped locales and checks that mode
state translations match the existing catalog. English buttons use Upper Start,
Lower Start and Whole Oven Start; other languages use their localized Start
label. Key and placeholder checks are automated, not native-speaker review.

This branch has not yet been installed on the owner's oven. The previous
build's hardware acceptance remains documented in
[legacy-oven-controls.md](investigations/legacy-oven-controls.md). Software
tests here send no commands to an appliance.
