# Orientation — read this first

This document exists for one audience: a developer who just cloned this repo,
and whatever coding assistant they work with (Claude Code, Cursor, etc.). It
is the frame. The rest of the docs assume you have it.

Budget five minutes. Then you'll know what this engine is, what it refuses
to be, and which misunderstandings cost people the most time.

---

## What Keros is

Keros is not a game engine with an AI bolted on. It is a **prompt-construction
substrate that happens to tick a grid.** Every turn, for every LLM-driven
being in the world, the engine builds a fresh system prompt from that being's
personality JSON and a fresh user prompt from the current world state, sends
it to a language model, parses the reply, and applies the result. Then the
turn advances.

There is no persistent chat history. There is no hidden state. The being is
not remembered between turns — it is *re-animated* each turn from the JSON
and the snapshot. The JSON is the soul; the snapshot is the situation; the
LLM is the breath in between.

Everything else — maps, items, rules, statuses, the GM — exists to make that
loop produce interesting behavior with minimal scripting.

## What Keros refuses to be

These refusals are load-bearing. They're not limitations to work around;
they're the shape that makes the rest work. A change that violates any of
these is almost always a change in the wrong direction.

- **Not a narrative engine.** There are no scripted quests, no plot beats,
  no "the player now enters chapter 2." Tensions exist at seed time. They
  resolve at runtime through being-driven action, not author declaration.

- **Not a chat-memory system.** NPCs do not remember conversations across
  turns the way a chatbot does. Their "memory" is the private log and
  personality fields, read fresh every turn. If you want persistence, you
  write it to the JSON.

- **Not third person.** Every personality field is second person ("you are",
  "your hands"). If that rule feels arbitrary, read `engine/contract.json`
  section `runtime.npc_system_prompt_shell` — the engine splices personality
  fields verbatim into a system prompt that begins *"You are {name}."* Third
  person silently breaks the interior-monologue spell.

- **Not code-heavy.** New creature? JSON. New interaction? JSON. New map,
  item, status, rule? JSON. If you reach for Python, you are usually either
  (a) adding a new effect primitive (legitimate — one line in `engine.py`)
  or (b) trying to script something the data could express (wrong).

- **Not affordance-driven.** NPCs receive raw ASCII maps and raw stats, not
  "you can see a bakery to your north." The LLM infers affordance from
  structure. This is a *feature*. Adding affordance prose flattens behavior.

- **Not a director-GM.** The GM is pressure, not narration. It fires when
  the scene stalls and nudges (whisper, rumor, spawn, inject). It does not
  plan arcs. See `GENERIC_GM_SYSTEM` in `scripts/live.py` for the default
  ethos.

If you find yourself adding backstory the being narrates, scripted events
by turn number, chat-history fields, or "helpful" affordance hints — stop.
Something is off.

## The tag-tier trap

Tags look identical in JSON. They are not identical in behavior. A new pair
mis-seeds here more often than anywhere else, and the engine fails silently.

The full table lives in `engine/contract.json` under `tags` — read it before
writing any JSON of your own. The short version:

- **Reserved tags** (`walkable`, `solid`, `opaque`, `alive`, `mobile`,
  `door`, `portal`, `locked`, `closed`, `item`, `inventory_source`,
  `status`) have *engine-level behavior*. The engine code checks for them
  directly. Inventing a new tag in this tier does nothing extra — the
  physics is already bound.

- **Universal-rule-matched tags** (`food`+`consumable`, `drink`+`consumable`,
  `melee`) are what the shipped universal rules look for. Put these on your
  items and the baseline verbs (eat, drink, attack) just work. Forget them
  and the being looks armed or stocked but cannot act.

- **Free-namespace tags** (everything else) only matter if a rule you wrote
  matches them. `merchant`, `cursed`, `water_source` are all free tags that
  become meaningful because a rule fires on them.

The single most common silent mis-seed: equipping a weapon without the
`melee` tag. The being looks armed, but `attack_melee` never matches. No
error, no complaint — just a being that cannot strike. Check tags first
when something doesn't fire.

## Smells and causes

When something feels wrong, look here before going deeper.

