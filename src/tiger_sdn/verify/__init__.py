"""src/tiger_sdn/verify — 정적 검증 (Stage 6, 이슈 #13).

두 축을 병합한다 — 대체 관계가 아니라 병합 대상(docs/plan.md Stage 6 참고):

  - `grounding` — Intent IR을 컴파일 전에 토폴로지 인벤토리에 대해 그라운딩한다
    (원본: research/safe_intent_sdn/validator.py의 TopologyInventory). 미지
    host/ip/device를 거부하고, enforcement.device 미지정을 명시적으로 거부한다
    (`compile/compiler.py`가 조용히 기본 스위치로 폴백하던 것과 대비).
  - `schema` + `conflict` + `static` — 컴파일된(또는 외부에서 들어온) ONOS
    FlowRule JSON을 검증한다 (원본: stage3_static의 세 모듈). 5종 충돌
    (Shadowing/Redundancy/Correlation/Imbrication/Generalization) + compound
    인텐트 내부 충돌을 잡는다.

파이프라인 위치: Intent IR -> compiler -> **static verification** -> Digital
Twin. `verify_program`은 컴파일 직전(IR 단계)에, `static.validate`는 컴파일
직후(FlowRule 단계)에 각각 돌리는 두 개의 게이트다.
"""

from __future__ import annotations

from .conflict import detect_conflict
from .grounding import FindingCategory, ValidationFinding, ValidationReport, verify_program
from .schema import validate_schema
from .static import StaticResult, validate
from .topology import TopologyInventory, load_topology_inventory

__all__ = [
    "FindingCategory",
    "StaticResult",
    "TopologyInventory",
    "ValidationFinding",
    "ValidationReport",
    "detect_conflict",
    "load_topology_inventory",
    "validate",
    "validate_schema",
    "verify_program",
]
