# CLAUDE.md - TIGER-SDN

자연어 의도에서 ONOS FlowRule로 가는 파이프라인. `sdn-intent-framework`와
`sdn-xai-pipeline`에서 핵심 로직을 파일 단위로, 이해하며 이식하는 중이다.
작업 트리를 통째로 복사하지 않는다.

`docs/plan.md`가 이 저장소의 유일한 계획 문서다. 작업 전에 읽는다. 새 조사나
결정이 생기면 별도 파일을 만들지 말고 그 문서를 갱신한다.

---

## 지금이 어느 단계인지

- **Stage 0-3(레포 골격, GOLD-350, Exp-1 회귀 하네스, 프롬프트 단일화)이 1차
  목표다.** Exp-1 수치에 영향을 주는 세 가지(프롬프트, 채점 로직, gold 데이터셋)만
  건드린다.
- **Stage 4-8(코어 이식)은 KICS 논문 마감(2026-08-24) 이후로 미루는 걸 권한다.**
  기술적으로 막혀 있진 않지만, 마감 전엔 논문 병행 작업(`docs/plan.md` 참고)에
  자원을 쓰는 편이 낫다. `research/paper/`(figure 산출 코드)는 마감 전에 손대지
  않는다.

---

## 원본 레포

`sdn-intent-framework`가 코드 정본, `sdn-xai-pipeline`이 원시 로그 정본이다
(둘은 코드가 거의 같고 경로만 다르다 - 로그는 후자에만 있다). **원본 레포에는
쓰지 않는다.** 읽어서 필요한 파일만 이 레포로 옮긴다.

**착수 전 확인할 것:** `sdn-intent-framework`의 `feat/unify-ir` 브랜치에 이미
검증된 IR 코드(`src/sdn_intent/`, 699 LOC)가 있는데, 이 브랜치는 origin에 push된
적이 없고 그 안의 코드도 커밋된 적이 없다. `git clone`으로는 못 받는다 - 지금 이
코드를 갖고 있는 사람이 커밋하고 push해야 다른 사람이 받을 수 있다. Stage 4
착수 전에 먼저 해결한다.

---

## 절대 규칙

1. **프롬프트 문자열을 코드에 쓰지 않는다.** `prompts/*.md`가 단일 출처다.
   이걸 어겨서 실험 2회가 무효화됐다. `docs/ANNOTATION_GUIDELINE.md`는 문서가
   아니라 규범 스펙이다 - 프롬프트를 고치면 가이드라인도 같이 본다.
2. **인용된 런의 원시 로그는 커밋한다.** `experiments/*/logs/`를 gitignore하지
   않는다. 이걸 어겨서 E1 원시 로그를 영구히 잃었다.
3. **Stage 2 완료 후로는 회귀 테스트가 초록이 아닌 채로 다음 Stage로 넘어가지 않는다.**
4. **`data/gold/gold.jsonl`(정본)을 직접 수정하지 않는다.** 파생본은
   `convert_gold350.py`로만 생성한다.
5. **마감(2026-08-24) 전에는 `research/paper/`를 손대지 않는다.**
6. **원본 두 레포는 삭제하지 않는다.**
7. **패키지명은 `sdn_intent`다.** `tiger_sdn`이 아니다 - 이미 검증된 699 LOC
   코드의 실제 이름을 그대로 쓴다.

---

## 자주 밟는 함정 (Stage 4-7용, 상세는 `docs/plan.md`)

- gold 정본(`gold.jsonl`)과 채점용 파생본(`gold350_eval.jsonl`)은 스키마가 다르다.
  `convert_gold350.py`가 잇는다.
- `intent_type`과 `action`은 다른 축이다. `security`는 `forward`/`block`을 둘 다
  가진다.
- 연구 IR의 `require_identity`는 GOLD-350과 비호환 - "최소 하나"로 완화한다.
- gold의 미지정 필드는 명시적 `null`. `extra="forbid"` 전에 None을 제거한다.
- 컴파일러가 `waypoints[0]`만 써서 multi-switch SFC가 단일 홉만 컴파일된다.
  Exp-1엔 무해, Exp-2에선 버그.

---

## 명령

```bash
# 정본 -> 채점 스키마 (출력이 커밋본과 바이트 동일해야 함)
python experiments/exp1/convert_gold350.py \
    --input data/gold/gold.jsonl --output data/gold/gold350_eval.jsonl

# Exp-1 재채점 (LLM 호출 없음, 약 2초)
python experiments/exp1/score.py \
    --dataset data/gold/gold350_eval.jsonl \
    --topology data/gold/topology_eval.json \
    --logs experiments/exp1/logs/ \
    --output experiments/exp1/reports/summary.json \
    --treatment T-D --run-id <run_id>

pytest
```

`--run-id`는 같은 treatment에 런이 여럿일 때 필수다.

---

## 스타일

- Python 3.11 이상. `from __future__ import annotations`, 타입 힌트, `pathlib`.
- 검증은 pydantic v2. 코어 모델은 `extra="forbid"`.
- `experiments/exp1/score.py`는 stdlib만 쓴다. 의존성을 추가하지 않는다.
- 이식한 코드에는 원본 경로를 주석으로 남긴다. 이식한 파일은 `docs/PROVENANCE.md`에도 기록한다.
- 문서에 `§`, `〃`, `·` 같은 기호를 쓰지 않는다. "4.1절"처럼 풀어 쓴다.
- 커밋은 작게. 파일 1개와 그 테스트가 1커밋.
