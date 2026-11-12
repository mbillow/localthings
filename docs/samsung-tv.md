# Samsung TV: OCF media player and settings

A Samsung TV reporting `oic.d.tv` now uses the existing Tizen TV capability
profile shared with Smart Monitors. The shared media-player entity provides
power-off, volume, absolute mute, input and sound mode when the device reports
those controls as writable. Picture mode remains a separate settings select.
Source and sound-mode names come from the TV; duplicate input names are
disambiguated by their wire IDs. Options update as the device changes them.

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
Standby wake, WSS remote, app launching, casting and external-speaker mute
routing are not part of this slice. The TV cannot wake over its offline OCF
endpoint, so TURN_ON is not advertised and a direct turn-on call is rejected.
Playing/paused states and playback buttons are not invented from input state.
With external audio, the TV can report stale audio state: target the speaker
entity directly rather than infer an external speaker toggle from the TV.
[Local-extension investigation notes](investigations/qn90a-local-controls.md)
record the hardware findings for those follow-ups; they are not shipped features.

The shared profile retains the default write-settle window for input switching.
Native mode writes returning an explicit refusal are accepted as an already-current
no-op only after the coordinator confirms the exact value with a fresh device GET.
