"""
Ported from sdn-intent-framework:research/experiments/eval/score_exp1.py (see docs/PROVENANCE.md).

research/experiments/eval/score_exp1.py - Exp-1 Scoring Engine

Scores run_exp1.py output JSONL files against gold dataset.
Produces a structured JSON report with per-treatment, per-category,
and per-repetition metrics.

Usage:
    python research/experiments/eval/score_exp1.py \\
        --dataset research/experiments/eval/data/intents_eval.jsonl \\
        --topology research/experiments/eval/data/topology_eval.json \\
        --logs research/experiments/eval/logs/ \\
        --output research/experiments/eval/reports/summary_exp1.json

    # Score only T-D logs
    python research/experiments/eval/score_exp1.py ... --treatment T-D

    # Include 95% bootstrap CI (slower)
    python research/experiments/eval/score_exp1.py ... --bootstrap
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════
# Constants — slots scored for IntentIR treatments (T-B, T-C, T-D)
# ════════════════════════════════════════════════════════════════════════

SLOT_NAMES = [
    "action",
    "source_ip",
    "destination_ip",
    "protocol",
    "dst_port",
    "device",
    "egress_port",
    "alt_egress_port",
    "queue",
    "min_bandwidth_mbps",
    "max_latency_ms",
    "waypoints",
    "via_device",
    "avoid_device",
]

# Bootstrap seeds: rep i (1-indexed) uses seed 41+i
_BOOTSTRAP_SEEDS = {i: 41 + i for i in range(1, 11)}


# ════════════════════════════════════════════════════════════════════════
# Topology alias map
# ════════════════════════════════════════════════════════════════════════

class AliasMap:
    """
    Maps any alias of a topology entity to its canonical ID.
    e.g. "s1", "switch 1", "switch1", "of:0000000000000001" -> "device:s1"
         "h1", "10.0.0.1", "10.0.0.1/32"                   -> "host:h1"
    """

    def __init__(self, topo_data: dict) -> None:
        self._map: dict[str, str] = {}  # lowercase_alias -> canonical_id
        self._known_ids: set[str] = set()

        for entity in topo_data.get("entities", []):
            cid = entity["id"]
            self._known_ids.add(cid)
            for alias in entity.get("aliases", []):
                self._map[alias.lower()] = cid
                # keep original case too
                self._map[alias] = cid

    def canonical(self, alias: str | None) -> str | None:
        """Return canonical ID for alias, or None if alias is None."""
        if alias is None:
            return None
        a = alias.strip()
        return self._map.get(a.lower()) or self._map.get(a) or a

    def is_known(self, alias: str | None) -> bool:
        """Return True if alias resolves to a known topology entity."""
        if alias is None:
            return True  # null references don't count as hallucinations
        a = alias.strip()
        return (a.lower() in self._map) or (a in self._map)

    def normalize_ip(self, ip: str | None) -> str | None:
        """Strip CIDR mask for comparison: '10.0.0.1/32' -> '10.0.0.1'"""
        if ip is None:
            return None
        return ip.split("/")[0].strip()

    def normalize_device(self, device: str | None) -> str | None:
        """Normalize device to canonical_id."""
        return self.canonical(device)

    def normalize_waypoints(self, waypoints) -> list[str] | None:
        """Normalize each 'device:port' waypoint (normalize device part)."""
        if not waypoints:
            return None
        result = []
        for wp in waypoints:
            wp_str = str(wp).strip()
            # 문자열 전체가 알려진 엔티티 별칭이면 canonical ID로 통일.
            # 포트 없는 waypoint("of:0000000000000002" / "s2" / "switch 2")를
            # 마지막 콜론 분리 방식으로 처리하면 "of:HEX"가 디바이스 "of"의
            # 포트로 오인되어 의미상 동일한 표기끼리도 매칭이 깨진다.
            if wp_str and self.is_known(wp_str):
                result.append(self.canonical(wp_str))
                continue
            # Split on last colon to separate port
            idx = wp_str.rfind(":")
            if idx >= 0:
                dev_part  = wp_str[:idx]
                port_part = wp_str[idx+1:]
                norm_dev  = self.normalize_device(dev_part) or dev_part
                result.append(f"{norm_dev}:{port_part}")
            else:
                result.append(wp_str)
        return sorted(result)


# ════════════════════════════════════════════════════════════════════════
# Gold helpers
# ════════════════════════════════════════════════════════════════════════

def extract_gold_rules(gold: dict) -> list[dict]:
    """
    Return gold rules as a list.
    Single-rule gold (flat): wrap in list.
    Compound gold (has 'rules' key): return as-is.
    """
    if "rules" in gold:
        return gold["rules"]
    return [gold]


def _safe_get(d: dict | None, *keys):
    """Safe nested dict access."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _get_slot_from_rule(rule: dict, slot: str):
    """Extract a slot value from a rule dict (gold or predicted)."""
    if slot == "action":
        return rule.get("action")
    if slot == "source_ip":
        return _safe_get(rule, "selector", "source", "ip")
    if slot == "destination_ip":
        return _safe_get(rule, "selector", "destination", "ip")
    if slot == "protocol":
        return _safe_get(rule, "selector", "protocol")
    if slot == "dst_port":
        return _safe_get(rule, "selector", "dst_port")
    if slot == "device":
        return _safe_get(rule, "enforcement", "device")
    if slot == "egress_port":
        return _safe_get(rule, "enforcement", "egress_port")
    if slot == "alt_egress_port":
        return _safe_get(rule, "enforcement", "alt_egress_port")
    if slot == "queue":
        qos = rule.get("qos")
        return qos.get("queue") if isinstance(qos, dict) else None
    if slot == "min_bandwidth_mbps":
        qos = rule.get("qos")
        return qos.get("min_bandwidth_mbps") if isinstance(qos, dict) else None
    if slot == "max_latency_ms":
        qos = rule.get("qos")
        return qos.get("max_latency_ms") if isinstance(qos, dict) else None
    if slot == "waypoints":
        routing = rule.get("routing")
        return routing.get("waypoints") if isinstance(routing, dict) else None
    if slot == "via_device":
        routing = rule.get("routing")
        return routing.get("via_device") if isinstance(routing, dict) else None
    if slot == "avoid_device":
        routing = rule.get("routing")
        return routing.get("avoid_device") if isinstance(routing, dict) else None
    return None


