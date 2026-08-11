"""src/tiger_sdn/orchestrate/repair.py — Repair Loop 피드백 생성.

원본: sdn-intent-framework의 src/xai_pipeline/pipeline/repair_utils.py
(`MAX_REPAIR_ATTEMPTS`, `build_repair_feedback`). 원본은 stage3_static의
`StaticResult`(schema_errors/conflicts) 하나만 피드백으로 바꿨는데, TIGER-SDN의
파이프라인은 두 게이트를 거친다 — 컴파일 전 그라운딩(`verify.grounding.
ValidationReport`, Stage 6)과 컴파일 후 정적 검증(`verify.static.StaticResult`,
동일 Stage 6)이 서로 다른 실패 모양을 낸다. 그래서 게이트별로 피드백 생성기를
하나씩 뒀다 — 원본의 단일 `build_repair_feedback`은 없다.
"""

from __future__ import annotations

from tiger_sdn.verify import StaticResult, ValidationReport

__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "build_repair_feedback_from_grounding",
    "build_repair_feedback_from_static",
]

MAX_REPAIR_ATTEMPTS: int = 3


def build_repair_feedback_from_grounding(
    report: ValidationReport, attempt: int, max_attempts: int
) -> str:
    """그라운딩 실패(미지 host/ip/device, missing_device, 충돌, path)를 LLM 재시도용 피드백으로."""
    lines = [
        f"[Repair attempt {attempt}/{max_attempts}"
        " — previous output failed grounding validation against the topology]"
    ]
    for finding in report.findings:
        lines.append(f"  - [{finding.category}/{finding.code}] rule(s) {finding.rule_indices}: {finding.message}")
    lines.append("Please revise the intent representation to fix these issues.")
    return "\n".join(lines)


def build_repair_feedback_from_static(
    result: StaticResult, attempt: int, max_attempts: int
) -> str:
    """컴파일된 FlowRule의 스키마/충돌 실패를 LLM 재시도용 피드백으로."""
    lines = [
        f"[Repair attempt {attempt}/{max_attempts}"
        " — previous output was rejected by static validation]"
    ]
    if result.schema_errors:
        lines.append("Schema errors:")
        for error in result.schema_errors:
            lines.append(f"  - {error}")
    if result.conflicts:
        lines.append("Conflicts:")
        for conflict in result.conflicts:
            lines.append(f"  - [{conflict.get('conflict_type', '?')}] {conflict.get('reason', '')}")
    lines.append("Please revise the intent representation to fix these issues.")
    return "\n".join(lines)
