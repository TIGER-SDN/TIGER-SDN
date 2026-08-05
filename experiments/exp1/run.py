"""
experiments/exp1/run.py — Exp-1 배치 러너

원본: sdn-intent-framework의 research/experiments/eval/run_exp1.py. 하드코딩되어
있던 SYSTEM_PROMPT/SYSTEM_DIRECT_FLOW를 prompts/registry.py를 통해 prompts/*.md에서
읽어오도록 바꾼 것 외에는 동작이 동일하다(Stage 3, 코드에 프롬프트 문자열
리터럴을 두지 않는다).

데이터셋별로 지정된 treatment 설정(T-A~T-D)에 따라 각 인텐트에 대해 LLM API를
N회 반복 호출한다. 반복(rep)마다 출력 디렉터리 아래 JSONL 파일을 하나씩 쓴다.

Usage:
    python experiments/exp1/run.py --config experiments/exp1/config/T-D-openrouter.toml
    python experiments/exp1/run.py --config experiments/exp1/config/T-A-openrouter.toml --repetitions 3
    python experiments/exp1/run.py --config experiments/exp1/config/T-D-openrouter.toml --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Project root (레포 루트, dataset/config 등 상대경로 해석용) ────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # pip install tomli
    except ImportError:
        raise RuntimeError("tomllib not available — run: pip install tomli")

from tiger_sdn import config as pipeline_config  # loads GOOGLE_API_KEY/OPENROUTER_API_KEY from .env
from prompts.registry import get_prompt

# ════════════════════════════════════════════════════════════════════════
# Config / data loaders
# ════════════════════════════════════════════════════════════════════════

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_dataset(path: Path) -> list[dict]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return cases


def _build_topology_prompt(topo_data: dict) -> str:
    """Build grounding prompt text from eval topology JSON format."""
    hosts: list[str] = []
    switch_lines: list[str] = []
    ports_map: dict[str, list[int]] = topo_data.get("ports", {})
    wiring: dict[str, dict] = topo_data.get("wiring", {})

    for entity in topo_data.get("entities", []):
        eid = entity["id"]
        aliases = entity.get("aliases", [])
        if eid.startswith("host:"):
            # aliases: [name, ip, ip/32] or [name, ip]
            name = aliases[0] if aliases else eid
            ip = next((a for a in aliases if "." in a and "/" not in a), "")
            if ip:
                hosts.append(f"{name}={ip}")
        elif eid.startswith("device:"):
            # aliases: [short_name, "switch N", "switchN", "of:..."]
            name = aliases[0] if aliases else eid
            onos_id = next((a for a in aliases if a.startswith("of:")), "")
            sw_wiring = wiring.get(name)
            if sw_wiring:
                # 배선 포함 표기: "s1 (of:...) ports: 1->s2, 2->s3, 3->h1, 4->h2, 9->firewall..."
                # gold의 reroute/sfc enforcement 값(어느 포트가 어느 스위치로 가는지)이
                # 이 배선 지식에 의존하므로 grounding이 반드시 커버해야 한다.
                port_str = ", ".join(
                    f"{p}->{dst}" for p, dst in sorted(sw_wiring.items(), key=lambda x: int(x[0]))
                )
            else:
                ports = sorted(ports_map.get(onos_id, []))
                port_str = ",".join(str(p) for p in ports)
            switch_lines.append(f"    {name} ({onos_id}) ports: {port_str}")

    host_str = ", ".join(hosts)
    switch_str = "\n".join(switch_lines)

    notes = topo_data.get("wiring_notes", [])
    notes_str = ("\n  Topology notes:\n" + "\n".join(f"    - {n}" for n in notes)) if notes else ""

    waypoints = topo_data.get("ids_waypoints", [])
    wp_line = f"  IDS/Firewall waypoints: {', '.join(waypoints)}\n" if waypoints else ""

    return (
        f"Network topology (ONLY reference entities listed here - do not invent others):\n"
        f"  Hosts: {host_str}\n"
        f"  Switches (port -> connected node):\n{switch_str}{notes_str}\n"
        f"{wp_line}"
        f"If the intent mentions a host IP or switch not in this list, "
        f"reject with reason \"unknown_entity\"."
    )


def load_topology_prompt(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _build_topology_prompt(data)


def load_demos_prompt(path: Path) -> str:
    """Build static few-shot block from demonstrations.json."""
    demos = json.loads(path.read_text(encoding="utf-8"))
    lines = ["## Examples (follow this format exactly)\n"]
    for demo in demos:
        instruction = demo["instruction"]
        output = json.dumps(demo["output"], ensure_ascii=False)
        lines.append(f"Input: {instruction}")
        lines.append(f"Output: {output}")
        lines.append("")
    return "\n".join(lines)


def build_system_prompt(
    output_format: str,
    few_shot: bool,
    grounding: bool,
    topology_prompt: str,
    demos_prompt: str,
) -> str:
    """Assemble the full system prompt for the given treatment."""
    base = get_prompt(output_format)

    parts = []

    # Grounding goes FIRST (so LLM sees it before the schema rules)
    if grounding and topology_prompt:
        parts.append(topology_prompt)

    parts.append(base)

    # Few-shot examples go LAST (closest to the user turn)
    if few_shot and demos_prompt:
        parts.append(demos_prompt)

    return "\n\n".join(parts)


# ════════════════════════════════════════════════════════════════════════
# Gemini API call with timing + token counting
# ════════════════════════════════════════════════════════════════════════

def call_gemini(
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout_s: float = 30.0,
    retries: int = 2,
    max_tokens: int | None = None,
) -> tuple[str | None, dict | None, int, int, float, str | None, str | None]:
    """
    Call Gemini and return:
      (raw_text, parsed_json, input_tokens, output_tokens, latency_ms, error_kind, error_msg)

    error_kind: None | "transport" | "schema_invalid"
    """
    from google import genai
    from google.genai import types

    api_key = pipeline_config.GOOGLE_API_KEY
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set — check .env file")

    client = genai.Client(api_key=api_key)
    max_attempts = retries + 1

    for attempt in range(max_attempts):
        t0 = time.perf_counter()
        try:
            gen_config_kwargs = dict(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=temperature,
            )
            if max_tokens is not None:
                gen_config_kwargs["max_output_tokens"] = max_tokens
            response = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(**gen_config_kwargs),
            )
            latency_ms = (time.perf_counter() - t0) * 1000

            raw_text = response.text or ""
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                return raw_text, None, input_tokens, output_tokens, latency_ms, "schema_invalid", str(exc)

            return raw_text, parsed, input_tokens, output_tokens, latency_ms, None, None

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            err_str = str(exc)

            if attempt < max_attempts - 1:
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait = [5, 15, 30][min(attempt, 2)]
                else:
                    wait = 2 ** attempt
                print(f"    [retry {attempt+1}/{max_attempts}] {err_str[:80]} - wait {wait}s")
                time.sleep(wait)
            else:
                return None, None, 0, 0, latency_ms, "transport", err_str

    return None, None, 0, 0, 0.0, "transport", "max retries exceeded"


# ════════════════════════════════════════════════════════════════════════
# OpenAI 호환 /chat/completions 공용 호출 (Ollama, OpenRouter 등)
# ════════════════════════════════════════════════════════════════════════

def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout_s: float = 120.0,
    retries: int = 2,
    max_tokens: int | None = None,
    extra_payload: dict | None = None,
) -> tuple[str | None, dict | None, int, int, float, str | None, str | None]:
    """
    OpenAI 호환 /chat/completions 호출 (non-streaming — usage 토큰 수 확보).
    반환 시그니처는 call_gemini와 동일:
      (raw_text, parsed_json, input_tokens, output_tokens, latency_ms, error_kind, error_msg)

    thinking 모드 모델(qwen3 등) 대응: 응답의 <think>...</think> 블록과 ```json 코드펜스를
    제거한 뒤 JSON 파싱한다.

    extra_payload: 요청 바디에 그대로 병합되는 provider별 확장 필드
      (예: OpenRouter의 {"reasoning": {"max_tokens": N}}).
    """
    import re as _re
    import urllib.request
    import urllib.error

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        # Cloudflare가 Python-urllib 기본 UA를 봇으로 차단(403 code 1010)하므로 필수
        "User-Agent": "Mozilla/5.0",
    }
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra_payload:
        payload.update(extra_payload)

    max_attempts = retries + 1
    for attempt in range(max_attempts):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            latency_ms = (time.perf_counter() - t0) * 1000

            raw_text = (
                body.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            ).strip()
            usage = body.get("usage", {}) or {}
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0

            cleaned = raw_text
            if "<think>" in cleaned:
                cleaned = _re.sub(r"<think>.*?</think>", "", cleaned, flags=_re.DOTALL).strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                inner = lines[1:] if lines[0].startswith("```") else lines
                if inner and inner[-1].strip() == "```":
                    inner = inner[:-1]
                cleaned = "\n".join(inner).strip()

            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                return raw_text, None, input_tokens, output_tokens, latency_ms, "schema_invalid", str(exc)

            return raw_text, parsed, input_tokens, output_tokens, latency_ms, None, None

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            err_str = str(exc)
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                print(f"    [retry {attempt+1}/{max_attempts}] {err_str[:80]} - wait {wait}s")
                time.sleep(wait)
            else:
                return None, None, 0, 0, latency_ms, "transport", err_str

    return None, None, 0, 0, 0.0, "transport", "max retries exceeded"


