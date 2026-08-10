"""src/tiger_sdn/twin/bandwidth.py — Digital Twin용 iperf3 대역폭 측정.

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/bandwidth.py. 내용
변경 없이 그대로 옮겼다.

reach-only twin(`REACH_ONLY`)이 일부러 생략하는 프로브이자, reach+bandwidth
twin(`REACH_AND_BANDWIDTH`)이 쓰는 프로브다. 두 Mininet 호스트 사이에 실제로
전달된 처리량을 측정하므로, "도달은 가능하지만 목표 대역폭을 못 채우는" QoS
인텐트를 조용히 통과시키지 않고 잡아낸다.

사람이 읽는 출력을 긁지 않도록 ``iperf3 -J``(JSON)를 쓰고 결과에서 달성
전송률을 파싱한다.
"""

from __future__ import annotations

import json
import re
import subprocess
import time

# 전달된 전송률이 목표의 이 비율 이상이면 "충족"으로 친다. HTB/tc 아래에서
# iperf3는 공칭 상한에 정확히 도달하지 않고 측정값도 잡음이 있어서, 1.0보다
# 낮은 여유가 없으면 간신히 충분한 링크를 실패로 라벨링하게 된다.
DEFAULT_TOLERANCE = 0.85

_SERVER_READY_TIMEOUT = 5.0
_CLIENT_RETRIES = 4
_CLIENT_RETRY_DELAY = 0.7


def _wait_for_server(dst, port: int, timeout: float = _SERVER_READY_TIMEOUT) -> bool:
    """``dst``의 일회성 iperf3 서버가 ``port``에서 리스닝할 때까지 폴링한다.

    이게 없으면 클라이언트가 서버의 비동기 ``popen()`` 기동과 경쟁해 아예
    연결에 실패할 수 있는데 — 그 연결 실패는 이전엔 "측정된 처리량이 정말
    0에 가깝다"와 구별되지 않았다(``_parse_mbps``가 둘 다 조용히 0.0을
    반환했으므로).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if f":{port} " in dst.cmd(f"ss -ltn 2>/dev/null | grep ':{port} '"):
            return True
        time.sleep(0.1)
    return False


def measure_bandwidth(
    net,
    src_host: str,
    dst_host: str,
    dst_ip: str,
    *,
    duration: int = 4,
    udp: bool = False,
    port: int = 5201,
) -> float:
    """``src_host``에서 ``dst_host``로 전달된 처리량을 Mbps로 반환한다.

    ``dst_host``에서 일회성 iperf3 서버를 띄우고 ``src_host``에서 iperf3
    클라이언트를 실행한다. 전송이 정말로 실패하면(예: 경로가 차단됨) ``0.0``을
    반환하는데, 호출자는 이를 "목표 미달성"으로 취급한다 — 다만 클라이언트를
    몇 번 재시도한 뒤에 그렇게 한다. 클라이언트 연결 실패(서버 미준비, 일시
    오류)로 인한 순수 0.0을 진짜 0 처리량 측정과 조용히 섞으면 안 되기
    때문이다.
    """
    if not re.match(r"^[\d.]+$", dst_ip):
        raise ValueError(f"invalid IP: {dst_ip}")

    src = net.get(src_host)
    dst = net.get(dst_host)

    # 일회성 서버(-1): 클라이언트 하나를 서빙하고 종료하므로 남는 프로세스가
    # 없다. Popen 핸들 덕분에 조기 반환 시 강제 종료할 수 있다.
    server = dst.popen(
        ["iperf3", "-s", "-1", "-p", str(port), "--json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_server(dst, port):
            print(f"    [Twin] warning: iperf3 server on {dst_host}:{port} never came up in time")

        flags = "-u -b 0" if udp else ""
        for attempt in range(_CLIENT_RETRIES):
            raw = src.cmd(f"iperf3 -c {dst_ip} -p {port} -t {duration} {flags} -J 2>/dev/null")
            mbps, parsed = _parse_mbps(raw, udp=udp)
            if parsed:
                return mbps
            print(f"    [Twin] warning: iperf3 client {src_host}->{dst_ip}:{port} attempt {attempt + 1} produced no parseable result")
            time.sleep(_CLIENT_RETRY_DELAY)
        return 0.0
    finally:
        try:
            server.terminate()
        except Exception:
            pass
        # 이중 안전장치: 일회성 서버가 남겨둔 iperf3를 강제 종료한다.
        dst.cmd("pkill -f 'iperf3 -s' 2>/dev/null")


def _parse_mbps(raw: str, *, udp: bool) -> tuple[float, bool]:
    """iperf3 ``-J`` 결과 블롭에서 달성 Mbps를 추출한다.

    ``(mbps, parsed)``를 반환한다 — ``parsed``는 유효한 측정값을 전혀 뽑을 수
    없을 때(클라이언트 오류, 손상된 JSON) False다. 정상 파싱됐지만 정말로
    0인 전송률과는 구별되므로, 호출자는 깨진 측정값을 "목표 미달성"으로 조용히
    취급하는 대신 재시도할 수 있다.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return 0.0, False
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return 0.0, False
    end_block = data.get("end", {})
    if udp:
        summary = end_block.get("sum", {})
    else:
        summary = end_block.get("sum_received") or end_block.get("sum", {})
    bits_per_second = summary.get("bits_per_second")
    if not isinstance(bits_per_second, (int, float)):
        return 0.0, False
    return round(bits_per_second / 1e6, 3), True


def meets_target(measured_mbps: float, target_mbps: float, tolerance: float = DEFAULT_TOLERANCE) -> bool:
    """``measured_mbps``가 ``target_mbps``의 tolerance 이내면 True."""
    return measured_mbps >= target_mbps * tolerance
