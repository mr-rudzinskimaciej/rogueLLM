"""
Model-tier registry for the debug pipeline.

Three tiers, resolved at call time from environment variables:

    pro    - heavy lifting: bootstrap, worldbuilder, GM, memory compaction
    flash  - per-turn NPC calls, rule expansion, item expansion
    judge  - debug pipeline judges (cartographer / augur / coroner / npc_observer)

Defaults target DeepSeek V4 (released 2026-04-24) routed through OpenRouter,
pinned to first-party DeepSeek inference via the provider routing field.

Override per call by setting the matching env var, or by passing model= to
call_tier() directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PRO = "deepseek/deepseek-v4-pro"
DEFAULT_FLASH = "deepseek/deepseek-v4-flash"
DEFAULT_JUDGE = "anthropic/claude-sonnet-4-6"

DEFAULT_PROVIDER = "deepseek"


@dataclass(frozen=True)
class TierResolved:
    """A resolved (tier_name, model_slug, provider_pref) triple."""
    tier: str
    model: str
    provider: str | None


def resolve_tier(tier: str) -> TierResolved:
    """
    Resolve a tier name to a concrete model slug + provider preference.

    Env vars:
        KEROS_MODEL_PRO       (default: deepseek/deepseek-v4-pro)
        KEROS_MODEL_FLASH     (default: deepseek/deepseek-v4-flash)
        KEROS_MODEL_JUDGE     (default: anthropic/claude-sonnet-4-6)
        KEROS_PROVIDER_PRO    (default: deepseek)
        KEROS_PROVIDER_FLASH  (default: deepseek)
        KEROS_PROVIDER_JUDGE  (default: unset = no provider pin)
    """
    tier = tier.lower()
    if tier == "pro":
        model = os.environ.get("KEROS_MODEL_PRO", DEFAULT_PRO)
        provider = os.environ.get("KEROS_PROVIDER_PRO", DEFAULT_PROVIDER) or None
    elif tier == "flash":
        model = os.environ.get("KEROS_MODEL_FLASH", DEFAULT_FLASH)
        provider = os.environ.get("KEROS_PROVIDER_FLASH", DEFAULT_PROVIDER) or None
    elif tier == "judge":
        model = os.environ.get("KEROS_MODEL_JUDGE", DEFAULT_JUDGE)
        provider = os.environ.get("KEROS_PROVIDER_JUDGE", "") or None
    else:
        raise ValueError(f"unknown tier: {tier!r} (expected pro/flash/judge)")
    return TierResolved(tier=tier, model=model, provider=provider)


def openrouter_extra_body(provider: str | None) -> dict:
    """
    Build the `extra_body` payload OpenRouter expects for provider pinning.
    Empty dict if no provider pin requested.
    """
    if not provider:
        return {}
    return {"provider": {"only": [provider]}}
