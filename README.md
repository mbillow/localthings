<!-- dark mode -->
<img src="custom_components/localthings/brand/dark_logo@2x.png#gh-dark-mode-only" alt="LocalThings Logo"/>
<!-- light mode -->
<img src="custom_components/localthings/brand/logo@2x.png#gh-light-mode-only" alt="LocalThings Logo"/>

<p align="center">
  <img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/mbillow/localthings" />
  <img alt="GitHub watchers" src="https://img.shields.io/github/watchers/mbillow/localthings" />
</p>

<p align="center">
  <img alt="GitHub Release" src="https://img.shields.io/github/v/release/mbillow/localthings" />
  <img alt="hacs validation" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=HACS%20validation&label=hacs%20validation" />
  <img alt="hassfest" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=Hassfest%20validation&label=hassfest" />
  <img alt="tests" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=Pytest&label=tests" />

</p>

# LocalThings

**A native Home Assistant custom integration for local control of newer-generation Samsung connected appliances.** No cloud round-trip. Add a device through HA's normal *Settings > Devices & Services* flow and it talks CoAP-over-DTLS straight to the appliance on your LAN.

This integration uses the [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) library to handle the low-level DTLS/CoAP communication with devices.

### What you get

Adding a device just needs a host IP and your CA credentials in the UI. The integration reads the appliance's identity and picks the matching capability registry on its own, so there's no per-model descriptor to write for a new unit of a type that's already supported.

Credential setup is one-time. The first device you add asks for a CA certificate and key (see Part 2); every device after that reuses the same stored CA and only asks for the host IP, minting its own per-device leaf cert automatically.

Your state stays on your LAN: HA talks to the appliance over a direct DTLS session, and Samsung's cloud sees nothing from this integration. (The appliance itself still maintains its own connection to Samsung; that's firmware behavior on the device side, not something this integration controls.)

### Supported appliance types

| Type | Registry |
|---|---|
| Air conditioner | `by_type/airconditioner.py` |
| Air purifier | `by_type/air_purifier.py` |
| Dehumidifier | `by_type/dehumidifier.py` |
| Dryer | `by_type/dryer.py` |
| Oven | `by_type/oven.py` |
| Microwave | `by_type/microwave.py` |
| Gas cooktop (read-only burner status) | `by_type/cooktop.py` |
| Range hood | `by_type/range_hood.py` |
| Range | `by_type/range.py` |
| Dishwasher | `by_type/dishwasher.py` |
| Refrigerator | `by_type/refrigerator.py` |
| Washer | `by_type/washer.py` |
| Water purifier | `by_type/water_purifier.py` |
| Vacuum clean/auto-empty station | `by_type/vacuum_station.py` |
| Air dresser | `by_type/air_dresser.py` |

Each registry composes shared and family-specific `Capability` objects from `registry/capabilities/`; those modules document the individual resources/entities in more depth than a README table can stay current with.

Other Tizen RT / DAWIT-family appliances almost certainly speak the same protocol underneath, since the auth path and CoAP primitives are shared across the fleet. Adding a new type means writing a new `by_type/<name>.py` registry file; it doesn't require reverse-engineering the protocol again. See **Adding a new appliance type** below.

---

## Part 1: Is your appliance compatible?

```sh
# UDP scan for DTLS-CoAP ports
nmap -Pn -sU -p 49152-49160 "$APPLIANCE_IP"
```

- Any UDP port in `49152-49160` open|filtered with a DTLS handshake responding: newer firmware (Tizen RT 3.x, DAWIT 3.0+). This is what the integration talks to. Most devices answer on `49154`/`49155`, but some builds bind lower (e.g. `49153`). The config flow probes the whole range and auto-detects the live port, so you don't need to know which one your device uses.
- Only `8888/tcp` open (token-based HTTPS): older firmware (roughly 2018-2022). **Not supported here.**

---

## Part 2: One-time setup, get the AC14K_M CA credentials

The config flow (Part 3) needs a **CA certificate and CA private key** to mint each device's leaf cert itself. Specifically, it needs the `AC14K_M` intermediate CA — a cert chain that's been public for years and still ships in current Samsung firmware trust stores. Every Samsung Tizen/RT-OCF appliance trusts identities chained to that CA with full access by default, so a cert signed by it is what lets HA talk to your appliance without Samsung's cloud in the loop. HA doesn't need the *device's* original cert or key, only something `AC14K_M` has signed, and it mints that itself once you give it the CA.

