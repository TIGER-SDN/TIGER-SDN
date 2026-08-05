# TIGER-SDN 이식 계획

> 대상: `Jangmyun/sdn-intent-framework`(원본) + `seongyooo/sdn-xai-pipeline`(원본) -> `TIGER-SDN/TIGER-SDN`(신규)
> 참고 마일스톤: KICS 논문 마감 2026-08-24

이 저장소의 유일한 계획 문서다. 새 조사나 결정이 생기면 별도 파일을 만들지 말고
이 문서를 갱신한다.

---

## 이식 방식

파일 단위로, 필요한 이유를 이해하면서 옮긴다. 원본의 작업 트리를 통째로 복사하지
않는다 - 원본에는 md 51개(약 10,800줄)와 코어 2벌(약 11,100 LOC)이 있어서, 통째로
옮기면 그 부담이 새 레포에 그대로 옮겨진다.

- **원본 레포에는 쓰지 않는다.** 읽어서 필요한 파일만 이 레포로 옮긴다.
- **원본 두 레포는 삭제하지 않는다.** 커밋 이력과 실험 로그가 논문 재현성의 증거다.
- **패키지명은 `tiger_sdn`이다.** 레포명(TIGER-SDN)과 일치시킨다. 아래 "확인된 사실 1"의
  IR 코드는 원본에서 `sdn_intent`라는 이름을 쓰고 있으므로, 이식할 때 이름을 바꾼다
  (`src/sdn_intent/` -> `src/tiger_sdn/`, 내부 import 경로도 함께 고침).

---

## 확인된 사실

**1. IR 코드가 GitHub에 없다.**
`sdn-intent-framework`의 `feat/unify-ir` 브랜치에 `src/sdn_intent/`(IR 모델, 어댑터,
예측 래퍼, 699 LOC)가 있고 GOLD-350 accepted 300건 로드를 이미 검증했지만, 이
브랜치는 origin에 push된 적이 없고 그 안의 `src/sdn_intent/`도 한 번도 커밋된 적이
없다. **`git clone`이나 GitHub에서는 이 코드를 받을 수 없다** - 지금 이 코드를 갖고
있는 사람이 커밋하고 push하거나, 파일을 직접 공유해야 한다. Stage 4 착수 전에
먼저 해결한다.

**2. GOLD-350은 정본과 파생본의 스키마가 다르다.**
`docs/dataset/gold.jsonl`(정본, 350건)은 연구 스키마(`source_port`, `action:"deny"`)를
쓰고, `research/experiments/eval/data/gold350_eval.jsonl`(파생, 350건)은 제품
스키마(`dst_port`, `action:"block"`)를 쓴다. `convert_gold350.py`가 둘을 잇는다.
Exp-1이 실제로 채점하는 건 파생본이므로, **정본만 옮기면 채점이 안 돈다** -
변환기가 필수 이식 대상이다. 이 스크립트의 서두 주석 46줄에 스키마 차이 전체가
설명되어 있다.

**3. `score_exp1.py`는 992줄, 외부 의존성 0(stdlib만).**

---

## 왜 이 순서인가

`run_exp1.py`가 코어에서 가져오는 것은 `config`와 `SYSTEM_PROMPT` 둘뿐이다. IR도
컴파일러도 검증기도 쓰지 않는다. 그래서 **Exp-1 수치를 바꾸는 건 프롬프트, 채점
로직, gold 데이터셋 셋뿐**이고, 코어를 아무리 잘 이식해도 논문 수치와 바로
연결되지 않는다 - 코어가 논문에 반영되는 유일한 경로는 이식한 코어를 실제로
실행하는 Exp-2(Stage 8)뿐이다.

그래서 **Stage 0-3(레포 골격, 데이터셋, 회귀 하네스, 프롬프트 단일화)은 위 세 가지만
다루므로 마감 전에 끝낼 수 있고, 끝내는 편이 좋다.** Stage 4-8(코어 이식)은 마감 전
수치와 무관하므로 마감 이후로 미뤄도 안전하다.

---

## 목표 레포 구조

