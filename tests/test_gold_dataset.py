from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold" / "gold.jsonl"
GOLD350_EVAL_PATH = ROOT / "data" / "gold" / "gold350_eval.jsonl"
CONVERTER_PATH = ROOT / "experiments" / "exp1" / "convert_gold350.py"

EXPECTED_CATEGORY_COUNTS = {
    "forwarding": 50,
    "security": 50,
    "qos": 50,
    "sfc": 50,
    "reroute": 50,
    "compound": 50,
    "ambiguous_unsupported": 50,
}


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_converter():
    spec = importlib.util.spec_from_file_location("convert_gold350", CONVERTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_gold_has_350_cases():
    cases = _load_jsonl(GOLD_PATH)
    assert len(cases) == 350


def test_gold_has_seven_balanced_categories():
    cases = _load_jsonl(GOLD_PATH)
    counts = Counter(case["category"] for case in cases)
    assert counts == EXPECTED_CATEGORY_COUNTS


def test_gold_accepted_rejected_split():
    cases = _load_jsonl(GOLD_PATH)
    statuses = Counter(case["expected"]["status"] for case in cases)
    assert statuses == {"accepted": 300, "rejected": 50}
    rejected_categories = {
        case["category"] for case in cases if case["expected"]["status"] == "rejected"
    }
    assert rejected_categories == {"ambiguous_unsupported"}


def test_convert_gold350_reproduces_committed_derivation():
    converter = _load_converter()
    cases = _load_jsonl(GOLD_PATH)
    warnings: list[str] = []
    converted = [converter.convert_case(case, warnings) for case in cases]

    committed = _load_jsonl(GOLD350_EVAL_PATH)
    assert converted == committed