This repo doesn't include the needed CA bundle. For an example of how to obtain it, including fetching the AC14K_M cert and key and verifying they pair, see the `smartthings-local` protocol project's [`setup_cert.py`](https://github.com/QuiteYellow/SmartThings-Local/blob/main/setup_cert.py). However you obtain the CA cert and key, paste their PEM contents into the HA config flow's "CA Certificate (PEM)" and "CA Private Key (PEM)" fields in Part 3. You only need to do this once, since every appliance you add afterward reuses the same stored CA.

---

## Part 3: Add the integration in Home Assistant

1. Copy `custom_components/localthings/` into your HA config's `custom_components/` directory. (Or add this repo as a custom repository in HACS — `Integration` category — and install it from there.)
2. Restart HA.
3. **Settings > Devices & Services > Add Integration > LocalThings.**
4. First device: paste the appliance's IP, plus the contents of the CA private and public key from Part 2.
5. The flow sends a DTLS `ClientHello` to every port in the `49152-49160` range at once and keeps the one that answers -- a real DTLS server identifies itself in about one round trip, and the probe stops there, so nothing is left behind on the appliance. Only that port is then given a real certificate handshake: it fetches the current UUID from Samsung's cloud gateway, mints a leaf cert signed by your CA, and reads the device's identity and `/device/0`. On success it creates the config entry, already knowing the appliance's serial, model, and type.
6. Every subsequent device only asks for the host IP. The stored CA credentials are reused, and so is the leaf cert itself -- every appliance accepts the same one -- so adding a second appliance doesn't depend on Samsung's cloud being reachable at all. If a device rejects the reused cert (the UUID behind it does rotate), the flow mints a fresh one and retries by itself.

Entities appear under one HA device per appliance, named for the appliance's type and model. Rename freely: the device is keyed on its serial, not its name.

---

## Part 4: Per-device settings

Each device has its own **Configure** option in Settings > Devices & Services, under **Device settings**:

- **Allow writes even when remote control is reported off** — by default, LocalThings blocks every write with a clear error whenever a device reports remote control off, rather than letting the device silently reject it. Some devices accept certain writes anyway (e.g. default detergent/softener dosing on a washer) even while reporting remote control off. Only enable this if you've confirmed writes actually work on your device with remote control off — otherwise you trade a clear error for a silent failure.
- **Estimated finish -- minimum change (minutes)** — a washer/dryer/dishwasher's `finish_time` sensor is recomputed from the device's own remaining-time estimate on every poll, which commonly drifts or gets revised by a minute or two between updates. This setting holds `finish_time` at its last reported value until a new estimate differs by at least this many minutes, cutting down on Home Assistant history/logbook noise from a value that hasn't meaningfully changed. Defaults to `3`; set it to `0` to report every computed change.

---

## Development

### Docker Compose dev environment

```sh
docker compose up -d --build
docker compose logs -f
```

The `Dockerfile` builds on the official `home-assistant/home-assistant:stable` image and pre-installs `smartthings-local`, so the dependency is present at container start instead of depending on HA's own runtime pip-install step. Re-run with `--build` whenever the pinned `smartthings-local` version changes.

`docker-compose.yml` sets `network_mode: host`, which is required since DTLS is UDP and won't traverse Docker's bridge NAT to reach LAN appliances, and bind-mounts `custom_components/localthings/` read-only into `ha_config/custom_components/`. Bump `custom_components.localthings` to `debug` in `ha_config/configuration.yaml` for verbose protocol logging.

### Tests

```sh
python3.13 -m venv .venv          # 3.13 or newer; see below
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -q
```

`requirements-dev.txt` already pulls in Home Assistant and pytest at matching versions, so there's nothing to install alongside it. Use Python 3.13 or newer: pip resolves the newest `pytest-homeassistant-custom-component` your interpreter supports, and on 3.12 or older nothing resolves and the install fails outright. CI runs 3.14.

A large suite covering registry composition, discovery, entity descriptors, and golden-file regression against captured device dumps. `requirements-dev.txt` pins `smartthings-local` the same way `manifest.json` does, so tests exercise the real published protocol layer rather than a vendored copy.

---

## Repo layout

```
custom_components/localthings/
  manifest.json         Requirements (incl. the smartthings-local PyPI dep), version, domain
  __init__.py            async_setup_entry / async_unload_entry
  config_flow.py          ClientHello port probe, UUID fetch, leaf cert minting, identity resolution
  coordinator.py          Polling + push update coordination, stale-state fallback, write dispatch
  observe.py              CoAP OBSERVE (push-mode) support layered on the coordinator
  diagnostics.py           Redacted diagnostics download (device state + coverage metadata)
  const.py                 Domain, config keys, probe ports
  entity.py                Base entity wiring capability registry -> HA entity
  sensor.py / binary_sensor.py / switch.py / number.py / select.py / button.py / time.py / fan.py / climate.py / water_heater.py
                            One module per HA platform
  catalog.py               Reads the shipped translation catalog (which keys/states exist)
  translations/            Config-flow copy + entity name/state translations, one file per
                            language; en.json is the source of truth (no strings.json —
                            Home Assistant never reads one from a custom integration)
  registry/
    registry.py             Builds the global capability registry, validates href collisions
    capability.py           Capability dataclass (href, entities, transforms)
    entities.py             Per-platform entity descriptor dataclasses
    discovery.py            Binds a device's live resources to registered capabilities
    adapter.py               Flattens bound entities into HA-ready state
    identity.py              Reads /oic/p + /oic/d (manufacturer, model, OCF device type)
    redact.py                 Strips account/identity data before diagnostics leave HA
    capabilities/             Shared + per-family Capability definitions (common, airconditioner,
                               cooktop, range_hood, dryer, oven, dishwasher, fridge, washer,
                               laundry, operational, ignored)
    by_type/                  One DeviceRegistry per appliance type, composed from capabilities/
tests/                    Registry composition, discovery, entity descriptors, coordinator/observe
                            behavior, and golden-file regression against captured device dumps
requirements-dev.txt        Test deps, including the smartthings-local package
docker-compose.yml / ha_config/   Local HA dev environment
```

---

## Reporting a capability gap

If your appliance's type isn't recognized, or it exposes resources this integration doesn't model yet, a Repairs
issue appears under Settings > System > Repairs pointing you at Settings > Devices & Services > this device >
the menu > Download diagnostics. That download is already redacted of account/network identifiers (Bixby login
email, access tokens, device IDs, MAC addresses, serial numbers) before it's generated, so it's safe to attach
directly to a new issue using the linked device-support template. This is the fastest way to help add or expand
support for hardware the maintainers don't have.

---

## Adding a new appliance type

1. Get a capture of the appliance's `/device/0` response. The easiest way: add the device to HA (type detection failing is fine) and pull its Diagnostics download from Settings > Devices & Services > the device > the menu > Download diagnostics — it already contains a redacted dump of the device's resources.
2. Reuse existing `Capability` objects from `registry/capabilities/` wherever the resource matches one already declared. Most `common.py` capabilities (power, kids lock, remote control, alarms, energy/water meters) are shared verbatim across families; add new ones only for resources unique to the new type.
3. Create `registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` instead of `capabilities` for any resource whose `href` isn't fixed (for example per-compartment fridge resources); see `refrigerator.py` for the pattern.
4. Register it in `_REGISTRY_BY_KEY` in `registry/by_type/__init__.py`, then route devices to it by adding the board-family token from their `modelNum` to `_BOARD_TOKEN_TO_KEY` — a single row, e.g. `'VSKR': 'vacuum_station'`. Tokens are matched whole (the model string is upper-cased and split on any run of non-alphanumerics), so one entry covers every delimiter spelling Samsung uses: `TP1X_DA-AC-RAC-01001` and `TP2X_RAC_20K` both resolve on `RAC`. Name the specific type, never the board family that contains it — `DA-AC-` prefixes RAC/WAC/DHM/AIR alike, so a bare `AC` row would swallow the dehumidifier and the air purifier. If the board is shared across types (washers and dryers both report `DA_WM_`), add the consumer-model prefix from `description` to `_CONSUMER_PREFIX_TO_KEY` instead. If the device omits `/information/vs/0` entirely (as the verified NA9300K cooktop does), add a distinctive, conservative resource-signature rule to `for_device_by_resources()`.

   `oneUiVersion` is deliberately not consulted — see `resolve()` in that file for why.
5. Add golden-file coverage in `tests/` against a captured `/device/0` dump for the new type.

No config-flow changes are needed. Device-type detection and entity wiring are fully driven by the registry.

---

## Known device behavior

Samsung's firmware occasionally drops the DTLS session briefly — this is normal appliance-side behavior, not a bug. The integration reconnects automatically, and from HA's perspective a brief reconnect looks like an entity holding its last value for one poll cycle rather than going `unavailable`. When an appliance supports it, the integration prefers push-based updates (instant, via `observe.py`) over polling, falling back to polling otherwise.

If reconnects become persistent (more than a handful per minute), something's actually wrong. Check the appliance's Wi-Fi link first, then look for a competing DTLS client on the LAN — only one active session per appliance is allowed at a time.

Deregistering a device in SmartThings causes a reset of its network settings as soon as it accesses Samsung's servers, dropping it off Wi-Fi until it's re-onboarded through the SmartThings app. As such, consider keeping devices registered even if egress-blocked, to avoid them resetting upon brief internet access.

### Multi-subdevice ("2-in-1") air conditioner systems

Some Samsung installs run more than one indoor subdevice off a single outdoor unit, all reachable over the *one* IP/DTLS session your config entry connects to (a floor-standing + wall-mounted 2-in-1 is a common shape). The integration discovers any sibling subdevices automatically, once, right after the first successful poll — there's nothing to configure. Each discovered subdevice gets its own HA device (linked to the main one via "via device") and its own `climate` card, so it lands in its own room in the dashboard instead of being invisible or mixed into the master's state.

Two on-the-wire shapes are supported, both keyed off what the appliance itself reports:

- **Indexed siblings** — the device answers a `/device/1`, `/device/2`, ... collection alongside its own `/device/0`, mirroring every resource at that index.
- **UUID-prefixed tree** — the device reports a sibling's id in `x.com.samsung.da.subdeviceIdList`, and that id doubles as a literal href prefix for the sibling's own resource tree.

A candidate that answers but never produces any real, user-facing state (an unused slot some installs report alongside a genuine second subdevice) is silently skipped rather than turned into a phantom entity — check diagnostics' `subdevices`/`subdevices_skipped` blocks if a subdevice you expect to see isn't showing up, and file an issue with that diagnostics download attached.

---

## Contributing

Patches are welcome, especially:

- New `by_type/` registries for appliance families not yet covered (AC, microwave, etc.) on the same Tizen RT 3.x firmware family.
- Confirmation or refutation of compatibility on additional models within an already-supported type.
- Protocol-level fixes, which belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) rather than here. HA-side fixes (entities, config flow, coordinator, registry) belong in this repo.