# ════════════════════════════════════════════════════════════════════════
# Prediction helpers
# ════════════════════════════════════════════════════════════════════════

def detect_pred_status(output: dict | None) -> str:
    """
    Determine predicted status from LLM output.
    Returns: "accepted" | "rejected" | "error"
    """
    if output is None:
        return "error"
    if output.get("status") == "rejected":
        return "rejected"
    if "rules" in output and isinstance(output["rules"], list):
        return "accepted"
    if "flows" in output and isinstance(output["flows"], list):
        return "accepted"   # T-A direct_flow
    # Some LLMs may output flat single rule without "rules" wrapper
    if "action" in output:
        return "accepted"
    return "error"


def extract_pred_rules(output: dict | None) -> list[dict]:
    """Extract the rules list from LLM output (T-B/C/D only)."""
    if output is None:
        return []
    rules = output.get("rules")
    if rules and isinstance(rules, list):
        return rules
    # Fallback: flat single rule without wrapper
    if "action" in output:
        return [output]
    return []


# ════════════════════════════════════════════════════════════════════════
# Rule-pair comparison
# ════════════════════════════════════════════════════════════════════════

def _compare_slot(slot: str, gold_val, pred_val, alias_map: AliasMap) -> bool | None:
    """
    Compare one slot.
    Returns True (match), False (mismatch), or None (gold is null -> skip).
    """
    # Skip slots where gold is null
    if gold_val is None:
        return None
    # Pred is null but gold is not -> mismatch
    if pred_val is None:
        return False

    # IP slots: normalize (strip /32)
    if slot in ("source_ip", "destination_ip"):
        return alias_map.normalize_ip(str(gold_val)) == alias_map.normalize_ip(str(pred_val))

    # Device slots: normalize to canonical ID
    if slot in ("device", "via_device", "avoid_device"):
        return alias_map.normalize_device(str(gold_val)) == alias_map.normalize_device(str(pred_val))

    # Waypoints: normalize each element
    if slot == "waypoints":
        if not isinstance(gold_val, list) or not isinstance(pred_val, list):
            return False
        norm_gold = alias_map.normalize_waypoints(gold_val) or []
        norm_pred = alias_map.normalize_waypoints(pred_val) or []
        return norm_gold == norm_pred

    # Numeric slots: compare as numbers if possible
    if slot in ("dst_port", "egress_port", "alt_egress_port", "queue"):
        try:
            return int(gold_val) == int(pred_val)
        except (TypeError, ValueError):
            return str(gold_val).strip() == str(pred_val).strip()

    if slot in ("min_bandwidth_mbps", "max_latency_ms"):
        try:
            return abs(float(gold_val) - float(pred_val)) < 1e-6
        except (TypeError, ValueError):
            return str(gold_val).strip() == str(pred_val).strip()

    # String slots (action, protocol, etc.): case-insensitive
    return str(gold_val).strip().lower() == str(pred_val).strip().lower()


