"""experiments/exp2/e2_evaluation.py — Exp-2 케이스/결과 모델과 채점 로직.

원본: sdn-intent-framework의 research/safe_intent_sdn/e2_evaluation.py.
`.intent_ir`/`.validator`(연구 트랙 스키마)를 `tiger_sdn.ir`/`tiger_sdn.verify`
(통합 IR)로 바꾼 것 외에는 스코어링 로직 자체는 그대로다. `FindingCategory`가
"path"까지 포함하는 4종이라는 점도 원본과 동일(원본 스키마도 4종이었다 —
Stage 6이 한동안 3종으로 줄여뒀던 것을 Stage 8에서 다시 채웠다,
`tiger_sdn/verify/grounding.py` 참고).

컴포넌트 레벨, 고정 IR conformance 평가다 — 케이스는 gold/직접 작성한 Intent IR
픽스처이지 LLM 출력이 아니므로, 여기서 B1/B2가 재는 것은 컴파일러-검증기
경계(RQ2)뿐이지 end-to-end LLM+IR 시스템 비교가 아니다. `docs/plan.md` Stage 8
"착수 시 결정 사항" 참고.
"""
from __future__ import annotations

from statistics import mean, median
from typing import Any, Callable, Iterable, Literal, get_args

from pydantic import Field

from tiger_sdn.ir import IntentProgram, StrictModel
from tiger_sdn.verify import FindingCategory, ValidationFinding

__all__ = [
    "E2Case",
    "E2Result",
    "validate_results",
    "score_treatment",
    "compute_validator_overhead",
]


class E2Case(StrictModel):
    id: str
    category: Literal["clean", "reference", "conflict", "feasibility", "multi", "path"]
    expected_findings: list[FindingCategory] = Field(default_factory=list)
    expected_codes: list[str] = Field(default_factory=list)
    program: IntentProgram


class E2Result(StrictModel):
    case_id: str
    treatment: Literal["B1", "B2"]
    outcome: Literal["accepted", "rejected"]
    findings: list[ValidationFinding] = Field(default_factory=list)
    rejection_stage: Literal["validator", "compiler"] | None = None
    error: str | None = None
    # 반복 측정(warmed-up)의 중앙값 — experiments/exp2/run.py 참고.
    # duration_ms는 실제로 실행된 단계들에 걸친 관측 종단 지연이지, 단계별로
    # 격리된 "검증기 오버헤드" 측정이 아니다 — B2는 검증기가 거부하면 컴파일러를
    # 아예 실행하지 않는다. 오버헤드 주장에는 compute_validator_overhead()를 쓴다.
    validator_duration_ms: float | None = Field(default=None, ge=0)
    compiler_duration_ms: float | None = Field(default=None, ge=0)
    duration_ms: float = Field(ge=0)


