# Extending — building your world on Keros

This engine is a substrate. To make a game, replace the JSON in `examples/`
with your own. You almost never edit Python.

## The five files

A world is five JSON files pointed at by a `world.json`.

### `world.json` — the entry point

```json
{
  "turn": 0,
  "rng_seed": 1,
  "start_map": "your_map_id",
  "map_file": "your_map.json",
  "entities_file": "your_entities.json",
  "rules_file": "your_rules.json",
  "statuses_file": "your_statuses.json",
  "gm_notes": {
    "world_name": "Your Setting",
    "world_tone": "One paragraph. This is pasted into every NPC's system prompt. It colors their voice. Replace.",
    "story_arc": "Optional. What is under the surface.",
    "intervention_policy": "Optional. When the GM is enabled, how eager should it be?"
  }
}
```

For multi-map worlds use `"maps": { "id1": "file1.json", "id2": "file2.json" }`
instead of `map_file`. You can also pass a **list** to `rules_file` to merge
rule packs.

### Maps

ASCII grid + legend. Glyphs are arbitrary; the tags on each legend entry drive
mechanics (`walkable`, `solid`, `opaque`, `water_source`, `door`, anything you
invent).

```json
{
  "id": "market",
  "name": "Old Market",
  "desc": "Wooden stalls. Rain on canvas.",
  "grid": ["########", "#......#", "#..o...#", "########"],
  "legend": {
    "#": {"name": "wall",  "tags": ["solid", "opaque"]},
    ".": {"name": "plank", "tags": ["walkable"]},
    "o": {"name": "well",  "tags": ["walkable", "water_source"]}
  }
}
```

### Entities

`templates` are item/creature blueprints you can spawn by id. `instances` are
beings placed on maps at startup.

A being with no `personality` is a dumb AI (seeks/attacks heuristics). A being
with a `personality` block gets the full LLM prompt every turn. Personality
fields are written in **second person** — "you are...", "your hands..." — so
the model reads them as interior monologue.

Fields the NPC prompt reads (all optional — include only what matters):
`identity_anchor`, `body`, `wound`, `contradictions`, `notices_first`,
`inner_voice`, `fears`, `comfort`, `traits`, `drives`, `speech`, `knowledge`,
`plan`, plus `bonds` and `relations`.

See `examples/starter/entities.json` for a complete worked example.

### Rules

Rules match on: `verb` + actor tags + (optionally) target tags + equipped item
tags + arbitrary python-expr `condition`. They fire a list of effects.

Effects include (non-exhaustive): `move`, `damage`, `heal`, `mod_stat`,
`add_tag`, `remove_tag`, `transfer_item`, `remove_item`, `spawn`, `message`,
`door_bump`. Effects reference the matched context via `actor`, `target`,
`item`, `result`.

### Statuses

Temporary conditions attached to an entity. Each has `on_turn` and `on_expire`
effect blocks plus a `duration`.

## The worldbuilder skill (optional, powerful)

`engine/worldbuilder.py` exposes three generator functions:

- `create_character(engine, sketch, location, pos, llm_call, model)`
  Expands a one-line sketch (e.g. `"a one-eyed rat-catcher, paranoid, hoards
  pelts"`) into a full personality-bearing entity and inserts it live.
- `create_map(engine, sketch, connect_to, connect_pos, llm_call, model)`
  Generates a new map with a portal connected to an existing one.
- `create_rule(engine, sketch, llm_call, model)`
  Emits a new rule from a plain-English description.

Call these from your own scripts, or enable them via the GM's
`create_character` / `create_map` / `create_rule` actions (see the GM system
prompt in `scripts/live.py`).

**Tone override:** the worldbuilder ships with a generic default tone. Replace
it once at startup:

```python
from engine.worldbuilder import set_worldbuilder_tone
set_worldbuilder_tone("Your tone paragraph here. Cyberpunk / cozy / mythic / etc.")
```

Or pass per-call tones directly.

## Bootstrap from lore (recommended first step)

Write a `lore.md` (any length, any structure — prose, headers, constraints,
named characters). Then:

```bash
python scripts/bootstrap.py --lore lore.md --out examples/myworld/ --size small
```

The pipeline runs 7 staged LLM calls, each with scoped memory of prior
artifacts:

1. **World meta + scope** — `world_name`, `world_tone`, `story_arc`,
   `intervention_policy`, and the soft counts for later stages.
2. **Setting-specific rules** — new verbs your lore implies. Universal
   rules (move/wait/attack/eat/drink/give) are appended automatically.
3. **Setting statuses** — temporary conditions beyond the universal
   `bleeding`. Often 0.
4. **Item templates** — items that match tags the rules check.
5. **Maps** — one LLM call per map, each seeing the prior ones.
6. **Beings** — one LLM call per being, each seeing prior beings. This is
   where tone and texture live. Reuses the `CHARACTER_CREATION_SYSTEM`
   ethos (second-person personality fields, contradictions, wound, drives).
7. **Bond weave** — a final pass that adds `bonds`/`relations` between
   the generated beings so they arrive with history.

After generation the script:
- validates cross-references (inventory items exist, map placements are
  walkable, etc.)
- runs philosophy checks (no flat beings, no pure heroes/villains, ≥50%
  bonds coverage, world_tone is non-generic, personality is second-person)
- ticks the world for 2 silent turns to catch engine-level errors

The prompts live in `engine/prompts/bootstrap.py` and are **readable as a
spec**. If you'd rather use Claude Code interactively to generate files by
hand, open that module — each stage prompt explains what the engine needs
and why. The script is a programmatic fallback.

### Bootstrap CLI

| Flag                  | What                                                 |
|-----------------------|------------------------------------------------------|
| `--lore`              | path to lore.md (required)                           |
| `--out`               | output directory (required)                          |
| `--size`              | `small` / `medium` / `large` (soft counts)           |
| `--reference`         | calibration world dir (default: `examples/starter/`) |
| `--model`             | LLM for all 7 stages (or `$KEROS_BOOTSTRAP_MODEL`)   |
| `--skip-dry-run`      | skip the 2-turn engine smoke test                    |
| `--skip-philosophy`   | skip heuristic content checks                        |

## Reskin checklist

To take the starter and turn it into your own setting:

1. Copy `examples/starter/` → `examples/<yourworld>/`.
2. Edit `world.json` — new `world_name`, new `world_tone`, new arc.
3. Rewrite `map_*.json`, `entities.json`, `rules.json`, `statuses.json`.
4. Run: `python scripts/live.py -w examples/<yourworld>/world.json -p <any_id> --turns 20`
5. Iterate. Nothing in `engine/` needs to change.

## When to edit Python

- You invented a new **effect primitive** (e.g. `teleport_to_nearest_ally`).
  Add a handler in `engine/engine.py` next to the existing effect handlers.
- You want a different **GM style**. Edit `GENERIC_GM_SYSTEM` in
  `scripts/live.py` or write your own driver.
- You want **per-entity tone** rather than world-global. Call
  `build_npc_system_prompt(actor, world_tone=...)` with a per-actor string.

Everything else is JSON.