def call_ollama(model: str, **kwargs):
    """Ollama(로컬/자체호스팅) OpenAI 호환 엔드포인트 호출."""
    return _call_openai_compatible(
        base_url=pipeline_config.LLM_BASE_URL,
        api_key=pipeline_config.LLM_API_KEY,
        model=model,
        **kwargs,
    )


def call_openrouter(model: str, reasoning_max_tokens: int | None = None, **kwargs):
    """OpenRouter (https://openrouter.ai) 호출. 모델명 예: 'qwen/qwen3-8b'.

    reasoning_max_tokens: qwen3 같은 hybrid thinking 모델의 thinking 토큰 예산 상한
      (OpenRouter의 {"reasoning": {"max_tokens": N}} — effort 방식은 Qwen3에 안 먹혀서
      max_tokens 방식 사용). None이면 무제한(모델 기본 동작).
    """
    if not pipeline_config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set — check .env file")
    extra_payload = (
        {"reasoning": {"max_tokens": reasoning_max_tokens}}
        if reasoning_max_tokens is not None
        else None
    )
    return _call_openai_compatible(
        base_url=pipeline_config.OPENROUTER_BASE_URL,
        api_key=pipeline_config.OPENROUTER_API_KEY,
        model=model,
        extra_payload=extra_payload,
        **kwargs,
    )


