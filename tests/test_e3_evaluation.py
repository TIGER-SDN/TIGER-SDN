"""tests/test_e3_evaluation.py — experiments/exp3/e3_evaluation.py의 순수 함수 단위 테스트.

원본: 없음 (신규, 이슈 #37 참고). `stage_contribution()`을 집중적으로 다룬다 —
qwen 350케이스 전수 실행에서 "REJECT로 끝난 것만 catch로 센다"는 첫 구현이
그라운딩/정적검증의 실제 기여도를 심하게 과소평가한다는 게 드러났다: repair
loop가 게이트의 피드백을 받아 LLM이 문제를 실제로 고치고 승인으로 끝나는
경우가 있는데, 그것도 "그 게이트가 없었으면 조용히 나갔을 문제를 막았다"는
점에서 동일한 기여다. 이 테스트가 그 세 갈래(reject/fixed/not_caught) 분류를
고정한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "exp3"))

from e3_evaluation import E3Result, stage_contribution  # noqa: E402


def _result(case_id: str, arm: str, decision: str, findings: list | None = None) -> E3Result:
    return E3Result(
        case_id=case_id, category="forwarding", arm=arm, tier="A", model="fake-model", run_id="r1",
        decision=decision, stage_settled="approved" if decision != "REJECT" else "grounding_reject",
        reason="x", grounding_findings=findings or [],
    )


def test_rejected_by_full_counts_as_caught():
    no_gate = [_result("C1", "no_grounding", "APPROVE_WITHOUT_TWIN", findings=[{"code": "unknown_device"}])]
    full = [_result("C1", "full", "REJECT")]
    report = stage_contribution(no_gate, full, diag_field="grounding_findings")
    assert report["silently_approved_count"] == 1
    assert report["rejected_by_full_count"] == 1
    assert report["fixed_by_full_count"] == 0
    assert report["not_caught_by_full_count"] == 0
    assert report["real_catch_rate"] == 1.0
    assert report["case_ids"]["rejected"] == ["C1"]


def test_repair_fixed_case_counts_as_caught_not_missed():
    """full이 REJECT가 아니라 APPROVE로 끝나도, repair가 그 문제를 실제로
    없앴으면(같은 finding이 full 자신의 진단에 더는 없으면) 잡은 것으로 센다."""
    no_gate = [_result("C1", "no_grounding", "APPROVE_WITHOUT_TWIN", findings=[{"code": "missing_device"}])]
    full = [_result("C1", "full", "APPROVE_WITHOUT_TWIN", findings=[])]  # repair가 고쳐서 finding 사라짐
    report = stage_contribution(no_gate, full, diag_field="grounding_findings")
    assert report["rejected_by_full_count"] == 0
    assert report["fixed_by_full_count"] == 1
    assert report["not_caught_by_full_count"] == 0
    assert report["real_catch_rate"] == 1.0
    assert report["case_ids"]["fixed"] == ["C1"]


def test_finding_still_present_in_full_counts_as_not_caught():
    """full이 APPROVE인데 같은 finding이 여전히 남아있으면(예: 경고 전용
    conflict라 게이트를 안 막는 경우) 진짜로 못 잡은 것 — 0으로 센다."""
    no_gate = [_result("C1", "no_grounding", "APPROVE_WITHOUT_TWIN", findings=[{"code": "unknown_device"}])]
    full = [_result("C1", "full", "APPROVE_WITHOUT_TWIN", findings=[{"code": "unknown_device"}])]
    report = stage_contribution(no_gate, full, diag_field="grounding_findings")
    assert report["rejected_by_full_count"] == 0
    assert report["fixed_by_full_count"] == 0
    assert report["not_caught_by_full_count"] == 1
    assert report["real_catch_rate"] == 0.0
    assert report["case_ids"]["not_caught"] == ["C1"]


def test_mixed_batch_matches_qwen_350_case_observation():
    """qwen 350케이스 전수 실행에서 실제로 관측된 패턴 재현: 그라운딩 3건 중
    1건 reject, 2건 fixed, 0건 not_caught -> real_catch_rate 1.0 (기존
    "REJECT로만 catch를 세는" 구현이었다면 1/3 = 0.333로 나왔을 것)."""
    no_gate = [
        _result("G-CMP-049", "no_grounding", "APPROVE_WITHOUT_TWIN", findings=[{"code": "shadowed_rule"}]),
        _result("G-RRT-043", "no_grounding", "APPROVE_WITHOUT_TWIN", findings=[{"code": "missing_device"}]),
        _result("G-SFC-046", "no_grounding", "APPROVE_WITHOUT_TWIN", findings=[{"code": "path_waypoint_device_mismatch"}]),
    ]
    full = [
        _result("G-CMP-049", "full", "REJECT"),
        _result("G-RRT-043", "full", "APPROVE_WITHOUT_TWIN", findings=[]),
        _result("G-SFC-046", "full", "APPROVE_WITHOUT_TWIN", findings=[]),
    ]
    report = stage_contribution(no_gate, full, diag_field="grounding_findings")
    assert report["silently_approved_count"] == 3
    assert report["rejected_by_full_count"] == 1
    assert report["fixed_by_full_count"] == 2
    assert report["not_caught_by_full_count"] == 0
    assert report["real_catch_rate"] == 1.0


def test_no_silently_approved_cases_yields_null_rate_not_zero_division():
    report = stage_contribution([], [], diag_field="grounding_findings")
    assert report["silently_approved_count"] == 0
    assert report["real_catch_rate"] is None
