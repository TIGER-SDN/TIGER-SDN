"""src/tiger_sdn/api/app.py — 웹 UI용 FastAPI 백엔드.

원본: sdn-intent-framework의 src/xai_pipeline/api.py. 실행:
``uv run uvicorn tiger_sdn.api.app:app --reload --port 8000`` (레포 루트에서 —
config.py가 cwd 기준으로 .env/logs/토폴로지를 찾는다. 원본과 동일 관례).

원본과의 구조적 차이: 원본 `_run_pipeline`은 Stage1~6을 API 레이어 안에서 직접
오케스트레이션했지만, 여기서는 `orchestrate.pipeline.run_pipeline()`이 파싱부터
정적검증까지의 Repair Loop + twin을 이미 구현하고 있으므로 `on_event` 콜백 하나로
그 결과를 SSE 큐에 그대로 흘려보낸다. 이 레이어가 추가로 하는 일은 셋뿐이다:
(1) `RunContext`를 만들어 넘기고 결과로 `.finish()`, (2) 성공 경로에서
`explain.build_decision()`으로 최종 판정 리포트를 만들고, (3) APPROVE 계열이면
`deploy.Deployer`로 실제 배포한다 — 원본 Stage5/Stage6에 해당.

Stage 9 스코프 결정(docs/plan.md)에 따라 원본에서 뺀 것: RAG, 커스텀 토폴로지
에디터, 네트워크 프리셋 라이브 트래픽 시뮬레이터, flow state 캐시. `/api/topology`는
읽기 전용 D3 뷰만 제공한다(ONOS 실시간 조회, 실패 시 `data/gold/topology_eval.json`
정적 폴백 — 이 파일이 이미 twin 기본 다이아몬드 토폴로지와 dpid가 맞아떨어지는
평가용 정본 인벤토리라 별도 픽스처를 새로 만들지 않았다).
"""

from __future__ import annotations

import asyncio
import json
import queue as std_queue
import shutil
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tiger_sdn import config
from tiger_sdn.backends import OnosClient, OnosError
from tiger_sdn.deploy import Deployer
from tiger_sdn.explain import build_decision
from tiger_sdn.orchestrate import run_pipeline
from tiger_sdn.runctx import RunContext

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path.cwd()
_DEFAULT_TOPOLOGY_PATH = _REPO_ROOT / "data" / "gold" / "topology_eval.json"
_INTENT_MAX_LEN = 1000  # 프롬프트 인젝션 및 과도한 입력 방지 (원본과 동일)

