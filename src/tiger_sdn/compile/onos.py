"""src/tiger_sdn/compile/onos.py — ONOS REST FlowRule JSON의 엄격한 pydantic 모델.

원본: sdn-intent-framework의 research/safe_intent_sdn/onos.py (Criterion/Instruction
판별 유니언, OnosFlow/OnosFlowSet 부분만 포팅). `parse_onos_response`/`_normalize_flow`
(ONOS 응답 -> IntentPrediction 역방향 파서)는 Stage 5(컴파일 방향)에서 쓰이지 않아
포팅하지 않았다 — Stage 7(Digital Twin)에서 배포된 flow를 되읽어야 할 때 필요해지면
그때 포팅한다.

`compiler.py`가 조립한 criteria/instructions를 이 모델에 태워 `model_dump()`하면
ONOS REST API가 기대하는 JSON이 그대로 나온다 — `extra="forbid"`라 필드 오타나
지원하지 않는 criterion/instruction 조합은 여기서 막힌다.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EthTypeCriterion(StrictModel):
    type: Literal["ETH_TYPE"]
    ethType: str


class IpCriterion(StrictModel):
    type: Literal["IPV4_SRC", "IPV4_DST", "IPV6_SRC", "IPV6_DST"]
    ip: str


class ProtocolCriterion(StrictModel):
    type: Literal["IP_PROTO"]
    protocol: int


class TransportCriterion(StrictModel):
    type: Literal["TCP_SRC", "TCP_DST", "UDP_SRC", "UDP_DST"]
    tcpPort: int | None = Field(default=None, ge=1, le=65535)
    udpPort: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def _correct_port(self) -> "TransportCriterion":
        tcp = self.type.startswith("TCP")
        if tcp != (self.tcpPort is not None) or tcp == (self.udpPort is not None):
            raise ValueError("criterion must carry its matching transport port")
        return self


class InPortCriterion(StrictModel):
    type: Literal["IN_PORT"]
    port: int


Criterion = Annotated[
    Union[EthTypeCriterion, IpCriterion, ProtocolCriterion, TransportCriterion, InPortCriterion],
    Field(discriminator="type"),
]


class OutputInstruction(StrictModel):
    type: Literal["OUTPUT"]
    port: str | int


class QueueInstruction(StrictModel):
    type: Literal["QUEUE"]
    queueId: int = Field(ge=0)


class VlanInstruction(StrictModel):
    type: Literal["L2MODIFICATION"]
    subtype: Literal["VLAN_ID"]
    vlanId: int = Field(ge=0, le=4095)


Instruction = Annotated[
    Union[OutputInstruction, QueueInstruction, VlanInstruction], Field(discriminator="type")
]


class OnosSelector(StrictModel):
    criteria: list[Criterion]


class OnosTreatment(StrictModel):
    instructions: list[Instruction] = Field(min_length=1)


class OnosFlow(StrictModel):
    priority: int
    timeout: int
    isPermanent: bool | str
    deviceId: str
    selector: OnosSelector
    treatment: OnosTreatment | None = None


class OnosFlowSet(StrictModel):
    flows: list[OnosFlow] = Field(min_length=1)
