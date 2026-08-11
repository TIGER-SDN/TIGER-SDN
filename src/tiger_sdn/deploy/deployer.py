"""src/tiger_sdn/deploy/deployer.py — ONOS FlowRule 실제 배포.

원본: sdn-intent-framework의 src/xai_pipeline/pipeline/stage6_deploy/deployer.py.
`OnosClient`/`OnosError`의 위치만 `tiger_sdn.backends.onos`로 바꾸고(docs/plan.md
목표 구조가 배포 백엔드를 `backends/`로 격리해 뒀다), `config` 임포트를
`tiger_sdn.config`로 바꾼 것 외에는 내용 변경 없이 옮겼다 — 메서드 시그니처
(`deploy_flow_rules`/`flows`)가 원본 `OnosClient`와 그대로 일치한다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from tiger_sdn import config
from tiger_sdn.backends.onos import OnosClient, OnosError


@dataclass
class DeployResult:
    """FlowRule 배포 결과"""

    success: bool
    flow_ids: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if self.success:
            return f"배포 성공 ({len(self.flow_ids)}개 flow ID: {self.flow_ids})"
        return f"배포 실패: {self.error}"


class Deployer:
    """ONOS FlowRule 배포기"""

    def __init__(
        self,
        onos_url: str | None = None,
        onos_user: str | None = None,
        onos_password: str | None = None,
    ) -> None:
        self.onos_url = onos_url or config.ONOS_URL
        self.onos_user = onos_user or config.ONOS_USER
        self.onos_password = onos_password or config.ONOS_PASSWORD

    def deploy(self, flowrule: dict) -> DeployResult:
        """ONOS에 FlowRule을 배포하고 새로 생긴 flow ID를 수집한다.

        Args:
            flowrule: {"flows": [...]} 형식의 FlowRule dict

        Returns:
            DeployResult
        """
        client = OnosClient(
            base_url=self.onos_url,
            username=self.onos_user,
            password=self.onos_password,
        )

        try:
            before_flows = client.flows()
            before_ids = {f.get("id") for f in before_flows if f.get("id")}
        except OnosError:
            before_ids = set()

        try:
            client.deploy_flow_rules(flowrule)
        except (OnosError, ValueError) as exc:
            return DeployResult(success=False, error=str(exc))

        time.sleep(1)  # 배포 안정화 대기
        try:
            after_flows = client.flows()
        except OnosError as exc:
            return DeployResult(success=True, flow_ids=[], error=f"flow 조회 실패: {exc}")

        # 배포된 FlowRule의 (deviceId, priority) 조합으로 필터링 — before/after id
        # 차집합만으로는 동시에 다른 프로세스가 추가한 무관한 flow까지 "신규"로
        # 오귀속될 수 있으므로 둘 다 만족해야 한다.
        target_keys = {
            (f.get("deviceId"), f.get("priority"))
            for f in flowrule.get("flows", [])
            if f.get("deviceId") and f.get("priority") is not None
        }

        new_flow_ids: list[str] = []
        for flow in after_flows:
            flow_id = flow.get("id")
            if not flow_id or flow_id in before_ids:
                continue
            if target_keys and (flow.get("deviceId"), flow.get("priority")) not in target_keys:
                continue
            new_flow_ids.append(flow_id)

        return DeployResult(success=True, flow_ids=new_flow_ids)
