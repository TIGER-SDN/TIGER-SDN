"""src/tiger_sdn/verify/grounding.py — Intent IR을 토폴로지 인벤토리에 대해 그라운딩 검증.

원본: sdn-intent-framework의 research/safe_intent_sdn/validator.py
(`_check_references`/`_check_feasibility`/`_check_conflicts`). 통합 IR
(`tiger_sdn.ir.model`)의 필드 이름에 맞춰 옮겼다 — 연구 스키마와의 대응:

  - `TrafficSelector.ingress_port` → `IntentSelector.in_port`
  - `TrafficSelector.source_port`/`destination_port` → `src_port`/`dst_port`
  - `Enforcement.avoid_device` → `IntentRouting.avoid_device`
    (tiger_sdn IR은 avoid_device를 enforcement가 아니라 routing에 둔다)

`_check_path_constraints`(SFC 체인 연속성)는 포팅하지 않았다 — 연구 스키마는
`IntentProgram.sfc_chain`이라는 프로그램 레벨 필드로 웨이포인트를 표현했지만,
tiger_sdn IR은 그 개념이 없다(각 규칙이 자기 몫의 `enforcement.device`를 이미
들고 있음, `compile/compiler.py` docstring 참고). 필요해지면 규칙별
`sfc_role` 순서를 직접 비교하는 방식으로 별도 구현한다.

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

FindingCategory = Literal["reference", "feasibility", "conflict"]


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
