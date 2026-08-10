"""src/tiger_sdn/ir/model.py — 통합 Intent IR — controller-neutral 중간 표현.

원본: sdn-intent-framework의 feat/unify-ir 브랜치, src/sdn_intent/ir/model.py
(커밋된 적 없는 로컬 상태 — docs/plan.md "확인된 사실 1" 참고. 패키지명만
sdn_intent -> tiger_sdn으로 바꾸고 내용은 그대로 옮겼다.)

`xai_pipeline.models.intent_ir`(제품 구현)와 `safe_intent_sdn.intent_ir`
(연구 구현)를 하나로 합친 것이다. 필드 이름과 형태는 GOLD-350이 쓰는
제품 스키마를 정본으로 삼고, 연구 구현에서는 다음을 흡수했다.

  - `extra="forbid"` — LLM이 만들어낸 미지의 필드를 조용히 무시하지 않는다
  - 의미 제약 검증 — action ↔ intent_type 정합성, qos 규칙의 qos 필수성 등
  - 단일/복합 규칙을 `IntentProgram.rules` 하나로 통일

흡수하지 **않은** 것:

  - `Endpoint.require_identity` (host/ip 중 정확히 하나) — GOLD-350은
    `{"host": "h2", "ip": "10.0.0.2"}`처럼 둘을 함께 쓴다. 이 둘은 같은
    엔티티의 별칭이므로 배타 관계가 아니다. 대신 "최소 하나"로 완화했다.
  - `ingress_port >= 1` — 제품 구현은 0을 허용한다(OpenFlow 논리 포트).

── 스키마 구조 ──────────────────────────────────────────────────────────
  IntentPrediction            LLM 파싱 결과 래퍼 (accepted / rejected)
  └── IntentProgram           규칙 목록 — 단일 인텐트도 rules 길이 1
      └── IntentRule
          ├── action          forward | block | qos | sfc | reroute
          ├── intent_type     forwarding | security | qos | sfc | reroute
          ├── selector        트래픽 매칭 조건
          ├── enforcement     집행 위치와 출력 포트
          ├── qos             품질 요구사항
          └── routing         SFC waypoint / reroute 경로
"""

from __future__ import annotations

import ipaddress
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "StrictModel",
    "EndpointRef",
    "IntentSelector",
    "IntentEnforcement",
    "IntentQoS",
    "IntentRouting",
    "IntentRule",
    "IntentProgram",
    "Action",
    "IntentType",
    "ACTION_TO_INTENT_TYPE",
]

Action = Literal["forward", "block", "qos", "sfc", "reroute"]
IntentType = Literal["forwarding", "security", "qos", "sfc", "reroute"]

# action → intent_type 자동 파생 (intent_type 미지정 시)
ACTION_TO_INTENT_TYPE: dict[str, str] = {
    "forward": "forwarding",
    "block": "security",
    "qos": "qos",
    "sfc": "sfc",
    "reroute": "reroute",
}

# intent_type 별로 허용되는 action — 연구 구현의 validate_semantics 계승.
#
# 두 필드는 서로 다른 축이다. `intent_type`은 정책 도메인(보안이냐 포워딩이냐),
# `action`은 데이터플레인 동작(통과냐 차단이냐)이다. 그래서 security는 두 action을
# 모두 갖는다 — 화이트리스트는 forward, 블랙리스트는 block이다. 연구 구현이
# security에 {allow, deny}를 둔 것과 같은 구분이며, GOLD-350에는 이 조합이
# 13건 있다(예: G-CMP-043 "Whitelist SSH from h1 to h2 and drop all other SSH").
_ALLOWED_ACTIONS: dict[str, set[str]] = {
    "forwarding": {"forward"},
    "security": {"forward", "block"},
    "qos": {"qos"},
    "sfc": {"sfc"},
    "reroute": {"reroute"},
}


