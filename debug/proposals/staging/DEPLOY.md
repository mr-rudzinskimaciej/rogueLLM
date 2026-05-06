# Deployment notes — staged speedup bundle

Two files modified in `debug/proposals/staging/`. Both AST-parse cleanly.

## What's in the bundle

| Proposal | File | Change |
|---|---|---|
| 1 | `runtime.py` | Parallel NPC decisions via `ThreadPoolExecutor` |
| 2 | `runtime.py` | Parallel compaction fan-out, hoisted out of actor loop |
| 3 | `runtime.py` | Async weaver: dispatch on bg pool, drain at start of next round |
| 4 | `wet_run_counted.py` | `THINKING_BY_ROLE` dict — weaver ON, NPCs/breath OFF |
| 5 | `wet_run_counted.py` + `runtime.py` | `TEMP_BY_ROLE` (compaction 0.7, weaver 1.4, settling 1.0) + `compact_memory` accepts `temperature` param |
| 6 | `runtime.py` | Compaction trigger overflow-only; periodic-by-turn dropped (cache-friendly) |

Skipped: Proposal 7 (cache-stable prompt restructure). Phase D, after measuring 1-6 delta.

## Deploy command (run AFTER current 100-turn capture lands)

```bash
cd C:/Users/User/Documents/Keros/RogueLLM/rogueLLM
cp debug/proposals/staging/runtime.py engine/runtime.py
cp debug/proposals/staging/wet_run_counted.py scripts/wet_run_counted.py
```

Or use git diff to inspect changes first:

```bash
cd C:/Users/User/Documents/Keros/RogueLLM/rogueLLM
diff -u engine/runtime.py debug/proposals/staging/runtime.py | less
diff -u scripts/wet_run_counted.py debug/proposals/staging/wet_run_counted.py | less
```

## Smoke test before scaling

After deploy, run a 10-turn smoke against `examples/droga_smoka_v3` to verify:

```bash
set -a && . ./.env && set +a
py scripts/wet_run_counted.py \
   -w examples/droga_smoka_v3/world.json --turns 10 \
   --model deepseek/deepseek-v4-flash --model-gm deepseek/deepseek-v4-pro \
   --enable-gm --enable-weaver \
   --capture reports/parallel_smoke.json --delay 0
```

Watch for:
- **Wall time vs the 6h baseline.** Expect ~12-25 min for 10 turns at this scale (from ~36 min baseline).
- **`weaver_dispatched` and `weaver_landed` audit lines** — async weaver should fire at turn 10, land somewhere in turn 11-12.
- **`npc_compact` events** — should fire less frequently than every 5 turns, only when overflow.
- **No new silent bugs** — coroner verdict on the 10-turn slice.

## Known minor issue: compaction audit role label

When parallel compaction fires, the wet_run_counted instrumentation will tag
those calls with `role: npc` in audit (the last role setter was the parallel
NPC fan-out). The TEMPERATURE (0.7) is still correctly applied because
`compact_memory(temperature=0.7)` passes it explicitly through the LLM call.

To fix the audit label fidelity later, plumb a role-aware
`worldbuilder_llm` wrapper through `RuntimeConfig.worldbuilder_llm`. Not
worth doing now; doesn't affect actual behavior.

## Rollback

```bash
cd C:/Users/User/Documents/Keros/RogueLLM/rogueLLM
git checkout HEAD -- engine/runtime.py scripts/wet_run_counted.py
```

(Or just `cp` the pre-modification originals back — they're in git.)

## Expected combined effect on 100-turn run

- Wall time: ~6h baseline → **~1.5-2h** (parallelism + async weaver)
- Cost: ~$1.50 baseline → **~$0.95** (cache hits from less-frequent compaction)
- Quality: weaver gets full thinking budget without blocking; compaction stable across turns

## Persona consensus that drove this

Both Maciej and Repligate independently said:
- Same-turn cross-NPC reactivity loss is *thin* — propagate cross-turn through private_log instead
- ThreadPoolExecutor is the right tool (not asyncio rewrite)
- Speed isn't the prize; **A/B testable iteration cadence** is
- Async weaver is the prototype for "long-thinking oracles" pattern
