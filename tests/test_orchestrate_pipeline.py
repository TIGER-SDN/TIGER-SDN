"""tests/test_orchestrate_pipeline.py — orchestrate.pipeline.run_pipeline 단위 테스트.

원본: 없음 (신규). LLM은 `tiger_sdn.orchestrate.pipeline.parse_intent`를
바꿔치기해 완전히 결정론적으로 만든다 — parse 자체의 LLM 호출 로직은
tests/test_parse.py가 이미 커버한다. 여기서는 오직 파이프라인 배선(그라운딩
->컴파일->정적검증 게이트 순서, repair loop의 재시도/피드백/소진, 컴파일
예외는 재시도하지 않고 즉시 ERROR)만 검증한다.

이 개발 환경(Windows)에는 Mininet이 없으므로 TwinVerifier.verify()는 항상
status="skipped"를 반환한다 — 그래서 성공 경로의 기대 decision은 항상
APPROVE_WITHOUT_TWIN이다(실 twin 검증은 scripts/twin_smoke_test.sh가 커버,
Stage 7 참고).
"""

from __future__ import annotations

import pytest

from tiger_sdn.ir import from_gold350
from tiger_sdn.orchestrate import pipeline as pipeline_module
from tiger_sdn.orchestrate.pipeline import run_pipeline
from tiger_sdn.orchestrate.repair import MAX_REPAIR_ATTEMPTS
from tiger_sdn.parse.parser import ParseResult
from tiger_sdn.parse.llm_client import LLMResponse
from tiger_sdn.runctx import RunContext

TOPOLOGY = {
    "entities": [
        {"id": "host:h1", "aliases": ["h1", "10.0.0.1", "10.0.0.1/32"]},
        {"id": "host:h2", "aliases": ["h2", "10.0.0.2", "10.0.0.2/32"]},
        {"id": "device:s1", "aliases": ["s1", "switch 1", "of:0000000000000001"]},
    ],
    "ports": {"of:0000000000000001": [1, 2, 3, 4]},
}


def _llm_response() -> LLMResponse:
    return LLMResponse("raw", {}, 1, 1, 1.0, None, None)


def _accepted(rules: list[dict]) -> ParseResult:
    prediction = from_gold350({"status": "accepted", "rules": rules})
    return ParseResult(prediction=prediction, llm_response=_llm_response())


def _rejected(reason: str = "ambiguous") -> ParseResult:
    from tiger_sdn.ir import IntentPrediction

    return ParseResult(prediction=IntentPrediction.reject(reason), llm_response=_llm_response())


VALID_FORWARD_RULE = {
    "action": "forward",
    "intent_type": "forwarding",
    "selector": {"source": {"host": "h1"}, "destination": {"host": "h2"}},
    "enforcement": {"device": "switch 1"},
}


class _FakeOnosClient:
    """existing_flows를 흉내내는 가짜 클라이언트 — 실 ONOS 접속을 피한다."""

    def __init__(self, flows: list[dict] | None = None, raise_error: bool = False):
        self._flows = flows or []
        self._raise = raise_error

    def flows(self) -> list[dict]:
        if self._raise:
            raise ConnectionError("no ONOS here")
        return self._flows


def test_happy_path_reaches_approve_without_twin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))
    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(),
    )
    assert result.decision == "APPROVE_WITHOUT_TWIN"
    assert result.repair_attempts == 0
    assert result.flow_set is not None
    assert result.static_result is not None and result.static_result.passed
    assert result.twin_result is not None and result.twin_result.status == "skipped"


def test_empty_intent_is_an_error_without_calling_parse(monkeypatch: pytest.MonkeyPatch):
    called = False

    def fake_parse(intent, **kw):
        nonlocal called
        called = True
        return _accepted([VALID_FORWARD_RULE])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline("   ", model="fake-model", topology=TOPOLOGY, onos_client=_FakeOnosClient())
    assert result.decision == "ERROR"
    assert not called


def test_parser_rejection_short_circuits_to_reject(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _rejected("contradictory"))
    result = run_pipeline("do something impossible", model="fake-model", topology=TOPOLOGY, onos_client=_FakeOnosClient())
    assert result.decision == "REJECT"
    assert "contradictory" in result.reason
    assert result.repair_attempts == 0


def test_grounding_failure_triggers_repair_with_feedback_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    unknown_host_rule = {**VALID_FORWARD_RULE, "selector": {"source": {"host": "h99"}, "destination": {"host": "h2"}}}
    calls: list[str | None] = []

    def fake_parse(intent, *, model, topology_prompt, repair_feedback=None):
        calls.append(repair_feedback)
        if len(calls) == 1:
            return _accepted([unknown_host_rule])
        return _accepted([VALID_FORWARD_RULE])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline("forward from h99 to h2", model="fake-model", topology=TOPOLOGY, onos_client=_FakeOnosClient())

    assert result.decision == "APPROVE_WITHOUT_TWIN"
    assert result.repair_attempts == 1
    assert len(calls) == 2
    assert calls[0] is None
    assert calls[1] is not None and "unknown_host" in calls[1]


