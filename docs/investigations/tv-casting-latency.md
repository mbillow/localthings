# TV casting: direct launches, and live cameras at ~4.5 s instead of ~20 s

Measured on a UE50TU7172 (2020, Tizen 5.5); firmware findings are from the
`T-PTMDEUC` image (`libmde-service-tvcontrol.so`, `libmde-protocol-stthing.so`,
the .NET apps under `/usr/apps`, `/hal/etc/multimedia/mmfw_player.ini`).

Short version: images, video files, cameras and YouTube now start without the
TV's "Shared a new video with you" prompt. A continuous live stream (MPEG-TS,
HLS) plays about **20 s behind** real time in Gallery, whatever is done to
it. The same camera served as **live MPEG-DASH plays ~4.5 s behind**, once the
playlist starts the player 3 s behind the live edge. Cameras are cast that
way (`camera_dash.py`).

## Launch paths

| Path | Resource and body | Prompt | Used for |
|---|---|---|---|
| Renderer | `/content/renderer`, `{action: play, contents: [{host, path, mimetype}], provider.user}` | yes | HLS; anything with `extra.ask: true`; TVs without the launches below |
| Gallery | `/sec/tv/launchnativeapp`, `targetAppId: com.samsung.tv.gallery`, `bundleParam: [{key: smart_thing_uri_<image\|video>0, value: <url>}]` | no | images, video files, cameras |
| YouTube | `/sec/tv/launchapp`, `targetAppId: "111299001912"`, `targetAppParam: "#play?v=<id>"` | no | YouTube |

Both direct launches are what "Watch now" on the prompt does internally:

- `ContentRendererProperty::playContent` calls
  `AppControlUtil::LaunchApp(com.samsung.tv.gallery, {smart_thing_uri_<kind><n>: uri})`
  for images and video. For `video/youtube` it calls
  `was_force_launch_webapp("111299001912", uri)`.
- The stthing protocol layer (`_CreatePlayingInfoListJson`) rewrites a YouTube
  item's path into `#play?v=<id>` before that call.
- `LaunchAppProperty::launchApp` passes `targetAppParam` to
  `AppControlUtil::LaunchWebApp(id, param)`, which is the same
  `was_force_launch_webapp` call.

Both resources are listed in `/oic/res` but never answer a read, so the
integration checks the listing (`AVControl.can_launch_gallery` /
`can_launch_youtube` in `av.py`) and falls back to the renderer when a TV lacks them.

The prompt's title is `provider.user.userName` (else `deviceName`, else
"SmartThings"), and its icon is downloaded from `iconUrl`. It ignores keys sent
through `/sec/tv/remotecontrol`, so only the physical remote confirms it.
Auto-confirming it is therefore impossible, and was removed.

## What plays

| Content | Result |
|---|---|
| H.264 up to 1080p, HEVC | plays |
| MP4 (index at either end, Range used), MKV, WebM, MPEG-TS | plays |
| Endless chunked MPEG-TS or fMP4 (a live remux) | plays, ~20 s behind; the camera path with `extra.ask` or without the Gallery launch |
| HLS through the renderer | plays. The mimetype must be `video/*`: HA's fMP4 camera HLS played as `video/mp4`, a live playlist of `.ts` segments only as `video/mpeg` |
| HLS through the Gallery launch | refused ("not supported") |
| Live MPEG-DASH (`.mpd`) through the Gallery launch | plays, ~4.5 s behind live (started 3 s behind the edge); the camera path |
| `rtsp://` through the Gallery launch | never loads (60 s) |
| MJPEG | not supported |

## Camera latency

Method: `_videolab/lab.py` (a local test server, not committed) serves a live
stream whose picture is the serving laptop's wall clock, rendered by ffmpeg.
The lag is the TV's clock minus a phone's. The laptop's clock matched Home
Assistant's to within a second. Every variant was launched through Gallery
unless noted.

| Variant | Lag |
|---|---|
| Doorbell sub stream, ffmpeg remux to chunked MPEG-TS | ~18 s |
| Same, padded to a fixed mux rate with null packets | ~18 s |
| Clock at 1 Mbit/s, keyframe every 1 s | 23 s |
| Clock at 8 Mbit/s, keyframe every 1 s | 23 s |
| Clock, served as a 50 GB "file" (Content-Length, 206 to a Range) | same |
| Same, end-of-file probes answered with timestamps implying a 1 s or a 1 h duration | 22 s |
| Clock as live HLS, 1 s segments, through the renderer (`video/mpeg`) | same |
| Relative seek +15 s (`/capability/mediaPlayback/main/0`, `modes: [seek]`) after playback starts | no effect |
| `fastForward` (`/capability/mediaTrackControl/main/0`) after playback starts | no effect |

What the server log and the screen show:

- The TV makes one request and reads it at exactly real time (1.08 Mbit/s for
  79 s, 8.4 Mbit/s for 38 s). Nothing upstream holds the stream back.
- Gallery opens after ~6 s and shows the first frame, which is close to live.
  It then buffers until about 20 s of media are queued and plays from that
  first frame onwards.
