"""src/tiger_sdn/runctx — 실행 로깅 (Stage 4-8과 독립적으로 이식 가능, 이슈 #16).

원본: sdn-intent-framework의 research/safe_intent_sdn/run_context.py + schema.py.
연구 트랙이 이 영역의 정본이다 — JSON Schema 자동생성, secret redaction, 동시성
에서 제품 구현(`xai_pipeline`)의 손으로 채운 로그 dict보다 우월하다.
"""

from __future__ import annotations

from tiger_sdn.runctx.run_context import (
    ArtifactRef,
    Decision,
    EventRecord,
    LogLevel,
    RunContext,
    RunManifest,
    RunStatus,
)
from tiger_sdn.runctx.schema import generate_schemas

__all__ = [
    "ArtifactRef",
    "Decision",
    "EventRecord",
    "LogLevel",
    "RunContext",
    "RunManifest",
    "RunStatus",
    "generate_schemas",
]
