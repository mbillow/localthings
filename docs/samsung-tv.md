# Samsung TV: initial picture-mode settings

A Samsung TV reporting `oic.d.tv` now uses the existing Tizen TV capability
profile shared with Smart Monitors. This first slice provides **picture mode**
when the TV advertises a writable mode list. Input and sound mode are bound
without standalone selects, reserved for the shared media player.

## Setup

An existing OCF credential for the TV is required. Most owners cannot acquire
one yet; LocalThings does not recover it automatically. A SmartThings cloud
access token or TV remote token is not an OCF credential.

1. Turn the TV on and make it reachable from Home Assistant on the LAN.
2. Add LocalThings and enter its address.
3. Import its owner/peer PSK using [the credential import flow](credential-acquisition.md#importing-a-credential).
4. Let setup authenticate the device and read its resources. Never delete its
   SmartThings registration to repair LocalThings; that can reset its networking.

Picture-mode labels and writable IDs come from the TV, not a model-specific
list. A temporarily absent list can appear later without re-adding the device.
A CoAP success carrying `controlResponse.result=false` is still a refusal.
This picture select does not opt into confirmation-on-refusal: that hardware
finding concerned sound mode, not picture mode.

## Scope and evidence

The fixture is the full 49-resource cached diagnostics snapshot from a
QE50QN90AATXXU on 2026-11-12, with account, network, identity and content values
redacted for publication. Its `device0` list is only the fixture loader's wrapper
for flat per-href resources; the TV does not serve a `/device/0` Collection.
Resources absent from the cache or timed out are not fabricated. The capture's
Game HDR current mode is not advertised as writable, so it is not a new option.

Known AV plumbing reuses the shared AV/TV coverage. Useful unsupported remote,
native-app, ambient-mode and weather resources remain visible as coverage gaps.
A media player, standby wake, WSS remote, app launching, casting and
external-speaker mute routing are not part of this slice.
[Local-extension investigation notes](investigations/qn90a-local-controls.md)
record the hardware findings for those follow-ups; they are not shipped features.

The shared profile retains the default write-settle window. A six-second HDMI
settle claim is unnecessary here: this PR does not expose input switching.