def score_rule_pair(
    gold_rule: dict,
    pred_rule: dict,
    alias_map: AliasMap,
) -> dict:
    """
    Score a single (gold_rule, pred_rule) pair.

    Returns:
        {
          "slot_scores": {slot: True|False|None, ...},
          "scored_slots": [slot, ...],       # slots where gold != null
          "matched_slots": [slot, ...],
          "normalized_exact_match": bool,
          "n_scored": int,
          "n_matched": int,
        }
    """
    slot_scores: dict[str, bool | None] = {}
    scored: list[str] = []
    matched: list[str] = []

    for slot in SLOT_NAMES:
        gold_val = _get_slot_from_rule(gold_rule, slot)
        pred_val = _get_slot_from_rule(pred_rule, slot)
        result = _compare_slot(slot, gold_val, pred_val, alias_map)
        slot_scores[slot] = result
        if result is not None:  # gold was non-null
            scored.append(slot)
            if result:
                matched.append(slot)

    n_scored  = len(scored)
    n_matched = len(matched)
    nem = (n_scored > 0) and (n_matched == n_scored)

    return {
        "slot_scores":            slot_scores,
        "scored_slots":           scored,
        "matched_slots":          matched,
        "normalized_exact_match": nem,
        "n_scored":               n_scored,
        "n_matched":              n_matched,
    }


def match_compound_rules(
    gold_rules: list[dict],
    pred_rules: list[dict],
    alias_map: AliasMap,
) -> dict:
    """
    Order-agnostic matching for compound intents.
    Tries all permutations of pred_rules (up to len(gold_rules)) and
    picks the assignment that maximises n_matched.

    Returns same structure as score_rule_pair (aggregated over best match).
    """
    n_gold = len(gold_rules)
    n_pred = len(pred_rules)

    if n_pred == 0:
        # No predictions at all
        return _empty_rule_score()

    # Pad or trim pred list to match gold length
    if n_pred >= n_gold:
        candidates = list(permutations(pred_rules, n_gold))
    else:
        # Fewer pred rules than gold: try all positions for pred_rules among gold slots
        # (pads at any position, not just appended at end)
        pad = [{}] * (n_gold - n_pred)
        candidates = [list(p) for p in permutations(pred_rules + pad)]

    best_result = None
    best_matched = -1

    for pred_assignment in candidates:
        all_slot_scores: dict[str, list[bool | None]] = defaultdict(list)
        total_scored  = 0
        total_matched = 0
        all_nem = True

        for g_rule, p_rule in zip(gold_rules, pred_assignment):
            pair = score_rule_pair(g_rule, p_rule, alias_map)
            total_scored  += pair["n_scored"]
            total_matched += pair["n_matched"]
            all_nem = all_nem and pair["normalized_exact_match"]
            for slot, v in pair["slot_scores"].items():
                all_slot_scores[slot].append(v)

        if total_matched > best_matched:
            best_matched = total_matched
            # Merge slot scores: a slot matches if it matches in ALL paired rules
            merged: dict[str, bool | None] = {}
            scored_agg: list[str] = []
            matched_agg: list[str] = []
            for slot in SLOT_NAMES:
                vals = [v for v in all_slot_scores[slot] if v is not None]
                if not vals:
                    merged[slot] = None
                else:
                    result = all(vals)
                    merged[slot] = result
                    scored_agg.append(slot)
                    if result:
                        matched_agg.append(slot)
            best_result = {
                "slot_scores":            merged,
                "scored_slots":           scored_agg,
                "matched_slots":          matched_agg,
                "normalized_exact_match": all_nem,
                "n_scored":               total_scored,
                "n_matched":              total_matched,
            }

    return best_result or _empty_rule_score()


def _empty_rule_score() -> dict:
    return {
        "slot_scores":            {s: None for s in SLOT_NAMES},
        "scored_slots":           [],
        "matched_slots":          [],
        "normalized_exact_match": False,
        "n_scored":               0,
        "n_matched":              0,
    }