def test_grounding_failure_exhausts_repair_attempts_and_rejects(monkeypatch: pytest.MonkeyPatch):
    unknown_host_rule = {**VALID_FORWARD_RULE, "selector": {"source": {"host": "h99"}, "destination": {"host": "h2"}}}
    call_count = 0

    def fake_parse(intent, *, model, topology_prompt, repair_feedback=None):
        nonlocal call_count
        call_count += 1
        return _accepted([unknown_host_rule])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline("forward from h99 to h2", model="fake-model", topology=TOPOLOGY, onos_client=_FakeOnosClient())

    assert result.decision == "REJECT"
    assert result.repair_attempts == MAX_REPAIR_ATTEMPTS
    assert call_count == MAX_REPAIR_ATTEMPTS + 1
    assert not result.grounding_report.is_valid


def test_compile_error_is_not_repaired(monkeypatch: pytest.MonkeyPatch):
    # eth_type=ipv6인데 엔드포인트는 IPv4 호스트 — grounding은 이걸 안 보고
    # (host 존재 자체만 확인) compile._compile_selector가 모순으로 거부한다.
    ipv6_mismatch_rule = {**VALID_FORWARD_RULE, "selector": {**VALID_FORWARD_RULE["selector"], "eth_type": "ipv6"}}
    call_count = 0

    def fake_parse(intent, **kw):
        nonlocal call_count
        call_count += 1
        return _accepted([ipv6_mismatch_rule])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline("forward h1 to h2 as ipv6", model="fake-model", topology=TOPOLOGY, onos_client=_FakeOnosClient())

    assert result.decision == "ERROR"
    assert "compile failed" in result.reason
    assert call_count == 1  # 재시도 안 함 — 원본 main.py의 stage2 동작과 동일
    assert result.grounding_report is not None and result.grounding_report.is_valid


def test_onos_lookup_failure_is_best_effort_and_does_not_block(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))
    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(raise_error=True),
    )
    assert result.decision == "APPROVE_WITHOUT_TWIN"


def test_skip_twin_flag_short_circuits_twin_verification(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))
    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), skip_twin=True,
    )
    assert result.twin_result.reason == "skip_twin=True"
    assert result.decision == "APPROVE_WITHOUT_TWIN"


def test_run_context_captures_artifacts_and_stage_timings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))
    run = RunContext(
        "intent-001", "test-topology", model_name="fake-model", prompt_version="v1", log_dir=tmp_path,
    )
    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), run_context=run,
    )
    run.finish(result.decision)

    assert set(run.manifest.artifacts) == {
        "input_intent", "generated_ir", "compiled_policy", "static_validation", "twin_test_results",
    }
    assert {"parse_ms", "grounding_ms", "compile_ms", "static_validation_ms", "twin_ms"} <= set(
        run.manifest.execution_time
    )
    assert run.manifest.final_decision == "APPROVE_WITHOUT_TWIN"


def test_on_event_streams_stage_transitions_and_a_final_done(monkeypatch: pytest.MonkeyPatch):
    """on_event는 Stage 9 API 레이어(이 PR 범위 밖)가 SSE에 연결하는 훅이다 —

    on_event=None(기본값)이면 완전히 그대로 동작한다는 걸 다른 테스트들이 이미
    보장하므로, 여기서는 콜백이 실제로 불릴 때 이벤트 흐름 자체만 확인한다.
    """
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))
    events: list[dict] = []
    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), on_event=events.append,
    )

    assert events[-1] == {"type": "done", "decision": result.decision, "reason": result.reason}
    settled = [e for e in events if e["type"] == "stage" and e["status"] != "running"]
    assert [(e["stage"], e["status"]) for e in settled] == [
        ("parse", "done"), ("grounding", "done"), ("compile", "done"),
        ("static_validation", "done"), ("twin", "skipped"),
    ]
    assert not any(e["type"] == "repair" for e in events)


