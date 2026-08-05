"""tests/test_prompt_registry.py — prompts/registry.py 대응표와 프롬프트 파일 해시를 고정한다.

Exp-1 수치를 실제로 움직이는 유일한 코드 자산이 프롬프트이므로(Stage 3), 의도치
않은 프롬프트 수정이나 registry 대응표 변경을 이 스냅샷으로 잡는다. 프롬프트를
의도적으로 바꿀 때는 EXPECTED_HASHES를 함께 갱신한다.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "prompts" / "registry.py"
PROMPTS_DIR = ROOT / "prompts"

EXPECTED_REGISTRY = {
    "direct_flow": "direct_flow.md",
    "intent_ir": "intent_ir.md",
}

EXPECTED_HASHES = {
    "direct_flow.md": "b021462407494250dfe3bf97dc25c65ce82a9212251ff2f4734bcb6512674e42",
    "intent_ir.md": "d4955a7ee57a786855986ab4d3a97ef7afa8e3512db0854d0e25eeabd93f88ff",
}


def _load_registry():
    spec = importlib.util.spec_from_file_location("prompt_registry", REGISTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_registry_mapping_is_fixed():
    registry = _load_registry()
    assert registry.REGISTRY == EXPECTED_REGISTRY


def test_prompt_file_hashes_are_fixed():
    for filename, expected_hash in EXPECTED_HASHES.items():
        content = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert actual_hash == expected_hash, f"{filename} content changed unexpectedly"


def test_get_prompt_reads_registered_files():
    registry = _load_registry()
    for output_format in EXPECTED_REGISTRY:
        text = registry.get_prompt(output_format)
        assert isinstance(text, str) and text.strip()


def test_get_prompt_rejects_unknown_output_format():
    registry = _load_registry()
    try:
        registry.get_prompt("nonexistent_format")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown output_format")


def test_no_prompt_string_literals_in_python_source():
    """완료 기준: 코드베이스 전체에 프롬프트 문자열 리터럴 0건.

    prompts/*.md 밖에서 이 프롬프트들의 특징적인 헤더 문구가 다시 하드코딩되지
    않았는지 확인한다.
    """
    markers = [
        "You are an SDN network intent parser.",
        "You are an SDN network operator. Given a natural language network intent",
    ]
    for py_file in ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", "node_modules"} for part in py_file.parts):
            continue
        if py_file == Path(__file__).resolve():
            continue
        text = py_file.read_text(encoding="utf-8")
        for marker in markers:
            assert marker not in text, f"prompt string literal found in {py_file}"
