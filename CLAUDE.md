# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

TIGER-SDN is a pipeline from natural-language network intent to ONOS FlowRules, plus
the evaluation framework used to validate it. It is being ported, file by file and
with understanding, from two source repos: `Jangmyun/sdn-intent-framework` (code of
record) and `seongyooo/sdn-xai-pipeline` (raw experiment logs of record — the two
repos share nearly identical code, but only the latter has the logs). Do not copy
either work tree wholesale, and do not write to either source repo — read from them,
port only what's needed.

**`docs/plan.md` is the single planning document for this repository.** Read it
before starting work. If new investigation or decisions come up, update that
document instead of creating a new one.

## Current stage

Stages 0-3 (repo skeleton, GOLD-350 dataset, Exp-1 regression harness, prompt
unification) are complete and stand as the paper's Exp-1 safety net regardless of
what happens next: it reproduces from committed logs with zero LLM calls, so it
stays reliable no matter how far core porting gets. Only three things affect Exp-1
numbers: the prompt, the scoring logic, and the gold dataset.

**As of 2026-08-10, the plan changed:** core porting (Stage 4-8: IR, compiler,
static verification, digital twin) is no longer deferred until after the KICS
deadline (2026-08-24) — it proceeds immediately, plus a new Stage 9 (web UI, built
fresh against the ported core rather than a straight port of `xai_pipeline`'s
API/UI). The goal is to get the full pipeline working end-to-end, including the web
UI, and re-run experiments against it. Stage 4 (Intent IR) is done — see
`docs/plan.md` for per-stage detail and known pitfalls. `research/paper/`
(figure-generation code in the source repos) still must not be touched before the
deadline; that rule is independent of the Stage 4-8 timeline change.

The Stage 4 blocker is resolved: the verified IR code
(`sdn-intent-framework`'s `feat/unify-ir` branch, `src/sdn_intent/`, 699 LOC) was
never pushed or committed upstream, but it was still sitting, untracked, in that
repo's local working tree — it was read from there and ported (never committed to
the source repo) into `src/tiger_sdn/ir/`.

## Commands

```bash
# Regenerate the scoring-schema dataset from the canonical gold set
# (output must be byte-identical to the committed gold350_eval.jsonl)
python experiments/exp1/convert_gold350.py \
    --input data/gold/gold.jsonl --output data/gold/gold350_eval.jsonl

# Re-score Exp-1 from committed raw logs (no LLM calls, ~2s)
python experiments/exp1/score.py \
    --dataset data/gold/gold350_eval.jsonl \
    --topology data/gold/topology_eval.json \
    --logs experiments/exp1/logs/ \
    --output experiments/exp1/reports/summary.json \
    --treatment T-D --run-id <run_id>

# Run all tests (regression harness + gold dataset checks)
pytest

# Run a single test file
pytest tests/test_exp1_regression.py
```

`--run-id` is required for `score.py` when a treatment has more than one run under
`experiments/exp1/logs/`.

## Architecture

```
natural language intent -> Intent IR -> compiler -> static verification -> Digital Twin -> deployment
```

The core design principle: an LLM produces the intent representation, but nothing
reaches the dataplane until it has been deterministically verified. Intent IR is the
verifiable intermediate representation between LLM output and actual flow rules.

- **Intent IR** — a strict pydantic (`extra="forbid"`) representation of LLM output,
  covering forwarding, security, qos, sfc, and reroute intents in one schema.
- **Compiler** — deterministically turns Intent IR into ONOS FlowRules.
- **Static verification** — pre-compile conflict detection and topology grounding;
  rejects nonexistent entities and conflicting rules.
- **Digital Twin** — Mininet/ONOS-based simulation that verifies flow behavior before
  deployment.

As of this stage, only the evaluation layer (`experiments/exp1/`, `data/gold/`) is
ported; `src/tiger_sdn/` (IR, compiler, verifier, twin) does not exist yet — that's
Stage 4-8.

### Why Exp-1 is decoupled from the core pipeline

