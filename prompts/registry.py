"""prompts/registry.py — output_format -> 프롬프트 파일 매핑

원본: sdn-intent-framework의 src/xai_pipeline/pipeline/stage1_intent/intent_parser.py
(SYSTEM_PROMPT, T-B/T-C/T-D용)와 research/experiments/eval/run_exp1.py
(SYSTEM_DIRECT_FLOW, T-A용)에 하드코딩되어 있던 프롬프트 문자열을 prompts/*.md로
추출한 뒤, experiments/exp1 config의 [experiment].output_format 값과 프롬프트
파일의 대응표를 여기 한 곳에 고정한다. 코드에 프롬프트 문자열 리터럴을 두지
않는다 — Stage 3 완료 기준.
"""
from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent

REGISTRY: dict[str, str] = {
    "direct_flow": "direct_flow.md",
    "intent_ir": "intent_ir.md",
}


def get_prompt(output_format: str) -> str:
    """output_format에 대응하는 system prompt 텍스트를 반환한다."""
    try:
        filename = REGISTRY[output_format]
    except KeyError as exc:
        raise ValueError(f"unknown output_format: {output_format!r}") from exc
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")
