# Samsung TV

A Samsung TV reporting `oic.d.tv` uses the Tizen capability profile shared
with Smart Monitors, plus its own entities:

- **Media player** on the shared AV platform: power-off, volume, mute, input
  and sound mode, each offered only while the TV reports it writable. Channel,
  programme and position come from the broadcast schedule on live TV; title and
  artwork from the content info and thumbnail otherwise. `play_media` tunes a
  channel, launches an app, or casts an image, video, HLS stream or YouTube link.
- **Turn-on** by Wake-on-LAN, offered once the entry knows the TV's MAC. The OCF
  endpoint is gone in standby, except during the TV's short wakes from network
  standby, when the power switch is written instead (see below). Wi-Fi wake
  needs the TV's "Power On with Mobile" setting.
- **Remote** (`remote.send_command` with `KEY_*` names or aliases; while the
  TV is off (in standby, unreachable, or two polls failed) a key fails at once
  with "The TV is off", unless a turn-on is under way, which it waits for, up
  to 30 s), **notify**
  (an on-screen pop-up), an **Open app** select and a **screen off** button.
- The `search`, `open_settings`, `set_sleep_timer` and `forget_known_apps`
  actions.
- A **picture mode** select.
- Optional low-latency **camera casting**, off by default (below).

Remote keys, volume, mute, input and picture mode go out at once, alongside
any read of the TV's state in progress, and the call returns with the TV's
answer; the entity shows the new value at once and a later read confirms it.
Other commands wait for the read to finish. Measured on a QE85QN95A with keys,
mute, input and picture mode (volume is the same request as mute): these
writes answered and took effect as often as with no reads running, and no read
failed or changed.

Source and sound-mode names come from the TV; duplicate input names are
disambiguated by their wire IDs. Options update as the device changes them.
The TV can rename an input after the device on it (`AVR-X1800H`, then
`HDMI 3`), so a script can select it by its wire ID instead (`source: HDMI3`),
which stays the same.

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

## Off, standby and turning on

- A TV that is off shows `off` on its media player and remote (`unavailable`
  when no MAC is known to wake it); its other entities are unavailable.
  Switching it off logs no error.
- In standby the TV wakes for a moment every few minutes and answers with its
  power switch off (measured on a QE85QN95A: 6 to 20 s every 5 minutes).
  That answer reads as off, so those wakes don't bring its other entities
  back with stale values. Any TV whose switch answers off is shown off; Art
  Mode on The Frame and Ambient Mode have not been measured.
- After a Wake-on-LAN turn-on, LocalThings cancels a connection attempt still
  waiting on the TV that was off and looks for the TV again. The measured TV
  was on the network 0.5 to 1.3 s after the packet.
- A TV awake in network standby ignores the magic packet (measured twice on a
  QE85QN95A) but turns on when its power switch is written. A turn-on that
  finds the TV there writes the switch, once per standby wake; if the TV drops
  back to standby first, the packet is sent again.
- A TV turned on with its own remote or an app announces itself over SSDP.
  With Home Assistant's SSDP integration loaded (part of the default
  configuration), that announcement makes LocalThings look at once; without
  it, the next poll finds the TV, within about 30 s.
- Once Home Assistant's SSDP integration has reported the TV (a device it
  already knew counts), a TV that is off is polled less often: every 30 s at
  first, doubling to every 2 minutes. Its next power-on announcement finds it
  at once; a TV that doesn't answer yet when it announces itself is polled
  again within 30 s.
  When a poll finds the TV on after at least two polls without an answer, and
  no announcement came within a minute either side of it, LocalThings takes the
  announcement as lost on the way (a switch filtering multicast, a Wi-Fi drop)
  and polls the TV every 30 s while it is off until the integration reloads.
  A TV that refused the connection in between was on, so that case doesn't
  count. A TV that answers but refuses the connection is still polled every
  30 s. Other devices keep the 30 s poll.
- Discovery doesn't read three resources the QE85QN95A lists but doesn't
  answer while on (`/sec/tv/remotecontrol`, `/sec/tv/launchnativeapp`,
  `/autoreconnect/ap`), each of which would cost a read timeout.
- Adding a TV whose power switch answers off (a standby wake) fails with "The
  TV is in standby"; turn it on and try again.

## Camera casting

`media_player.play_media` with a `media-source://camera/...` id casts a camera
to the TV. How it gets there depends on the TV's **Camera casting** option
(**Settings > Devices & services > LocalThings > the TV > Configure > Camera
casting**):

| | Off (default) | On |
| --- | --- | --- |
| Stream | Home Assistant's own camera HLS | LocalThings runs ffmpeg and serves live MPEG-DASH |
| Delay behind live | about 20 s | about 4.5 s |
| On the TV | the renderer's prompt, accepted with the TV's remote | Gallery opens directly, no prompt |
| Added to Home Assistant | nothing | two HTTP endpoints and an ffmpeg process per camera being cast |

Turn it on if you cast cameras to the TV, a doorbell for instance, and the
delay matters. Nothing else on the TV uses it, and links, files and YouTube
cast the same way either way. Measurements are in
[casting latency](investigations/tv-casting-latency.md).

It needs:

- ffmpeg, which Home Assistant OS and the container image already have;
- a camera with Home Assistant's stream feature (RTSP and the like). An
  MJPEG-only camera is shown as a snapshot either way;
- a Home Assistant URL the TV can reach over plain LAN access
  (**Settings > System > Network**). An `https` URL needs a certificate the TV
  trusts.

It's off by default because of what it adds to Home Assistant. The endpoints
are `/api/localthings/camera_dash/<token>/...` and
`/api/localthings/camera_stream/<camera>`. The TV can't log in, so the DASH
endpoint takes no Home Assistant login: a random 32-character token in the
path is the credential, and it stops working 30 s after the TV stops fetching
from it. The MPEG-TS endpoint, the fallback when the cast asks for the TV's
prompt (`extra: {ask: true}`) or DASH can't start, takes a signed URL like
Home Assistant's own media. Both endpoints answer only while at least one TV has the option on.
Home Assistant can't remove a route once it's added, so after you turn the
option off the endpoints stay registered but refuse every request. Turning it
off also ends any camera this TV is casting.

## Scope and evidence

The fixture is the full 49-resource cached diagnostics snapshot from a
QE50QN90AATXXU on 2026-11-12, with account, network, identity and content values
redacted for publication. Its `device0` list is only the fixture loader's wrapper
for flat per-href resources; the TV does not serve a `/device/0` Collection.
Resources absent from the cache or timed out are not fabricated. The capture's
Game HDR current mode is not advertised as writable, so it is not a new option.

WSS remote tokens and external-speaker mute routing are not included. With
external audio, the TV can report stale audio state: target the speaker entity
directly rather than infer an external speaker toggle from the TV.
[Local-extension investigation notes](investigations/qn90a-local-controls.md)
record the QN90A hardware findings; [casting latency](investigations/tv-casting-latency.md)
records the renderer and Gallery measurements from a UE50TU7172.

TV writes settle for six seconds, as on the soundbar. Native mode writes returning an explicit refusal are accepted as an already-current
no-op only after the coordinator confirms the exact value with a fresh device GET.
