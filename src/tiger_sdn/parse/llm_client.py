"""src/tiger_sdn/parse/llm_client.py — 모델명으로 백엔드를 자동 선택하는 LLM 호출.

원본: sdn-intent-framework의 src/xai_pipeline/pipeline/stage1_intent/llm_client.py
(백엔드 라우팅 규칙) 대신, TIGER-SDN에 이미 이식돼 있던
experiments/exp1/run.py의 call_llm/call_gemini/call_ollama/call_openrouter/
_call_openai_compatible을 베이스로 삼았다 — Exp-1용으로 이미 tiger_sdn.config를
쓰도록 적응돼 있고, 스트리밍 대신 non-streaming으로 바꿔 토큰 사용량/지연시간을
실제로 받아오게 되어 있어(원본 llm_client.py는 스트리밍이라 토큰 수를 못 얻는다)
runctx 로깅(execution_time/token_usage)에 그대로 꽂을 수 있다.

experiments/exp1/run.py 자체는 건드리지 않았다 — CLAUDE.md의 설계대로 Exp-1은
코어와 분리돼 있어야 하므로(prompt/scoring/gold만 그 수치에 영향), 여기서
가져다 쓰는 대신 독립적으로 다시 포팅했다.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any, NamedTuple, Optional

from tiger_sdn import config

__all__ = ["LLMResponse", "call_llm"]


class LLMResponse(NamedTuple):
    raw_text: Optional[str]
    parsed: Optional[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    latency_ms: float
    error_kind: Optional[str]  # None | "transport" | "schema_invalid"
    error_message: Optional[str]


def _strip_thinking_and_fences(text: str) -> str:
    cleaned = text
    if "<think>" in cleaned:
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        cleaned = "\n".join(inner).strip()
    return cleaned


def _call_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout_s: float = 120.0,
    retries: int = 2,
    max_tokens: Optional[int] = None,
    extra_payload: Optional[dict] = None,
) -> LLMResponse:
    """OpenAI 호환 /chat/completions 호출 (non-streaming). Ollama/OpenRouter 공용."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        # Cloudflare가 Python-urllib 기본 User-Agent를 봇으로 차단(403 code 1010)한다.
        "User-Agent": "Mozilla/5.0",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra_payload:
        payload.update(extra_payload)

    max_attempts = retries + 1
    for attempt in range(max_attempts):
        started = time.perf_counter()
        try:
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
            latency_ms = (time.perf_counter() - started) * 1000

            raw_text = (
                body.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            ).strip()
            usage = body.get("usage", {}) or {}
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0

            cleaned = _strip_thinking_and_fences(raw_text)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                return LLMResponse(raw_text, None, input_tokens, output_tokens, latency_ms, "schema_invalid", str(exc))
            return LLMResponse(raw_text, parsed, input_tokens, output_tokens, latency_ms, None, None)

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            if attempt < max_attempts - 1:
                time.sleep(2**attempt)
            else:
                return LLMResponse(None, None, 0, 0, latency_ms, "transport", str(exc))

    return LLMResponse(None, None, 0, 0, 0.0, "transport", "max retries exceeded")


def _call_gemini(
    *, model: str, system: str, user: str, temperature: float = 0.2, retries: int = 3,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    from google import genai
    from google.genai import types

    api_key = config.GOOGLE_API_KEY
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set — check .env")

    client = genai.Client(api_key=api_key)
    max_attempts = retries + 1

    for attempt in range(max_attempts):
        started = time.perf_counter()
        try:
            gen_config_kwargs: dict[str, Any] = dict(
                system_instruction=system, response_mime_type="application/json", temperature=temperature,
            )
            if max_tokens is not None:
                gen_config_kwargs["max_output_tokens"] = max_tokens
            response = client.models.generate_content(
                model=model, contents=user, config=types.GenerateContentConfig(**gen_config_kwargs),
            )
            latency_ms = (time.perf_counter() - started) * 1000
            raw_text = response.text or ""
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                return LLMResponse(raw_text, None, input_tokens, output_tokens, latency_ms, "schema_invalid", str(exc))
            return LLMResponse(raw_text, parsed, input_tokens, output_tokens, latency_ms, None, None)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            if attempt < max_attempts - 1:
                err_str = str(exc)
                wait = [5, 15, 30][min(attempt, 2)] if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) else 2**attempt
                time.sleep(wait)
            else:
                return LLMResponse(None, None, 0, 0, latency_ms, "transport", str(exc))

    return LLMResponse(None, None, 0, 0, 0.0, "transport", "max retries exceeded")


def call_llm(
    model: str, *, system: str, user: str, temperature: float = 0.2,
    timeout_s: float = 120.0, retries: int = 2, max_tokens: Optional[int] = None,
) -> LLMResponse:
    """모델명으로 백엔드를 자동 선택해 호출한다.

    gemini* -> Gemini API / '/' 포함(예: qwen/qwen3-8b) -> OpenRouter / 그 외 -> Ollama.
    """
    if config.is_gemini(model):
        return _call_gemini(model=model, system=system, user=user, temperature=temperature, max_tokens=max_tokens)
    if "/" in model:
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY not set — check .env")
        return _call_openai_compatible(
            base_url=config.OPENROUTER_BASE_URL, api_key=config.OPENROUTER_API_KEY, model=model,
            system=system, user=user, temperature=temperature, timeout_s=timeout_s,
            retries=retries, max_tokens=max_tokens,
        )
    return _call_openai_compatible(
        base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, model=model,
        system=system, user=user, temperature=temperature, timeout_s=timeout_s,
        retries=retries, max_tokens=max_tokens,
    )