app = FastAPI(title="TIGER-SDN Pipeline API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: str | None = Security(_api_key_header)) -> None:
    """API_KEY가 설정된 경우에만 X-API-Key 헤더를 요구한다. 빈 문자열이면 개발 모드(무인증)."""
    if config.API_KEY and key != config.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing X-API-Key header")


class RunRequest(BaseModel):
    intent: str
    model: str = config.LLM_MODEL
    skip_twin: bool = False
    skip_deploy: bool = False


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _default_topology() -> dict[str, Any]:
    return json.loads(_DEFAULT_TOPOLOGY_PATH.read_text(encoding="utf-8"))


# ── Pipeline runner (synchronous, called in a worker thread) ──────────────────

def _run(req: RunRequest, q: std_queue.Queue) -> None:
    intent = req.intent.strip()
    if not intent:
        q.put(_sse({"type": "error", "error": "intent가 비어 있습니다."}))
        q.put(_sse({"type": "done", "decision": "ERROR"}))
        return
    if len(intent) > _INTENT_MAX_LEN:
        q.put(_sse({
            "type": "error",
            "error": f"intent가 너무 깁니다 ({len(intent)}자). 최대 {_INTENT_MAX_LEN}자.",
        }))
        q.put(_sse({"type": "done", "decision": "ERROR"}))
        return

    def emit(event: dict[str, Any]) -> None:
        q.put(_sse(event))

    try:
        topology = _default_topology()
    except (OSError, json.JSONDecodeError) as exc:
        emit({"type": "error", "error": f"토폴로지 로딩 실패: {exc}"})
        emit({"type": "done", "decision": "ERROR"})
        return

    run = RunContext(
        intent_id=intent[:80],
        topology_id=topology.get("topology_id", "default"),
        model_name=req.model,
        prompt_version="intent_ir",
        log_dir=config.LOGS_DIR / "runs",
    )
    try:
        result = run_pipeline(
            intent,
            model=req.model,
            topology=topology,
            skip_twin=req.skip_twin,
            run_context=run,
            on_event=emit,
        )
    except Exception as exc:  # pragma: no cover — run_pipeline은 이미 대부분을 잡지만 안전망
        run.fail(exc)
        emit({"type": "error", "error": str(exc)})
        emit({"type": "done", "decision": "ERROR", "run_id": run.run_id})
        return

    if (
        result.prediction is not None
        and result.prediction.program is not None
        and result.flow_set is not None
        and result.static_result is not None
        and result.twin_result is not None
    ):
        flowrule = result.flow_set.model_dump(mode="json")
        report = build_decision(
            intent, result.prediction.program, flowrule, result.static_result, result.twin_result,
        )
        emit({"type": "decision", "report": report.model_dump(mode="json")})

        if result.decision in ("APPROVE", "APPROVE_WITHOUT_TWIN") and not req.skip_deploy:
            emit({"type": "stage", "stage": "deploy", "status": "running"})
            deploy_result = Deployer().deploy(flowrule)
            emit({
                "type": "stage",
                "stage": "deploy",
                "status": "done" if deploy_result.success else "error",
                "result": {
                    "success": deploy_result.success,
                    "flow_ids": deploy_result.flow_ids,
                    "error": deploy_result.error,
                },
            })

    run.finish(result.decision)
    emit({"type": "done", "run_id": run.run_id, "decision": result.decision, "reason": result.reason})


# ── API Routes ──────────────────────────────────────────────────────────────

@app.post("/api/run", dependencies=[Depends(_require_api_key)])
async def api_run(req: RunRequest) -> StreamingResponse:
    q: std_queue.Queue = std_queue.Queue()

    async def stream():
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, _run, req, q)
        while True:
            try:
                yield q.get_nowait()
            except std_queue.Empty:
                if fut.done():
                    # 큐를 완전히 비운다 — run_pipeline()이 자체 "done"(run_id 없음)을
                    # 반환 직전에 먼저 내보내고, 그 뒤에야 _run()이 이어서
                    # decision/deploy 단계와 run_id 있는 최종 "done"을 내보낸다.
                    # 메시지 내용으로 조기 종료하면(예전 버그) 그 뒷부분이 전부
                    # 유실된다 — 스레드가 끝나고 큐가 실제로 빌 때만 종료한다.
                    while True:
                        try:
                            yield q.get_nowait()
                        except std_queue.Empty:
                            break
                    await fut
                    return
                await asyncio.sleep(0.05)
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sw_label(device_id: str) -> str:
    try:
        return f"S{int(device_id.split(':')[-1], 16)}"
    except ValueError:
        return device_id[-4:] if len(device_id) > 4 else device_id


def _host_label(host: dict[str, Any]) -> str:
    ip = (host.get("ipAddresses") or [""])[0]
    try:
        return f"H{int(ip.split('.')[-1])}"
    except (ValueError, IndexError):
        return host.get("id", "H?")


def _onos_topology_as_d3(client: OnosClient) -> dict[str, Any]:
    devices = client.devices() or []
    available = [d for d in devices if d.get("available", False)]
    if not available:
        raise OnosError("no available devices")

    hosts_data = client.hosts() or []
    links_data = client.links() or []
    flows_data = client.flows() or []

    dev_actions: dict[str, set[str]] = {}
    for f in flows_data:
        device_id = f.get("deviceId", "")
        for instruction in f.get("treatment", {}).get("instructions", []):
            dev_actions.setdefault(device_id, set()).add(instruction.get("type", ""))

    def _dev_state(device_id: str, avail: bool) -> str:
        if not avail:
            return "offline"
        actions = dev_actions.get(device_id, set())
        if "NOACTION" in actions:
            return "drop"
        if "OUTPUT" in actions:
            return "forward"
        return "idle"

    nodes = [
        {
            "id": d["id"], "label": _sw_label(d["id"]), "type": "switch",
            "state": _dev_state(d["id"], d.get("available", False)),
        }
        for d in devices
    ]
    dev_label = {n["id"]: n["label"] for n in nodes}

    host_nodes = [
        {
            "id": h["id"], "label": _host_label(h), "type": "host",
            "ip": (h.get("ipAddresses") or [""])[0],
            "switch": (h.get("locations") or [{}])[0].get("elementId", ""),
        }
        for h in hosts_data
    ]

    seen: set[tuple[str, str]] = set()
    links = []
    for link in links_data:
        src = link.get("src", {}).get("device", "")
        dst = link.get("dst", {}).get("device", "")
        key = tuple(sorted([src, dst]))
        if key not in seen:
            seen.add(key)
            links.append({"source": src, "target": dst})
    for h in host_nodes:
        if h["switch"]:
            links.append({"source": h["id"], "target": h["switch"]})

    flow_table = []
    for f in flows_data[:20]:
        device_id = f.get("deviceId", "")
        criteria = f.get("selector", {}).get("criteria", [])
        match_parts = []
        for c in criteria[:2]:
            val = c.get("ip") or c.get("port") or c.get("mac") or c.get("ethType") or ""
            if val:
                match_parts.append(f"{c.get('type', '?')}={val}")
        instructions = f.get("treatment", {}).get("instructions", [])
        is_drop = not instructions or all(i.get("type") in ("NOACTION", "DROP") for i in instructions)
        flow_table.append({
            "device": dev_label.get(device_id, device_id[-4:] if len(device_id) > 4 else device_id),
            "priority": f.get("priority", 0),
            "match": ", ".join(match_parts) or "-",
            "action": "DROP" if is_drop else "FORWARD",
        })

    return {
        "nodes": nodes + host_nodes,
        "links": links,
        "flow_table": flow_table,
        "rule_count": len(flows_data),
        "error": None,
    }


