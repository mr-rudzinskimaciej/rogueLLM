# Keros Engine

A tiny, data-first, LLM-native roguelike/simulation engine.

**Not a game.** A substrate on which you build one. The engine does not assume
a setting, a story, or a genre. It handles grids, entities, rules, turns, FOV,
status effects, memory, and LLM-driven NPCs. Your setting lives in JSON.

> **First time here?** Read [ORIENTATION.md](ORIENTATION.md) before anything
> else. It's five minutes and covers what this engine is, what it refuses
> to be, and the mistakes that cost new pairs the most time.

## Why this shape

- **Game state IS the prompt.** Each turn, every LLM NPC receives a fresh
  snapshot of the world: ASCII map, its own stats, visible entities, recent
  events, available actions. The prompt is the memory. No fragile multi-turn
  chat state.
- **Data over code.** Maps, entities, rules, statuses, worlds — all JSON.
  Adding a new creature, item, or interaction rarely touches Python.
- **NPCs see what the player sees.** Raw numbers and tile grids. No affordance
  prose. The model infers intent from structure.
- **GM as pressure, not script.** An optional game-master LLM nudges the world
  when it stalls — whisper, rumor, spawn, inject, narrate — never scripts
  outcomes.
- **Worldbuilder skill.** `create_character`, `create_map`, `create_rule` —
  brief sketch in, validated JSON out, hot-inserted into the world.

## Install

```bash
pip install -r requirements.txt
cp .env.example .env   # add your key
```

## Run the starter

```bash
python scripts/live.py --world examples/starter/world.json --player wanderer --turns 20
```

With no API key set, NPCs all return `wait` and you can still verify the
engine loads and ticks. Set `KEROS_API_KEY` to bring them alive.

## Or: bootstrap your own world from lore

Drop a `lore.md` describing your setting (prose, free-form) and generate a
full seed set — map, beings, rules, items, statuses — in one shot.

```bash
python scripts/bootstrap.py --lore my_setting.md --out examples/myworld/ --size small
```

The pipeline runs seven staged LLM calls that each see only the prior
artifacts they need to stay consistent, then validates the result and
silently ticks the world for two turns to catch engine errors. See
[engine/prompts/bootstrap.py](engine/prompts/bootstrap.py) — the prompts are
also a readable spec if you'd rather have Claude Code generate the files by
hand.

## Layout

```
engine/            Pure engine. Reusable. Do not edit unless you mean to.
  engine.py        Rule interpreter, effect primitives, FOV, state.
  runtime.py       Turn scheduling, NPC/GM prompt assembly, resolver.
  loader.py        JSON world bundle loader.
  metalang.py      JSON schema validation.
  llm_adapter.py   OpenAI-compatible client. Reads env vars only.
  worldbuilder.py  create_character / create_map / create_rule.
  replay_capture.py  Frame-by-frame replay snapshots.
  prompts/         Specialized prompts (memory compaction, etc).
scripts/
  live.py          Generic CLI driver. No hardcoded paths.
examples/
  starter/         Smallest working world — three beings at a crossing.
```

## Next steps

- [ORIENTATION.md](ORIENTATION.md) — frame, refusals, tag tiers, smells. Read first.
- [QUICKSTART.md](QUICKSTART.md) — run the starter in 3 minutes.
- [EXTENDING.md](EXTENDING.md) — fork the starter, or bootstrap from lore.
- [engine/contract.json](engine/contract.json) — the canonical physics.

## License

MIT. See [LICENSE](LICENSE).
