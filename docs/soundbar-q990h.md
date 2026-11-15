# HW-Q990H setting contracts

Changed, read back and restored on one HW-Q990H (#733).

| Setting | Resource | Property | Values |
| --- | --- | --- | --- |
| Active Voice Amplifier | `/sec/networkaudio/av/soundsetting` | `activevoiceamplifier` | Integer `0` / `1` |
| SpaceFit Sound | `/sec/networkaudio/av/soundsetting` | `spacefitsound` | Integer `0` / `1` |
| Rear speaker placement | `/sec/networkaudio/surroundspeaker` | `x.com.samsung.networkaudio.currentRearPosition` | One of `rearPositionList` (`Rear`, `Front`) |

The combined resource's fields carry no vendor prefix. Older bars serve
`/activeVoiceAmplifier` and `/spacefitSound` with prefixed fields; where both
forms report a setting, a valid combined field wins for that setting.

`tests/fixtures/soundbar_q990h_device.json` is a reconstructed contract
fixture with synthetic states, not a diagnostics capture. Replace it with a
LocalThings **Download diagnostics** from a Q990H when one is available.