- The buffer is a fixed amount of time, not bytes: 1 and 8 Mbit/s lag the same.
- With a fake length the TV treats the stream as a file. It probes the middle
  and the end (`Range: bytes=5000000624-`, `bytes=49999750000-`), then reads on
  from 96 KiB. The duration it derives makes no difference.
- The low-latency ffmpeg flags (`-fflags nobuffer`, `-flags low_delay`, short
  probe, `-flush_packets 1`, `-muxdelay 0`) only cut the time to the first
  byte, from ~6 s to 2–4 s. They don't affect the lag.

The system player's config (`mmfw_player.ini`) doesn't account for 20 s:

```
[http streaming]
http max ring buffer size = 5242880 ; bytes (5MB)
http buffering limit = 99.0 ; percent
http buffering time = 1.2
[rtsp streaming]
rtsp buffering time = 5000; msec
rtsp rebuffering time = 15000; msec
[dlna]
dlna buffering time = 4
```

The deep buffer is most likely Gallery's own setting for streams. Gallery is
not in the firmware (it is installed from the store), so it could not be
checked.

## The TV's own low-latency camera paths, and why they are out of reach

The firmware has a dedicated camera player, `libmmfiotplayertv` with RTSP and
WebRTC plugins. `mmfw_iot_player_tv.ini` sets `jitter buffer latency=10` and
`video pipeline latency = 100` (ms). It is reachable only from apps on the TV:

- **Notification video.** `com.samsung.tv.notification-app` plays a
  notification's extra-data bundle `video_info` with key `uri`. A URI starting
  with `rtsp` gets the IoT player; anything else gets the normal media player.
  But `com.samsung.tv.notification-serviceapp`, which posts the OCF/`ocf_noti`
  notifications this integration sends, only ever sets `vd_details`, never
  `video_info`. No app in the firmware posts `video_info`.
- **Multi View camera.** Choosing a SmartThings camera or doorbell on the home
  screen (`CompSmartThingsPreview::LaunchSTApp`) launches
  `com.samsung.tv.multiscreen` with `{mode: camera-full, id: <SmartThings
  device id>, name}`. Multi View then gets the stream from Samsung's cloud
  (`api.st-av.net/v1/stream`), so it only serves cameras registered in
  SmartThings and takes no URL. The app also exits when the model's
  `multiview.support` feature flag is off ("Multiview support is false"),
  which is likely on a TU7 series set.

## Live DASH: ~4.5 s

A dynamic MPD launched through Gallery (`smart_thing_uri_video0` = the
`.mpd` URL) goes to the TV's own DASH engine (`SamsungDASH/2.0`,
`libdash.so`). Measured with the same wall-clock picture:

| Playlist | Lag |
|---|---|
| ffmpeg live DASH, 1 s segments, fixed `SegmentTemplate` numbering | ~7 s |
| Same, with DVB low-latency markers, `ServiceDescription`/`Latency` target 1.5 s | ~7 s |
| Same, 0.5 s segments and/or `minBufferTime` 0.5 s | ~9 s (no gain) |
| Same, with `ProducerReferenceTime` (in the MPD and as `prft` boxes) and `urn:dvb:dash:lowlatency:critical:2019` | ~6 s, steady |
| `SegmentTimeline`, 3 s keyframe interval (a camera-like stream), playlist re-fetched every 3 s | plays fine |
| `SegmentTimeline`, 1 s segments, AAC audio track, no markers, `suggestedPresentationDelay` 3 s | **~4.5 s, plays straight through** |
| Same, `suggestedPresentationDelay` 2 s | stalls and rebuffers |

From the server log:

- The TV fetches the playlist, the `UTCTiming` URL and the init segment, then
  starts at the **newest finished segment**. It then asks for each next
  segment a moment before ffmpeg has finished it, gets a 404 and retries at
  once. It sits at the live edge.
- With ffmpeg's `suggestedPresentationDelay` of 1 s the player starts at the
  newest segment and plays the first second almost live (visible with a
  beeping audio track). It then runs dry waiting for the next segment and
  rebuffers ~6 s deep: that stall is where the 7 s came from. Started 3 s
  behind the live edge it never runs dry and stays ~4.5 s behind; 2 s isn't
  enough headroom. Catch-up (`PlaybackRate`) never engaged.

Firmware notes on catch-up (`CDashDataHandler::CheckAndSetUpLowLatency`):

- It is considered for a live stream when `CDashStream::isLowLatency()`
  (`urn:dvb:dash:lowlatency:critical:2019`, the scope marker
  `urn:dvb:dash:lowlatency:scope:2019`, or a segment-duration check) or the
  engine setting `streaming.lowLatencyEnabled` (false in
  `/etc/multimedia/dash_default_settings.json`) says so.
