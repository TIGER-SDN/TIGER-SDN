"""src/tiger_sdn/explain/decision.py — 정적 검증 + Digital Twin 결과로 최종 판정.

원본: sdn-intent-framework의 src/xai_pipeline/pipeline/stage5_xai/explainer.py.
결정론적(템플릿 기반) 부분만 이식했다 — `_compute_confidence`/
`_build_decision_reason`/`_build_evidence`/각 `_summarize_*`는 원본 그대로의
로직이고, decision_reason을 자연어로 다듬는 *선택적* LLM 2차 호출
(`self.client`가 있을 때만 타는 `_llm_decision_reason`)은 이번 포팅에서 뺐다
— Stage 9 착수 시 결정(docs/plan.md 참고): decision_reason은 항상 템플릿
문자열이다. `explain/`은 plan.md가 "장기 보류"로 미뤄뒀던 자리지만, 이 결정론적
버전은 원본의 핵심 로직(120-125줄의 decision 분기)을 그대로 옮긴 것이라 사실상
그 보류를 해소한다.

`XAIReport.to_dict()` 대신 pydantic `DecisionReport.model_dump()`를 쓴다 —
tiger_sdn 전역 컨벤션(runctx/ir/compile 전부 `.model_dump()`를 쓴다. `.to_dict()`
관례는 존재하지 않는다).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tiger_sdn.ir import IntentProgram
from tiger_sdn.twin import TwinResult
from tiger_sdn.verify import StaticResult

Decision = Literal["APPROVE", "APPROVE_WITHOUT_TWIN", "REJECT"]

_ACTION_LABEL: dict[str, str] = {
    "block": "차단",
    "forward": "전달",
    "qos": "QoS 처리",
    "sfc": "서비스 체인",
    "reroute": "경로 변경",
}

_TWIN_STATUS_LABEL: dict[str, str] = {
    "passed": "검증 통과",
    "failed": "검증 실패",
    "skipped": "스킵됨",
    "error": "오류 발생",
}


class DecisionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    ir_summary: str
    flowrule_summary: str
    static_summary: str
    twin_summary: str
    decision: Decision
    decision_reason: str
    confidence: float = 0.0
    confidence_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


def _summarize_rule(rule: Any) -> str:
    label = _ACTION_LABEL.get(rule.action, rule.action)
    sel = rule.selector
    bits: list[str] = []
    if sel.source and (sel.source.ip or sel.source.host):
        bits.append(f"src={sel.source.ip or sel.source.host}")
    if sel.destination and (sel.destination.ip or sel.destination.host):
        bits.append(f"dst={sel.destination.ip or sel.destination.host}")
    if sel.protocol:
        bits.append(f"proto={sel.protocol}")
    if sel.dst_port is not None:
        bits.append(f"dport={sel.dst_port}")
    if rule.enforcement and rule.enforcement.egress_port is not None:
        bits.append(f"outPort={rule.enforcement.egress_port}")
    if rule.qos and rule.qos.queue is not None:
        bits.append(f"queue={rule.qos.queue}")
    match_str = " | ".join(bits) if bits else "(기본 매칭)"
    device = rule.enforcement.device if rule.enforcement else "?"
    return f"action={rule.action}({label}) | device={device} | {match_str}"


def _summarize_program(program: IntentProgram) -> str:
    if not program.is_compound:
        return _summarize_rule(program.single)
    parts = []
    for i, rule in enumerate(program.rules):
        label = _ACTION_LABEL.get(rule.action, rule.action)
        device = rule.enforcement.device if rule.enforcement else "?"
        parts.append(f"rule[{i}]: {rule.action}({label}) on {device}")
    return f"복합 인텐트 {len(program.rules)}개 룰 — " + " | ".join(parts)


def _summarize_flowrule(flowrule: dict) -> str:
    flows = flowrule.get("flows", [])
    if not flows:
        return "FlowRule 없음"
    flow = flows[0]
    device_id = flow.get("deviceId", "?")
    priority = flow.get("priority", "?")
    criteria = flow.get("selector", {}).get("criteria", [])
    treatment = flow.get("treatment")
    instructions = (treatment or {}).get("instructions", [])
    is_drop = treatment is None or not instructions or any(
        i.get("type") == "NOACTION" for i in instructions
    )
    action_str = "DROP" if is_drop else "FORWARD/QoS"
    return (
        f"deviceId={device_id} | priority={priority} | "
        f"criteria={len(criteria)}개 | action={action_str}"
    )


def _summarize_twin(twin_result: TwinResult) -> str:
    label = _TWIN_STATUS_LABEL.get(twin_result.status, twin_result.status)
    if twin_result.reason:
        return f"{label} ({twin_result.reason})"
    return label


def _compute_confidence(
    static_result: StaticResult, twin_result: TwinResult
) -> tuple[float, dict[str, float]]:
    if static_result.schema_errors:
        static_score = 0.0
    elif static_result.conflicts:
        static_score = 0.3
    elif static_result.warnings:
        static_score = 0.8
    else:
        static_score = 1.0

    twin_score_map = {"passed": 1.0, "skipped": 0.7, "failed": 0.0, "error": 0.0}
    twin_score = twin_score_map.get(twin_result.status, 0.0)

    if twin_result.status == "skipped":
        confidence = static_score
    else:
        confidence = static_score * 0.5 + twin_score * 0.5
    return round(confidence, 4), {"static": static_score, "twin": twin_score}


def _build_decision_reason(static_result: StaticResult, twin_result: TwinResult) -> str:
    reasons: list[str] = []

    if static_result.passed:
        reasons.append("정적 검증 통과 (스키마 OK, 충돌 없음)")
    else:
        if static_result.schema_errors:
            reasons.append(
                f"스키마 오류 {len(static_result.schema_errors)}개: {static_result.schema_errors[0]}"
            )
        if static_result.conflicts:
            conflict_types = [c.get("conflict_type", "?") for c in static_result.conflicts]
            reasons.append(f"충돌 탐지 {len(static_result.conflicts)}건: {', '.join(conflict_types)}")

    if twin_result.status == "passed":
        checks_ok = sum(1 for v in twin_result.checks.values() if v)
        total = len(twin_result.checks)
        reasons.append(f"Digital Twin 검증 통과 ({checks_ok}/{total} 체크)")
    elif twin_result.status == "skipped":
        reasons.append(f"Digital Twin 스킵 ({twin_result.reason})")
    elif twin_result.status == "failed":
        failed = [k for k, v in twin_result.checks.items() if not v]
        reasons.append(f"Digital Twin 실패 (실패 체크: {', '.join(failed)})")
    elif twin_result.status == "error":
        reasons.append(f"Digital Twin 오류: {twin_result.reason}")

    return "; ".join(reasons)


def _build_evidence(
    program: IntentProgram,
    flowrule: dict,
    static_result: StaticResult,
    twin_result: TwinResult,
) -> list[dict[str, Any]]:
    flows = flowrule.get("flows", [])
    flow_summary = flows[0] if flows else {}
    return [
        {
            "stage": "Stage1_IntentParsing",
            "finding": _summarize_program(program),
            "data": program.model_dump(),
        },
        {
            "stage": "Stage2_FlowRuleCompile",
            "finding": (
                f"deviceId={flow_summary.get('deviceId', '?')}, "
                f"priority={flow_summary.get('priority', '?')}"
            ),
            "data": flowrule,
        },
        {
            "stage": "Stage3_StaticValidation",
            "finding": static_result.summary(),
            "data": {
                "passed": static_result.passed,
                "schema_errors": static_result.schema_errors,
                "conflicts": static_result.conflicts,
                "warnings": static_result.warnings,
            },
        },
        {
            "stage": "Stage4_DigitalTwin",
            "finding": twin_result.summary(),
            "data": {
                "status": twin_result.status,
                "reason": twin_result.reason,
                "checks": twin_result.checks,
                "evidence": twin_result.evidence,
            },
        },
    ]


def build_decision(
    intent: str,
    program: IntentProgram,
    flowrule: dict,
    static_result: StaticResult,
    twin_result: TwinResult,
) -> DecisionReport:
    """정적 검증 + Digital Twin 결과를 종합해 최종 판정을 만든다.

    결정 로직은 원본 stage5_xai/explainer.py:120-125와 동일:
      REJECT               정적 검증 실패 또는 twin이 failed/error
      APPROVE_WITHOUT_TWIN 정적 검증 통과 + twin skipped
      APPROVE               정적 검증 통과 + twin passed
    """
    if not static_result.passed or twin_result.status in ("failed", "error"):
        decision: Decision = "REJECT"
    elif twin_result.status == "skipped":
        decision = "APPROVE_WITHOUT_TWIN"
    else:
        decision = "APPROVE"

    confidence, breakdown = _compute_confidence(static_result, twin_result)

    return DecisionReport(
        intent=intent,
        ir_summary=_summarize_program(program),
        flowrule_summary=_summarize_flowrule(flowrule),
        static_summary=static_result.summary(),
        twin_summary=_summarize_twin(twin_result),
        decision=decision,
        decision_reason=_build_decision_reason(static_result, twin_result),
        confidence=confidence,
        confidence_breakdown=breakdown,
        evidence=_build_evidence(program, flowrule, static_result, twin_result),
    )
