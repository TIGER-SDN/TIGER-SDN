"""
experiments/exp1/convert_gold350.py — GOLD-350 -> Exp-1 gold 스키마 변환기

원본: sdn-intent-framework의 research/experiments/eval/convert_gold350.py

data/gold/gold.jsonl (팀원이 작성한 350케이스, 이중 라벨링+조정 완료된 gold)을
score.py가 소비하는 data/gold/gold350_eval.jsonl 형식으로 변환한다.

두 데이터셋의 스키마 차이 (분석 근거):
  - action 값 체계가 다르다: GOLD-350은 sdn_intent-framework 계열의 3그룹
    (forward/deny/prioritize/allow) + 별도 intent_type(7종 카테고리)로 분리되어 있는 반면,
    파이프라인은 action 자체가 5종(forward/block/qos/sfc/reroute)이다.
    → intent_type 기준으로 pipeline action을 재매핑한다 (action 원문이 아니라 intent_type이
      pipeline의 action과 1:1 대응).
  - selector 필드명: source_port/destination_port/ingress_port → src_port 계열이 아니라
    애초에 Exp-1 채점 스키마(SLOT_NAMES)에는 dst_port만 존재 — source_port/ingress_port는
    채점 대상이 아니므로 변환 시 버린다.
  - selector.{source,destination}.ip가 GOLD-350은 host만 채워져 있고 ip가 null인 경우가
    많다 → ANNOTATION_GUIDELINE.md §1의 고정 인벤토리(h1=10.0.0.1 …)로 역채움한다.
  - enforcement.egress_port가 GOLD-350은 문자열("9")이다 → score.py의 _compare_slot이
    int() 캐스팅 후 비교하므로 실제로는 변환 없이도 채점 가능하지만, 기존 intents_eval.jsonl과
    형식을 맞추기 위해 정수로 캐스팅한다.
  - device 값이 GOLD-350은 "of:0000000000000001" 형식이다 → score.py의 AliasMap이
    이 형식도 별칭으로 이미 알고 있으므로(topology_eval.json entities) 별도 변환 불필요.
  - SFC: GOLD-350은 LLM이 아니라 "이미 컴파일된 것처럼" ingress/transit/egress로 미리
    쪼개서 표현한다(각 rule에 sfc_role 태그). 파이프라인의 Stage1 IR은 action="sfc" 단일
    rule + routing.waypoints로 표현하고 Stage2 컴파일러가 나중에 쪼갠다 — 즉 GOLD-350의
    sfc gold는 파이프라인이 기대하는 IR 추상화 수준과 다르다. 이 스크립트가 ingress/transit/
    egress rule들을 다시 하나의 action="sfc" IR로 합친다(program.sfc_chain을 그대로
    routing.waypoints로 사용 — 이미 "sX:port" 정규화된 형태로 제공됨).
    **주의**: sfc_chain 길이가 2 이상인 multi_switch_chain 케이스(10개)는 파이프라인
    Stage2 컴파일러(stage2_flowrule/compiler.py, 아직 미이식)가 waypoints[0]만 사용해
    단일 홉만 컴파일한다 — 즉 IR 골드로는 정확하지만 Stage1-only 평가(Exp-1)에서만
    안전하게 쓸 수 있고, 전체 파이프라인 실행(Exp-2/3)에는 아직 그대로 못 쓴다. 이
    스크립트는 경고만 출력하고 변환은 그대로 진행한다(Exp-1 채점 목적에는 문제없음).
  - reroute: GOLD-350은 via_device/avoid_device 개념이 아예 없다 — 그냥 forward rule의
    enforcement.device/egress_port를 바꾸는 것으로 경로 변경을 표현한다. 기존
    intents_eval.jsonl 컨벤션(enforcement는 비우고 routing.via_device만 채움)과 다르다.
    score.py는 gold 슬롯이 null이면 채점에서 제외하므로 안전하게 동작하지만,
    via_device/avoid_device 슬롯은 이 데이터셋의 reroute 케이스에서는 항상 채점 제외된다
    (정보 자체가 gold에 없음). enforcement.device/egress_port 슬롯은 대신 채점된다.

Usage:
    python experiments/exp1/convert_gold350.py \
        --input data/gold/gold.jsonl \
        --output data/gold/gold350_eval.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ANNOTATION_GUIDELINE.md §1 고정 인벤토리
HOST_IP: dict[str, str] = {
    "h1": "10.0.0.1",
    "h2": "10.0.0.2",
    "h3": "10.0.0.3",
    "h4": "10.0.0.4",
}
IP_HOST: dict[str, str] = {v: k for k, v in HOST_IP.items()}

# GOLD-350 category(=intent_type 대응) -> pipeline action 매핑
# security는 action 원문(deny/allow)에 따라 갈라지므로 별도 처리
_INTENT_TYPE_TO_ACTION: dict[str, str] = {
    "forwarding": "forward",
    "qos": "qos",
    "sfc": "sfc",
    "reroute": "reroute",
}


class ConversionWarning(Exception):
    pass


def _resolve_endpoint(ep: dict | None) -> dict | None:
    """{'host':..,'ip':..} 중 하나만 있으면 다른 하나를 인벤토리로 역채움."""
    if ep is None:
        return None
    host = ep.get("host")
    ip = ep.get("ip")
    if ip is None and host in HOST_IP:
        ip = HOST_IP[host]
    if host is None and ip is not None:
        ip_only = ip.split("/")[0]
        host = IP_HOST.get(ip_only)  # 인벤토리 밖 IP면 None 유지 (unknown_entity 케이스)
    return {"host": host, "ip": ip}


def _map_action(intent_type: str, raw_action: str | None) -> str:
    if intent_type == "security":
        # deny -> block, allow -> forward (explicit allow-rule도 결국 permit/OUTPUT)
        return "block" if raw_action == "deny" else "forward"
    mapped = _INTENT_TYPE_TO_ACTION.get(intent_type)
    if mapped is None:
        raise ConversionWarning(f"알 수 없는 intent_type: {intent_type!r}")
    return mapped


def _convert_enforcement(enf: dict | None) -> dict:
    if enf is None:
        return {"device": None, "egress_port": None}
    egress = enf.get("egress_port")
    if egress is not None:
        try:
            egress = int(egress)
        except (TypeError, ValueError):
            pass  # 캐스팅 실패 시 원본 유지 (score.py가 문자열 비교로 폴백)
    return {"device": enf.get("device"), "egress_port": egress}


def _convert_single_rule(rule: dict, intent_type_hint: str | None = None) -> dict:
    """GOLD-350 rule(dict) -> Exp-1 gold rule(dict). sfc_role이 있는 rule은 호출 전 병합 필요."""
    intent_type = rule.get("intent_type") or intent_type_hint
    action = _map_action(intent_type, rule.get("action"))

    sel = rule.get("selector") or {}
    converted = {
        "action": action,
        "intent_type": intent_type,
        "selector": {
            "source": _resolve_endpoint(sel.get("source")),
            "destination": _resolve_endpoint(sel.get("destination")),
            "protocol": sel.get("protocol"),
            "dst_port": sel.get("destination_port"),
        },
        "enforcement": _convert_enforcement(rule.get("enforcement")),
        "qos": rule.get("qos"),
        "routing": None,
    }
    return converted


def _convert_sfc_rules(rules: list[dict], sfc_chain: list[str] | None) -> dict:
    """sfc_role(ingress/transit/egress)로 쪼개진 rule들을 단일 action=sfc IR로 병합."""
    by_role = {r.get("sfc_role"): r for r in rules}
    ingress = by_role.get("ingress")
    egress = by_role.get("egress")
    if ingress is None or egress is None:
        raise ConversionWarning(f"sfc rule에 ingress/egress role이 없음: roles={list(by_role)}")

    base = _convert_single_rule(ingress, intent_type_hint="sfc")
    base["action"] = "sfc"
    base["enforcement"]["alt_egress_port"] = _convert_enforcement(egress.get("enforcement")).get("egress_port")
    base["routing"] = {
        "waypoints": sfc_chain,
        "via_device": None,
        "avoid_device": None,
    }
    return base


def convert_case(case: dict, warnings: list[str]) -> dict:
    case_id = case["id"]
    category = case["category"]
    instruction = case["instruction"]
    exp = case["expected"]

    out: dict = {"case_id": case_id, "category": category, "intent_text": instruction}

    if exp["status"] == "rejected":
        reason = exp["rejection"]["reason"]
        out["rejection_type"] = reason
        out["gold"] = {"status": "rejected", "rejection_reason": reason}
        return out

    program = exp["program"]
    rules = program["rules"]

    if category == "sfc":
        try:
            gold_rule = _convert_sfc_rules(rules, program.get("sfc_chain"))
        except ConversionWarning as exc:
            warnings.append(f"{case_id}: {exc}")
            gold_rule = _convert_single_rule(rules[0])
        if program.get("sfc_chain") and len(program["sfc_chain"]) > 1:
            warnings.append(
                f"{case_id}: multi-hop sfc_chain(len={len(program['sfc_chain'])}) — "
                "stage2_flowrule/compiler.py는 waypoints[0]만 컴파일함 (Exp-1 IR 채점은 안전, "
                "Exp-2/3 파이프라인 실행은 아직 미지원)"
            )
        out["gold"] = {"status": "accepted", **gold_rule}
        return out

    if len(rules) == 1:
        gold_rule = _convert_single_rule(rules[0])
        out["gold"] = {"status": "accepted", **gold_rule}
        return out

    # compound (또는 sfc가 아닌데 다중 rule인 경우 — 방어적으로 compound 취급)
    converted_rules = [_convert_single_rule(r) for r in rules]
    out["gold"] = {"status": "accepted", "rules": converted_rules}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="GOLD-350 -> Exp-1 gold 스키마 변환기")
    parser.add_argument("--input", default="data/gold/gold.jsonl")
    parser.add_argument("--output", default="data/gold/gold350_eval.jsonl")
    args = parser.parse_args()

    in_path = ROOT / args.input
    out_path = ROOT / args.output

    cases = [json.loads(l) for l in in_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    warnings: list[str] = []
    converted = []
    errors: list[str] = []
    for case in cases:
        try:
            converted.append(convert_case(case, warnings))
        except Exception as exc:
            errors.append(f"{case.get('id', '?')}: {exc}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in converted:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"변환 완료: {len(converted)}/{len(cases)} 케이스 -> {out_path}")
    if warnings:
        print(f"\n경고 {len(warnings)}건:")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print(f"\n오류 {len(errors)}건 (변환 실패, 출력에서 제외됨):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
