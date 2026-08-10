"""tests/test_twin.py — Stage 7 완료 기준: QoS 대역폭이 pass/fail 판정을 반환.

원본: 없음 (신규). docs/plan.md Stage 7 참고.

`twin_verifier.verify()` 자체(Mininet + ONOS 실배포)는 Linux + root + Mininet이
없는 이 개발 환경과 CI(ubuntu-latest, non-root, mn 미설치) 양쪽 모두에서
`_check_platform()`이 non-empty 사유를 반환해 스킵되므로 여기서 직접 실행할 수
없다 — 대신 플랫폼 유무와 무관하게 항상 실행 가능한 순수 로직(대역폭 판정,
토폴로지 헬퍼, 컴파일된 flow에서 intent_spec을 뽑는 파서)과 플랫폼 게이팅
자체를 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from tiger_sdn.compile import compile_prediction
from tiger_sdn.ir import from_research
from tiger_sdn.twin import TwinResult, TwinVerifier
from tiger_sdn.twin.bandwidth import _parse_mbps, meets_target
from tiger_sdn.twin.topology import get_expected_device_ids, get_test_host_pairs
from tiger_sdn.twin.twin_verifier import TwinVerifier as _TwinVerifierImpl

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_PATH = ROOT / "data" / "gold" / "topology_eval.json"


def _load_endpoint_ips() -> dict[str, str]:
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


# ── bandwidth ────────────────────────────────────────────────────────────────


def test_meets_target_within_default_tolerance():
    # tolerance 0.85 -> 8.5 Mbps는 10 Mbps 목표를 충족한다.
    assert meets_target(8.6, 10.0) is True


def test_meets_target_below_default_tolerance():
    assert meets_target(8.0, 10.0) is False


def test_parse_mbps_tcp_uses_sum_received():
    raw = "some iperf3 stderr noise\n" + json.dumps(
        {"end": {"sum_received": {"bits_per_second": 9_400_000.0}}}
    )
    mbps, parsed = _parse_mbps(raw, udp=False)
    assert parsed is True
    assert mbps == 9.4


def test_parse_mbps_udp_uses_sum():
    raw = json.dumps({"end": {"sum": {"bits_per_second": 5_000_000.0}}})
    mbps, parsed = _parse_mbps(raw, udp=True)
    assert parsed is True
    assert mbps == 5.0


def test_parse_mbps_unparseable_is_not_parsed_not_silently_zero():
    mbps, parsed = _parse_mbps("connect failed: connection refused", udp=False)
    assert parsed is False
    assert mbps == 0.0


# ── topology ─────────────────────────────────────────────────────────────────


def test_default_diamond_expects_four_devices():
    assert get_expected_device_ids(None) == {
        "of:0000000000000001",
        "of:0000000000000002",
        "of:0000000000000003",
        "of:0000000000000004",
    }


def test_default_diamond_host_pairs():
    assert get_test_host_pairs(None) == (("h1", "h4"), ("h2", "h3"))


def test_custom_topology_expects_its_own_devices():
    custom = {"switches": [{"id": "s1", "dpid": "0" * 16}, {"id": "s2", "dpid": "1" * 15 + "2"}]}
    assert get_expected_device_ids(custom) == {"of:" + "0" * 16, "of:" + "1" * 15 + "2"}


def test_custom_topology_host_pairs_with_four_or_more_hosts():
    custom = {"hosts": [{"id": "h1"}, {"id": "h2"}, {"id": "h3"}, {"id": "h4"}, {"id": "h5"}]}
    assert get_test_host_pairs(custom) == (("h1", "h5"), ("h2", "h3"))


def test_custom_topology_host_pairs_with_two_hosts_reuses_pair():
    custom = {"hosts": [{"id": "a"}, {"id": "b"}]}
    assert get_test_host_pairs(custom) == (("a", "b"), ("a", "b"))


# ── TwinVerifier._extract_intent_specs against real compiled GOLD-350 flows ──


def _compile(rules: list[dict]):
    prediction = from_research({"status": "accepted", "program": {"rules": rules}})
    return compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)


def test_extract_intent_specs_forward_rule_with_ips_and_port():
    flow_set = _compile(
        [
            {
                "intent_type": "forwarding",
                "action": "forward",
                "selector": {
                    "source": {"host": "h1"},
                    "destination": {"host": "h2"},
                    "protocol": "tcp",
                    "destination_port": 22,
                },
            }
        ]
    )
    specs = TwinVerifier._extract_intent_specs(flow_set.model_dump())
    assert len(specs) == 1
    action, src, dst, proto, port, flow = specs[0]
    assert action == "forward"
    assert src == "10.0.0.1"
    assert dst == "10.0.0.2"
    assert proto == "tcp"
    assert port == 22


def test_extract_intent_specs_block_rule_has_no_treatment_and_block_action():
    flow_set = _compile(
        [
            {
                "intent_type": "security",
                "action": "deny",
                "selector": {"source": {"host": "h1"}, "destination": {"host": "h2"}},
            }
        ]
    )
    specs = TwinVerifier._extract_intent_specs(flow_set.model_dump())
    assert len(specs) == 1
    action, _src, _dst, _proto, _port, flow = specs[0]
    assert action == "block"
    assert flow["treatment"] is None


def test_extract_intent_specs_compound_yields_one_spec_per_flow():
    flow_set = _compile(
        [
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
                "selector": {"destination": {"host": "h2"}, "protocol": "tcp", "destination_port": 22},
            },
        ]
    )
    specs = TwinVerifier._extract_intent_specs(flow_set.model_dump())
    assert len(specs) == 2
    assert [s[0] for s in specs] == ["forward", "block"]


def test_egress_port_none_for_block_and_set_for_forward():
    flow_set = _compile(
        [
            {
                "intent_type": "security",
                "action": "deny",
                "selector": {"source": {"host": "h1"}, "destination": {"host": "h2"}},
            },
            {
                "intent_type": "forwarding",
                "action": "forward",
                "selector": {"source": {"host": "h1"}, "destination": {"host": "h3"}},
                "enforcement": {"device": "of:0000000000000001", "egress_port": 7},
            },
        ]
    )
    block_flow, forward_flow = (f.model_dump() for f in flow_set.flows)
    assert TwinVerifier._egress_port(block_flow) is None
    assert TwinVerifier._egress_port(forward_flow) == "7"


# ── platform gating ───────────────────────────────────────────────────────────


def test_check_platform_reports_a_reason_on_this_host():
    # 이 스위트는 Linux+root+Mininet 3박자가 모두 갖춰진 환경(twin의 실제
    # 실행 대상)에서 돌지 않는다 — 개발 머신(Windows)도 CI(ubuntu-latest,
    # non-root, mn 미설치)도 아니다. 어느 쪽이든 사유가 비어있지 않아야 한다.
    reason = _TwinVerifierImpl._check_platform()
    assert isinstance(reason, str)
    assert reason != ""


def test_verify_skips_gracefully_without_mininet_or_onos():
    flow_set = _compile(
        [
            {
                "intent_type": "forwarding",
                "action": "forward",
                "selector": {"source": {"host": "h1"}, "destination": {"host": "h4"}},
            }
        ]
    )
    result = TwinVerifier().verify(flow_set)
    assert isinstance(result, TwinResult)
    assert result.status == "skipped"
    assert result.reason != ""
