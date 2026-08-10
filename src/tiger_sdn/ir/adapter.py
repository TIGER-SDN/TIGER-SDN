"""src/tiger_sdn/ir/adapter.py — 외부 스키마 ↔ 통합 IR 변환기.

원본: sdn-intent-framework의 feat/unify-ir 브랜치, src/sdn_intent/ir/adapter.py
(커밋된 적 없는 로컬 상태 — docs/plan.md "확인된 사실 1" 참고. 패키지명만
sdn_intent -> tiger_sdn으로 바꾸고 내용은 그대로 옮겼다.)

두 종류의 기존 데이터를 통합 IR로 읽어들이기 위한 어댑터다.

  1. GOLD-350 (제품 스키마)     — `from_gold350()`
  2. 연구 트랙 스키마            — `from_research()` / `to_research()`

연구 스키마와의 왕복 변환은 다음 정규화를 제외하면 무손실이다.

  * `enforcement.egress_port` 가 문자열이면 정수로 변환한다. 연구 데이터셋에는
    `"2"` 같은 숫자 문자열이 78건 섞여 있는데(표기 불일치), 값 자체는 보존되므로
    손실이 아니라 정규화다. `to_research()` 는 정수로 되돌린다.
  * `selector.source.ip` 에 마스크가 없으면 `/32` 를 붙인다.

── 필드 대응표 ──────────────────────────────────────────────────────────
  연구                                통합 (= 제품)
  selector.source_port          →     selector.src_port
  selector.destination_port     →     selector.dst_port
  selector.ingress_port         →     selector.in_port
  enforcement.avoid_device      →     routing.avoid_device
  program.sfc_chain             →     rules[].routing.waypoints
  rejection.reason              →     rejection.reason  (동일)

── action 어휘 대응 ─────────────────────────────────────────────────────
  연구는 (intent_type, action) 두 축을 쓰고 통합 IR도 같은 두 축을 쓰지만
  action 어휘가 다르다.

  (forwarding, forward)   ↔  (forwarding, forward)
  (security,   allow)     ↔  (security,   forward)     ← 화이트리스트
  (security,   deny)      ↔  (security,   block)       ← 블랙리스트
  (qos,        prioritize)↔  (qos,        qos)
  (sfc,        forward)   ↔  (sfc,        sfc)
  (reroute,    forward)   ↔  (reroute,    reroute)
"""

from __future__ import annotations

from typing import Any, Optional

from tiger_sdn.ir.model import IntentProgram, IntentRule
from tiger_sdn.ir.prediction import IntentPrediction

__all__ = ["from_gold350", "from_research", "to_research", "AdapterError"]


class AdapterError(ValueError):
    """변환할 수 없는 입력을 만났을 때 발생한다.

    조용히 넘기지 않는다 — 변환 실패는 데이터 문제이거나 대응표 누락이므로
    드러나야 한다.
    """


# (intent_type, 연구 action) → 통합 action
_RESEARCH_TO_UNIFIED_ACTION: dict[tuple[str, str], str] = {
    ("forwarding", "forward"): "forward",
    ("security", "allow"): "forward",
    ("security", "deny"): "block",
    ("qos", "prioritize"): "qos",
    ("sfc", "forward"): "sfc",
    ("reroute", "forward"): "reroute",
}

# 역방향 — (intent_type, 통합 action) → 연구 action
_UNIFIED_TO_RESEARCH_ACTION: dict[tuple[str, str], str] = {
    ("forwarding", "forward"): "forward",
    ("security", "forward"): "allow",
    ("security", "block"): "deny",
    ("qos", "qos"): "prioritize",
    ("sfc", "sfc"): "forward",
    ("reroute", "reroute"): "forward",
}


