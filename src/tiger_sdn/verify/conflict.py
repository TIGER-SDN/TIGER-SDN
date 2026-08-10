"""src/tiger_sdn/verify/conflict.py — 규칙 기반 FlowRule 충돌 탐지.

원본: sdn-intent-framework의
src/xai_pipeline/pipeline/stage3_static/conflict_detector.py. 내용 변경 없이
포팅했다.

충돌 유형:
  Shadowing    : 상위 priority가 하위 규칙의 match를 완전히 포함 + action 다름
  Redundancy   : match 겹침 + action 같음 (중복 규칙)
  Correlation  : match 겹침 + action 다름
  Imbrication  : match 부분 포함 + action 다름
  Generalization: match 포함 관계 + action 같음
"""
from __future__ import annotations

import ipaddress
import json

__all__ = [
    "normalize_hex",
    "ip_overlaps",
    "ip_is_subset",
    "get_criteria_dict",
    "get_action_key",
    "criteria_overlap",
    "detect_conflict",
]


# ── 유틸리티 함수 ──────────────────────────────────────────────

def normalize_hex(val: str) -> str:
    """0x0800 → 0x800 정규화"""
    try:
        return hex(int(val, 16))
    except Exception:
        return val.lower()


def ip_overlaps(ip1: str, ip2: str) -> bool:
    """두 IP CIDR 범위가 겹치는지 확인"""
    try:
        n1 = ipaddress.ip_network(ip1, strict=False)
        n2 = ipaddress.ip_network(ip2, strict=False)
        return n1.overlaps(n2)
    except Exception:
        return ip1 == ip2


def ip_is_subset(ip_sub: str, ip_super: str) -> bool:
    """ip_sub이 ip_super에 완전히 포함되는지 확인"""
    try:
        n_sub = ipaddress.ip_network(ip_sub, strict=False)
        n_super = ipaddress.ip_network(ip_super, strict=False)
        return n_sub.subnet_of(n_super)
    except Exception:
        return ip_sub == ip_super


def get_criteria_dict(flow: dict) -> dict:
    """selector.criteria 리스트를 {type: criterion} dict로 변환"""
    criteria = flow.get("selector", {}).get("criteria", [])
    return {c["type"]: c for c in criteria}


def get_action_key(flow: dict) -> str:
    """action을 비교 가능한 문자열로 변환.
    treatment 없음 또는 NOACTION instruction = DROP으로 통일.
    """
    treatment = flow.get("treatment")
    if treatment is None:
        return "DROP"
    instructions = treatment.get("instructions", [])
    # NOACTION만 있는 경우 = treatment 없는 DROP과 동일하게 취급
    if not instructions or all(i.get("type") == "NOACTION" for i in instructions):
        return "DROP"
    return json.dumps(
        sorted(instructions, key=lambda x: json.dumps(x, sort_keys=True)),
        sort_keys=True,
    )


def _field_value(c: dict, ctype: str) -> str:
    """criterion에서 비교 가능한 값 추출"""
    if ctype == "ETH_TYPE":
        return normalize_hex(c.get("ethType", ""))
    elif ctype in ("IPV4_SRC", "IPV4_DST"):
        return c.get("ip", "")
    elif ctype == "IP_PROTO":
        return str(c.get("protocol", ""))
    elif ctype == "TCP_DST":
        return str(c.get("tcpPort", ""))
    elif ctype == "UDP_DST":
        # 데이터셋에 UDP_DST인데 tcpPort 키를 쓰는 경우 방어
        return str(c.get("udpPort", "") or c.get("tcpPort", ""))
    elif ctype == "TCP_SRC":
        return str(c.get("tcpPort", ""))
    elif ctype == "UDP_SRC":
        return str(c.get("udpPort", "") or c.get("tcpPort", ""))
    elif ctype == "IN_PORT":
        return str(c.get("port", ""))
    elif ctype == "VLAN_VID":
        return str(c.get("vlanId", ""))
    return json.dumps(c, sort_keys=True)


# ── Match 비교 ─────────────────────────────────────────────────

