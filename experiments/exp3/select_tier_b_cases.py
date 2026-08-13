"""experiments/exp3/select_tier_b_cases.py — Tier B(실 Digital Twin) 고정 표본 선정.

원본: 없음 (신규, 이슈 #37 / docs/plan.md Exp-3 참고). 1회 실행 스크립트 —
출력(`data/tierB_case_ids.json`)을 커밋해 모든 모델·No-System/Full 두 arm이
동일 케이스 집합으로 비교되게 한다.

두 카테고리/케이스 제외를 코드로 직접 확인한 사실에 근거해 적용한다:

1. **SFC 카테고리 전부 제외.** 골드 SFC 규칙은 `of:0000000000000001:9`
   (방화벽 포트)로 컴파일되는데, 실 Mininet 다이아몬드 토폴로지
   (`twin/topology.py::build_network()`)는 s1에 포트 1~4만 배선돼 있다 —
   포트 9는 물리적으로 없다. `twin_verifier.py`의 `sfc_chain` 스킵 가드는
   `OnosFlowSet`에 그 필드 자체가 없어 죽은 코드라, SFC 케이스를 그대로
   twin에 태우면 존재하지 않는 포트에 실배포를 시도하게 된다.
2. **h2<->h3가 주 대상인 케이스 제외.** `twin/topology.py::get_test_host_pairs()`
   가 `(h2, h3)`를 "다른 케이스 검증 중에도 항상 도달 가능해야 하는" 회귀
   쌍으로 하드코딩해서, 실제로 h2<->h3 트래픽을 다루는 케이스는 twin이
   의도대로 동작해도(정상 차단 등) 회귀 체크와 충돌해 거짓 실패로 잡힌다
   (`scripts/twin_smoke.py` 주석에 실측 사례 기록됨).

`ambiguous_unsupported`(거부 케이스, 배포할 FlowRule 자체가 없음)도 자연히
제외된다 — 이 스크립트는 `gold.status == "accepted"`인 케이스만 본다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e3_evaluation import E3Case, load_cases  # noqa: E402

TWIN_COMPATIBLE_CATEGORIES = ["forwarding", "security", "qos", "reroute", "compound"]


def _rule_hosts(rule: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    selector = rule.get("selector") or {}
    for side in ("source", "destination"):
        host = (selector.get(side) or {}).get("host")
        if host:
            hosts.add(host)
    return hosts


def _is_h2h3_only(case: E3Case) -> bool:
    """gold.rules(복합) 또는 gold 자체(단일)의 모든 규칙이 h2/h3만 참조하면 True."""
    rules = case.gold.get("rules") or [case.gold]
    hosts: set[str] = set()
    for rule in rules:
        hosts |= _rule_hosts(rule)
    return bool(hosts) and hosts.issubset({"h2", "h3"})


def select_tier_b_cases(
    cases: list[E3Case], *, seed: int, per_category: int,
) -> dict[str, list[str]]:
    by_category: dict[str, list[str]] = {}
    for category in TWIN_COMPATIBLE_CATEGORIES:
        pool = [
            c.case_id for c in cases
            if c.category == category and c.gold.get("status") == "accepted" and not _is_h2h3_only(c)
        ]
        pool.sort()  # 재현성 — random.sample은 입력 순서에 의존하므로 정렬 후 고정 시드로 뽑는다.
        if len(pool) < per_category:
            raise ValueError(f"{category}: eligible pool ({len(pool)}) smaller than per_category ({per_category})")
        rng = random.Random(seed)
        by_category[category] = sorted(rng.sample(pool, per_category))
    return by_category


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/gold/gold350_eval.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--per-category", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "data" / "tierB_case_ids.json")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    by_category = select_tier_b_cases(cases, seed=args.seed, per_category=args.per_category)
    case_ids = sorted(cid for ids in by_category.values() for cid in ids)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "seed": args.seed, "per_category": args.per_category,
                "categories": TWIN_COMPATIBLE_CATEGORIES,
                "by_category": by_category, "case_ids": case_ids,
            },
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(case_ids)} case_ids ({args.per_category}/category x {len(by_category)} categories) to {args.output}")


if __name__ == "__main__":
    main()
