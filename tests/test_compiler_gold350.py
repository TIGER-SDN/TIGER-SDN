"""tests/test_compiler_gold350.py — Stage 5 완료 기준: gold accepted 300건이
컴파일 에러 없이 통과.

원본: 없음 (신규). docs/plan.md Stage 5 참고.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiger_sdn.compile import CompilationError, compile_prediction
from tiger_sdn.compile.onos import OnosFlowSet
from tiger_sdn.ir import from_research

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold" / "gold.jsonl"
TOPOLOGY_PATH = ROOT / "data" / "gold" / "topology_eval.json"


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_endpoint_ips() -> dict[str, str]:
    """topology_eval.json의 host 엔티티에서 alias(호스트명) -> IP 매핑을 뽑는다."""
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    endpoint_ips: dict[str, str] = {}
    for entity in topology["entities"]:
        if not entity["id"].startswith("host:"):
            continue
        aliases = entity["aliases"]
        host_name = aliases[0]
        ip = next(a for a in aliases[1:] if "/" not in a)
        endpoint_ips[host_name] = ip
    return endpoint_ips


ENDPOINT_IPS = _load_endpoint_ips()


def test_endpoint_ips_cover_all_hosts_in_topology():
    assert ENDPOINT_IPS == {"h1": "10.0.0.1", "h2": "10.0.0.2", "h3": "10.0.0.3", "h4": "10.0.0.4"}


def test_all_accepted_cases_compile_without_error():
    cases = _load_cases()
    accepted = [c for c in cases if c["expected"]["status"] == "accepted"]
    assert len(accepted) == 300

    failures = []
    for case in accepted:
        prediction = from_research(case["expected"])
        try:
            flow_set = compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)
        except CompilationError as exc:
            failures.append((case["id"], repr(exc)))
            continue
        assert isinstance(flow_set, OnosFlowSet)
        assert len(flow_set.flows) == len(case["expected"]["program"]["rules"])

    assert failures == []


def test_rejected_prediction_cannot_be_compiled():
    prediction = from_research({"status": "rejected", "rejection": {"reason": "ambiguous"}})
    with pytest.raises(CompilationError):
        compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)


def test_block_rule_has_no_treatment():
    prediction = from_research(
        {
            "status": "accepted",
            "program": {
                "rules": [
                    {
                        "intent_type": "security",
                        "action": "deny",
                        "selector": {"source": {"host": "h1"}, "destination": {"host": "h2"}},
                        "enforcement": {"device": "of:0000000000000001"},
                    }
                ]
            },
        }
    )
    flow_set = compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)
    assert flow_set.flows[0].treatment is None


def test_missing_enforcement_falls_back_to_default_device_and_normal_port():
    """`enforcement`가 전혀 없는 규칙(GOLD-350 accepted 408건 중 217건)도
    컴파일이 에러 없이 통과해야 한다 — 배치는 IR이 표현하는 범위 밖이라
    조용히 기본값(switch 1, 포트 NORMAL)으로 채운다. 이 폴백을 명시적 거부로
    바꾸는 일은 Stage 6(정적 검증)의 몫이다."""
    prediction = from_research(
        {
            "status": "accepted",
            "program": {
                "rules": [
                    {
                        "intent_type": "forwarding",
                        "action": "forward",
                        "selector": {"eth_type": "ipv4"},
                    }
                ]
            },
        }
    )
    flow_set = compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)
    flow = flow_set.flows[0]
    assert flow.deviceId == "of:0000000000000001"
    assert flow.treatment.instructions[0].port == "NORMAL"


def test_device_hint_accepts_natural_language_forms():
    from tiger_sdn.compile.compiler import _resolve_device_id

    assert _resolve_device_id("of:0000000000000004") == "of:0000000000000004"
    assert _resolve_device_id("switch 4") == "of:0000000000000004"
    assert _resolve_device_id("s4") == "of:0000000000000004"
    assert _resolve_device_id("switch second") == "of:0000000000000002"
    assert _resolve_device_id(None) == "of:0000000000000001"


def test_rule_order_is_preserved_as_decreasing_priority():
    """compound whitelist+blacklist(G-CMP-043류)에서 먼저 나온(더 구체적인)
    규칙이 더 높은 우선순위를 받아야 한다."""
    prediction = from_research(
        {
            "status": "accepted",
            "program": {
                "rules": [
                    {
                        "intent_type": "security",
                        "action": "allow",
                        "selector": {
                            "source": {"host": "h1"},
                            "destination": {"host": "h2"},
                            "protocol": "tcp",
                            "destination_port": 22,
                        },
                    },
                    {
                        "intent_type": "security",
                        "action": "deny",
                        "selector": {
                            "destination": {"host": "h2"},
                            "protocol": "tcp",
                            "destination_port": 22,
                        },
                    },
                ]
            },
        }
    )
    flow_set = compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)
    allow_flow, deny_flow = flow_set.flows
    assert allow_flow.priority > deny_flow.priority