| Smell | Likely cause | Where to look |
|---|---|---|
| NPC suddenly cheerful / generic / modern | Tone bleed from a cold model or long context | `world_tone` in `world.json`; is it specific enough? |
| Being picks `wait` every turn | Drives reference verbs that don't exist | Cross-check personality.drives against rules.json verbs |
| Armed being can't attack | Weapon template missing `melee` tag | Item templates in entities.json |
| Rule never fires | Priority collision, or tag mismatch | Priority worked example in `engine/contract.json` |
| Everyone says the same thing | Private log not compacting; model hit context drift | Memory compaction in `engine/prompts/memory_compaction.py` |
| Beings ignore each other | Out of FOV radius, or different maps | `fov_radius` and `location` fields |
| GM feels intrusive | `intervention_policy` too eager, or GM model too opinionated | `gm_notes.intervention_policy`; try a smaller model |
| Third-person prose in NPC output | Personality fields written third-person | Check every personality field is "you", never "he/she" |
| Bootstrap produces flat beings | Scope too large for single lore doc | Re-run with `--size small`; or enrich lore.md |

None of these have a numeric fix. They all have a *structural* fix. That's
the pattern — when something feels wrong, change the data, not a threshold.

## Reading order

Read in this order. Stop whenever you have enough to ship.

1. **This file** — the frame.
2. **`README.md`** — install, layout, one command to run the starter.
3. **`QUICKSTART.md`** — 3 minutes to a live scene with the starter world.
4. **`EXTENDING.md`** — how to write your own world from scratch or via
   bootstrap-from-lore.
5. **`engine/contract.json`** — the physics. Tag tiers, stats, effects,
   universal rules, rule matcher fields, priority worked example. This is
   the single source of truth — if another doc disagrees with this file,
   this file wins.
6. **`engine/prompts/bootstrap.py`** — the seven-stage system prompts, read
   as a spec when you want to know exactly what the bootstrap LLM is being
   told to produce.
7. **`engine/runtime.py`** — how the turn prompt is actually assembled.
   Read this when you need to understand what an NPC sees each turn.
8. **`engine/engine.py`** — the effect primitives, FOV, rule matching, turn
   scheduler. Read only when adding a new primitive.

If you only read three things: this file, `engine/contract.json`,
`QUICKSTART.md`.

## For the Claude Code half of the pair

A few notes specifically for the agent:

- **Prefer `engine/contract.json` over restating facts.** When the human
  asks about tags, effects, stats, or the turn prompt shape, read the
  contract and cite the section. Do not paraphrase from memory — that's
  how drift happens.

- **Don't edit `engine/*.py` without a reason in the task.** The engine
  code is small and calibrated. Adding helpers, wrapping things in classes,
  or "improving" error handling here usually regresses behavior. If the
  human asks for a new effect primitive, that's legitimate — add one
  handler in `engine.py` next to the existing ones, and update
  `contract.json` in the same commit.

- **JSON is the answer most of the time.** "Add a new creature" → JSON.
  "Add a new interaction" → JSON rule. "Change a being's voice" → JSON
  personality fields. Reach for Python only when the data genuinely can't
  express it.

- **When a rule doesn't fire, check tags before logic.** Nine times out of
  ten, the issue is tag tier (see above), not rule shape.

- **The bootstrap prompts are a spec.** If the human asks you to generate
  a world without running `scripts/bootstrap.py`, open
  `engine/prompts/bootstrap.py` and follow the seven stages by hand. Each
  stage's system prompt tells you exactly what the engine needs at that
  stage.

- **Cross-reference the contract when you emit JSON.** Whenever you write
  an entity, rule, item, or map, the shape is in `engine/contract.json`.
  Use it as a checklist.

## What this document deliberately does not contain

Some knowledge is worth discovering through use. Numbers for hunger
thresholds, GM firing intervals, private-log compaction cadence — these
all exist in the code, but if we told you "a good compaction looks like
N turns retained" you would tune to that number instead of watching the
logs and feeling when an NPC starts repeating itself. That feeling is the
actual signal. Run the starter. Watch it breathe. Then find the knob when
you need it.

The same goes for the GM's intervention style. The policy is editable in
`world.json`; the default generic prompt is in `scripts/live.py`. Beyond
that, you'll learn more by running 50 turns and watching where nudges feel
heavy-handed or absent than by reading another paragraph about it.

The engine rewards watching. Preserve the watching.
