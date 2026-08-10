"""src/tiger_sdn/ir/prediction.py — 파싱 결과 래퍼 — accepted / rejected 분기.

원본: sdn-intent-framework의 feat/unify-ir 브랜치, src/sdn_intent/ir/prediction.py
(커밋된 적 없는 로컬 상태 — docs/plan.md "확인된 사실 1" 참고. 패키지명만
sdn_intent -> tiger_sdn으로 바꾸고 내용은 그대로 옮겼다.)

LLM 파싱과 토폴로지 검증의 결과를 담는다. 제품 구현과 연구 구현이 각각
`IntentPrediction`을 갖고 있었으나 형태가 달랐다.

  제품:  status / program / compound / rejection_reason / rejection_detail
  연구:  status / program / rejection{reason}

여기서는 연구 구현의 중첩 분기 구조(`rejection` 객체)를 택했다. 거부 사유에
설명 문자열을 함께 담을 자리가 생기고, "정확히 한 쪽 분기만 채워진다"는 불변식을
검증기로 강제할 수 있기 때문이다. 제품 구현의 `program` / `compound` 이원화는
`IntentProgram.rules`가 항상 목록이므로 사라졌다.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from tiger_sdn.ir.model import IntentProgram, StrictModel

__all__ = ["RejectionReason", "RejectedIntent", "IntentPrediction"]

RejectionReason = Literal["ambiguous", "contradictory", "unknown_entity", "unsupported"]


class RejectedIntent(StrictModel):
    """인텐트를 거부한 이유.

    ambiguous      — 구체적 동작을 특정할 수 없을 만큼 모호하다
    contradictory  — 서로 모순되는 요구가 섞여 있다
    unknown_entity — 토폴로지에 없는 호스트/스위치를 참조한다
    unsupported    — 이 시스템이 지원하지 않는 기능이다
    """

    reason: RejectionReason
    detail: Optional[str] = None


class IntentPrediction(StrictModel):
    """LLM 파싱 결과 — 수락된 프로그램 또는 거부 사유 중 정확히 하나를 담는다."""

    status: Literal["accepted", "rejected"]
    program: Optional[IntentProgram] = None
    rejection: Optional[RejectedIntent] = None

    @model_validator(mode="after")
    def _validate_branch(self) -> "IntentPrediction":
        accepted = self.status == "accepted"
        if accepted and self.program is None:
            raise ValueError("accepted 예측에는 program이 있어야 합니다")
        if accepted and self.rejection is not None:
            raise ValueError("accepted 예측에는 rejection이 없어야 합니다")
        if not accepted and self.rejection is None:
            raise ValueError("rejected 예측에는 rejection이 있어야 합니다")
        if not accepted and self.program is not None:
            raise ValueError("rejected 예측에는 program이 없어야 합니다")
        return self

    # ── 편의 생성자 ──────────────────────────────────────────────────

    @classmethod
    def accept(cls, program: IntentProgram) -> "IntentPrediction":
        return cls(status="accepted", program=program)

    @classmethod
    def reject(
        cls, reason: RejectionReason, detail: Optional[str] = None
    ) -> "IntentPrediction":
        return cls(status="rejected", rejection=RejectedIntent(reason=reason, detail=detail))
