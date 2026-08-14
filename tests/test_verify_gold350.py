"""tests/test_verify_gold350.py — Stage 6 완료 기준: 그라운딩 검증이 미지
엔티티를 거부.

원본: 없음 (신규, 이식 대상 원본 stage3_static/{schema_validator,
conflict_detector,static_validator}.py와 research/safe_intent_sdn/validator.py
자체에는 테스트가 0개였다 — docs/plan.md Stage 6 "함께 처리" 참고).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiger_sdn.ir import IntentEnforcement, IntentProgram, IntentRule, IntentSelector, from_research
from tiger_sdn.verify import (
    TopologyInventory,
    detect_conflict,
    load_topology_inventory,
    validate,
    validate_schema,
    verify_program,
)

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold" / "gold.jsonl"
TOPOLOGY_PATH = ROOT / "data" / "gold" / "topology_eval.json"


@pytest.fixture(scope="module")
def inventory() -> TopologyInventory:
    return load_topology_inventory(json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8")))


def _program(rule: IntentRule) -> IntentProgram:
    return IntentProgram(rules=[rule])


# ── TopologyInventory 로딩 ──────────────────────────────────────────────


def test_load_topology_inventory_resolves_host_and_device_aliases(inventory: TopologyInventory):
    assert inventory.aliases["h1"] == "host:h1"
    assert inventory.aliases["10.0.0.1"] == "host:h1"
    assert inventory.aliases["s1"] == "device:s1"
    assert inventory.aliases["switch 1"] == "device:s1"
    assert inventory.device_ports["device:s1"] == frozenset({1, 2, 3, 4, 9})


# ── 그라운딩: 미지 엔티티 거부 (완료 기준) ──────────────────────────────


def test_verify_program_rejects_unknown_host(inventory: TopologyInventory):
    rule = IntentRule(
        action="forward",
        selector=IntentSelector(source={"host": "h99"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s1"),
    )
    report = verify_program(_program(rule), inventory)
    assert not report.is_valid
    assert any(f.code == "unknown_host" for f in report.findings)


def test_verify_program_rejects_unknown_ip(inventory: TopologyInventory):
    rule = IntentRule(
        action="forward",
        selector=IntentSelector(source={"ip": "192.168.1.1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s1"),
    )
    report = verify_program(_program(rule), inventory)
    assert any(f.code == "unknown_ip" for f in report.findings)


def test_verify_program_rejects_unknown_device(inventory: TopologyInventory):
    rule = IntentRule(
        action="forward",
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s99"),
    )
    report = verify_program(_program(rule), inventory)
    assert any(f.code == "unknown_device" for f in report.findings)


def test_verify_program_accepts_fully_grounded_rule(inventory: TopologyInventory):
    rule = IntentRule(
        action="forward",
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s1", egress_port=4),
    )
    report = verify_program(_program(rule), inventory)
    assert report.is_valid


# ── device 미지정 → 명시적 거부 (컴파일러의 조용한 폴백과 통일) ────────


def test_verify_program_rejects_missing_device_instead_of_defaulting(inventory: TopologyInventory):
    """compile/compiler.py의 `_resolve_device_id`는 enforcement.device가 없으면
    조용히 기본 스위치로 폴백한다 — 정적 검증은 그 관대함을 물려받지 않고
    명시적으로 거부해야 한다 (docs/plan.md Stage 6 "함께 처리")."""
    rule = IntentRule(action="forward", selector=IntentSelector(eth_type="ipv4"))
    assert rule.enforcement is None

    report = verify_program(_program(rule), inventory)
    assert not report.is_valid
    assert [f.code for f in report.findings] == ["missing_device"]


def test_gold350_accepted_cases_missing_enforcement_are_flagged(inventory: TopologyInventory):
    """docs/plan.md Stage 5에 따르면 accepted 408규칙 중 217건이 enforcement가
    없다 — 컴파일은 통과하지만 정적 검증은 그 규칙들을 명시적으로 걸러내야
    한다."""
    cases = [
        json.loads(line)
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    accepted = [c for c in cases if c["expected"]["status"] == "accepted"]

    missing_enforcement_flagged = 0
    for case in accepted:
        prediction = from_research(case["expected"])
        report = verify_program(prediction.program, inventory)
        flagged_indices = {f.rule_indices[0] for f in report.findings if f.code == "missing_device"}
        for index, rule in enumerate(prediction.program.rules):
            if rule.enforcement is None or rule.enforcement.device is None:
                assert index in flagged_indices
                missing_enforcement_flagged += 1

    assert missing_enforcement_flagged > 0


# ── 그라운딩: feasibility (포트 범위) ────────────────────────────────────


def test_verify_program_rejects_egress_port_out_of_range(inventory: TopologyInventory):
    rule = IntentRule(
        action="forward",
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s1", egress_port=99),
    )
    report = verify_program(_program(rule), inventory)
    assert any(f.code == "egress_port_out_of_range" for f in report.findings)


# ── 그라운딩: shadowed_rule 충돌 ─────────────────────────────────────────


def test_verify_program_detects_shadowed_rule(inventory: TopologyInventory):
    """더 일반적인 상위 규칙(모든 h1->h2)이 더 구체적인 하위 규칙(h1->h2 tcp/22)을
    다른 action으로 가리면 shadowed_rule 충돌이다."""
    general = IntentRule(
        action="block",
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s1"),
    )
    specific = IntentRule(
        action="forward",
        selector=IntentSelector(
            source={"host": "h1"}, destination={"host": "h2"}, protocol="tcp", dst_port=22
        ),
        enforcement=IntentEnforcement(device="s1"),
    )
    program = IntentProgram(rules=[general, specific])
    report = verify_program(program, inventory)
    assert any(f.code == "shadowed_rule" and f.rule_indices == [0, 1] for f in report.findings)


def test_verify_program_shadowed_rule_respects_explicit_priority_over_index(inventory: TopologyInventory):
    """rule.priority가 명시되면 인덱스가 아니라 그 값이 우열을 가른다 — 인덱스상
    나중에 오는 규칙이라도 더 높은 priority를 받으면 이긴다(컴파일러와 동일 규칙)."""
    general = IntentRule(
        action="block",
        priority=100,
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device="s1"),
    )
    specific = IntentRule(
        action="forward",
        priority=200,
        selector=IntentSelector(
            source={"host": "h1"}, destination={"host": "h2"}, protocol="tcp", dst_port=22
        ),
        enforcement=IntentEnforcement(device="s1"),
    )
    program = IntentProgram(rules=[general, specific])
    report = verify_program(program, inventory)
    assert not any(f.code == "shadowed_rule" for f in report.findings)


# ── FlowRule 스키마 검증 ──────────────────────────────────────────────


def test_validate_schema_accepts_well_formed_flow():
    result = validate_schema(
        {
            "flows": [
                {
                    "priority": 40000,
                    "deviceId": "of:0000000000000001",
                    "selector": {"criteria": [{"type": "ETH_TYPE", "ethType": "0x0800"}]},
                    "treatment": {"instructions": [{"type": "OUTPUT", "port": "2"}]},
                }
            ]
        }
    )
    assert result["valid"]
    assert result["errors"] == []


def test_validate_schema_rejects_bad_device_id_and_priority():
    result = validate_schema(
        {
            "flows": [
                {
                    "priority": 999999,
                    "deviceId": "not-a-device",
                    "selector": {"criteria": [{"type": "ETH_TYPE", "ethType": "0x0800"}]},
                }
            ]
        }
    )
    assert not result["valid"]
    assert len(result["errors"]) >= 2


# ── FlowRule 충돌 탐지 (5종) ─────────────────────────────────────────────


def _flow(*, priority: int, ip: str, action: str = "forward", device: str = "of:0000000000000001") -> dict:
    treatment = None if action == "block" else {"instructions": [{"type": "OUTPUT", "port": "2"}]}
    return {
        "priority": priority,
        "deviceId": device,
        "selector": {
            "criteria": [
                {"type": "ETH_TYPE", "ethType": "0x0800"},
                {"type": "IPV4_DST", "ip": ip},
            ]
        },
        "treatment": treatment,
    }


def test_detect_conflict_redundancy_for_identical_match_and_action():
    new = _flow(priority=100, ip="10.0.0.1/32")
    existing = _flow(priority=100, ip="10.0.0.1/32")
    [conflict] = detect_conflict(new, [existing])
    assert conflict["conflict_type"] == "Redundancy"


def test_detect_conflict_shadowing_when_higher_priority_covers_lower():
    new = _flow(priority=200, ip="10.0.0.0/24", action="block")
    existing = _flow(priority=100, ip="10.0.0.1/32", action="forward")
    [conflict] = detect_conflict(new, [existing])
    assert conflict["conflict_type"] == "Shadowing"


def test_detect_conflict_correlation_for_equal_priority_overlap_different_action():
    new = _flow(priority=100, ip="10.0.0.0/24", action="block")
    existing = _flow(priority=100, ip="10.0.0.5/32", action="forward")
    [conflict] = detect_conflict(new, [existing])
    assert conflict["conflict_type"] == "Imbrication"


def test_detect_conflict_none_for_disjoint_devices():
    new = _flow(priority=100, ip="10.0.0.1/32", device="of:0000000000000001")
    existing = _flow(priority=100, ip="10.0.0.1/32", device="of:0000000000000002")
    assert detect_conflict(new, [existing]) == []


def test_detect_conflict_generalization_for_broader_match_same_action():
    new = _flow(priority=100, ip="10.0.0.0/24", action="forward")
    existing = _flow(priority=90, ip="10.0.0.5/32", action="forward")
    [conflict] = detect_conflict(new, [existing])
    assert conflict["conflict_type"] == "Generalization"


# ── 그라운딩: path (SFC 체인 연속성/순서, reroute avoid_device) ─────────
#
# Stage 8(Exp-2 sfc/reroute 확장, docs/plan.md Stage 8 "착수 시 결정 사항")에서
# 이식. data/gold/topology_eval.json의 s1 포트 9가 "firewall/IDS service
# point (SFC waypoint)"로 이미 문서화되어 있어 그 배선을 그대로 쓴다.


def _sfc_rule(*, role: str, device: str, egress_port: int, waypoints: list[str], in_port: int | None = None) -> IntentRule:
    return IntentRule(
        action="sfc",
        sfc_role=role,
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}, in_port=in_port),
        enforcement=IntentEnforcement(device=device, egress_port=egress_port),
        routing={"waypoints": waypoints},
    )


def test_verify_program_accepts_continuous_sfc_chain(inventory: TopologyInventory):
    chain = ["of:0000000000000001:9"]
    ingress = _sfc_rule(role="ingress", device="s1", egress_port=9, waypoints=chain)
    egress = _sfc_rule(role="egress", device="s1", egress_port=4, waypoints=chain, in_port=9)
    report = verify_program(IntentProgram(rules=[ingress, egress]), inventory)
    assert report.is_valid


def test_verify_program_rejects_sfc_chain_length_mismatch(inventory: TopologyInventory):
    chain = ["of:0000000000000001:9"]
    ingress = _sfc_rule(role="ingress", device="s1", egress_port=9, waypoints=chain)
    transit = _sfc_rule(role="transit", device="s1", egress_port=9, waypoints=chain, in_port=9)
    egress = _sfc_rule(role="egress", device="s1", egress_port=4, waypoints=chain, in_port=9)
    report = verify_program(IntentProgram(rules=[ingress, transit, egress]), inventory)
    assert any(f.code == "path_chain_length_mismatch" for f in report.findings)


def test_verify_program_rejects_sfc_waypoint_device_mismatch(inventory: TopologyInventory):
    chain = ["of:0000000000000002:1"]  # 체인은 s2를 가리키지만 다음 규칙은 s1
    ingress = _sfc_rule(role="ingress", device="s1", egress_port=9, waypoints=chain)
    egress = _sfc_rule(role="egress", device="s1", egress_port=4, waypoints=chain, in_port=9)
    report = verify_program(IntentProgram(rules=[ingress, egress]), inventory)
    assert any(f.code == "path_waypoint_device_mismatch" for f in report.findings)


def test_verify_program_rejects_sfc_port_discontinuity(inventory: TopologyInventory):
    chain = ["of:0000000000000001:9"]
    ingress = _sfc_rule(role="ingress", device="s1", egress_port=9, waypoints=chain)
    # egress의 in_port(3)가 체인 토큰의 포트(9)와 불연속
    egress = _sfc_rule(role="egress", device="s1", egress_port=4, waypoints=chain, in_port=3)
    report = verify_program(IntentProgram(rules=[ingress, egress]), inventory)
    assert any(f.code == "path_port_discontinuity" for f in report.findings)


def test_verify_program_rejects_sfc_role_order_duplicate_ingress(inventory: TopologyInventory):
    chain = ["of:0000000000000001:9"]
    first = _sfc_rule(role="ingress", device="s1", egress_port=9, waypoints=chain)
    second = _sfc_rule(role="ingress", device="s1", egress_port=4, waypoints=chain, in_port=9)
    report = verify_program(IntentProgram(rules=[first, second]), inventory)
    assert any(f.code == "path_role_order_invalid" for f in report.findings)


# ── 그라운딩: 단일 규칙 SFC (제품 스키마) ────────────────────────────────
#
# 위 테스트들은 전부 다중 규칙(연구 스키마, from_research()가 sfc_chain을
# 규칙마다 복제)을 쓴다. 실제 배포된 프롬프트(prompts/intent_ir.md)와
# from_gold350()은 SFC를 단일 규칙 + routing.waypoints로 표현한다 —
# sfc_role 필드 자체가 없다. GOLD-350 SFC 50건 전부 이 형태(직접 확인).
# 이 두 체크(_check_sfc_chain/_check_sfc_role_order)가 원래 다중 규칙만
# 가정하고 있어서 실제 라이브 파서 출력을 항상 거부하던 버그를 고쳤다
# (이슈 #40).


def _single_rule_sfc(*, device: str, egress_port: int, waypoints: list[str]) -> IntentRule:
    return IntentRule(
        action="sfc",
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h2"}),
        enforcement=IntentEnforcement(device=device, egress_port=egress_port),
        routing={"waypoints": waypoints},
    )


def test_verify_program_accepts_single_rule_sfc_with_valid_waypoints(inventory: TopologyInventory):
    rule = _single_rule_sfc(device="s1", egress_port=9, waypoints=["of:0000000000000001:9"])
    report = verify_program(IntentProgram(rules=[rule]), inventory)
    assert report.is_valid


def test_verify_program_rejects_single_rule_sfc_unknown_waypoint_device(inventory: TopologyInventory):
    rule = _single_rule_sfc(device="s1", egress_port=9, waypoints=["no-such-switch:1"])
    report = verify_program(IntentProgram(rules=[rule]), inventory)
    assert any(f.code == "unknown_device" for f in report.findings)


def test_verify_program_rejects_single_rule_sfc_waypoint_port_out_of_range(inventory: TopologyInventory):
    rule = _single_rule_sfc(device="s1", egress_port=9, waypoints=["of:0000000000000001:999"])
    report = verify_program(IntentProgram(rules=[rule]), inventory)
    assert any(f.code == "sfc_waypoint_port_out_of_range" for f in report.findings)


def test_verify_program_all_gold350_sfc_cases_pass_grounding():
    """GOLD-350 SFC 50건 전부(실제 from_gold350() 변환 형태)가 그라운딩을
    통과해야 한다 — 이슈 #40의 회귀 방지. 고치기 전엔 50/50 전부 거부됐다."""
    import json
    from pathlib import Path

    from tiger_sdn.ir import from_gold350
    from tiger_sdn.verify import load_topology_inventory

    root = Path(__file__).resolve().parents[1]
    topology = json.loads((root / "data/gold/topology_eval.json").read_text(encoding="utf-8"))
    live_inventory = load_topology_inventory(topology)

    lines = (root / "data/gold/gold350_eval.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    sfc_cases = [c for c in cases if c["category"] == "sfc"]
    assert len(sfc_cases) == 50

    failures = []
    for case in sfc_cases:
        gold = case["gold"]
        prediction = from_gold350({"status": "accepted", "rules": [{k: v for k, v in gold.items() if k != "status"}]})
        report = verify_program(prediction.program, live_inventory)
        if not report.is_valid:
            failures.append((case["case_id"], [f.code for f in report.findings]))
    assert failures == []


def test_verify_program_rejects_reroute_avoid_device_conflict(inventory: TopologyInventory):
    rule = IntentRule(
        action="reroute",
        selector=IntentSelector(source={"host": "h1"}, destination={"host": "h4"}),
        enforcement=IntentEnforcement(device="s1", egress_port=1),
        routing={"avoid_device": "s1"},
    )
    report = verify_program(_program(rule), inventory)
    assert any(f.code == "path_avoid_device_conflict" for f in report.findings)


# ── static.validate: compound 내부 충돌의 CIDR 겹침 버그 수정 ────────────


def _compound_flow(*, priority: int, ip: str, action: str) -> dict:
    treatment = None if action == "block" else {"instructions": [{"type": "OUTPUT", "port": "2"}]}
    return {
        "priority": priority,
        "deviceId": "of:0000000000000001",
        "selector": {
            "criteria": [
                {"type": "ETH_TYPE", "ethType": "0x0800"},
                {"type": "IPV4_SRC", "ip": ip},
                {"type": "IPV4_DST", "ip": "10.0.0.2/32"},
                {"type": "IP_PROTO", "protocol": 6},
            ]
        },
        "treatment": treatment,
    }


def test_static_validate_flags_compound_intra_conflict_on_overlapping_but_unequal_cidrs():
    """두 규칙의 IPV4_SRC 문자열이 다르지만(10.0.0.0/24 vs 10.0.0.5/32) 실제로는
    겹친다 — 이식 전 버전은 딕셔너리 완전 일치만 보므로 이 쌍을 놓쳤다."""
    flow_a = _compound_flow(priority=100, ip="10.0.0.0/24", action="forward")
    flow_b = _compound_flow(priority=90, ip="10.0.0.5/32", action="block")
    result = validate({"intent_action": "compound", "flows": [flow_a, flow_b]})
    assert not result.passed
    assert any(c.get("conflict_type") == "Intra-Shadowing" for c in result.conflicts)


def test_static_validate_no_intra_conflict_for_non_overlapping_cidrs():
    flow_a = _compound_flow(priority=100, ip="10.0.0.0/30", action="forward")
    flow_b = _compound_flow(priority=90, ip="10.0.1.0/24", action="block")
    result = validate({"intent_action": "compound", "flows": [flow_a, flow_b]})
    assert result.passed
