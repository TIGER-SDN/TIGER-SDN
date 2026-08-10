"""src/tiger_sdn/backends/__init__.py — 실제 SDN 컨트롤러와의 배포 백엔드.

원본: sdn-intent-framework의 research/safe_intent_sdn/twin/onos_client.py
(`OnosClient`/`OnosError`만 여기로 분리 — `docs/plan.md` 목표 구조가
`backends/onos.py`를 Stage 5-7이 공유하는 배포 백엔드로 격리해 뒀다. Mininet
전용 코드(`twin_verifier.py` 등)는 `tiger_sdn.twin`에 남는다.)
"""

from tiger_sdn.backends.onos import OnosClient, OnosError

__all__ = ["OnosClient", "OnosError"]