def _fallback_topology_as_d3(topology: dict[str, Any]) -> dict[str, Any]:
    """ONOS 미접속 시 `data/gold/topology_eval.json`을 D3 노드/링크로 변환."""
    switch_device_id: dict[str, str] = {}
    for entity in topology.get("entities", []):
        if not entity["id"].startswith("device:"):
            continue
        short = entity["id"].split(":", 1)[1]
        device_id = next((a for a in entity["aliases"] if a.startswith("of:")), None)
        if device_id:
            switch_device_id[short] = device_id

    nodes = [
        {"id": device_id, "label": short.upper(), "type": "switch", "state": "idle"}
        for short, device_id in switch_device_id.items()
    ]

    host_ip: dict[str, str] = {}
    for entity in topology.get("entities", []):
        if not entity["id"].startswith("host:"):
            continue
        short = entity["id"].split(":", 1)[1]
        ip = next((a for a in entity["aliases"] if a.count(".") == 3 and "/" not in a), "")
        host_ip[short] = ip

    seen: set[tuple[str, str]] = set()
    links = []
    for sw, ports in topology.get("wiring", {}).items():
        sw_device_id = switch_device_id.get(sw)
        if sw_device_id is None:
            continue
        for neighbor in ports.values():
            if neighbor in switch_device_id:
                key = tuple(sorted([sw_device_id, switch_device_id[neighbor]]))
                if key not in seen:
                    seen.add(key)
                    links.append({"source": key[0], "target": key[1]})
            elif neighbor in host_ip:
                nodes.append({
                    "id": neighbor, "label": neighbor.upper(), "type": "host",
                    "ip": host_ip[neighbor], "switch": sw_device_id,
                })
                links.append({"source": neighbor, "target": sw_device_id})

    return {"nodes": nodes, "links": links, "flow_table": [], "rule_count": 0, "error": None}


@app.get("/api/topology")
def api_topology() -> dict[str, Any]:
    try:
        client = OnosClient(base_url=config.ONOS_URL, username=config.ONOS_USER, password=config.ONOS_PASSWORD, timeout=2.0)
        return _onos_topology_as_d3(client)
    except OnosError:
        pass
    try:
        return _fallback_topology_as_d3(_default_topology())
    except (OSError, json.JSONDecodeError) as exc:
        return {"nodes": [], "links": [], "flow_table": [], "rule_count": 0, "error": str(exc)}


# ── Logs (runctx 매니페스트 기반) ───────────────────────────────────────────

def _run_dirs() -> list[Path]:
    runs_root = config.LOGS_DIR / "runs"
    return [p.parent for p in runs_root.glob("*/*/manifest.json")]


@app.get("/api/logs")
def api_logs() -> list[dict[str, Any]]:
    entries = []
    for run_dir in sorted(_run_dirs(), key=lambda p: (p / "manifest.json").stat().st_mtime, reverse=True)[:10]:
        try:
            data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append({
            "run_id": data.get("run_id", ""),
            "intent": data.get("intent_id", ""),
            "decision": data.get("final_decision"),
            "timestamp": data.get("date", ""),
        })
    return entries


@app.delete("/api/logs", dependencies=[Depends(_require_api_key)])
def api_clear_logs() -> dict[str, Any]:
    deleted = 0
    for run_dir in _run_dirs():
        shutil.rmtree(run_dir, ignore_errors=True)
        deleted += 1
    return {"ok": True, "deleted": deleted}


# ── Static files (must be last — mounts "/") ───────────────────────────────
_static = _PACKAGE_DIR / "static"
_static.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")
