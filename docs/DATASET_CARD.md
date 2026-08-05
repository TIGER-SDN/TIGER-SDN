# GOLD-350 Dataset Card

350 English SDN intent instructions, balanced at 50 cases across seven
categories: `forwarding`, `security`, `qos`, `sfc`, `reroute`, `compound`,
and `ambiguous_unsupported`. Semantic gold is an ordered `IntentProgram`
(`EvaluationCase` schema), an independent project annotation under a fixed
4-host/4-switch topology (see `docs/ANNOTATION_GUIDELINE.md` section 1) —
not an official NetIntent or ONOS label.

## Composition

| category | n | status |
|----------|---|--------|
| forwarding | 50 | accepted |
| security | 50 | accepted |
| qos | 50 | accepted |
| sfc | 50 | accepted (>=2 rules, `sfc_chain` set) |
| reroute | 50 | accepted |
| compound | 50 | accepted (>=2 rules) |
| ambiguous_unsupported | 50 | rejected (ambiguous 15, unknown_entity 15, contradictory 10, unsupported 10) |

## Gold status: **adjudicated (LLM double-annotation)**

Gold labels were fixed by independent double labeling plus adjudication:

- Two independent annotator sessions labeled a blind split (instruction text
  only, category and program withheld). Cohen's kappa = **1.000** on category
  (7-way), status, and rejection reason; **0** inter-annotator disagreements.
- Validation against author-intended labels found 2 divergences (both
  multi-hop SFC cases whose wording failed to invoke a service role). These
  were resolved by revising the instruction text to invoke the service
  function; they are marked `source="adjudicated"` in the final labels (the
  other 348 are `unanimous`).

**Known limitation.** Annotators are independent LLM agent sessions sharing
one detailed guideline, not human experts. Kappa = 1.0 demonstrates
reproducibility under a decisive guideline, not human inter-annotator
agreement, and does not replace it. Gold programs depend on the assumed
topology and are not official upstream labels.

## Files

| path | contents |
|------|----------|
| `data/gold/gold.jsonl` | final 350 gold cases (`EvaluationCase` schema, research skema) |
| `data/gold/gold350_eval.jsonl` | derived scoring-schema cases, produced by `experiments/exp1/convert_gold350.py` |
| `data/gold/topology_eval.json` | evaluation-time alias inventory + wiring, used for grounding prompts and scoring normalization |
| `data/gold/demonstrations.json` | few-shot examples (fictitious entities, disjoint from the gold topology) |
| `docs/ANNOTATION_GUIDELINE.md` | v1.0 labeling rules (normative — the converter and prompts derive from it) |

**Not yet ported.** The candidate-generation / blind-annotation / agreement
provenance (`candidates.jsonl`, `blind/instructions.jsonl`,
`annotations/*.jsonl`, `build_candidates.py`, `build_gold.py`,
`compute_agreement.py`) still lives only in `sdn-intent-framework`'s
`research/experiments/gold/`. `build_gold.py` and `build_candidates.py`
import `safe_intent_sdn.e1_evaluation.EvaluationCase`, a core module not
ported to this repo yet (see `docs/plan.md` Stage 4), so porting the scripts
as-is would leave them unrunnable here. Deferred until that dependency is
resolved.

## Reproduce

Scoring-schema conversion (this repo, no LLM calls, ~2s):

```bash
python experiments/exp1/convert_gold350.py \
    --input data/gold/gold.jsonl --output data/gold/gold350_eval.jsonl
```

Candidate generation / annotation / agreement computation still require
`sdn-intent-framework`'s `research/experiments/gold/` scripts (see "Not yet
ported" above).
