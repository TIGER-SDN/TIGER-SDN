# PROVENANCE

TIGER-SDN으로 이식한 파일마다 원본 경로를 기록한다. 매 Stage 끝에 갱신한다.
`docs/plan.md`의 이식 규율(원본 레포에는 쓰지 않는다, 원본 두 레포는 삭제하지
않는다)을 참고.

| TIGER-SDN 경로 | 원본 레포 | 원본 경로 | Stage | 비고 |
|---|---|---|---|---|
| `LICENSE` | `sdn-intent-framework` | `LICENSE.md` | 0 | 그대로 이식 |
| `.env.example` | `sdn-intent-framework` | `.env.example` | 0 | 그대로 이식 |
| `data/gold/gold.jsonl` | `sdn-intent-framework` | `docs/dataset/gold.jsonl` | 1 | 그대로 이식 (정본, 350건) |
| `experiments/exp1/convert_gold350.py` | `sdn-intent-framework` | `research/experiments/eval/convert_gold350.py` | 1 | `ROOT` 상대경로(parents 깊이)와 기본 입출력 경로만 조정, 변환 로직은 동일 — 출력이 원본과 바이트 동일함을 확인 |
| `data/gold/gold350_eval.jsonl` | `sdn-intent-framework` | `research/experiments/eval/data/gold350_eval.jsonl` | 1 | 그대로 이식 (파생본, 350건). 이식한 `convert_gold350.py`를 재실행해 바이트 동일 확인 |
| `data/gold/topology_eval.json` | `sdn-intent-framework` | `research/experiments/eval/data/topology_eval.json` | 1 | 그대로 이식 |
| `data/gold/demonstrations.json` | `sdn-intent-framework` | `research/experiments/eval/data/demonstrations.json` | 1 | 그대로 이식 |
| `docs/ANNOTATION_GUIDELINE.md` | `sdn-intent-framework` | `docs/dataset/ANNOTATION_GUIDELINE.md` | 1 | 그대로 이식 (규범 스펙) |
| `docs/DATASET_CARD.md` | `sdn-intent-framework` | `docs/dataset/DATASET_CARD.md` | 1 | 재작성 — 구성 표/Gold status/Known limitation은 유지, 경로 참조는 이 레포 구조로 교체, provenance 스크립트는 미이식으로 명시 |

**미이식 (Stage 1 "재현성용" 항목, 보류):** `research/experiments/gold/`
(`build_candidates.py`, `build_gold.py`, `compute_agreement.py`,
`annotations/*.jsonl`, `data/candidates.jsonl`, `data/blind/`) — `build_gold.py`와
`build_candidates.py`가 아직 이식하지 않은 `safe_intent_sdn.e1_evaluation.EvaluationCase`를
import한다. Stage 4에서 코어를 이식한 뒤 다시 검토한다.
