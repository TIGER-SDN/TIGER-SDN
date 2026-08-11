"""tests/test_runctx.py — Stage-independent 완료 기준 (이슈 #16): RunContext round-trips
a manifest/event stream through save/finish/fail and redacts configured secrets.

원본: sdn-intent-framework의 research/tests/test_config_and_logging.py. `AppSettings`
(TOML + pydantic-settings, 이 레포에는 없음)에 묶인 config-loading 테스트
(`test_default_and_b0_override` 등)는 제외했다 — `tiger_sdn.runctx.RunContext`는
그 설정 객체 대신 키워드 인자를 직접 받도록 이식됐으므로(`src/tiger_sdn/runctx/
run_context.py` 참고) 해당 테스트들은 대상이 없다. 나머지 로깅 동작(매니페스트,
이벤트 스트림, secret redaction, 아티팩트 버저닝, stage 중첩, 동시성)은 그대로
옮겼다.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel

from tiger_sdn.runctx import RunContext, RunManifest, generate_schemas

API_KEY = "test-api-secret"


def _make_run(
    tmp_path: Path,
    intent_id: str,
    topology_id: str = "topology_a",
    *,
    console: bool = False,
    log_level: str = "INFO",
    secret_values: tuple[str, ...] = (API_KEY,),
) -> RunContext:
    return RunContext(
        intent_id,
        topology_id,
        model_name="gemini-3.1-flash-lite",
        prompt_version="T-D",
        log_dir=tmp_path,
        log_level=log_level,  # type: ignore[arg-type]
        console=console,
        secret_values=secret_values,
        repo_root=tmp_path,
    )


class ArtifactKind(Enum):
    INTENT = "intent"


@dataclass
class ArtifactChild:
    count: int


class ArtifactPayload(BaseModel):
    path: Path
    created_at: datetime
    kind: ArtifactKind
    child: ArtifactChild
    api_key: str


def test_successful_run_writes_valid_manifest_and_redacts(tmp_path: Path) -> None:
    with _make_run(tmp_path, "intent-001") as run:
        first_run_id = run.run_id
        reference = run.save_artifact("input_intent", "allow h1 to reach h2")
        secret_artifact = run.save_artifact(
            "llm_raw_output", {"api_key": API_KEY, "result": f"response included {API_KEY}"},
        )
        run.log_event("llm_completed", stage="translation", message=f"Bearer {API_KEY}")
        run.finish(
            "APPROVE",
            {"execution_time": {"translation_ms": 12.5}, "token_usage": {"input": 10, "output": 5, "total": 15}},
        )

    manifest = RunManifest.model_validate_json(run.manifest_path.read_text(encoding="utf-8"))
    assert manifest.run_id == first_run_id
    assert manifest.status == "succeeded"
    assert manifest.final_decision == "APPROVE"
    assert manifest.input_intent == reference
    assert manifest.token_usage["total"] == 15
    artifact_bytes = (run.run_dir / reference.path).read_bytes()
    assert hashlib.sha256(artifact_bytes).hexdigest() == reference.sha256
    secret_payload = (run.run_dir / secret_artifact.path).read_text(encoding="utf-8")
    assert API_KEY not in secret_payload
    assert API_KEY not in run.event_path.read_text(encoding="utf-8")
    assert all(json.loads(line) for line in run.event_path.read_text(encoding="utf-8").splitlines())


def test_failed_run_is_retained(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="deliberate"):
        with _make_run(tmp_path, "intent-fail") as run:
            run.save_artifact("generated_ir", {"partial": True})
            raise RuntimeError("deliberate failure")

    manifest = RunManifest.model_validate_json(run.manifest_path.read_text(encoding="utf-8"))
    assert manifest.status == "failed"
    assert manifest.error == {"type": "RuntimeError", "message": "deliberate failure"}
    assert (run.run_dir / manifest.generated_ir.path).exists()


def test_run_ids_are_unique(tmp_path: Path) -> None:
    first = _make_run(tmp_path, "one")
    second = _make_run(tmp_path, "two")
    first.finish(None, {})
    second.finish(None, {})
    assert first.run_id != second.run_id


def test_schema_generation(tmp_path: Path) -> None:
    generated = generate_schemas(tmp_path)
    assert {path.name for path in generated} == {
        "run_manifest.schema.json",
        "event_record.schema.json",
        "intent_prediction.schema.json",
        "onos_flow_set.schema.json",
    }
    for path in generated:
        assert json.loads(path.read_text(encoding="utf-8"))["type"] == "object"


def test_log_level_filters_file_and_console(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with _make_run(tmp_path, "level-test", console=True, log_level="ERROR") as run:
        run.log_event("filtered_info")
        run.log_event("retained_error", level="ERROR")

    events = [json.loads(line) for line in run.event_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["retained_error"]
    console = capsys.readouterr().out
    assert "retained_error" in console
    assert "filtered_info" not in console


def test_high_log_level_still_creates_empty_event_file(tmp_path: Path) -> None:
    with _make_run(tmp_path, "empty-events", log_level="CRITICAL") as run:
        pass
    assert run.event_path.is_file()
    assert run.event_path.read_text(encoding="utf-8") == ""


def test_invalid_event_level_is_rejected(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "invalid-level")
    with pytest.raises(ValueError, match="Unsupported log level"):
        run.log_event("typo", level="INOF")  # type: ignore[arg-type]
    run.finish()


def test_topology_id_and_keyword_metrics(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "default-topology", topology_id="topology_b")
    run.finish("APPROVE", execution_time={"translation_ms": 2}, token_usage={"total": 7})
    assert run.manifest.topology_id == "topology_b"
    assert run.manifest.execution_time["translation_ms"] == 2.0
    assert run.manifest.token_usage == {"total": 7}


def test_duplicate_metric_forms_are_rejected_without_closing_run(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "duplicate-metrics")
    with pytest.raises(ValueError, match="both metrics"):
        run.finish(metrics={"execution_time": {}}, execution_time={})
    assert run.manifest.status == "running"
    run.finish()


def test_structured_artifacts_are_serialized_redacted_and_versioned(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "artifact-types")
    payload = ArtifactPayload(
        path=Path("input/file.txt"),
        created_at=datetime(2026, 7, 15, 12, 30),
        kind=ArtifactKind.INTENT,
        child=ArtifactChild(count=2),
        api_key=API_KEY,
    )
    first = run.save_artifact("generated_ir", payload)
    second = run.save_artifact("generated_ir.json", payload)
    with pytest.raises(ValueError, match=r"\.json extension"):
        run.save_artifact("invalid.yaml", payload)
    run.finish()

    assert first.path == "artifacts/generated_ir.json"
    assert second.path == "artifacts/generated_ir_2.json"
    assert set(run.manifest.artifacts) == {"generated_ir", "generated_ir_2"}
    assert run.manifest.generated_ir == second
    saved = json.loads((run.run_dir / second.path).read_text(encoding="utf-8"))
    assert saved == {
        "api_key": "**********",
        "child": {"count": 2},
        "created_at": "2026-07-15T12:30:00",
        "kind": "intent",
        "path": "input/file.txt",
    }


def test_closed_run_rejects_all_writes(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "closed-run")
    run.finish()
    operations = (
        lambda: run.log_event("late"),
        lambda: run.save_artifact("late", {"value": 1}),
        lambda: run.finish(),
        lambda: run.fail(RuntimeError("late")),
    )
    for operation in operations:
        with pytest.raises(RuntimeError, match="already succeeded"):
            operation()


def test_context_preserves_exception_after_explicit_finish(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "original-error")
    with pytest.raises(ValueError, match="application error"):
        with run:
            run.finish()
            raise ValueError("application error")


def test_stage_binds_events_and_records_duration(tmp_path: Path) -> None:
    with _make_run(tmp_path, "stage-success") as run:
        with run.stage("translation"):
            run.log_event("llm_called")
            run.log_event("override", stage="custom")
        run.finish(token_usage={"total": 3})

    events = [json.loads(line) for line in run.event_path.read_text(encoding="utf-8").splitlines()]
    stages = {event["event"]: event["stage"] for event in events}
    assert stages["stage_started"] == "translation"
    assert stages["llm_called"] == "translation"
    assert stages["override"] == "custom"
    assert stages["stage_completed"] == "translation"
    assert run.manifest.execution_time["translation_ms"] >= 0


def test_failed_stage_is_recorded_and_redacted(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="stage failed"):
        with _make_run(tmp_path, "stage-failure") as run:
            with run.stage("validation"):
                raise RuntimeError(f"stage failed {API_KEY}")

    events_text = run.event_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in events_text.splitlines()]
    assert [event["event"] for event in events][-2:] == ["stage_failed", "run_failed"]
    assert API_KEY not in events_text
    assert run.manifest.status == "failed"
    assert "validation_ms" in run.manifest.execution_time


def test_nested_stage_restores_parent_binding(tmp_path: Path) -> None:
    with _make_run(tmp_path, "nested-stage") as run:
        with run.stage("outer"):
            with run.stage("inner"):
                run.log_event("inside_inner")
            run.log_event("back_in_outer")

    events = [json.loads(line) for line in run.event_path.read_text(encoding="utf-8").splitlines()]
    event_stages = {event["event"]: event["stage"] for event in events}
    assert event_stages["inside_inner"] == "inner"
    assert event_stages["back_in_outer"] == "outer"
    assert "outer_ms" in run.manifest.execution_time
    assert "inner_ms" in run.manifest.execution_time


def test_concurrent_artifact_versions_are_all_retained(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "concurrent-artifacts")
    with ThreadPoolExecutor(max_workers=4) as executor:
        references = list(executor.map(lambda value: run.save_artifact("result", {"value": value}), range(8)))
    run.finish()

    assert len({reference.path for reference in references}) == 8
    assert len(run.manifest.artifacts) == 8
    assert all((run.run_dir / reference.path).is_file() for reference in references)
