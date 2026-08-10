"""scripts/twin_smoke.py — live Digital Twin smoke test against a real ONOS + Mininet.

원본: 없음 (신규, docs/plan.md "실검증 도구" 참고). `sdn-intent-framework`의
`research/scripts/e3_twin_smoke.sh`가 같은 역할(twin_verifier를 실 컨트롤러에
대고 exercise)을 하지만 아직 이식하지 않은 experiments/e3의 E3 하네스에
의존한다 — 그 하네스 없이도 돌아가도록, Stage 5 컴파일러로 그 자리에서 만든
작은 FlowRule 2개(forward, block)를 Stage 7 TwinVerifier에 직접 넘긴다.

tests/test_twin.py는 이 코드의 순수 로직(대역폭 판정, intent_spec 추출, 플랫폼
게이팅)만 검증한다 — TwinVerifier.verify()의 본 경로(Mininet 기동, ONOS 배포,
reachability 프로브, rollback)가 실제로 동작하는지는 이 스크립트가 처음
확인한다. scripts/onos.sh start로 ONOS가 떠 있어야 하고, Linux + root +
Mininet이 필요하다 — 아니면 verify()가 status="skipped"를 반환하고 이 스크립트는
실패로 취급한다(스킵은 "확인했다"가 아니므로).
"""

from __future__ import annotations

from tiger_sdn.compile import compile_prediction
from tiger_sdn.ir import from_research
from tiger_sdn.twin import REACH_ONLY, TwinVerifier

ENDPOINT_IPS = {"h1": "10.0.0.1", "h2": "10.0.0.2", "h3": "10.0.0.3", "h4": "10.0.0.4"}


def _compile(rules: list[dict]):
    prediction = from_research({"status": "accepted", "program": {"rules": rules}})
    return compile_prediction(prediction, endpoint_ips=ENDPOINT_IPS)


# Both cases target h1<->h4. The default diamond topology's regression check
# (twin.topology.get_test_host_pairs) hardcodes h2<->h3 as the "unrelated"
# pair that must stay reachable regardless of what's under test -- a case
# that targets h2/h3 itself makes that check spuriously fail (observed live:
# a block h2->h3 case correctly blocked traffic, intent_check=True, but
# regression=False because the framework expected h2<->h3 to still be
# reachable). Stick to h1<->h4 here to stay clear of that reserved pair.
CASES = {
    "forward h1->h4": _compile(
        [
            {
                "intent_type": "forwarding",
                "action": "forward",
                "selector": {"source": {"host": "h1"}, "destination": {"host": "h4"}},
            }
        ]
    ),
    "block h1->h4": _compile(
        [
            {
                "intent_type": "security",
                "action": "deny",
                "selector": {"source": {"host": "h1"}, "destination": {"host": "h4"}},
            }
        ]
    ),
}


def main() -> int:
    verifier = TwinVerifier()
    failed = False
    for label, flow_set in CASES.items():
        print(f"[twin-smoke] verifying: {label}")
        result = verifier.verify(flow_set, checks=REACH_ONLY)
        print(f"[twin-smoke]   {result.summary()} checks={result.checks}")
        if result.status == "skipped":
            print("[twin-smoke]   SKIPPED -- platform requirements not met (Linux+root+Mininet)")
            failed = True
        elif result.status != "passed":
            failed = True
    if failed:
        print("[twin-smoke] FAILED -- see per-case output above")
    else:
        print("[twin-smoke] PASS: both cases verified against the live twin")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
