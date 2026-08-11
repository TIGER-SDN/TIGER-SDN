"""src/tiger_sdn/runctx/schema.py — 실행 로깅 레코드용 JSON Schema 생성기.

원본: sdn-intent-framework의 research/safe_intent_sdn/schema.py. `intent_ir`/`onos`
모듈이 이 레포에서는 각각 `tiger_sdn.ir.prediction`/`tiger_sdn.compile.onos`로
갈라져 있어(Stage 4, Stage 5) import 경로만 그에 맞춰 고쳤다 — 로직은 동일하다.
"""

from __future__ import annotations

import json
from pathlib import Path

from tiger_sdn.compile.onos import OnosFlowSet
from tiger_sdn.ir.prediction import IntentPrediction
from tiger_sdn.runctx.run_context import EventRecord, RunManifest

REPO_ROOT = Path(__file__).resolve().parents[3]


def generate_schemas(output_dir: Path | None = None) -> list[Path]:
    target = output_dir or REPO_ROOT / "schemas"
    target.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for name, model in (
        ("run_manifest", RunManifest),
        ("event_record", EventRecord),
        ("intent_prediction", IntentPrediction),
        ("onos_flow_set", OnosFlowSet),
    ):
        path = target / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        generated.append(path)
    return generated


if __name__ == "__main__":
    for schema_path in generate_schemas():
        print(schema_path)
