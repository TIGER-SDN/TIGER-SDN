"""src/tiger_sdn/orchestrate/pipeline.py — parse -> verify -> compile -> verify -> twin.

원본: sdn-intent-framework의 main.py의 Repair Loop 구조(파싱->컴파일->정적검증을
`repair_utils.MAX_REPAIR_ATTEMPTS`회까지 재시도, twin은 루프 밖에서 1회)를
승격한다(docs/plan.md Stage 8 "착수 시 결정 사항" — Repair Loop 승격은
Exp-2가 아니라 여기서 한다).

main.py와 다른 점 셋:

1. **6단계가 아니라 4단계.** 원본의 stage5(XAI 설명)/stage6(ONOS 실배포)는
   `explain/`/`deploy/`처럼 장기 보류라 이 파이프라인 범위 밖이다 — 결정
   (APPROVE/APPROVE_WITHOUT_TWIN/REJECT/ERROR)까지만 낸다.
2. **그라운딩이 파서 안이 아니라 별도 게이트.** 원본은 `NetworkTopology.
   check_intent()`를 파서 내부에서 돌렸지만, 여기서는 `verify.grounding.
   verify_program()`(IR 단계, 컴파일 전)과 `verify.static.validate()`(FlowRule
   단계, 컴파일 후)를 파이프라인이 명시적으로 두 게이트로 돌린다 — 원본의
   컴파일 단계 실패(재시도 안 함, 즉시 ERROR)와 정적 검증 단계 실패(재시도함)
   구분은 그대로 유지: 그라운딩/정적검증 실패는 repair loop 대상, 컴파일
   자체의 예외(`CompilationError`)는 아니다.
3. **ONOS 기존 flow 조회가 베스트에포트.** 원본처럼 조회 실패해도 파이프라인은
   계속 진행하고(그저 정적 검증의 외부 충돌 탐지를 스킵), 루프 최초 1회에만
   조회한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from tiger_sdn.backends import OnosClient
from tiger_sdn.compile import CompilationError, OnosFlowSet, compile_prediction
from tiger_sdn.ir import IntentPrediction
from tiger_sdn.orchestrate.repair import (
    MAX_REPAIR_ATTEMPTS,
    build_repair_feedback_from_grounding,
    build_repair_feedback_from_static,
)
from tiger_sdn.parse import build_topology_prompt, parse_intent
from tiger_sdn.runctx import RunContext
from tiger_sdn.twin import REACH_ONLY, TwinResult, TwinVerifier
from tiger_sdn.verify import (
    StaticResult,
    TopologyInventory,
    ValidationReport,
    load_topology_inventory,
    validate as static_validate,
    verify_program,
)

__all__ = ["PipelineResult", "run_pipeline"]

Decision = Literal["APPROVE", "APPROVE_WITHOUT_TWIN", "REJECT", "ERROR"]


@dataclass
class PipelineResult:
    """파이프라인 한 번 실행의 전체 결과 — 모든 게이트의 산출물을 그대로 담는다."""

    decision: Decision
    intent: str
    reason: str
    repair_attempts: int = 0
    prediction: Optional[IntentPrediction] = None
    grounding_report: Optional[ValidationReport] = None
    flow_set: Optional[OnosFlowSet] = None
    static_result: Optional[StaticResult] = None
    twin_result: Optional[TwinResult] = None


def _host_ip_map(topology: dict[str, Any]) -> dict[str, str]:
    """host alias -> IP 맵. 컴파일러의 host->ip 해석에 필요하다.

    원본: experiments/exp2/run.py의 host_ip_map — 같은 topology_eval.json
    형식을 읽는 로직이라 그대로 다시 포팅했다(experiments/는 건드리지 않음).
    """
    from ipaddress import ip_address

    def _is_ip(value: str) -> bool:
        try:
            ip_address(value)
            return True
        except ValueError:
            return False

    mapping: dict[str, str] = {}
    for entity in topology.get("entities", []):
        if not entity["id"].startswith("host:"):
            continue
        ip = next((alias for alias in entity["aliases"] if _is_ip(alias)), None)
        if ip is None:
            continue
        for alias in entity["aliases"]:
            if alias != ip:
                mapping[alias] = ip
    return mapping


def run_pipeline(
    intent: str,
    *,
    model: str,
    topology: dict[str, Any],
    skip_twin: bool = False,
    twin_checks: frozenset[str] = REACH_ONLY,
    onos_client: Optional[OnosClient] = None,
    run_context: Optional[RunContext] = None,
) -> PipelineResult:
    """자연어 인텐트 하나를 파싱부터 twin 검증까지 끝까지 돌린다.

    Args:
        intent: 자연어 네트워크 인텐트.
        model: LLM 모델 이름 (tiger_sdn.parse.llm_client.call_llm의 라우팅 규칙).
        topology: data/gold/topology_eval.json과 같은 형식의 토폴로지 딕셔너리 —
            그라운딩 인벤토리, 프롬프트 텍스트, host->ip 맵을 여기서 전부 파생한다.
        skip_twin: True면 Digital Twin 검증을 건너뛴다(twin_result.status="skipped").
        twin_checks: TwinVerifier.verify()에 넘길 체크 집합.
        onos_client: 기존 flow 조회에 쓸 클라이언트. None이면 기본 설정으로 하나
            만든다 — 조회 실패는 베스트에포트로 무시한다(정적 검증의 외부 충돌
            탐지만 스킵됨).
        run_context: 있으면 각 게이트를 runctx.RunContext.stage()로 감싸고
            아티팩트를 저장한다. None이면 로깅 없이 순수 함수로 동작한다.
    """
    intent = intent.strip()
    if not intent:
        return PipelineResult(decision="ERROR", intent=intent, reason="intent is empty")

    inventory: TopologyInventory = load_topology_inventory(topology)
    topology_prompt = build_topology_prompt(topology)
    endpoint_ips = _host_ip_map(topology)

    if run_context is not None:
        run_context.save_artifact("input_intent", intent)

    existing_flows: Optional[list[dict]] = None
    try:
        client = onos_client or OnosClient()
        existing_flows = client.flows()
    except Exception:
        existing_flows = None  # ONOS에 못 붙어도 파이프라인은 계속 — 외부 충돌 탐지만 스킵.

    prediction: Optional[IntentPrediction] = None
    grounding_report: Optional[ValidationReport] = None
    flow_set: Optional[OnosFlowSet] = None
    static_result: Optional[StaticResult] = None
    repair_feedback: Optional[str] = None
    attempt = 0

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        with _stage(run_context, "parse"):
            try:
                parse_result = parse_intent(
                    intent, model=model, topology_prompt=topology_prompt, repair_feedback=repair_feedback,
                )
            except ValueError as exc:
                return PipelineResult(decision="ERROR", intent=intent, reason=f"parse failed: {exc}", repair_attempts=attempt)
            prediction = parse_result.prediction

        if prediction.status == "rejected":
            assert prediction.rejection is not None
            reason = f"parser rejected: [{prediction.rejection.reason}] {prediction.rejection.detail or ''}"
            if run_context is not None:
                run_context.log_event("intent_rejected", stage="parse", reason=prediction.rejection.reason)
            return PipelineResult(decision="REJECT", intent=intent, reason=reason, repair_attempts=attempt, prediction=prediction)

        if run_context is not None:
            run_context.save_artifact("generated_ir", prediction.model_dump(mode="json"))

        with _stage(run_context, "grounding"):
            grounding_report = verify_program(prediction.program, inventory)
        if not grounding_report.is_valid:
            if attempt >= MAX_REPAIR_ATTEMPTS:
                return PipelineResult(
                    decision="REJECT", intent=intent,
                    reason=f"grounding failed after {MAX_REPAIR_ATTEMPTS} repair attempts",
                    repair_attempts=attempt, prediction=prediction, grounding_report=grounding_report,
                )
            repair_feedback = build_repair_feedback_from_grounding(grounding_report, attempt + 1, MAX_REPAIR_ATTEMPTS)
            continue

        with _stage(run_context, "compile"):
            try:
                flow_set = compile_prediction(prediction, endpoint_ips=endpoint_ips)
            except CompilationError as exc:
                return PipelineResult(
                    decision="ERROR", intent=intent, reason=f"compile failed: {exc}",
                    repair_attempts=attempt, prediction=prediction, grounding_report=grounding_report,
                )
        if run_context is not None:
            run_context.save_artifact("compiled_policy", flow_set.model_dump(mode="json"))

        with _stage(run_context, "static_validation"):
            static_result = static_validate(flow_set.model_dump(), existing_flows=existing_flows)
        if run_context is not None:
            run_context.save_artifact("static_validation", {
                "passed": static_result.passed, "schema_errors": static_result.schema_errors,
                "conflicts": static_result.conflicts, "warnings": static_result.warnings,
            })
        if not static_result.passed:
            if attempt >= MAX_REPAIR_ATTEMPTS:
                return PipelineResult(
                    decision="REJECT", intent=intent,
                    reason=f"static validation failed after {MAX_REPAIR_ATTEMPTS} repair attempts",
                    repair_attempts=attempt, prediction=prediction,
                    grounding_report=grounding_report, flow_set=flow_set, static_result=static_result,
                )
            repair_feedback = build_repair_feedback_from_static(static_result, attempt + 1, MAX_REPAIR_ATTEMPTS)
            continue

        break

    assert flow_set is not None and static_result is not None  # 위 for 루프가 break로만 여기 도달

    with _stage(run_context, "twin"):
        if skip_twin:
            twin_result = TwinResult(status="skipped", reason="skip_twin=True")
        else:
            twin_result = TwinVerifier().verify(flow_set, checks=twin_checks)
    if run_context is not None:
        run_context.save_artifact("twin_test_results", {
            "status": twin_result.status, "reason": twin_result.reason,
            "checks": twin_result.checks, "evidence": twin_result.evidence,
        })

    if twin_result.status == "passed":
        decision: Decision = "APPROVE"
    elif twin_result.status == "skipped":
        decision = "APPROVE_WITHOUT_TWIN"
    else:
        decision = "REJECT"

    return PipelineResult(
        decision=decision, intent=intent, reason=twin_result.summary(), repair_attempts=attempt,
        prediction=prediction, grounding_report=grounding_report, flow_set=flow_set,
        static_result=static_result, twin_result=twin_result,
    )


def _stage(run_context: Optional[RunContext], name: str):
    """run_context가 있으면 .stage(name)을, 없으면 아무 것도 안 하는 컨텍스트를 반환한다."""
    if run_context is not None:
        return run_context.stage(name)
    from contextlib import nullcontext

    return nullcontext()
