"""
PROPOSAL 2 — Parallelize compaction fan-out.

WHAT:
Currently in `engine/runtime.py:1521-1538`, compaction triggers fire one NPC
at a time inside the actor loop. Compaction calls are heavy (V4-pro, full
private_log + soul fields per NPC). Every 5 turns × 6 NPCs = 6 sequential
big calls.

After: hoist the compaction logic out of the actor loop. After all actors
have resolved their turn actions, gather the NPCs whose `should_compact`
fires, run their `compact_memory` calls concurrently.

WIN: 6× → 1× on compaction wall time. Saves 25-75s on each compaction turn
(every 5th turn). Total: 8-25 min over 100 turns.

DIFF SHAPE:

```python
# Inside run_round, AFTER the per-actor decision/resolution loop:
candidates_for_compaction = []
for actor in actors:
    actor_id = actor["id"]
    if actor_id == player_id:
        continue
    private_log = actor.get("private_log", [])
    log_tokens = sum(count_tokens_text(e.get("text", "")) for e in private_log) if private_log else 0
    should_compact = (
        len(private_log) > 5
        and (
            (config.npc_self_update_interval > 0 and engine.state.turn % config.npc_self_update_interval == 0)
            or log_tokens > config.npc_self_update_token_limit
        )
    )
    if should_compact:
        candidates_for_compaction.append(actor_id)

if candidates_for_compaction:
    llm = _worldbuilder_llm(config)
    if llm:
        with ThreadPoolExecutor(max_workers=min(8, len(candidates_for_compaction))) as pool:
            results = list(pool.map(
                lambda aid: (aid, compact_memory(engine, aid, llm, config.worldbuilder_model)),
                candidates_for_compaction
            ))
        for aid, result in results:
            if result.get("success"):
                audit.append(f"npc_compact:{aid}:trimmed={result['trimmed_count']}:changed={','.join(result['changed_fields'])}")
            else:
                audit.append(f"npc_compact_failed:{aid}:{result.get('message', '?')}")
    else:
        for aid in candidates_for_compaction:
            audit.append(f"npc_compact_skipped:{aid}:no_llm")
```

THREAD-SAFETY: `compact_memory` writes to `engine.state.entities[aid]` which
is per-actor — no overlap. The function reads soul fields and trims
private_log atomically per NPC.

ESTIMATED SAVINGS: 25-75s every 5 turns → ~5-15s/turn average → 8-25 min
over 100 turns.

LANDING ORDER: This after Proposal 1 (NPC decisions) — same threading
harness, less semantic risk.
"""