def call_llm(model: str, reasoning_max_tokens: int | None = None, **kwargs):
    """모델명으로 백엔드 자동 선택.

    gemini* → Gemini API
    '/' 포함 (예: qwen/qwen3-8b) → OpenRouter (OpenRouter 모델 ID는 항상 vendor/model 형식)
    그 외 (예: qwen3:8b) → Ollama

    reasoning_max_tokens는 OpenRouter 경로에만 의미가 있으므로 여기서만 전달한다
    (Gemini/Ollama 호출부는 이 파라미터를 모른다).
    """
    if pipeline_config.is_gemini(model):
        return call_gemini(model=model, **kwargs)
    if "/" in model:
        return call_openrouter(model=model, reasoning_max_tokens=reasoning_max_tokens, **kwargs)
    return call_ollama(model=model, **kwargs)


# ════════════════════════════════════════════════════════════════════════
# Main runner
# ════════════════════════════════════════════════════════════════════════

def run(
    cfg: dict,
    dataset: list[dict],
    system_prompt: str,
    repetitions: int,
    output_dir: Path,
    dry_run: bool = False,
    concurrency: int = 1,
) -> None:
    exp = cfg["experiment"]
    llm = cfg["llm"]

    treatment  = exp["treatment"]
    model      = llm["model"]
    temperature = float(llm.get("temperature", 0.2))
    timeout_s  = float(llm.get("timeout_s", 30.0))
    retries    = int(llm.get("retries", 2))
    max_tokens = int(llm["max_tokens"]) if llm.get("max_tokens") is not None else None
    reasoning_max_tokens = (
        int(llm["reasoning_max_tokens"]) if llm.get("reasoning_max_tokens") is not None else None
    )

    # Stable run_id across all repetitions of this execution
    # e.g. "gemini-3.1-flash-lite" -> "gemini-31-flash-lite", "qwen3:8b" -> "qwen3-8b",
    # "qwen/qwen3-8b" (OpenRouter) -> "qwen-qwen3-8b"
    # (콜론·슬래시는 Windows 파일명에서 불법이므로 반드시 치환)
    model_slug = model.replace(".", "").replace(":", "-").replace("/", "-")
    run_uuid   = uuid.uuid4().hex[:8]
    run_id     = f"{treatment}-{model_slug}-{run_uuid}"

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*65}")
    print(f"  Exp-1 Run: {run_id}")
    print(f"  Treatment : {treatment}")
    print(f"  Model     : {model}  temp={temperature}")
    print(f"  Dataset   : {len(dataset)} cases  x  {repetitions} reps")
    print(f"  Output    : {output_dir}")
    if dry_run:
        print(f"  [DRY RUN] - no API calls will be made")
    print(f"{'='*65}\n")

    total_cases = len(dataset)

    def build_record(i: int, case: dict, rep: int) -> dict:
        """단일 케이스를 처리하고 로그 레코드를 만든다. 스레드에서 호출됨 — 공유 상태를 건드리지 않는다."""
        case_id = case["case_id"]
        intent  = case["intent_text"]
        prefix  = f"    [{i+1:02d}/{total_cases}] {case_id:<14}"

        if dry_run:
            print(f"{prefix} [DRY RUN skip]")
            return {
                "case_id":       case_id,
                "treatment":     treatment,
                "model":         model,
                "run_id":        run_id,
                "repetition":    rep,
                "output":        None,
                "raw_content":   None,
                "latency_ms":    0.0,
                "input_tokens":  0,
                "output_tokens": 0,
                "error_kind":    "dry_run",
                "error":         None,
            }

        raw_text, parsed, in_tok, out_tok, latency, err_kind, err_msg = call_llm(
            model=model,
            system=system_prompt,
            user=intent,
            temperature=temperature,
            timeout_s=timeout_s,
            retries=retries,
            max_tokens=max_tokens,
            reasoning_max_tokens=reasoning_max_tokens,
        )

        status_tag = ""
        if err_kind:
            status_tag = f"[{err_kind}]"
        elif parsed is not None:
            if parsed.get("status") == "rejected":
                status_tag = "[rejected]"
            elif "flows" in parsed:
                status_tag = f"[{len(parsed['flows'])} flow(s)]"
            elif "rules" in parsed:
                status_tag = f"[{len(parsed['rules'])} rule(s)]"
            else:
                status_tag = "[ok]"

        print(f"{prefix} {latency:6.0f}ms  in={in_tok} out={out_tok}  {status_tag}")

        return {
            "case_id":       case_id,
            "treatment":     treatment,
            "model":         model,
            "run_id":        run_id,
            "repetition":    rep,
            "output":        parsed,
            "raw_content":   raw_text,
            "latency_ms":    round(latency, 1),
            "input_tokens":  in_tok,
            "output_tokens": out_tok,
            "error_kind":    err_kind,
            "error":         err_msg,
        }

    for rep in range(1, repetitions + 1):
        out_file = output_dir / f"{run_id}-r{rep:02d}.jsonl"
        print(f"  Rep {rep}/{repetitions} -> {out_file.name}")

        records: list[dict] = []
        write_lock = threading.Lock()

        # 케이스마다 즉시 flush — rep 전체가 끝나야 파일에 쓰던 이전 방식은
        # 중간에 프로세스가 죽으면(레이트리밋 만료, 노트북 종료 등) rep 전체를
        # 유실시켰다. 이제 케이스 단위로 즉시 디스크에 반영해 중단 시에도
        # 그때까지의 결과는 남는다. score.py는 case_id로 매칭하므로
        # 파일 내 줄 순서(=완료 순서)는 채점에 영향 없다.
        with open(out_file, "w", encoding="utf-8") as f:
            if concurrency <= 1 or dry_run:
                for i, case in enumerate(dataset):
                    record = build_record(i, case, rep)
                    records.append(record)
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
            else:
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = {
                        executor.submit(build_record, i, case, rep): i
                        for i, case in enumerate(dataset)
                    }
                    for future in as_completed(futures):
                        record = future.result()
                        with write_lock:
                            records.append(record)
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                            f.flush()

        pass_count = sum(1 for r in records if r["error_kind"] is None)
        fail_count = sum(1 for r in records if r["error_kind"] is not None)
        print(f"    -> saved {len(records)} records  (ok={pass_count}, err={fail_count})\n")

    print(f"Done. run_id={run_id}\n")


