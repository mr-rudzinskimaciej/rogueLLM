"""
Minimal LLM adapter.

Reads credentials and model list from environment — NEVER hard-code keys here.

Environment variables:
  KEROS_API_KEY        Required for live LLM calls. If unset, the adapter
                       returns "wait" and the simulation runs with no LLM.
  KEROS_API_BASE       OpenAI-compatible base URL. Defaults to OpenRouter.
  KEROS_FREE_MODELS    Comma-separated model IDs used for round-robin when
                       the requested model contains ":free". Optional.

Any OpenAI-compatible endpoint works (OpenRouter, vLLM, Ollama proxy,
Together, Fireworks, Anthropic via proxy, etc.).
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"

# Module-level call log. Drained per-frame by replay_capture into frame["llm_calls"].
# Each entry: {model, ok, raw_len, elapsed_ms, error}
call_log: list[dict[str, Any]] = []


def _provider_extra_body(model: str) -> dict:
    """OpenRouter provider routing.

    Auto-pins to first-party DeepSeek when model is `deepseek/*` (4x cheaper
    than third-party providers). Override via KEROS_PROVIDER ("" disables pin).
    """
    override = os.environ.get("KEROS_PROVIDER")
    if override is not None:
        return {"provider": {"only": [override]}} if override else {}
    if model.startswith("deepseek/"):
        return {"provider": {"only": ["deepseek"]}}
    return {}


def _free_models() -> list[str]:
    raw = os.environ.get("KEROS_FREE_MODELS", "").strip()
    if not raw:
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


_ring_index: int = 0


def _has_openai() -> bool:
    try:
        import openai  # noqa: F401
        return True
    except Exception:
        return False


def _advance_ring(models: list[str]) -> str:
    global _ring_index
    _ring_index = (_ring_index + 1) % len(models)
    return models[_ring_index]


def resolve_model(requested: str) -> str:
    """Round-robin through KEROS_FREE_MODELS on every call for :free requests.

    If no free models are configured, return the requested string verbatim.
    """
    if ":free" not in requested:
        return requested
    models = _free_models()
    if not models:
        return requested
    return _advance_ring(models)


def llm_chat_completion(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float = 1.2,
    max_retries: int = 2,
    timeout: float = 60.0,
) -> str:
    api_key = os.environ.get("KEROS_API_KEY", "").strip()
    if not api_key or not _has_openai():
        return "wait"

    api_base = os.environ.get("KEROS_API_BASE", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)

    current_model = resolve_model(model)
    for attempt in range(max_retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": current_model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            extra = _provider_extra_body(current_model)
            if extra:
                kwargs["extra_body"] = extra
            t0 = time.monotonic()
            response: Any = client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content if response.choices else ""
            call_log.append({"model": current_model, "ok": True,
                             "raw_len": len(text or ""),
                             "elapsed_ms": int((time.monotonic() - t0) * 1000),
                             "error": None})
            return (text or "wait").strip()
        except Exception as exc:
            call_log.append({"model": current_model, "ok": False, "raw_len": 0,
                             "elapsed_ms": int((time.monotonic() - t0) * 1000) if 't0' in locals() else 0,
                             "error": type(exc).__name__})
            print(f"[llm] {current_model} failed (attempt {attempt}): {type(exc).__name__}", file=sys.stderr, flush=True)
            if ":free" in model:
                models = _free_models()
                if models:
                    current_model = _advance_ring(models)
            continue

    return "wait"
