# TV sound output: read-only, and what an external receiver does to volume

On a 2023 KQ77SC95A (wired) with the sound output set to an HDMI-eARC
soundbar, `/sec/tv/mediaOutput` serves the output list and the selected
output:

```json
{"outputList": [{"id": "internal", "name": "TV Speaker", "type": "DEFAULT"},
                {"id": "RCV-1", "name": "Soundbar(HDMI-eARC)", "type": "HDMI"},
                {"id": "optical", "name": "Receiver (Optical)", "type": "OPTICAL"}],
 "curMediaOutput": "RCV-1"}
```

LocalThings exposes it as the read-only `sound_output` sensor.

## What was measured

- **Output selection cannot be written.** `{"curMediaOutput": "internal"}`,
  `"optical"` and `{"x.com.samsung.tv.curMediaOutput": "internal"}` all answer
  2.04, and `curMediaOutput` never changes (polled for 8-12 s).
- **Absolute volume is the TV speaker's.** `/sec/tv/audio` volume read 7 while
  the soundbar and the TV's own UPnP volume read 0. Writing volume 1..7 read
  back exactly, but the soundbar volume never moved.
- **Mute reaches the soundbar, but its readback lags one write.** The soundbar
  muted and unmuted within about 1 s. `/sec/tv/audio` `mute` read false after
  mute=true, and true after mute=false, staying true for 2.5 min until the next
  write. The media player showed muted while the soundbar was not.
- **Volume steps reach a receiver.** `KEY_VOLUP` through
  `/sec/tv/remotecontrol` moved the soundbar volume (CEC), so relative steps
  work where absolute volume does not.

## Also observed working

- Input switching in both directions (about 1-2 s); the sound mode list
  follows the input, for example a game mode on a console input.
- The screen-off button, woken by any key.
- `open_app` with "sources" opens Smart Hub.
- The `ambient_mode` button enters "Ambient Mode", even on this OLED, and
  `KEY_EXIT` leaves it.
- `play_media` with an app id (YouTube) opens the app in about 4 s, and the
  `open_app` select learns its name.
- Remote `KEY_CHUP`/`KEY_CHDOWN` change the channel on an HDMI set-top box
  over CEC; `KEY_PAUSE`/`KEY_PLAY` did nothing on that box.
- A `channel` `play_media` with no tuner signal is refused
  (`controlResponse.result=false`) and surfaces as an error.

## Left unchanged

The media player's volume handling with an external output is unchanged. Using
the sensor to switch to steps or to hide the slider is a follow-up.

## Related

`/capability/custom.energyType/main/0` is now covered with no entity: writing
`energySavingOperation` true answers 2.04, but energy saving stays off and the
readback stays false (#895).
