"""src/tiger_sdn/parse/grounding_prompt.py — 토폴로지를 LLM 프롬프트 텍스트로.

원본: experiments/exp1/run.py의 _build_topology_prompt (T-B/T-C/T-D 트리트먼트가
쓰는 grounding 프롬프트, data/gold/topology_eval.json 형식 그대로 읽는다).
내용 변경 없이 옮겼다 — Exp-1 수치와 무관한 코드지만, 같은 입력 형식에 이미
검증된 로직이라 새로 쓰지 않고 그대로 재사용한다(experiments/exp1/run.py 자체는
건드리지 않음, CLAUDE.md의 Exp-1/코어 분리 원칙).

파싱된 IR이 실제로 이 토폴로지에 그라운딩됐는지는 여기서 강제하지 않는다 —
그건 verify.grounding.verify_program()의 몫이다(결정론적, LLM에 의존하지 않음).
이 프롬프트는 LLM이 존재하는 엔티티를 알고 있게 해서 환각 자체를 줄이는
용도이지, 안전을 보장하는 게이트가 아니다.
"""

from __future__ import annotations

from typing import Any


def build_topology_prompt(topology: dict[str, Any]) -> str:
    """eval 토폴로지 JSON 형식에서 grounding 프롬프트 텍스트를 만든다."""
    hosts: list[str] = []
    switch_lines: list[str] = []
    ports_map: dict[str, list[int]] = topology.get("ports", {})
    wiring: dict[str, dict] = topology.get("wiring", {})

    for entity in topology.get("entities", []):
        entity_id = entity["id"]
        aliases = entity.get("aliases", [])
        if entity_id.startswith("host:"):
            name = aliases[0] if aliases else entity_id
            ip = next((a for a in aliases if "." in a and "/" not in a), "")
            if ip:
                hosts.append(f"{name}={ip}")
        elif entity_id.startswith("device:"):
            name = aliases[0] if aliases else entity_id
            onos_id = next((a for a in aliases if a.startswith("of:")), "")
            sw_wiring = wiring.get(name)
            if sw_wiring:
                port_str = ", ".join(
                    f"{port}->{dst}" for port, dst in sorted(sw_wiring.items(), key=lambda x: int(x[0]))
                )
            else:
                ports = sorted(ports_map.get(onos_id, []))
                port_str = ",".join(str(p) for p in ports)
            switch_lines.append(f"    {name} ({onos_id}) ports: {port_str}")

    host_str = ", ".join(hosts)
    switch_str = "\n".join(switch_lines)

    notes = topology.get("wiring_notes", [])
    notes_str = ("\n  Topology notes:\n" + "\n".join(f"    - {n}" for n in notes)) if notes else ""

    waypoints = topology.get("ids_waypoints", [])
    waypoints_line = f"  IDS/Firewall waypoints: {', '.join(waypoints)}\n" if waypoints else ""

    return (
        "Network topology (ONLY reference entities listed here - do not invent others):\n"
        f"  Hosts: {host_str}\n"
        f"  Switches (port -> connected node):\n{switch_str}{notes_str}\n"
        f"{waypoints_line}"
        "If the intent mentions a host IP or switch not in this list, "
        'reject with reason "unknown_entity".'
    )