def test_on_event_reports_repair_attempts_on_grounding_failure(monkeypatch: pytest.MonkeyPatch):
    call_count = 0

    def fake_parse(intent, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _accepted([{**VALID_FORWARD_RULE, "enforcement": {"device": "switch 99"}}])
        return _accepted([VALID_FORWARD_RULE])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    events: list[dict] = []
    result = run_pipeline(
        "forward h1 to h2 on switch 99", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), on_event=events.append,
    )

    assert result.decision == "APPROVE_WITHOUT_TWIN"
    assert result.repair_attempts == 1
    repair_events = [e for e in events if e["type"] == "repair"]
    assert len(repair_events) == 1
    assert repair_events[0] == {"type": "repair", "attempt": 1, "reason": "grounding_failed"}
    grounding_statuses = [
        e["status"] for e in events
        if e["type"] == "stage" and e["stage"] == "grounding" and e["status"] != "running"
    ]
    assert grounding_statuses == ["rejected", "done"]


# ── Exp-3 ablation kwarg (skip_grounding/skip_static_validation/
# max_repair_attempts/initial_prediction) — 원본: 없음, 신규 (docs/plan.md
# Exp-3, 이슈 #37 참고). 이 네 kwarg는 skip_twin과 같은 패턴(기본값 불변)이라
# 위 test_skip_twin_flag_*와 같은 스타일로 검증한다.


def test_skip_grounding_lets_a_nonexistent_switch_number_compile_silently(monkeypatch: pytest.MonkeyPatch):
    """그라운딩을 끄면 컴파일러가 존재하지 않는 스위치 번호를 그냥 통과시킨다 —
    Exp-3의 핵심 동기(그라운딩이 실제로 무엇을 잡아내는가)를 코드로 고정한다.
    """
    nonexistent_switch_rule = {**VALID_FORWARD_RULE, "enforcement": {"device": "switch 99"}}
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([nonexistent_switch_rule]))

    result = run_pipeline(
        "forward h1 to h2 on switch 99", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), skip_grounding=True,
    )

    assert result.grounding_report is None
    assert result.flow_set is not None
    assert result.flow_set.flows[0].deviceId == "of:0000000000000063"  # 존재하지 않는 switch 99, 에러 없음
    assert result.decision == "APPROVE_WITHOUT_TWIN"


def test_skip_static_validation_bypasses_the_static_gate(monkeypatch: pytest.MonkeyPatch):
    """static_result는 아예 계산되지 않고 None으로 남는다 — 정적검증 자체가
    실제로 뭘 잡아내는지는 tests/test_verify_gold350.py가 이미 커버하므로,
    여기서는 게이트가 정말 안 도는지 배선만 확인한다."""
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))

    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), skip_static_validation=True,
    )

    assert result.static_result is None
    assert result.flow_set is not None
    assert result.decision == "APPROVE_WITHOUT_TWIN"


def test_max_repair_attempts_override_exhausts_earlier_than_module_default(monkeypatch: pytest.MonkeyPatch):
    unknown_host_rule = {**VALID_FORWARD_RULE, "selector": {"source": {"host": "h99"}, "destination": {"host": "h2"}}}
    call_count = 0

    def fake_parse(intent, **kw):
        nonlocal call_count
        call_count += 1
        return _accepted([unknown_host_rule])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline(
        "forward from h99 to h2", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), max_repair_attempts=1,
    )

    assert result.decision == "REJECT"
    assert result.repair_attempts == 1
    assert call_count == 2  # attempt 0 + 1 재시도, MAX_REPAIR_ATTEMPTS(3)까지 안 감


def test_initial_prediction_skips_the_first_parse_call(monkeypatch: pytest.MonkeyPatch):
    """capture-ir/replay 설계 — attempt 0는 캡처된 IntentPrediction을 그대로 쓰고
    parse_intent()는 repair가 걸린 attempt 1부터만 새로 불린다."""
    unknown_host_rule = {**VALID_FORWARD_RULE, "selector": {"source": {"host": "h99"}, "destination": {"host": "h2"}}}
    captured = from_gold350({"status": "accepted", "rules": [unknown_host_rule]})
    call_count = 0

    def fake_parse(intent, **kw):
        nonlocal call_count
        call_count += 1
        return _accepted([VALID_FORWARD_RULE])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline(
        "forward from h99 to h2", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), initial_prediction=captured,
    )

    assert call_count == 1  # attempt 0는 initial_prediction 재사용, attempt 1만 새로 파싱
    assert result.repair_attempts == 1
    assert result.decision == "APPROVE_WITHOUT_TWIN"


def test_skip_grounding_and_skip_static_together_is_single_shot(monkeypatch: pytest.MonkeyPatch):
    """두 게이트를 동시에 끄면 repair를 트리거할 조건이 없어 attempt 0에서
    항상 끝난다 — "5번째 arm"이 별도 처리 없이 공짜로 얻어지는 지점."""
    call_count = 0

    def fake_parse(intent, **kw):
        nonlocal call_count
        call_count += 1
        return _accepted([VALID_FORWARD_RULE])

    monkeypatch.setattr(pipeline_module, "parse_intent", fake_parse)
    result = run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), skip_grounding=True, skip_static_validation=True,
    )

    assert call_count == 1
    assert result.repair_attempts == 0
    assert result.grounding_report is None
    assert result.static_result is None
    assert result.decision == "APPROVE_WITHOUT_TWIN"


