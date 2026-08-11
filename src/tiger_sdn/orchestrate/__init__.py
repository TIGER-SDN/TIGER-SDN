"""src/tiger_sdn/orchestrate — parse -> verify -> compile -> verify -> twin 배선 (Stage 8/9).

`docs/plan.md`가 "Repair Loop 승격"이라 부르는 부분 — main.py/api.py에 있던
이중 구현을 여기 하나로 승격한다.
"""

from __future__ import annotations

from tiger_sdn.orchestrate.pipeline import PipelineResult, run_pipeline

__all__ = ["PipelineResult", "run_pipeline"]
