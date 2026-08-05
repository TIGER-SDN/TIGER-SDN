"""src/tiger_sdn/config.py — LLM API 자격증명/엔드포인트 로딩

원본: sdn-intent-framework의 src/xai_pipeline/config.py. experiments/exp1/run.py가
실제로 쓰는 LLM 자격증명·엔드포인트만 남기고 트림했다 — API 서버(CORS/API_KEY),
ONOS, 데이터셋 경로 폴백 등은 해당 서브시스템을 이식하는 Stage에서 다시 다룬다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")

LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "https://ollama.jangmyun.dev/v1")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "gemini-3.1-flash-lite")
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "ollama")
GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def is_gemini(model: str) -> bool:
    """Gemini 모델 여부 판단"""
    return model.lower().startswith("gemini")