- It then needs a representation `ProducerReferenceTime` ("current
  representation not a valid LL stream, turn off catch up" otherwise). The
  representation parser doesn't read the element, the adaptation-set one
  does; inband `prft` boxes go through `CDashMp4Parser::_ProcessPrftBox`.
- With all of that in place the lag still didn't fall below ~6 s. Samsung's
  developer spec lists `ServiceDescription`/`Latency` from Tizen 9.0 and
  `ProducerReferenceTime` from Tizen 10.0; this TV is Tizen 5.5.

What the integration does (`camera_dash.py`):

- ffmpeg copies the camera's video (no re-encoding) and turns the first audio
  track into AAC. It writes 1 s segments (one per keyframe interval if that
  is longer), a `SegmentTimeline` playlist, and `prft` boxes, into a
  temporary folder.
- Each cast gets a random token in the path
  (`/api/localthings/camera_dash/<token>/manifest.mpd`); the view needs no
  other auth, because the TV fetches segments by relative address and can't
  sign them. Home Assistant's own HLS works the same way.
- The playlist is served with a `UTCTiming` pointing at the session's clock,
  and `suggestedPresentationDelay` set to 3 s (or two of the longest
  segments, for cameras with a longer keyframe interval).
- The address goes to the TV unsigned (the token is the credential).
- Every Representation gets a `bandwidth`: ffmpeg leaves it out when the
  camera states no bitrate, and the TV's DASH engine then rejects the
  playlist ("not found necessary attribute @bandwidth in mpd" in the firmware;
  the cameras whose playlists lacked it were the ones that didn't play).
- ffmpeg keeps its default probe here (5 s / 5 MB), not the MPEG-TS
  stream's short one: with that, a camera that sends its codec parameters
  only in-band got `codecs=""` and an init segment the TV couldn't play.
- MPEG-TS and HLS sources get `-tag:v avc1`: their stream tag is refused by
  the MP4 muxer otherwise (`-tag:v 0` doesn't clear it on ffmpeg 4.4 or
  6.1). RTSP carries none. Another codec makes ffmpeg exit naming the tag it
  wants (`hev1` for HEVC), and the session restarts once with that tag.
- Cameras without Home Assistant's stream feature (MJPEG) get no DASH: their
  "stream source" is the MJPEG URL, so the TV shows the snapshot instead.
- The first playlist waits until every track (video and audio) lists a
  segment: audio comes from the first second, video only from the camera's
  first keyframe, and a playlist without video yet made the TV give up
  (Synology Surveillance Station stream).
- Gallery waits only a few seconds for that first playlist, less than a
  camera with a long keyframe interval needs (Lechange: the TV hung up, HA
  cancelled the request, the TV showed "not supported"). When the TV hangs
  up before the playlist is complete, the integration opens it on the TV
  again once it is, once per session; the fast cameras are unaffected.
- Each track is shifted to start at zero (`setts` on the copied video,
  `asetpts` on the audio). A Reolink main stream came through with video
  timestamps 12.4 hours from zero and audio overflowing into negative
  segment durations; the TV kept re-fetching the playlist and never asked
  for a segment it thought lay hours ahead. Stamping packets by arrival time
  (`-use_wallclock_as_timestamps`) also fixes that, but makes segment lengths
  ragged.
- If the TV ever answers "not supported" without fetching anything, check
  Home Assistant's IP bans (`ip_bans.yaml`): a banned TV gets 403 for every
  request, before any integration code runs (seen here; it looked exactly
  like a playlist the TV refused).
- The TV's first playlist request waits until ffmpeg has written segments, so
  ffmpeg's start overlaps Gallery's.
- The session ends when the TV hasn't asked for anything for 30 s, when
  ffmpeg exits or stops writing (a stalled camera), when the last TV casts
  something else, or when Home Assistant stops; its folder is deleted.
- If DASH can't start (no ffmpeg), the cast falls back to Home Assistant's
  own camera stream (HLS through the prompt), not to the MPEG-TS stream,
  which needs the same ffmpeg.
- The DVB markers are left out: they made no measurable difference, and an
  unknown "essential" property could make other models reject the playlist.
- With `extra.ask: true`, or on a TV without the Gallery launch, a camera
  uses the continuous MPEG-TS stream through the prompted cast, the tested
  combination there.

The lag grows with the camera's keyframe interval, since a segment can't be
shorter than it. A keyframe every second (Reolink: "I-frame interval 1x")
keeps segments at 1 s.

## Options left

- **Accept ~4.5 s** with live DASH. `extra.ask: true` keeps the prompt for
  "someone is at the door -- show the camera?".
- **A small companion TV app** using AVPlay, whose
  `setBufferingParam("PLAYER_BUFFER_FOR_PLAY", "PLAYER_BUFFER_SIZE_IN_SECOND", n)`
  is present in this firmware (`libwebapis_avplay.so`). It would be launched
  with the stream URL through `/sec/tv/launchapp` `targetAppParam`, the same
  way as YouTube. That needs the TV in Developer Mode and a Samsung
  certificate (or publication in Samsung's store), so it can only be an
  optional extra, not something the integration ships.
- **Not worth pursuing:** the TV browser (opened through
  `/sec/tv/searchonweb` `searchUrl` -> `LaunchBrowserApp`), which is slow on
  this TV, and DLNA, whose renderer is very strict about formats and never
  played a camera here.
