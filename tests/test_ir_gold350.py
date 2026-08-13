"""tests/test_ir_gold350.py — Stage 4 완료 기준: GOLD-350 accepted 300건 전량 로드, 실패 0.

원본: 없음 (신규). docs/plan.md Stage 4 참고.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiger_sdn.ir import AdapterError, IntentPrediction, from_research, to_research

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold" / "gold.jsonl"


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_all_accepted_cases_load_without_failure():
    cases = _load_cases()
    accepted = [c for c in cases if c["expected"]["status"] == "accepted"]
    assert len(accepted) == 300

    failures = []
    for case in accepted:
        try:
            from_research(case["expected"])
        except (AdapterError, ValueError) as exc:
            failures.append((case["id"], repr(exc)))

    assert failures == []


def test_intent_prediction_pydantic_json_round_trip_is_exact():
    """orchestrate.pipeline의 capture-ir/replay 설계(Exp-3, 이슈 #37)가
    IntentPrediction을 ``model_dump(mode="json")``로 로그에 저장했다가
    ``model_validate()``로 다시 읽어 ``initial_prediction``에 넘기는 걸
    전제한다 — 이건 to_research() 어댑터 왕복(위 테스트)과 다른 층이라
    (pydantic 자체의 JSON 직렬화 충실도), GOLD-350 accepted 전 케이스로
    routing.waypoints/qos 같은 중첩 optional 필드까지 별도로 확인한다.
    """
    cases = _load_cases()
    accepted = [c for c in cases if c["expected"]["status"] == "accepted"]

    for case in accepted:
        prediction = from_research(case["expected"])
        dumped = prediction.model_dump(mode="json")
        restored = IntentPrediction.model_validate(dumped)
        assert restored == prediction, case["id"]
        assert restored.model_dump(mode="json") == dumped, case["id"]


def test_all_rejected_cases_load_without_failure():
    cases = _load_cases()
    rejected = [c for c in cases if c["expected"]["status"] == "rejected"]
    assert len(rejected) == 50

    failures = []
    for case in rejected:
        try:
            from_research(case["expected"])
        except (AdapterError, ValueError) as exc:
            failures.append((case["id"], repr(exc)))

    assert failures == []


def _drop_none(value):
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def _normalize(value):
    """adapter.py가 문서화한 두 정규화(egress_port str->int, ip에 /32 부착)를
    양쪽에 동일하게 적용해 원본과 왕복 결과를 직접 비교할 수 있게 한다."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "egress_port" and v is not None:
                out[k] = int(v)
            elif k == "ip" and isinstance(v, str) and "/" not in v:
                out[k] = v + "/32"
            else:
                out[k] = _normalize(v)
        return out
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def test_research_round_trip_is_lossless_up_to_documented_normalizations():
    """to_research(from_research(x)) == x, adapter.py 서두 주석의 두 정규화 제외.

    egress_port 문자열->정수, ip에 마스크 없으면 /32 부착 — 값 보존, 표기만 정규화.
    """
    cases = _load_cases()
    accepted = [c for c in cases if c["expected"]["status"] == "accepted"]

    for case in accepted:
        original = _normalize(_drop_none(case["expected"]))
        round_tripped = _normalize(_drop_none(to_research(from_research(case["expected"]))))
        assert round_tripped == original, case["id"]


def test_from_research_rejects_unknown_action_intent_type_combo():
    with pytest.raises(AdapterError):
        from_research(
            {
                "status": "accepted",
                "program": {
                    "rules": [
                        {
                            "intent_type": "qos",
                            "action": "forward",
                            "selector": {},
                        }
                    ]
                },
            }
        )
