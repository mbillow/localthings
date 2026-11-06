# Samsung TV: initial settings support and hardware findings

This is an initial, settings-only contribution to [#388](https://github.com/mbillow/localthings/issues/388),
verified against one QE50QN90AATXXU (QN90A). It does not establish support for
every Samsung TV or for other brands.

## What this change provides

The TV routes from `/oic/d`'s `oic.d.tv`, using the per-href reading already
implemented for AV boards. Three configuration selects use the TV's own
`x.com.samsung.tv.supportedModes` list:

- Input, at `/sec/tv/inputsource`.
- Picture mode, at `/sec/tv/mode/picture`.
- Sound mode, at `/sec/tv/mode/sound`.

The labels come from the device, while writes send its raw
`x.com.samsung.tv.id` as `x.com.samsung.tv.currentMode`. Two inputs called
`PC` remain individually addressable as `PC (HDMI1)` and `PC (HDMI4)`.
Lists are not hardcoded to the captured model. A missing mode list or a
resource without the writable `oic.if.a` interface creates no control.

Other useful TV resources remain unbound and can still trigger the coverage
repair. This is intentional: this change neither claims full coverage nor
hides unsupported functionality. A media player, remote, app launching,
Wake-on-LAN and external-speaker routing are not implemented by this PR.
The shared media player is being reviewed separately in
[#719](https://github.com/mbillow/localthings/pull/719).

## Setup with an existing OCF credential

1. Turn the TV on. The setup host must be able to reach it on the local network;
   standby may answer discovery without serving enough authenticated resources.
2. Add **LocalThings** in Home Assistant and enter the TV's address.
3. If automatic certificate authentication is refused, use the existing
   owner/peer PSK import flow. Follow [Credential acquisition](credential-acquisition.md#importing-a-credential)
   for the distinction between identities and key formats. Import a credential
   for this TV, not a SmartThings cloud access token or a Samsung remote token.
4. Confirm that setup authenticates and resolves the TV's own OCF identity.
   The controls depend on the mode resources actually returned by that TV.
5. Check each select's options. For a reversible first test, note the current
   value, choose another device-advertised value, confirm it on the TV, then
   restore the initial value.

This does not acquire an OCF key. The research setup recovered a credential
from the owner's rooted Android SmartThings installation; there is not yet a
portable, published TV recovery procedure here. Do not apply a washer's
removal/re-registration procedure to a TV on the strength of this report.
Ownership transfer and `/oic/sec/*` writes are outside this contribution.

## Command success is not just CoAP success

The QN90A can return CoAP `2.04` with `controlResponse.result=false`.
An audio write, for example, returned `failed to load current volume` while
audio was routed externally. A repeated sound-mode selection could also be
refused even though that mode was already active.

The coordinator now reports an explicit `result=false` as a refusal. A select
opts into the existing live-confirmation mechanism: a refusal is accepted
only if a fresh authenticated GET contains the requested field and value.
An optimistic cache entry, a timeout or a different live value is not success.
An explicit refusal is not resent.

## Findings for the media-player and remote follow-ups

The following checks were performed on the same hardware using a local
extension. They are evidence for follow-up work, not features of this PR:

| Function | Observed result | Verification |
| --- | --- | --- |
| Input selection | Passed, including the two PC inputs | Independent authenticated GET after HA action; original restored |
| Picture/sound mode | Passed | Independent authenticated GET after HA action; originals restored |
| Native browser launch | Passed | Content resource reported Browser and `org.tizen.browser` |
| Standby/wake | Passed | Standby lost the LAN API; wake was followed by fresh OCF and LAN power reads |
| WSS channel up/down | Passed | Authenticated channel resource changed and returned to its initial value |
| Native-video pause/resume | Passed in the video tested | Viewer confirmed both effects; app unspecified |
| Remote mute and volume keys | Passed with external audio | Fresh authenticated soundbar audio reads, not the TV's stale audio state |
| Absolute TV mute with external audio | Failed | TV could refuse the write or report stale mute |
| Explicit external-speaker mute route | Passed in the extension | Mute, mute, unmute, unmute matched fresh speaker GETs and HA state |

### Two separate credentials

OCF uses its PSK identity/key. The working remote transport instead used a
TV-approved token for
`wss://<TV>:8002/api/v2/channels/samsung.remote.control`. A valid connection
reported `ms.channel.connect`. These credentials are not interchangeable.

On this firmware `/sec/tv/remotecontrol` acknowledged keys without changing
the channel or mute state. Press/release variations did not fix it. A token
obtained from the OCF `/sec/tv/appdata/remote-server` exchange also did not
authenticate the tested remote channel. Do not offer remote features based
on those acknowledgements alone.

A reusable remote-setup flow should keep the connection open while the owner
approves access on the TV, accept a token only from `ms.channel.connect`,
and store it privately. Timeout and denial must remain failed setup, not an
optimistic success. The research extension used a manually prepared token
file; that private-file convention is not a public integration API and is
deliberately not copied into this PR. Token-bearing URLs must not reach
logs, exception text or diagnostics.

### External audio

The TV's `/sec/tv/audio` could remain unchanged while the connected
soundbar's real mute or volume changed. Do not derive an absolute mute
operation from a blind key toggle or use the TV's stale state to decide
whether to toggle. The tested extension used an explicit, optional speaker
entity, read that entity's mute status and forwarded absolute mute/unmute
requests to it. Speaker availability and mute support must be checked;
failure must not silently fall back to a toggle.

### Playback and wake limits

`contentinfo` did not provide a reliable playing/paused field. A successful
key send cannot establish playback telemetry. A separate DLNA test fixture
advertised Stop but not Pause and rejected Pause; that is not evidence that
native-video pause is broken or that every app supports it.

Wake used the TV's reported network MAC followed by actual power read-back.
A sent magic packet alone is not a successful wake, and a network timeout
alone is not proof that the TV is off.

## Capture and regression coverage

`tests/fixtures/tv_qn90a_device.json` contains only the three authenticated
mode GETs. Its `device0` list is a synthetic wrapper for the existing fixture
loader; this TV does not provide that Collection. It is not a full resource
dump. Identity, network, account, content and security resources are absent.

Tests cover routing, the selected capture's coverage and golden keys,
device-advertised write values, duplicate display names, malformed or missing
option lists, read-only resources, option updates and refused-command live
confirmation. Edge cases constructed for tests are separate from the capture.

For another model, a useful report names the model/firmware, which of these
resources it actually returns, its advertised modes, the exact operation
tested, the independent observation and whether the original state was
restored. Keep failed, untested and confirmed outcomes distinct. Inspect any
diagnostics before posting, especially custom input names, network identifiers
and embedded JSON; never post keys, remote tokens or raw SmartThings databases.