# ════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Exp-1 batch runner (LLM API)")
    parser.add_argument("--config",      required=True, help="TOML config path (e.g. experiments/exp1/config/T-D-openrouter.toml)")
    parser.add_argument("--repetitions", type=int, default=10, help="Number of repetitions (default: 10)")
    parser.add_argument("--output",      default="experiments/exp1/logs/", help="Output directory for JSONL files")
    parser.add_argument("--dry-run",     action="store_true", help="Skip API calls; write placeholder records")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel in-flight requests per rep (default: 1 = sequential). Only meaningful for cloud APIs (Gemini/OpenRouter) — raise gradually and back off on 429s.")
    args = parser.parse_args()

    config_path = ROOT / args.config
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}")
        sys.exit(1)

    cfg = load_config(config_path)
    exp = cfg["experiment"]

    dataset_path  = ROOT / exp["dataset_path"]
    _topo_str     = exp.get("topology_path", "")
    topology_path = ROOT / _topo_str if _topo_str else None
    demos_path    = ROOT / exp.get("demos_path", "") if exp.get("demos_path") else None
    output_dir    = ROOT / args.output

    if not dataset_path.exists():
        print(f"ERROR: dataset not found: {dataset_path}")
        sys.exit(1)

    dataset = load_dataset(dataset_path)

    topology_prompt = ""
    if exp.get("grounding") and topology_path is not None and topology_path.exists():
        topology_prompt = load_topology_prompt(topology_path)

    demos_prompt = ""
    if exp.get("few_shot") and demos_path and demos_path.exists():
        demos_prompt = load_demos_prompt(demos_path)

    system_prompt = build_system_prompt(
        output_format   = exp.get("output_format", "intent_ir"),
        few_shot        = bool(exp.get("few_shot")),
        grounding       = bool(exp.get("grounding")),
        topology_prompt = topology_prompt,
        demos_prompt    = demos_prompt,
    )

    # Print prompt length for reference
    prompt_tokens_est = len(system_prompt) // 4
    print(f"System prompt: ~{prompt_tokens_est} tokens estimated ({len(system_prompt)} chars)")

    run(
        cfg        = cfg,
        dataset    = dataset,
        system_prompt = system_prompt,
        repetitions   = args.repetitions,
        output_dir    = output_dir,
        dry_run       = args.dry_run,
        concurrency   = args.concurrency,
    )


if __name__ == "__main__":
    main()
