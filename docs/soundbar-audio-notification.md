# Soundbar audio notifications

Experimental. Measured on one HW-S61B (firmware HW-S61BWWB-1010.0) with a
logging HTTP server; the firmware notes come from the HW-Q850A image.

## Use

A soundbar that reports `/x.com.samsung/notification/receiver` with resource
type `x.com.samsung.audionotification` gets `play_media`, `announce` and media
browsing on its media player. `tts.speak`, `media_player.play_media` with
`media_content_type: music` and an audio media source all work:

```yaml
action: tts.speak
target:
  entity_id: tts.home_assistant_cloud
data:
  media_player_entity_id: media_player.soundbar
  message: The washing machine has finished.
```

The bar plays its own chime, then the clip, also from standby. It interrupts
whatever is playing. Its volume and what resumes afterwards are the bar's own;
LocalThings doesn't change either.

## Contract

| Step | Behavior |
| --- | --- |
| Write | POST `{"x.com.samsung.audio": "<url>"}` to the receiver. 2.04 only means the write landed. |
| Download | One plain GET (no User-Agent, no Range). No redirects. One retry after a failure. 30 s limit for the whole transfer. Chunked responses work. |
| URL | Only fetched when the path ends in a file extension; otherwise silently ignored. |
| Playback | Whatever is downloaded plays as headerless signed 16-bit little-endian mono PCM at 24 kHz. Ogg, MP3 and WAV come out as noise or at the wrong speed. |
| Length | Playback stops about 41 s into the clip (a number spoken every second was last heard at 41). |
| Chime | Always played first. The receiver has no field that skips it. |

LocalThings therefore converts the source with ffmpeg to 24 kHz mono PCM
before the write (`audio_notify.py`), keeps it in memory for five minutes, and
serves it unauthenticated at `/api/localthings/audio/<random token>.pcm` from
Home Assistant's local network URL (Settings > System > Network), never a
cloud URL, which answers through a redirect the bar won't follow. Plain HTTP
to an IP address and HTTPS through a public host name have been tested. A
Home Assistant that serves HTTPS only doesn't fall back to its IP address: set
a local URL the bar can reach, with a certificate it accepts.

Clips are cut to 40 s, with a warning in the log, since the bar stops there
anyway. A live stream (internet radio) is read for 40 s and plays as a 40 s
clip.

A conversion may take 60 s, and at most 8 clips (about 2 MB each) are kept,
until the last LocalThings entry unloads.