`run_exp1.py` (the harness being ported into `experiments/exp1/run.py`) only pulls
`config` and `SYSTEM_PROMPT` from the core — it never touches the IR, compiler, or
verifier. That means the only things that can move Exp-1's numbers are the prompt,
the scoring logic (`experiments/exp1/score.py`), and the gold dataset
(`data/gold/gold.jsonl` and its derivative). The ported core only affects the paper
once it's actually exercised, which happens in Exp-2 (Stage 8) — not before.

### GOLD-350 dataset

`data/gold/gold.jsonl` is the canonical 350-case dataset (300 accepted / 50
rejected across 7 categories: forwarding, security, qos, sfc, reroute, compound,
ambiguous_unsupported), using the research schema (e.g. `source_port`,
`action: "deny"`). `data/gold/gold350_eval.jsonl` is a derived copy in the scoring
schema (e.g. `dst_port`, `action: "block"`) that `experiments/exp1/score.py` actually
scores against. `experiments/exp1/convert_gold350.py` is the only thing that may
regenerate the derived file — never hand-edit `gold350_eval.jsonl`, and never edit
`gold.jsonl` directly either. `docs/ANNOTATION_GUIDELINE.md` is not just
documentation — it's the normative spec behind the converter's IP backfilling,
prompt selector rules, and `topology_eval.json`'s port sets. If you touch the
prompt, check the guideline; if you touch the guideline, check the prompt.

### Exp-1 reproducibility

Exp-1 reproduces without any LLM calls: raw response logs for cited runs are
committed under `experiments/exp1/logs/`, so re-running `score.py` against them
reproduces the paper's numbers in about 2 seconds with zero external dependencies.
`tests/test_exp1_regression.py` re-scores the committed logs and diffs the result
against the committed reports in `experiments/exp1/reports/`, failing on any leaf
mismatch. This is the safety net protecting the paper's numbers — do not let it go
red across a Stage boundary.

## Hard rules

1. **No prompt strings in code.** `prompts/*.md` is the single source of truth (this
   applies once Stage 3 lands `prompts/intent_ir.md` / `prompts/direct_flow.md` /
   `prompts/registry.py`). Violating this has invalidated two prior experiment runs.
2. **Commit raw logs for any run that's cited.** Don't gitignore
   `experiments/*/logs/` — losing E1's raw logs once was permanent.
3. **Never merge to the next Stage with a red regression test**, once Stage 2 is
   done.
4. **Never hand-edit `data/gold/gold.jsonl`.** Only `convert_gold350.py` may produce
   the derived `gold350_eval.jsonl`.
5. **Don't touch `research/paper/`** (figure-generation code, in the source repos)
   before the 2026-08-24 deadline.
6. **Don't delete either source repo** (`sdn-intent-framework`, `sdn-xai-pipeline`).
7. **The package name is `tiger_sdn`**, matching the repo name. The original IR code
   uses `sdn_intent` — when it's ported in Stage 4, rename the package and its
   internal import paths to `tiger_sdn`.

## Known pitfalls (relevant once Stage 4+ starts, details in `docs/plan.md`)

- `gold.jsonl` (canonical) and `gold350_eval.jsonl` (scoring) use different schemas;
  `convert_gold350.py` is the only bridge between them.
- `intent_type` and `action` are different axes — `security` intents can carry
  either `forward` or `block` actions.
- The research IR's `require_identity` (host XOR ip) is incompatible with GOLD-350;
  relax it to "at least one."
- Unspecified fields in gold data are explicit `null` — strip `None` before
  validating against `extra="forbid"` models.
- The compiler only reads `waypoints[0]`, so multi-switch SFC chains compile to a
  single hop. Harmless for Exp-1, a real bug for Exp-2.

## Style

- Python 3.11+. Use `from __future__ import annotations`, type hints, and
  `pathlib`.
- Validation via pydantic v2. Core models use `extra="forbid"`.
- `experiments/exp1/score.py` is stdlib-only — don't add dependencies to it.
- When porting code from the source repos, leave a comment with the original path;
  for files that can't hold comments (data, some docs), put the original path in the
  commit message instead.
- Don't use `§`, `〃`, `·` in documentation — spell things out instead (e.g. "Stage
  4.1").
- Keep commits small — one file and its test per commit.
