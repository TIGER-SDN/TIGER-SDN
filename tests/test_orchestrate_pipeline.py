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
