"""
Judge dispatcher.

Loads a prompt (debug/prompts/<name>.md) and a slice (debug/runs/.../slices/<key>.json),
calls an LLM via the judge tier, parses verdict JSON, writes to verdicts/<name>.json.

Runs all four judges in parallel by default.
"""
from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from debug.config import resolve_tier, openrouter_extra_body


# Map judge name -> (slice key, prompt filename).
JUDGES = {
    "cartographer":  ("world_growth", "cartographer.md"),
    "augur":         ("emergence",    "augur.md"),
    "coroner":       ("silent_bugs",  "coroner.md"),
    "npc_observer":  ("npc_behavior", "npc_observer.md"),
}


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def load_slice(slices_dir: Path, key: str) -> dict:
    return json.loads((slices_dir / f"{key}.json").read_text(encoding="utf-8"))


def _build_user_prompt(slice_data: dict) -> str:
    return (
        "Here is your slice. Read it, then answer with strict JSON per the schema.\n\n"
        "```json\n"
        + json.dumps(slice_data, ensure_ascii=False, indent=2)
        + "\n```"
    )


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction from a model response that may be code-fenced."""
    if not text:
        return None
    # Try direct parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip ```json fences.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # First {...} block.
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    return None


def call_judge_llm(system_prompt: str, user_prompt: str, tier: str = "judge") -> tuple[str, str]:
    """
    Call the LLM at the given tier. Returns (raw_text, model_used).

    No KEROS_API_KEY -> returns a stub verdict so the pipeline runs in dry mode.
    """
    api_key = os.environ.get("KEROS_API_KEY", "").strip()
    resolved = resolve_tier(tier)
    if not api_key:
        stub = json.dumps({
            "verdict": "(no KEROS_API_KEY set; judge ran in dry mode)",
            "intensity": "flat",
            "examples": [],
            "concerns": ["dry-mode: no live judgment"],
        })
        return stub, resolved.model + " [dry]"

    try:
        from openai import OpenAI
    except ImportError:
        stub = json.dumps({
            "verdict": "(openai client not installed)",
            "intensity": "flat",
            "examples": [],
            "concerns": ["openai package missing"],
        })
        return stub, resolved.model + " [no-client]"

    base = os.environ.get("KEROS_API_BASE", "https://openrouter.ai/api/v1")
    client = OpenAI(api_key=api_key, base_url=base, timeout=120.0)

    extra_body = openrouter_extra_body(resolved.provider)
    kwargs: dict[str, Any] = {
        "model": resolved.model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    try:
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content if resp.choices else ""
        return text or "", resolved.model
    except Exception as exc:
        print(f"[judge] {resolved.model} call failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        stub = json.dumps({
            "verdict": f"(judge call failed: {type(exc).__name__})",
            "intensity": "flat",
            "examples": [],
            "concerns": [f"call_error: {type(exc).__name__}"],
        })
        return stub, resolved.model + " [error]"


def run_one_judge(judge_name: str, slices_dir: Path, verdicts_dir: Path) -> dict:
    slice_key, prompt_file = JUDGES[judge_name]
    system_prompt = load_prompt(prompt_file)
    slice_data = load_slice(slices_dir, slice_key)
    user_prompt = _build_user_prompt(slice_data)

    raw, model = call_judge_llm(system_prompt, user_prompt, tier="judge")
    parsed = _extract_json(raw)

    verdict = {
        "judge": judge_name,
        "model": model,
        "block": slice_data.get("block"),
        "verdict": (parsed or {}).get("verdict") if parsed else None,
        "intensity": (parsed or {}).get("intensity") if parsed else None,
        "examples": (parsed or {}).get("examples", []) if parsed else [],
        "concerns": (parsed or {}).get("concerns", []) if parsed else [],
        "parse_ok": parsed is not None,
        "raw_response": raw if parsed is None else None,  # keep raw only on parse failure
    }
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    (verdicts_dir / f"{judge_name}.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return verdict


def run_all_judges(block_dir: str | Path, parallel: bool = True) -> dict[str, dict]:
    """
    Run all four judges for a block directory.
    block_dir = runs/<run_id>/blocks/<n>/, must contain slices/{world_growth,emergence,...}.json
    """
    block_dir = Path(block_dir)
    slices_dir = block_dir / "slices"
    verdicts_dir = block_dir / "verdicts"

    results: dict[str, dict] = {}
    if parallel:
        with ThreadPoolExecutor(max_workers=len(JUDGES)) as pool:
            futures = {
                pool.submit(run_one_judge, name, slices_dir, verdicts_dir): name
                for name in JUDGES
            }
            for fut in as_completed(futures):
                name = futures[fut]
                results[name] = fut.result()
    else:
        for name in JUDGES:
            results[name] = run_one_judge(name, slices_dir, verdicts_dir)
    return results
