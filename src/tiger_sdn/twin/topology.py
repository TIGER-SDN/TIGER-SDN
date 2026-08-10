"""src/tiger_sdn/twin/topology.py — Digital Twin용 Mininet 토폴로지 빌더.

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/topology.py. 내용
변경 없이 그대로 옮겼다. `build_network()`의 4스위치 다이아몬드 배선(포트 번호
포함)은 `data/gold/topology_eval.json`의 `wiring`/`ports` 절과 정확히 일치한다
— GOLD-350 채점이 가정하는 토폴로지를 이 twin이 실제로 에뮬레이션한다.

Mininet은 각 빌더 함수 안에서만 import한다(모듈 최상단이 아님) — 그래야 이
모듈 자체는 어떤 플랫폼에서도 import 가능하고, 빌더를 실제로 호출할 때만
Mininet이 필요하다.

Mininet 파이썬 패키지는 컴파일된 확장이 없는 순수 파이썬 패키지지만
apt로 설치되어 *시스템* 파이썬의 site-packages에 있고, 이 프로젝트의
uv 관리 가상환경(시스템 site-packages를 보지 못하는 완전 격리 인터프리터)에는
없다. 그래서 `mn --version`은 성공해도 평범한 `import mininet`은 실패한다.
컴파일된 확장이 없으므로 다른(그러나 호환되는) 파이썬 3.x 런타임에서 가져와도
안전하다 — `_import_mininet()`이 시스템 파이썬에게 위치를 물어 `sys.path`에
붙인다.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

# build_network()의 s1-s3-s4 TCLink 대역폭과 일치한다: QoS 예약이 아무리 커도
# 이 경로가 물리적으로 낼 수 있는 상한.
DIAMOND_FAST_LINK_MBPS = 10.0

EXPECTED_DEVICE_IDS: set[str] = {
    "of:0000000000000001",
    "of:0000000000000002",
    "of:0000000000000003",
    "of:0000000000000004",
}


def get_expected_device_ids(custom_data: Optional[dict] = None) -> set[str]:
    """토폴로지가 등록해야 하는 ONOS device id 집합을 반환한다."""
    if custom_data is None:
        return EXPECTED_DEVICE_IDS
    return {f"of:{sw.get('dpid', '0' * 16)}" for sw in custom_data.get("switches", [])}


def get_test_host_pairs(
    custom_data: Optional[dict] = None,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """``(primary_pair, regression_pair)`` 호스트 이름 쌍을 반환한다.

    ``primary_pair``는 테스트 대상 인텐트의 src/dst, ``regression_pair``는
    영향을 받지 않아야 하는 독립적인 쌍이다. 기본 다이아몬드에서는
    ``(("h1","h4"), ("h2","h3"))``.
    """
    if custom_data is None:
        return ("h1", "h4"), ("h2", "h3")

    ids = [h["id"] for h in custom_data.get("hosts", [])]
    if len(ids) >= 4:
        return (ids[0], ids[-1]), (ids[1], ids[2])
    if len(ids) == 2:
        return (ids[0], ids[1]), (ids[0], ids[1])
    if len(ids) >= 1:
        return (ids[0], ids[0]), (ids[0], ids[0])
    return ("h1", "h4"), ("h2", "h3")


_SYSTEM_PYTHON3 = "/usr/bin/python3"  # 이 저장소가 가정하는 Ubuntu 컨테이너 전용 경로


def _system_mininet_site_dir() -> Optional[str]:
    """시스템 ``python3``에게 자신의 Mininet 패키지 위치를 물어본다.

    바로 쓸 수 있는 절대경로여야 한다 — ``uv run`` 아래에서는 PATH에
    ``.venv/bin``이 앞에 붙어서, 이름만 쓴 ``python3``는 우리가 우회하려는
    바로 그(Mininet이 없는) venv 인터프리터로 되돌아간다.

    ``sys.path``에 추가할 디렉터리(Mininet의 부모 디렉터리)를 반환하거나,
    시스템 인터프리터에도 Mininet이 없으면 ``None``을 반환한다.
    """
    try:
        result = subprocess.run(
            [_SYSTEM_PYTHON3, "-c", "import mininet, os; print(os.path.dirname(os.path.dirname(mininet.__file__)))"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return path or None


def _import_mininet():
    try:
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
        from mininet.topo import Topo
        return TCLink, Mininet, OVSSwitch, RemoteController, Topo
    except ImportError:
        pass

    # 이 인터프리터는 Mininet을 못 본다 — 아마 시스템 파이썬(apt install
    # mininet이 설치한 곳)과 격리된 uv 관리 venv일 것이다. 시스템 python3에게
    # 위치를 물어 한 번 더 시도하고, 그래도 안 되면 포기한다.
    system_dir = _system_mininet_site_dir()
    if system_dir and system_dir not in sys.path:
        sys.path.append(system_dir)

    try:
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
        from mininet.topo import Topo
    except ImportError as exc:
        raise RuntimeError(
            "Mininet is not installed. Install with: "
            "sudo apt-get install mininet openvswitch-switch"
        ) from exc
    return TCLink, Mininet, OVSSwitch, RemoteController, Topo


def build_network_from_custom(
    custom_data: dict,
    controller_ip: str = "127.0.0.1",
    controller_port: int = 6653,
):
    """``{switches, hosts, links}`` 딕셔너리로부터 Mininet 네트워크를 빌드한다.

    각 링크는 ``bw``(Mbps)를 가질 수 있다 — ``TCLink``로 강제해 공유 링크를
    실제로 포화시켜 진짜 혼잡을 만들 수 있다.
    """
    TCLink, Mininet, OVSSwitch, RemoteController, Topo = _import_mininet()

    sw_ids = {sw["id"] for sw in custom_data.get("switches", [])}
    host_ids = {h["id"] for h in custom_data.get("hosts", [])}

    class CustomTopo(Topo):
        def build(self):
            for sw in custom_data.get("switches", []):
                self.addSwitch(sw["id"], dpid=sw.get("dpid", "0" * 16), protocols="OpenFlow13")
            for h in custom_data.get("hosts", []):
                self.addHost(h["id"], ip=f"{h.get('ip', '10.0.0.1')}/24", mac=h.get("mac", ""))
            for lnk in custom_data.get("links", []):
                src, dst = lnk["source"], lnk["target"]
                if src not in sw_ids | host_ids or dst not in sw_ids | host_ids:
                    continue
                bw = lnk.get("bw")
                if bw:
                    self.addLink(src, dst, cls=TCLink, bw=bw)
                else:
                    self.addLink(src, dst)

    controller = RemoteController("c0", ip=controller_ip, port=controller_port, protocols="tcp")
    return Mininet(
        topo=CustomTopo(),
        controller=controller,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
    )


def build_network(controller_ip: str = "127.0.0.1", controller_port: int = 6653):
    """기본 다이아몬드 토폴로지(스위치 4개, 호스트 4개)를 빌드한다.

    느린 경로 s1-s2-s4는 1 Mbps, 빠른 경로 s1-s3-s4는 10 Mbps로 제한되어
    QoS/reroute 동작이 경로별로 측정 가능한 차이를 낸다.
    """
    TCLink, Mininet, OVSSwitch, RemoteController, Topo = _import_mininet()

    class DiamondTopo(Topo):
        def build(self):
            h1 = self.addHost("h1", ip="10.0.0.1/24")
            h2 = self.addHost("h2", ip="10.0.0.2/24")
            h3 = self.addHost("h3", ip="10.0.0.3/24")
            h4 = self.addHost("h4", ip="10.0.0.4/24")

            s1 = self.addSwitch("s1", dpid="0000000000000001", protocols="OpenFlow13")
            s2 = self.addSwitch("s2", dpid="0000000000000002", protocols="OpenFlow13")
            s3 = self.addSwitch("s3", dpid="0000000000000003", protocols="OpenFlow13")
            s4 = self.addSwitch("s4", dpid="0000000000000004", protocols="OpenFlow13")

            self.addLink(h1, s1, port2=3)
            self.addLink(h2, s1, port2=4)
            self.addLink(h3, s4, port2=3)
            self.addLink(h4, s4, port2=4)

            # 느린 경로 s1-s2-s4 (1 Mbps) vs 빠른 경로 s1-s3-s4 (10 Mbps)
            self.addLink(s1, s2, port1=1, port2=1, cls=TCLink, bw=1)
            self.addLink(s2, s4, port1=2, port2=1, cls=TCLink, bw=1)
            self.addLink(s1, s3, port1=2, port2=1, cls=TCLink, bw=10)
            self.addLink(s3, s4, port1=2, port2=2, cls=TCLink, bw=10)

    controller = RemoteController("c0", ip=controller_ip, port=controller_port, protocols="tcp")
    return Mininet(
        topo=DiamondTopo(),
        controller=controller,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
    )
