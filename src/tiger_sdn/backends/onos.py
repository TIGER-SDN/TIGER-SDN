"""src/tiger_sdn/backends/onos.py — 최소 ONOS REST 클라이언트.

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/onos_client.py.
내용 변경 없이 그대로 옮겼다 — 이미 모듈 수준 `config` 의존성이 없고(연결
정보를 생성자 인자로 받음), stdlib(`urllib`)만 써서 서드파티 의존성이 없다.
`docs/plan.md` 목표 구조가 이 파일을 `backends/onos.py`로 격리해 뒀다 — Stage 5
(컴파일러의 `compile/onos.py`는 FlowRule *스키마*만 정의)와 달리, 이 파일은
실제 ONOS 컨트롤러에 그 FlowRule을 배포/조회/롤백하는 *클라이언트*다.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any


class OnosError(RuntimeError):
    """ONOS REST 요청이 실패했을 때 발생한다."""


class OnosClient:
    """작은 ONOS REST API 클라이언트 (urllib으로 GET/POST/DELETE)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8181/onos/v1",
        username: str = "onos",
        password: str = "rocks",
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        }

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        """ONOS REST 요청을 보내고 파싱된 JSON을 반환한다(본문이 없으면 None)."""
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        data = None
        headers = dict(self.headers)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return json.loads(body.decode("utf-8")) if body else None
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OnosError(f"ONOS HTTP {exc.code}: {body[:300]}") from exc
        except URLError as exc:
            raise OnosError(f"ONOS connection failed: {exc.reason}") from exc

    def wait_until_ready(self, timeout: float = 120.0, interval: float = 2.0) -> None:
        """ONOS가 devices 조회에 응답할 때까지 블록한다. timeout 초과 시 예외."""
        deadline = time.monotonic() + timeout
        last_error = "not ready"
        while time.monotonic() < deadline:
            try:
                self.request("GET", "devices")
                return
            except OnosError as exc:
                last_error = str(exc)
                time.sleep(interval)
        raise OnosError(f"ONOS not ready within {timeout:.0f}s: {last_error}")

    def devices(self) -> list[dict[str, Any]]:
        return self.request("GET", "devices").get("devices", [])

    def available_device_ids(self) -> set[str]:
        return {d["id"] for d in self.devices() if d.get("available") is True and d.get("id")}

    def wait_for_devices(
        self, expected_ids: set[str], timeout: float = 60.0, interval: float = 2.0
    ) -> set[str]:
        """expected_ids의 모든 항목이 available로 보고될 때까지 블록한다."""
        deadline = time.monotonic() + timeout
        available: set[str] = set()
        while time.monotonic() < deadline:
            available = self.available_device_ids()
            if expected_ids <= available:
                return available
            time.sleep(interval)
        missing = sorted(expected_ids - available)
        raise OnosError(f"ONOS devices never connected: {missing}")

    def wait_for_hosts(
        self, expected_count: int, timeout: float = 30.0, interval: float = 2.0
    ) -> int:
        """ONOS가 최소 expected_count개의 호스트를 발견할 때까지 블록한다.

        ONOS는 packet-in으로 호스트 위치를 학습한다. twin의 토폴로지는
        `autoStaticArp=True`로 빌드되어 호스트가 ARP를 보내지 않으므로, 실제
        트래픽을 보내기 전까지 ONOS가 호스트를 배치할 수 없다 — 그때까지는
        `fwd`가 설치할 경로가 없어 첫 reachability probe가 실패한다. 호출자는
        모든 호스트가 보이도록 워밍업 `pingAll`과 이 함수를 짝지어 쓴다.

        timeout에 도달해도 예외 대신 관측된 개수를 반환하고 경고만 남긴다 —
        진짜 판정은 reachability 체크 자체이기 때문이다.
        """
        deadline = time.monotonic() + timeout
        count = 0
        while time.monotonic() < deadline:
            try:
                count = len(self.hosts())
            except OnosError:
                count = 0
            if count >= expected_count:
                return count
            time.sleep(interval)
        print(f"    [Twin] warning: ONOS discovered {count}/{expected_count} hosts before timeout")
        return count

    def wait_for_flow(
        self, device_id: str, priority: int, timeout: float = 15.0, interval: float = 1.0
    ) -> None:
        """device_id 위 priority의 flow가 ADDED 상태가 될 때까지 블록한다.

        timeout에 도달해도 원본과 동일하게 예외 대신 경고 후 반환한다 —
        미확인 flow는 약한 신호일 뿐이고, 진짜 판정은 이어지는 연결성
        체크이기 때문이다.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                for f in self.request("GET", f"flows/{device_id}").get("flows", []):
                    if f.get("priority") == priority and f.get("state") == "ADDED":
                        return
            except Exception:
                pass
            time.sleep(interval)
        print(f"    [Twin] warning: flow(priority={priority}) not confirmed ADDED (continuing)")

    def activate_application(self, app_name: str) -> None:
        self.request("POST", f"applications/{app_name}/active")

    def deploy_flow_rules(self, payload: dict[str, Any]) -> None:
        flows = payload.get("flows")
        if not isinstance(flows, list) or not flows:
            raise ValueError("payload must contain a non-empty 'flows' array")
        self.request("POST", "flows", payload)

    def flows(self) -> list[dict[str, Any]]:
        return self.request("GET", "flows").get("flows", [])

    def delete_flow(self, device_id: str, flow_id: str) -> None:
        self.request("DELETE", f"flows/{device_id}/{flow_id}")

    def delete_flows_by_priority(self, priority: int) -> int:
        """priority의 모든 flow를 삭제한다(롤백 기본 동작). 삭제된 개수를 반환."""
        matches = [f for f in self.flows() if f.get("priority") == priority]
        failed = 0
        for flow in matches:
            try:
                self.delete_flow(flow["deviceId"], flow["id"])
            except Exception as exc:
                failed += 1
                print(f"    [warn] failed to delete flow {flow.get('id')}: {exc}")
        if failed:
            print(f"    [warn] rollback left {failed}/{len(matches)} flows in ONOS")
        return len(matches) - failed

    def hosts(self) -> list[dict[str, Any]]:
        return self.request("GET", "hosts").get("hosts", [])

    def links(self) -> list[dict[str, Any]]:
        return self.request("GET", "links").get("links", [])

    def port_statistics(self) -> list[dict[str, Any]]:
        """포트별 byte/packet 카운터 (링크 사용률 증거용)."""
        return self.request("GET", "statistics/ports").get("statistics", [])

    def clear_app_flows(self, app_ids: list[str] | None = None) -> None:
        """주어진 앱들이 설치한 flow를 삭제한다(기본값: REST + fwd)."""
        if app_ids is None:
            app_ids = ["org.onosproject.rest", "org.onosproject.fwd"]
        for app_id in app_ids:
            try:
                self.request("DELETE", f"flows/application/{app_id}")
            except Exception:
                pass
