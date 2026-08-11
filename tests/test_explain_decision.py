"""tests/test_explain_decision.py — explain.decision: 결정론적 최종 판정.

원본: 없음 (신규, Stage 9). 결정 로직은
sdn-intent-framework의 src/xai_pipeline/pipeline/stage5_xai/explainer.py:120-125를
그대로 옮긴 것이므로, 그 세 분기(REJECT/APPROVE_WITHOUT_TWIN/APPROVE)를
표 형태로 검증한다.
"""
from __future__ import annotations

import pytest

from tiger_sdn.explain import build_decision
from tiger_sdn.ir import IntentProgram, IntentRule
from tiger_sdn.twin import TwinResult
from tiger_sdn.verify import StaticResult

PROGRAM = IntentProgram(
    rules=[
        IntentRule(
            action="block",
            selector={"source": {"ip": "10.0.0.1/32"}, "eth_type": "ipv4"},
            enforcement={"device": "switch 1"},
        )
    ]
)
FLOWRULE = {
    "flows": [
        {
            "deviceId": "of:0000000000000001",
            "priority": 40000,
            "timeout": 0,
            "isPermanent": True,
            "selector": {"criteria": [{"type": "IPV4_SRC", "ip": "10.0.0.1/32"}]},
            "treatment": None,
        }
    ]
}


def _static(**overrides) -> StaticResult:
    defaults = dict(passed=True, schema_errors=[], conflicts=[], warnings=[])
    defaults.update(overrides)
    return StaticResult(**defaults)


@pytest.mark.parametrize(
    "static_result,twin_result,expected_decision",
    [
        (_static(), TwinResult(status="passed", checks={"baseline_connectivity": True}), "APPROVE"),
        (_static(), TwinResult(status="skipped", reason="skip_twin option"), "APPROVE_WITHOUT_TWIN"),
        (_static(), TwinResult(status="failed", checks={"intent_check": False}), "REJECT"),
        (_static(), TwinResult(status="error", reason="mininet crashed"), "REJECT"),
        (
            _static(passed=False, conflicts=[{"conflict_type": "Shadowing", "reason": "x"}]),
            TwinResult(status="skipped", reason="static validation failed"),
            "REJECT",
        ),
    ],
)
def test_build_decision_branches(static_result, twin_result, expected_decision):
    report = build_decision("block 10.0.0.1 on switch 1", PROGRAM, FLOWRULE, static_result, twin_result)
    assert report.decision == expected_decision


def test_build_decision_confidence_approve_is_full():
    report = build_decision(
        "intent", PROGRAM, FLOWRULE, _static(), TwinResult(status="passed", checks={"a": True})
    )
    assert report.confidence == 1.0
    assert report.confidence_breakdown == {"static": 1.0, "twin": 1.0}


def test_build_decision_confidence_skipped_twin_uses_static_only():
    report = build_decision(
        "intent", PROGRAM, FLOWRULE, _static(warnings=["redundant rule"]),
        TwinResult(status="skipped", reason="skip_twin option"),
    )
    assert report.confidence == 0.8
    assert report.confidence_breakdown == {"static": 0.8, "twin": 0.7}


def test_build_decision_evidence_has_four_stages():
    report = build_decision(
        "intent", PROGRAM, FLOWRULE, _static(), TwinResult(status="skipped", reason="skip_twin option")
    )
    stages = [e["stage"] for e in report.evidence]
    assert stages == [
        "Stage1_IntentParsing",
        "Stage2_FlowRuleCompile",
        "Stage3_StaticValidation",
        "Stage4_DigitalTwin",
    ]


def test_build_decision_reason_mentions_conflict_types():
    report = build_decision(
        "intent", PROGRAM, FLOWRULE,
        _static(passed=False, conflicts=[{"conflict_type": "Shadowing", "reason": "dup"}]),
        TwinResult(status="skipped", reason="static validation failed"),
    )
    assert "Shadowing" in report.decision_reason
