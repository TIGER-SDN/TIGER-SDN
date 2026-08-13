"""experiments/exp3/e3_evaluation.py — Exp-3 케이스/결과 모델과 cross-arm 채점 로직.

원본: 없음 (신규, 이슈 #37 / docs/plan.md Exp-3 참고). `experiments/exp2/
e2_evaluation.py`의 3분할 관례(모델+로더+채점 함수를 run.py/score.py가 공유)를
그대로 따른다.

Exp-1(파서만)·Exp-2(컴파일러+검증기만, LLM 없음) 둘 다 "실제 LLM 출력이 이
파이프라인의 각 게이트를 통과하는가", "게이트 하나를 빼면 뭐가 달라지는가"를
답하지 못한다. Exp-3는 같은 케이스를 4개 arm(no_system/no_grounding/no_static/
full)에 태워 그 차이를 직접 잰다 — `E3Result.arm`이 비교축이다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from tiger_sdn.ir import StrictModel

__all__ = [
    "Arm",
    "CaptureRecord",
    "E3Case",
    "E3Result",
    "load_cases",
    "load_capture_records",
    "load_results",
    "load_tier_b_case_ids",
    "validate_results",
    "stage_contribution",
    "per_arm_summary",
    "no_system_vs_full_headline",
    "tier_b_headline",
]

Arm = Literal["no_system", "no_grounding", "no_static", "full"]
Tier = Literal["A", "B"]
Decision = Literal["APPROVE", "APPROVE_WITHOUT_TWIN", "REJECT", "ERROR"]
StageSettled = Literal[
    "llm_transport_error", "llm_self_reject", "grounding_reject",
    "compile_error", "static_reject", "twin_reject", "twin_error", "approved",
]


class E3Case(StrictModel):
    """data/gold/gold350_eval.jsonl의 한 줄 — 스코어링 스키마 그대로, 변환 없음."""

    case_id: str
    category: Literal[
        "forwarding", "security", "qos", "sfc", "reroute", "compound", "ambiguous_unsupported",
    ]
    intent_text: str
    rejection_type: str | None = None
    gold: dict[str, Any]


class CaptureRecord(StrictModel):
    """`--arm capture-ir`가 케이스당 1번 부른 attempt-0 파싱 결과.

    `no_grounding`/`no_static`/`full` arm이 이 로그를 읽어
    `run_pipeline(..., initial_prediction=...)`에 그대로 넘긴다(LLM 비결정성이
    arm 비교를 오염시키지 않도록 — docs/plan.md Exp-3 "LLM을 arm마다 새로
    부를지" 참고). `prediction`은 파싱이 성공했을 때만 채워진다 — LLM 전송
    자체가 실패하면 None으로 남고 error_kind/error에 사유가 남는다.
    """

    case_id: str
    model: str
    run_id: str
    prediction: dict[str, Any] | None = None
    raw_content: str | None = None
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error_kind: str | None = None
    error: str | None = None


class E3Result(StrictModel):
    """arm 하나, 케이스 하나의 최종 결과. `stage_contribution()`/`*_headline()`이
    이 필드들로 cross-arm 비교를 계산한다.
    """

    case_id: str
    category: str
    arm: Arm
    tier: Tier
    model: str
    run_id: str
    decision: Decision
    stage_settled: StageSettled
    reason: str
    repair_attempts: int = 0
    rule_count: int | None = None
    # 진단 필드 — 그 arm에서 게이트가 실제로 돌았으면 PipelineResult 그대로,
    # 게이트를 스킵한 arm이어도 비교를 위해 별도로 같은 검사를 한 번 더 돌려서
    # 채운다(run.py 참고). no_system은 IR이 없어 grounding_applicable=False.
    grounding_applicable: bool = True
    grounding_findings: list[dict[str, Any]] = Field(default_factory=list)
    static_schema_errors: list[str] = Field(default_factory=list)
    static_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    static_warnings: list[str] = Field(default_factory=list)
    # Tier B 전용
    twin_status: str | None = None
    twin_reason: str | None = None
    twin_checks: dict[str, Any] | None = None
    twin_evidence: dict[str, Any] | None = None
    # capture-ir 재생/원본 보존
    capture_run_id: str | None = None
    raw_flow_json: dict[str, Any] | None = None
    error: str | None = None


def load_cases(path: Path) -> list[E3Case]:
    return [
        E3Case.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_capture_records(path: Path) -> dict[str, CaptureRecord]:
    """capture-ir 로그를 case_id -> CaptureRecord로 읽는다."""
    records = [
        CaptureRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_id = {r.case_id: r for r in records}
    if len(by_id) != len(records):
        raise ValueError(f"{path}: duplicate case_id in capture log")
    return by_id


def load_results(path: Path) -> list[E3Result]:
    return [
        E3Result.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_tier_b_case_ids(path: Path) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))["case_ids"]


def validate_results(cases: list[E3Case], results: list[E3Result]) -> list[E3Result]:
    """채점 전 fail-closed 무결성 검사 — experiments/exp2/e2_evaluation.py의
    validate_results()와 같은 이유(불완전/중복 로그가 조용히 지표를 왜곡하는 걸
    막는다)."""
    case_ids = [c.case_id for c in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate case_id in dataset")

    result_ids = [r.case_id for r in results]
    if len(set(result_ids)) != len(result_ids):
        raise ValueError("duplicate case_id in result log")

    if set(result_ids) != set(case_ids):
        missing = sorted(set(case_ids) - set(result_ids))
        extra = sorted(set(result_ids) - set(case_ids))
        raise ValueError(f"result log does not match dataset 1:1: missing={missing} extra={extra}")

    arms = {r.arm for r in results}
    if len(arms) > 1:
        raise ValueError(f"result log mixes arms, expected exactly one: {sorted(arms)}")

    tiers = {r.tier for r in results}
    if len(tiers) > 1:
        raise ValueError(f"result log mixes tiers, expected exactly one: {sorted(tiers)}")

    return results


def per_arm_summary(cases: list[E3Case], results: list[E3Result]) -> dict[str, Any]:
    """decision 분포, gold accept/reject 대비 정확도, repair_attempts 통계."""
    results = validate_results(cases, results)
    by_id = {r.case_id: r for r in results}

    decision_counts: dict[str, int] = {}
    for r in results:
        decision_counts[r.decision] = decision_counts.get(r.decision, 0) + 1

    tp = fp = fn = tn = 0
    for case in cases:
        gold_accepted = case.gold.get("status") == "accepted"
        predicted_accepted = by_id[case.case_id].decision in ("APPROVE", "APPROVE_WITHOUT_TWIN")
        if gold_accepted and predicted_accepted:
            tp += 1
        elif gold_accepted and not predicted_accepted:
            fn += 1
        elif not gold_accepted and predicted_accepted:
            fp += 1
        else:
            tn += 1

    repair_attempts = [r.repair_attempts for r in results]
    stage_counts: dict[str, int] = {}
    for r in results:
        stage_counts[r.stage_settled] = stage_counts.get(r.stage_settled, 0) + 1

    return {
        "n_cases": len(results),
        "decision_counts": decision_counts,
        "stage_settled_counts": stage_counts,
        "accept_vs_gold": {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if (tp + fp) else None,
            "recall": tp / (tp + fn) if (tp + fn) else None,
            "accuracy": (tp + tn) / len(results) if results else None,
        },
        "repair_attempts": {
            "mean": sum(repair_attempts) / len(repair_attempts) if repair_attempts else None,
            "max": max(repair_attempts) if repair_attempts else None,
            "rescued": sum(1 for r in results if r.repair_attempts > 0 and r.decision in ("APPROVE", "APPROVE_WITHOUT_TWIN")),
        },
    }


def stage_contribution(
    no_gate_results: list[E3Result], full_results: list[E3Result], *, diag_field: str,
) -> dict[str, Any]:
    """"A(게이트를 뺀 arm)가 조용히 승인했을 케이스 중 B(full)가 실제로 막은 비율."

    `diag_field`는 "grounding_findings" 또는 "static_conflicts" — 그 arm에서
    게이트가 안 돌았어도 run.py가 진단용으로 채워둔 값이라, "게이트가 있었으면
    뭘 잡았을지"를 그대로 쓸 수 있다.
    """
    by_id_full = {r.case_id: r for r in full_results}
    would_silently_approve = [
        r.case_id for r in no_gate_results
        if r.decision in ("APPROVE", "APPROVE_WITHOUT_TWIN") and getattr(r, diag_field)
    ]
    caught_by_full = [
        case_id for case_id in would_silently_approve
        if by_id_full[case_id].decision not in ("APPROVE", "APPROVE_WITHOUT_TWIN")
    ]
    return {
        "silently_approved_count": len(would_silently_approve),
        "caught_by_full_count": len(caught_by_full),
        "marginal_catch_rate": (
            len(caught_by_full) / len(would_silently_approve) if would_silently_approve else None
        ),
        "case_ids": would_silently_approve,
    }


def no_system_vs_full_headline(cases: list[E3Case], no_system: list[E3Result], full: list[E3Result]) -> dict[str, Any]:
    """No-System과 Full 각각의 오탐(gold 거부인데 승인)/오거부(gold 승인인데 거부) 건수 비교."""
    by_case = {c.case_id: c for c in cases}

    def _false_rates(results: list[E3Result]) -> dict[str, Any]:
        by_id = {r.case_id: r for r in results}
        false_approve = [
            cid for cid, c in by_case.items()
            if c.gold.get("status") != "accepted" and by_id[cid].decision in ("APPROVE", "APPROVE_WITHOUT_TWIN")
        ]
        false_reject = [
            cid for cid, c in by_case.items()
            if c.gold.get("status") == "accepted" and by_id[cid].decision not in ("APPROVE", "APPROVE_WITHOUT_TWIN")
        ]
        return {"false_approve_count": len(false_approve), "false_reject_count": len(false_reject)}

    return {"no_system": _false_rates(no_system), "full": _false_rates(full)}


def tier_b_headline(no_system: list[E3Result], full: list[E3Result]) -> dict[str, Any]:
    """Tier B 핵심 지표 — arm별 twin 통과율 + No-System에서 Full로 갈 때
    FAIL(또는 ERROR)→PASS로 전환되는 케이스 목록(페어드 지표)."""

    def _twin_pass_rate(results: list[E3Result]) -> dict[str, Any]:
        reached = [r for r in results if r.twin_status is not None]
        passed = [r for r in reached if r.twin_status == "passed"]
        return {
            "n_cases": len(results),
            "reached_twin": len(reached),
            "passed": len(passed),
            "pass_rate": len(passed) / len(reached) if reached else None,
        }

    by_id_no_system = {r.case_id: r for r in no_system}
    by_id_full = {r.case_id: r for r in full}
    flipped_fail_to_pass = [
        case_id for case_id, r in by_id_no_system.items()
        if r.twin_status in ("failed", "error")
        and case_id in by_id_full and by_id_full[case_id].twin_status == "passed"
    ]

    return {
        "no_system": _twin_pass_rate(no_system),
        "full": _twin_pass_rate(full),
        "flipped_fail_to_pass": flipped_fail_to_pass,
        "flipped_fail_to_pass_count": len(flipped_fail_to_pass),
    }
