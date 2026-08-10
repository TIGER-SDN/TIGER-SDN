"""src/tiger_sdn/ir/__init__.py — 통합 Intent IR: 모델, 파싱 결과 래퍼, 외부 스키마 어댑터.

원본: sdn-intent-framework의 feat/unify-ir 브랜치, src/sdn_intent/ir/__init__.py
(커밋된 적 없는 로컬 상태 — docs/plan.md "확인된 사실 1" 참고. 패키지명만
sdn_intent -> tiger_sdn으로 바꾸고 내용은 그대로 옮겼다.)
"""

from tiger_sdn.ir.adapter import AdapterError, from_gold350, from_research, to_research
from tiger_sdn.ir.model import (
    ACTION_TO_INTENT_TYPE,
    Action,
    EndpointRef,
    IntentEnforcement,
    IntentProgram,
    IntentQoS,
    IntentRouting,
    IntentRule,
    IntentSelector,
    IntentType,
    StrictModel,
)
from tiger_sdn.ir.prediction import IntentPrediction, RejectedIntent, RejectionReason

__all__ = [
    "ACTION_TO_INTENT_TYPE",
    "Action",
    "AdapterError",
    "EndpointRef",
    "IntentEnforcement",
    "IntentPrediction",
    "IntentProgram",
    "IntentQoS",
    "IntentRouting",
    "IntentRule",
    "IntentSelector",
    "IntentType",
    "RejectedIntent",
    "RejectionReason",
    "StrictModel",
    "from_gold350",
    "from_research",
    "to_research",
]
