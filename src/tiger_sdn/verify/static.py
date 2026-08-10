"""src/tiger_sdn/verify/static.py — 정적 검증 통합 모듈.

원본: sdn-intent-framework의
src/xai_pipeline/pipeline/stage3_static/static_validator.py. 스키마 검증 +
충돌 탐지(외부 + compound 내부)를 실행하고 StaticResult를 반환한다.

── 이식 중 고친 버그: compound 내부 충돌 검사가 CIDR subset/overlap을 안 봄 ──
원본 `_check_intra_conflicts`는 IPV4_SRC/IPV4_DST를 포함한 key_types 필드
전체를 `c1[t] == c2[t]` 딕셔너리 완전 일치로 비교했다. 외부 충돌 탐지
(`conflict.criteria_overlap`)는 같은 필드를 `ip_overlaps`로 CIDR 겹침까지
보는데, compound 내부 검사만 다른(더 약한) 함수를 썼다 — 그래서
`10.0.0.0/24`와 `10.0.0.5/32`처럼 문자열은 다르지만 실제로 겹치는 IP 매치는
내부 충돌로 잡히지 않았다. 여기서는 IPV4_SRC/IPV4_DST 비교를
`conflict.ip_overlaps`로 통일했다.

── 이식 중 고친 버그: block 규칙과의 내부 충돌 검사가 크래시함 ──────────
원본은 `f.get("treatment", {}).get("instructions", [])`로 action을 읽었는데,
`treatment` 키가 아예 없을 때만 방어되고 block 규칙처럼 키는 있지만 값이
명시적으로 `None`인 경우는 방어되지 않아 `AttributeError`가 났다. 정확히
forward-vs-block 상반 액션을 잡으려는 검사가 block 규칙에서 죽는 셈이라
`(f.get("treatment") or {})`로 고쳤다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .conflict import _field_value, detect_conflict, ip_overlaps
from .schema import validate_schema

__all__ = ["StaticResult", "validate"]


@dataclass
class StaticResult:
    """정적 검증 결과"""

    passed: bool
    schema_errors: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """운영자용 한 줄 요약"""
        parts = []

        if self.schema_errors:
            parts.append(f"스키마 오류 {len(self.schema_errors)}개")
        else:
            parts.append("Schema OK")

        if self.conflicts:
            types = [c.get("conflict_type", "?") for c in self.conflicts]
            parts.append(f"충돌 {len(self.conflicts)}개 ({', '.join(types)})")
        else:
            parts.append("충돌 없음")

        if self.warnings:
            parts.append(f"경고 {len(self.warnings)}개")

        result_str = "PASS" if self.passed else "FAIL"
        return f"{' | '.join(parts)} → {result_str}"


def _criteria_field_overlaps(c1: dict, c2: dict, ctype: str) -> bool:
    """key_types 필드 하나가 두 규칙 사이에서 겹치는지 확인한다.
    IPV4_SRC/IPV4_DST는 CIDR 겹침으로, 그 외는 정확한 값 일치로 본다."""
    if ctype in ("IPV4_SRC", "IPV4_DST"):
        return ip_overlaps(_field_value(c1[ctype], ctype), _field_value(c2[ctype], ctype))
    return _field_value(c1[ctype], ctype) == _field_value(c2[ctype], ctype)


def _check_intra_conflicts(flows: list[dict]) -> list[dict]:
    """
    복합 인텐트에서 생성된 룰들 간의 내부 충돌을 검사한다.

    주요 체크:
    - Shadowing: 높은 priority의 catch-all 룰이 낮은 priority 룰을 가림
    - 같은 (device, criteria)에 서로 다른 action (forward vs block)
    """
    conflicts = []
    for i in range(len(flows)):
        for j in range(i + 1, len(flows)):
            f1, f2 = flows[i], flows[j]
            if f1.get("deviceId") != f2.get("deviceId"):
                continue  # 다른 스위치는 충돌 없음

            c1 = {c["type"]: c for c in f1.get("selector", {}).get("criteria", [])}
            c2 = {c["type"]: c for c in f2.get("selector", {}).get("criteria", [])}

            # 두 룰의 criteria가 완전히 겹치는지 확인
            shared_types = set(c1.keys()) & set(c2.keys())
            if not shared_types:
                continue

            # ETH_TYPE + IPV4_SRC + IPV4_DST + IP_PROTO + (TCP/UDP_DST) 모두 겹치면 충돌
            # 포트 필드(TCP_DST, UDP_DST, TCP_SRC, UDP_SRC)가 다르면 다른 트래픽이므로 충돌 아님
            key_types = {"ETH_TYPE", "IPV4_SRC", "IPV4_DST", "IP_PROTO"}
            port_types = {"TCP_DST", "UDP_DST", "TCP_SRC", "UDP_SRC"}
            if key_types.issubset(shared_types):
                match = all(
                    _criteria_field_overlaps(c1, c2, t) for t in key_types if t in c1 and t in c2
                )
                # 포트가 명시된 경우: 양쪽 모두 포트가 있고 값이 다르면 다른 트래픽 → 충돌 아님
                if match:
                    for pt in port_types:
                        if pt in c1 and pt in c2 and c1[pt] != c2[pt]:
                            match = False
                            break
                if match:
                    i1 = (f1.get("treatment") or {}).get("instructions", [])
                    i2 = (f2.get("treatment") or {}).get("instructions", [])
                    a1 = next((x["type"] for x in i1), "DROP")
                    a2 = next((x["type"] for x in i2), "DROP")
                    if a1 != a2:
                        conflicts.append({
                            "conflict_type": "Intra-Shadowing",
                            "reason": (
                                f"복합 인텐트 내 rule[{i}]({a1})과 rule[{j}]({a2})가 "
                                f"겹치는 트래픽에 상반된 액션을 지정합니다."
                            ),
                            "rule_indices": [i, j],
                        })

            # 한쪽이 catch-all (IP_PROTO, port 없음)이고 다른 쪽이 특정 포트를 가진 경우 경고
            p1 = f1.get("priority", 0)
            p2 = f2.get("priority", 0)
            higher, lower = (f1, f2) if p1 >= p2 else (f2, f1)
            hi_c = {c["type"] for c in higher.get("selector", {}).get("criteria", [])}
            lo_c = {c["type"] for c in lower.get("selector", {}).get("criteria", [])}
            if {"ETH_TYPE", "IPV4_SRC", "IPV4_DST"}.issubset(hi_c) and \
               "IP_PROTO" not in hi_c and "IP_PROTO" in lo_c:
                conflicts.append({
                    "conflict_type": "Intra-Shadowing",
                    "reason": (
                        f"복합 인텐트 내 catch-all 룰(priority={higher.get('priority')})이 "
                        f"특정 프로토콜 룰(priority={lower.get('priority')})을 가릴 수 있습니다. "
                        f"특정 룰의 priority를 더 높게 설정하세요."
                    ),
                    "rule_indices": [i, j],
                })

    return conflicts


def validate(
    flowrule: dict,
    existing_flows: Optional[list[dict]] = None,
) -> StaticResult:
    """
    FlowRule에 대해 스키마 검증과 충돌 탐지를 실행한다.

    Args:
        flowrule: {"flows": [...]} 형식의 FlowRule dict
        existing_flows: 기존 FlowRule 목록 (None이면 충돌 탐지 스킵)

    Returns:
        StaticResult 객체
    """
    schema_errors: list[str] = []
    conflicts: list[dict] = []
    warnings: list[str] = []

    # ── Step 1: 스키마 검증 ───────────────────────────────────
    schema_result = validate_schema(flowrule)
    if not schema_result["valid"]:
        schema_errors = schema_result["errors"]

    # ── Step 2: 충돌 탐지 (스키마가 유효할 때만) ──────────────
    # Redundancy(중복 규칙)·Generalization(일반화 버전)은 무해하므로 경고로만 처리.
    # Shadowing·Correlation·Imbrication만 실제 충돌(REJECT 사유)로 취급한다.
    _WARNING_ONLY = {"Redundancy", "Generalization"}

    # SFC 인텐트는 ingress/egress 쌍을 함께 배포하는 구조이므로,
    # ONOS에 남아 있는 이전 SFC 룰과 비교하면 false positive Correlation이 발생한다.
    # (새 egress의 IPV4_DST가 기존 ingress와 겹치고 action이 달라 오탐)
    # SFC 재배포 시 ONOS가 동일 priority/match 룰을 덮어쓰므로 외부 충돌 탐지를 스킵한다.
    is_sfc = flowrule.get("intent_action") == "sfc"

    if not schema_errors and existing_flows and not is_sfc:
        try:
            all_detected = detect_conflict(flowrule, existing_flows)
            for c in all_detected:
                if c.get("conflict_type") in _WARNING_ONLY:
                    warnings.append(
                        f"[{c['conflict_type']}] {c.get('reason', '')} "
                        f"(경고만, REJECT 사유 아님)"
                    )
                else:
                    conflicts.append(c)
        except Exception as exc:
            warnings.append(f"충돌 탐지 중 오류 발생: {exc}")

    # ── Step 3: 복합 인텐트 내부 충돌 검사 ──────────────────────
    if not schema_errors and flowrule.get("intent_action") == "compound":
        intra = _check_intra_conflicts(flowrule.get("flows", []))
        for c in intra:
            conflicts.append(c)

    # ── 최종 판정 ─────────────────────────────────────────────
    passed = (len(schema_errors) == 0) and (len(conflicts) == 0)

    return StaticResult(
        passed=passed,
        schema_errors=schema_errors,
        conflicts=conflicts,
        warnings=warnings,
    )
