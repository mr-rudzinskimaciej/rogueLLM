# debug/

Eval and observability for Keros. Sibling to `engine/`, never imported by it.

## Why this exists

Keros is small, slow, and full of silent bugs. The engine is doctrinally minimal —
no error noise, no built-in introspection. That doctrine is right for the engine
and wrong for the developer trying to tell whether a 5-turn run grew the world or
ran in place. This folder is the workshop where that question gets answered
without contaminating the substrate.

## The pipeline

```
engine run (5 turns)
   capture.json (replay_capture)
        slicer.py            (pure dict transforms; no LLM)
        |
        +-- wide slices/      one snapshot per 5-turn block
        |     world_growth.json
        |     emergence.json
        |     npc_behavior.json
        |     silent_bugs.json
        |
        +-- aspects/          cumulative per-subject feeds (append-only)
              character_<id>.jsonl
              gm_thread.jsonl
              errors.jsonl

   judges/run.py
        4 LLM calls in parallel, one slice each, each emits {verdict, intensity, examples}

   aggregator.py
        verdicts -> summary.md  (INTERESTING / BORING / OPEN LOOPS)
        + appends unresolved threads to loops.jsonl

   stdout: one screen per 5-turn block
```

Trigger condition: `if engine.state.turn % 5 == 0`. Run async; never blocks the
game loop.

## Layout

```
debug/
  config.py                model-tier registry (pro / flash / judge)
  slicer.py                pure transforms
  aggregator.py            verdicts -> markdown + loops persistence
  judges/
    run.py                 dispatcher: prompt + slice -> LLM -> JSON
  prompts/
    cartographer.md        world_growth judge prompt
    augur.md               emergence judge prompt
    coroner.md             silent_bugs judge prompt
    npc_observer.md        npc_behavior judge prompt
  runs/<run_id>/
    config.json
    blocks/<n>/
      slices/*.json
      verdicts/*.json
      summary.md
    aspects/
      character_<id>.jsonl
      gm_thread.jsonl
      errors.jsonl
    loops.jsonl            unresolved questions carried forward
```

## Slicing axes

Two families. Both read the same raw frames; they index differently.

**Wide slices** — per 5-turn block, all subjects.
Snapshot view. Used by per-block judges.

**Long slices** — per subject (character / gm / errors), accumulating over
runs. Append-only. Used to spot trends invisible in any single block (e.g.,
parse-fail rate climbing 1% -> 12% over 20 blocks).

## Engine instrumentation gaps (TODO before slicer is fully accurate)

The slicer reads what `replay_capture.append_frame` already saves. Three gaps
limit what we can observe without engine changes:

1. **Audit window is 16** (`replay_capture.py:83`). One turn often produces more
   than 16 audit lines, so older lines are dropped before save. Bump to 64 so a
   5-turn slice retains its full audit tail.
2. **No LLM call metadata.** `parse_ok`, `raw_len`, `elapsed_ms`, `role`,
   `actor` are not captured per call. Without them, the silent_bugs judge can't
   distinguish a 429 retry from a JSON parse failure from an empty response.
3. **No rule-collision logging.** `contract.json` flags this as the most common
   silent mis-seed; nothing in the audit log records it today.
4. **No glyph-collision logging.** Bootstrap can produce two entities sharing
   `(map_id, pos)`; the engine silently no-ops. Should append
   `glyph_collide:{eid_a}+{eid_b}@{pos}` to audit.

These four are the prerequisite engine patches. Until they ship, the
silent_bugs slice is half-blind, and the README will say so on every run.

## Model tiers

See `config.py`. Three tiers, env-var driven:

- `pro` — bootstrap, worldbuilder, GM, memory compaction (DeepSeek V4 pro,
  exact slug TBD pending research)
- `flash` — NPC turns, rule expansion, item expansion (DeepSeek V4 flash, slug TBD)
- `judge` — eval judges (Sonnet during stabilization; swap once verdict
  format proves stable)

OpenRouter `provider` field will pin first-party DeepSeek inference (provider
name TBD pending research).

## How to run

Post-run analysis. Slicer + judges + aggregator over all blocks of a capture:

```
py -m debug.run --capture reports/ds_10turn.json --run-id ds_v4_smoke
```

Useful flags:

```
--block-size 5         # turns per block; start 5, scale to 15, then 100
--blocks 0,1,2         # process only specific block indices
--no-judges            # skip LLM calls; slicer + aggregator only (free, fast)
--serial               # run judges sequentially within a block (default: parallel)
```

Output lands in `debug/runs/<run-id>/`. `summary.md` per block prints to stdout
as it's produced.

## Engine adapter changes that ship with this work

`engine/llm_adapter.py` now auto-pins to first-party DeepSeek (`provider:
{only: [deepseek]}`) whenever the model id begins with `deepseek/`. Cost drops
~4x compared to third-party DeepSeek inference on OpenRouter.

Override:

```
KEROS_PROVIDER=fireworks    # force every call through fireworks
KEROS_PROVIDER=""           # disable pinning entirely (let OpenRouter route freely)
```

If unset, only `deepseek/*` models get auto-pinned; `openai/*` and `anthropic/*`
are unaffected.
