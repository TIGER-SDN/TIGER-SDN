"""scripts/run_pipeline.py — CLI로 orchestrate.pipeline.run_pipeline 하나를 돌려본다.

원본: sdn-intent-framework의 main.py(--intent 하나 받아 콘솔에 단계별로 찍는
CLI)의 역할이지만, 그 파일 자체(6단계, RAG, XAI 설명, ONOS 배포까지)를
포팅한 게 아니라 이번에 새로 만든 orchestrate.pipeline.run_pipeline을 위한
가벼운 스모크 CLI다. 실제 자연어 -> 결과 흐름을 웹 UI(Stage 9, 팀원 담당)
없이도 눈으로 확인하는 용도.

LLM을 실제로 호출하므로 .env에 LLM_* 자격증명이 설정돼 있어야 한다
(tiger_sdn.config 참고, .env.example에 항목 있음). Gemini 모델(기본값)을
쓰려면 `uv add google-genai`가 별도로 필요하다 — experiments/exp1/run.py의
call_gemini와 마찬가지로 pyproject.toml 기본 의존성에는 없다(지금까지
프로젝트 전체가 커밋된 로그 재채점만 해 왔지 실제로 LLM을 호출한 적이 없어서
아무도 이 의존성을 추가할 필요가 없었다 — 이 스크립트가 그 첫 실사용 지점).
Ollama/OpenRouter 모델(모델명에 "/" 포함, 예: qwen/qwen3-8b)은 stdlib(urllib)
만 쓰므로 추가 설치가 필요 없다.

Usage:
    uv run python scripts/run_pipeline.py --intent "block all traffic from 10.0.0.1 to 10.0.0.4 on switch 1"
    uv run python scripts/run_pipeline.py --intent "..." --model gemini-3.1-flash-lite --skip-twin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tiger_sdn import config  # noqa: E402
from tiger_sdn.orchestrate import run_pipeline  # noqa: E402

_EXIT_CODES = {"APPROVE": 0, "APPROVE_WITHOUT_TWIN": 1, "REJECT": 2, "ERROR": 3}


def main() -> int:
    # Windows 콘솔(cp949 등)에서 이 파일의 em dash 등이 UnicodeEncodeError로
    # print()를 죽이는 것을 방지 (원본 main.py의 같은 처리를 그대로 가져옴).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intent", required=True, help="자연어 네트워크 인텐트")
    parser.add_argument("--model", default=config.LLM_MODEL, help=f"LLM 모델 (기본: {config.LLM_MODEL})")
    parser.add_argument(
        "--topology", type=Path, default=ROOT / "data/gold/topology_eval.json",
        help="토폴로지 JSON 경로 (기본: data/gold/topology_eval.json)",
    )
    parser.add_argument("--skip-twin", action="store_true", help="Digital Twin 검증 스킵")
    parser.add_argument("--verbose", action="store_true", help="컴파일된 FlowRule까지 출력")
    args = parser.parse_args()

    topology = json.loads(args.topology.read_text(encoding="utf-8"))

    print(f'Intent: "{args.intent}"')
    print(f"Model:  {args.model}")
    print()

    result = run_pipeline(
        args.intent, model=args.model, topology=topology, skip_twin=args.skip_twin,
    )

    print(f"[repair attempts] {result.repair_attempts}")
    if result.prediction is not None:
        if result.prediction.status == "accepted":
            print(f"[parse] accepted, {len(result.prediction.program.rules)} rule(s)")
        else:
            print(f"[parse] rejected: {result.prediction.rejection}")
    if result.grounding_report is not None:
        print(f"[grounding] {len(result.grounding_report.findings)} finding(s)")
    if result.static_result is not None:
        print(f"[static] {result.static_result.summary()}")
    if result.twin_result is not None:
        print(f"[twin] {result.twin_result.summary()}")

    print()
    print(f"Decision: {result.decision}")
    print(f"Reason:   {result.reason}")

    if args.verbose and result.flow_set is not None:
        print()
        print(json.dumps(result.flow_set.model_dump(), indent=2))

    return _EXIT_CODES[result.decision]


if __name__ == "__main__":
    raise SystemExit(main())