# ── intent_action 스탬핑 (커밋 3470521) 회귀 테스트 ────────────────────────
# 코드 리뷰가 "GOLD-350 SFC 케이스는 항상 rule 2개 이상이라 is_compound가
# 먼저 걸려 elif sfc 분기가 죽은 코드"라고 지적했다 — 직접 실행해서 반박:
# data/gold/gold350_eval.jsonl의 SFC 50건 전부 rule 1개다(is_compound=False).
# 아래 두 테스트가 그 사실과 두 분기 각각의 효과를 고정한다.


def test_compound_program_is_stamped_compound_and_static_catches_intra_shadowing(
    monkeypatch: pytest.MonkeyPatch,
):
    shared_selector = {
        "source": {"ip": "10.0.0.1/32"}, "destination": {"ip": "10.0.0.2/32"},
        "protocol": "tcp", "eth_type": "ipv4",
    }
    conflicting_rules = [
        {"action": "forward", "intent_type": "forwarding", "selector": shared_selector, "enforcement": {"device": "switch 1", "egress_port": 2}},
        {"action": "block", "intent_type": "security", "selector": shared_selector, "enforcement": {"device": "switch 1"}},
    ]
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted(conflicting_rules))

    # skip_grounding=True — grounding's own shadowed_rule conflict check already
    # catches this exact same-criteria/opposite-action pattern at the IR level
    # (see docs/plan.md Exp-3 correction re: Exp-2's B1/B2 evidence being about
    # grounding, not static). Isolating static's gate is the point of this test.
    result = run_pipeline(
        "forward then block the same traffic", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), skip_grounding=True,
    )

    assert result.prediction.program.is_compound is True
    assert result.static_result is not None and not result.static_result.passed
    assert any(c["conflict_type"] == "Intra-Shadowing" for c in result.static_result.conflicts)
    assert result.decision == "REJECT"


def test_single_rule_sfc_program_is_stamped_sfc_not_compound(monkeypatch: pytest.MonkeyPatch):
    """GOLD-350의 SFC 규칙(50건 전부)은 단일 rule이므로 ``is_compound`` False —
    ``elif prediction.program.single.action == "sfc"`` 분기가 실제로 도달돼
    ``intent_action="sfc"``가 스탬핑된다(``"compound"``가 아님)."""
    sfc_rule = {
        "action": "sfc", "intent_type": "sfc",
        "selector": {"source": {"host": "h1"}, "destination": {"host": "h2"}},
        "enforcement": {"device": "switch 1", "egress_port": 2, "alt_egress_port": 1},
        "routing": {"waypoints": ["of:0000000000000001:2"]},
    }
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([sfc_rule]))

    real_static_validate = pipeline_module.static_validate
    captured_flow_dict: dict = {}

    def spy_static_validate(flowrule, **kw):
        captured_flow_dict.update(flowrule)
        return real_static_validate(flowrule, **kw)

    monkeypatch.setattr(pipeline_module, "static_validate", spy_static_validate)

    # grounding no longer needs to be skipped here -- issue #40 (grounding
    # rejected every single-rule/product-schema SFC program outright) is fixed.
    result = run_pipeline(
        "route h1 to h2 through the service chain", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(),
    )

    assert result.prediction.program.is_compound is False
    assert captured_flow_dict.get("intent_action") == "sfc"
    assert result.decision == "APPROVE_WITHOUT_TWIN"


def test_skip_grounding_and_skip_static_emit_a_skipped_stage_event(monkeypatch: pytest.MonkeyPatch):
    """skip_twin은 스킵돼도 항상 "stage" 이벤트를 낸다(status="skipped") —
    skip_grounding/skip_static_validation도 같은 패턴이어야 on_event 소비자가
    스테이지 하나를 아예 놓치는 일이 없다."""
    monkeypatch.setattr(pipeline_module, "parse_intent", lambda intent, **kw: _accepted([VALID_FORWARD_RULE]))
    events: list[dict] = []

    run_pipeline(
        "forward h1 to h2 on switch 1", model="fake-model", topology=TOPOLOGY,
        onos_client=_FakeOnosClient(), skip_grounding=True, skip_static_validation=True,
        on_event=events.append,
    )

    stage_events = {e["stage"]: e["status"] for e in events if e["type"] == "stage"}
    assert stage_events["grounding"] == "skipped"
    assert stage_events["static_validation"] == "skipped"
