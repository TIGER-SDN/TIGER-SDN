"""src/tiger_sdn/runctx/run_context.py — 실행 단위의 durable/구조화 로깅.

원본: sdn-intent-framework의 research/safe_intent_sdn/run_context.py (연구 트랙이
이 영역의 정본 — JSON Schema 자동생성, secret redaction, 동시성에서 제품 구현
(`xai_pipeline`의 main.py/api.py가 손으로 채우던 `pipeline_result`/`result` dict)
보다 우월하다, 이슈 #16). 패키지명만 sdn_intent -> tiger_sdn으로 바꾼 게 아니라,
`RunContext`가 원래 받던 `AppSettings`(TOML + pydantic-settings, 이 레포에는 아직
없음 — `tiger_sdn.config`는 평면 상수 몇 개뿐)를 걷어내고 실제로 필요한 값들을
생성자 키워드 인자로 직접 받도록 바꿨다. 이벤트 스트림/매니페스트/아티팩트/
secret redaction 동작 자체는 변경 없이 그대로 옮겼다.

`xai_pipeline`의 `pipeline_result` dict(run_id/intent/model/rag_k/timestamp +
stage1~6 결과 + decision)와 비교하면: `RunManifest`의 `input_intent`/
`generated_ir`/`compiled_policy`/`static_validation`/`twin_test_results`/
`repair_history` 아티팩트 슬롯이 stage1~4 + repair loop를 그대로 흡수한다.
`rag_k`(RAG)와 stage5/6(XAI 설명, 실배포)는 흡수 대상이 아니다 — RAG는 이식
대상에서 제외됐고("이식하지 않는 것" 표), XAI/deploy는 장기 보류 상태라
`RunManifest`에 대응 슬롯이 없다.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "ArtifactRef",
    "Decision",
    "EventRecord",
    "LogLevel",
    "RunContext",
    "RunManifest",
    "RunStatus",
]

Decision = Literal["APPROVE", "REJECT", "HOLD"]
RunStatus = Literal["running", "succeeded", "failed"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_SENSITIVE_KEY = re.compile(r"(api[_-]?key|password|authorization|secret|token)$", re.IGNORECASE)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_LEVEL_VALUES: dict[str, int] = {
    "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50,
}
_JSON_ADAPTER = TypeAdapter(Any)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    media_type: str


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    date: str
    git_commit: str
    git_dirty: bool
    model_name: str
    prompt_version: str
    topology_id: str
    intent_id: str
    feature_flags: dict[str, bool]
    random_seed: int
    config_snapshot: dict[str, Any]
    input_intent: ArtifactRef | None = None
    generated_ir: ArtifactRef | None = None
    static_validation: ArtifactRef | None = None
    compiled_policy: ArtifactRef | None = None
    twin_test_results: ArtifactRef | None = None
    repair_history: ArtifactRef | None = None
    final_decision: Decision | None = None
    execution_time: dict[str, float] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    status: RunStatus = "running"
    error: dict[str, str] | None = None
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)


class EventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: str
    level: LogLevel
    event: str
    run_id: str
    stage: str | None = None
    duration_ms: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


def _git_state(repo_root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, check=True,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip())
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "**********" if _SENSITIVE_KEY.search(str(key)) else _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class RunContext:
    """Own one run directory and its append-only event stream."""

    _MANIFEST_ARTIFACTS = {
        "input_intent", "generated_ir", "static_validation", "compiled_policy",
        "twin_test_results", "repair_history",
    }

    def __init__(
        self,
        intent_id: str,
        topology_id: str,
        *,
        model_name: str,
        prompt_version: str,
        random_seed: int = 42,
        feature_flags: dict[str, bool] | None = None,
        config_snapshot: dict[str, Any] | None = None,
        log_dir: Path | None = None,
        log_level: LogLevel = "INFO",
        console: bool = True,
        secret_values: tuple[str, ...] = (),
        repo_root: Path | None = None,
    ) -> None:
        self.log_level = log_level
        self.console = console
        self.started_at = perf_counter()
        now = _utc_now()
        self.run_id = f"{now.strftime('%Y%m%dT%H%M%S%f')[:-3]}Z_{uuid.uuid4().hex[:8]}"
        log_root = Path(log_dir) if log_dir is not None else Path.cwd() / "logs" / "runs"
        self.run_dir = log_root / now.strftime("%Y%m%d") / self.run_id
        self.artifact_dir = self.run_dir / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=False)
        self.event_path = self.run_dir / "events.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        self._lock = threading.RLock()
        self._current_stage: ContextVar[str | None] = ContextVar(
            f"run_stage_{self.run_id}", default=None,
        )
        self._stage_execution_time: dict[str, float] = {}
        self._secret_values = tuple(value for value in secret_values if value)
        commit, dirty = _git_state(repo_root or Path.cwd())
        self.manifest = RunManifest(
            run_id=self.run_id, date=_iso_now(), git_commit=commit, git_dirty=dirty,
            model_name=model_name, prompt_version=prompt_version,
            topology_id=topology_id, intent_id=intent_id,
            feature_flags=feature_flags or {}, random_seed=random_seed,
            config_snapshot=config_snapshot or {},
        )
        self._write_manifest()
        self.event_path.touch()
        self._emit_event("run_started", stage="orchestration")

    def __enter__(self) -> "RunContext":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: Any) -> bool:
        if exc is not None:
            if self.manifest.status == "running":
                try:
                    self.fail(exc)
                except Exception:
                    # Never replace the application exception with a logging failure.
                    pass
        elif self.manifest.status == "running":
            self.finish(None, {})
        return False

    def _ensure_running(self) -> None:
        if self.manifest.status != "running":
            raise RuntimeError(f"Run {self.run_id} is already {self.manifest.status}")

    def _write_manifest(self) -> None:
        temp_path = self.manifest_path.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8") as output:
            output.write(self.manifest.model_dump_json(indent=2))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, self.manifest_path)

    def log_event(
        self, event: str, stage: str | None = None, *, level: LogLevel = "INFO",
        duration_ms: float | None = None, evidence_ids: list[str] | None = None, **fields: Any,
    ) -> None:
        with self._lock:
            self._ensure_running()
            self._emit_event(
                event, stage, level=level, duration_ms=duration_ms,
                evidence_ids=evidence_ids, **fields,
            )

    def _emit_event(
        self, event: str, stage: str | None = None, *, level: LogLevel = "INFO",
        duration_ms: float | None = None, evidence_ids: list[str] | None = None, **fields: Any,
    ) -> None:
        if level not in _LEVEL_VALUES:
            raise ValueError(f"Unsupported log level: {level!r}")
        if _LEVEL_VALUES[level] < _LEVEL_VALUES[self.log_level]:
            return
        stage = stage if stage is not None else self._current_stage.get()
        fields = self._redact_secrets(fields)
        record = EventRecord(
            timestamp=_iso_now(), level=level, event=event, run_id=self.run_id,
            stage=stage, duration_ms=duration_ms, evidence_ids=evidence_ids or [], fields=fields,
        )
        with self._lock:
            with self.event_path.open("a", encoding="utf-8") as event_file:
                event_file.write(record.model_dump_json() + "\n")
                event_file.flush()
                os.fsync(event_file.fileno())
        if self.console:
            stage_text = f" [{stage}]" if stage else ""
            print(f"{record.timestamp} {level:<8}{stage_text} {event}")

    @contextmanager
    def stage(self, name: str) -> Iterator["RunContext"]:
        """Bind a stage to nested events and record its elapsed time."""
        if not name:
            raise ValueError("Stage name must not be empty")
        with self._lock:
            self._ensure_running()
        token = self._current_stage.set(name)
        started_at = perf_counter()
        try:
            self.log_event("stage_started")
            yield self
        except BaseException as error:
            duration_ms = round((perf_counter() - started_at) * 1000, 3)
            self._record_stage_duration(name, duration_ms)
            if self.manifest.status == "running":
                self.log_event(
                    "stage_failed", level="ERROR", duration_ms=duration_ms,
                    error_type=type(error).__name__, error_message=str(error),
                )
            raise
        else:
            duration_ms = round((perf_counter() - started_at) * 1000, 3)
            self._record_stage_duration(name, duration_ms)
            self.log_event("stage_completed", duration_ms=duration_ms)
        finally:
            self._current_stage.reset(token)

    def _record_stage_duration(self, name: str, duration_ms: float) -> None:
        metric_name = f"{name}_ms"
        with self._lock:
            self._stage_execution_time[metric_name] = round(
                self._stage_execution_time.get(metric_name, 0.0) + duration_ms, 3,
            )

    def save_artifact(self, name: str, data: Any) -> ArtifactRef:
        if not _SAFE_NAME.fullmatch(name) or "/" in name or ".." in name:
            raise ValueError(f"Unsafe artifact name: {name!r}")
        supplied_path = Path(name)
        if isinstance(data, bytes):
            suffix = supplied_path.suffix or ".bin"
            payload = self._redact_secrets(data)
            media_type = mimetypes.guess_type(f"artifact{suffix}")[0] or "application/octet-stream"
        elif isinstance(data, str):
            suffix = supplied_path.suffix or ".txt"
            payload = self._redact_secrets(data).encode()
            media_type = mimetypes.guess_type(f"artifact{suffix}")[0] or "text/plain"
        else:
            suffix = ".json"
            if supplied_path.suffix and supplied_path.suffix.lower() != suffix:
                raise ValueError(
                    f"Structured artifact {name!r} must use the {suffix} extension",
                )
            try:
                normalized = _JSON_ADAPTER.dump_python(data, mode="json")
                normalized = self._redact_secrets(normalized)
                serialized = json.dumps(
                    normalized, ensure_ascii=False, indent=2, sort_keys=True,
                )
            except Exception as error:
                raise TypeError(f"Artifact {name!r} is not JSON serializable") from error
            payload = (serialized + "\n").encode()
            media_type = "application/json"
        base_key = supplied_path.stem if supplied_path.suffix else name
        with self._lock:
            self._ensure_running()
            version = 1
            artifact_key = base_key
            artifact_path = self.artifact_dir / f"{artifact_key}{suffix}"
            while artifact_path.exists() or artifact_key in self.manifest.artifacts:
                version += 1
                artifact_key = f"{base_key}_{version}"
                artifact_path = self.artifact_dir / f"{artifact_key}{suffix}"
            artifact_path.write_bytes(payload)
            reference = ArtifactRef(
                path=artifact_path.relative_to(self.run_dir).as_posix(),
                sha256=hashlib.sha256(payload).hexdigest(), media_type=media_type,
            )
            self.manifest.artifacts[artifact_key] = reference
            if base_key in self._MANIFEST_ARTIFACTS:
                setattr(self.manifest, base_key, reference)
            self._write_manifest()
            self._emit_event(
                "artifact_saved", stage="persistence", artifact=artifact_key,
                path=reference.path,
            )
            return reference

    def _redact_secrets(self, value: Any) -> Any:
        value = _redact(value)
        if isinstance(value, dict):
            return {key: self._redact_secrets(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_secrets(item) for item in value]
        if isinstance(value, bytes):
            for secret in self._secret_values:
                value = value.replace(secret.encode(), b"**********")
            return value
        if isinstance(value, str):
            for secret in self._secret_values:
                value = value.replace(secret, "**********")
        return value

    def finish(
        self, decision: Decision | None = None, metrics: dict[str, Any] | None = None, *,
        execution_time: dict[str, Any] | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> None:
        metrics = metrics or {}
        if execution_time is not None and "execution_time" in metrics:
            raise ValueError("execution_time was provided in both metrics and a keyword argument")
        if token_usage is not None and "token_usage" in metrics:
            raise ValueError("token_usage was provided in both metrics and a keyword argument")
        supplied_execution_time = execution_time if execution_time is not None else metrics.get("execution_time", {})
        supplied_token_usage = token_usage if token_usage is not None else metrics.get("token_usage", {})
        converted_execution_time = {
            key: float(value) for key, value in supplied_execution_time.items()
        }
        converted_token_usage = {
            key: int(value) for key, value in supplied_token_usage.items()
        }
        with self._lock:
            self._ensure_running()
            self.manifest.status = "succeeded"
            self.manifest.final_decision = decision
            self.manifest.execution_time = {
                **self._stage_execution_time,
                **converted_execution_time,
                "total_ms": round((perf_counter() - self.started_at) * 1000, 3),
            }
            self.manifest.token_usage = converted_token_usage
            self._write_manifest()
            self._emit_event("run_finished", stage="orchestration", decision=decision)

    def fail(self, error: BaseException) -> None:
        with self._lock:
            self._ensure_running()
            self.manifest.status = "failed"
            self.manifest.error = {
                "type": type(error).__name__, "message": self._redact_secrets(str(error)),
            }
            self.manifest.execution_time = {
                **self._stage_execution_time,
                "total_ms": round((perf_counter() - self.started_at) * 1000, 3),
            }
            self._write_manifest()
            self._emit_event(
                "run_failed", stage="orchestration", level="ERROR",
                error_type=type(error).__name__, error_message=str(error),
            )