def is_valid_ipv4_with_mask(value: str) -> bool:
    """'ip' 또는 'ip/mask' 형식 검증 (옥텟 0-255, mask 0-32).

    단순 자릿수 패턴(예: 999.999.999.999)은 통과시키지 않는다 — LLM 환각으로
    나온 잘못된 옥텟이 그대로 FlowRule까지 흘러가는 것을 막기 위함이다.
    """
    parts = value.split("/")
    if len(parts) > 2:
        return False
    try:
        ipaddress.IPv4Address(parts[0])
    except ValueError:
        return False
    if len(parts) == 2:
        try:
            mask = int(parts[1])
        except ValueError:
            return False
        if not 0 <= mask <= 32:
            return False
    return True


class StrictModel(BaseModel):
    """미지의 필드를 거부하는 기본 모델.

    연구 구현에서 흡수한 핵심 방어선이다. LLM이 스키마에 없는 필드를
    만들어내면 조용히 버려지는 대신 검증 오류가 된다.
    """

    model_config = ConfigDict(extra="forbid")


class EndpointRef(StrictModel):
    """트래픽 출발지/목적지 엔드포인트 참조.

    host와 ip는 같은 엔티티의 별칭이므로 함께 존재할 수 있다
    (GOLD-350의 표기 방식). 다만 둘 다 비어 있을 수는 없다.
    """

    host: Optional[str] = None
    ip: Optional[str] = None

    @field_validator("ip", mode="before")
    @classmethod
    def _normalize_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = str(v).strip()
        if not v:
            return None
        if not is_valid_ipv4_with_mask(v):
            raise ValueError(f"유효하지 않은 IPv4 주소: {v!r}")
        return v if "/" in v else v + "/32"

    @field_validator("host", mode="before")
    @classmethod
    def _blank_host_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    @model_validator(mode="after")
    def _require_identity(self) -> "EndpointRef":
        if self.host is None and self.ip is None:
            raise ValueError("엔드포인트는 host 또는 ip 중 최소 하나를 가져야 합니다")
        return self


class IntentSelector(StrictModel):
    """트래픽 매칭 조건 — 어떤 패킷을 대상으로 하는가."""

    source: Optional[EndpointRef] = None
    destination: Optional[EndpointRef] = None
    eth_type: Optional[Literal["ipv4", "ipv6", "arp"]] = None
    protocol: Optional[Literal["tcp", "udp", "icmp"]] = None
    src_port: Optional[int] = Field(default=None, ge=0, le=65535)
    dst_port: Optional[int] = Field(default=None, ge=0, le=65535)
    in_port: Optional[int] = Field(default=None, ge=0, le=65535)

    @model_validator(mode="after")
    def _ports_require_transport(self) -> "IntentSelector":
        """L4 포트를 지정했으면 전송 프로토콜도 지정되어야 한다.

        연구 구현에서 흡수. 포트만 있고 protocol이 없으면 OpenFlow 매칭
        규칙을 만들 수 없다.
        """
        if (self.src_port is not None or self.dst_port is not None) and self.protocol not in {
            "tcp",
            "udp",
        }:
            raise ValueError("src_port/dst_port를 쓰려면 protocol이 tcp 또는 udp여야 합니다")
        return self


class IntentEnforcement(StrictModel):
    """정책 집행 위치 및 출력 포트 — 어디서, 어느 포트로."""

    device: Optional[str] = None
    egress_port: Optional[int] = Field(default=None, ge=0, le=65535)
    alt_egress_port: Optional[int] = Field(default=None, ge=0, le=65535)
    set_vlan_id: Optional[int] = Field(default=None, ge=0, le=4095)

    @field_validator("device", mode="before")
    @classmethod
    def _blank_device_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = str(v).strip()
        if v.lower() in ("", "none", "null"):
            return None
        return v


