"""src/tiger_sdn/twin/twin_verifier.py — Mininet 위 Digital Twin FlowRule 검증.

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/twin_verifier.py.
아래 네 가지를 제외하면 내용 변경 없이 그대로 옮겼다 — 나머지는 원본의 설계와
주석(레이스 컨디션 대응, 헤드룸 클램핑 등 실측 기반 판단이 담겨 있어 그대로
유지할 가치가 있다)을 보존했다.

1. **`_extract_intent_specs`를 통합 컴파일러의 출력 형태에 맞게 재작성했다.**
   원본은 `xai_pipeline` 컴파일러가 내던 `{"intent_action": "compound",
   "sub_rules": [{"flows":[...]}, ...], "flows": [...]}` 형태(컴파운드 인텐트를
   `sub_rules`로 한 겹 더 감싸는 구조)를 가정했다. `tiger_sdn.compile.compile_prediction()`
   은 그런 겹구조 없이 `{"flows": [f1, f2, ..., fN]}`로 항상 평평하게 낸다
   (규칙 N개 -> flow N개, `intent_action`/`sub_rules` 키 자체가 없음). 원본
   코드를 그대로 쓰면 `intent_action`이 없을 때 `sub_rules = [flowrule]`로
   떨어져 전체 flowrule을 "규칙 하나"로 취급하고 `flows[0]`만 보므로, 컴파운드
   예측(규칙 2개 이상)에서 첫 번째 flow 이후를 전부 놓친다. 새 버전은
   `flowrule["flows"]`를 그냥 순회해 **flow 하나당 intent_spec 하나**를 낸다
   — treatment가 없으면 block, 있으면 forward류로 판정(`_verify_intents`가
   실제로 쓰는 건 `expect_reach = action != "block"` 뿐이므로 forward/qos/
   sfc/reroute를 더 세분화할 필요가 없다).
2. **`verify()`가 `OnosFlowSet`(pydantic)도 받는다.** `compile_prediction()`의
   반환값을 바로 넘길 수 있도록 dict가 아니면 `.model_dump()`를 호출한다.
3. **`ovs-ofctl` 명령을 f-string 조립에서 인자 리스트로 바꿨다**(`docs/plan.md`
   Stage 7에 명시된 pitfall) — `_install_steering`/`_remove_steering`.
   `Node.cmd()`가 최종적으로는 네임스페이스 안 셸에 문자열 한 줄을 보내는
   구조라 리스트로 바꿔도 셸 자체를 우회하지는 못하지만, 이전처럼 통째로
   조립한 문자열에 리터럴 큰따옴표로 값을 감싸던 취약한 수동 인용을 없애고
   각 필드를 별도 인자로 넘긴다. `match`(콤마로만 구분되고 공백이 없는 문자열)
   자체도 `shlex.quote()`로 감싸 방어했다.
4. **`verify()`에 `progress_cb` 파라미터를 추가했다** (Stage 9, docs/plan.md
   참고) — 원본 `xai_pipeline`의 `TwinVerifier.verify(self, flowrule,
   progress_cb=None)`와 동일한 패턴. `_log()`가 기존 `print()`에 더해
   `progress_cb`가 설정돼 있으면 그것도 호출한다 — 웹 API(Stage 9)가 twin
   진행 상황을 SSE로 스트리밍하는 데 쓴다. 기존 호출부(테스트 등)는
   `progress_cb`를 안 넘기면 그대로 동작한다(기본값 `None`).

Linux + root + Mininet + 접근 가능한 ONOS 컨트롤러가 필요하다. 없으면
``verify()``가 ``status="skipped"``를 반환한다.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from tiger_sdn.twin.bandwidth import measure_bandwidth, meets_target

_STEERING_COOKIE = "0xdeadbeef"

# 기본 체크 세트: 인텐트 쌍의 도달성 + 회귀 쌍.
REACH_ONLY: frozenset[str] = frozenset({"reach", "regression"})
REACH_AND_BANDWIDTH: frozenset[str] = frozenset({"reach", "regression", "bandwidth"})

_DEFAULT_IP_MAP = {"10.0.0.1": "h1", "10.0.0.2": "h2", "10.0.0.3": "h3", "10.0.0.4": "h4"}

# QoS 큐가 예약할 수 있는 링크 용량의 비율 — 나머지는 best-effort 트래픽
# (ARP/ICMP/반환 경로) 몫으로 남긴다. provision_min_rate_queue 참고.
_MIN_RATE_HEADROOM = 0.9

# ONOS가 flow ADDED를 보고하는 시점과 OVS/`fwd`가 (경로가 바뀌었을 수 있는)
# 실제 데이터플레인에 수렴하는 시점 사이의 간극을 흡수하기 위해 도달성
# probe를 재시도한다.
_REACH_ATTEMPTS = 3
_REACH_RETRY_DELAY = 1.5


def _device_id_to_sw_name(device_id: str, custom_data: Optional[dict]) -> Optional[str]:
    """'of:0000000000000002' -> 's2' (커스텀 토폴로지 우선, 아니면 숫자로 디코드)."""
    if custom_data:
        for sw in custom_data.get("switches", []):
            if f"of:{sw.get('dpid', '')}" == device_id:
                return sw["id"]
    try:
        return f"s{int(device_id.replace('of:', ''), 16)}"
    except ValueError:
        return None


def _find_host_switch(host_id: str, custom_data: Optional[dict]) -> Optional[str]:
    if not custom_data:
        return None
    sw_ids = {sw["id"] for sw in custom_data.get("switches", [])}
    for lnk in custom_data.get("links", []):
        s, t = lnk["source"], lnk["target"]
        if s == host_id and t in sw_ids:
            return t
        if t == host_id and s in sw_ids:
            return s
    return None


def _bfs_sw_path(src_sw: str, dst_sw: str, custom_data: dict) -> list[str]:
    sw_ids = {sw["id"] for sw in custom_data.get("switches", [])}
    adj: dict[str, list[str]] = {s: [] for s in sw_ids}
    for lnk in custom_data.get("links", []):
        s, t = lnk["source"], lnk["target"]
        if s in sw_ids and t in sw_ids:
            adj[s].append(t)
            adj[t].append(s)
    q: deque[list[str]] = deque([[src_sw]])
    visited = {src_sw}
    while q:
        path = q.popleft()
        if path[-1] == dst_sw:
            return path
        for nb in adj.get(path[-1], []):
            if nb not in visited:
                visited.add(nb)
                q.append(path + [nb])
    return []


def _find_mininet_port(net, sw_from: str, sw_to: str) -> Optional[int]:
    """``sw_from``에서 ``sw_to`` 방향의 OpenFlow 포트.

    ``TCIntf``는 ``.port`` 속성이 없으므로 ``OVSSwitch.ports`` 딕셔너리를
    우선 쓰고, 안 되면 인터페이스 이름 파싱으로 대체한다('s1-eth2' -> 2).
    """
    sw_node = net.get(sw_from)
    for link in net.links:
        n1, n2 = link.intf1.node.name, link.intf2.node.name
        if n1 == sw_from and n2 == sw_to:
            intf = link.intf1
        elif n2 == sw_from and n1 == sw_to:
            intf = link.intf2
        else:
            continue
        if hasattr(sw_node, "ports") and intf in sw_node.ports:
            return sw_node.ports[intf]
        try:
            return int(intf.name.split("eth")[-1])
        except (ValueError, IndexError):
            pass
    return None


def _ofport_to_ifname(net, sw_name: str, ofport: str | int) -> Optional[str]:
    """``sw_name`` 위 OpenFlow 포트 번호를 리눅스 인터페이스 이름으로 해석한다."""
    out = net.get(sw_name).cmd(f"ovs-vsctl --bare -- --columns=name find interface ofport={ofport}").strip()
    return out or None


def provision_min_rate_queue(
    net, sw_name: str, ofport: str | int, min_rate_mbps: float, max_rate_mbps: float, queue_id: int = 0
) -> bool:
    """스위치 포트에 실제 OVS HTB min-rate 큐를 구성한다.

    컴파일된 QoS flow의 ``QUEUE(queueId=...)`` 액션은 OVS에 그 큐가 실제로
    egress 포트에 설정돼 있어야만 의미 있게 작동한다 — 안 그러면 동작이
    구현별로 달라지는데, 실무에서는 대역폭 예약이 아니라 거의 전체 패킷
    손실로 관측됐다. 이 함수가 "prioritize" 액션에 진짜 min-rate 보장을
    부여해서, 진짜로 체크될 수 있게(그리고 요청이 큐의 ``max_rate_mbps``—
    물리 링크 용량—가 절대 낼 수 없는 값이면 진짜로 실패할 수 있게) 한다.

    예약은 링크 용량 자체가 아니라 ``_MIN_RATE_HEADROOM``만큼만 클램핑된다.
    요청을 용량까지 그대로 클램핑하면(예: 10 Mbps 링크에 15 Mbps 목표) 큐
    0이 링크 *전체*를 예약해 그 큐에 속하지 않는 모든 것(ARP, ICMP, 반환
    트래픽)을 굶긴다 — 데이터셋이 의존하는, 용량을 초과하는 바로 그 케이스들
    에서 무관한 reachability 체크가 깨지는 게 실측됐다. 여유를 남겨두면
    용량 초과 목표는 여전히 도달 불가능하게 만들면서 best-effort 트래픽은
    살아있게 한다.

    포트의 인터페이스를 해석할 수 있었고 프로비저닝을 시도했으면 True.
    """
    ifname = _ofport_to_ifname(net, sw_name, ofport)
    if not ifname:
        return False
    min_bps = int(min(min_rate_mbps, max_rate_mbps * _MIN_RATE_HEADROOM) * 1_000_000)
    max_bps = int(max_rate_mbps * 1_000_000)
    net.get(sw_name).cmd(
        f"ovs-vsctl -- set port {ifname} qos=@newqos "
        f"-- --id=@newqos create qos type=linux-htb other-config:max-rate={max_bps} queues:{queue_id}=@q{queue_id} "
        f"-- --id=@q{queue_id} create queue other-config:min-rate={min_bps} other-config:max-rate={max_bps}"
    )
    return True


def clear_ovs_qos() -> None:
    """이전 실행이 남긴 OVS QoS/Queue 레코드를 전부 파괴한다.

    ``provision_min_rate_queue``는 ovsdb의 QoS/Queue 테이블에 행을 만들어
    포트에 붙인다. ``mn -c``는 브리지와 포트를 정리하지만 이 행들은
    가비지컬렉트하지 **않아서**, 이게 없으면 모든 arm의 모든 케이스마다
    쌓인다(전체 E3 실행에서 33건 이상).

    전제: 이 호스트의 OVS는 실험 전용이다(``mn -c``가 모든 Mininet 상태를
    지울 때 이미 하는 것과 같은 가정) — 그래서 모든 QoS 레코드를 지우는 게
    안전하다. 실패는 무시한다 — 이건 체크가 아니라 best-effort 정리다.
    """
    for table in ("qos", "queue"):
        try:
            subprocess.run(
                ["ovs-vsctl", "--all", "destroy", table],
                capture_output=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass


@dataclass
class TwinResult:
    """Digital Twin 검증의 결과."""

    status: str  # "passed" | "failed" | "skipped" | "error"
    reason: str = ""
    checks: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)

    def summary(self) -> str:
        label = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "error": "ERROR"}.get(
            self.status, self.status.upper()
        )
        return f"{label}: {self.reason}" if self.reason else label


class TwinVerifier:
    """FlowRule을 Mininet twin에 배포하고 동작을 체크한다."""

    def __init__(
        self,
        onos_url: str = "http://127.0.0.1:8181/onos/v1",
        onos_user: str = "onos",
        onos_password: str = "rocks",
        controller_ip: str = "127.0.0.1",
        controller_port: int = 6653,
    ) -> None:
        self.onos_url = onos_url
        self.onos_user = onos_user
        self.onos_password = onos_password
        self.controller_ip = controller_ip
        self.controller_port = controller_port
        self._progress_cb: Callable[[str], None] | None = None

    def _log(self, msg: str) -> None:
        print(f"    [Twin] {msg}")
        if self._progress_cb is not None:
            self._progress_cb(msg)

    def verify(
        self,
        flowrule,
        *,
        checks: frozenset[str] = REACH_ONLY,
        min_mbps: float | None = None,
        background_traffic: list[dict] | None = None,
        custom_data: dict | None = None,
        ip_map: dict[str, str] | None = None,
        progress_cb: Callable[[str], None] | None = None,
    ) -> TwinResult:
        """``flowrule``을 twin에 배포하고 동작을 검증한다.

        Args:
            flowrule: ``{"flows": [...]}`` 컴파일된 ONOS flow 페이로드.
                `tiger_sdn.compile.compile_prediction()`이 반환하는
                `OnosFlowSet`을 직접 넘겨도 된다(dict가 아니면
                `.model_dump()`를 호출한다).
            checks: 판정에 반영할 체크. ``"bandwidth"``는 iperf3 프로브를
                실행한다(reach-only arm은 생략).
            min_mbps: bandwidth 체크에 필요한 전달 전송률.
            background_traffic: 테스트 아래에서 재생할 정속 플로우들
                (traffic_generator.start_background_traffic 참고).
            custom_data: 선택적 ``{switches, hosts, links}`` 토폴로지;
                ``None``이면 다이아몬드 빌더를 쓴다.
            ip_map: 선택적 ip->host 맵; ``None``이면 다이아몬드 기본값.

        Returns:
            TwinResult. bandwidth 프로브가 돌았으면 ``evidence``에 항상
            ``measured_mbps``가 담긴다.
        """
        self._progress_cb = progress_cb

        if not isinstance(flowrule, dict):
            flowrule = flowrule.model_dump()

        skip_reason = self._check_platform()
        if skip_reason:
            return TwinResult(status="skipped", reason=skip_reason)

        from tiger_sdn.backends.onos import OnosClient
        from tiger_sdn.twin.topology import (
            build_network,
            build_network_from_custom,
            get_expected_device_ids,
            get_test_host_pairs,
        )
        from tiger_sdn.twin.traffic_generator import start_background_traffic

        client = OnosClient(
            base_url=self.onos_url, username=self.onos_user, password=self.onos_password
        )
        expected_ids = get_expected_device_ids(custom_data)
        primary_pair, regression_pair = get_test_host_pairs(custom_data)

        if flowrule.get("sfc_chain"):
            return TwinResult(
                status="skipped",
                reason="SFC intents cannot be verified in the twin without a waypoint device",
            )

        flows = flowrule.get("flows", [])
        intent_specs = self._extract_intent_specs(flowrule)
        if not intent_specs:
            return TwinResult(
                status="skipped",
                reason="FlowRule has no IPV4_SRC/DST criteria to target a traffic check",
            )
        action, src_ip, dst_ip, flow_proto, flow_dst_port, flow = intent_specs[0]

        ip_to_host = dict(ip_map) if ip_map else self._ip_to_host(custom_data)
        host_to_ip = {hid: ip for ip, hid in ip_to_host.items()}

        dst_host = ip_to_host.get(dst_ip or "", primary_pair[1])
        if src_ip is not None:
            src_host = ip_to_host.get(src_ip, primary_pair[0])
        else:
            src_host = next((h for h in ip_to_host.values() if h != dst_host), primary_pair[0])
        baseline_dst_ip = dst_ip or host_to_ip.get(primary_pair[1], "10.0.0.4")

        net = None
        traffic = None
        checks_result: dict = {}
        evidence: dict = {}

        try:
            self._log("(1) waiting for ONOS controller...")
            client.wait_until_ready(timeout=60.0)

            self._log("(2) activating ONOS OpenFlow apps...")
            for app in ("org.onosproject.openflow-base", "org.onosproject.openflow", "org.onosproject.fwd"):
                try:
                    client.activate_application(app)
                except Exception:
                    pass
            time.sleep(2)

            self._log("(3) clearing existing flows...")
            client.clear_app_flows()
            time.sleep(1)

            self._log("(4) cleaning stale Mininet interfaces...")
            subprocess.run(["mn", "-c"], capture_output=True, timeout=15)
            clear_ovs_qos()

            if custom_data:
                self._log("(4) starting Mininet (custom topology)...")
                net = build_network_from_custom(custom_data, self.controller_ip, self.controller_port)
            else:
                self._log("(4) starting Mininet (diamond topology)...")
                net = build_network(self.controller_ip, self.controller_port)
            net.start()
            client.wait_for_devices(expected_ids, timeout=90.0)
            time.sleep(3)

            # 측정 전에 데이터플레인을 워밍업한다. 토폴로지는
            # autoStaticArp=True로 빌드되어 호스트가 ARP를 보내지 않으므로,
            # (packet-in으로 호스트를 배치하는) ONOS는 실제 트래픽이 오기
            # 전까지 위치를 알 수 없다. 이게 없으면 `fwd`가 설치할 경로가
            # 없어서 배포 전 baseline 체크를 포함한 첫 reachability probe가
            # 간헐적으로 "차단됨"으로 읽힌다(실측).
            #
            # 실측: 실제로 이 문제를 고치는 건 pingAll이다. wait_for_hosts가
            # 타임아웃돼도 ONOS의 호스트 테이블에는 4개 중 0-1개만 잡히는
            # 경우가 잦은데 모든 reachability 체크는 통과한다 — 즉 호스트
            # 테이블 등록이 준비 신호가 아니라, `fwd`와 OVS 데이터플레인을
            # 미리 자극하는 것 자체가 신호다. wait_for_hosts는 등록이 실제로
            # 성공하면 일찍 반환하고 아니어도 안정화 시간 역할을 겸하므로
            # 그대로 둔다 — 재측정 없이 타임아웃을 줄이지 말 것.
            self._log("(4a) warming up host discovery (pingAll)...")
            try:
                net.pingAll(timeout="1")
            except Exception as exc:
                self._log(f"   (note) warm-up pingAll raised {exc!r}; continuing")
            client.wait_for_hosts(len(net.hosts), timeout=30.0)

            if min_mbps is not None and not custom_data:
                from tiger_sdn.twin.topology import DIAMOND_FAST_LINK_MBPS
                qos_device = _device_id_to_sw_name(flow.get("deviceId", ""), custom_data)
                qos_port = self._egress_port(flow)
                if qos_device and qos_port is not None:
                    self._log(f"(4c) provisioning min-rate queue: {qos_device} port {qos_port} -> {min_mbps} Mbps")
                    provision_min_rate_queue(net, qos_device, qos_port, min_mbps, DIAMOND_FAST_LINK_MBPS)

            if background_traffic:
                self._log(f"(4b) replaying {len(background_traffic)} background flow(s)...")
                traffic = start_background_traffic(net, background_traffic)

            self._log(f"(5) baseline connectivity: {src_host} -> {baseline_dst_ip}")
            baseline_ok, baseline_msg = self._ping_check(net, src_host, baseline_dst_ip, expect_reach=True)
            checks_result["baseline_connectivity"] = baseline_ok
            evidence["baseline_msg"] = baseline_msg

            self._log("(6) deploying FlowRule...")
            client.deploy_flow_rules(flowrule)
            for f in flows:
                client.wait_for_flow(
                    device_id=f.get("deviceId", "of:0000000000000001"),
                    priority=f.get("priority", 50000),
                    timeout=15.0,
                )
            # ONOS가 flow를 ADDED로 보고하는 건 자기 장부의 확인일 뿐, OVS가
            # 데이터플레인 설치를 반드시 끝냈다는 뜻은 아니다 — 곧바로
            # 체크하면 이 간극을 앞질러서 가짜 "차단됨" TCP/UDP intent_check
            # 결과를 낼 만큼 자주 발생했다(라이브 진단으로 SYN/RST 왕복이
            # 조금만 기다리면 실제로는 멀쩡함을 확인).
            time.sleep(1)

            self._verify_intents(
                net, intent_specs, ip_to_host, host_to_ip, primary_pair,
                checks=checks, min_mbps=min_mbps, custom_data=custom_data,
                checks_result=checks_result, evidence=evidence,
            )

            if "regression" in checks:
                self._verify_regression(
                    net, primary_pair, regression_pair, host_to_ip, checks_result, evidence
                )

            verdict_keys = [k for k in checks_result if not k.startswith("_")]
            all_passed = all(checks_result[k] for k in verdict_keys)
            failed = [k for k in verdict_keys if not checks_result[k]]
            return TwinResult(
                status="passed" if all_passed else "failed",
                reason="all checks passed" if all_passed else f"failed checks: {', '.join(failed)}",
                checks=checks_result,
                evidence=evidence,
            )

        except Exception as exc:
            return TwinResult(
                status="error", reason=f"Digital Twin error: {exc}",
                checks=checks_result, evidence=evidence,
            )

        finally:
            if traffic is not None:
                try:
                    traffic.stop()
                except Exception:
                    pass
            self._log("(rollback) removing deployed FlowRule...")
            try:
                # 첫 번째 sub-rule만이 아니라 배포된 모든 flow의 priority를
                # 롤백한다 — 케이스가 intent_specs[0]이 다루지 않는 추가
                # flow(예: E3의 강제 배경-경로 규칙)를 함께 담을 수 있고,
                # 안 그러면 그게 케이스가 끝난 뒤에도 ONOS에 남는다.
                priorities = {f.get("priority") for f in flows if f.get("priority") is not None}
                if priorities:
                    for priority in priorities:
                        client.delete_flows_by_priority(priority)
                else:
                    client.clear_app_flows()
            except Exception:
                pass
            if net is not None:
                try:
                    net.stop()
                except Exception:
                    pass
            try:
                subprocess.run(["mn", "-c"], capture_output=True, timeout=15)
            except Exception:
                pass
            # mn -c는 이 케이스가 만들었을 ovsdb QoS/Queue 행을 회수하지
            # 않으므로 여기서도 지운다 — 안 그러면 남은 케이스들에 쌓인다.
            clear_ovs_qos()

    # ── 인텐트 추출 / 체크 ──────────────────────────────────────────────────

    @staticmethod
    def _extract_intent_specs(flowrule: dict) -> list[tuple]:
        """``flowrule["flows"]``의 각 flow에서 (action, src_ip, dst_ip, proto, port, flow)를 뽑는다.

        `tiger_sdn.compile.compile_prediction()`은 컴파운드 예측이라도
        `sub_rules` 없이 `{"flows": [f1, ..., fN]}`로 평평하게 낸다 — 규칙
        하나가 flow 하나다. 그래서 이 함수는 flow마다 하나의 spec을 뽑는다
        (원본처럼 `intent_action`/`sub_rules` 중첩을 가정하지 않는다). action은
        treatment 유무로만 판정한다 — `_verify_intents`가 실제로 쓰는 건
        `expect_reach = action != "block"` 뿐이라 forward/qos/sfc/reroute를
        더 세분화할 필요가 없다.
        """
        specs: list[tuple] = []
        for flow in flowrule.get("flows", []):
            action = "forward" if flow.get("treatment") else "block"
            src = dst = proto = port = None
            for c in flow.get("selector", {}).get("criteria", []):
                if c["type"] == "IPV4_SRC":
                    src = c.get("ip", "").split("/")[0]
                elif c["type"] == "IPV4_DST":
                    dst = c.get("ip", "").split("/")[0]
                elif c["type"] == "IP_PROTO":
                    proto = {6: "tcp", 17: "udp", 1: "icmp"}.get(c.get("protocol"))
                elif c["type"] == "TCP_DST":
                    port = c.get("tcpPort")
                elif c["type"] == "UDP_DST":
                    port = c.get("udpPort")
            if src is not None or dst is not None:
                specs.append((action, src, dst, proto, port, flow))
        return specs

    @staticmethod
    def _egress_port(flow: dict) -> str | int | None:
        """컴파일된 flow dict의 OUTPUT 명령 포트를 반환한다."""
        if not flow.get("treatment"):
            return None
        for instr in flow["treatment"].get("instructions", []):
            if instr.get("type") == "OUTPUT":
                return instr.get("port")
        return None

    @staticmethod
    def _ip_to_host(custom_data: dict | None) -> dict[str, str]:
        if custom_data:
            mapping = {h["ip"]: h["id"] for h in custom_data.get("hosts", []) if h.get("ip")}
            if mapping:
                return mapping
        return dict(_DEFAULT_IP_MAP)

    def _verify_intents(
        self, net, intent_specs, ip_to_host, host_to_ip, primary_pair,
        *, checks, min_mbps, custom_data, checks_result, evidence,
    ) -> None:
        for idx, (action, src_ip, dst_ip, proto, port, flow) in enumerate(intent_specs):
            suffix = "" if len(intent_specs) == 1 else f"_{idx}"
            dst_host = ip_to_host.get(dst_ip or "", primary_pair[1])
            if src_ip is not None:
                src_host = ip_to_host.get(src_ip, primary_pair[0])
            else:
                src_host = next((h for h in ip_to_host.values() if h != dst_host), primary_pair[0])
            dst_ip_resolved = dst_ip or host_to_ip.get(primary_pair[1], "10.0.0.4")
            expect_reach = action != "block"

            steered = self._install_steering(net, action, src_ip, dst_ip, src_host, flow, custom_data)
            try:
                if proto in ("tcp", "udp") and port is not None:
                    ok, msg = self._port_check(net, src_host, dst_ip_resolved, proto, port, expect_reach)
                else:
                    ok, msg = self._ping_check(net, src_host, dst_ip_resolved, expect_reach)
                checks_result[f"intent_check{suffix}"] = ok
                evidence[f"intent_msg{suffix}"] = msg
            finally:
                self._remove_steering(net, steered)

            # bandwidth 프로브: 도달 가능한(forward/qos/reroute) 인텐트에만.
            if "bandwidth" in checks and expect_reach and min_mbps is not None:
                measured = measure_bandwidth(
                    net, src_host, dst_host, dst_ip_resolved,
                    udp=(proto == "udp"),
                )
                met = meets_target(measured, min_mbps)
                checks_result[f"bandwidth{suffix}"] = met
                evidence[f"measured_mbps{suffix}"] = measured
                evidence[f"bandwidth_target_mbps{suffix}"] = min_mbps

    def _install_steering(self, net, action, src_ip, dst_ip, src_host, flow, custom_data) -> list[str]:
        """block 인텐트의 트래픽을 차단 스위치를 반드시 거치도록 강제한다
        (fwd 앱이 우회 경로를 잡지 못하게). 정리용으로 거쳐간 스위치 목록을
        반환한다."""
        steered: list[str] = []
        if action != "block" or not custom_data or not (src_ip and dst_ip):
            return steered
        block_sw = _device_id_to_sw_name(flow.get("deviceId", ""), custom_data)
        src_sw = _find_host_switch(src_host, custom_data)
        if not (block_sw and src_sw):
            return steered
        sw_path = _bfs_sw_path(src_sw, block_sw, custom_data)
        if len(sw_path) < 2:
            return steered
        for i in range(len(sw_path) - 1):
            hop, nxt = sw_path[i], sw_path[i + 1]
            out_port = _find_mininet_port(net, hop, nxt)
            if out_port:
                match = f"cookie={_STEERING_COOKIE},priority=55000,ip,nw_src={src_ip},nw_dst={dst_ip},actions=output:{out_port}"
                net.get(hop).cmd("ovs-ofctl", "add-flow", hop, shlex.quote(match), "-O", "OpenFlow13")
                steered.append(hop)
        if steered:
            time.sleep(1)
        return steered

    @staticmethod
    def _remove_steering(net, steered: list[str]) -> None:
        for hop in steered:
            match = f"{_STEERING_COOKIE}/-1"
            net.get(hop).cmd("ovs-ofctl", "del-flows", hop, shlex.quote(match), "-O", "OpenFlow13")

    def _verify_regression(
        self, net, primary_pair, regression_pair, host_to_ip, checks_result, evidence
    ) -> None:
        if regression_pair == primary_pair:
            checks_result["regression"] = True
            evidence["regression_msg"] = "skipped -- no independent host pair"
            return
        regression_dst_ip = host_to_ip.get(regression_pair[1], "10.0.0.3")
        ok, msg = self._ping_check(net, regression_pair[0], regression_dst_ip, expect_reach=True)
        checks_result["regression"] = ok
        evidence["regression_msg"] = msg

    # ── 저수준 프로브 ────────────────────────────────────────────────────────

    def _ping_check(self, net, src_host: str, dst_ip: str, expect_reach: bool) -> tuple[bool, str]:
        """ICMP 도달성 — 응답이 하나라도 통과하거나 시도 횟수가 소진될 때까지 재시도.

        두 가지 규칙이 중요하다.

        * "도달 가능"은 *응답이 하나라도 통과했다*는 뜻이지 "손실 0%"가
          아니다. 혼잡하지만 살아있는 링크는 ICMP를 일부 정당하게 떨어뜨릴
          수 있다 — 대역폭 부족을 잡는 건 이 체크가 아니라 bandwidth
          프로브의 몫이다.
        * 3패킷 ping 한 번을 재시도하는 이유: egress 포트를 고정하는 규칙을
          배포하면 트래픽이 ONOS의 반응형 `fwd` 앱이 아직 하류에 채우지
          않은 경로로 리다이렉트될 수 있다. 이 수렴 간극이 잠시 뒤엔 멀쩡한
          경로에서 100% 손실의 "차단됨" 판독을 내는 게 관측됐다(같은 케이스가
          다른 arm에서는 통과했다). 한 라운드라도 성공하면 도달성이
          증명되므로, 재시도 루프는 첫 성공에서 멈추고 완전히 도달 불가능한
          경로만 루프를 소진시킨다.
        """
        try:
            if not re.match(r"^[\d.]+$", dst_ip):
                return False, f"invalid IP: {dst_ip}"
            host = net.get(src_host)
            losses: list[int] = []
            for attempt in range(_REACH_ATTEMPTS):
                host.sendCmd(f"ping -c 3 -W 1 {dst_ip}")
                result = host.waitOutput()
                m = re.search(r"(\d+)% packet loss", result)
                losses.append(int(m.group(1)) if m else 100)
                if losses[-1] < 100:
                    break
                if attempt < _REACH_ATTEMPTS - 1:
                    time.sleep(_REACH_RETRY_DELAY)
            reachable = min(losses) < 100
            if reachable and len(losses) > 1:
                self._log(f"   (note) {src_host}->{dst_ip} needed {len(losses)} ping rounds to converge: {losses}% loss")
            verb = f"reachable ({min(losses)}% loss)" if reachable else "blocked"
            success = reachable if expect_reach else not reachable
            return success, f"{src_host}->{dst_ip} {verb} (expected {'reach' if expect_reach else 'block'})"
        except Exception as exc:
            return False, f"ping error: {exc}"

    def _port_check(
        self, net, src_host: str, dst_ip: str, proto: str, port: int, expect_reach: bool
    ) -> tuple[bool, str]:
        """``src_host``에서의 raw 소켓 connect로 TCP/UDP 도달성을 본다.

        ``_ping_check``와 같은 두 규칙: 데이터플레인 수렴 간극(ONOS가 flow를
        ADDED로 보고해도 OVS가 설치를 끝냈다는 뜻은 아님) 때문에 연결을
        재시도하고, **어떤** 시도든 성공하면 도달성이 증명된다 — 그래서
        루프는 첫 성공에서 멈추고 정말로 도달 불가능한 대상만 루프를
        소진시킨다. 마지막 시도가 아니라 "하나라도 성공"을 취하는 건
        security(block) 인텐트에도 안전한 방향으로 읽히게 한다 — 뭐라도
        통과했다면 정책이 막지 못한 것이다.
        """
        try:
            if not re.match(r"^[\d.]+$", dst_ip):
                return False, f"invalid IP: {dst_ip}"
            port = int(port)
            host = net.get(src_host)
            cmd = (
                'python3 -c "import socket,errno;'
                "s=socket.socket();s.settimeout(3);"
                f"e=s.connect_ex(('{dst_ip}',{port}));s.close();"
                "print('REACHABLE' if e==0 or e==errno.ECONNREFUSED else 'BLOCKED')\""
            )
            attempts: list[bool] = []
            for attempt in range(_REACH_ATTEMPTS):
                host.sendCmd(cmd)
                attempts.append("REACHABLE" in host.waitOutput())
                if attempts[-1]:
                    break
                if attempt < _REACH_ATTEMPTS - 1:
                    time.sleep(_REACH_RETRY_DELAY)
            reachable = any(attempts)
            if reachable and len(attempts) > 1:
                self._log(f"   (note) {src_host}->{dst_ip}:{port} needed {len(attempts)} attempts to converge")
            success = reachable if expect_reach else not reachable
            verb = "reachable" if reachable else "blocked"
            return success, f"{src_host}->{dst_ip}:{proto.upper()}/{port} {verb} (expected {'reach' if expect_reach else 'block'})"
        except Exception as exc:
            return False, f"port check error: {exc}"

    @staticmethod
    def _check_platform() -> str:
        """Linux + root + Mininet이 모두 있으면 "", 아니면 skip 사유를 반환한다."""
        if sys.platform != "linux":
            return f"platform is not Linux (got {sys.platform})"
        if os.geteuid() != 0:
            return "no root privileges (run with sudo -E)"
        try:
            subprocess.run(["mn", "--version"], capture_output=True, check=True, timeout=5)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return "Mininet (mn) is not installed"
        return ""