```
TIGER-SDN/
├── CLAUDE.md, README.md, LICENSE, CONTRIBUTORS.md
├── pyproject.toml, .env.example
│
├── data/gold/
│   ├── gold.jsonl               정본 350건 (연구 스키마)
│   ├── gold350_eval.jsonl       파생 350건 (채점 스키마)
│   ├── topology_eval.json, demonstrations.json
│   └── provenance/              candidates, blind, annotations
│
├── docs/
│   ├── plan.md                  이 문서
│   ├── ANNOTATION_GUIDELINE.md  규범 스펙 - 변환기와 프롬프트가 참조
│   └── DATASET_CARD.md
│
├── prompts/
│   ├── intent_ir.md, direct_flow.md
│   └── registry.py              treatment -> 프롬프트 매핑
│
├── experiments/
│   ├── exp1/{run,score,convert_gold350}.py, config/, logs/, reports/
│   └── exp2/
│
├── src/tiger_sdn/
│   ├── config.py
│   ├── ir/                      Stage 4
│   ├── compile/                 Stage 5
│   ├── verify/                  Stage 6
│   ├── twin/                    Stage 7
│   ├── backends/onos.py         Stage 5-7 (배포 백엔드로 격리)
│   ├── runctx/                  실행 로깅 (연구 트랙이 정본)
│   ├── parse/, orchestrate/pipeline.py  Stage 8
│   ├── explain/, deploy/        장기 보류
│   └── py.typed
│
└── tests/
```

---

## Stage 0. 레포 골격

| 항목 | 처리 |
|---|---|
| `LICENSE` | `sdn-intent-framework`의 `LICENSE.md` 그대로. MIT, 저자 `Jangmyun, seongyooo` |
| `CONTRIBUTORS.md` | 신규. 두 저자, 서브시스템 주 저자(`xai_pipeline` = seongyooo, `safe_intent_sdn` = Jangmyun) |
| `.env.example` | `sdn-intent-framework`의 것 그대로 |
| `pyproject.toml` | 신규. 초기 의존성은 `pydantic`, `python-dotenv`, `pytest`만 |
| `.github/workflows/ci.yml` | 신규. Stage 2에서 회귀 테스트 연결 |

**완료 기준:** `pytest` 0 tests 성공, CI 초록, `LICENSE`/`CONTRIBUTORS.md` 존재.

---

## Stage 1. GOLD-350 데이터셋

가장 준비도가 높은 자산이라 먼저 확보한다. 코드 의존성 0.

| 원본 (`sdn-intent-framework`) | 대상 |
|---|---|
| `docs/dataset/gold.jsonl` (350건) | `data/gold/gold.jsonl` |
| `research/experiments/eval/convert_gold350.py` | `experiments/exp1/convert_gold350.py` |
| `research/experiments/eval/data/gold350_eval.jsonl` (350건) | `data/gold/gold350_eval.jsonl` |
| `research/experiments/eval/data/topology_eval.json` | `data/gold/topology_eval.json` |
| `research/experiments/eval/data/demonstrations.json` | `data/gold/demonstrations.json` |
| `docs/dataset/ANNOTATION_GUIDELINE.md` | `docs/ANNOTATION_GUIDELINE.md` |
| `docs/dataset/DATASET_CARD.md` | `docs/DATASET_CARD.md` - 재작성 필요, 아래 참고 |

> **`ANNOTATION_GUIDELINE.md`는 문서가 아니라 규범 스펙이다.** `convert_gold350.py`의
> IP 역채움, 프롬프트의 selector 규칙, `topology_eval.json`의 포트셋이 전부 여기서
> 나온다. **프롬프트를 고치면 가이드라인도 같이 본다. 반대도 마찬가지다.**

> `DATASET_CARD.md`는 그대로 옮기면 안 된다. 이식 대상이 아닌 논문 프로토콜 문서를
> 참조하고 경로도 다르다. 구성 표와 Known limitation(어노테이터가 인간 전문가가
> 아니라 가이드라인을 공유한 독립 LLM 세션이라는 명시)은 유지하고 참조만 고친다.

**재현성용 (권장):** `research/experiments/gold/`(후보 생성, 어노테이션, 일치도 산출
스크립트+데이터) -> `data/gold/provenance/`. `build_gold.py`가
`safe_intent_sdn.e1_evaluation.EvaluationCase`를 import하므로 그 의존을 끊거나 함께
가져온다.

**신규:** `tests/test_gold_dataset.py` - 350건 카운트, 카테고리 7종, 정본->파생본
변환 재현.

**완료 기준:** `convert_gold350.py` 출력이 커밋된 `gold350_eval.jsonl`과 바이트 동일.

