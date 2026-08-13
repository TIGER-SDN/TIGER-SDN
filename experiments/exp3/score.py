"""experiments/exp3/score.py — Exp-3 arm 로그를 cross-arm 리포트(paper 산출물)로 채점.

원본: 없음 (신규, 이슈 #37 / docs/plan.md Exp-3 참고). `experiments/exp2/
score.py`의 다중 결과 파일 -> treatment별 그룹 -> 비교 리포트 패턴을 arm
4종(Tier A)/2종(Tier B)으로 확장한다.

범위 주의: Tier A는 attempt-0 파싱을 capture-ir 로그에서 공유하는 세 arm
(no_grounding/no_static/full)과 완전히 독립적인 no_system을 함께 비교한다 —
`no_system_vs_full_headline`만 이 둘을 직접 대조하고, `stage_contribution`은
capture를 공유하는 세 arm 사이에서만 계산한다(no_system과 no_grounding/
no_static을 stage_contribution으로 직접 비교하지 않는다 — LLM 출력 자체가
다른 프롬프트에서 나와 공정한 대조가 아니다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e3_evaluation import (  # noqa: E402
    E3Result,
    load_cases,
    load_results,
    load_tier_b_case_ids,
    no_system_vs_full_headline,
    per_arm_summary,
    stage_contribution,
    tier_b_headline,
)

_TIER_A_SCOPE = (
    "Tier A: GOLD-350 전 케이스, twin 없이(parse -> grounding -> compile -> "
    "static_validation). no_grounding/no_static/full 세 arm은 capture-ir "
    "로그의 attempt-0 파싱을 공유한다(LLM 비결정성이 arm 비교를 오염시키지 "
    "않도록) — repair로 인한 attempt 1+ 재파싱만 arm마다 독립적으로 갈라진다. "
    "no_system은 direct_flow 프롬프트를 쓰는 완전히 별도 경로라 capture를 "
    "공유하지 않는다."
)
_TIER_B_SCOPE = (
    "Tier B: 실 Digital Twin(Mininet+ONOS) 배포 검증. twin-호환 5개 카테고리"
    "(forwarding/security/qos/reroute/compound)에서 h2<->h3 주 대상 케이스를 "
    "제외하고 카테고리당 10개, 50케이스 고정 표본(tierB_case_ids.json) — "
    "SFC 카테고리는 존재하지 않는 포트(of:...0001:9)를 targets하므로 전부 "
    "제외한다. no_system/full만 비교한다(no_grounding/no_static은 정적 "
    "레벨 진단만으로 이미 기여도가 드러나 twin까지 반복하지 않음)."
)


def _load_arm_results(paths: list[Path]) -> dict[str, list[E3Result]]:
    by_arm: dict[str, list[E3Result]] = {}
    for path in paths:
        results = load_results(path)
        if not results:
            continue
        arms = {r.arm for r in results}
        if len(arms) > 1:
            raise ValueError(f"{path}: mixes arms {sorted(arms)}, expected one file per arm")
        arm = next(iter(arms))
        if arm in by_arm:
            raise ValueError(f"arm {arm!r} appears in more than one input file")
        by_arm[arm] = results
    return by_arm


def score_tier_a(cases, by_arm: dict[str, list[E3Result]]) -> dict:
    report: dict = {"scope": _TIER_A_SCOPE, "arms": {}}

    for arm, results in sorted(by_arm.items()):
        report["arms"][arm] = per_arm_summary(cases, results)

    if "no_grounding" in by_arm and "full" in by_arm:
        report["stage_contribution"] = report.get("stage_contribution", {})
        report["stage_contribution"]["grounding"] = stage_contribution(
            by_arm["no_grounding"], by_arm["full"], diag_field="grounding_findings",
        )
    if "no_static" in by_arm and "full" in by_arm:
        report["stage_contribution"] = report.get("stage_contribution", {})
        report["stage_contribution"]["static_validation"] = stage_contribution(
            by_arm["no_static"], by_arm["full"], diag_field="static_conflicts",
        )
    if "no_system" in by_arm and "full" in by_arm:
        report["no_system_vs_full"] = no_system_vs_full_headline(cases, by_arm["no_system"], by_arm["full"])

    return report


def score_tier_b(by_arm: dict[str, list[E3Result]]) -> dict:
    unexpected = set(by_arm) - {"no_system", "full"}
    if unexpected:
        raise ValueError(f"Tier B only scores no_system/full, got: {sorted(unexpected)}")

    report: dict = {"scope": _TIER_B_SCOPE, "arms": {arm: len(results) for arm, results in by_arm.items()}}
    if "no_system" in by_arm and "full" in by_arm:
        report["headline"] = tier_b_headline(by_arm["no_system"], by_arm["full"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=["A", "B"], required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/gold/gold350_eval.jsonl")
    parser.add_argument("--case-id-file", type=Path, help="Tier B 필수 — tierB_case_ids.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("files", nargs="+", type=Path, help="arm별 결과 JSONL (파일당 arm 1개)")
    args = parser.parse_args()

    if args.tier == "B" and args.case_id_file is None:
        parser.error("--tier B requires --case-id-file")

    cases = load_cases(args.dataset)
    if args.tier == "B":
        wanted = set(load_tier_b_case_ids(args.case_id_file))
        cases = [c for c in cases if c.case_id in wanted]

    by_arm = _load_arm_results(args.files)
    report = score_tier_a(cases, by_arm) if args.tier == "A" else score_tier_b(by_arm)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
