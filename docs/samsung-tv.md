# Samsung TV

A Samsung TV reporting `oic.d.tv` uses the Tizen capability profile shared
with Smart Monitors, plus its own entities:

- **Media player** on the shared AV platform: power-off, volume, mute, input
  and sound mode, each offered only while the TV reports it writable. Channel,
  programme and position come from the broadcast schedule on live TV; title and
  artwork from the content info and thumbnail otherwise. `play_media` tunes a
  channel, launches an app, or casts an image, video, HLS stream or YouTube link.
- **Turn-on** by Wake-on-LAN, offered once the entry knows the TV's MAC. The OCF
  endpoint is gone in standby, so no OCF power-on write is sent. Wi-Fi wake
  needs the TV's "Power On with Mobile" setting.
- **Remote** (`remote.send_command` with `KEY_*` names or aliases), **notify**
  (an on-screen pop-up), an **Open app** select and a **screen off** button.
- The `search`, `open_settings`, `set_sleep_timer` and `forget_known_apps`
  actions.
- A **picture mode** select.
- Optional low-latency **camera casting**, off by default (below).

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
