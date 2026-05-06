"""
PROPOSAL 7 — Cache-stable prompt structure. The big cost lever.

THE INSIGHT:
DeepSeek caches identical prefixes. The longer the stable prefix, the more
gets cached, the cheaper each turn becomes. Currently the engine builds
prompts with mixed-stability content interleaved.

Today's NPC prompt structure (from `engine.build_prompt`, lines 800-890):

```
You are in <map>. It is <period>.        ← STABLE within scene
You have <gold> gold.                     ← changes per turn
You have <items>.                         ← changes per turn
[drives block]                            ← changes per turn (phase advances)
You know: <knowledge>                     ← STABLE between compactions
Your plan for today: <plan>               ← STABLE between compactions
[GM whispers]                             ← changes per turn
Your recent experience: ...               ← changes per turn
What you see: ...                         ← changes per turn
```

The interleaving means almost ZERO cacheable prefix. Even though
`identity_anchor`, `body`, `wound`, `inner_voice`, etc. are stable for many
turns, they're added in `build_npc_system_prompt` (the system prompt) which
IS cached — but the user prompt is rebuilt fresh every turn with mixed-
stability content.

THE PROPOSAL:
Reorder `build_prompt` to put stable content first, volatile content last.

```
[SYSTEM PROMPT — stable per actor, cached fully]
   You are <name>. <identity_anchor>
   <body>
   <wound>
   <inner_voice>
   <speech_style>
   <fears>
   <comfort>
   <traits>
   <bonds>
   <relations>
   <knowledge>      ← was in user prompt, move to system
   <plan>           ← was in user prompt, move to system
   World tone: <world_tone>

[USER PROMPT — fresh each turn]
   It is <period>.
   You are in <map>.
   You have <gold> gold and <items>.
   [drives block]
   [GM whispers]
   Your recent experience: <last 40 entries>
   What you see: <map snapshot>
```

WHAT CHANGES:
- `engine.build_prompt` returns just the volatile portion
- `build_npc_system_prompt` absorbs the now-stable personality fields
- compaction updates personality fields → cache busts ONCE → restabilizes
- Between compactions, the system prompt is byte-identical → DeepSeek caches it

CACHE MODEL:
- System prompt size after this change: ~2-4k tokens (stable)
- User prompt size: ~3-5k tokens (volatile)
- Cache hit ratio on system prompt: ~90% (busts only on compaction)
- Cost saved on cached portion: 90% × 2-4k × 90% cache discount = ~70-85%
  of the system prompt cost effectively zeroed

EXPECTED COST SAVINGS:
Stacked with Proposal 6: ~$0.50-0.80 / 100 turns (down from $1.50 baseline).

WATCH:
- The stable system prompt depends on personality fields not changing
  between compactions. Today, build_npc_system_prompt does include some
  personality. We're moving MORE in. As long as compaction is the only
  mutator, the cache holds.
- `bonds` and `relations` mutate via `engine.act` (e.g., a relation update
  from parsed `relation` field). If we move those to system, those mutations
  become cache-busters too. Compromise: keep bonds in user prompt, move
  the slower-moving fields (knowledge, plan, wound, inner_voice) to system.

ESTIMATED EFFORT: ~2-3 hours of careful refactor + verification. Engine
internals + worldbuilder + bootstrap need consistent semantics.

LANDING: Phase D. After we've measured the gains from 1+2+3+6 individually.
This one is the polish, not the unlock.
"""