def _drop_none(value: Any) -> Any:
    """None 값을 재귀적으로 제거한다.

    두 데이터셋 모두 미지정 필드를 명시적 null 로 적어두는데, 통합 IR 은
    `extra="forbid"` 이면서 Optional 이므로 키 자체를 빼는 편이 안전하다.
    """
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def _coerce_port(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise AdapterError(f"포트 값이 불리언입니다: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise AdapterError(f"포트로 변환할 수 없는 값: {value!r}") from exc


# ════════════════════════════════════════════════════════════════════════
# GOLD-350 (제품 스키마)
# ════════════════════════════════════════════════════════════════════════


def from_gold350(gold: dict) -> IntentPrediction:
    """GOLD-350 레코드의 `gold` 필드를 통합 IR 예측으로 변환한다.

    수락 케이스는 두 가지 형태가 섞여 있다.
      * 복합:  {"status": "accepted", "rules": [...]}
      * 단일:  {"status": "accepted", "action": ..., "selector": {...}, ...}
    """
    status = gold.get("status")
    if status == "rejected":
        reason = gold.get("rejection_reason")
        if reason is None:
            raise AdapterError("rejected gold 레코드에 rejection_reason이 없습니다")
        return IntentPrediction.reject(reason, gold.get("rejection_detail"))

    if status != "accepted":
        raise AdapterError(f"알 수 없는 gold status: {status!r}")

    if "rules" in gold:
        raw_rules = gold["rules"]
    else:
        raw_rules = [{k: v for k, v in gold.items() if k != "status"}]

    rules = [IntentRule(**_drop_none(r)) for r in raw_rules]
    return IntentPrediction.accept(IntentProgram(rules=rules))


# ════════════════════════════════════════════════════════════════════════
# 연구 트랙 스키마
# ════════════════════════════════════════════════════════════════════════


def _research_rule_to_unified(
    raw: dict, sfc_chain: Optional[list[str]]
) -> IntentRule:
    raw = _drop_none(raw)
    intent_type = raw.get("intent_type")
    action = raw.get("action")
    key = (intent_type, action)
    if key not in _RESEARCH_TO_UNIFIED_ACTION:
        raise AdapterError(f"대응표에 없는 (intent_type, action) 조합: {key}")

    selector = dict(raw.get("selector") or {})
    # 포트 필드명 정렬
    for research_name, unified_name in (
        ("source_port", "src_port"),
        ("destination_port", "dst_port"),
        ("ingress_port", "in_port"),
    ):
        if research_name in selector:
            selector[unified_name] = _coerce_port(selector.pop(research_name))

    enforcement = dict(raw.get("enforcement") or {})
    # avoid_device 는 통합 IR 에서 routing 으로 옮겨간다
    avoid_device = enforcement.pop("avoid_device", None)
    if "egress_port" in enforcement:
        enforcement["egress_port"] = _coerce_port(enforcement["egress_port"])

    routing: dict[str, Any] = {}
    if avoid_device is not None:
        routing["avoid_device"] = avoid_device
    if intent_type == "sfc":
        if not sfc_chain:
            raise AdapterError("sfc 규칙인데 program.sfc_chain이 없습니다")
        routing["waypoints"] = list(sfc_chain)

    return IntentRule(
        action=_RESEARCH_TO_UNIFIED_ACTION[key],
        intent_type=intent_type,
        selector=selector,
        enforcement=enforcement or None,
        qos=raw.get("qos") or None,
        routing=routing or None,
        sfc_role=raw.get("sfc_role"),
    )


def from_research(expected: dict) -> IntentPrediction:
    """연구 트랙의 `expected` 블록을 통합 IR 예측으로 변환한다."""
    status = expected.get("status")
    if status == "rejected":
        rejection = expected.get("rejection") or {}
        reason = rejection.get("reason")
        if reason is None:
            raise AdapterError("rejected 레코드에 rejection.reason이 없습니다")
        return IntentPrediction.reject(reason, rejection.get("detail"))

    if status != "accepted":
        raise AdapterError(f"알 수 없는 status: {status!r}")

    program = expected.get("program") or {}
    raw_rules = program.get("rules") or []
    if not raw_rules:
        raise AdapterError("accepted 레코드에 규칙이 없습니다")

    sfc_chain = program.get("sfc_chain")
    rules = [_research_rule_to_unified(r, sfc_chain) for r in raw_rules]
    return IntentPrediction.accept(IntentProgram(rules=rules))


def _unified_rule_to_research(rule: IntentRule) -> dict:
    itype = rule.resolved_intent_type
    key = (itype, rule.action)
    if key not in _UNIFIED_TO_RESEARCH_ACTION:
        raise AdapterError(f"역방향 대응표에 없는 조합: {key}")

    selector = rule.selector.model_dump(exclude_none=True)
    for unified_name, research_name in (
        ("src_port", "source_port"),
        ("dst_port", "destination_port"),
        ("in_port", "ingress_port"),
    ):
        if unified_name in selector:
            selector[research_name] = selector.pop(unified_name)

    enforcement = (
        rule.enforcement.model_dump(exclude_none=True) if rule.enforcement else {}
    )
    if rule.routing is not None and rule.routing.avoid_device is not None:
        enforcement["avoid_device"] = rule.routing.avoid_device

    out: dict[str, Any] = {
        "intent_type": itype,
        "action": _UNIFIED_TO_RESEARCH_ACTION[key],
        "selector": selector,
    }
    if rule.qos is not None:
        out["qos"] = rule.qos.model_dump(exclude_none=True)
    if enforcement:
        out["enforcement"] = enforcement
    if rule.sfc_role is not None:
        out["sfc_role"] = rule.sfc_role
    return out


def to_research(prediction: IntentPrediction) -> dict:
    """통합 IR 예측을 연구 트랙 스키마로 되돌린다."""
    if prediction.status == "rejected":
        assert prediction.rejection is not None  # 모델 검증기가 보장
        rejection: dict[str, Any] = {"reason": prediction.rejection.reason}
        if prediction.rejection.detail is not None:
            rejection["detail"] = prediction.rejection.detail
        return {"status": "rejected", "rejection": rejection}

    assert prediction.program is not None  # 모델 검증기가 보장
    rules = [_unified_rule_to_research(r) for r in prediction.program.rules]

    program: dict[str, Any] = {"rules": rules}
    # sfc_chain 은 규칙에 흩어진 waypoints 에서 되살린다 (모두 동일해야 한다)
    chains = {
        tuple(r.routing.waypoints)
        for r in prediction.program.rules
        if r.resolved_intent_type == "sfc" and r.routing and r.routing.waypoints
    }
    if len(chains) > 1:
        raise AdapterError(f"규칙마다 sfc 체인이 다릅니다: {chains}")
    if chains:
        program["sfc_chain"] = list(next(iter(chains)))

    return {"status": "accepted", "program": program}
