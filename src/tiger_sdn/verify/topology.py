"""src/tiger_sdn/verify/topology.py — 토폴로지 인벤토리 로딩.

원본: sdn-intent-framework의 research/safe_intent_sdn/validator.py의
`TopologyInventory`/`load_topology_inventory`. `data/gold/topology_eval.json`
(entities/aliases + ports)을 그대로 읽는 형식이라 내용 변경 없이 포팅했다.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import ConfigDict

from tiger_sdn.ir.model import StrictModel

__all__ = ["TopologyInventory", "load_topology_inventory"]


class TopologyInventory(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aliases: dict[str, str]
    device_ports: dict[str, frozenset[int]]


def load_topology_inventory(data: Mapping[str, Any]) -> TopologyInventory:
    aliases: dict[str, str] = {}
    for entity in data["entities"]:
        for alias in entity["aliases"]:
            aliases[alias] = entity["id"]
    device_ports = {
        aliases.get(device, device): frozenset(ports)
        for device, ports in data.get("ports", {}).items()
    }
    return TopologyInventory(aliases=aliases, device_ports=device_ports)
