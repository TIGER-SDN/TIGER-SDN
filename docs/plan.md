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
수치와 무관하므로 마감 이후로 미뤄도 안전하다 — **였으나 아래 "2026-08-10 방향 전환"
참고.**

---

## 2026-08-10 방향 전환

Stage 0-3 완료로 Exp-1 안전망(LLM 호출 없이 커밋된 로그로 재현)은 이미 확보됐다.
이 안전망은 이후 어떤 일이 있어도 그대로 유지된다 — Exp-1 수치를 움직이는 건 여전히
프롬프트/채점 로직/gold 데이터셋 셋뿐이다.

여기에 더해, **마감(2026-08-24)과 무관하게 지금부터 코어 전체(Stage 4-8) +
신규 웹 UI(Stage 9)를 이어서 이식한다.** 목표는 파이프라인을 웹 UI까지 포함해
end-to-end로 동작시킨 뒤, 그 위에서 실험을 다시 진행하는 것이다. 더 이상
"마감 전엔 코어에 손대지 않는다"는 전제로 순서를 짜지 않는다.

- **Stage 4(Intent IR)는 이미 완료.** "확인된 사실 1"의 블로커(`feat/unify-ir`의
  미커밋 IR 코드)는 실제로는 로컬 워킹 디렉토리에 그대로 남아 있어서, 원본 레포에
  쓰지 않고 그 자리에서 읽어 포팅하는 것으로 해결됐다.
- **Stage 9(웹 UI)를 신규로 추가한다.** 기존 "이식하지 않는 것" 표에 있던
  `xai_pipeline/api.py` + UI는 참고용으로만 보고, TIGER-SDN 구조에 맞게 새로
  설계한다 — 그대로 이식하지 않는다.
