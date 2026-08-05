"""Exp-1 회귀 테스트 — 커밋된 T-A/B/C/D 로그를 재채점해 커밋된 리포트와 대조한다.

LLM 호출 없음. score.py의 순수 채점 로직만 재실행하므로 수 초 내에 끝난다.
`metadata`(생성 시각, 로그 절대경로)는 실행마다 달라지므로 비교에서 제외하고,
`treatments`(실제 채점 지표)만 재귀적으로 비교한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCORE_PY = ROOT / "experiments" / "exp1" / "score.py"
DATASET = ROOT / "data" / "gold" / "gold350_eval.jsonl"
TOPOLOGY = ROOT / "data" / "gold" / "topology_eval.json"
LOGS_DIR = ROOT / "experiments" / "exp1" / "logs"
REPORTS_DIR = ROOT / "experiments" / "exp1" / "reports"

TREATMENTS = ["T-A", "T-B", "T-C", "T-D"]


def _diff_leaves(expected, actual, path: str = "") -> list[str]:
    """트리를 재귀적으로 비교해 값이 다른 leaf 경로 목록을 반환한다."""
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


@pytest.mark.parametrize("treatment", TREATMENTS)
def test_exp1_rescore_matches_committed_report(tmp_path, treatment):
    committed_report = REPORTS_DIR / f"{treatment}_openrouter_r1_summary.json"
    assert committed_report.exists(), f"missing committed report: {committed_report}"

    output_path = tmp_path / f"{treatment}_rescored.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCORE_PY),
            "--dataset", str(DATASET),
            "--topology", str(TOPOLOGY),
            "--logs", str(LOGS_DIR),
            "--output", str(output_path),
            "--treatment", treatment,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    expected = json.loads(committed_report.read_text())["treatments"]
    actual = json.loads(output_path.read_text())["treatments"]

    mismatches = _diff_leaves(expected, actual)
    assert mismatches == [], f"{treatment}: {len(mismatches)} field mismatch(es): {mismatches[:10]}"
