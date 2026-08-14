"""src/tiger_sdn/verify/grounding.py — Intent IR을 토폴로지 인벤토리에 대해 그라운딩 검증.

원본: sdn-intent-framework의 research/safe_intent_sdn/validator.py
(`_check_references`/`_check_feasibility`/`_check_conflicts`). 통합 IR
(`tiger_sdn.ir.model`)의 필드 이름에 맞춰 옮겼다 — 연구 스키마와의 대응:

  - `TrafficSelector.ingress_port` → `IntentSelector.in_port`
  - `TrafficSelector.source_port`/`destination_port` → `src_port`/`dst_port`
  - `Enforcement.avoid_device` → `IntentRouting.avoid_device`
    (tiger_sdn IR은 avoid_device를 enforcement가 아니라 routing에 둔다)

`_check_path_constraints`(SFC 체인 연속성/순서, reroute avoid_device)는 Stage 8
(Exp-2 sfc/reroute 확장 데이터셋, `docs/plan.md` Stage 8 "착수 시 결정 사항"
참고)에서 이식했다. 연구 스키마는 `IntentProgram.sfc_chain`이라는 프로그램
레벨 필드로 웨이포인트를 표현했지만, tiger_sdn IR은 그 개념이 없다 — 대신
`ir/adapter.py`의 `from_research()`가 변환 시 그 체인을 sfc 규칙마다
`routing.waypoints`에 그대로 복제해 둔다. 그래서 `_sfc_chain_of()`가 첫 sfc
규칙의 `routing.waypoints`를 프로그램의 체인으로 취급해 원본 로직을
그대로 적용한다.

── device 미지정 시 명시적 거부 ───────────────────────────────────────────
`compile/compiler.py`의 `_resolve_device_id`는 `enforcement`가 없거나
`enforcement.device`가 없으면 조용히 기본 스위치(`of:...0001`)로 폴백한다
(GOLD-350 accepted 217/408건이 enforcement 자체가 없어서 채택한 관대함).
이 폴백은 컴파일을 막지 않지만, 배치 위치가 실제로는 미지정이었다는 사실은
사라진다. 정적 검증은 컴파일과 달리 "배포해도 안전한가"를 판정하는 계층이므로
여기서는 그 관대함을 유지하지 않는다 — enforcement.device가 없는 규칙은
`missing_device`로 명시 거부한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from tiger_sdn.ir.model import EndpointRef, IntentProgram, IntentRule, IntentSelector, StrictModel

from .topology import TopologyInventory

__all__ = ["FindingCategory", "ValidationFinding", "ValidationReport", "verify_program"]

FindingCategory = Literal["reference", "feasibility", "conflict", "path"]


class ValidationFinding(StrictModel):
    category: FindingCategory
    code: str
    rule_indices: list[int] = Field(min_length=1)
    message: str


class ValidationReport(StrictModel):
    findings: list[ValidationFinding] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.findings


def verify_program(program: IntentProgram, inventory: TopologyInventory) -> ValidationReport:
    findings = [
        *_check_references(program, inventory),
        *_check_feasibility(program, inventory),
        *_check_conflicts(program, inventory),
        *_check_path_constraints(program, inventory),
    ]
    return ValidationReport(findings=findings)


def _check_references(program: IntentProgram, inventory: TopologyInventory) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for index, rule in enumerate(program.rules):
        for endpoint in (rule.selector.source, rule.selector.destination):
            if endpoint is None:
                continue
            if endpoint.host is not None and endpoint.host not in inventory.aliases:
                findings.append(
                    ValidationFinding(
                        category="reference", code="unknown_host", rule_indices=[index],
                        message=f"rule {index}: unknown host {endpoint.host!r}",
                    )
                )
            elif endpoint.ip is not None and endpoint.ip not in inventory.aliases:
                findings.append(
                    ValidationFinding(
                        category="reference", code="unknown_ip", rule_indices=[index],
                        message=f"rule {index}: unknown IP {endpoint.ip!r}",
                    )
                )
        device = rule.enforcement.device if rule.enforcement else None
        if device is None:
            findings.append(
                ValidationFinding(
                    category="reference", code="missing_device", rule_indices=[index],
                    message=f"rule {index}: no enforcement.device — refusing to silently default a switch",
                )
            )
            continue
        canonical = inventory.aliases.get(device)
        if canonical is None or not canonical.startswith("device:"):
            findings.append(
                ValidationFinding(
                    category="reference", code="unknown_device", rule_indices=[index],
                    message=f"rule {index}: unknown device {device!r}",
                )
            )
    return findings


def _check_feasibility(program: IntentProgram, inventory: TopologyInventory) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for index, rule in enumerate(program.rules):
        enforcement = rule.enforcement
        device = enforcement.device if enforcement else None
        if device is None:
            continue  # 이미 _check_references가 missing_device로 보고
        ports = inventory.device_ports.get(inventory.aliases.get(device, device))
        if ports is None:
            continue  # 미지 장치는 이미 _check_references가 보고
        if enforcement.egress_port is not None and enforcement.egress_port not in ports:
            findings.append(
                ValidationFinding(
                    category="feasibility", code="egress_port_out_of_range", rule_indices=[index],
                    message=f"rule {index}: egress_port {enforcement.egress_port!r} not valid on {device!r}",
                )
            )
        if rule.selector.in_port is not None and rule.selector.in_port not in ports:
            findings.append(
                ValidationFinding(
                    category="feasibility", code="ingress_port_out_of_range", rule_indices=[index],
                    message=f"rule {index}: in_port {rule.selector.in_port} not valid on {device!r}",
                )
            )
    return findings


# compile/compiler.py의 compile_prediction 기본값과 맞춰야 한다 — priority가 없는
# 규칙은 인덱스가 빠를수록 높은 priority를 받는다(priority_start부터 priority_step씩 감소).
_DEFAULT_PRIORITY_START = 40000
_DEFAULT_PRIORITY_STEP = 1


def _effective_priority(index: int, rule: IntentRule) -> int:
    if rule.priority is not None:
        return rule.priority
    return _DEFAULT_PRIORITY_START - index * _DEFAULT_PRIORITY_STEP


def _check_conflicts(program: IntentProgram, inventory: TopologyInventory) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    rules = program.rules
    priorities = [_effective_priority(index, rule) for index, rule in enumerate(rules)]
    for i in range(len(rules)):
        device_i = _device_of(rules[i], inventory)
        if device_i is None:
            continue
        for j in range(i + 1, len(rules)):
            if rules[i].action == rules[j].action:
                continue
            if _device_of(rules[j], inventory) != device_i:
                continue
            # 인덱스가 아니라 컴파일러가 실제로 부여할 priority로 우열을 가린다 —
            # 명시적 rule.priority가 인덱스 순서를 뒤집을 수 있다.
            higher, lower = (i, j) if priorities[i] >= priorities[j] else (j, i)
            if _selector_covers(rules[higher].selector, rules[lower].selector, inventory.aliases):
                findings.append(
                    ValidationFinding(
                        category="conflict", code="shadowed_rule", rule_indices=[higher, lower],
                        message=f"rule {lower} is shadowed by higher-priority rule {higher} with a different action",
                    )
                )
    return findings


def _device_of(rule: IntentRule, inventory: TopologyInventory) -> str | None:
    device = rule.enforcement.device if rule.enforcement else None
    if device is None:
        return None
    return inventory.aliases.get(device, device)


def _selector_covers(general: IntentSelector, specific: IntentSelector, aliases: dict[str, str]) -> bool:
    for field in ("eth_type", "protocol", "src_port", "dst_port", "in_port"):
        gval = getattr(general, field)
        if gval is not None and gval != getattr(specific, field):
            return False
    for field in ("source", "destination"):
        gep: EndpointRef | None = getattr(general, field)
        if gep is None:
            continue
        sep: EndpointRef | None = getattr(specific, field)
        if sep is None or _canonical_endpoint(gep, aliases) != _canonical_endpoint(sep, aliases):
            return False
    return True


def _canonical_endpoint(endpoint: EndpointRef, aliases: dict[str, str]) -> str:
    spelling = endpoint.host or endpoint.ip or ""
    return aliases.get(spelling, spelling)


def _check_path_constraints(program: IntentProgram, inventory: TopologyInventory) -> list[ValidationFinding]:
    """SFC 체인 연속성/순서, reroute avoid_device 검사.

    reference(엔티티 존재)/conflict(쌍별 shadowing)/feasibility(단일 규칙
    포트 범위) 어디에도 속하지 않는 교차 규칙/메타데이터 문제라 별도
    path 카테고리로 분리했다.
    """
    return [
        *_check_sfc_chain(program, inventory),
        *_check_sfc_role_order(program),
        *_check_avoid_device(program, inventory),
    ]


def _sfc_chain_of(program: IntentProgram) -> list[str] | None:
    """SFC 규칙들의 routing.waypoints에서 프로그램 레벨 체인을 복원한다.

    from_research()가 연구 스키마의 program.sfc_chain을 sfc 규칙마다
    동일하게 routing.waypoints로 복제해 두므로, 첫 sfc 규칙의 waypoints를
    그 프로그램의 체인으로 취급한다.
    """
    for rule in program.rules:
        if rule.action == "sfc" and rule.routing and rule.routing.waypoints:
            return rule.routing.waypoints
    return None


def _parse_chain_token(token: str) -> tuple[str, str | None]:
    if token.count(":") <= 1:
        return token, None
    device, port = token.rsplit(":", 1)
    return device, port


def _coerce_port(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _check_sfc_waypoints_exist(rule: IntentRule, inventory: TopologyInventory) -> list[ValidationFinding]:
    """단일 규칙 SFC(제품 스키마 — `prompts/intent_ir.md` + `ir.adapter.
    from_gold350()`가 만드는 실제 라이브 파서 출력 형태)의 ``routing.
    waypoints`` 각 토큰이 실제 존재하는 device:port인지만 확인한다.

    연구 스키마(다중 규칙, `from_research()`)와 달리 다음 규칙이 없어
    "체인이 규칙 순서와 일치하는가"를 비교할 대상 자체가 없다 —
    `enforcement.device`/`egress_port`(이 규칙 자신의 진입점)는 이미
    `_check_references`/`_check_feasibility`가 모든 규칙에 대해 일반적으로
    검사하므로, 여기서는 체인 나머지 홉(웨이포인트 리스트)만 본다.
    """
    findings: list[ValidationFinding] = []
    waypoints = (rule.routing.waypoints if rule.routing else None) or []
    for token in waypoints:
        device_part, port_part = _parse_chain_token(token)
        device = inventory.aliases.get(device_part, device_part)
        ports = inventory.device_ports.get(device)
        if ports is None:
            findings.append(
                ValidationFinding(
                    category="reference", code="unknown_device", rule_indices=[0],
                    message=f"sfc waypoint {token!r} references unknown device {device_part!r}",
                )
            )
            continue
        if port_part is not None:
            port = _coerce_port(port_part)
            if port is None or port not in ports:
                findings.append(
                    ValidationFinding(
                        category="feasibility", code="sfc_waypoint_port_out_of_range", rule_indices=[0],
                        message=f"sfc waypoint {token!r}: port {port_part} not valid on {device_part!r}",
                    )
                )
    return findings


def _check_sfc_chain(program: IntentProgram, inventory: TopologyInventory) -> list[ValidationFinding]:
    rules = program.rules
    sfc_indices = [i for i, rule in enumerate(rules) if rule.action == "sfc"]
    if not sfc_indices:
        return []
    if len(rules) == 1:
        return _check_sfc_waypoints_exist(rules[0], inventory)
    chain = _sfc_chain_of(program) or []
    if len(chain) != len(rules) - 1:
        return [
            ValidationFinding(
                category="path", code="path_chain_length_mismatch", rule_indices=list(range(len(rules))),
                message=f"sfc chain has {len(chain)} entries but program has {len(rules)} rules (expected {len(rules) - 1})",
            )
        ]
    findings: list[ValidationFinding] = []
    for k in range(1, len(rules)):
        token = chain[k - 1]
        device_part, port_part = _parse_chain_token(token)
        token_device = inventory.aliases.get(device_part, device_part)
        rule_device = _device_of(rules[k], inventory)
        if rule_device is None or token_device != rule_device:
            findings.append(
                ValidationFinding(
                    category="path", code="path_waypoint_device_mismatch", rule_indices=[k - 1, k],
                    message=(
                        f"sfc chain[{k - 1}]={token!r} does not match rule {k}'s device "
                        f"{rules[k].enforcement.device if rules[k].enforcement else None!r}"
                    ),
                )
            )
            continue
        ports = inventory.device_ports.get(token_device)
        if ports is None:
            findings.append(
                ValidationFinding(
                    category="path", code="path_unknown_waypoint", rule_indices=[k - 1, k],
                    message=f"sfc chain[{k - 1}]={token!r} references an unknown device",
                )
            )
            continue
        token_port = _coerce_port(port_part) if port_part is not None else None
        if port_part is not None and (token_port is None or token_port not in ports):
            findings.append(
                ValidationFinding(
                    category="path", code="path_waypoint_port_out_of_range", rule_indices=[k - 1, k],
                    message=f"sfc chain[{k - 1}]={token!r} port not valid on {device_part!r}",
                )
            )
            continue
        prev_device = _device_of(rules[k - 1], inventory)
        if prev_device == token_device:
            prev_enforcement = rules[k - 1].enforcement
            prev_egress = prev_enforcement.egress_port if prev_enforcement else None
            next_ingress = rules[k].selector.in_port
            if token_port is None or prev_egress != token_port or next_ingress != token_port:
                findings.append(
                    ValidationFinding(
                        category="path", code="path_port_discontinuity", rule_indices=[k - 1, k],
                        message=f"same-device hop at rule {k} is not port-continuous with rule {k - 1}",
                    )
                )
    return findings


def _check_sfc_role_order(program: IntentProgram) -> list[ValidationFinding]:
    sfc_indices = [i for i, rule in enumerate(program.rules) if rule.action == "sfc"]
    if len(sfc_indices) <= 1:
        # 0개면 애초에 검사 대상이 없고, 1개면 순서를 어길 대상 자체가 없다
        # (단일 규칙 제품 스키마 SFC가 여기 해당 — sfc_role 필드가 아예 없음).
        return []
    roles = [program.rules[i].sfc_role for i in sfc_indices]
    invalid = (
        roles[0] != "ingress"
        or roles.count("ingress") > 1
        or ("egress" in roles and roles.index("egress") != len(roles) - 1)
    )
    if not invalid:
        return []
    return [
        ValidationFinding(
            category="path", code="path_role_order_invalid", rule_indices=sfc_indices,
            message=f"sfc_role sequence {roles} is not a valid ingress[...transit...][egress] order",
        )
    ]


def _check_avoid_device(program: IntentProgram, inventory: TopologyInventory) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for index, rule in enumerate(program.rules):
        routing = rule.routing
        if routing is None or routing.avoid_device is None:
            continue
        avoid = inventory.aliases.get(routing.avoid_device, routing.avoid_device)
        device = _device_of(rule, inventory)
        if device is not None and device == avoid:
            findings.append(
                ValidationFinding(
                    category="path", code="path_avoid_device_conflict", rule_indices=[index],
                    message=(
                        f"rule {index}: enforcement.device "
                        f"{rule.enforcement.device if rule.enforcement else None!r} conflicts with "
                        f"avoid_device {routing.avoid_device!r}"
                    ),
                )
            )
    return findings