def criteria_overlap(c1: dict, c2: dict) -> bool:
    """
    두 criteria dict가 같은 패킷을 매치할 수 있는지 확인.
    공통 필드 없을 때 ETH_TYPE 호환성 체크 포함.
    """
    common_types = set(c1.keys()) & set(c2.keys())

    # 공통 필드가 없는 경우: ETH_TYPE 기반 의미적 호환성 체크
    if not common_types:
        ip_fields = {
            "IPV4_SRC", "IPV4_DST", "IP_PROTO",
            "TCP_SRC", "TCP_DST", "UDP_SRC", "UDP_DST",
        }
        eth_c1 = _field_value(c1["ETH_TYPE"], "ETH_TYPE") if "ETH_TYPE" in c1 else None
        eth_c2 = _field_value(c2["ETH_TYPE"], "ETH_TYPE") if "ETH_TYPE" in c2 else None

        # ARP(0x806) 규칙은 IP 레벨 필드와 겹칠 수 없음
        if eth_c1 == "0x806" and ip_fields & set(c2.keys()):
            return False
        if eth_c2 == "0x806" and ip_fields & set(c1.keys()):
            return False
        # IPv6(0x86dd) 규칙은 IPv4 주소 필드와 겹칠 수 없음
        if eth_c1 == "0x86dd" and ip_fields & set(c2.keys()):
            return False
        if eth_c2 == "0x86dd" and ip_fields & set(c1.keys()):
            return False
        return True

    for ctype in common_types:
        v1 = c1[ctype]
        v2 = c2[ctype]

        if ctype in ("IPV4_SRC", "IPV4_DST"):
            if not ip_overlaps(_field_value(v1, ctype), _field_value(v2, ctype)):
                return False
        else:
            if _field_value(v1, ctype) != _field_value(v2, ctype):
                return False

    return True  # 모든 공통 필드에서 충돌 없음 → overlap


def _criteria_equal(c1: dict, c2: dict) -> bool:
    """두 criteria dict가 동일한 match인지 확인"""
    if set(c1.keys()) != set(c2.keys()):
        return False
    for ctype in c1:
        if ctype in ("IPV4_SRC", "IPV4_DST"):
            if _field_value(c1[ctype], ctype) != _field_value(c2[ctype], ctype):
                return False
        else:
            if _field_value(c1[ctype], ctype) != _field_value(c2[ctype], ctype):
                return False
    return True


def _is_subset(c_sub: dict, c_super: dict) -> bool:
    """c_sub의 match가 c_super의 match에 완전히 포함되는지 확인"""
    for ctype in c_super:
        if ctype not in c_sub:
            return False  # c_super가 더 구체적 → c_sub은 subset 아님

    for ctype in c_sub:
        if ctype not in c_super:
            continue  # c_sub이 더 구체적인 필드

        v_sub = c_sub[ctype]
        v_super = c_super[ctype]

        if ctype in ("IPV4_SRC", "IPV4_DST"):
            if not ip_is_subset(_field_value(v_sub, ctype), _field_value(v_super, ctype)):
                return False
        else:
            if _field_value(v_sub, ctype) != _field_value(v_super, ctype):
                return False

    return True