class IntentQoS(StrictModel):
    """QoS 파라미터 — 품질 요구사항."""

    queue: Optional[int] = Field(default=None, ge=0)
    min_bandwidth_mbps: Optional[float] = Field(default=None, gt=0)
    max_latency_ms: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _require_value(self) -> "IntentQoS":
        if all(
            v is None
            for v in (self.queue, self.min_bandwidth_mbps, self.max_latency_ms)
        ):
            raise ValueError("qos는 queue, min_bandwidth_mbps, max_latency_ms 중 하나가 필요합니다")
        return self


class IntentRouting(StrictModel):
    """SFC / Reroute 경로 지정 — 어떤 경로를 통해."""

    waypoints: Optional[list[str]] = None
    via_device: Optional[str] = None
    avoid_device: Optional[str] = None

    @model_validator(mode="after")
    def _require_value(self) -> "IntentRouting":
        if self.waypoints is None and self.via_device is None and self.avoid_device is None:
            raise ValueError("routing은 waypoints, via_device, avoid_device 중 하나가 필요합니다")
        if self.waypoints is not None and not self.waypoints:
            raise ValueError("waypoints가 빈 목록일 수 없습니다")
        return self


class IntentRule(StrictModel):
    """단일 정책 규칙."""

    action: Action
    intent_type: Optional[IntentType] = None
    selector: IntentSelector = Field(default_factory=IntentSelector)
    enforcement: Optional[IntentEnforcement] = None
    qos: Optional[IntentQoS] = None
    routing: Optional[IntentRouting] = None
    priority: Optional[int] = Field(default=None, ge=0)

    # 연구 구현 계승 — SFC 체인에서 이 규칙이 차지하는 위치. 제품 스키마와
    # GOLD-350에는 없는 필드라 기본값은 None이며, 연구 데이터 왕복 변환의
    # 무손실성을 위해 유지한다.
    sfc_role: Optional[Literal["ingress", "transit", "egress"]] = None

    @property
    def resolved_intent_type(self) -> str:
        """intent_type이 없으면 action에서 파생한다."""
        return self.intent_type or ACTION_TO_INTENT_TYPE[self.action]

    @model_validator(mode="after")
    def _validate_semantics(self) -> "IntentRule":
        """action ↔ intent_type 정합성과 필드별 적용 범위를 검증한다.

        연구 구현의 validate_semantics를 제품 스키마의 action 어휘에 맞춰
        옮긴 것이다.
        """
        itype = self.resolved_intent_type
        if self.action not in _ALLOWED_ACTIONS[itype]:
            raise ValueError(f"intent_type={itype}에는 action={self.action}을 쓸 수 없습니다")

        if itype == "qos" and self.qos is None:
            raise ValueError("qos 규칙에는 qos 제약이 필요합니다")
        if itype != "qos" and self.qos is not None:
            raise ValueError("qos 제약은 qos 규칙에서만 유효합니다")

        if self.routing is not None and self.routing.avoid_device is not None:
            if itype != "reroute":
                raise ValueError("avoid_device는 reroute 규칙에서만 유효합니다")

        if itype == "sfc" and (self.routing is None or not self.routing.waypoints):
            raise ValueError("sfc 규칙에는 routing.waypoints가 필요합니다")

        if self.sfc_role is not None and itype != "sfc":
            raise ValueError("sfc_role은 sfc 규칙에서만 유효합니다")

        return self


class IntentProgram(StrictModel):
    """평가 순서대로 정렬된 규칙 목록.

    단일 인텐트도 길이 1의 목록으로 표현한다 — 제품 구현의
    `IntentIR` / `CompoundIntentIR` 이원화를 없앤 결과다.
    """

    rules: list[IntentRule] = Field(min_length=1)
    description: str = ""

    @property
    def is_compound(self) -> bool:
        return len(self.rules) > 1

    @property
    def single(self) -> IntentRule:
        """단일 규칙 프로그램의 유일한 규칙을 반환한다."""
        if len(self.rules) != 1:
            raise ValueError(f"단일 규칙이 아닙니다 (rules={len(self.rules)})")
        return self.rules[0]
