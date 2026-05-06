# Proposals — speedup + cache-cost optimizations

Staged here so the **current 100-turn run isn't disturbed**. Combine after it lands.

## The seven changes

| # | Title | Saves | Risk | Touches |
|---|---|---|---|---|
| 1 | Parallel NPC decisions | 25-75s/turn | medium (semantic shift) | runtime.py |
| 2 | Parallel compaction fan-out | 25-75s every 5 turns | low | runtime.py |
| 3 | Async weaver (your idea) | ~all weaver latency | medium (state machine) | runtime.py + wet_run |
| 4 | `--no-thinking` for NPCs only | 20-30% NPC tail | low | wet_run_counted.py |
| 5 | Per-role temperature tuning | quality + cache | low | wet_run_counted.py |
| 6 | Less frequent compaction | cache hits + cost | low | runtime.py default |
| 7 | Cache-stable prompt structure | 50%+ cost on stable parts | medium | engine.py + wet_run |

**Weaver keeps thinking enabled** per your direction — it's the long-arc oracle, deserves max budget. Async-ifying it (proposal 3) is what lets it think long without blocking the loop.

## Recommended landing order

1. Phase A — proposals 4, 5, 6: pure-config, low-risk. Land first as smoke test.
2. Phase B — proposals 1 + 2: parallelism. Real speedup. Run a 10-turn smoke vs the current sequential baseline to compare.
3. Phase C — proposal 3: async weaver. Architectural. After Phases A+B prove the harness.
4. Phase D — proposal 7: cache-stable prompts. Biggest cost lever, biggest scope. Optional.

## What we're NOT doing
- Not rewriting engine.py NPC prompt assembly to asyncio — Maciej's call: ThreadPoolExecutor is the right tool for I/O-bound HTTP fan-out of 6 calls.
- Not changing turn-order semantics in `engine.act` — actions still resolve serially in speed order, only the *decisions* parallelize.
- Not adding caching infrastructure — DeepSeek caches automatically when prompt prefixes match. Proposal 7 is just structuring prompts to maximize that.

## Combined ETA per 100 turns (rough)

| State | Wall time | Cost |
|---|---|---|
| Current (sequential, no caching attention) | ~6h | ~$1.50 |
| After Phase A | ~5h | ~$1.20 |
| After Phase A+B | ~2h | ~$1.00 |
| After Phase A+B+C | ~1.5h | ~$0.95 |
| After all (incl. Phase D cache play) | ~1-1.5h | ~$0.50-0.80 |

Mostly the wall-time wins are from B and C; the cost wins are from D and 6.
