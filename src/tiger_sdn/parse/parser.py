"""src/tiger_sdn/parse/parser.py — 자연어 인텐트 -> IntentPrediction.

원본: sdn-intent-framework의
src/xai_pipeline/pipeline/stage1_intent/intent_parser.py(IntentParser.parse의
구조 — LLM 자체 거부 감지, repair_feedback 처리)를 베이스로 삼되, 세 가지를
바꿨다.

1. **RAG 없음.** `stage1_intent/rag.py`는 매 요청마다 임베딩 인덱스를 전량
   재구축해 이식이 아니라 재작성이 필요하다고 이미 결론 났다(docs/plan.md
   "이식하지 않는 것"). 여기서는 애초에 RAG 파라미터 자체를 받지 않는다.
2. **토폴로지 그라운딩을 파서가 직접 안 한다.** 원본은
   `NetworkTopology.check_intent()`로 파싱 직후 host/ip/device 존재를 검사했지만,
   TIGER-SDN에서는 그 역할을 `verify.grounding.verify_program()`이 결정론적으로
   대신한다(Stage 6) — LLM이 만든 IR을 있는 그대로 반환하고, 그라운딩 검증은
   orchestrate/pipeline.py가 다음 단계에서 돌린다. 프롬프트에 토폴로지 텍스트를
   주입하는 것(`grounding_prompt.build_topology_prompt`)은 여전히 하지만, 이건
   환각을 "줄이는" 용도이지 막는 게이트가 아니다.
3. **LLM 출력을 from_gold350()으로 통합 IR에 태운다.** `prompts/intent_ir.md`의
   출력 스키마(action=forward/block/qos/sfc/reroute, selector.src_port/dst_port,
   rule-level routing.waypoints)는 연구 스키마가 아니라 GOLD-350 제품 스키마와
   필드명이 그대로 일치한다 — `tiger_sdn.ir.adapter.from_gold350()`이 정확히
   이 형태를 위한 어댑터다(host->ip 별칭 변환 없이 IntentRule에 직접 매핑).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import NamedTuple, Optional

from pydantic import ValidationError

from tiger_sdn.ir import AdapterError, IntentPrediction, from_gold350
from tiger_sdn.parse.llm_client import LLMResponse, call_llm

__all__ = ["ParseResult", "build_system_prompt", "parse_intent"]

_VALID_REJECTION_REASONS = {"ambiguous", "contradictory", "unsupported", "unknown_entity"}


def _load_get_prompt():
    """prompts/registry.py를 경로 기반으로 로드한다.

    `prompts/`에는 `__init__.py`가 없어(레포 루트 전용, pip 패키징 대상이 아님)
    `from prompts.registry import get_prompt`는 저장소 루트가 마침 sys.path에
    있을 때(예: repo root가 CWD인 `uv run`)만 우연히 동작하고, 다른 CWD에서
    `tiger_sdn`을 import하면(향후 웹 서버 등) 조용히 깨진다.
    `tests/test_prompt_registry.py`가 이미 쓰던 방식(파일 경로 기반 로드)을
    그대로 써서 CWD와 무관하게 만든다.
    """
    repo_root = Path(__file__).resolve().parents[3]
    registry_path = repo_root / "prompts" / "registry.py"
    spec = importlib.util.spec_from_file_location("tiger_sdn_prompts_registry", registry_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.get_prompt


get_prompt = _load_get_prompt()


class ParseResult(NamedTuple):
    prediction: IntentPrediction
    llm_response: LLMResponse


def build_system_prompt(topology_prompt: str = "") -> str:
    """intent_ir 프롬프트를 조립한다. 그라운딩이 스키마 규칙보다 먼저 오게 한다."""
    base = get_prompt("intent_ir")
    return f"{topology_prompt}\n\n{base}" if topology_prompt else base


def parse_intent(
    intent: str,
    *,
    model: str,
    topology_prompt: str = "",
    repair_feedback: Optional[str] = None,
) -> ParseResult:
    """자연어 인텐트를 LLM으로 파싱해 IntentPrediction으로 반환한다.

    repair_feedback이 있으면 사용자 메시지 뒤에 덧붙인다(orchestrate.pipeline의
    repair loop가 이전 검증 실패를 피드백으로 재전달할 때 씀).

    Raises:
        ValueError: LLM 호출 자체가 실패(전송 오류 또는 응답이 JSON이 아님).
            이건 "인텐트가 거부됐다"가 아니라 파이프라인이 계속 진행할 수 없는
            상태라 IntentPrediction으로 감싸지 않고 예외로 올린다.
    """
    system = build_system_prompt(topology_prompt)
    user = intent if not repair_feedback else f"{intent}\n\n{repair_feedback}"
    response = call_llm(model, system=system, user=user)

    if response.parsed is None:
        raise ValueError(
            f"LLM call failed ({response.error_kind}) for intent {intent[:80]!r}: {response.error_message}"
        )

    return ParseResult(prediction=_to_prediction(response.parsed), llm_response=response)


def _to_prediction(raw: dict) -> IntentPrediction:
    if raw.get("status") == "rejected":
        reason_raw = str(raw.get("rejection_reason", "")).strip().lower()
        reason = reason_raw if reason_raw in _VALID_REJECTION_REASONS else "ambiguous"
        detail = str(raw.get("rejection_detail", "")).strip() or f"LLM rejected: {reason}"
        return IntentPrediction.reject(reason, detail)  # type: ignore[arg-type]

    rules = raw.get("rules")
    if not rules or not isinstance(rules, list):
        return IntentPrediction.reject("ambiguous", "LLM returned no rules array")

    try:
        return from_gold350({"status": "accepted", "rules": rules})
    except (AdapterError, ValidationError) as exc:
        return IntentPrediction.reject("ambiguous", f"IR validation failed: {exc}")
