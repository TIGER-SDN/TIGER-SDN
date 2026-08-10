"""src/tiger_sdn/twin/traffic_generator.py — Digital Twin용 배경 트래픽 재생.

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/traffic_generator.py.
내용 변경 없이 그대로 옮겼다.

모든 프로세스는 Mininet ``host.popen`` 핸들이다 — ``stop()``이 각 핸들을
종료한 뒤 호스트별로 남은 iperf3를 ``pkill``하고, 호출자(``TwinVerifier``)가
``finally`` 블록에서 ``mn -c``까지 추가로 실행하므로, 실행이 실패해도 다음
케이스에 iperf3나 인터페이스가 남지 않는다.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class TrafficHandles:
    """케이스가 끝난 뒤 정리해야 할, 살아있는 배경 트래픽 프로세스들."""

    procs: list = field(default_factory=list)          # list[Popen]
    hosts: set[str] = field(default_factory=set)       # 건드린 호스트 이름
    _net: object = None

    def stop(self) -> None:
        for proc in self.procs:
            try:
                proc.terminate()
            except Exception:
                pass
        # SIGTERM을 무시한 것들을 우리가 시작했던 호스트별로 강제 종료한다.
        if self._net is not None:
            for host_name in self.hosts:
                try:
                    self._net.get(host_name).cmd("pkill -9 iperf3 2>/dev/null")
                except Exception:
                    pass
        self.procs.clear()


def start_background_traffic(net, flows: list[dict], *, base_port: int = 5301) -> TrafficHandles:
    """``flows``에 기술된 정속 iperf3 플로우들을 시작한다.

    각 flow dict: ``{"src": "h2", "dst": "h3", "dst_ip": "10.0.0.3",
    "mbps": 6, "proto": "udp"|"tcp", "duration": <sec>}``. 배경 부하는
    UDP를 선호한다 — 손실과 무관하게 고정된 제공 전송률을 내서 공유
    병목에 안정적인 혼잡을 만들기 때문이다.
    """
    handles = TrafficHandles(_net=net)
    for offset, flow in enumerate(flows):
        src = net.get(flow["src"])
        dst = net.get(flow["dst"])
        port = base_port + offset
        proto = flow.get("proto", "udp")
        udp_flag = "-u" if proto == "udp" else ""
        mbps = flow["mbps"]
        duration = int(flow.get("duration", 30))

        server = dst.popen(
            ["iperf3", "-s", "-p", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        handles.procs.append(server)
        handles.hosts.update({flow["src"], flow["dst"]})

    # 클라이언트가 붙기 전에 서버가 바인딩할 시간을 준다.
    time.sleep(1)

    for offset, flow in enumerate(flows):
        src = net.get(flow["src"])
        port = base_port + offset
        proto = flow.get("proto", "udp")
        udp_flag = "-u" if proto == "udp" else ""
        mbps = flow["mbps"]
        duration = int(flow.get("duration", 30))
        client = src.popen(
            f"iperf3 -c {flow['dst_ip']} -p {port} {udp_flag} -b {mbps}M "
            f"-t {duration} >/dev/null 2>&1",
            shell=True,
        )
        handles.procs.append(client)

    # 호출자가 인텐트 플로우를 측정하기 전에 제공 부하가 올라올 시간을 준다.
    time.sleep(1)
    return handles