def _compare_two_flows(f_new: dict, f_existing: dict) -> dict:
    """두 flow dict 간의 충돌을 탐지한다."""
    # 0단계: 다른 장치의 규칙은 같은 패킷을 처리할 수 없으므로 충돌 불가
    dev_new = f_new.get("deviceId")
    dev_existing = f_existing.get("deviceId")
    if dev_new and dev_existing and dev_new != dev_existing:
        return {
            "conflicting": False,
            "conflict_type": None,
            "reason": f"다른 장치({dev_new} vs {dev_existing}) — 충돌 불가",
        }

    c_new = get_criteria_dict(f_new)
    c_existing = get_criteria_dict(f_existing)
    p_new = int(f_new.get("priority", 0))
    p_existing = int(f_existing.get("priority", 0))
    a_new = get_action_key(f_new)
    a_existing = get_action_key(f_existing)

    # 1단계: match overlap 확인
    if not criteria_overlap(c_new, c_existing):
        return {
            "conflicting": False,
            "conflict_type": None,
            "reason": "match 기준이 겹치지 않아 충돌 없음",
        }

    action_equal = a_new == a_existing
    match_equal = _criteria_equal(c_new, c_existing)

    # Redundancy: match 동일 + action 동일
    if match_equal and action_equal:
        return {
            "conflicting": True,
            "conflict_type": "Redundancy",
            "reason": f"match와 action이 완전히 동일한 중복 규칙 (priority {p_new} vs {p_existing})",
        }

    # 포함 관계 확인
    new_sub_existing = _is_subset(c_new, c_existing)
    existing_sub_new = _is_subset(c_existing, c_new)

    if not action_equal:
        # ── Priority 해소 판단 ─────────────────────────────────
        # OpenFlow에서 priority가 높은 룰이 항상 이긴다.
        # 새 룰이 기존 룰보다 우선순위가 높으면 겹치는 패킷은 새 룰이 처리 → 의도된 동작.
        # 이 경우 Correlation/Imbrication은 false positive이므로 보고하지 않는다.
        # 단, 새 룰이 기존 룰의 match를 완전히 포함하여 기존 룰이 영원히 도달 불가한
        # Shadowing은 운영자에게 알려야 하는 진짜 충돌이다.

        if p_new > p_existing:
            # 새 룰이 우선 → 기존 룰 일부/전체가 가려지는지 확인
            if _is_subset(c_existing, c_new):
                # 기존 룰의 match가 새 룰에 완전히 포함 → 기존 룰 도달 불가 (Shadowing)
                return {
                    "conflicting": True,
                    "conflict_type": "Shadowing",
                    "reason": (
                        f"새 규칙(priority {p_new})이 기존 규칙(priority {p_existing})의 "
                        f"match를 완전히 포함하여 기존 규칙이 도달 불가"
                    ),
                }
            # 부분 겹침이지만 새 룰이 우선 → 정상 override, 충돌 아님
            return {
                "conflicting": False,
                "conflict_type": None,
                "reason": (
                    f"새 규칙(priority {p_new})이 기존 규칙(priority {p_existing})보다 "
                    f"우선순위가 높아 정상 override (충돌 아님)"
                ),
            }

        elif p_new < p_existing:
            # 기존 룰이 우선 → 새 룰이 의도한 패킷을 기존 룰이 가로챌 수 있음
            if _is_subset(c_new, c_existing):
                # 새 룰의 match 전체가 기존 고우선순위 룰에 덮임 → 새 룰 도달 불가
                return {
                    "conflicting": True,
                    "conflict_type": "Shadowing",
                    "reason": (
                        f"기존 규칙(priority {p_existing})이 새 규칙(priority {p_new})의 "
                        f"match를 완전히 포함하여 새 규칙이 도달 불가"
                    ),
                }
            # 부분 겹침 + 기존 룰 우선 → 겹치는 패킷에서 새 룰이 무시될 수 있음
            if new_sub_existing or existing_sub_new:
                return {
                    "conflicting": True,
                    "conflict_type": "Imbrication",
                    "reason": (
                        f"기존 규칙(priority {p_existing})이 새 규칙(priority {p_new})보다 "
                        f"우선순위가 높고 match가 겹침 — 겹치는 패킷에서 새 규칙이 무시될 수 있음"
                    ),
                }
            return {
                "conflicting": True,
                "conflict_type": "Correlation",
                "reason": (
                    f"기존 규칙(priority {p_existing})이 새 규칙(priority {p_new})보다 "
                    f"우선순위가 높고 match가 겹치며 action이 다름"
                ),
            }

        else:
            # 동일 priority + 겹치는 match + 다른 action → 진짜 충돌
            if new_sub_existing or existing_sub_new:
                return {
                    "conflicting": True,
                    "conflict_type": "Imbrication",
                    "reason": (
                        f"동일 priority({p_new})에서 match가 부분 포함 관계이며 action이 다름 "
                        f"— 패킷 처리 결과 비결정적"
                    ),
                }
            return {
                "conflicting": True,
                "conflict_type": "Correlation",
                "reason": (
                    f"동일 priority({p_new})에서 match가 겹치고 action이 다름 "
                    f"— 패킷 처리 결과 비결정적"
                ),
            }

    # action 동일
    if action_equal and (new_sub_existing or existing_sub_new):
        return {
            "conflicting": True,
            "conflict_type": "Generalization",
            "reason": "한 규칙이 다른 규칙의 일반화 버전 (더 넓은 match, 같은 action)",
        }

    # match 겹침 + action 동일 + match 다름 → 잠재적 Redundancy
    return {
        "conflicting": True,
        "conflict_type": "Redundancy",
        "reason": "match가 부분적으로 겹치고 action이 동일 (잠재적 중복)",
    }


def detect_conflict(new_flow: dict, existing_flows: list[dict]) -> list[dict]:
    """
    new_flow를 existing_flows의 각 flow와 비교하여 충돌 목록을 반환한다.

    Args:
        new_flow: {"flows": [...]} 형식 또는 단일 flow dict
        existing_flows: 기존 flow dict 리스트 (각각 단일 flow 또는 {"flows": [...]} 형식)

    Returns:
        충돌이 있는 항목만 담은 리스트:
        [{"conflicting": True, "conflict_type": str, "reason": str, "with_flow": dict}, ...]
    """
    # new_flow 정규화 (flows 래퍼 있으면 모든 flows 추출 — B6 fix: SFC 다중 룰 전체 검사)
    if "flows" in new_flow:
        new_flows = new_flow.get("flows", [])
    else:
        new_flows = [new_flow]

    conflicts = []

    for f_new in new_flows:
        for existing in existing_flows:
            # existing 정규화
            if "flows" in existing:
                existing_sub_flows = existing.get("flows", [])
            else:
                existing_sub_flows = [existing]

            for f_existing in existing_sub_flows:
                result = _compare_two_flows(f_new, f_existing)
                if result["conflicting"]:
                    # 중복 충돌 방지 (같은 (f_new, f_existing) 쌍 재진입 없음)
                    conflicts.append({
                        "conflicting": True,
                        "conflict_type": result["conflict_type"],
                        "reason": result["reason"],
                        "with_flow": f_existing,
                    })

    return conflicts
