"""src/tiger_sdn/verify/schema.py — ONOS FlowRule 스키마 검증.

원본: sdn-intent-framework의
src/xai_pipeline/pipeline/stage3_static/schema_validator.py. `compile/onos.py`의
pydantic 모델(extra="forbid")과 달리 이 검증은 priority/deviceId 형식처럼
타입만으로는 못 잡는 값 범위까지 보므로, 컴파일러가 만든 FlowRule뿐 아니라
외부에서 들어온(예: ONOS에서 되읽은) FlowRule JSON도 그대로 검증할 수 있게
dict 입력을 받는다.

── orchestrate.pipeline 배선 중 발견해 고친 버그: isPermanent 타입 불일치 ──
원본은 `isPermanent: str`(ONOS REST 문서의 문자열 표기를 그대로 따름)만
허용했는데, `compile/onos.py`의 `OnosFlow.isPermanent`는 `bool | str`이고
`compile/compiler.py`가 실제로 채우는 값은 파이썬 `True`(bool)다 — pydantic은
bool->str을 암묵 변환하지 않으므로, 실제 컴파일러 출력을 이 스키마에 그대로
태우면 항상 "Input should be a valid string"으로 거부됐다. `static.py`/
`schema.py` 자체 테스트는 손으로 만든 dict 픽스처만 썼고 `compile_prediction()`
결과를 실제로 통과시켜 본 적이 없어서 이 불일치가 Stage 6 완료 이후로도 안
드러났다 — orchestrate.pipeline이 parse->compile->verify를 실제로 이어붙이면서
처음 걸렸다. Stage 7의 라이브 twin 검증(scripts/twin_smoke_test.sh)이 실
ONOS에 `isPermanent: true`(JSON boolean)를 성공적으로 배포한 전례가 있으므로,
bool 쪽이 실제로 맞는 표현이다 — `str`을 버리지 않고 `bool | str`로 넓혀
문자열 표기도 여전히 받는다.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

__all__ = ["validate_schema"]


def _is_valid_ip_cidr(v) -> bool:
    """'ip' 또는 'ip/mask' 형식 검증 (옥텟 0-255, mask 0-32)."""
    if not isinstance(v, str):
        return False
    parts = v.split("/")
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
        if not (0 <= mask <= 32):
            return False
    return True


def _is_valid_port(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 65535

# ── 허용 instruction types ─────────────────────────────────────
VALID_INSTRUCTION_TYPES: set[str] = {
    "OUTPUT", "DROP", "NOACTION", "TABLE", "GROUP",
    "METER", "QUEUE", "L0MODIFICATION", "L2MODIFICATION",
    "L3MODIFICATION", "L4MODIFICATION", "EXTENSION",
}

# ── 허용 criterion types ──────────────────────────────────────
VALID_CRITERION_TYPES: set[str] = {
    "IN_PORT", "IN_PHY_PORT", "ETH_DST", "ETH_SRC", "ETH_TYPE",
    "VLAN_VID", "VLAN_PCP", "IP_DSCP", "IP_ECN", "IP_PROTO",
    "IPV4_SRC", "IPV4_DST", "TCP_SRC", "TCP_DST",
    "UDP_SRC", "UDP_DST", "ICMPV4_TYPE", "ICMPV4_CODE",
    "IPV6_SRC", "IPV6_DST", "METADATA", "TUNNEL_ID",
}

DEVICE_ID_PATTERN = re.compile(r"^of:[0-9a-f]{16}$", re.IGNORECASE)


# ── Pydantic 모델 ──────────────────────────────────────────────

class _Criterion(BaseModel):
    type: str
    model_config = {"extra": "allow"}

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in VALID_CRITERION_TYPES:
            raise ValueError(
                f"알 수 없는 criterion type: '{v}'. "
                f"허용: {sorted(VALID_CRITERION_TYPES)}"
            )
        return v

    @model_validator(mode="after")
    def _validate_value_fields(self) -> "_Criterion":
        """type별 값 필드도 검증한다 — extra=allow가 값 자체는 무검증 통과시키므로
        LLM 환각(잘못된 IP/포트 범위)이 여기서 걸러지지 않으면 Stage3를 그대로
        통과해 Stage4(ONOS)에서야 실패한다."""
        t = self.type
        data = self.model_dump()
        if t in ("IPV4_SRC", "IPV4_DST"):
            ip = data.get("ip")
            if not _is_valid_ip_cidr(ip):
                raise ValueError(f"{t}.ip가 유효한 IPv4 CIDR이 아닙니다: {ip!r}")
        elif t in ("TCP_SRC", "TCP_DST"):
            port = data.get("tcpPort")
            if not _is_valid_port(port):
                raise ValueError(f"{t}.tcpPort는 0~65535 범위의 정수여야 합니다: {port!r}")
        elif t in ("UDP_SRC", "UDP_DST"):
            port = data.get("udpPort")
            if not _is_valid_port(port):
                raise ValueError(f"{t}.udpPort는 0~65535 범위의 정수여야 합니다: {port!r}")
        elif t == "VLAN_VID":
            vlan = data.get("vlanId")
            if not isinstance(vlan, int) or isinstance(vlan, bool) or not (0 <= vlan <= 4095):
                raise ValueError(f"{t}.vlanId는 0~4095 범위의 정수여야 합니다: {vlan!r}")
        return self


class _Instruction(BaseModel):
    type: str
    model_config = {"extra": "allow"}

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in VALID_INSTRUCTION_TYPES:
            raise ValueError(
                f"알 수 없는 instruction type: '{v}'. "
                f"허용: {sorted(VALID_INSTRUCTION_TYPES)}"
            )
        return v

    @model_validator(mode="after")
    def _validate_value_fields(self) -> "_Instruction":
        if self.type == "OUTPUT":
            port = getattr(self, "port", None)
            if port is None or (isinstance(port, str) and not port.strip()):
                raise ValueError("OUTPUT instruction에는 'port' 필드가 필요합니다.")
        return self


class _Selector(BaseModel):
    criteria: list[_Criterion]

    @field_validator("criteria")
    @classmethod
    def _not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("selector.criteria가 비어 있습니다.")
        return v


class _Treatment(BaseModel):
    instructions: list[_Instruction]


class _FlowRule(BaseModel):
    priority: int
    timeout: int = 0
    isPermanent: bool | str = True
    deviceId: str
    selector: _Selector
    treatment: Optional[_Treatment] = None  # 없으면 암묵적 DROP

    model_config = {"extra": "allow"}

    @field_validator("priority")
    @classmethod
    def _priority_range(cls, v: int) -> int:
        if not (0 <= v <= 65535):
            raise ValueError(f"priority는 0~65535 범위여야 합니다. 현재: {v}")
        return v

    @field_validator("timeout")
    @classmethod
    def _timeout_range(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"timeout은 0 이상이어야 합니다. 현재: {v}")
        return v

    @field_validator("deviceId")
    @classmethod
    def _device_id_format(cls, v: str) -> str:
        if not DEVICE_ID_PATTERN.match(v):
            raise ValueError(
                f"deviceId 형식 오류: '{v}'. "
                f"올바른 형식: 'of:000000000000000X'"
            )
        return v

    @field_validator("isPermanent")
    @classmethod
    def _is_permanent_valid(cls, v: bool | str) -> bool | str:
        if isinstance(v, bool):
            return v
        if v not in ("true", "false"):
            raise ValueError(
                f"isPermanent는 bool 또는 'true'/'false' 문자열이어야 합니다. 현재: '{v}'"
            )
        return v


class _FlowRuleWrapper(BaseModel):
    flows: list[_FlowRule]


# ── 공개 검증 함수 ─────────────────────────────────────────────

def validate_schema(flowrule: dict) -> dict:
    """
    ONOS FlowRule JSON을 Pydantic으로 검증한다.

    Args:
        flowrule: {"flows": [...]} 또는 단일 flow dict

    Returns:
        {
            "valid": bool,
            "errors": [str]  # 오류 메시지 목록
        }
    """
    errors: list[str] = []

    # flows 배열 최상위 구조 확인
    if "flows" not in flowrule:
        errors.append("최상위에 'flows' 배열이 없습니다.")
        return {"valid": False, "errors": errors}

    if not isinstance(flowrule["flows"], list) or not flowrule["flows"]:
        errors.append("'flows' 배열이 비어 있거나 리스트가 아닙니다.")
        return {"valid": False, "errors": errors}

    try:
        _FlowRuleWrapper(**flowrule)
        return {"valid": True, "errors": []}
    except Exception as exc:
        # Pydantic v2 ValidationError는 .errors() 메서드 제공
        if hasattr(exc, "errors"):
            for err in exc.errors():
                loc = " → ".join(str(x) for x in err.get("loc", []))
                msg = err.get("msg", str(err))
                errors.append(f"[{loc}] {msg}" if loc else msg)
        else:
            errors.append(str(exc))
        return {"valid": False, "errors": errors}
