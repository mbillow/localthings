"""Air-conditioner device registry (Samsung ARTIK051_PRAC-class, issue #17).

The first device whose core controls surface as a single composite HA `climate`
entity (see capabilities/airconditioner.py and climate.py). Power/mode/temp/wind
are consumed by that entity rather than exposed as separate switches/selects, so
this registry includes *common.UNIVERSAL but deliberately NOT common.POWER --
on/off is the climate entity's HVACMode.OFF / TURN_ON/OFF. See common.POWER's
own comment in capabilities/common.py for why it's excluded.

common.ENERGY_METER itself is also excluded from UNIVERSAL here, replaced by
the ENERGY_METER_GENERIC/ENERGY_METER_LEGACY pair -- the legacy ARTIK051 board
generation (issue #193) reports cumulativePower in a different unit than
every other AC family, so this registry needs two mutually-exclusive variants
of that one capability instead of the single shared one every other registry
uses unconditionally.

Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0.
"""
# air_purifier 모듈은 커스텀 추가분(HEPA_FILTER, DEVICE_ACTIVE 재사용)을 위해
# 임포트. 두 캡퍼빌리티 모두 이 레지스트리가 원래 다루지 않던 href
# (/filter/hepafilter/vs/0, /devicespecificinfo/vs/0)를 정확히 같은 형태로
# 다루고 있어서 새로 만들지 않고 그대로 재사용함 (adding-device-support
# 스킬 9절 "Reuse before writing new code" 원칙).
from ..capabilities import air_purifier, airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='airconditioner',
    capabilities=_build([
        # --- 커스텀 추가분: /information/vs/0을 전역 무시목록에서 제외 -----
        # 원래 이 href는 기기타입 판별용으로만 쓰이고 전역적으로 무시됨
        # (ignored.py). 이 레지스트리에서만 예외로 빼서 진짜 모델명 센서
        # (airconditioner.MODEL_INFO)로 노출함.
        *[c for c in ignored.IGNORED if c.href != '/information/vs/0'],
        # --- 커스텀 추가분 끝 -------------------------------------------------
        *[c for c in common.UNIVERSAL if c is not common.ENERGY_METER],
        # --- 커스텀 추가분: 독립 Power 스위치 (climate 카드와 별개) --------
        # COVERAGE에서 HREF_POWER/HREF_POWER_VS를 뺐기 때문에(capabilities/
        # airconditioner.py 참고) 여기서 진짜 엔티티로 등록해도 충돌 안 남.
        *common.POWER,
        # --- 커스텀 추가분 끝 -------------------------------------------------
        airconditioner.ENERGY_METER_GENERIC,
        airconditioner.ENERGY_METER_LEGACY,
        dishwasher.DIAGNOSIS,
        airconditioner.CLIMATE,
        airconditioner.AIR_PURIFY,
        airconditioner.AUTO_CLEAN,
        airconditioner.AIR_FILTER,
        airconditioner.AIR_QUALITY,
        airconditioner.DISPLAY_LIGHT,
        airconditioner.MUTE_ONCE,
        airconditioner.CURRENT_LIMIT,
        airconditioner.ANOMALY_LOAD,
        airconditioner.ABSENCE_POWER_SAVING,
        airconditioner.MOTION_DETECT_WIND,
        airconditioner.CURRENT_TEMPERATURE,
        airconditioner.CURRENT_TEMPERATURE_VS,
        airconditioner.HUMIDITY,
        # --- 커스텀 추가분 (모델 ACA-KR-TP2-21-AN9000 청정환기 커버리지 갭) ---
        airconditioner.MODEL_INFO,
        airconditioner.FAN_SPEED_SELECT,
        airconditioner.SWING_DIRECTION_SELECT,
        airconditioner.AI_CLEAN,
        airconditioner.WINDFREE,
        airconditioner.WINDSLEEP,
        air_purifier.HEPA_FILTER,
        air_purifier.DEVICE_ACTIVE,
        # --- 커스텀 추가분 끝 ---
        *airconditioner.COVERAGE,
    ]),
)
