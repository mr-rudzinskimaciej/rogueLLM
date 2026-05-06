"""
PROPOSAL 3 — Async weaver. The user's idea.

WHAT:
The weaver (GM_ANTERIOR / the Accumulation) generates long-arc tension and
campaign-octave pressure. It fires every `weaver_interval` turns (default
10). Its output is queued whispers / events / drives — designed to land
out-of-band, not to be reactive to immediate gameplay.

Currently it fires SYNCHRONOUSLY inside `run_round` at line ~1632. The whole
turn waits for the weaver to think.

The user's insight: the weaver doesn't *need* to land in the same turn it
was triggered. If we fire it as a background future and let its result land
1-2 turns later, the engine never waits. AND we can give it max thinking
budget, larger context, etc., without paying for that latency at runtime.

WIN: 100% of weaver wall time eliminated from the critical path. Weaver
thinks for 30-60s per fire; over 10 fires per 100 turns that's 5-10 min
saved on wall time.

ARCHITECTURE:

```
engine.state._pending_weaver: concurrent.futures.Future | None = None

# Each turn, BEFORE NPCs decide:
if engine.state._pending_weaver and engine.state._pending_weaver.done():
    raw_weaver = engine.state._pending_weaver.result()
    apply_weaver_output(engine, raw_weaver)  # queue whispers, drives, etc.
    engine.state._pending_weaver = None

if (config.weaver_enabled and weaver_decider
    and engine.state.turn % config.weaver_interval == 0
    and engine.state._pending_weaver is None):
    weaver_prompt = build_weaver_prompt(engine, ...)
    pool = engine.state._weaver_pool  # long-lived single-thread pool
    engine.state._pending_weaver = pool.submit(weaver_decider, weaver_prompt)
    # Don't await — keep playing
```

KEY POINTS:
- Single-threaded executor for weaver (only ever one in flight)
- If the weaver from turn 10 hasn't returned by turn 20, *don't* fire another.
  Just skip and let the in-flight one land when ready. Audit:
  `weaver_skipped:in_flight_from_turn_X`.
- Apply-on-completion happens at turn-start, BEFORE NPCs build prompts —
  so NPCs see the new whispers/drives the next turn after weaver finishes.
- Weaver gets MAX thinking enabled (per user). Other roles can have
  thinking disabled (proposal 4). Weaver alone is the slow oracle.

WIN STRUCTURE:
- Wall time: weaver latency disappears from critical path
- Quality: weaver can take 60-120s with full reasoning, no penalty
- Cost: same as before (weaver still costs $X per fire), just amortized
- Cadence: weaver "lands" 1-2 turns after triggered, slightly delayed
  but the personas already framed whispers as "messages from a slightly
  prior world" — this just makes that ontology honest.

THREAD-SAFETY:
- Single-thread pool means no concurrent weaver runs.
- The future's result is read in the main thread before NPCs decide —
  no contention with NPC threads from proposal 1.
- engine.state._pending_weaver: store the future on engine.state so the
  pool isn't garbage-collected mid-run.

CANCELLATION ON SAVE:
- If save_capture is called while a weaver is in flight, drain it
  (future.cancel() or wait briefly), or accept that the next run
  starts fresh. Note in the capture: "weaver_in_flight_at_save".

ESTIMATED SAVINGS: 5-10 min over 100 turns + better weaver quality (max
thinking). The strategic win: this is the prototype for "long-thinking
oracles" — agents that take longer than a turn to produce output. If the
pattern works, it could extend to slow-tier worldbuilder calls
(`create_map`, `create_rule`) that don't need to land same-turn either.
"""