# ════════════════════════════════════════════════════════════════════════
# Hallucination detection
# ════════════════════════════════════════════════════════════════════════

def check_hallucination(pred_rules: list[dict], alias_map: AliasMap) -> tuple[list[str], float]:
    """
    Inspect all entity references in predicted rules.
    Returns (hallucinated_refs, rate).
    rate = n_hallucinated / n_total_refs (or 0.0 if no refs)
    """
    refs: list[str] = []
    for rule in pred_rules:
        src  = _safe_get(rule, "selector", "source", "host")
        dst  = _safe_get(rule, "selector", "destination", "host")
        dev  = _safe_get(rule, "enforcement", "device")
        for ref in (src, dst, dev):
            if ref is not None:
                refs.append(str(ref).strip())

    if not refs:
        return [], 0.0

    hallucinated = [r for r in refs if not alias_map.is_known(r)]
    rate = len(hallucinated) / len(refs)
    return hallucinated, rate


# ════════════════════════════════════════════════════════════════════════
# Record-level scorer
# ════════════════════════════════════════════════════════════════════════

def score_record(
    record: dict,
    gold_case: dict,
    alias_map: AliasMap,
) -> dict:
    """
    Score one log record against its gold case.

    Returns a flat dict of all metrics for this record.
    """
    case_id   = record["case_id"]
    treatment = record.get("treatment", "")
    output    = record.get("output")
    err_kind  = record.get("error_kind")

    gold      = gold_case["gold"]
    category  = gold_case["category"]
    gold_status = gold["status"]    # "accepted" | "rejected"

    # ── Schema validity ────────────────────────────────────────────
    schema_validity = (err_kind is None) and (output is not None)

    # ── Predicted status ───────────────────────────────────────────
    pred_status = detect_pred_status(output) if schema_validity else "error"

    # ── Status match ───────────────────────────────────────────────
    if pred_status == "error":
        status_match = False
    elif gold_status == "accepted":
        status_match = (pred_status == "accepted")
    else:
        status_match = (pred_status == "rejected")

    result: dict = {
        "case_id":          case_id,
        "category":         category,
        "gold_status":      gold_status,
        "schema_validity":  schema_validity,
        "pred_status":      pred_status,
        "status_match":     status_match,
    }

    # ── Rejection metrics (gold=rejected) ─────────────────────────
    if gold_status == "rejected":
        gold_reason = gold.get("rejection_reason")
        detected    = (pred_status == "rejected")
        if detected:
            pred_reason = output.get("rejection_reason") if output else None
            reason_match = (str(pred_reason).strip().lower() == str(gold_reason).strip().lower())
        else:
            reason_match = None

        result["rejection_detected"]     = detected
        result["rejection_reason_match"] = reason_match
        result["false_acceptance"]       = (pred_status == "accepted")
        return result

    # ── Accepted gold metrics ─────────────────────────────────────
    result["false_rejection"] = (pred_status != "accepted")

    # Slot metrics only for IntentIR treatments (T-B, T-C, T-D)
    # and only when pred is also accepted
    is_ir_treatment = treatment not in ("T-A",)

    if not is_ir_treatment or pred_status != "accepted":
        result["slot_scores"]            = None
        result["normalized_exact_match"] = None
        result["hallucinated_entities"]  = []
        result["hallucinated_entity_rate"] = None
        result["n_gold_rules"]           = len(extract_gold_rules(gold))
        result["n_pred_rules"]           = 0
        result["rule_count_match"]       = False
        return result

    # Extract rule lists
    gold_rules = extract_gold_rules(gold)
    pred_rules = extract_pred_rules(output)
    n_gold     = len(gold_rules)
    n_pred     = len(pred_rules)

    result["n_gold_rules"]    = n_gold
    result["n_pred_rules"]    = n_pred
    result["rule_count_match"] = (n_gold == n_pred)

    # Hallucination (regardless of rule count)
    hall_refs, hall_rate = check_hallucination(pred_rules, alias_map)
    result["hallucinated_entities"]    = hall_refs
    result["hallucinated_entity_rate"] = hall_rate

    # Slot scoring
    if pred_rules:
        if n_gold == 1:
            pair_result = score_rule_pair(gold_rules[0], pred_rules[0], alias_map)
        else:
            pair_result = match_compound_rules(gold_rules, pred_rules, alias_map)
        result["slot_scores"]            = pair_result["slot_scores"]
        result["normalized_exact_match"] = pair_result["normalized_exact_match"]
    else:
        result["slot_scores"]            = {s: None for s in SLOT_NAMES}
        result["normalized_exact_match"] = False

    return result