def validate_results(cases: Iterable[E2Case], results: Iterable[E2Result]) -> list[E2Result]:
    """채점 전에 로그 무결성 문제를 fail-closed로 먼저 걸러낸다.

    불완전하거나 손상된 실행이 조용히 precision/recall을 부풀리는 실패
    양상을 정확히 막는다: 누락된 케이스(과거엔 세지 않고 건너뛰었다), 중복
    case id(과거엔 마지막 것이 조용히 이겼다), treatment가 섞인 로그, 그리고
    outcome/rejection_stage/findings/error가 내부적으로 모순인 결과 — findings
    없이 "validator"에서 거부된 결과가 outcome만으로 findings 뒷받침 없이
    any_defect recall을 부풀리는 경우까지 포함한다.
    """
    cases = list(cases)
    results = list(results)

    case_ids = [c.id for c in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate case id in dataset")

    result_ids = [r.case_id for r in results]
    if len(set(result_ids)) != len(result_ids):
        raise ValueError("duplicate case_id in result log")

    if set(result_ids) != set(case_ids):
        missing = sorted(set(case_ids) - set(result_ids))
        extra = sorted(set(result_ids) - set(case_ids))
        raise ValueError(f"result log does not match dataset 1:1: missing={missing} extra={extra}")

    treatments = {r.treatment for r in results}
    if len(treatments) > 1:
        raise ValueError(f"result log mixes treatments, expected exactly one: {sorted(treatments)}")

    for r in results:
        if r.outcome == "accepted":
            if r.rejection_stage is not None or r.findings or r.error is not None:
                raise ValueError(f"{r.case_id}: accepted result carries rejection metadata")
        elif r.rejection_stage == "validator":
            if r.treatment != "B2":
                raise ValueError(f"{r.case_id}: only B2 can reject at the validator stage")
            if not r.findings:
                raise ValueError(f"{r.case_id}: validator rejection must carry at least one finding")
            if r.error is not None:
                raise ValueError(f"{r.case_id}: validator rejection must not carry a compiler error")
        elif r.rejection_stage == "compiler":
            if r.findings:
                raise ValueError(f"{r.case_id}: compiler rejection must not carry categorized findings")
            if not r.error:
                raise ValueError(f"{r.case_id}: compiler rejection must carry a non-empty error message")
        else:
            raise ValueError(f"{r.case_id}: rejected result is missing rejection_stage")

    return results


def _confusion(
    cases: list[E2Case],
    by_id: dict[str, E2Result],
    expected_fn: Callable[[E2Case], bool],
    actual_fn: Callable[[E2Result], bool],
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for case in cases:
        expected, actual = expected_fn(case), actual_fn(by_id[case.id])
        if expected and actual:
            tp += 1
        elif expected and not actual:
            fn += 1
        elif not expected and actual:
            fp += 1
        else:
            tn += 1
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "recall": tp / (tp + fn) if (tp + fn) else None,
    }


def score_treatment(cases: Iterable[E2Case], results: Iterable[E2Result]) -> dict[str, Any]:
    """하나의 treatment 로그를 데이터셋의 expected_findings 기준으로 채점한다.

    `validate_results`를 먼저 호출하므로, 불완전/중복/모순된 로그는 결함을
    과소평가하는 대신 예외를 낸다.

    `any_defect`는 B1/B2 어느 쪽에도 비교 가능하다(카테고리 분류 존재 여부와
    무관하게 거부 여부만 본다). `defect_positive_total`/`rejected_count`는
    "N/25건 거부" 같은 원시 카운트를 그대로 노출한다.

    `unknown_host_subset`은 B1의 컴파일러가 host->IP 리졸버가 있어야 실행
    자체가 되는 부작용으로 우연히 잡아내는 결함(미매핑 host alias) 하나만
    떼어 본다 — 설계된 검증 기능이 아니다.

    `by_category`는 케이스 레벨 카테고리 탐지다 — 특정 code/rule_indices가
    맞았는지와 무관하게, 그 케이스에서 카테고리 X 태그가 붙은 finding이
    하나라도 있으면 히트로 센다. 카테고리화된 findings를 내는 유일한
    treatment인 B2에서만 의미가 있다. `code_mismatch_cases`는 별도의 더 엄격한
    진단이다(precision/recall에 포함되지 않음) — 카테고리 레벨 히트는
    맞았지만 실제 finding code가 expected_codes와 다른 케이스들을 나열한다.
    """
    cases = list(cases)
    results = list(results)
    validate_results(cases, results)
    by_id = {r.case_id: r for r in results}
    treatment = results[0].treatment if results else None

    defect_positive = [c for c in cases if c.expected_findings]
    any_defect = _confusion(
        cases, by_id,
        lambda case: bool(case.expected_findings),
        lambda result: result.outcome == "rejected",
    )
    report: dict[str, Any] = {
        "any_defect": any_defect,
        "defect_positive_total": len(defect_positive),
        "true_negative_total": len(cases) - len(defect_positive),
        "rejected_count": any_defect["tp"],
    }

    host_subset = [c for c in cases if c.expected_codes == ["unknown_host"]]
    if host_subset:
        report["unknown_host_subset"] = _confusion(
            host_subset, by_id, lambda case: True, lambda result: result.outcome == "rejected",
        )

    # 실제 실행된 단계들에 걸친 관측 종단 케이스당 지연 — 단계별로 격리된
    # 오버헤드 측정이 아니다. compute_validator_overhead()를 대신 쓴다.
    durations = [r.duration_ms for r in results]
    report["observed_latency_ms"] = {
        "mean": mean(durations), "median": median(durations), "min": min(durations), "max": max(durations), "total": sum(durations),
    }

    if treatment == "B2":
        report["by_category"] = {
            category: _confusion(
                cases, by_id,
                lambda case, category=category: category in case.expected_findings,
                lambda result, category=category: category in {f.category for f in result.findings},
            )
            for category in get_args(FindingCategory)
        }
        report["code_mismatch_cases"] = [
            case.id
            for case in cases
            if case.expected_codes
            and {f.code for f in by_id[case.id].findings} != set(case.expected_codes)
        ]

    return report


def compute_validator_overhead(
    cases: Iterable[E2Case], b1_results: Iterable[E2Result], b2_results: Iterable[E2Result]
) -> dict[str, Any]:
    """검증기가 추가한 지연을 *공통 경로에서만* 격리한다.

    결함-양성 케이스는 컴파일러 대신 검증기에서 조기 반환하므로, B2의 전체
    평균 지연이 B1보다 낮게 나올 수 있다 — 이는 결함을 일찍 잡아낸 실질적
    효과이지 검증기 자체의 비용에 대한 증거가 아니다. 비용을 격리하려면
    참 음성 케이스(`expected_findings == []`)로 비교를 제한한다 — B1과 B2
    모두 이 케이스들 전부에서 컴파일러를 실행하므로, 이 부분집합에서 B2가
    쓰는 추가 시간이 정확히 B1 기준선 대비 검증기의 추가 오버헤드다.
    """
    cases = list(cases)
    b1_results, b2_results = list(b1_results), list(b2_results)
    validate_results(cases, b1_results)
    validate_results(cases, b2_results)
    b1_by_id = {r.case_id: r for r in b1_results}
    b2_by_id = {r.case_id: r for r in b2_results}

    common_path_ids = [c.id for c in cases if not c.expected_findings]
    b1_totals = [b1_by_id[i].duration_ms for i in common_path_ids]
    b2_validator = [b2_by_id[i].validator_duration_ms for i in common_path_ids]
    b2_compiler = [b2_by_id[i].compiler_duration_ms for i in common_path_ids]
    b2_totals = [b2_by_id[i].duration_ms for i in common_path_ids]

    def _stats(values: list[float]) -> dict[str, float]:
        return {"mean": mean(values), "median": median(values)}

    return {
        "case_count": len(common_path_ids),
        "b1_compiler_only_ms": _stats(b1_totals),
        "b2_validator_ms": _stats(b2_validator),
        "b2_compiler_ms": _stats(b2_compiler),
        "b2_total_ms": _stats(b2_totals),
        "added_overhead_ms": {
            "mean": mean(b2_totals) - mean(b1_totals),
            "median": median(b2_totals) - median(b1_totals),
        },
    }