---

## Stage 2. Exp-1 회귀 하네스

외부 의존성 0이라 코어보다 먼저 세울 수 있다. 세워두면 이후 디버깅이 싸진다.

| 원본 | 대상 |
|---|---|
| `sdn-intent-framework`의 `research/experiments/eval/score_exp1.py` (992 LOC) | `experiments/exp1/score.py` |
| `sdn-xai-pipeline`의 `experiments/eval/logs/` 중 T-A/B/C/D 각 1개(합계 약 1.9MB) | `experiments/exp1/logs/` |
| `sdn-xai-pipeline`의 `experiments/eval/reports/T-{A,B,C,D}_openrouter_r1_summary.json` | `experiments/exp1/reports/` - 골든 기대값 |
| `sdn-intent-framework`의 `research/experiments/eval/config/T-{A,B,C,D}-openrouter.toml` | `experiments/exp1/config/` |

> 원시 로그는 `sdn-xai-pipeline`에만 있다 - `sdn-intent-framework`는 이걸 gitignore
> 했다. 코드는 `sdn-intent-framework`에서, 로그는 `sdn-xai-pipeline`에서 가져온다.

**신규:** `tests/test_exp1_regression.py` - 로그를 재채점해 커밋된 리포트와 대조,
252필드 불일치 0을 확인한다. 재채점 1회 약 2초, LLM 호출 없음. CI에 넣는다.

**완료 기준:** CI에서 T-A, T-B, T-C, T-D 4개 모두 초록.

---

## Stage 3. 프롬프트 단일 출처화

"왜 이 순서인가"에 의해 Exp-1 수치를 실제로 움직이는 유일한 코드 자산이다.
프롬프트 하드코딩이 과거 **실험 2회를 무효화**시켰다.

| 원본 | 처리 |
|---|---|
| `stage1_intent/intent_parser.py`의 `SYSTEM_PROMPT` | 텍스트만 추출해 `prompts/intent_ir.md`로 |
| `run_exp1.py`의 `SYSTEM_DIRECT_FLOW`(T-A용, 하드코딩되어 있음) | `prompts/direct_flow.md`로 |
| `run_exp1.py` 본체 | `experiments/exp1/run.py`로 |
| `xai_pipeline/config.py` | `src/tiger_sdn/config.py`로. `.env` 로딩만 남기고 트림 |

**신규:** `prompts/registry.py`(treatment -> 프롬프트 매핑), `tests/test_prompt_registry.py`
(대응표 고정, 프롬프트 파일 해시 스냅샷).

**알아둘 것:**

- 현재 `SYSTEM_PROMPT`는 GOLD-350 가이드라인의 selector 완전성 규칙을 규범으로
  채택해 개정된 결과물이다. `prompts/intent_ir.md`와 `docs/ANNOTATION_GUIDELINE.md`는
  같이 움직인다.
- T-B/T-C가 `h1`에서 `10.0.0.1`을 추측해야 하는 건 결함이 아니라 grounding 효과를
  재려는 의도된 설계다.
- **L-SEC-R01 라벨 충돌 1건.** 위 프롬프트 개정의 부작용으로 Large 보조 데이터셋의
  한 케이스(`"Block all traffic from 10.0.0.5."`)가 구 라벨과 어긋난다. Large
  트랙을 쓸 때만 걸린다 - 쓰기로 하면 그때 라벨을 고친다.

**완료 기준:** 코드베이스 전체에 프롬프트 문자열 리터럴 0건.

---

> ### 여기까지가 1차 목표 (마감 전 완료 권장)
> 코어(IR, 컴파일러, 검증기)는 한 줄도 안 건드리고, 데이터셋 완비 + CI 회귀 테스트
> + 프롬프트 단일 출처를 확보한다. 이 안전망 위에서 Stage 4-8을 옮기는 편이 훨씬 싸다.

---

## 마감 전 논문 병행 작업

코어를 건드리지 않으므로 Stage 0-3과 병행 가능하다.