# ════════════════════════════════════════════════════════════════════════
# Aggregation helpers
# ════════════════════════════════════════════════════════════════════════

def _mean(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def aggregate_run(scored_records: list[dict]) -> dict:
    """
    Compute all metrics over one run's scored records (one repetition).
    """
    all_c    = scored_records
    acc_gold = [r for r in all_c if r["gold_status"] == "accepted"]
    rej_gold = [r for r in all_c if r["gold_status"] == "rejected"]

    # Schema validity (all cases)
    schema_validity = _mean([int(r["schema_validity"]) for r in all_c])

    # Status match (all cases)
    status_match = _mean([int(r["status_match"]) for r in all_c])

    # False rejection rate (gold=accepted)
    false_rejection_rate = _mean([int(r.get("false_rejection", False)) for r in acc_gold])

    # Rejection metrics (gold=rejected)
    rejection_recall = _mean([int(r.get("rejection_detected", False)) for r in rej_gold])
    rr_vals = [int(r["rejection_reason_match"]) for r in rej_gold if r.get("rejection_reason_match") is not None]
    rejection_reason_match = _mean(rr_vals)
    false_acceptance_rate  = _mean([int(r.get("false_acceptance", False)) for r in rej_gold])

    # For slot metrics: gold=accepted, pred=accepted, IntentIR only
    slot_eligible = [r for r in acc_gold if r.get("slot_scores") is not None]

    slot_accuracy: dict[str, float | None] = {}
    for slot in SLOT_NAMES:
        vals = [int(r["slot_scores"][slot]) for r in slot_eligible if r["slot_scores"].get(slot) is not None]
        slot_accuracy[slot] = _mean(vals)

    nem_vals = [int(r["normalized_exact_match"]) for r in slot_eligible if r.get("normalized_exact_match") is not None]
    normalized_exact_match = _mean(nem_vals)

    her_vals = [r["hallucinated_entity_rate"] for r in slot_eligible if r.get("hallucinated_entity_rate") is not None]
    hallucinated_entity_rate = _mean(her_vals)

    # Rule count match (compound eligible)
    cmp_eligible = [r for r in slot_eligible if r.get("n_gold_rules", 1) > 1]
    rule_count_match = _mean([int(r["rule_count_match"]) for r in cmp_eligible]) if cmp_eligible else None

    return {
        "schema_validity":        schema_validity,
        "status_match":           status_match,
        "false_rejection_rate":   false_rejection_rate,
        "rejection_recall":       rejection_recall,
        "rejection_reason_match": rejection_reason_match,
        "false_acceptance_rate":  false_acceptance_rate,
        "slot_accuracy":          slot_accuracy,
        "normalized_exact_match": normalized_exact_match,
        "hallucinated_entity_rate": hallucinated_entity_rate,
        "rule_count_match":       rule_count_match,
    }


def aggregate_treatment(per_rep_metrics: list[dict]) -> dict:
    """
    Compute mean ± std over repetitions for each metric.
    per_rep_metrics: list of dicts from aggregate_run, one per repetition.
    """
    def _stats(values: list) -> dict:
        vals = [v for v in values if v is not None]
        if not vals:
            return {"mean": None, "std": None, "n": 0}
        n    = len(vals)
        mean = sum(vals) / n
        if n > 1:
            std = math.sqrt(sum((x - mean)**2 for x in vals) / (n - 1))
        else:
            std = 0.0
        return {"mean": round(mean, 4), "std": round(std, 4), "n": n}

    flat_keys = [
        "schema_validity", "status_match", "false_rejection_rate",
        "rejection_recall", "rejection_reason_match", "false_acceptance_rate",
        "normalized_exact_match", "hallucinated_entity_rate", "rule_count_match",
    ]

    aggregate: dict = {}
    for key in flat_keys:
        vals = [m.get(key) for m in per_rep_metrics]
        aggregate[key] = _stats(vals)

    aggregate["slot_accuracy"] = {}
    for slot in SLOT_NAMES:
        vals = [m["slot_accuracy"].get(slot) for m in per_rep_metrics]
        aggregate["slot_accuracy"][slot] = _stats(vals)

    return aggregate


def per_category_metrics(
    all_scored: list[dict],   # flat list of all scored records across all reps
) -> dict[str, dict]:
    """
    Compute aggregate metrics grouped by category.
    """
    categories = sorted({r["category"] for r in all_scored})
    result: dict = {}

    for cat in categories:
        cat_records = [r for r in all_scored if r["category"] == cat]

        acc_gold = [r for r in cat_records if r["gold_status"] == "accepted"]
        rej_gold = [r for r in cat_records if r["gold_status"] == "rejected"]
        slot_elig = [r for r in acc_gold if r.get("slot_scores") is not None]

        def _m(lst): return round(_mean(lst), 4) if _mean(lst) is not None else None

        slot_acc: dict = {}
        for slot in SLOT_NAMES:
            vals = [int(r["slot_scores"][slot]) for r in slot_elig if r["slot_scores"].get(slot) is not None]
            slot_acc[slot] = _m(vals)

        result[cat] = {
            "n_records":            len(cat_records),
            "schema_validity":       _m([int(r["schema_validity"]) for r in cat_records]),
            "status_match":          _m([int(r["status_match"]) for r in cat_records]),
            "false_rejection_rate":  _m([int(r.get("false_rejection", False)) for r in acc_gold]),
            "rejection_recall":      _m([int(r.get("rejection_detected", False)) for r in rej_gold]),
            "rejection_reason_match": _m([int(r["rejection_reason_match"]) for r in rej_gold if r.get("rejection_reason_match") is not None]),
            "normalized_exact_match": _m([int(r["normalized_exact_match"]) for r in slot_elig if r.get("normalized_exact_match") is not None]),
            "hallucinated_entity_rate": _m([r["hallucinated_entity_rate"] for r in slot_elig if r.get("hallucinated_entity_rate") is not None]),
            "slot_accuracy":         slot_acc,
        }

    return result


# ════════════════════════════════════════════════════════════════════════
# Bootstrap CI (optional)
# ════════════════════════════════════════════════════════════════════════

def bootstrap_ci(
    values: list[float],
    n_resamples: int = 10_000,
    seed: int = 42,
    ci: float = 0.95,
) -> tuple[float, float]:
    """
    Compute bootstrap CI for the mean of values.
    Returns (lower, upper) percentile bounds.
    """
    import random
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1 - ci) / 2
    lo = means[int(alpha * n_resamples)]
    hi = means[int((1 - alpha) * n_resamples)]
    return round(lo, 4), round(hi, 4)


