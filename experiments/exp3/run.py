"""experiments/exp3/run.py — Exp-3 ablation arm 러너.

원본: 없음 (신규, 이슈 #37 / docs/plan.md Exp-3 참고). 5개 `--arm` 모드:

  capture-ir     케이스마다 parse_intent()를 정확히 1번 불러 IntentPrediction을
                 저장한다 — no_grounding/no_static/full 세 arm의 attempt-0가
                 공유하는 원본. LLM 비결정성이 arm 비교를 오염시키지 않게 하는
                 장치(docs/plan.md "LLM을 arm마다 새로 부를지" 참고).
  no-system      `direct_flow` 프롬프트로 LLM이 FlowRule JSON을 직접 낸다 —
                 IR/그라운딩/컴파일러가 아예 없는 별도 경로라 capture-ir와 무관.
  no-grounding   `run_pipeline(..., skip_grounding=True, initial_prediction=...)`.
  no-static      `run_pipeline(..., skip_static_validation=True, initial_prediction=...)`.
  full           `run_pipeline(..., initial_prediction=...)` 그대로(그라운딩+
                 정적검증+repair 전부 활성).

`--tier B`(실 Digital Twin)는 no-system/full만 허용한다 — no-grounding/
no-static은 Tier A의 정적 레벨 진단만으로 게이트의 기여도가 이미 드러나므로
twin까지 반복할 필요가 없다(docs/plan.md 참고). Tier B의 full은 Tier A의
capture-ir 로그를 재사용해 그라운딩/컴파일/정적검증을 다시 결정론적으로
계산하고 twin만 새로 돌린다(새 LLM 콜 없음) — no-system은 Tier A no-system
로그의 raw FlowRule JSON을 그대로 꺼내 TwinVerifier를 직접 부른다
(run_pipeline을 거치지 않음 — IR이 없어 못 태운다).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tiger_sdn.ir import IntentPrediction  # noqa: E402
from tiger_sdn.orchestrate.pipeline import PipelineResult, run_pipeline  # noqa: E402
from tiger_sdn.parse.llm_client import call_llm  # noqa: E402
from tiger_sdn.parse.parser import get_prompt, parse_intent  # noqa: E402
from tiger_sdn.parse.grounding_prompt import build_topology_prompt  # noqa: E402
from tiger_sdn.twin import REACH_ONLY, TwinVerifier  # noqa: E402
from tiger_sdn.verify import (  # noqa: E402
    TopologyInventory,
    load_topology_inventory,
    validate as static_validate,
    verify_program,
)

from e3_evaluation import (  # noqa: E402
    Arm,
    CaptureRecord,
    E3Case,
    E3Result,
    load_capture_records,
    load_cases,
    load_results,
    load_tier_b_case_ids,
)

GATED_ARMS = {"no_grounding", "no_static", "full"}


def _model_slug(model: str) -> str:
    return model.replace(".", "").replace(":", "-").replace("/", "-")


# ── capture-ir ────────────────────────────────────────────────────────────

def run_capture(case: E3Case, *, model: str, topology_prompt: str, run_id: str) -> CaptureRecord:
    try:
        parse_result = parse_intent(
            case.intent_text, model=model, topology_prompt=topology_prompt, repair_feedback=None,
        )
    except ValueError as exc:
        return CaptureRecord(case_id=case.case_id, model=model, run_id=run_id, error_kind="transport", error=str(exc))

    response = parse_result.llm_response
    return CaptureRecord(
        case_id=case.case_id, model=model, run_id=run_id,
        prediction=parse_result.prediction.model_dump(mode="json"),
        raw_content=response.raw_text, latency_ms=response.latency_ms,
        input_tokens=response.input_tokens, output_tokens=response.output_tokens,
        error_kind=response.error_kind, error=response.error_message,
    )


# ── no-system ─────────────────────────────────────────────────────────────

def run_no_system(case: E3Case, *, model: str, run_id: str, tier: str) -> E3Result:
    """direct_flow 프롬프트로 LLM이 FlowRule을 직접 낸다 — IR/그라운딩/컴파일러
    없음. static 스키마/충돌 검사는 게이팅이 아니라 진단용으로만 돌린다
    (`prompts/direct_flow.md`의 출력 형식이 이미 static.py의 스키마와 맞는다)."""
    system = get_prompt("direct_flow")
    response = call_llm(model, system=system, user=case.intent_text)

    if response.parsed is None:
        return E3Result(
            case_id=case.case_id, category=case.category, arm="no_system", tier=tier, model=model, run_id=run_id,
            decision="ERROR", stage_settled="llm_transport_error",
            reason=f"LLM call failed ({response.error_kind}): {response.error_message}",
            grounding_applicable=False, error=response.error_message,
        )

    parsed = response.parsed
    if parsed.get("status") == "rejected":
        reason = f"LLM rejected: [{parsed.get('rejection_reason', '')}] {parsed.get('rejection_detail', '')}".strip()
        return E3Result(
            case_id=case.case_id, category=case.category, arm="no_system", tier=tier, model=model, run_id=run_id,
            decision="REJECT", stage_settled="llm_self_reject", reason=reason, grounding_applicable=False,
        )

    flows = parsed if "flows" in parsed else {"flows": []}
    diag = static_validate(flows, existing_flows=None)
    return E3Result(
        case_id=case.case_id, category=case.category, arm="no_system", tier=tier, model=model, run_id=run_id,
        decision="APPROVE_WITHOUT_TWIN", stage_settled="approved",
        reason="no system: LLM output trusted as-is (no grounding/compiler/static gate)",
        rule_count=len(flows.get("flows", [])), grounding_applicable=False,
        static_schema_errors=diag.schema_errors, static_conflicts=diag.conflicts, static_warnings=diag.warnings,
        raw_flow_json=flows,
    )


def run_no_system_tier_b(case: E3Case, raw_flow_json: Optional[dict], *, model: str, run_id: str) -> E3Result:
    """Tier A no-system 로그의 raw FlowRule을 그대로 실 twin에 배포한다 —
    run_pipeline()을 거치지 않는다(IR이 없어 못 태운다)."""
    if raw_flow_json is None:
        return E3Result(
            case_id=case.case_id, category=case.category, arm="no_system", tier="B", model=model, run_id=run_id,
            decision="ERROR", stage_settled="llm_transport_error",
            reason="Tier A no-system 로그에 raw_flow_json이 없음(LLM 거부 또는 전송 실패)",
            grounding_applicable=False,
        )

    twin_result = TwinVerifier().verify(raw_flow_json, checks=REACH_ONLY)
    if twin_result.status == "passed":
        decision, stage_settled = "APPROVE", "approved"
    elif twin_result.status == "skipped":
        decision, stage_settled = "APPROVE_WITHOUT_TWIN", "approved"
    else:
        decision = "REJECT"
        stage_settled = "twin_error" if twin_result.status == "error" else "twin_reject"

    return E3Result(
        case_id=case.case_id, category=case.category, arm="no_system", tier="B", model=model, run_id=run_id,
        decision=decision, stage_settled=stage_settled, reason=twin_result.summary(),
        grounding_applicable=False, raw_flow_json=raw_flow_json,
        twin_status=twin_result.status, twin_reason=twin_result.reason,
        twin_checks=twin_result.checks, twin_evidence=twin_result.evidence,
    )


# ── no-grounding / no-static / full (gated arms via run_pipeline) ─────────

def _stamp_intent_action(flow_dict: dict, prediction: IntentPrediction) -> dict:
    """orchestrate.pipeline.run_pipeline()의 static_validate() 호출부와 동일한
    스탬핑 — no-static arm의 진단 재계산이 실제 게이트와 같은 조건으로 채점되게
    한다(verify/static.py의 intent_action 배선, 이슈 #37 참고)."""
    if prediction.status == "accepted":
        if prediction.program.is_compound:
            flow_dict["intent_action"] = "compound"
        elif prediction.program.single.action == "sfc":
            flow_dict["intent_action"] = "sfc"
    return flow_dict


def _stage_settled(result: PipelineResult) -> str:
    if result.prediction is None:
        return "llm_transport_error"
    if result.prediction.status == "rejected":
        return "llm_self_reject"
    if result.decision == "ERROR":
        return "compile_error"
    if result.decision == "REJECT":
        if result.twin_result is not None:
            return "twin_error" if result.twin_result.status == "error" else "twin_reject"
        if result.static_result is not None and not result.static_result.passed:
            return "static_reject"
        return "grounding_reject"
    return "approved"


def _to_e3_result(
    case: E3Case, result: PipelineResult, *, arm: Arm, tier: str, model: str, run_id: str,
    inventory: TopologyInventory, capture_run_id: Optional[str],
) -> E3Result:
    grounding_applicable = result.prediction is not None and result.prediction.status == "accepted"
    grounding_findings: list[dict[str, Any]] = []
    if grounding_applicable:
        if result.grounding_report is not None:
            grounding_findings = [f.model_dump(mode="json") for f in result.grounding_report.findings]
        else:  # skip_grounding=True — 진단용으로 한 번 더 계산
            diag = verify_program(result.prediction.program, inventory)
            grounding_findings = [f.model_dump(mode="json") for f in diag.findings]

    static_schema_errors: list[str] = []
    static_conflicts: list[dict[str, Any]] = []
    static_warnings: list[str] = []
    if result.static_result is not None:
        static_schema_errors = result.static_result.schema_errors
        static_conflicts = result.static_result.conflicts
        static_warnings = result.static_result.warnings
    elif result.flow_set is not None and result.prediction is not None:  # skip_static_validation=True
        flow_dict = _stamp_intent_action(result.flow_set.model_dump(), result.prediction)
        diag = static_validate(flow_dict, existing_flows=None)
        static_schema_errors, static_conflicts, static_warnings = diag.schema_errors, diag.conflicts, diag.warnings

    rule_count = len(result.prediction.program.rules) if grounding_applicable else None

    return E3Result(
        case_id=case.case_id, category=case.category, arm=arm, tier=tier, model=model, run_id=run_id,
        decision=result.decision, stage_settled=_stage_settled(result), reason=result.reason,
        repair_attempts=result.repair_attempts, rule_count=rule_count,
        grounding_applicable=grounding_applicable, grounding_findings=grounding_findings,
        static_schema_errors=static_schema_errors, static_conflicts=static_conflicts, static_warnings=static_warnings,
        twin_status=result.twin_result.status if result.twin_result is not None else None,
        twin_reason=result.twin_result.reason if result.twin_result is not None else None,
        twin_checks=result.twin_result.checks if result.twin_result is not None else None,
        twin_evidence=result.twin_result.evidence if result.twin_result is not None else None,
        capture_run_id=capture_run_id,
    )


def run_gated_arm(
    case: E3Case, captured: Optional[CaptureRecord], *, arm: Arm, tier: str, model: str,
    topology: dict[str, Any], inventory: TopologyInventory, run_id: str,
    max_repair_attempts: Optional[int], skip_twin: bool,
) -> E3Result:
    initial_prediction = None
    if captured is not None and captured.prediction is not None:
        initial_prediction = IntentPrediction.model_validate(captured.prediction)

    kwargs: dict[str, Any] = dict(
        model=model, topology=topology, skip_twin=skip_twin, initial_prediction=initial_prediction,
    )
    if max_repair_attempts is not None:
        kwargs["max_repair_attempts"] = max_repair_attempts
    if arm == "no_grounding":
        kwargs["skip_grounding"] = True
    elif arm == "no_static":
        kwargs["skip_static_validation"] = True

    result = run_pipeline(case.intent_text, **kwargs)
    capture_run_id = captured.run_id if captured is not None else None
    return _to_e3_result(case, result, arm=arm, tier=tier, model=model, run_id=run_id, inventory=inventory, capture_run_id=capture_run_id)


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", required=True, choices=["capture-ir", "no-system", "no-grounding", "no-static", "full"])
    parser.add_argument("--tier", choices=["A", "B"], default="A")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/gold/gold350_eval.jsonl")
    parser.add_argument("--topology", type=Path, default=ROOT / "data/gold/topology_eval.json")
    parser.add_argument("--model", required=True)
    parser.add_argument("--capture-file", type=Path, help="capture-ir 로그 — no-grounding/no-static/full 필수")
    parser.add_argument("--no-system-log", type=Path, help="Tier A no-system 결과 로그 — --arm no-system --tier B 필수")
    parser.add_argument(
        "--case-id-file", type=Path,
        help='{"case_ids": [...]} 형식 파일로 케이스 필터링 — Tier B는 필수(tierB_case_ids.json), '
             "Tier A는 선택(카테고리 고른 파일럿 표본 등에 사용)",
    )
    parser.add_argument("--case-id", help="단일 케이스만(디버그용)")
    parser.add_argument("--limit", type=int, help="파일럿용 — 처음 N개 케이스만")
    parser.add_argument("--max-repair-attempts", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=1, help="Tier A만 유효 — Tier B는 twin이 단일 호스트 순차 실행이라 강제로 1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    arm_key = args.arm.replace("-", "_")
    if arm_key in GATED_ARMS and args.capture_file is None:
        parser.error(f"--arm {args.arm} requires --capture-file")
    if args.tier == "B":
        if arm_key not in ("no_system", "full"):
            parser.error("--tier B only supports --arm no-system or --arm full")
        if args.case_id_file is None:
            parser.error("--tier B requires --case-id-file")
        if arm_key == "no_system" and args.no_system_log is None:
            parser.error("--arm no-system --tier B requires --no-system-log (Tier A's no-system result log)")
    concurrency = 1 if args.tier == "B" else max(1, args.concurrency)

    cases = load_cases(args.dataset)
    if args.case_id:
        cases = [c for c in cases if c.case_id == args.case_id]
    elif args.case_id_file is not None:
        wanted = set(load_tier_b_case_ids(args.case_id_file))
        cases = [c for c in cases if c.case_id in wanted]
    if args.limit:
        cases = cases[: args.limit]

    topology = json.loads(args.topology.read_text(encoding="utf-8"))
    inventory = load_topology_inventory(topology)
    topology_prompt = build_topology_prompt(topology)

    run_id = f"{args.arm}-{args.tier}-{_model_slug(args.model)}-{uuid.uuid4().hex[:8]}"
    print(f"[exp3] arm={args.arm} tier={args.tier} model={args.model} cases={len(cases)} run_id={run_id}")

    captures: dict[str, CaptureRecord] = {}
    if arm_key in GATED_ARMS:
        captures = load_capture_records(args.capture_file)

    no_system_flows: dict[str, Optional[dict]] = {}
    if args.tier == "B" and arm_key == "no_system":
        for r in load_results(args.no_system_log):
            no_system_flows[r.case_id] = r.raw_flow_json

    def process(case: E3Case) -> Any:
        if args.arm == "capture-ir":
            return run_capture(case, model=args.model, topology_prompt=topology_prompt, run_id=run_id)
        if args.arm == "no-system":
            if args.tier == "B":
                return run_no_system_tier_b(case, no_system_flows.get(case.case_id), model=args.model, run_id=run_id)
            return run_no_system(case, model=args.model, run_id=run_id, tier=args.tier)
        return run_gated_arm(
            case, captures.get(case.case_id), arm=arm_key, tier=args.tier, model=args.model,
            topology=topology, inventory=inventory, run_id=run_id,
            max_repair_attempts=args.max_repair_attempts, skip_twin=(args.tier == "A"),
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    n_done = 0
    with args.output.open("w", encoding="utf-8") as fh:
        def _write(record) -> None:
            nonlocal n_done
            with write_lock:
                fh.write(record.model_dump_json() + "\n")
                fh.flush()
                n_done += 1
                if n_done % 10 == 0 or n_done == len(cases):
                    print(f"  [{n_done}/{len(cases)}]")

        if concurrency <= 1:
            for case in cases:
                _write(process(case))
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(process, case): case for case in cases}
                for future in as_completed(futures):
                    _write(future.result())

    print(f"[exp3] wrote {n_done} records to {args.output}")


if __name__ == "__main__":
    main()