| 작업 | 완료 기준 |
|---|---|
| 논문 서술 정합성 결정 | GOLD-350 Exp-1을 유일한 정량 결과로, E2/E3는 구성요소 평가로 분리 서술 |
| SFC 프로세스 다운 원인 조사 | `concurrency=20`에서 재발하는 크래시. rep 확대 전 선행 |
| Exp-1 rep 확대 (1 -> 3 이상) | **Stage 3 완료 후 착수** - 안 그러면 프롬프트 드리프트로 실험이 또 무효화될 수 있다 |
| L-SEC-R01 라벨 수정 | Large 트랙 쓸 때만 |
| RAG 인덱스 캐싱 (선택) | 파싱 결과가 안 바뀌는 순수 성능 수정 |

**마감 전 하지 말 것:** 대규모 코어 리팩터링, 두 코어의 성급한 병합, figure 산출
코드(`research/paper/`) 수정.

---

## Stage 4-8. 코어 이식 (마감 이후 권장)

### Stage 4. Intent IR

베이스: `sdn-intent-framework`의 `src/sdn_intent/ir/`(699 LOC, "확인된 사실 1" 참고) ->
`src/tiger_sdn/ir/`로 옮기며 패키지명을 바꾼다(import 경로 포함).
흡수: `research/safe_intent_sdn/intent_ir.py`의 `StrictModel`/`extra="forbid"`.
대조용(이식 안 함): `src/xai_pipeline/models/intent_ir.py`.

**알려진 함정:**

- `safe_intent_sdn.Endpoint`의 `require_identity`(host/ip 중 정확히 하나)는
  GOLD-350과 비호환 - "최소 하나"로 완화한다.
- `intent_type`과 `action`은 다른 축이다. `security`는 `forward`/`block` 둘 다
  가진다 - `block`으로만 매핑하면 13건이 떨어진다.
- `egress_port`가 문자열인 레코드 78건. int 변환은 안전(정규화).
- SFC 표현 차이 - 제품은 `routing.waypoints`, 연구는 `sfc_role` + `sfc_chain`.
- gold의 미지정 필드는 명시적 `null`. `extra="forbid"` 전에 None 제거.

검증 안 된 부분: 어댑터의 연구 스키마 왕복 변환. 여기부터 pytest 작성.

**완료 기준:** accepted 300건 전량 로드, 실패 0.

### Stage 5. 컴파일러

베이스: `src/xai_pipeline/pipeline/stage2_flowrule/compiler.py`(340 LOC).
흡수: `research/safe_intent_sdn/compiler.py`의 `CompilationError`.

> **미해결 버그.** 컴파일러가 `waypoints[0]`만 사용해 multi-switch SFC 체인을 단일
> 홉만 컴파일한다(10건). Exp-1엔 무해하지만 Exp-2에서는 버그다. 여기서 고치거나
> 최소 실패 테스트로 남긴다.

**완료 기준:** gold accepted 300건이 컴파일 에러 없이 통과.

### Stage 6. 정적 검증 (2축 병합)

베이스: `stage3_static/{schema_validator,conflict_detector,static_validator}.py`
(충돌 탐지 5종) + `research/safe_intent_sdn/validator.py`(`TopologyInventory`
그라운딩). 대체 관계가 아니라 병합 대상이다.

**함께 처리:** compound 내부 충돌 검사가 CIDR subset/overlap을 안 봄(외부 탐지와
동일 함수 사용) / `device` 미지정 시 조용히 폴백하던 것을 명시적 거부로 통일 /
`schema_validator` 등 테스트 0개를 이식과 동시에 작성.

**완료 기준:** 그라운딩 검증이 미지 엔티티를 거부.

### Stage 7. Digital Twin

베이스: `stage4_twin/twin_verifier.py`(1100 LOC) 외 토폴로지/ONOS클라이언트/트래픽
생성기. 흡수: `research/safe_intent_sdn/twin/bandwidth.py`의 `meets_target()` 판정
(iperf가 측정만 하고 판정을 안 하던 것 보완). `ovs-ofctl` 명령을 f-string 조립에서
인자 리스트로 변경.

**완료 기준:** QoS 대역폭이 pass/fail 판정을 반환.

### Stage 8. Exp-2 (최종 목표)

이식한 코어를 실제로 실행하는 유일한 실험이다. 베이스:
`research/safe_intent_sdn/e2_evaluation.py` + `research/experiments/e2/`.
`main.py`/`api.py`에 있던 Repair Loop 이중 구현을 `orchestrate/pipeline.py`로 승격.

**완료 기준:** 파싱/컴파일/검증 통과율 리포트 생성.

### 실행 로깅 (Stage 2 직후 착수해도 무방)

