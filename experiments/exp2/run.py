"""experiments/exp2/run.py — E2 데이터셋에 B1(컴파일러만) 또는 B2(검증기+컴파일러) 실행.

원본: sdn-intent-framework의 research/experiments/e2/run_validation.py.

gold/직접 작성한 Intent IR 픽스처에서 시작하는 컴포넌트 레벨, 고정 IR 평가다 —
LLM 호출이 없고, pass/fail *결과* 자체에는 반복이나 시드가 필요 없다 —
`verify_program`/`compile_prediction`은 `experiments/exp2/data/cases*.jsonl`에
있는 픽스처의 순수 결정론적 함수이므로 treatment당 한 번의 패스로 outcome/
findings/error가 정확히 나온다. 반면 지연(latency) 측정은 결정론적 코드라도
서브밀리초 단위에서 노이즈가 있으므로, 각 단계를 워밍업 후 여러 번 반복
측정해 중앙값으로 보고한다(`_timed_ms`) — pass/fail 결과 자체는 여전히 단일
호출에서 나온다(순수 함수를 타이밍만을 위해 다시 돌려도 결과가 바뀌지 않으므로).

케이스 데이터(`data/cases.jsonl`, `data/cases_sfc_reroute.jsonl`)는 연구
스키마(`source_port`, `enforcement.avoid_device`, `program.sfc_chain` 등)
그대로 커밋돼 있다 — `load_cases()`가 `tiger_sdn.ir.from_research()`로
통합 IR로 변환한다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tiger_sdn.compile import CompilationError, compile_prediction
from tiger_sdn.ir import IntentPrediction
from tiger_sdn.verify import TopologyInventory, load_topology_inventory, verify_program

from e2_evaluation import E2Case, E2Result, load_cases  # noqa: E402

TIMING_REPEATS = 31
TIMING_WARMUP = 1


def _timed_ms(fn: Callable[[], Any], *, repeats: int = TIMING_REPEATS, warmup: int = TIMING_WARMUP) -> float:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def _is_ip(value: str) -> bool:
    try:
        ip_address(value)
        return True
    except ValueError:
        return False


def host_ip_map(topology: dict[str, Any]) -> dict[str, str]:
    """완전한 host-alias -> IP 맵 — B1의 컴파일러가 공정한(퇴화하지 않은)
    기준선이 되려면, 실제 host alias를 전부 받아야 한다. 빈 리졸버를 주면
    환각인지 여부와 무관하게 host를 참조하는 규칙을 전부 거부하게 된다.
    """
    mapping: dict[str, str] = {}
    for entity in topology["entities"]:
        if not entity["id"].startswith("host:"):
            continue
        ip = next((alias for alias in entity["aliases"] if _is_ip(alias)), None)
        if ip is None:
            continue
        for alias in entity["aliases"]:
            if alias != ip:
                mapping[alias] = ip
    return mapping


def run_case(
    case: E2Case, *, treatment: str, endpoint_ips: dict[str, str], inventory: TopologyInventory
) -> E2Result:
    prediction = IntentPrediction(status="accepted", program=case.program, rejection=None)
    outcome, findings, rejection_stage, error = "accepted", [], None, None
    validator_duration_ms: float | None = None
    compiler_duration_ms: float | None = None

    if treatment == "B2":
        report = verify_program(case.program, inventory)
        validator_duration_ms = _timed_ms(lambda: verify_program(case.program, inventory))
        if not report.is_valid:
            outcome, findings, rejection_stage = "rejected", report.findings, "validator"

    if outcome == "accepted":
        try:
            compile_prediction(prediction, endpoint_ips=endpoint_ips)
        except CompilationError as exc:
            outcome, rejection_stage, error = "rejected", "compiler", str(exc)

        def _compile_once() -> None:
            try:
                compile_prediction(prediction, endpoint_ips=endpoint_ips)
            except CompilationError:
                pass

        compiler_duration_ms = _timed_ms(_compile_once)

    duration_ms = (validator_duration_ms or 0.0) + (compiler_duration_ms or 0.0)
    return E2Result(
        case_id=case.id, treatment=treatment, outcome=outcome,
        findings=findings, rejection_stage=rejection_stage, error=error,
        validator_duration_ms=validator_duration_ms, compiler_duration_ms=compiler_duration_ms,
        duration_ms=duration_ms,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--treatment", choices=["B1", "B2"], required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "experiments/exp2/data/cases.jsonl")
    parser.add_argument("--topology", type=Path, default=ROOT / "experiments/exp2/data/topology.json")
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
    if args.limit:
        cases = cases[: args.limit]

    topology = json.loads(args.topology.read_text(encoding="utf-8"))
    inventory = load_topology_inventory(topology)
    endpoint_ips = host_ip_map(topology)

    results = [
        run_case(case, treatment=args.treatment, endpoint_ips=endpoint_ips, inventory=inventory)
        for case in cases
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(result.model_dump_json() + "\n")
    print(f"wrote {len(results)} results to {args.output}")


if __name__ == "__main__":
    main()
