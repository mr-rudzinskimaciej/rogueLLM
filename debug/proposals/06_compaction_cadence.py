"""
PROPOSAL 6 — Less frequent compaction. Cache hits + cost win.

WHY THIS IS THE BIGGEST CACHE LEVER:

DeepSeek caches identical prompt prefixes at ~10% of normal price for V3.
V4 hasn't published exact cache pricing on OpenRouter as of 2026-04-24
release — but the user notes it's "extremely cheap." Cache hits depend on:

1. Same model + same provider (we have this — pinned to first-party DeepSeek)
2. Identical PREFIX (system prompt + early user prompt up to a divergence)
3. Within cache window (typically ~24h for DeepSeek)

For NPC turns, the prompt prefix is dominated by:
- The system prompt (build_npc_system_prompt) — STABLE per actor
- Early personality fields (identity_anchor, body, wound, etc.) — STABLE
  unless compaction updates them

So between compactions, an NPC's prompt prefix is identical turn-over-turn,
modulo the world snapshot. Cache hit rate is HIGH.

When compaction fires, soul fields update → cache busts → next turn pays
full price → cache rebuilds.

CURRENT BEHAVIOR (after my earlier fix):
- npc_self_update_interval = 5 (every 5 turns × 6 NPCs)
- 100-turn run = 20 compaction events × 6 NPCs = 120 cache busts

PROPOSAL: drop the periodic trigger. Rely on overflow-only.

```python
# In runtime.py:1521, REPLACE:
should_compact = (
    len(private_log) > 5
    and (
        (config.npc_self_update_interval > 0 and engine.state.turn % config.npc_self_update_interval == 0)
        or log_tokens > config.npc_self_update_token_limit
    )
)

# WITH:
# Compaction fires only when private_log exceeds the token threshold.
# This maximizes cache hits by stabilizing prompt prefixes across more turns.
should_compact = (
    len(private_log) > 5
    and log_tokens > config.npc_self_update_token_limit
)
```

PARAMETER TUNING:
- Current `npc_self_update_token_limit = 1500` (per NPC private_log).
- An NPC adds ~400-800 tokens of private_log per turn (feel/notice/think/say/face).
- So the threshold fires every 2-4 turns currently — but ONLY when the
  modulo gate aligns. The new behavior: fires whenever an NPC's log
  actually overflows.
- Recommend bumping `npc_self_update_token_limit` from 1500 → 3000 to
  make compactions less frequent. Each NPC compacts roughly every 5-7 turns
  instead of every 5 (slightly less often, fully demand-driven).

CACHE-HIT MODEL (rough):
- Without periodic trigger: compactions fire ~12-15 times per 100 turns
  (down from 20 × 6 = 120 events).
- Most NPC turn prompts hit cache for the static portions. If 70% of an
  NPC's prompt is stable across turns, and cache is 10% of normal price,
  that's a ~63% cost reduction on the cached portion of NPC prompts.

EXPECTED COST SAVINGS:
Current: ~$1.50 / 100 turns
After cache-aware compaction cadence: ~$0.70-1.00 / 100 turns
Savings: 30-50% on cost.

EXPECTED TIME SAVINGS:
Compaction is a heavy LLM call (~5-10s per NPC, parallelized would be
~5-10s per fire). Going from ~20 fires to ~12 fires saves ~40-80s wall time
over 100 turns.

USER FRAMING: "cache is extremely cheap, so compaction less often = still
cheaper and faster." This proposal directly executes that play.

LANDING: Phase A (config-only). One-line trigger change + threshold bump.
Lowest risk, biggest cost lever in the stack.
"""
