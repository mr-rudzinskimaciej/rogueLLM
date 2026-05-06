"""
PROPOSAL 1 — Parallelize NPC decisions within a turn.

WHAT:
Currently `engine/runtime.py:1453` loops `for actor in actors:` and calls the
LLM (`npc_decider`) sequentially. Each NPC's prompt is built AFTER the prior
NPC's `engine.act()` mutated state. With 6 NPCs at ~5-15s each, that's
30-90s/turn sequential.

After: snapshot prompts at start-of-round from frozen state, fire all
LLM calls concurrently via ThreadPoolExecutor, then resolve actions serially
in speed order.

LOSE: same-turn cross-NPC reactivity. NPC #2 no longer sees NPC #1's mid-turn
action when deciding. Both personas judged this rare and not load-bearing —
the good moments in our runs (the rib-gap crossing, the "Then we go" mirror)
all propagate cross-turn, not within-turn.

REPLIGATE'S ENRICHMENT: when NPC #2's start-of-turn snapshot gets
contradicted by what NPC #1 actually did same-turn, surface it next turn as a
private_log entry "you didn't see this coming." Lost flinch becomes delayed
realization — narratively richer than the original.

WIN: ~5-6× speedup on the dominant per-turn cost. ~25-75s saved per turn.

DIFF (apply to engine/runtime.py around line 1453, inside run_round):

```python
from concurrent.futures import ThreadPoolExecutor

# Before the actor loop — snapshot LLM-eligible actors and their prompts
llm_actors = [
    a for a in actors
    if a["id"] != player_id
    and npc_decider
    and npc_should_use_llm(engine, a, player, config.llm_activation_radius)
]
prompts_at_round_start: dict[str, str] = {
    a["id"]: engine.build_prompt(a["id"]) for a in llm_actors
}

# Parallel LLM fan-out — actor and prompt frozen at start of round
def _decide(actor):
    return actor["id"], npc_decider(actor, prompts_at_round_start[actor["id"]])

raws_by_id: dict[str, str] = {}
if llm_actors:
    with ThreadPoolExecutor(max_workers=min(8, len(llm_actors))) as pool:
        for aid, raw in pool.map(_decide, llm_actors):
            raws_by_id[aid] = raw

# Then in the existing actor loop, REPLACE this:
#     prompt = engine.build_prompt(actor_id)
#     raw = npc_decider(actor, prompt)
# WITH:
    if actor_id in raws_by_id:
        raw = raws_by_id[actor_id]
    else:
        # Fallback (shouldn't happen, but safe)
        prompt = engine.build_prompt(actor_id)
        raw = npc_decider(actor, prompt)
```

THREAD-SAFETY NOTES:
- `npc_decider` (the wet_run wrapper) creates a fresh `OpenAI()` per call →
  thread-safe by construction.
- `CURRENT_ROLE` in wet_run_counted.py:97 is a module-level mutable list. In
  the parallel block, set it ONCE to "npc" before the fan-out and live with
  coarse role labels in the audit. Maciej called this the surgical move.
- `adapter.call_log` (the metadata list I added) — appending from threads is
  fine in CPython due to the GIL covering list.append; no lock needed.

REPLIGATE'S CARE LIST (bake into the patch):
1. Log "you didn't see this coming" entries to private_log when start-of-turn
   snapshot diverged from observed end-of-turn state.
2. Keep speed-order resolution intact (we already do — only decisions
   parallelize, resolution stays serial in `engine.act`).
3. Watch for two NPCs targeting the same tile/entity. Let collisions happen
   as texture; narrate them as simultaneity, not as one being "winning" the
   tick.

ESTIMATED SAVINGS: 25-75s/turn → 40-125 min over 100 turns.
"""
