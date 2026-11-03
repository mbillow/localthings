# HW-Q990H local setting contracts

These three settings were changed and restored on one HW-Q990H through a
separate PSK-authenticated OCF implementation. Each requested value was read
back from the device; the result was not inferred from an accepted POST.

| Setting | Resource | Property | Verified values |
| --- | --- | --- | --- |
| Active Voice Amplifier | `/sec/networkaudio/av/soundsetting` | `activevoiceamplifier` | Integer `0` / `1` |
| SpaceFit Sound | `/sec/networkaudio/av/soundsetting` | `spacefitsound` | Integer `0` / `1` |
| Rear speaker placement | `/sec/networkaudio/surroundspeaker` | `x.com.samsung.networkaudio.currentRearPosition` | `Rear` / `Front` |

The rear-speaker resource advertised `rearPositionList` with the same vendor
prefix and the values `Rear`, `Front`. The select reads that list instead of
assuming every model has the same options. The HW-S61B fixture reports `none`
and exposes no placement control.

The AVA and SpaceFit fields above have no vendor prefix. Older soundbars use
separate `/activeVoiceAmplifier` and `/spacefitSound` resources with namespaced
fields. Preserve those mappings. When both forms report a setting, the valid
combined-resource field takes precedence for that setting; a missing or
unsupported combined field leaves the older mapping available.

`tests/fixtures/soundbar_q990h_device.json` is a deliberately minimal,
reconstructed contract fixture with synthetic states. It is not a full resource
dump, and it contains no credentials, account data, device UUIDs, network names,
addresses or serial numbers. Existing soundbar controls and the HW-S61B fixture
are reused rather than copied into a new model-specific registry.

The LocalThings descriptors are tested offline against the actual registry and
entity inclusion paths. They have not been installed in a production
LocalThings entry or exercised through its UI on HW-Q990H. The hardware evidence
establishes these resource contracts, not compatibility of every soundbar
feature. Custom EQ-band writes remain unconfirmed on this model.

Related: [media-device support](https://github.com/mbillow/localthings/issues/388)
and the [soundbar foundation](https://github.com/mbillow/localthings/pull/606).
