"""src/tiger_sdn/config.py — LLM API 자격증명/엔드포인트 로딩 + Stage 9 API 서버 설정

원본: sdn-intent-framework의 src/xai_pipeline/config.py. Stage 3에서는
experiments/exp1/run.py가 실제로 쓰는 LLM 자격증명·엔드포인트만 남기고 트림했다 —
API 서버(CORS/API_KEY), ONOS, 로그 경로는 "해당 서브시스템을 이식하는 Stage에서
다시 다룬다"고 미뤄뒀던 부분으로, Stage 9(tiger_sdn.api/orchestrate)가 이제
그 서브시스템이라 다시 채운다.
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

# ── ONOS ──────────────────────────────────────────────────────────────────
ONOS_URL: str = os.environ.get("ONOS_URL", "http://127.0.0.1:8181/onos/v1")
ONOS_USER: str = os.environ.get("ONOS_USER", "onos")
ONOS_PASSWORD: str = os.environ.get("ONOS_PASSWORD", "rocks")
ONOS_CONTROLLER_IP: str = os.environ.get("ONOS_CONTROLLER_IP", "127.0.0.1")
ONOS_CONTROLLER_PORT: int = int(os.environ.get("ONOS_CONTROLLER_PORT", "6653"))

# ── API 서버 (Stage 9) ────────────────────────────────────────────────────
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()
] or ["*"]
API_KEY: str = os.environ.get("API_KEY", "")
LOGS_DIR: Path = Path.cwd() / "logs"


def is_gemini(model: str) -> bool:
    """Gemini 모델 여부 판단"""
    return model.lower().startswith("gemini")
