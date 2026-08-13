"""tests/test_api.py — api.app: 웹 UI용 FastAPI 백엔드.

원본: 없음 (신규, Stage 9). `orchestrate.run_pipeline`/`backends.OnosClient`/
`deploy.Deployer`를 가짜로 바꿔치기해 LLM/Mininet/ONOS 없이 SSE 스트림 배선,
`/api/topology`의 라이브/폴백 두 경로, `/api/logs`가 runctx 매니페스트를
정확히 읽고 지우는지만 검증한다.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from tiger_sdn.api import app as app_module
from tiger_sdn.backends import OnosError
from tiger_sdn.compile.onos import OnosFlowSet
from tiger_sdn.deploy import DeployResult
from tiger_sdn.ir import IntentPrediction, IntentProgram, IntentRule
from tiger_sdn.orchestrate.pipeline import PipelineResult
from tiger_sdn.twin import TwinResult
from tiger_sdn.verify import StaticResult

client = TestClient(app_module.app)

PROGRAM = IntentProgram(
    rules=[
        IntentRule(
            action="block",
            selector={"source": {"ip": "10.0.0.1/32"}, "eth_type": "ipv4"},
            enforcement={"device": "switch 1"},
        )
    ]
)
FLOW_SET = OnosFlowSet(
    flows=[
        {
            "priority": 40000,
            "timeout": 0,
            "isPermanent": True,
            "deviceId": "of:0000000000000001",
            "selector": {"criteria": [{"type": "IPV4_SRC", "ip": "10.0.0.1/32"}]},
            "treatment": None,
        }
    ]
)


def _events(response_text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in response_text.splitlines() if line.startswith("data: ")]


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.config, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(app_module.config, "API_KEY", "")


# ── POST /api/run ────────────────────────────────────────────────────────────

def test_run_rejects_empty_intent():
    resp = client.post("/api/run", json={"intent": "   "})
    events = _events(resp.text)
    assert events[0]["type"] == "error"
    assert events[-1] == {"type": "done", "decision": "ERROR"}


def test_run_rejects_intent_too_long():
    resp = client.post("/api/run", json={"intent": "x" * 1001})
    events = _events(resp.text)
    assert events[0]["type"] == "error"
    assert "너무 깁니다" in events[0]["error"]


def test_run_streams_stage_and_decision_events_without_deploy(monkeypatch):
    prediction = IntentPrediction.accept(PROGRAM)

    def fake_run_pipeline(intent, *, model, topology, skip_twin, run_context, on_event):
        on_event({"type": "stage", "stage": "parse", "status": "done"})
        return PipelineResult(
            decision="APPROVE_WITHOUT_TWIN", intent=intent, reason="ok",
            prediction=prediction, flow_set=FLOW_SET,
            static_result=StaticResult(passed=True),
            twin_result=TwinResult(status="skipped", reason="skip_twin=True"),
        )

    monkeypatch.setattr(app_module, "run_pipeline", fake_run_pipeline)

    resp = client.post("/api/run", json={"intent": "block 10.0.0.1", "skip_deploy": True})
    events = _events(resp.text)

    assert {"type": "stage", "stage": "parse", "status": "done"} in events
    decision_events = [e for e in events if e["type"] == "decision"]
    assert decision_events[0]["report"]["decision"] == "APPROVE_WITHOUT_TWIN"
    assert not any(e.get("stage") == "deploy" for e in events)
    done = events[-1]
    assert done == {"type": "done", "run_id": done["run_id"], "decision": "APPROVE_WITHOUT_TWIN", "reason": "ok"}

    manifest_paths = list((app_module.config.LOGS_DIR / "runs").glob("*/*/manifest.json"))
    assert len(manifest_paths) == 1
    manifest = json.loads(manifest_paths[0].read_text())
    assert manifest["final_decision"] == "APPROVE_WITHOUT_TWIN"


class _FakeDeployer:
    def deploy(self, flowrule: dict) -> DeployResult:
        return DeployResult(success=True, flow_ids=["0x1"])


def test_run_deploys_on_approve_and_reports_flow_ids(monkeypatch):
    prediction = IntentPrediction.accept(PROGRAM)

    def fake_run_pipeline(intent, *, model, topology, skip_twin, run_context, on_event):
        return PipelineResult(
            decision="APPROVE", intent=intent, reason="ok",
            prediction=prediction, flow_set=FLOW_SET,
            static_result=StaticResult(passed=True),
            twin_result=TwinResult(status="passed", checks={"a": True}),
        )

    monkeypatch.setattr(app_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(app_module, "Deployer", _FakeDeployer)

    resp = client.post("/api/run", json={"intent": "block 10.0.0.1"})
    events = _events(resp.text)
    deploy_events = [e for e in events if e.get("stage") == "deploy"]
    assert deploy_events[-1]["status"] == "done"
    assert deploy_events[-1]["result"]["flow_ids"] == ["0x1"]


def test_run_handles_run_pipeline_exception(monkeypatch):
    def fake_run_pipeline(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "run_pipeline", fake_run_pipeline)

    resp = client.post("/api/run", json={"intent": "block 10.0.0.1"})
    events = _events(resp.text)
    assert events[0]["type"] == "error"
    assert "boom" in events[0]["error"]
    assert events[-1]["decision"] == "ERROR"


def test_run_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", "secret")

    resp = client.post("/api/run", json={"intent": "block 10.0.0.1"})
    assert resp.status_code == 403

    resp = client.post("/api/run", json={"intent": "   "}, headers={"X-API-Key": "secret"})
    assert resp.status_code == 200


# ── GET /api/topology ─────────────────────────────────────────────────────────

class _FakeOnosClientDown:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def devices(self):
        raise OnosError("controller down")


def test_topology_falls_back_to_default_when_onos_unreachable(monkeypatch):
    monkeypatch.setattr(app_module, "OnosClient", _FakeOnosClientDown)

    resp = client.get("/api/topology")
    data = resp.json()

    assert data["error"] is None
    assert data["rule_count"] == 0
    switch_ids = {n["id"] for n in data["nodes"] if n["type"] == "switch"}
    assert "of:0000000000000001" in switch_ids
    host_ids = {n["id"] for n in data["nodes"] if n["type"] == "host"}
    assert "h1" in host_ids


class _FakeOnosClientLive:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def devices(self):
        return [{"id": "of:0000000000000001", "available": True}]

    def hosts(self):
        return [{
            "id": "aa:bb/None", "ipAddresses": ["10.0.0.1"],
            "locations": [{"elementId": "of:0000000000000001"}],
        }]

    def links(self):
        return []

    def flows(self):
        return [{
            "deviceId": "of:0000000000000001", "priority": 40000,
            "selector": {"criteria": [{"type": "IPV4_SRC", "ip": "10.0.0.1/32"}]},
            "treatment": {"instructions": [{"type": "NOACTION"}]},
        }]


def test_topology_uses_live_onos_when_available(monkeypatch):
    monkeypatch.setattr(app_module, "OnosClient", _FakeOnosClientLive)

    resp = client.get("/api/topology")
    data = resp.json()

    assert data["rule_count"] == 1
    assert data["flow_table"][0]["action"] == "DROP"
    switch_node = next(n for n in data["nodes"] if n["type"] == "switch")
    assert switch_node["label"] == "S1"
    assert switch_node["state"] == "drop"
    host_node = next(n for n in data["nodes"] if n["type"] == "host")
    assert host_node["label"] == "H1"
    assert {"source": host_node["id"], "target": "of:0000000000000001"} in data["links"]


# ── /api/logs ────────────────────────────────────────────────────────────────

def _write_manifest(logs_dir, run_id: str, decision: str, intent_id: str) -> None:
    run_dir = logs_dir / "runs" / "20260813" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "date": "2026-08-13T00:00:00Z",
        "final_decision": decision, "intent_id": intent_id,
    }))


def test_logs_lists_manifests_sorted_by_recency():
    logs_dir = app_module.config.LOGS_DIR
    _write_manifest(logs_dir, "run-older", "REJECT", "old intent")
    time.sleep(0.01)
    _write_manifest(logs_dir, "run-newer", "APPROVE", "new intent")

    entries = client.get("/api/logs").json()

    assert [e["run_id"] for e in entries] == ["run-newer", "run-older"]
    assert entries[0]["decision"] == "APPROVE"


def test_logs_delete_clears_run_directories():
    logs_dir = app_module.config.LOGS_DIR
    _write_manifest(logs_dir, "run-a", "REJECT", "intent")

    resp = client.delete("/api/logs")

    assert resp.json() == {"ok": True, "deleted": 1}
    assert client.get("/api/logs").json() == []