# ════════════════════════════════════════════════════════════════════════
# I/O helpers
# ════════════════════════════════════════════════════════════════════════

def load_gold(path: Path) -> dict[str, dict]:
    """Load gold dataset as {case_id: case_dict}."""
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {c["case_id"]: c for c in cases}


def load_topology(path: Path) -> AliasMap:
    data = json.loads(path.read_text(encoding="utf-8"))
    return AliasMap(data)


def load_log_files(
    logs_path: Path,
    treatment_filter: str | None,
    run_id_filter: str | None = None,
) -> dict[str, list[tuple[int, list[dict]]]]:
    """
    Load all JSONL log files.
    Returns: {treatment: [(repetition, records), ...]}

    Safety: run_exp1.py flushes one rep at a time, so a crashed/interrupted
    session leaves a *complete-looking* but stray run_id behind (different
    run_id, same treatment). If run_id_filter is not given and more than one
    distinct run_id is found for the same treatment, this raises SystemExit
    instead of silently summing them into the aggregate — that would corrupt
    the reported repetition count/stats with leftover partial runs.
    """
    if logs_path.is_file():
        files = [logs_path]
    else:
        files = sorted(logs_path.glob("*.jsonl"))

    treatment_runs: dict[str, list[tuple[int, list[dict]]]] = defaultdict(list)
    treatment_run_ids: dict[str, set[str]] = defaultdict(set)

    for fpath in files:
        records = [json.loads(l) for l in fpath.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not records:
            continue
        trt = records[0].get("treatment", "unknown")
        rep = records[0].get("repetition", 0)
        run_id = records[0].get("run_id", "unknown")

        if treatment_filter and trt != treatment_filter:
            continue
        if run_id_filter and run_id != run_id_filter:
            continue

        treatment_runs[trt].append((rep, records))
        treatment_run_ids[trt].add(run_id)

    if not run_id_filter:
        contaminated = {trt: ids for trt, ids in treatment_run_ids.items() if len(ids) > 1}
        if contaminated:
            print("ERROR: multiple distinct run_ids found for the same treatment "
                  "(likely leftover/crashed runs mixed with the intended run):")
            for trt, ids in sorted(contaminated.items()):
                print(f"  {trt}: {sorted(ids)}")
            print("Pass --run-id <run_id> to pick one explicitly, or remove the stray "
                  "log files before scoring.")
            sys.exit(1)

    # Sort by repetition within each treatment
    for trt in treatment_runs:
        treatment_runs[trt].sort(key=lambda x: x[0])

    return dict(treatment_runs)


# ════════════════════════════════════════════════════════════════════════
# Report printer
# ════════════════════════════════════════════════════════════════════════

def print_report(report: dict) -> None:
    treatments = report.get("treatments", {})

    for trt, tdata in sorted(treatments.items()):
        agg = tdata.get("aggregate", {})
        n_reps = tdata.get("n_repetitions", 0)
        n_acc  = tdata.get("n_accepted_gold", 0)
        n_rej  = tdata.get("n_rejected_gold", 0)

        print(f"\n{'='*70}")
        print(f"  Treatment: {trt}   reps={n_reps}   cases={n_acc+n_rej} (acc={n_acc} rej={n_rej})")
        print(f"{'='*70}")

        def _fmt(key):
            v = agg.get(key, {})
            if not isinstance(v, dict):
                return "N/A"
            m, s = v.get("mean"), v.get("std")
            if m is None:
                return "N/A"
            return f"{m:.3f} +/- {s:.3f}"

        print(f"  schema_validity        : {_fmt('schema_validity')}")
        print(f"  status_match           : {_fmt('status_match')}")
        print(f"  false_rejection_rate   : {_fmt('false_rejection_rate')}")
        print(f"  rejection_recall       : {_fmt('rejection_recall')}")
        print(f"  rejection_reason_match : {_fmt('rejection_reason_match')}")
        print(f"  false_acceptance_rate  : {_fmt('false_acceptance_rate')}")
        print(f"  normalized_exact_match : {_fmt('normalized_exact_match')}")
        print(f"  hallucinated_entity_rate:{_fmt('hallucinated_entity_rate')}")

        slot_acc = agg.get("slot_accuracy", {})
        if any(v.get("mean") is not None for v in slot_acc.values() if isinstance(v, dict)):
            print(f"\n  Slot accuracy:")
            for slot in SLOT_NAMES:
                v = slot_acc.get(slot, {})
                if isinstance(v, dict) and v.get("mean") is not None:
                    print(f"    {slot:<22}: {v['mean']:.3f} +/- {v['std']:.3f}")

        print(f"\n  Per-category (status_match):")
        for cat, cdata in sorted(tdata.get("per_category", {}).items()):
            sm  = cdata.get("status_match")
            nem = cdata.get("normalized_exact_match")
            sm_str  = f"{sm:.3f}" if sm is not None else " N/A"
            nem_str = f"{nem:.3f}" if nem is not None else " N/A"
            print(f"    {cat:<12}  status_match={sm_str}  nem={nem_str}")

    print(f"\n{'='*70}\n")


# ════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Exp-1 Scoring Engine")
    parser.add_argument("--dataset",   required=True, help="Gold JSONL path")
    parser.add_argument("--topology",  required=True, help="Topology JSON path (for alias normalization)")
    parser.add_argument("--logs",      required=True, help="Logs directory or specific JSONL file")
    parser.add_argument("--output",    required=True, help="Output report JSON path")
    parser.add_argument("--treatment", default=None,  help="Filter by treatment (e.g. T-D)")
    parser.add_argument("--run-id",    default=None,  help="Filter by exact run_id — required when multiple run_ids exist for the same treatment (e.g. leftover crashed runs)")
    parser.add_argument("--bootstrap", action="store_true", help="Compute 95%% bootstrap CI per rep (slow)")
    args = parser.parse_args()

    ROOT = Path(__file__).resolve().parents[2]

    dataset_path  = ROOT / args.dataset
    topology_path = ROOT / args.topology
    logs_path     = ROOT / args.logs
    output_path   = ROOT / args.output

    if not dataset_path.exists():
        print(f"ERROR: dataset not found: {dataset_path}")
        sys.exit(1)
    if not topology_path.exists():
        print(f"ERROR: topology not found: {topology_path}")
        sys.exit(1)
    if not logs_path.exists():
        print(f"ERROR: logs path not found: {logs_path}")
        sys.exit(1)

    print(f"Loading gold: {dataset_path.name}")
    gold_map = load_gold(dataset_path)
    print(f"  {len(gold_map)} cases loaded")

    print(f"Loading topology: {topology_path.name}")
    alias_map = load_topology(topology_path)

    print(f"Loading log files from: {logs_path}")
    treatment_runs = load_log_files(logs_path, args.treatment, args.run_id)
    if not treatment_runs:
        print("ERROR: no matching log files found")
        sys.exit(1)
    for trt, runs in treatment_runs.items():
        print(f"  {trt}: {len(runs)} repetition(s)")

    # Build report
    report: dict = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset":      dataset_path.name,
            "topology":     topology_path.name,
            "logs":         str(logs_path),
        },
        "treatments": {},
    }

    for trt, runs in sorted(treatment_runs.items()):
        print(f"\nScoring {trt} ({len(runs)} rep(s), {len(gold_map)} cases each)...")

        all_scored_records: list[dict] = []  # flat list across all reps
        per_rep_metrics: list[dict] = []
        per_rep_data: list[dict] = []

        for rep, records in runs:
            rep_scored: list[dict] = []
            missing_gold = 0

            for rec in records:
                case_id = rec["case_id"]
                if case_id not in gold_map:
                    missing_gold += 1
                    continue
                scored = score_record(rec, gold_map[case_id], alias_map)
                rep_scored.append(scored)
                all_scored_records.append(scored)

            if missing_gold:
                print(f"  rep {rep}: WARNING {missing_gold} records had no matching gold case")

            run_metrics = aggregate_run(rep_scored)
            per_rep_metrics.append(run_metrics)

            rep_entry: dict = {
                "repetition": rep,
                "n_records":  len(rep_scored),
                "metrics":    run_metrics,
            }

            # Optional bootstrap CI
            if args.bootstrap:
                seed = _BOOTSTRAP_SEEDS.get(rep, 42)
                ci_dict: dict = {}
                flat_keys = [
                    "schema_validity", "status_match", "false_rejection_rate",
                    "rejection_recall", "normalized_exact_match", "hallucinated_entity_rate",
                ]
                for key in flat_keys:
                    val = run_metrics.get(key)
                    if val is not None:
                        scored_vals = []
                        for r in rep_scored:
                            if key == "schema_validity":
                                scored_vals.append(int(r["schema_validity"]))
                            elif key == "status_match":
                                scored_vals.append(int(r["status_match"]))
                            elif key == "false_rejection_rate" and r["gold_status"] == "accepted":
                                scored_vals.append(int(r.get("false_rejection", False)))
                            elif key == "rejection_recall" and r["gold_status"] == "rejected":
                                scored_vals.append(int(r.get("rejection_detected", False)))
                            elif key == "normalized_exact_match" and r.get("normalized_exact_match") is not None:
                                scored_vals.append(int(r["normalized_exact_match"]))
                            elif key == "hallucinated_entity_rate" and r.get("hallucinated_entity_rate") is not None:
                                scored_vals.append(r["hallucinated_entity_rate"])
                        if scored_vals:
                            lo, hi = bootstrap_ci(scored_vals, seed=seed)
                            ci_dict[key] = {"ci95_lo": lo, "ci95_hi": hi}
                rep_entry["bootstrap_ci95"] = ci_dict

            per_rep_data.append(rep_entry)
            print(f"  rep {rep}: schema={run_metrics['schema_validity']:.3f}  "
                  f"status={run_metrics['status_match']:.3f}  "
                  f"nem={run_metrics['normalized_exact_match']}")

        # Accepted/rejected counts (from gold)
        n_acc = sum(1 for c in gold_map.values() if c["gold"]["status"] == "accepted")
        n_rej = sum(1 for c in gold_map.values() if c["gold"]["status"] == "rejected")

        # Detect run_ids from first rep's records
        first_rep_records = runs[0][1] if runs else []
        run_ids = list({r.get("run_id", "") for r in first_rep_records if r.get("run_id")})

        report["treatments"][trt] = {
            "run_ids":            run_ids,
            "n_repetitions":      len(runs),
            "n_cases":            len(gold_map),
            "n_accepted_gold":    n_acc,
            "n_rejected_gold":    n_rej,
            "per_rep":            per_rep_data,
            "aggregate":          aggregate_treatment(per_rep_metrics),
            "per_category":       per_category_metrics(all_scored_records),
        }

    # Save report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReport saved: {output_path}")

    # Print human-readable summary
    print_report(report)


if __name__ == "__main__":
    main()
