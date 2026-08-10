"""src/tiger_sdn/twin/__init__.py — Digital Twin 실행 코어 (Stage 7).

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/__init__.py.
`OnosClient`/`OnosError` re-export는 `tiger_sdn.backends.onos`로 옮겨갔으므로
뺐다 — `docs/plan.md` 목표 구조가 ONOS 클라이언트를 `backends/`로 격리해 뒀다.

여기 있는 모든 것은 실행 시점에 Linux + root + Mininet + 접근 가능한 ONOS
컨트롤러를 요구한다. Mininet은 build/verify 함수 *안에서* 지연 import되므로,
비-Linux/비-root 호스트(예: CI, 이 개발 머신의 Windows 네이티브)에서 이
패키지를 import하는 것 자체는 안전하고 순수 헬퍼만 실행 가능하다.
"""

from __future__ import annotations

from tiger_sdn.twin.twin_verifier import REACH_AND_BANDWIDTH, REACH_ONLY, TwinResult, TwinVerifier

__all__ = ["REACH_AND_BANDWIDTH", "REACH_ONLY", "TwinResult", "TwinVerifier"]