`research/safe_intent_sdn/run_context.py` + `schema.py`가 이 영역의 정본이다 -
JSON Schema 자동생성, secret redaction, 동시성에서 연구 트랙이 우월하다. 제품 구현의
로그 필드를 흡수한다.

---

## 이식하지 않는 것

| 원본 | 이유 |
|---|---|
| `src/xai_pipeline/api.py` + UI | 논문 정량 결과와 무관 |
| `src/xai_pipeline/main.py` | Repair Loop만 승격, 나머지 버림 |
| `src/xai_pipeline/evaluate.py` | Exp-1/Exp-2가 대체 |
| `stage5_xai/explainer.py`, `stage6_deploy/deployer.py` | `explain/`/`deploy/` 자리는 목표 구조에 있으나 장기 보류 |
| `stage1_intent/rag.py` | 매 요청 전량 재구축(임베딩 API 수백 회) - 이식이 아니라 재작성 필요 |
| `research/experiments/e1/`, `e3/` | E1은 Exp-1이 대체, E3는 원시 로그 부재 |
| `research/paper/` | figure 산출 코드 - 마감까지 손대지 않는다 |
| `docs/dataset/GOLD350_VERIFICATION.md` | 시점 보고서. L-SEC-R01과 프롬프트 계보 등 유효한 사실은 Stage 3에 이미 흡수 |
| 원 레포 md 51개 (나머지) | 필요한 것만 발췌 |

---

## 규율

1. **원본 레포에 쓰지 않는다.** 읽어서 필요한 파일만 옮긴다.
2. **Stage 2 완료 후로는 회귀 테스트가 초록이 아닌 채로 다음 Stage로 넘어가지 않는다.**
3. **프롬프트 문자열을 코드에 쓰지 않는다.** `prompts/*.md`가 단일 출처다.
4. **인용된 런의 원시 로그는 커밋한다.** `experiments/*/logs/`를 gitignore하지 않는다.
5. **`data/gold/gold.jsonl`(정본)을 직접 수정하지 않는다.** 파생본은
   `convert_gold350.py`로만 생성한다.
6. **마감(2026-08-24) 전에는 `research/paper/`를 손대지 않는다.**
7. 라이선스 MIT, 저자 `Jangmyun, seongyooo`. 원본 두 레포는 삭제하지 않는다.

---

## 진행 현황

- [x] **Stage 0.** 레포 골격, LICENSE, CONTRIBUTORS, CI
  - [x] `docs/plan.md`, `CLAUDE.md`, `README.md`, `.gitignore`
  - [x] `LICENSE`, `CONTRIBUTORS.md`, `.env.example`
  - [x] `pyproject.toml`, `.github/workflows/ci.yml`
- [x] **Stage 1.** GOLD-350 (재현성용 provenance 스크립트는 core 미이식으로 보류)
- [x] **Stage 2.** Exp-1 회귀 하네스 (완료, 이슈 #4)
  - `score.py`, `convert_gold350.py`, `config/T-{A,B,C,D}-openrouter.toml` 이식
  - T-A/B/C/D 원시 로그(1.9MB)는 `sdn-intent-framework`의 `logs 2/`(로컬,
    gitignore됨)에서 발견해 이식. `TA-TD_openrouter_comparison.md`가 지목한
    run_id로 골든 리포트 재생성, 원 보고서 수치와 일치 확인.
  - `tests/test_exp1_regression.py` 신규 작성, `pytest` 4개 전부 초록.
  - 이식 중 `score.py`의 `ROOT` 경로 깊이 계산과 argparse `%` 이스케이프 버그를
    발견해 수정 (원본: `sdn-intent-framework`의
    `research/experiments/eval/score_exp1.py`).
- [ ] **Stage 3.** 프롬프트 단일 출처화 ← 여기까지 1차 목표
- [ ] **마감 전 병행 작업** (논문 서술, SFC 조사, rep 확대, 라벨 수정)
- [ ] **KICS 논문 마감 2026-08-24**
- [ ] **Stage 4.** Intent IR (착수 전 "확인된 사실 1" 해결 필요)
- [ ] **Stage 5.** 컴파일러
- [ ] **Stage 6.** 정적 검증
- [ ] **Stage 7.** Digital Twin
- [ ] **Stage 8.** Exp-2 (최종)
- [ ] **실행 로깅**