If you submit a PR, please don't include real device UUIDs, MACs, serials, IPs, or CA private key material. Use the placeholders from the config-flow form instead.

-----------
# localthings 커스텀 패치 — Samsung 시스템 청정환기 (ACA-KR-TP2-21-AN9000)

이 포크는 [mbillow/localthings](https://github.com/mbillow/localthings)에
"시스템 청정환기"(모델 ACA-KR-TP2-21-AN9000, vid `DA-AC-DIFFUSER-01001`)의
누락된 리소스 커버리지를 추가한 개인 패치입니다. 원본 프로젝트에는 아직
반영되지 않았습니다.

## 배경
이 기기는 `/oic/d`에서 자신을 `oic.d.airconditioner`로 선언해서
localthings의 `airconditioner` 레지스트리로 라우팅되는데, 실제로는 일반
에어컨과 다른 리소스 구조(청정/환기/스마트환기 모드, 헤파필터, 인공지능
청정 등)를 가지고 있어서 다수의 리소스가 `unbound_hrefs`로 남아 있었습니다.
(진단 덤프로 직접 재현·검증한 이력은 이 저장소의 이슈/PR 설명 참고)

## 바꾼 파일 3개
| 파일 | 바뀐 내용 |
|---|---|
| `custom_components/localthings/registry/capabilities/airconditioner.py` | 아래 "추가된 기능" 참고 |
| `custom_components/localthings/registry/by_type/airconditioner.py` | 새 캡퍼빌리티들을 레지스트리에 등록 |
| `custom_components/localthings/translations/en.json` | 새 엔티티 이름/옵션 라벨 |

파일 안에서 `# --- 커스텀 추가분 ---` ~ `# --- 커스텀 추가분 끝 ---` 주석으로
감싼 부분이 전부 이번에 추가한 코드입니다. 원본과 무엇이 다른지 한눈에
보려면:
```bash
git diff v0.18.0 -- custom_components/localthings/registry/capabilities/airconditioner.py
git diff v0.18.0 -- custom_components/localthings/registry/by_type/airconditioner.py
git diff v0.18.0 -- custom_components/localthings/translations/en.json
```

## 추가된 기능
- `switch.power_switch` — 독립 전원 스위치 (원래 이 레지스트리는 climate
  카드로만 전원을 다루도록 설계돼 있었음 -- 대시보드에서 바로 켜고 끌 수
  있게 별도 스위치 추가. climate 카드와 href를 공유하며 충돌 없음)
- `select.ventilation_mode` — 청정 / 환기 / 스마트환기 (climate 카드의
  hvac_mode로는 표현 안 되는 이 기기 고유 모드 어휘)
- `select.fan_speed_select` — 팬속도 (자동/약/중/강). 원래 climate
  카드 안의 fan_mode로만 흡수되던 것을 독립 컨트롤로도 노출
- `select.swing_direction_select` — 스윙 방향 (Up and Low / Fixed).
  swing_mode와 동일하게 독립 컨트롤로도 노출
- `switch.windfree` / `switch.windsleep` — 무풍 / 취침모드
- `switch.ai_clean_active` / `select.ai_clean_mode` — 인공지능 청정
  (원래 "의미 없는 내부 plumbing"으로 무시되던 `/airlevelcheck/vs/0`를
  실제 SmartThings 앱 기능에 대응하는 진짜 컨트롤로 승격)
- `sensor.dust`(PM10) / `fine_dust`(PM2.5) / `super_fine_dust`(PM1.0) /
  `odor` / `clean_level` / `co2` — 공기질 6종, `µg/m³`/`ppm`/`Lv` 단위와
  HA 표준 device_class(pm10/pm25/pm1) 포함
- `sensor.hepa_filter_usage` / `sensor.hepa_filter_status` — 헤파필터
  (air_purifier.py의 기존 캡퍼빌리티 재사용)
- `binary_sensor.device_active` — 기기 활성 상태 (air_purifier.py 재사용)
- `sensor.model_name` — 모델명 진단 센서 (원래 기기타입 판별에만 쓰이고
  전역 무시되던 `/information/vs/0`을 이 레지스트리에서만 예외로 노출)

## 다른 에어컨 사용자에게 미치는 영향
`power_switch`, `fan_speed_select`, `swing_direction_select`,
`ai_clean_active`/`ai_clean_mode`, `model_name`은 `airconditioner`
레지스트리 전체가 공유하는 href를 다루기 때문에, 이 통합구성요소로
연결된 **다른 모든 진짜 에어컨 모델에도 똑같이 새 엔티티가 생깁니다**
(기존 climate 카드 동작에는 영향 없음 -- 추가 노출일 뿐). 반대로
`ventilation_mode`, `windfree`, `windsleep`은 이 기기의 특이한 모드
어휘로만 게이팅돼 있어서 다른 에어컨에는 나타나지 않습니다.

## 검증
HA 없이 `registry/` 패키지만으로 직접 구동해서 확인했습니다
(adding-device-support 스킬의 §2 방식):
- 이 모델의 실제 진단 덤프로 `unbound_hrefs: []` (커버리지 100%)
- `tests/fixtures/airconditioner*_device.json` 기존 22개 fixture 전부
  재실행 — 에러 없음, 기존 entity 개수/값 회귀 없음

## ⚠ 확인 안 된 부분 (실기기 미확인)
아래 쓰기(write) 계약은 이 파일의 다른 단순 필드들과 같은 패턴으로 추정한
것이며, 실기기에서 눌러본 결과가 아직 없습니다. 사용해보시고 안 먹는
게 있으면 이슈로 남겨주세요:
- `power_switch`, `windfree`, `windsleep`, `ai_clean_active`,
  `ai_clean_mode`, `ventilation_mode`, `fan_speed_select`,
  `swing_direction_select`의 쓰기 동작

## 설치 (HACS 커스텀 저장소)
1. HACS → 우측 상단 점 3개 → 사용자 지정 저장소(Custom repositories)
2. 저장소 URL에 이 포크 주소 입력, 카테고리는 "통합 구성 요소"
3. 추가 후 "localthings" 검색해서 설치 (원본 대신 이 포크가 뜸)

## 원본과 계속 동기화하기 (업스트림 업데이트 따라가기)
```bash
git remote add upstream https://github.com/mbillow/localthings.git
git fetch upstream
git merge upstream/main   # 또는 rebase
```
충돌 나면 위 "바꾼 파일 3개" 표에 있는 파일들 위주로 확인하세요 —
`# --- 커스텀 추가분 ---` 주석으로 감싼 블록만 살아있으면 됩니다.
원본 프로젝트가 나중에 이 기기를 정식 지원하게 되면, 그때는 이 포크
대신 원본으로 다시 옮기는 걸 추천드립니다.

## 원본 프로젝트에 기여하기
이 패치가 실기기에서 잘 동작하는 게 확인되면, 원본 저장소에 이슈나 PR로
제보하는 것도 좋은 방법입니다 — adding-device-support 스킬 문서에 나온
대로, 이 프로젝트는 실사용자 리포트 기반으로 기기 지원을 넓혀갑니다.