- **바뀌지 않는 것:** `research/paper/`는 마감 전에 손대지 않는다(이 규율은
  Stage 4-8 일정 변경과 무관하게 유지). "마감 전 병행 작업"(이슈 #6-10)은 우선순위가
  아니지만 여전히 열려 있고, 코어 이식과 병행해도 무방하다.

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
│   ├── ir/                      Stage 4 (완료)
│   ├── compile/                 Stage 5
│   ├── verify/                  Stage 6
│   ├── twin/                    Stage 7
│   ├── backends/onos.py         Stage 5-7 (배포 백엔드로 격리)
│   ├── runctx/                  실행 로깅 (완료, 이슈 #16)
│   ├── parse/, orchestrate/pipeline.py  Stage 9 파서/파이프라인 (완료)
│   ├── explain/                 결정론적 최종 판정 (완료, Stage 9)
│   ├── deploy/                  ONOS FlowRule 실배포 (완료, Stage 9)
│   ├── api/                     FastAPI 앱 + static/{index,app.js,style.css} (완료, Stage 9)
│   └── py.typed
│
├── scripts/                     Digital Twin 실검증 운영 스크립트 (Stage 7) +
│   │                             Stage 9 개발 편의 스크립트 (이슈 #31)
│   ├── installation/{setup,doctor}.sh
│   └── onos.sh, smoke_test.sh, twin_smoke_test.sh, twin_smoke.py,
│       start_mn_single3.sh, dev_server.sh
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

## Stage 4-8. 코어 이식 + Stage 9. 웹 UI (2026-08-10부터 마감 무관하게 즉시 진행)

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

### Stage 5. 컴파일러 (완료)

계획 당시엔 "베이스: `xai_pipeline`의 `stage2_flowrule/compiler.py`, 흡수:
`research`의 `CompilationError`"로 적었지만, 실제 착수해보니 두 원본을 그대로
쓸 수 없어서 통합 IR(`tiger_sdn.ir`)에 맞춰 새로 조합했다 — 아래 "직접 포팅이
아닌 이유" 참고. 산출물: `src/tiger_sdn/compile/{onos.py,compiler.py,__init__.py}`.

**직접 포팅이 아닌 이유.** `research/safe_intent_sdn/compiler.py`는
`enforcement.device`와 `qos.queue`가 없으면 무조건 `CompilationError`를 던지는데,
실제 GOLD-350 accepted 408규칙을 세어보니 **217건(53%)이 `enforcement` 자체가
없고, qos 50건 중 49건이 `queue` 없이 `min_bandwidth_mbps`만 갖는다** — 배치
(device/port)와 큐 프로비저닝은 애초에 이 Intent IR이 표현하는 범위 밖이고,
annotator가 "무엇을 할지"만 판정하고 "어디서"는 의도적으로 비워 뒀다. research
컴파일러를 그대로 쓰면 완료 기준(accepted 300건 무오류)을 채울 수 없다. 그래서
`xai_pipeline`의 관대한 폴백(device 미지정 -> "switch 1", egress_port 미지정 ->
ONOS 예약 포트 "NORMAL", qos.queue 미지정 시 QUEUE 명령어 생략)을 기본으로
채택했다. 이 폴백을 명시적 거부로 바꾸는 일은 원래 계획대로 **Stage 6의 몫으로
남긴다**(정적 검증기가 컴파일 전에 배치 누락을 거부해야 한다는 뜻 — 컴파일러
자체의 관대함은 그대로 둔다).

`CompilationError`(원래 `xai_pipeline` 쪽은 `CompileError`)라는 이름과, host->ip
엔드포인트 해석 + eth_type/IP 버전 정합성 검사는 `research/safe_intent_sdn/compiler.py`
쪽에서 그대로 흡수했다 — 애초 계획의 "흡수" 대상이 정확히 이 부분이었다.

**미해결 버그였던 것 — 재현되지 않음으로 판명.** "컴파일러가 `waypoints[0]`만
사용해 multi-switch SFC 체인을 단일 홉만 컴파일한다"는 원래 `xai_pipeline`
컴파일러가 구 플랫 스키마(`waypoints`+`alt_out_port` 압축 표현) 위에서 겪던
버그다. GOLD-350(`from_research()` 경로)에서는 이 합성이 아예 필요 없다 —
N홉 체인을 컴파일러가 아니라 **annotator가 이미 N개의 개별 규칙으로 펼쳐
놓았다**(`sfc_role`: ingress/transit/egress, 각자 자기 몫의 `enforcement.device`/
`egress_port`를 직접 보유). 그래서 sfc/reroute 규칙은 forward와 완전히 동일한
경로로 컴파일되고, `routing.waypoints`는 컴파일에 안 쓰이는 설명용 메타데이터로
남는다 — 버그를 "고쳤다"기보다 IR 설계상 재현되지 않는다.

**미지원 범위(의도적, 문서화됨):** `enforcement.alt_egress_port`(하나의 규칙
안에 waypoint 포트 + 최종 egress 포트를 압축해 담는 `from_gold350()` 스타일
표현)는 아직 다루지 않는다 — GOLD-350 accepted 300건은 전부 `from_research()`
경로라 이 필드를 쓰지 않는다. `from_gold350()` 경로를 실제로 쓰게 되면
(Exp-2/Stage 8에서 제품 스키마 직접 소비가 필요해지면) 그때 확장한다.

**신규:** `tests/test_compiler_gold350.py` — accepted 300건 컴파일 에러 0,
거부된 예측 컴파일 시 에러, block 규칙에 treatment 없음, `enforcement` 완전
누락 시 기본 device/NORMAL 포트 폴백, device_hint 자연어 파싱, compound
규칙의 순서->우선순위 보존(먼저 나온 더 구체적인 규칙이 더 높은 우선순위) 확인.
`pytest` 24개 전부 초록(기존 17개 + 신규 7개).

**완료 기준 충족:** gold accepted 300건이 컴파일 에러 없이 통과.

### Stage 6. 정적 검증 (2축 병합)

베이스: `stage3_static/{schema_validator,conflict_detector,static_validator}.py`
(충돌 탐지 5종) + `research/safe_intent_sdn/validator.py`(`TopologyInventory`
그라운딩). 대체 관계가 아니라 병합 대상이다.

**함께 처리:** compound 내부 충돌 검사가 CIDR subset/overlap을 안 봄(외부 탐지와
동일 함수 사용) / `device` 미지정 시 조용히 폴백하던 것을 명시적 거부로 통일 /
`schema_validator` 등 테스트 0개를 이식과 동시에 작성.

**완료 기준:** 그라운딩 검증이 미지 엔티티를 거부.

### Stage 7. Digital Twin (완료)

계획 당시엔 "베이스: `stage4_twin/twin_verifier.py`(1100 LOC, `xai_pipeline`
쪽)"로 적었지만, 실제로는 이미 `meets_target()` 판정을 갖추고 있던
`research/safe_intent_sdn/twin/`(twin_verifier.py 1100 LOC, topology.py,
traffic_generator.py, bandwidth.py) + `onos_client.py`가 더 완성도 높은
베이스였다 — 계획의 "흡수" 대상이던 `meets_target()`이 애초에 이 원본에
있었으므로 별도 이식이 아니라 베이스 선택 자체를 research 쪽으로 옮겼다.
`ovs-ofctl` 명령은 계획대로 f-string 조립에서 인자 리스트로 변경했다.
`_extract_intent_specs`는 통합 컴파일러(Stage 5)의 평평한 `{"flows": [...]}`
출력에 맞게 재작성 — 자세한 내용은 "진행 현황" 참고.

**완료 기준:** QoS 대역폭이 pass/fail 판정을 반환.

### Stage 8. Exp-2 (최종 목표, 완료)

이식한 코어를 실제로 실행하는 유일한 실험이다. 베이스:
`research/safe_intent_sdn/e2_evaluation.py` + `research/experiments/e2/`.
`main.py`/`api.py`에 있던 Repair Loop 이중 구현을 `orchestrate/pipeline.py`로 승격.

**완료 기준:** 파싱/컴파일/검증 통과율 리포트 생성.

**착수 시 결정 사항 (2026-08-11):**

Exp-2(`e2_evaluation.py`)는 LLM을 전혀 호출하지 않는 **고정 IR conformance
평가**다 — 손으로 만들고 검증한 IR 픽스처를 컴파일러/검증기에 통과시켜 B1(컴파일러만)
vs B2(검증기+컴파일러)를 비교하는 RQ2 실험(컴파일러-검증기 경계 측정)이지,
자연어→IR을 실제로 파싱하는 end-to-end 실험이 아니다. 완료 기준의 "파싱"은 이
고정 IR 픽스처(JSONL)를 로드하고 어댑터로 변환하는 단계를 가리킨다 — 사람이
이미 검증한 픽스처이므로 항상 100%. 실제 LLM 기반 자연어→IR 파싱(신규
`orchestrate/pipeline.py` 파서)은 Exp-2 완료 기준에 포함되지 않는다 — Stage 9가
그 파서를 필요로 하는 시점에 별도로 설계한다.

- **데이터셋 범위:** 원본 48-case(`cases.jsonl`)뿐 아니라 sfc/reroute 확장
  (`cases_sfc_reroute.jsonl`, 65-case)까지 포함한다. 단, 확장 데이터셋의 9개
  `path` 카테고리 케이스(`path_unknown_waypoint`, `path_chain_length_mismatch`,
  `path_port_discontinuity`, `path_waypoint_device_mismatch`,
  `path_role_order_invalid`, `path_avoid_device_conflict`)는 Stage 6이
  `FindingCategory`에서 `"path"`를 의도적으로 빼놓아 (통합 IR에
  프로그램 레벨 `sfc_chain` 필드가 없어서) 현재 아무 카테고리도 못 잡는다.
  이 케이스들을 쓰려면 `verify/grounding.py`에 `"path"` 카테고리와
  SFC 체인 연속성 검사(`_check_sfc_chain`/`_check_sfc_role_order`/
  `_check_avoid_device`, 원본 `research/safe_intent_sdn/validator.py:181-296`,
  약 115 LOC)를 먼저 이식해야 한다 — Stage 8 안에서 처리한다(Stage 6 완료
  기준을 소급 변경하지 않고, 그때 미룬 항목을 여기서 채우는 것).
- **`build_dataset.py`/`build_sfc_reroute_dataset.py`(데이터셋 생성기)는
  이식하지 않는다.** 둘 다 `research/experiments/e1/data/intents*.jsonl`에
  의존하는데, e1은 GOLD-350/Exp-1이 대체하므로 이식 대상에서 제외된
  상태다(위 "목표 레포 구조" 참고). 생성기 대신 그 출력물(`cases.jsonl`,
  `defective_authored.jsonl`, `cases_sfc_reroute.jsonl`,
  `defective_sfc_reroute.jsonl`)을 `gold350_eval.jsonl`과 같은 방식으로
  고정 픽스처로 커밋한다 — 재생성이 필요해지면 그때 e1 데이터를 이식 예외로
  들이는 걸 다시 논의한다.
- **`orchestrate/pipeline.py`(Repair Loop 승격)는 이번 패스에서 분리한다.**
  Exp-2 완료 기준(파싱/컴파일/검증 통과율 리포트, 위 정의대로 고정 IR
  기준)과 무관하고, LLM 파서 재설계(구 `intent_parser.py`는 이식 대상이
  아님 — Stage 3가 이미 `SYSTEM_PROMPT`만 뽑아 `prompts/intent_ir.md`로
  옮겼다)가 별도로 필요해 범위가 크다. `repair_utils.py`(39 LOC,
  `MAX_REPAIR_ATTEMPTS`/`build_repair_feedback`)는 이미 `verify/static.py`의
  `StaticResult`와 구조가 동일해 이식 자체는 쉽지만, 그걸 실제로 구동할
  루프 본체는 Stage 9(웹 UI)가 착수될 때 함께 설계하는 편이 낫다 — 그
  전까지는 사문화될 코드이기 때문. 필요해지면 별도 이슈로 관리한다.

### Stage 9. 웹 UI (신규, 2026-08-10 결정, 2026-08-13 완료)

이식한 파이프라인(IR → 컴파일러 → 검증기 → twin)을 자연어 인텐트 입력부터 결과
확인까지 브라우저에서 조작할 수 있게 하는 계층. `src/xai_pipeline/api.py` + 기존
프론트엔드는 **그대로 이식하지 않는다** — 참고용으로만 보고 TIGER-SDN 구조(특히
`IntentPrediction`/`IntentProgram` 스키마, `runctx` 로깅)에 맞게 새로 설계한다.

**착수 전 결정 필요 3가지, 이슈 #31 확인 후 착수 시점에 다음과 같이 확정:**

- **프레임워크:** FastAPI + `static/`(HTML/JS/CSS) — 원본과 동일 구성(`api.py`
  하나가 REST API와 UI를 같이 서빙, `uvicorn ...:app --port 8000` 한 프로세스).
  새 프레임워크를 도입할 이유가 없었다(원본이 이미 이 구성으로 검증됨).
- **레포 분리 여부:** 분리하지 않는다 — `src/tiger_sdn/api/`로 코어와 한 레포에
  둔다. API가 `orchestrate.pipeline.run_pipeline()`을 인프로세스로 직접 호출하므로
  분리하면 그 경계를 HTTP로 다시 감싸야 해서 이득이 없다.
- **배포 백엔드 연결 방식:** `backends.onos.OnosClient`를 API 레이어가 직접
  인스턴스화해서 쓴다(별도 배포 서비스 없음) — `deploy.Deployer`가 이미 이
  클라이언트를 감싸고 있어 그대로 재사용.

**추가로 이번 착수 시 확정한 스코프 결정(사용자 승인, 2026-08-11):**

- **XAI 설명은 결정론적 판정만.** 원본 `stage5_xai/explainer.py`의 LLM
  paraphrase 2차 호출(`self.client` 있을 때만 타는 선택적 분기)은 빼고,
  템플릿 기반 `_compute_confidence`/`_build_decision_reason`/`_build_evidence`
  로직만 `explain/decision.py`로 이식. `decision_reason`은 항상 결정론적 문자열.
- **토폴로지 패널은 읽기 전용.** 커스텀 토폴로지 에디터(드래그/드롭, Apply/Cancel,
  netcfg push)와 "라이브 트래픽 프리셋"(패킷 애니메이션 시뮬레이터)은 전부
  후속 이슈로 이연 — `GET /api/topology`가 반환하는 토폴로지 하나만 표시.
- **RAG는 v1에서 완전히 제외.** 토글조차 없음(숨김이 아니라 제거) — 별도
  이슈(#10)에서 재작성 여부를 다룬다.

**완료 기준:** 자연어 인텐트를 입력해 accepted/rejected 결과와 (accepted인 경우)
컴파일된 FlowRule을 웹 UI에서 확인할 수 있다. **코드/테스트 레벨로는 충족**
(FastAPI 앱 + SSE 스트림 + 정적 UI 전부 작성·연결됨, `pytest` 초록, 실제
Docker/ONOS 대상 `scripts/dev_server.sh` end-to-end 확인 완료 — `/api/topology`가
라이브 ONOS 데이터를 반환하는 것까지 확인함). **단, 실제 브라우저에서의 시각/조작
확인은 아직 안 됨** — 이 작업을 수행한 에이전트 환경엔 브라우저 자동화 도구가
없어, DOM id/onclick/CSS 클래스 상호 참조 검증과 `node --check` 문법 검사,
실 HTTP 서빙 확인으로 갈음했다(CLAUDE.md의 UI 변경 규칙 참고). `./scripts/
dev_server.sh` 띄운 뒤 브라우저로 최소 한 번 직접 확인하는 게 남은 작업이다.

**진행 순서:** 5개 서브스테이지로 나눠 진행 — 9.1 파서/파이프라인 코어(2026-08-11,
PR #35), 9.2 FastAPI 앱(2026-08-13), 9.3 정적 UI 포팅(2026-08-13), 9.4 테스트
편의 환경/이슈 #31(2026-08-13), 9.5 이 문서 갱신. 상세 내역은 아래 "진행 현황"
참고.

### Exp-3. Ablation 실험 (신규, 2026-08-13 결정, 이슈 #37)

Stage 9 파서/파이프라인이 실제 LLM + 실제 Digital Twin까지 end-to-end로 동작하는
걸 확인한 뒤, "GOLD-350을 그냥 전부 파이프라인에 태워본다"는 안을 "이 시스템을
안 썼을 때와 썼을 때 얼마나 다른가, 어느 단계가 실제로 무엇을 잡아내는가"를
재는 ablation 실험으로 재설계했다. Exp-1(파서만)·Exp-2(컴파일러+검증기만, LLM
없음) 둘 다 이 질문에 답 못 한다. Exp-1/Exp-2의 하네스·커밋된 로그는 건드리지
않는다.

**설계 — 4개 arm:** No-System(`direct_flow` 프롬프트로 LLM이 FlowRule 직접
생성) / No-Grounding(그라운딩 게이트 스킵) / No-Static(정적검증 게이트 스킵) /
Full(그대로). `run_pipeline()`에 `skip_grounding`/`skip_static_validation`/
`max_repair_attempts`/`initial_prediction` 4개 kwarg를 추가해 지원한다(전부
기본값 불변, `skip_twin`과 같은 패턴). `initial_prediction`으로 세 arm
(no_grounding/no_static/full)이 attempt-0 LLM 파싱을 공유해(`capture-ir` 로그)
LLM 비결정성이 arm 비교를 오염시키지 않게 한다. Tier B(실 twin)는 No-System과
Full만 페어드로 비교한다(twin-호환 5개 카테고리, SFC 전부·h2↔h3 주 대상 케이스
제외, 카테고리당 10개 고정 표본 50개 — `experiments/exp3/data/tierB_case_ids.json`).

**설계 중 발견해 고친 core 버그:** `verify/static.py`의 복합 인텐트 내부충돌
검사·SFC 외부충돌 스킵이 `intent_action` 필드에 의존하는데, 컴파일러가 그
필드를 세팅한 적이 없어(원본 `xai_pipeline`은 냈지만 이 레포 컴파일러는 규칙을
평평한 flow 리스트로 펴서 그 키 자체가 없음) 실제 파이프라인 출력에 대해 죽은
코드였다. `orchestrate/pipeline.py`가 `static_validate()` 호출 직전에
스탬핑하도록 수정.

**설계 중 발견해 별도 이슈로 남긴 버그(이슈 #40):** `verify/grounding.py`의
`_check_sfc_chain`/`_check_sfc_role_order`가 연구 스키마의 다중 규칙 SFC
표현(규칙마다 sfc_role, 체인 길이=규칙수-1)을 전제하는데, 실제 배포된 프롬프트
(`prompts/intent_ir.md`)와 `from_gold350()`은 SFC를 단일 규칙+`routing.
waypoints`로 표현한다 — GOLD-350 SFC 50건 전부 규칙 1개인데, 그 경우
`_check_sfc_chain`은 waypoints가 비어 있길 기대해서 실제로는 항상 거부한다.
Exp-1은 그라운딩을 안 돌리고 Exp-2의 SFC 픽스처는 `from_research()`(다중 규칙)
경유라, "실제 프로덕트 스키마 SFC 인텐트가 실제 그라운딩 게이트를 통과하는가"가
이번에 처음 실행됐다. Exp-3 실행 전 고칠지, SFC 카테고리를 Tier A에서도
"현재는 100% grounding_reject"로 해석하고 넘어갈지는 아직 미정.

**진행 상황 (2026-08-13):** `experiments/exp3/`(`run.py`/`score.py`/
`e3_evaluation.py`/`select_tier_b_cases.py`) 스캐폴딩 + 코어 kwarg 확장까지
코드는 완료(PR #39), 전부 mock으로 검증됨. 실 LLM 파일럿(`--limit 30`)과 전수
실행은 아직 안 함 — qwen/qwen3-8b 확정, 추가 모델(Qwen3.5 9B/Llama 3.1 8B/
Granite 4.1 8B 검토 중) slug 미정.

### 실행 로깅 (Stage 2 직후 착수해도 무방)

`research/safe_intent_sdn/run_context.py` + `schema.py`가 이 영역의 정본이다 -
JSON Schema 자동생성, secret redaction, 동시성에서 연구 트랙이 우월하다. 제품 구현의
로그 필드를 흡수한다.

---

## 이식하지 않는 것

| 원본 | 이유 |
|---|---|
| `src/xai_pipeline/api.py` + UI | Stage 9에서 `src/tiger_sdn/api/{app.py,static/}`로 포팅 완료(2026-08-13) — 그대로 이식이 아니라 스코프를 트림한 재설계: RAG, 커스텀 토폴로지 에디터, 라이브 트래픽 프리셋 시뮬레이터, Digital Twin 패킷 애니메이션 오버레이, flow-state 캐시/Saved State 탭을 뺐다. `orchestrate.pipeline.run_pipeline()` 위에 얇은 SSE 레이어만 얹는 구조라 원본의 Stage1~6 인라인 오케스트레이션과도 다르다 |
| `src/xai_pipeline/main.py` | Repair Loop만 승격, 나머지 버림 |
| `src/xai_pipeline/evaluate.py` | Exp-1/Exp-2가 대체 |
| `stage1_intent/rag.py` | 매 요청 전량 재구축(임베딩 API 수백 회) - 이식이 아니라 재작성 필요 (이슈 #10) |
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
- [x] **Stage 3.** 프롬프트 단일 출처화 ← 여기까지 1차 목표 (완료, 이슈 #5)
  - `prompts/intent_ir.md`(T-B/C/D), `prompts/direct_flow.md`(T-A) — `SYSTEM_PROMPT`/
    `SYSTEM_DIRECT_FLOW` 문자열을 그대로 텍스트만 추출, 내용 변경 없음(과거 커밋된
    로그/리포트와 무관 — 재실행 없이 포팅만 했으므로 회귀 테스트 영향 없음).
  - `prompts/registry.py` — `output_format`("direct_flow" | "intent_ir") -> 프롬프트
    파일 매핑. `experiments/exp1/run.py`가 이걸 통해서만 프롬프트를 읽는다.
  - `experiments/exp1/run.py` — `research/experiments/eval/run_exp1.py`를 포팅.
    `SYSTEM_PROMPT`/`SYSTEM_DIRECT_FLOW` 하드코딩 제거, `prompts.registry.get_prompt`
    사용. `ROOT` 깊이와 `--output` 기본값을 새 레포 경로에 맞게 조정.
  - `src/tiger_sdn/config.py` — `xai_pipeline/config.py`를 트림. `run.py`가 실제
    쓰는 LLM 자격증명/엔드포인트(`GOOGLE_API_KEY`, `OPENROUTER_*`, `LLM_*`)만 남기고
    API 서버/ONOS/데이터셋 경로 폴백은 제거(해당 서브시스템 이식 시 재검토).
  - `experiments/exp1/config/T-{A,B,C,D}-openrouter.toml`의 `dataset_path`/
    `topology_path`/`demos_path`를 `research/experiments/eval/data/...`(원본 경로,
    Stage 2 포팅 시 갱신 누락)에서 `data/gold/...`(이 레포의 실제 경로)로 수정 —
    `run.py`가 이 값을 그대로 읽어 파일을 여니 안 고치면 동작하지 않았다.
  - `tests/test_prompt_registry.py` 신규 — registry 대응표 고정, 프롬프트 파일
    sha256 해시 스냅샷, 코드베이스 전체에서 프롬프트 헤더 문구 리터럴 재등장 여부
    검사. `pytest` 13개 전부 초록(기존 8개 + 신규 5개).
  - 완료 기준 충족: 코드베이스 전체에 프롬프트 문자열 리터럴 0건.
- [ ] **마감 전 병행 작업** (논문 서술, SFC 조사, rep 확대, 라벨 수정) — 우선순위
  아님(2026-08-10 방향 전환), 코어 이식과 병행 가능
- [ ] **KICS 논문 마감 2026-08-24**
- [x] **Stage 4.** Intent IR
  - "확인된 사실 1" 해결: `feat/unify-ir` 브랜치의 미커밋 `src/sdn_intent/`가
    실제로는 로컬 워킹 디렉토리에 그대로 남아 있었다(삭제되지 않음). 원본
    레포에는 쓰지 않는다는 규율에 따라 커밋/푸시하지 않고, 파일 내용을 그대로
    읽어 `src/tiger_sdn/ir/`로 포팅했다 — `sdn_intent` -> `tiger_sdn` 패키지명
    변경 외 내용 변경 없음.
  - `model.py`/`prediction.py`/`adapter.py`/`ir/__init__.py`를 이식. 알려진
    함정(require_identity 완화, intent_type/action 2축 분리, egress_port 문자열
    정규화, SFC waypoints 매핑, None 필드 제거)은 원본 코드에 이미 반영되어
    있었음을 확인.
  - `tests/test_ir_gold350.py` 신규 — accepted 300건 전량 로드 실패 0, rejected
    50건 로드 실패 0, 연구 스키마 왕복 변환 무손실(egress_port str->int, ip에
    /32 부착 두 정규화 제외 완전 일치) 확인. `pytest` 17개 전부 초록(기존 13개
    + 신규 4개).
  - 완료 기준 충족: accepted 300건 전량 로드, 실패 0.
  - 미이식: 컴파일러/검증기/twin(Stage 5-7)에서 IR을 실제로 소비하는 지점 —
    IR 계층 자체의 포팅과 검증만 이번 범위.
- [x] **Stage 5.** 컴파일러
  - 계획 당시 "베이스"였던 `xai_pipeline` 컴파일러를 직접 포팅하지 않고
    `research/safe_intent_sdn/compiler.py`의 설계와 통합해 새로 작성 — 실제
    GOLD-350을 세어보니 accepted 408규칙 중 217건(53%)이 `enforcement` 자체가
    없어 research 쪽의 엄격한 필수 요구를 그대로 쓰면 완료 기준을 채울 수
    없었다. 상세 이유는 Stage 5 절 "직접 포팅이 아닌 이유" 참고.
  - `src/tiger_sdn/compile/{onos.py,compiler.py,__init__.py}` 신규.
    `CompilationError`, host->ip 엔드포인트 해석은 research에서 흡수.
    device/egress_port 미지정 시 관대한 폴백(switch 1 / NORMAL 포트)은
    xai_pipeline에서 흡수 — 명시적 거부로 바꾸는 일은 Stage 6으로 위임.
  - "미해결 버그"(`waypoints[0]`만 써서 multi-switch SFC가 단일 홉으로
    뭉개짐)는 GOLD-350의 `from_research()` 경로에서 애초에 재현되지 않음을
    확인 — N홉 체인이 컴파일러가 아니라 IR 단계에서 이미 N개 규칙으로
    펼쳐져 있다.
  - `tests/test_compiler_gold350.py` 신규 — accepted 300건 컴파일 에러 0
    포함 7개 테스트. `pytest` 24개 전부 초록(기존 17개 + 신규 7개).
  - 완료 기준 충족: gold accepted 300건이 컴파일 에러 없이 통과.
- [x] **Stage 6.** 정적 검증 (2축 병합, 이슈 #13)
  - 대체가 아니라 병합: `stage3_static/{schema_validator,conflict_detector,
    static_validator}.py`(ONOS FlowRule 스키마 + 5종 충돌 탐지, 컴파일 후
    단계)와 `research/safe_intent_sdn/validator.py`의 `TopologyInventory`
    그라운딩(컴파일 전, IR 단계)을 각각 `src/tiger_sdn/verify/`로 포팅해
    한 패키지에 담았다 — 파이프라인상 두 게이트가 서로 다른 시점(IR 단계 vs
    FlowRule 단계)에서 돈다는 점은 그대로 유지.
  - `verify/{topology,grounding,schema,conflict,static}.py` 신규.
    `grounding.py`는 research의 필드 이름(`ingress_port`/`source_port`/
    `destination_port`/`enforcement.avoid_device`)을 통합 IR의 이름
    (`in_port`/`src_port`/`dst_port`/`routing.avoid_device`)에 맞춰 옮겼다.
    `_check_path_constraints`(program 레벨 `sfc_chain`)는 포팅하지 않음 —
    통합 IR은 SFC 웨이포인트를 규칙별 `enforcement`/`sfc_role`로 이미
    펼쳐 놓으므로 그 개념 자체가 없다(Stage 5 컴파일러 절 참고).
  - "함께 처리" 3건 모두 반영:
    1. compound 내부 충돌 검사(`static.py`)가 IPV4_SRC/IPV4_DST를 딕셔너리
       완전 일치로만 비교하던 것을 외부 탐지와 같은 `conflict.ip_overlaps`로
       통일 — `10.0.0.0/24`와 `10.0.0.5/32`처럼 문자열은 다르지만 실제로
       겹치는 CIDR 쌍을 이제 잡는다.
    2. `enforcement.device` 미지정 시 컴파일러가 조용히 기본 스위치로
       폴백하던 것(Stage 5)을 정적 검증에서는 `missing_device`로 명시
       거부하도록 통일 — `grounding._check_references`에 추가.
    3. `schema_validator`/`conflict_detector`/`static_validator`/
       `validator.py` 넷 다 원본에 테스트가 0개였다 —
       `tests/test_verify_gold350.py` 신규로 전부 커버.
  - 이식 중 발견해 함께 고친 버그: `static._check_intra_conflicts`가
    `f.get("treatment", {}).get(...)`로 action을 읽었는데, block 규칙은
    `treatment` 키는 있지만 값이 `None`이라 `AttributeError`로 죽었다 —
    정확히 forward-vs-block 상반 액션을 잡으려는 검사가 block 규칙 자체에서
    크래시하는 셈이었다. `(f.get("treatment") or {})`로 수정.
  - 그라운딩 병합 후 발견해 별도 커밋으로 고친 버그: `grounding._check_conflicts`가
    `shadowed_rule` 판정에서 규칙 인덱스를 곧 priority로 가정했다 — 명시적
    `rule.priority`가 인덱스 순서를 뒤집으면(더 나중 규칙이 더 높은 priority)
    거꾸로 된 오탐(`shadowed_rule`)을 냈다. 컴파일러(`compile/compiler.py`의
    `compile_prediction`)와 동일한 유효 priority 계산(`rule.priority` 우선,
    없으면 `priority_start - index * priority_step`)으로 우열을 가리도록
    수정 — `tests/test_verify_gold350.py`에 회귀 테스트 추가.
  - `tests/test_verify_gold350.py` 신규 — 미지 host/ip/device 거부,
    device 미지정 명시 거부(GOLD-350 accepted의 실제 enforcement-누락 규칙
    포함), egress_port 범위 초과 거부, shadowed_rule 충돌(명시적 priority가
    인덱스 순서를 뒤집는 경우 포함), FlowRule 스키마 검증, 5종 충돌 탐지
    각각, compound CIDR 겹침 버그 재현 20개. `pytest` 43개 전부 초록(기존
    24개 + 신규 19개).
  - 완료 기준 충족: 그라운딩 검증(`verify_program`)이 미지 host/ip/device를
    거부.
- [x] **Stage 7.** Digital Twin (Stage 6과 별도 브랜치에서 병행 진행 — 정적
  검증기 유무와 무관하게 twin 자체는 컴파일된 flow를 그대로 소비하므로 순서
  의존성이 없었다.)
  - 베이스: `sdn-intent-framework`의 `research/safe_intent_sdn/twin/`
    (`twin_verifier.py` 1100 LOC, `topology.py`, `traffic_generator.py`,
    `bandwidth.py`) + `onos_client.py`. 흡수: `bandwidth.py`의 `meets_target()`
    판정(계획대로 원본에 이미 있었음 — iperf3가 측정만 하고 판정을 안 하던
    문제는 이 함수가 이미 해결한 상태였다).
  - `src/tiger_sdn/twin/{twin_verifier,topology,traffic_generator,bandwidth}.py`,
    `src/tiger_sdn/backends/onos.py` 신규. `OnosClient`/`OnosError`는 계획된
    목표 구조대로 `twin/`이 아니라 `backends/onos.py`로 분리했다 — Stage 5의
    `compile/onos.py`(FlowRule 스키마)와 이름은 겹치지만 역할이 다르다(배포
    "클라이언트" vs 스키마 "정의").
  - `_extract_intent_specs`를 통합 컴파일러 출력 형태에 맞게 재작성했다 —
    원본은 `xai_pipeline` 컴파일러의 `sub_rules` 중첩 구조를 가정했지만
    `tiger_sdn.compile.compile_prediction()`은 `{"flows": [f1, ..., fN]}`로
    항상 평평하게 내므로, 원본 그대로 쓰면 컴파운드 예측(규칙 2개 이상)에서
    첫 flow 이후를 전부 놓쳤을 것이다. flow 하나당 intent_spec 하나로 재작성.
  - `verify()`가 dict뿐 아니라 `OnosFlowSet`(pydantic)도 받도록 확장 —
    `compile_prediction()`의 반환값을 바로 넘길 수 있다.
  - `docs/plan.md` Stage 7에 명시된 pitfall(`ovs-ofctl` 명령을 f-string
    조립에서 인자 리스트로) 반영 — `_install_steering`/`_remove_steering`이
    이제 `net.get(hop).cmd("ovs-ofctl", "add-flow", hop, shlex.quote(match), ...)`
    형태로 각 필드를 별도 인자로 넘기고 `match`는 `shlex.quote()`로 방어한다.
  - `tests/test_twin.py` 신규 — 대역폭 판정(`meets_target`/`_parse_mbps`),
    토폴로지 헬퍼(`get_expected_device_ids`/`get_test_host_pairs`), 실제
    GOLD-350 규칙을 컴파일한 flow에서 `_extract_intent_specs`/`_egress_port`가
    forward/block/compound 각각 올바른 intent_spec을 뽑는지, 플랫폼 게이팅
    (`_check_platform`/`verify()`가 Mininet/ONOS 없이도 `status="skipped"`로
    안전하게 반환하는지)을 검증한다. Mininet+ONOS 실배포 자체(`verify()`의
    본 경로)는 이 개발 환경에도 CI(ubuntu-latest, non-root, mn 미설치)에도
    없어 실행할 수 없으므로 테스트 대상에서 제외 — 플랫폼 요구사항은
    `_check_platform()`을 통해 항상 검증 가능한 형태로 게이팅되어 있다.
    Stage 6과 합류(main) 후 `pytest` 59개 전부 초록(합류 시점 main 43개 +
    신규 16개).
  - 완료 기준 충족: `meets_target()`이 QoS 대역폭의 pass/fail 판정을 반환한다
    (`bandwidth.py`, `tests/test_twin.py`에서 검증).
  - **실검증 도구 이식 (2026-08-10, 병합 이후 추가).** `tests/test_twin.py`는
    순수 로직만 검증하고 `verify()`의 본 경로(Mininet 기동, ONOS 배포,
    reachability 프로브, rollback)는 이 개발 환경에도 CI에도 실행 조건이
    없어 한 번도 exercise된 적이 없었다 — `docker compose`가 아니라
    `sdn-intent-framework`의 `research/scripts/`가 쓰던 방식(ONOS만
    `docker run --network host` 컨테이너 1개, Mininet/OVS는 호스트에 네이티브
    설치)을 그대로 `scripts/{installation/{setup,doctor}.sh,onos.sh,
    smoke_test.sh,start_mn_single3.sh}`로 포팅했다(Ubuntu 22.04도 지원하도록
    확장, 컨테이너 이름을 `tiger-sdn-onos`로 변경). `scripts/twin_smoke.py`
    + `twin_smoke_test.sh`는 신규 — `research/scripts/e3_twin_smoke.sh`와
    같은 역할(twin_verifier를 실 컨트롤러에 대고 exercise)이지만, 그 스크립트가
    의존하는 E3 하네스(`experiments/e3/`, 미이식)가 없어도 되도록 Stage 5
    컴파일러로 그 자리에서 만든 작은 FlowRule(forward/block) 2개를 Stage 7
    `TwinVerifier`에 직접 넘기는 방식으로 새로 작성했다.
  - **라이브 검증 완료 (같은 세션, WSL2 Ubuntu 22.04).** 기존 `xai-sdn-onos`
    컨테이너(포트 6653/8101/8181 점유)를 내리고 `tiger-sdn-onos:2.7.0`을 새로
    띄웠다. `.venv` 충돌은 `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/tiger-sdn`로
    레포 밖에 WSL 전용 venv를 둬서 피했다. `sudo ./scripts/twin_smoke_test.sh`
    까지 실제로 실행해 forward/block 두 케이스 모두
    `baseline_connectivity`/`intent_check`/`regression` 전부 PASS — Stage 7
    코드가 처음으로 실 ONOS+Mininet에 FlowRule을 배포하고 도달성을 확인하고
    롤백까지 정상 완료함을 확인했다.
  - **과정에서 찾아 고친 문제 3건** (전부 `scripts/`, twin_verifier 자체는
    무결):
    1. `doctor.sh`가 `UV_PROJECT_ENVIRONMENT`를 몰라서 실제로는 정상인
       환경도 FAIL로 오탐 — `<repo>/.venv` 대신 그 변수를 우선 확인하도록 수정.
    2. **실제 사고.** `sudo` 없이 `-E`를 안 붙이고 `twin_smoke_test.sh`를
       실행하면 `UV_PROJECT_ENVIRONMENT`가 사라져 `uv run`이
       `<repo>/.venv`(Windows에서 빌드된 그 venv, `/mnt/c/...` 위에 있으므로
       WSL에서도 같은 경로)로 폴백한다 — 실제로 이 경로로 Windows `.venv`가
       삭제되고 Linux/Python 3.14로 재생성되어 Windows 쪽 개발 환경이 한 번
       깨졌다(`uv sync`로 복구). `require_project_environment()`를 추가해
       `PROJECT_ROOT`가 `/mnt/*` 위인데 그 변수가 없으면 추측하지 않고
       명시적으로 거부하도록 고쳤다 — 이런 문서화만으로는 막지 못한 사고였다.
    3. `scripts/twin_smoke.py`의 두 번째 케이스가 `block h2->h3`였는데,
       `twin.topology.get_test_host_pairs()`가 다이아몬드 토폴로지 기본값에서
       `h2<->h3`를 "테스트 중인 인텐트와 무관하게 항상 뚫려 있어야 할 쌍"으로
       하드코딩해 둔 것과 충돌 — 라이브로 돌려보니 `intent_check=True`(차단
       자체는 정확)인데 `regression=False`(무관한 쌍이 막혔다고 오판)로
       FAIL이 났다. TwinVerifier의 버그가 아니라 스모크 테스트가 예약된 쌍을
       건드린 설계 실수였다 — 두 케이스 모두 `h1<->h4`로 바꿔 해결.
- [x] **Stage 8.** Exp-2 (최종, 2026-08-11)
  - 착수 시 결정 사항(위 Stage 8 절 참고): sfc/reroute 확장 포함, "파싱"은
    고정 IR conformance로 해석(LLM 없음), `orchestrate/pipeline.py`(Repair
    Loop 승격)는 Stage 9로 이연.
  - `src/tiger_sdn/verify/grounding.py`에 `path` `FindingCategory`와
    SFC 체인 연속성/순서·reroute `avoid_device` 검사 이식(원본
    `research/safe_intent_sdn/validator.py:181-296`). 통합 IR엔
    `IntentProgram.sfc_chain`이 없으므로, `from_research()`가 sfc 규칙마다
    복제해 두는 `routing.waypoints`에서 체인을 복원하는 방식으로 재설계했다
    (`_sfc_chain_of`). `tests/test_verify_gold350.py`에 6개 신규 테스트.
  - `experiments/exp2/data/`에 토폴로지 2종 포팅
    (`research/experiments/e1/data/{topology,topology_diamond}.json`) —
    각각 `cases.jsonl`/`cases_sfc_reroute.jsonl` 전용. 유일한 변경은 마스크
    없는 IPv4 별칭마다 `/32` 형태 추가(안 하면 `EndpointRef`의 자동 정규화와
    어긋나 정상 케이스도 `unknown_ip`로 오탐).
  - `cases.jsonl`(48건)/`cases_sfc_reroute.jsonl`(65건)을 바이트 동일하게
    고정 픽스처로 포팅. 생성기(`build_dataset.py`/`build_sfc_reroute_dataset.py`)는
    이식하지 않음(위 "착수 시 결정 사항" 참고).
  - `research/safe_intent_sdn/e2_evaluation.py`(`E2Case`/`E2Result`,
    `validate_results`/`score_treatment`/`compute_validator_overhead`)를
    `experiments/exp2/e2_evaluation.py`로 이식 — `tiger_sdn.ir`/
    `tiger_sdn.verify`로 임포트만 교체, 채점 로직은 그대로.
    `load_cases()`를 신규 추가해 연구 스키마 그대로인 커밋 파일을
    `from_research()`로 통합 IR로 변환하는 다리 역할을 하게 했다(원본은
    데이터 파일 스키마가 애초에 자기 모델과 같아서 이 단계가 없었다).
  - `research/experiments/e2/{run_validation,score}.py`를
    `experiments/exp2/{run,score}.py`로 이식 — `safe_intent_sdn.compiler`/
    `validator` 대신 `tiger_sdn.compile`/`verify` 사용.
  - 완료 기준 충족: `experiments/exp2/reports/{summary,sfc_reroute_summary}.json`
    생성 확인. B2(검증기+컴파일러)가 두 데이터셋 모두에서 정오탐 100%
    (48건: `any_defect` precision/recall 1.0/1.0, 25/25건 거부; sfc/reroute
    65건: 1.0/1.0, 13/13건 거부, `path` 카테고리 단독으로도 1.0/1.0,
    `code_mismatch_cases` 0건) — B1(컴파일러만)은 각각 4/25, 0/13건만 거부해
    검증기의 실질적 기여가 드러난다. `experiments/exp2/logs/`에 첫 실행
    로그를 커밋(하드 규율 #2 — LLM은 안 쓰지만 인용될 실행이므로 동일하게
    취급). `tests/test_exp2_regression.py` 신규(4개): 고정 로그
    재채점이 커밋된 리포트와 완전 일치하는지, 매번 새로 실행해도(타이밍만
    바뀜) B2 정오탐 100%가 유지되는지 — grounding.py/compiler.py 회귀를
    잡는 실질적 안전망. Stage 8 합류 후 `pytest` 69개 전부 초록(합류 시점
    main 65개 + 신규 4개).
- [ ] **Stage 9.** 웹 UI (신규) — 코드/테스트 완료, 브라우저 수동 확인만 남음
  - [x] **9.1 파서/파이프라인 코어** 완료 (2026-08-11, PR #35).
  - `src/tiger_sdn/parse/parser.py`: `sdn-intent-framework`의
    `stage1_intent/intent_parser.py` 구조를 베이스로 이식하되 RAG는 빼고
    (이미 "이식하지 않는 것" 결정 사항), 토폴로지 그라운딩도 파서 안에서
    안 한다(Stage 6 `verify.grounding.verify_program()`이 대신함). LLM
    출력은 `prompts/intent_ir.md` 스키마 그대로가 GOLD-350 제품 스키마와
    일치해 `ir.adapter.from_gold350()`으로 바로 통합 IR에 태운다.
  - `src/tiger_sdn/parse/llm_client.py`: `experiments/exp1/run.py`의
    `call_llm`/`call_gemini`/`call_openai_compatible`을 베이스로 재이식
    (non-streaming — 원본 `stage1_intent/llm_client.py`는 스트리밍이라
    토큰 사용량을 못 얻어 runctx 로깅에 못 꽂는다). `experiments/exp1/run.py`
    자체는 손대지 않았다(Exp-1은 코어와 분리 유지, CLAUDE.md 규율).
  - `src/tiger_sdn/orchestrate/pipeline.py` + `repair.py`: 원본 `main.py`의
    Repair Loop(파싱->컴파일->정적검증 최대 `MAX_REPAIR_ATTEMPTS`회 재시도,
    twin은 루프 밖 1회)를 승격(Stage 8 "착수 시 결정 사항"에서 이연됐던
    부분). XAI 설명/ONOS 실배포(원본 stage5/6)는 범위 밖 — 결정
    (APPROVE/APPROVE_WITHOUT_TWIN/REJECT/ERROR)까지만 낸다. 그라운딩(IR
    단계)과 정적 검증(FlowRule 단계)을 파이프라인이 명시적으로 두 게이트로
    돌리고, 게이트별로 다른 repair feedback 생성기(`repair.py`)를 쓴다 —
    원본의 단일 `build_repair_feedback`과 다른 점. `run_context`를 넘기면
    각 게이트를 `runctx.RunContext.stage()`로 감싸고 아티팩트를 저장한다.
  - 배선 중 `verify/schema.py`에서 실버그 발견: `isPermanent`를 `str`로만
    받게 돼 있었는데 `compile/compiler.py`가 실제로 채우는 값은 파이썬
    `bool`(`True`)이라 `compile_prediction()` 결과를 그대로 정적 검증에
    태우면 항상 거부됐다(손으로 만든 dict 픽스처만 테스트해 와서 Stage 6
    완료 이후로도 안 드러났음). Stage 7 라이브 twin 검증이 실 ONOS에
    `isPermanent: true`(JSON boolean)를 성공 배포한 전례가 있어 `bool |
    str`로 넓혀 고쳤다(문자열 표기도 계속 받음).
  - `scripts/run_pipeline.py`: `run_pipeline()` 하나를 CLI로 돌려보는
    스모크 스크립트(원본 `main.py`의 6단계/RAG/XAI/배포를 그대로 포팅한
    게 아니라 이번 파이프라인 전용으로 새로 작성) — 웹 UI 없이도 눈으로
    확인하는 용도. 실제로 LLM을 호출하는 첫 실사용 지점이라(지금까지는
    커밋된 로그 재채점만 해 왔음) Gemini 기본 모델을 쓰려면
    `google-genai`가 추가로 필요하다는 게 이번에 드러남(pyproject.toml
    기본 의존성에는 없음).
  - `tests/test_parse.py`(11개), `tests/test_orchestrate_pipeline.py`(9개)
    신규 — LLM 호출은 목으로 대체. `pytest` 105개 중 104개 초록(main 85개 +
    신규 20개; 나머지 1개는 `test_runctx.py`의 기존 Windows 경로 구분자
    문제로 이번 변경과 무관하게 원래도 실패 — stash로 확인함).
  - [x] **9.2 FastAPI 앱** 완료 (2026-08-13). `src/tiger_sdn/api/app.py` —
    `POST /api/run`(SSE, `orchestrate.pipeline.run_pipeline()`의 `on_event`
    콜백을 큐로 그대로 흘림), `GET /api/topology`(ONOS 실시간 조회, 실패 시
    `data/gold/topology_eval.json` 폴백 — twin 기본 다이아몬드와 dpid가
    맞아떨어지는 평가용 정본 인벤토리라 별도 픽스처를 새로 만들지 않았다),
    `GET`/`DELETE /api/logs`(`runctx` 매니페스트 글롭 — 손으로 만든 JSON
    덤프가 아님). `explain/decision.py`(`build_decision`)와
    `deploy/deployer.py`(`Deployer`)를 이번에 함께 이식 — Stage 6 이후
    "장기 보류"였던 `explain/`/`deploy/` 자리를 채웠다. `tiger_sdn.twin.
    TwinVerifier.verify()`에 `progress_cb` 훅, `orchestrate.pipeline.
    run_pipeline()`에 `on_event` 훅을 추가(둘 다 additive, 기존 호출부/
    테스트 영향 없음). `tests/test_api.py`(10개), `tests/test_explain_decision.py`
    (9개), `tests/test_deploy.py`(4개) 신규.
    - **버그 발견 및 수정 (9.3 포팅 중 교차 검증으로 발견):**
      `run_pipeline()`이 반환 직전 자체 `"done"` 이벤트를 먼저 내보내는데,
      `api_run()`의 SSE 스트림이 "done" 문자열이 보이면 즉시 종료하도록
      짜여 있어 그 뒤에 나오는 `"decision"` 이벤트/`deploy` 스테이지/
      run_id 있는 최종 `"done"`이 전부 유실됐다. 메시지 내용이 아니라
      "백그라운드 스레드가 끝나고 큐가 실제로 빌 때"로 종료 조건을 바꿔
      고쳤다.
  - [x] **9.3 정적 UI 포팅** 완료 (2026-08-13). `sdn-intent-framework`의
    `src/xai_pipeline/static/{index.html,app.js,style.css}`(합계 5404줄)를
    `src/tiger_sdn/api/static/`로 포팅, TIGER-SDN으로 리브랜딩. 위 "착수
    시 확정한 스코프 결정"대로 RAG/에디터/라이브 트래픽 프리셋/twin 패킷
    애니메이션 오버레이(백엔드가 구조화된 `twin_info`/`twin_bw` 이벤트를
    안 주므로 데이터 소스 자체가 없음)/flow-state 캐시를 뺐다 —
    2668줄(index.html 236 + app.js 1305 + style.css 1130)로 트림. 스테이지
    카드가 원본의 숫자 스테이지(`ev.stage: 1..6`)가 아니라 백엔드의 실제
    문자열 스테이지(parse/grounding/compile/static_validation/twin/deploy)로
    키잉되도록 재설계 — 필드 접근은 `pipeline.py`/`app.py`/각 모델 정의를
    직접 대조해 하나하나 맞췄다(추측 없음). 위 SSE 종료조건 버그는 이 작업
    중 교차검증으로 발견.
    - **검증 범위와 한계:** 이 작업을 수행한 에이전트 환경엔 브라우저
      자동화 도구가 없어 실제 시각/조작 확인은 못 했다. 대신 확인한 것 —
      실 uvicorn으로 정적 파일 HTTP 서빙(200, 올바른 content-type),
      `node --check`로 `app.js` 문법 검증, `app.js`의 모든
      `getElementById`/`onclick` 대상과 `index.html`의 실제 id/핸들러
      전수 대조(누락 0건), `app.js`가 참조하는 CSS 클래스가 `style.css`에
      전부 정의돼 있는지 대조. `pytest` 전부 초록.
  - [x] **9.4 테스트 편의 환경** 완료 (2026-08-13, 이슈 #31). `scripts/
    dev_server.sh` 신규 — `scripts/onos.sh start` + `uv run uvicorn
    tiger_sdn.api.app:app --reload`를 한 커맨드로. Digital Twin은 root가
    필요하지만(`TwinVerifier._check_platform()`) root 없이 실행해도 서버는
    정상 동작하고 twin만 `status="skipped"`로 표시 — `sudo -E env
    "PATH=$PATH" ./scripts/dev_server.sh`로 실제 twin 검증까지 가능.
    실 Docker/ONOS 대상으로 end-to-end 확인(`GET /api/topology`가 정적
    폴백이 아니라 라이브 ONOS 장치 데이터를 반환하는 것까지 확인).
    `scripts/README.md`에 `NOPASSWD:ALL`이 아니라 두 진입점 스크립트
    (`dev_server.sh`/`twin_smoke_test.sh`)로 좁힌 sudoers 예시 문서화 —
    Mininet의 Python API가 개별 서브프로세스가 아니라 프로세스 전체의
    root 권한을 요구하므로 "명령 하나하나"가 아니라 "진입점 스크립트"가
    실제로 의미 있는 최소 범위. `.env.example`은 Stage 3에서 이미 ONOS/
    API 서버 변수를 다 갖추고 있어 변경 불필요.
    - **부수 발견:** `dev_server.sh` 작성 중 `scripts/twin_smoke_test.sh`의
      `require_project_environment()`에서 동일한 실버그 발견 — bash의
      `[[ cond ]] || return`은 `return`에 인자가 없으면 실패한 테스트의
      종료 코드(1)를 그대로 물려받는데, `set -e` 아래서 이 함수를 최상위
      문장으로 호출하면 "아무 문제 없음" 경로에서도 스크립트가 조용히
      죽는다(실제로 `dev_server.sh`에서 이 방식으로 재현됨: 비root +
      `UV_PROJECT_ENVIRONMENT` 미설정 + `/mnt` 밖 — 가장 흔한 조합). 두
      스크립트 모두 `return 0`으로 명시해 고쳤다.
- [x] **실행 로깅** (완료, 이슈 #16)
  - `research/safe_intent_sdn/run_context.py` + `schema.py` -> `src/tiger_sdn/runctx/
    {run_context,schema}.py`. 이벤트 스트림/매니페스트/아티팩트 버저닝/secret
    redaction 로직은 변경 없이 그대로, `RunContext`가 받던 `AppSettings`(TOML +
    pydantic-settings, 이 레포엔 없음)만 걷어내고 필요한 값(model_name,
    prompt_version, log_dir, feature_flags, secret_values 등)을 생성자 키워드
    인자로 직접 받게 바꿨다. `schema.py`의 import는 `tiger_sdn.ir.prediction`/
    `tiger_sdn.compile.onos`로 교체.
  - "제품 구현의 로그 필드 흡수"(이슈 원문) 확인 결과: `xai_pipeline`의
    `main.py`/`api.py`가 손으로 채우던 `pipeline_result`/`result` dict(stage1~6 +
    decision)는 `RunManifest`의 아티팩트 슬롯(`input_intent`/`generated_ir`/
    `compiled_policy`/`static_validation`/`twin_test_results`/`repair_history`)이
    이미 stage1~4 + repair loop를 커버한다 — 추가로 흡수할 필드 없음. `rag_k`(RAG)는
    이식 대상에서 제외됐고, stage5/6(XAI 설명·실배포)는 이 시점엔 장기
    보류라 대응 슬롯이 없는 게 맞았다(이후 Stage 9에서 `explain/`/`deploy/`로
    이식됐지만, `RunManifest`엔 여전히 대응 슬롯이 없다 — 그 결과는
    `runctx`가 아니라 `api/app.py`가 SSE `"decision"`/`deploy` 스테이지
    이벤트로 직접 실어 나른다).
  - `tests/test_runctx.py` 신규(16개, 원본 `research/tests/test_config_and_logging.py`
    포팅 — `AppSettings` 로딩 테스트만 제외). `pytest` 전부 초록(85개).
- [ ] **전체 이식 완료 후 실험 재진행** (범위/일정 미정)
