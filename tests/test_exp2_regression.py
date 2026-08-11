"""Exp-2 회귀 테스트.

원본: 없음 (신규). tests/test_exp1_regression.py와 역할이 다르다 — Exp-1은
LLM 호출을 재현하지 않기 위해 커밋된 로그만 재채점하지만, Exp-2는 LLM을 전혀
쓰지 않는 고정 IR conformance 평가라 실행 비용/재현성 문제가 없다
(experiments/exp2/e2_evaluation.py, docs/plan.md Stage 8 "착수 시 결정 사항"
참고). 그래서 두 계층을 모두 검증한다.

  1. `test_score_rescore_matches_committed_report` — experiments/exp1과 같은
     패턴: 커밋된 B1/B2 로그를 score.py로 다시 채점해 커밋된 리포트와
     대조한다. score.py는 로그에 이미 기록된 duration_ms를 그대로 집계할 뿐
     자체적으로 시간을 재지 않으므로, 로그가 고정되어 있으면 전체 리포트가
     바이트 단위로 재현된다 — 타이밍 필드를 예외 처리할 필요가 없다.
  2. `test_run_fresh_reproduces_perfect_b2_precision_recall` — run.py를 새로
     실행해(타이밍은 매번 달라짐) grounding.py/compile/compiler.py 자체의
     정오탐 회귀를 잡는다. 타이밍이 아니라 outcome/findings만 확인한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_PY = ROOT / "experiments" / "exp2" / "run.py"
SCORE_PY = ROOT / "experiments" / "exp2" / "score.py"
DATA_DIR = ROOT / "experiments" / "exp2" / "data"
LOGS_DIR = ROOT / "experiments" / "exp2" / "logs"
REPORTS_DIR = ROOT / "experiments" / "exp2" / "reports"

DATASETS = [
    pytest.param(
        DATA_DIR / "cases.jsonl", DATA_DIR / "topology.json",
        LOGS_DIR / "B1.jsonl", LOGS_DIR / "B2.jsonl",
        REPORTS_DIR / "summary.json", None,
        id="cases",
    ),
    pytest.param(
        DATA_DIR / "cases_sfc_reroute.jsonl", DATA_DIR / "topology_sfc_reroute.json",
        LOGS_DIR / "B1_sfc_reroute.jsonl", LOGS_DIR / "B2_sfc_reroute.jsonl",
        REPORTS_DIR / "sfc_reroute_summary.json",
        "component-level controlled evaluation of the B1-B2 validation boundary on the "
        "65-case sfc/reroute extension (not an end-to-end LLM+IR system comparison; "
        "never merged with the original 48-case report)",
        id="cases_sfc_reroute",
    ),
]


def _diff_leaves(expected, actual, path: str = "") -> list[str]:
    mismatches: list[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected:
            child_path = f"{path}.{key}" if path else key
            if key not in actual:
                mismatches.append(f"{child_path}: missing in actual")
                continue
            mismatches.extend(_diff_leaves(expected[key], actual[key], child_path))
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            mismatches.append(f"{path}: length {len(expected)} != {len(actual)}")
        for i, (e, a) in enumerate(zip(expected, actual)):
            mismatches.extend(_diff_leaves(e, a, f"{path}[{i}]"))
    else:
        if expected != actual:
            mismatches.append(f"{path}: expected={expected!r} actual={actual!r}")
    return mismatches


@pytest.mark.parametrize("dataset, topology, b1_log, b2_log, report, scope_note", DATASETS)
def test_score_rescore_matches_committed_report(tmp_path, dataset, topology, b1_log, b2_log, report, scope_note):
    assert report.exists(), f"missing committed report: {report}"

    output_path = tmp_path / "rescored.json"
    cmd = [
        sys.executable, str(SCORE_PY),
        "--dataset", str(dataset),
        "--output", str(output_path),
        str(b1_log), str(b2_log),
    ]
    if scope_note is not None:
        cmd.extend(["--scope-note", scope_note])
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    expected = json.loads(report.read_text())
    actual = json.loads(output_path.read_text())

    mismatches = _diff_leaves(expected, actual)
    assert mismatches == [], f"{dataset.name}: {len(mismatches)} field mismatch(es): {mismatches[:10]}"


@pytest.mark.parametrize("dataset, topology, b1_log, b2_log, report, scope_note", DATASETS)
def test_run_fresh_reproduces_perfect_b2_precision_recall(tmp_path, dataset, topology, b1_log, b2_log, report, scope_note):
    """run.py를 매번 새로 실행해(타이밍은 매번 다름) grounding.py/compile이
    이 데이터셋에서 여전히 완벽한 B2 정오탐 판정을 내는지 확인한다 — Stage 8
    완료 기준("검증 통과율 리포트")의 핵심 주장이 회귀하지 않는지 지키는
    안전망이다."""
    b1_output = tmp_path / "B1.jsonl"
    b2_output = tmp_path / "B2.jsonl"
    for treatment, output in (("B1", b1_output), ("B2", b2_output)):
        result = subprocess.run(
            [
                sys.executable, str(RUN_PY),
                "--treatment", treatment,
                "--dataset", str(dataset),
                "--topology", str(topology),
                "--output", str(output),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    score_output = tmp_path / "summary.json"
    cmd = [
        sys.executable, str(SCORE_PY),
        "--dataset", str(dataset),
        "--output", str(score_output),
        str(b1_output), str(b2_output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    fresh = json.loads(score_output.read_text())
    committed = json.loads(report.read_text())

    assert fresh["B2"]["any_defect"]["precision"] == 1.0
    assert fresh["B2"]["any_defect"]["recall"] == 1.0
    assert fresh["B2"]["code_mismatch_cases"] == []
    assert fresh["B2"]["any_defect"] == committed["B2"]["any_defect"]
    assert fresh["B2"]["by_category"] == committed["B2"]["by_category"]
    assert fresh["B1"]["any_defect"] == committed["B1"]["any_defect"]
