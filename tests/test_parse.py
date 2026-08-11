"""tests/test_parse.py — parse 패키지(자연어 -> IntentPrediction) 단위 테스트.

원본: 없음 (신규, Stage 9 파서 부분). LLM을 실제로 호출하지 않는다 — `call_llm`을
가짜로 바꿔치기해 파서의 분기 로직(자체 거부 감지, gold350 어댑터 변환, 스키마
오류 처리, repair_feedback 전달)만 검증한다.
"""

from __future__ import annotations

import pytest

from tiger_sdn.parse import grounding_prompt, parser
from tiger_sdn.parse.llm_client import LLMResponse


def _fake_response(parsed: dict | None, **overrides) -> LLMResponse:
    defaults = dict(
        raw_text="raw", parsed=parsed, input_tokens=10, output_tokens=5,
        latency_ms=1.0, error_kind=None, error_message=None,
    )
    defaults.update(overrides)
    return LLMResponse(**defaults)


# ── build_topology_prompt ─────────────────────────────────────────────────


def test_build_topology_prompt_lists_hosts_and_switch_ports():
    topology = {
        "entities": [
            {"id": "host:h1", "aliases": ["h1", "10.0.0.1", "10.0.0.1/32"]},
            {"id": "device:s1", "aliases": ["s1", "switch 1", "of:0000000000000001"]},
        ],
        "ports": {"of:0000000000000001": [1, 2, 3]},
    }
    text = grounding_prompt.build_topology_prompt(topology)
    assert "h1=10.0.0.1" in text
    assert "s1 (of:0000000000000001) ports: 1,2,3" in text
    assert "do not invent others" in text


def test_build_topology_prompt_prefers_wiring_over_bare_ports():
    topology = {
        "entities": [{"id": "device:s1", "aliases": ["s1", "of:0000000000000001"]}],
        "ports": {"of:0000000000000001": [1, 2]},
        "wiring": {"s1": {"1": "s2", "2": "h1"}},
    }
    text = grounding_prompt.build_topology_prompt(topology)
    assert "1->s2, 2->h1" in text


# ── build_system_prompt ───────────────────────────────────────────────────


def test_build_system_prompt_puts_topology_before_schema():
    text = parser.build_system_prompt("TOPOLOGY-MARKER")
    assert text.index("TOPOLOGY-MARKER") < text.index("SDN network intent parser")


def test_build_system_prompt_without_topology_is_just_the_base_prompt():
    text = parser.build_system_prompt("")
    assert text.startswith("You are an SDN network intent parser")


# ── parse_intent ───────────────────────────────────────────────────────────


def test_parse_intent_converts_llm_self_rejection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        parser, "call_llm",
        lambda model, *, system, user: _fake_response(
            {"status": "rejected", "rejection_reason": "ambiguous", "rejection_detail": "too vague"}
        ),
    )
    result = parser.parse_intent("make it better", model="fake-model")
    assert result.prediction.status == "rejected"
    assert result.prediction.rejection.reason == "ambiguous"
    assert result.prediction.rejection.detail == "too vague"


def test_parse_intent_normalizes_unrecognized_rejection_reason(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        parser, "call_llm",
        lambda model, *, system, user: _fake_response(
            {"status": "rejected", "rejection_reason": "not_a_real_reason"}
        ),
    )
    result = parser.parse_intent("???", model="fake-model")
    assert result.prediction.rejection.reason == "ambiguous"


def test_parse_intent_accepts_valid_rules_via_gold350_adapter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        parser, "call_llm",
        lambda model, *, system, user: _fake_response(
            {
                "rules": [
                    {
                        "action": "block",
                        "intent_type": "security",
                        "selector": {"source": {"host": "h1"}, "destination": {"host": "h2"}},
                        "enforcement": {"device": "switch 1"},
                    }
                ],
                "description": "block h1 to h2",
            }
        ),
    )
    result = parser.parse_intent("block h1 to h2 on switch 1", model="fake-model")
    assert result.prediction.status == "accepted"
    assert result.prediction.program.single.action == "block"
    assert result.llm_response.input_tokens == 10


def test_parse_intent_rejects_when_rules_array_is_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        parser, "call_llm",
        lambda model, *, system, user: _fake_response({"rules": [], "description": ""}),
    )
    result = parser.parse_intent("nonsense", model="fake-model")
    assert result.prediction.status == "rejected"
    assert result.prediction.rejection.reason == "ambiguous"


def test_parse_intent_rejects_when_rule_fails_ir_validation(monkeypatch: pytest.MonkeyPatch):
    # protocol 없이 dst_port만 지정 — IntentSelector._ports_require_transport 위반.
    monkeypatch.setattr(
        parser, "call_llm",
        lambda model, *, system, user: _fake_response(
            {
                "rules": [
                    {
                        "action": "forward",
                        "selector": {"destination": {"host": "h2"}, "dst_port": 80},
                        "enforcement": {"device": "switch 1"},
                    }
                ],
            }
        ),
    )
    result = parser.parse_intent("forward port 80 to h2", model="fake-model")
    assert result.prediction.status == "rejected"
    assert result.prediction.rejection.reason == "ambiguous"
    assert "IR validation failed" in result.prediction.rejection.detail


def test_parse_intent_raises_on_llm_transport_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        parser, "call_llm",
        lambda model, *, system, user: _fake_response(
            None, error_kind="transport", error_message="connection refused",
        ),
    )
    with pytest.raises(ValueError, match="connection refused"):
        parser.parse_intent("block h1 to h2", model="fake-model")


def test_parse_intent_appends_repair_feedback_to_user_message(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_call_llm(model, *, system, user):
        captured["user"] = user
        return _fake_response({"status": "rejected", "rejection_reason": "ambiguous"})

    monkeypatch.setattr(parser, "call_llm", fake_call_llm)
    parser.parse_intent(
        "block h1 to h2", model="fake-model", repair_feedback="[Repair attempt 1/3] fix this",
    )
    assert captured["user"] == "block h1 to h2\n\n[Repair attempt 1/3] fix this"
