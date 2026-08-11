"""tests/test_deploy.py — deploy.deployer.Deployer: ONOS FlowRule 실제 배포.

원본: 없음 (신규, Stage 9). `deploy.deployer.OnosClient`를 가짜로 바꿔치기해
실 ONOS 접속 없이 before/after flow 목록 diff 로직(신규 flow ID 식별)만
검증한다 — 이게 이 파일에서 유일하게 로직다운 부분이다(나머지는 얇은 REST
호출 래핑).
"""
from __future__ import annotations

from tiger_sdn.backends.onos import OnosError
from tiger_sdn.deploy import Deployer
from tiger_sdn.deploy import deployer as deployer_module

FLOWRULE = {
    "flows": [
        {"deviceId": "of:0000000000000001", "priority": 40000},
        {"deviceId": "of:0000000000000001", "priority": 40001},
    ]
}


class _FakeOnosClient:
    def __init__(self, before=(), after=(), deploy_error=None):
        self._sequence = [list(before), list(after)]
        self._call = 0
        self._deploy_error = deploy_error

    def flows(self) -> list[dict]:
        result = self._sequence[min(self._call, len(self._sequence) - 1)]
        self._call += 1
        return result

    def deploy_flow_rules(self, flowrule: dict) -> None:
        if self._deploy_error is not None:
            raise self._deploy_error


def _install_fake_client(monkeypatch, fake):
    monkeypatch.setattr(deployer_module, "OnosClient", lambda **kwargs: fake)
    monkeypatch.setattr(deployer_module.time, "sleep", lambda seconds: None)


def test_deploy_identifies_only_new_matching_flows(monkeypatch):
    before = [{"id": "0x1", "deviceId": "of:0000000000000001", "priority": 39999}]
    after = [
        {"id": "0x1", "deviceId": "of:0000000000000001", "priority": 39999},  # pre-existing
        {"id": "0x2", "deviceId": "of:0000000000000001", "priority": 40000},  # new, matches
        {"id": "0x3", "deviceId": "of:0000000000000001", "priority": 40001},  # new, matches
        {"id": "0x4", "deviceId": "of:0000000000000002", "priority": 12345},  # new, unrelated
    ]
    _install_fake_client(monkeypatch, _FakeOnosClient(before=before, after=after))

    result = Deployer().deploy(FLOWRULE)

    assert result.success is True
    assert set(result.flow_ids) == {"0x2", "0x3"}


def test_deploy_returns_failure_on_deploy_error(monkeypatch):
    _install_fake_client(
        monkeypatch, _FakeOnosClient(before=[], after=[], deploy_error=OnosError("controller down"))
    )

    result = Deployer().deploy(FLOWRULE)

    assert result.success is False
    assert "controller down" in result.error
    assert result.flow_ids == []


def test_deploy_tolerates_before_flows_lookup_failure(monkeypatch):
    class _RaisingBeforeClient(_FakeOnosClient):
        def flows(self):
            if self._call == 0:
                self._call += 1
                raise OnosError("no controller yet")
            return super().flows()

    after = [{"id": "0x2", "deviceId": "of:0000000000000001", "priority": 40000}]
    _install_fake_client(monkeypatch, _RaisingBeforeClient(before=[], after=after))

    result = Deployer().deploy(FLOWRULE)

    assert result.success is True
    assert result.flow_ids == ["0x2"]


def test_deploy_result_summary_reports_success_and_failure():
    assert "성공" in deployer_module.DeployResult(success=True, flow_ids=["0x1"]).summary()
    assert "실패" in deployer_module.DeployResult(success=False, error="boom").summary()
