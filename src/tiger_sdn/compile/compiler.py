"""src/tiger_sdn/compile/compiler.py — 통합 Intent IR -> ONOS FlowRule 결정론적 컴파일러.

원본: 직접 포팅이 아니라 두 원본 컴파일러의 설계를 통합 IR(`tiger_sdn.ir`)에 맞게
새로 조합했다.

  - `xai_pipeline`의 `stage2_flowrule/compiler.py` (340 LOC) — device_hint 유연
    파싱(`extract_device_id`), 배치 정보 미지정 시의 관대한 폴백(`device_hint`
    프로퍼티가 "switch 1"로, `out_port` 미지정이 ONOS 예약 포트 "NORMAL"로 폴백)을
    가져왔다.
  - `research/safe_intent_sdn/compiler.py` — `CompilationError` 이름(원래
    `CompileError`였음), host->ip 엔드포인트 해석과 eth_type/IP 버전 정합성 검사를
    가져왔다.

두 방식을 그대로 합칠 수 없었던 이유: `research` 쪽은 `enforcement.device`와
`qos.queue`를 항상 요구해 컴파일 에러를 던지는데, GOLD-350 accepted 408규칙 중
217건(53%)이 `enforcement` 자체가 없고 qos 규칙은 49/50건이 `queue` 없이
`min_bandwidth_mbps`만 갖는다 — 배치(device/port)와 큐 프로비저닝은 애초에 이
Intent IR이 표현하는 범위 밖이다(annotator가 "무엇을 할지"만 판정하고 "어디서"는
비워 뒀다). 엄격한 쪽을 그대로 쓰면 완료 기준(accepted 300건 무오류 컴파일)을
채울 수 없어서, 미지정 시 조용히 기본값으로 채우는 `xai_pipeline` 쪽 관대함을
기본으로 채택했다 — 이 폴백을 명시적 거부로 바꾸는 일은 Stage 6(정적 검증)의
몫이다(plan.md Stage 6 참고).

── SFC/reroute가 별도 로직 없이 forward와 동일하게 컴파일되는 이유 ──────────
`xai_pipeline`의 원 컴파일러는 SFC를 `waypoints[0]` 하나로부터 ingress/egress
flow 쌍을 합성했다(멀티스위치 체인이 첫 홉으로만 뭉개지는 버그의 근원, 이슈 #12).
GOLD-350(`from_research()` 경로)은 이 합성이 필요 없다 — N홉 체인을 컴파일러가
아니라 **annotator가 이미 N개의 개별 규칙으로 펼쳐 놓았다**. 각 규칙이
`sfc_role`(ingress/transit/egress)과 자기 몫의 `enforcement.device`/`egress_port`를
직접 들고 있어서, sfc/reroute 규칙은 그냥 forward 규칙과 똑같이 컴파일된다.
`routing.waypoints`는 컴파일에 쓰이지 않는 설명용 메타데이터로 남는다. 따라서
waypoints[0] 버그는 "고쳤다"기보다 IR 설계상 재현되지 않는다.

── 미지원 범위 ──────────────────────────────────────────────────────────
`enforcement.alt_egress_port`(하나의 규칙 안에 waypoint 포트 + 최종 egress 포트를
압축해 담는 `from_gold350()` 표현, 예: SFC를 waypoint+alt_egress_port 한 쌍으로
표현하는 단일 오브젝트 스타일)는 이 컴파일러가 아직 다루지 않는다 — GOLD-350
accepted 300건은 전부 `from_research()` 경로(사전 전개된 규칙)이고 이 필드를
쓰지 않는다. 필요해지면 별도로 이 함수를 확장한다.
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import Mapping, Optional

from tiger_sdn.ir import EndpointRef, IntentPrediction, IntentRule, IntentSelector

from .onos import (
    EthTypeCriterion,
    InPortCriterion,
    IpCriterion,
    OnosFlow,
    OnosFlowSet,
    OnosSelector,
    OnosTreatment,
    OutputInstruction,
    ProtocolCriterion,
    QueueInstruction,
    TransportCriterion,
    VlanInstruction,
)

__all__ = ["CompilationError", "compile_prediction"]


class CompilationError(ValueError):
    """유효한 IR을 안전하게 ONOS flow로 표현할 수 없을 때 발생한다."""


# 배치 정보가 전혀 없을 때의 레거시 폴백 — xai_pipeline의 `IntentIR.device_hint`
# 프로퍼티가 `enforcement.device`가 없으면 "switch 1"로 조용히 대체하던 동작과 동일.
_DEFAULT_DEVICE = "of:0000000000000001"

_ETH_TYPE_MAP = {"ipv4": "0x0800", "ipv6": "0x86DD", "arp": "0x0806"}
_IP_PROTO_MAP = {"icmp": 1, "tcp": 6, "udp": 17}

_SWITCH_ID_ORDINALS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
    "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10,
}  # fmt: skip

_DEVICE_ID_RE = re.compile(r"^of:[0-9a-f]{16}$", re.IGNORECASE)
_DEVICE_NUM_RE = re.compile(r"(?:switch(?:es)?|sw|s|node)\s*(\d+)", re.IGNORECASE)
_BARE_NUM_RE = re.compile(r"(\d+)")


def _resolve_device_id(device_hint: Optional[str]) -> str:
    """자연어 device_hint를 ONOS device ID로 정규화한다. 미지정 시 기본 스위치로 폴백."""
    if device_hint is None:
        return _DEFAULT_DEVICE
    hint = device_hint.strip()
    if not hint:
        return _DEFAULT_DEVICE

    if _DEVICE_ID_RE.match(hint):
        return hint.lower()

    specific = _DEVICE_NUM_RE.search(hint)
    if specific:
        return f"of:{int(specific.group(1)):016x}"

    hint_lower = hint.lower()
    for word, num in _SWITCH_ID_ORDINALS.items():
        if word in hint_lower:
            return f"of:{num:016x}"

    bare = _BARE_NUM_RE.search(hint)
    if bare:
        return f"of:{int(bare.group(1)):016x}"

    raise CompilationError(f"device_hint에서 스위치 번호를 파싱할 수 없습니다: {device_hint!r}")


def _resolve_endpoint(endpoint: Optional[EndpointRef], endpoint_ips: Mapping[str, str]) -> Optional[str]:
    """엔드포인트를 IP 문자열로 해석한다. ip가 있으면 그대로, host만 있으면 매핑에서 조회."""
    if endpoint is None:
        return None
    value = endpoint.ip.split("/", 1)[0] if endpoint.ip else None
    if value is None and endpoint.host is not None:
        value = endpoint_ips.get(endpoint.host)
        if value is None:
            raise CompilationError(f"호스트 {endpoint.host!r}에 대한 IP 매핑이 없습니다")
    if value is None:
        raise CompilationError("엔드포인트에 host도 ip도 없습니다")
    try:
        return str(ip_address(value))
    except ValueError as exc:
        raise CompilationError(f"유효하지 않은 엔드포인트 IP: {value!r}") from exc


def _prefix(ip: str) -> str:
    return f"{ip}/{32 if ip_address(ip).version == 4 else 128}"


def _transport_criterion(protocol: Optional[str], direction: str, port: int) -> TransportCriterion:
    if protocol not in ("tcp", "udp"):
        raise CompilationError("전송 계층 포트는 protocol이 tcp 또는 udp여야 합니다")
    key = "tcpPort" if protocol == "tcp" else "udpPort"
    return TransportCriterion(type=f"{protocol.upper()}_{direction}", **{key: port})  # type: ignore[arg-type]


def _compile_selector(selector: IntentSelector, endpoint_ips: Mapping[str, str]) -> list:
    source = _resolve_endpoint(selector.source, endpoint_ips)
    destination = _resolve_endpoint(selector.destination, endpoint_ips)
    addresses = [v for v in (source, destination) if v is not None]
    versions = {ip_address(v).version for v in addresses}
    if len(versions) > 1:
        raise CompilationError("source와 destination의 IP 버전이 다릅니다")

    eth_type = selector.eth_type
    inferred = {4: "ipv4", 6: "ipv6"}.get(next(iter(versions), None))
    if eth_type == "arp" and addresses:
        raise CompilationError("ARP 트래픽은 이 ONOS 스키마에서 IP 기반 매칭으로 표현할 수 없습니다")
    if eth_type is not None and inferred is not None and eth_type != inferred:
        raise CompilationError("eth_type이 엔드포인트 IP 버전과 모순됩니다")
    effective_eth_type = eth_type or inferred
    if effective_eth_type == "ipv6" and selector.protocol == "icmp":
        raise CompilationError("protocol='icmp'는 IPv4 ICMP를 뜻합니다 — ICMPv6이 아닙니다")

    criteria: list = []
    if effective_eth_type is not None:
        criteria.append(EthTypeCriterion(type="ETH_TYPE", ethType=_ETH_TYPE_MAP[effective_eth_type]))
    if selector.protocol is not None:
        criteria.append(ProtocolCriterion(type="IP_PROTO", protocol=_IP_PROTO_MAP[selector.protocol]))

    family = "IPV6" if effective_eth_type == "ipv6" else "IPV4"
    if source is not None:
        criteria.append(IpCriterion(type=f"{family}_SRC", ip=_prefix(source)))
    if destination is not None:
        criteria.append(IpCriterion(type=f"{family}_DST", ip=_prefix(destination)))

    if selector.src_port is not None:
        criteria.append(_transport_criterion(selector.protocol, "SRC", selector.src_port))
    if selector.dst_port is not None:
        criteria.append(_transport_criterion(selector.protocol, "DST", selector.dst_port))
    if selector.in_port is not None:
        criteria.append(InPortCriterion(type="IN_PORT", port=selector.in_port))
    return criteria


def _compile_rule(rule: IntentRule, *, priority: int, endpoint_ips: Mapping[str, str]) -> OnosFlow:
    device_id = _resolve_device_id(rule.enforcement.device if rule.enforcement else None)
    criteria = _compile_selector(rule.selector, endpoint_ips)

    if rule.action == "block":
        treatment = None
    else:
        instructions: list = []
        if rule.qos is not None and rule.qos.queue is not None:
            instructions.append(QueueInstruction(type="QUEUE", queueId=rule.qos.queue))
        if rule.enforcement is not None and rule.enforcement.set_vlan_id is not None:
            instructions.append(
                VlanInstruction(type="L2MODIFICATION", subtype="VLAN_ID", vlanId=rule.enforcement.set_vlan_id)
            )
        egress_port = rule.enforcement.egress_port if rule.enforcement else None
        instructions.append(OutputInstruction(type="OUTPUT", port=str(egress_port) if egress_port is not None else "NORMAL"))
        treatment = OnosTreatment(instructions=instructions)

    return OnosFlow(
        priority=priority,
        timeout=0,
        isPermanent=True,
        deviceId=device_id,
        selector=OnosSelector(criteria=criteria),
        treatment=treatment,
    )


def compile_prediction(
    prediction: IntentPrediction,
    *,
    endpoint_ips: Optional[Mapping[str, str]] = None,
    priority_start: int = 40000,
    priority_step: int = 1,
) -> OnosFlowSet:
    """accepted 예측을 IR 규칙 순서를 우선순위로 보존하며 컴파일한다.

    `endpoint_ips`는 controller-neutral 호스트 이름(예: "h1")을 IP로 푸는 맵이다.
    ip가 직접 지정된 엔드포인트는 조회가 필요 없다.
    """
    if prediction.status != "accepted" or prediction.program is None:
        raise CompilationError("거부된 인텐트는 컴파일할 수 없습니다")
    if priority_step < 1:
        raise ValueError("priority_step은 1 이상이어야 합니다")

    resolver = endpoint_ips or {}
    flows: list[OnosFlow] = []
    for index, rule in enumerate(prediction.program.rules):
        priority = rule.priority if rule.priority is not None else priority_start - index * priority_step
        try:
            flows.append(_compile_rule(rule, priority=priority, endpoint_ips=resolver))
        except CompilationError as exc:
            raise CompilationError(f"rule[{index}] ({rule.action}): {exc}") from exc

    return OnosFlowSet(flows=flows)
