"""src/tiger_sdn/api — 웹 UI용 FastAPI 앱 (Stage 9).

원본: sdn-intent-framework의 src/xai_pipeline/api.py. 이 패키지는 그 하나의
파일을 이식한 게 아니라 `tiger_sdn.orchestrate.pipeline.run_pipeline()`(이미
파싱->그라운딩->컴파일->정적검증->twin의 Repair Loop 전체를 구현) 위에 HTTP
레이어만 얹는다 — 원본 api.py의 Stage1~4 인라인 오케스트레이션은 대상이 아니다.

`docs/plan.md`의 Stage 9 스코프 결정에 따라 원본 대비 뺀 것:
RAG(`rag_k`/`no_rag`), 커스텀 토폴로지 에디터(`/api/topology/custom`,
`/api/topology/apply`), 네트워크 프리셋 라이브 트래픽 시뮬레이터
(`/api/network-preset/*`), flow state 캐시(`/api/flow-state/*`).
"""

from __future__ import annotations
