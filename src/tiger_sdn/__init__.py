"""src/tiger_sdn/__init__.py — 자연어 네트워크 인텐트를 검증된 SDN 정책으로 변환하는 코어.

원본: sdn-intent-framework의 feat/unify-ir 브랜치, src/sdn_intent/__init__.py
(커밋된 적 없는 로컬 상태 — docs/plan.md "확인된 사실 1" 참고. 패키지명만
sdn_intent -> tiger_sdn으로 바꾸고 내용은 그대로 옮겼다.)

`xai_pipeline`(제품 구현)과 `safe_intent_sdn`(연구 구현)을 하나로 합치는
중이며, 현재는 Intent IR 계층만 이관되어 있다. 컴파일러·검증기·Digital Twin은
후속 단계에서 합쳐진다.
"""

from tiger_sdn.ir import (
    IntentPrediction,
    IntentProgram,
    IntentRule,
    from_gold350,
    from_research,
    to_research,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "IntentPrediction",
    "IntentProgram",
    "IntentRule",
    "__version__",
    "from_gold350",
    "from_research",
    "to_research",
]
