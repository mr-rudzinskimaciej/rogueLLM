"""
PROPOSAL 5 — Per-role temperature tuning.

CURRENT (all 1.2):
- NPC: 1.2
- GM Breath: 1.2
- GM Settling: 1.2
- Weaver: 1.2
- Compaction (worldbuilder calls): 1.2 (inherited from llm_adapter default)

USER NOTED: above 1 is OK. So we keep most things at 1.2+.

ANALYSIS BY ROLE:

| Role | Current | Recommended | Why |
|---|---|---|---|
| NPC | 1.2 | 1.2 | Character voice variety. Keep. |
| Weaver | 1.2 | 1.4 | Long-arc imagination — go higher. Stories want strangeness. |
| GM Breath | 1.2 | 1.2 | Sensory grace-notes. Keep. |
| GM Settling | 1.2 | 1.0 | Resolution / consistency. Slight drop for stability without flatness. |
| Compaction | 1.2 | 0.4 | **Summarization task. Determinism matters here.** Low temp = stable summaries = cache-friendly downstream prompts. |
| Worldbuilder (create_*) | 1.2 | 1.0 | Creation needs care — slight drop from voice work. |

KEY: compaction is currently using a CREATIVE temperature (1.2) for an
ANALYTICAL task (summarize log → soul-deltas). Stable summaries make
downstream NPC prompts more cache-friendly across turns.

DIFF SHAPE (no current per-role temperature plumbing — needs adding):

For compaction specifically, in `engine/runtime.py` where compact_memory is
called (line 1532):

```python
# compact_memory currently signature:
# def compact_memory(engine, actor_id, llm, model)
# Need to add temperature parameter or call adapter directly.

# Either: change compact_memory signature, OR call adapter directly with low temp:
result = compact_memory(engine, actor_id, llm, config.worldbuilder_model, temperature=0.4)
```

For wet_run_counted.py, add per-role overrides:

```python
TEMP_BY_ROLE: dict[str, float] = {
    "npc": args.temp,             # 1.2 default
    "gm_breath": args.temp_gm,    # 1.2
    "gm_settling": 1.0,           # tuned down for resolution
    "weaver": 1.4,                # tuned up for arc imagination
    "worldbuilder": 1.0,
    "npc_compact": 0.4,           # determinism
}
```

ESTIMATED SAVINGS:
- Quality: not measurable in time, but compaction at 0.4 reduces drift
  in summaries → more stable soul fields → more cache hits on downstream
  NPC prompts
- Cache cost: this is the unlock for proposal 6 (less frequent compaction)

Standalone savings: $0. Stacked with proposals 6+7: significant.

LANDING: low risk, high leverage. Land in Phase A (config-only).
"""
