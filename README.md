# TIGER-SDN

자연어 네트워크 의도(intent)를 ONOS FlowRule로 변환하는 파이프라인,
그리고 그 과정을 검증하고 설명하기 위한 평가 프레임워크.

```
자연어 의도 -> Intent IR -> 컴파일러 -> 정적 검증 -> Digital Twin -> 배포
```

핵심은 LLM이 만든 의도 표현을 결정론적으로 검증한 뒤에만 데이터플레인에 반영하는 것이다.
Intent IR이 LLM 출력과 실제 플로우룰 사이의, 검증 가능한 중간 표현 역할을 한다.

---

## 구성 요소

- **Intent IR** - LLM 출력을 담는 엄격한 중간 표현(pydantic, `extra="forbid"`). forwarding,
  security, qos, sfc, reroute를 단일 스키마로 표현한다.
- **컴파일러** - Intent IR을 결정론적으로 ONOS FlowRule로 변환한다.
- **정적 검증** - 컴파일 전 충돌 탐지와 토폴로지 그라운딩으로 존재하지 않는 엔티티, 충돌하는
  규칙을 거부한다.
- **Digital Twin** - Mininet/ONOS 기반으로 배포 전 플로우 동작을 시뮬레이션하고 판정한다.

---

## GOLD-350

자연어 의도 350건에 대한, 이중 어노테이션과 조정을 마친 평가 데이터셋.

- **accepted 300건.** 컴파일 가능한 의도. 단일 규칙 250건, 복합 50건
- **rejected 50건.** 거부 사유 4종: `ambiguous`, `contradictory`, `unknown_entity`, `unsupported`
- **카테고리 7종.** forwarding, security, qos, sfc, reroute, compound, ambiguous_unsupported

데이터셋 카드, 어노테이션 가이드라인, 어노테이터 간 일치도 산출 스크립트,
그리고 후보 생성부터 조정까지의 provenance가 함께 제공된다.

---

## 평가

| 실험 | 측정 대상 | 코어 실행 |
|---|---|---|
| **Exp-1** | LLM이 의도를 얼마나 정확한 구조로 표현하는가. T-A/B/C/D 4개 처치 비교 | 의도 파싱만 |
| **Exp-2** | 파이프라인(파싱, 컴파일, 검증) 통과율 | 전체 |

Exp-1의 처치는 출력 형식(Direct FlowRule 또는 Intent IR)과
보강(few-shot, 토폴로지 그라운딩)의 조합이다.

Exp-1은 LLM 호출 없이 재현된다. 인용된 런의 원시 응답 로그를 저장소에 커밋하므로,
채점기만 다시 돌리면 논문의 수치가 그대로 나온다. 외부 의존성 0, 약 2초.

---

## Docker

로컬에 Python 3.11/의존성이 없어도 Docker Compose로 테스트와 Exp-1 재현을 그대로
돌릴 수 있다.

```bash
# pytest 전체
docker compose run --rm test

# Exp-1(T-D) 재현 — LLM 호출 없음, 커밋된 로그 기준
docker compose run --rm exp1-score

# 대화형 셸 (레포가 /app에 마운트됨)
docker compose run --rm app
```

Digital Twin(Mininet/ONOS)은 아직 컨테이너화되어 있지 않다 — 진행 상황은
[`docs/plan.md`](docs/plan.md)를 참고한다.

---

## 문서

- [`docs/plan.md`](docs/plan.md) - 개발 로드맵, 설계 배경, 진행 현황. 이 저장소의 유일한 계획 문서

---

## 출처

`Jangmyun/sdn-intent-framework`, `seongyooo/sdn-xai-pipeline`을 기반으로 한다.

---

## 라이선스

MIT. Copyright (c) Jangmyun, seongyooo
