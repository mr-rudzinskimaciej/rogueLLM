"""
Bootstrap prompts — seed a new Keros world from a lore document.

This module is DUAL PURPOSE:

  1. Machine-readable: imported by scripts/bootstrap.py to drive a staged
     generation pipeline that produces a playable seed set.

  2. Human/agent-readable: if you run this by hand (e.g. via Claude Code,
     Cursor, or any chat interface) the prompts and preamble here are a
     complete spec. You can read them, understand what the engine expects,
     and generate each file yourself, pasting outputs into the right paths.

The seven stages below each have a system prompt (what the generator must
know about the engine) and a user-prompt builder (how to assemble the
per-stage context from lore + prior stages).

IMPORTANT: Prompts here are a first pass meant to be iterated on. Sections
marked [ITERATE] are the ones most likely to need tuning after real runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ============================================================================
# Canonical engine contract — single source of truth for tags/effects/stats/
# universal rules/runtime prompt shape. Loaded once at import. Every stage's
# system prompt cites it so the amnesiac LLM sees the engine's physics
# directly rather than inferring them from prose.
# ============================================================================

_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contract.json"
CONTRACT: dict[str, Any] = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def _contract_section(key: str) -> str:
    """Pretty-print a top-level contract section for inclusion in a prompt."""
    return json.dumps(CONTRACT[key], indent=2, ensure_ascii=False)


# ============================================================================
# Shared preamble — injected into every stage's system prompt.
# Explains the engine's design philosophy so the generator knows what a
# "good" seed looks like. [ITERATE: tighten as we learn which parts the
# generators ignore vs. which ones they internalize.]
# ============================================================================

SHARED_PREAMBLE = """\
You are seeding a new world for the Keros engine — a data-first, LLM-native
simulation substrate where NPCs are driven by language models reading their
own JSON personalities as interior monologue.

Understand these five truths before you write anything:

1. THE GAME IS DATA, NOT CODE. Every map, being, rule, item, and status is a
   JSON file. The engine interprets. Adding a new creature or interaction
   rarely touches Python. This means your seed has to be MECHANICALLY
   COMPLETE: tags that rules can match, items that verbs can consume, drives
   that reference verbs that actually exist.

2. BEINGS READ THEIR OWN PERSONALITY AS FIRST-PERSON INTERIOR MONOLOGUE.
   Every personality field is written in SECOND PERSON — "you are", "your
   hands", "you remember". Third person ("she is", "her hands") breaks the
   spell. The LLM stops being the being and starts narrating a character.
   This single rule is non-negotiable.

3. SPECIFICITY BEATS ARCHETYPE. "Paranoid merchant" collapses into a flat
   type within five turns. "You overcharge on the second day of a traveller's
   journey because travellers always overpay then" has texture the LLM can
   play. Every field earns its place by being concrete.

4. CONTRADICTIONS CREATE LIFE. Two real needs colliding — "want company but
   do not trust company" — generate variance. A single motivation produces a
   puppet. Every being gets 2-3 internal tensions.

5. NO HEROES, NO VILLAINS, NO ARCS THAT RESOLVE INSIDE THE LORE. This is a
   living world, not a story. Beings are trying to survive. Wounds shape
   them. Goals are small and daily. Leave the big questions open. The player
   and GM resolve tensions at runtime; you plant them."""


# ============================================================================
# Failure modes — what LLMs do by default that you must resist.
# [ITERATE: add more as we see real failures.]
# ============================================================================

FAILURE_MODES = """\
RESIST THESE DEFAULTS:

- Writing "kind" or "brave" NPCs unless the lore specifically asks. Default
  to guarded, pragmatic, self-interested. Generosity is earned, never given.

- Resolving the setting's tensions. If the lore says the town is dying, the
  seed shows a town in the middle of dying — not a town about to be saved.

- Generating generic names when the lore has its own vocabulary. Read the
  lore's proper nouns and extend from them. Never replace them.

- Producing prose when JSON is asked. Output ONLY valid JSON in the shape
  specified. No markdown fences. No commentary. No "Here is the JSON:".

- Flattening contradictions into single motivations. If a being "wants
  freedom but cannot leave", keep BOTH. Do not resolve the tension.

- Bloat. A small seed with texture beats a large seed with filler. Prefer
  three memorable beings over eight forgettable ones.

- Inventing proper nouns for characters the lore already names. If the lore
  names a character, use that name. Extend their backstory; do not replace."""


# ============================================================================
# Physics handbook — slices of contract.json injected into stage prompts.
# Different stages need different slices; we construct them once here.
# ============================================================================

RUNTIME_EXCERPT = (
    "=== THE RUNTIME PROMPT SHELL (what beings actually receive at play time) ===\n"
    "Your JSON is not decoration. The engine splices it into the following "
    "system prompt every turn, then sends a fresh user prompt with the map, "
    "stats, events, and available actions. Write JSON that makes this shell "
    "SOUND LIKE A PERSON when assembled.\n\n"
    "SYSTEM PROMPT SHELL (assembled from personality fields + world_tone):\n"
    "```\n"
    + "\n".join(CONTRACT["runtime"]["npc_system_prompt_shell"])
    + "\n```\n\n"
    "USER PROMPT EACH TURN — sections in order:\n"
    + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(CONTRACT["runtime"]["npc_user_prompt_shape"]["sections_in_order"]))
    + "\n\nREQUIRED RESPONSE FORMAT (the being must emit this exact shape each turn):\n"
    "```\n" + CONTRACT["runtime"]["npc_response_format"] + "\n```"
)

TAGS_HANDBOOK = (
    "=== TAG ONTOLOGY (three tiers — the amnesiac's biggest blind spot) ===\n"
    + CONTRACT["tags"]["_doc"] + "\n\n"
    + _contract_section("tags")
)

STATS_HANDBOOK = (
    "=== STATS CONTRACT (engine-privileged vs free keys) ===\n"
    + CONTRACT["stats"]["_doc"] + "\n\n"
    + _contract_section("stats")
)

EFFECTS_HANDBOOK = (
    "=== EFFECT PRIMITIVES (exhaustive list the engine can execute) ===\n"
    + CONTRACT["effects"]["_doc"] + "\n\n"
    + _contract_section("effects") + "\n\n"
    "=== RULE MATCHER FIELDS ===\n"
    + _contract_section("rule_matcher_fields") + "\n\n"
    "=== PRIORITY — WORKED EXAMPLE ===\n"
    + CONTRACT["priority_worked_example"]["_doc"] + "\n\n"
    + _contract_section("priority_worked_example")
)

PERSONALITY_HANDBOOK = (
    "=== PERSONALITY FIELD SPEC (all second person, all) ===\n"
    + CONTRACT["personality_fields"]["_doc"] + "\n\n"
    + _contract_section("personality_fields")
)


# Which contract slices each stage needs. Stages see what they must be
# mechanically correct about, nothing more — keeps prompts focused.
_STAGE_HANDBOOKS: dict[int, list[str]] = {
    1: [RUNTIME_EXCERPT],                                       # meta sets tone — needs to see what tone will feed into
    2: [EFFECTS_HANDBOOK, TAGS_HANDBOOK, STATS_HANDBOOK],       # rules reference all three
    3: [EFFECTS_HANDBOOK, STATS_HANDBOOK],                      # statuses are effects + stat math
    4: [TAGS_HANDBOOK, STATS_HANDBOOK],                         # items rely on tags (to be matched) and stats (to be consumed)
    5: [TAGS_HANDBOOK],                                         # maps = tiles with tags
    6: [RUNTIME_EXCERPT, PERSONALITY_HANDBOOK, TAGS_HANDBOOK, STATS_HANDBOOK],  # the load-bearing stage
    7: [RUNTIME_EXCERPT, PERSONALITY_HANDBOOK],                 # bonds must breathe in the same register
}


# ============================================================================
# Helper — build a stage system prompt by prefixing the shared preamble
# and the contract slices the stage needs.
# ============================================================================

def _system(stage_num: int, stage_name: str, stage_specific: str) -> str:
    handbooks = "\n\n".join(_STAGE_HANDBOOKS.get(stage_num, []))
    return (
        f"{SHARED_PREAMBLE}\n\n"
        f"{FAILURE_MODES}\n\n"
        f"{handbooks}\n\n"
        f"=== STAGE {stage_num}/7: {stage_name.upper()} ===\n\n"
        f"{stage_specific}"
    )


# ============================================================================
# Universal assets — shipped verbatim, not generated.
# These are the mechanical primitives every playable world needs. Generators
# layer setting-specific rules/statuses ON TOP of these.
# ============================================================================

# Universal rules and statuses live in contract.json as physics, not prompts.
# These references are convenience re-exports for the orchestrator.
UNIVERSAL_RULES: list[dict[str, Any]] = CONTRACT["universal_rules"]["rules"]
UNIVERSAL_STATUSES: dict[str, Any] = CONTRACT["universal_statuses"]["statuses"]


# ============================================================================
# STAGE 1 — World metadata + scope plan
# The director stage. Reads the lore and decides the seed's shape.
# ============================================================================

STAGE_1_SYSTEM = _system(1, "world meta + scope plan", """\
Your job at this stage is to READ THE LORE and output two things:

  1. world_meta — the world's name, tone, arc, and GM intervention policy.
  2. scope — how many maps, beings, items, rules, statuses to generate.

The world_tone is the single most important string you will write. It is
injected verbatim into EVERY being's system prompt for the rest of the
world's life. It sets the shared dialect of the setting. One paragraph.
Second person where natural. Concrete sensory detail, not abstractions.

BAD world_tone (generic):
  "This is a world of mystery and adventure where heroes face challenges."

GOOD world_tone (from the starter):
  "The world is what it is. People cooperate transactionally. Kindness is
  real, or a trap, or a risk. You decide each time. You do not volunteer
  information that could cost you. You act from what your body and memory
  already know."

The story_arc is NOT a plot. It is a tension that exists at the moment of
seeding. "Three strangers share a crossing for a day. None of them planned
to meet." — not "The heroes must stop the dark lord."

The intervention_policy guides the GM LLM at runtime. Write it as a
description of WHAT THE GM DOES in this setting — the channels, textures,
and kinds of action available to it — not as a description of how often
it stays silent. The GM does not speak to the player; it acts through the
world. In a dragon's gut world the GM acts through tremors in the flesh,
rumours from other organs, whispers inside a being's own skull. In a
trading-post world the GM acts through weather, travellers arriving,
market rumours. Name the channels specific to this setting.

BAD intervention_policy (silence-shaped, collapses to pass):
  "Fire rarely. Prefer silence. Only act when beings are stuck. Let the
  beings carry the tension themselves."

GOOD intervention_policy (action-shaped, names what the GM can do):
  "Your interventions shape the WORLD, not the meta-conversation — you
  do not speak to the player, you act through [settings-specific
  channels: the dragon's flesh / the weather / the road / the tides].
  Act through: world-events (tremors / dust storms / tides), rumours
  (news from other places), whispers to beings (felt as intuition),
  and stat-nudges when a body's reality needs to shift. Weight toward:
  [lore-specific moments — name two or three]."

This framing solves a prompt-attractor problem: if the intervention_policy
reads as a silence-reward, the GM collapses to pass() regardless of what
else you tell it. Describe what it DOES and the model will do that.

SCOPE — soft counts, not hard.
  small:  1 map,  ~3 beings,  ~6 items,  ~2 setting-specific rules
  medium: 2 maps, ~5 beings, ~10 items,  ~4 setting-specific rules
  large:  3 maps, ~8 beings, ~14 items,  ~6 setting-specific rules

The lore can push back. If the lore names five characters, respect that
even on a "small" run. If the lore implies one tiny location, don't invent
extras. Explain your deviation in scope.notes.

// [FUTURE — when the pipeline matures, this stage could also emit a
// `lore_digest` keyed by consumer (places / named_characters /
// implied_verbs / proper_nouns / constraints / tone_samples), so later
// stages could consume only the slice they need instead of the whole
// lore document. Kept on hold for now: full lore to every stage is
// safer until we have seen real failures from lore-dilution. Revisit
// after several bootstrap runs show which stages get distracted by
// irrelevant lore sections.]

OUTPUT FORMAT (exactly this shape, valid JSON, nothing else):

{
  "world_meta": {
    "world_name": "string",
    "world_tone": "one paragraph — injected into every NPC system prompt",
    "story_arc": "the tension that exists at seed time, not a plot",
    "intervention_policy": "how the GM should behave in this setting"
  },
  "scope": {
    "maps": 1,
    "beings": 3,
    "item_templates": 6,
    "setting_rules": 2,
    "setting_statuses": 1,
    "notes": "why I chose these counts given the lore"
  }
}""")


def build_stage_1_prompt(lore: str, size_hint: str, reference_world: dict[str, Any]) -> str:
    ref_pretty = _json(reference_world)
    return (
        f"SIZE HINT: {size_hint} (soft — override if the lore demands)\n\n"
        f"=== REFERENCE: a working starter world.json (DIFFERENT SETTING — "
        f"match the shape and depth of the world_tone/story_arc, NOT the content) ===\n"
        f"{ref_pretty}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Output JSON only."
    )


# ============================================================================
# STAGE 2 — Setting-specific rules
# Universal rules are appended by the orchestrator. You only write the ones
# this setting specifically needs.
# ============================================================================

STAGE_2_SYSTEM = _system(2, "setting-specific rules", """\
Your job: output rules specific to THIS setting. The engine already has
universal rules for move, wait, attack (melee), give, eat (food), and
drink (waterskin). You do not need to re-write those.

A rule is a JSON object that fires when a matching verb is attempted. It
checks: actor tags, target tags, equipped item tags, an optional python-expr
condition, and target adjacency. It fires a list of effects.

Effect primitives available (non-exhaustive): move, damage, heal, mod_stat,
add_tag, remove_tag, transfer_item, remove_item, spawn, message, door_bump.
Effects reference context via {actor.*}, {target.*}, {item.*}, {result.*}.

WHY SETTING-SPECIFIC RULES MATTER: they define what verbs BEINGS CAN WANT.
If the lore implies "bribery is how business gets done", you need a `bribe`
rule or beings will never try it. If the lore has forging, you need forge.
If beings are supposed to be able to drink from a well (a `water_source`
tile), you need that rule here — universal rules only cover consumable
items, not tiles.

Good rules are triggered by verbs beings will actually think to use. Think
about each being's drives (coming in stage 6) and make sure the verbs they
would naturally want are covered.

OUTPUT: a JSON array of rule objects. Shape of each:

{
  "id": "snake_case_id",
  "verb": "short_verb",
  "actor_has": ["optional", "tags"],
  "actor_equipped": ["optional"],
  "target_has": ["optional"],
  "item_has": ["optional"],
  "target_near": true,                    // optional, require adjacency
  "condition": "python-expr, optional",    // e.g. "actor.stats.gold >= 10"
  "priority": 0,
  "effects": [
    { "effect": "message", "text": "..." }
  ]
}

One useful rule almost every setting needs: drink from a water_source tile
(well/stream/fountain). Include it unless the setting has no natural water.

Output ONLY the JSON array. No commentary.""")


def build_stage_2_prompt(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], reference_rules: list[dict[str, Any]]) -> str:
    return (
        f"=== WORLD META (already decided) ===\n{_json(world_meta)}\n\n"
        f"=== SCOPE ===\nYou will write approximately {scope.get('setting_rules', 2)} "
        f"setting-specific rules.\n\n"
        f"=== REFERENCE: example rules.json from the starter (a DIFFERENT setting) ===\n"
        f"{_json(reference_rules)}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Output the JSON array of setting-specific rules."
    )


# ============================================================================
# STAGE 3 — Setting-specific statuses
# [ITERATE: this stage is least developed — most settings need 0-2 statuses.]
# ============================================================================

STAGE_3_SYSTEM = _system(3, "setting-specific statuses", """\
A status is a temporary condition attached to an entity. It has per-turn
effects and per-expire effects. The engine already handles "starving" and
"dehydrated" internally (from hunger/thirst thresholds) and ships a
"bleeding" universal status.

You write any setting-specific statuses the lore suggests. Examples:
  - "possessed" (a mind is not its own)
  - "marked" (someone/something has fixed on you)
  - "frostbitten" (cold damage accumulates)
  - "in_debt" (a social status that rules can reference)

Most settings need 0-2. If the lore doesn't suggest any, output an empty
object {}.

OUTPUT: JSON object keyed by status id. Shape of each value:

{
  "id": "status_id",
  "name": "Display Name",
  "tags": ["status", "dot" or other descriptor],
  "stats": { "duration": 5, "dmg_per_turn": 2 },
  "on_turn":   [{"effect": "...", ...}],
  "on_expire": [{"effect": "message", "text": "..."}]
}

Output ONLY valid JSON.""")


def build_stage_3_prompt(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], rules: list[dict[str, Any]], reference_statuses: dict[str, Any]) -> str:
    return (
        f"=== WORLD META ===\n{_json(world_meta)}\n\n"
        f"=== RULES GENERATED SO FAR (setting-specific) ===\n{_json(rules)}\n\n"
        f"=== REFERENCE: statuses.json from the starter ===\n{_json(reference_statuses)}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Target: ~{scope.get('setting_statuses', 1)} statuses. Output the JSON object."
    )


# ============================================================================
# STAGE 4 — Item templates
# ============================================================================

STAGE_4_SYSTEM = _system(4, "item templates", """\
Templates are reusable item blueprints that instances (in entity
inventories, or spawned into maps) reference by id.

Item templates exist to GIVE BEINGS THINGS TO DO WITH THEIR RULES. If you
have a `drink_from_skin` rule matching on tag `drink`, beings need at least
one template with that tag. Hunger → food items. Trade → items with a
`value` stat. Combat → weapons with `melee` + `dmg`. Locks → keys with a
matching `key_id`.

Do NOT generate items that have no rule to consume them. "Mysterious Orb"
with no rule matching "orb" is dead weight.

A template is:
{
  "id": "snake_case",
  "name": "Display Name",
  "glyph": "%",                           // single char
  "tags": ["item", "food", "consumable"], // rules match these
  "stats": { "heal": 4, "value": 3 }      // rules read these
}

OUTPUT: JSON object keyed by template id. No commentary.""")


def build_stage_4_prompt(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], rules: list[dict[str, Any]], reference_items: dict[str, Any]) -> str:
    return (
        f"=== WORLD META ===\n{_json(world_meta)}\n\n"
        f"=== ALL RULES (universal + setting) — items must serve these verbs ===\n"
        f"{_json(rules)}\n\n"
        f"=== REFERENCE: item templates from the starter ===\n{_json(reference_items)}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Generate ~{scope.get('item_templates', 6)} item templates. "
        f"Output the JSON object keyed by id."
    )


# ============================================================================
# STAGE 5 — Map(s)
# ============================================================================

STAGE_5_SYSTEM = _system(5, "map", """\
A map is an ASCII grid plus a legend. Glyphs are arbitrary single characters
in the grid; the legend maps each glyph to its tags.

Mechanics are driven by TAGS on legend entries, not by the glyph itself.
`walkable` — beings can move onto it.
`solid`    — blocks movement.
`opaque`   — blocks line-of-sight (FOV).
`water_source` — beings can use the `drink` verb if a rule matches.
`door`     — interactive, may open/close.
Invent any additional tags your rules will reference.

CONSTRAINTS:
- 8-14 wide, 5-9 tall. Bigger maps make runtime prompts huge.
- Always wall-bordered (solid perimeter).
- At least one distinctive feature tile (well/fire/altar/etc.) that invites
  action. A blank floor room is dead space.
- Match the lore's vocabulary: if the setting is a space station, use a
  corridor/bulkhead legend, not stone/wall.

OUTPUT shape per map:
{
  "id": "snake_case",
  "name": "Display Name",
  "desc": "1-2 sentences of atmosphere — sensory, concrete",
  "grid": ["row_string", "row_string", ...],
  "legend": {
    "#": {"name": "wall",  "tags": ["solid", "opaque"]},
    ".": {"name": "floor", "tags": ["walkable"]}
  }
}

If the scope requested >1 map, you will be called multiple times, once per
map, with the prior maps visible so you can maintain spatial coherence.

Output ONLY the JSON object for this one map. No commentary.""")


def build_stage_5_prompt(lore: str, world_meta: dict[str, Any], map_index: int, total_maps: int, prior_maps: list[dict[str, Any]], reference_map: dict[str, Any]) -> str:
    prior_section = ""
    if prior_maps:
        prior_section = f"=== MAPS ALREADY GENERATED (do not duplicate) ===\n{_json(prior_maps)}\n\n"
    return (
        f"=== WORLD META ===\n{_json(world_meta)}\n\n"
        f"This is map {map_index + 1} of {total_maps}.\n\n"
        f"{prior_section}"
        f"=== REFERENCE: a map from the starter (DIFFERENT setting) ===\n{_json(reference_map)}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Generate this map as a JSON object."
    )


# ============================================================================
# STAGE 6 — Beings (the load-bearing stage)
# One LLM call per being so each can see prior beings and reference them.
# Reuses the ethos of engine/worldbuilder.py:CHARACTER_CREATION_SYSTEM.
# ============================================================================

STAGE_6_SYSTEM = _system(6, "beings (one call per being)", """\
You are creating ONE being at a time. You see the beings already created so
you can reference them or deliberately diverge. You do NOT re-generate prior
beings.

The PERSONALITY FIELD SPEC is already in the handbook above (see the
'personality_fields' section). Read it carefully — every field is SECOND
PERSON, and that is what makes the runtime prompt sound like a person on
turn 1.

=== TURN-ONE SIMULATION — THINK BEFORE YOU WRITE ===

Before you generate this being, simulate turn 1 in your head.

  This being will wake up at position {pos} on map {location}. Its first
  user prompt will list: its body state (including hunger/thirst), gold,
  inventory, drives, knowledge, plan, then the visible map with legend,
  then nearby beings, then 'You could:' followed by the engine-computed
  list of available actions. It must respond Feel/Notice/Think/Face/
  Speak/Do within five seconds of reading all that.

=== DRIVES ARE NOT CHARACTER-SHEET DECORATION ===

Your job: write a being whose DRIVES map onto AT LEAST ONE verb that will
be available on turn 1. Drives are the hooks the being's system prompt
gives the LLM at play time. If drives cannot be acted on with the verbs
and items this world actually has, the being will pick 'wait' turn after
turn and the scene will die.

This means:
  - Every drive must be achievable given the verbs listed below AND the
    items/tiles the being can reach from its starting position.
  - At least one drive should be actionable on turn 1 — not a long-term
    project, but something the being could DO in its first five minutes.
  - Drives reference concrete subjects (item names, map features, other
    being ids) — not abstract goals.

=== TWO WORKED EXAMPLES (in the starter's tone, for calibration) ===

Example A — The Pedlar at the Crossing (from the starter):
  drives: ["sell at least two items today", "drink enough water",
           "reach the next town by dusk"]
  // Why this works: "sell" hooks a trade verb; "drink enough water"
  // hooks either a drink verb on the adjacent well tile or use-waterskin;
  // "reach the next town by dusk" is a long-term frame the plan field
  // makes concrete. Turn 1 has at least two immediate verbs available.

Example B — The Guard at the Crossing (from the starter):
  drives: ["eat something today", "hear news of the western road",
           "stay on post"]
  // Why this works: "eat something today" is turn-1 actionable the
  // moment a being with bread is adjacent (the Pedlar, in this case);
  // "hear news" hooks listen/speak interactions with anyone who arrives;
  // "stay on post" creates a spatial tension the patrol plan resolves.
  // The guard's hunger stat is elevated so the drive has physical
  // urgency from turn 1.

Notice the pattern: each drive is either (a) already actionable with
a verb that exists, or (b) waiting for another being to be present for
a social verb to fire. Never write drives that require a verb this
world does not have.

=== ENTITY INSTANCE SHAPE ===

A being is a JSON entity instance. Required fields beyond personality:

  id: snake_case, unique across all beings.
  name: Display name. Match the lore's vocabulary.
  glyph: Single character (usually first letter of the name).
  tags: MUST include ["alive", "mobile", "inventory_source"] plus any
    setting-appropriate tags ("human", "merchant", "ghost", etc.)
  stats: {hp, max_hp, dmg, arm, spd, gold, hunger, thirst}. See the STATS
    handbook above for thresholds. Most beings start with hunger/thirst
    around 20-40 — unless the lore implies severe deprivation.
  pos: [x, y]. Must be on a walkable tile of the assigned map. Do NOT
    collide with another being's pos.
  inventory: Array of template ids — items the being carries at seed time.
    Must reference templates that exist. Inventory should support drives.
  equipped: {weapon: template_id} or {}. IMPORTANT: the weapon's template
    MUST have 'melee' in its tags, or the attack_melee universal rule will
    silently fail to match and the being will look armed but cannot strike.
    Verify against the ITEM TEMPLATE IDS list in the user prompt before
    finalizing.
  fov_radius: 6-10 typically.
  location: The id of the map this being starts on.
  bonds: {} — will be filled by stage 7.
  relations: {} — will be filled by stage 7.

OUTPUT: a single JSON object — the being's entity instance, including
its personality block. No commentary.""")


def build_stage_6_prompt(
    lore: str,
    world_meta: dict[str, Any],
    being_index: int,
    total_beings: int,
    maps: list[dict[str, Any]],
    item_templates: dict[str, Any],
    rule_verbs: list[str],
    prior_beings: list[dict[str, Any]],
    reference_being: dict[str, Any],
) -> str:
    maps_summary = [
        {"id": m["id"], "name": m.get("name", ""), "desc": m.get("desc", ""),
         "grid": m["grid"], "legend": m.get("legend", {})}
        for m in maps
    ]
    prior_names = [b.get("name", b.get("id")) for b in prior_beings]
    prior_summary = ""
    if prior_beings:
        prior_summary = (
            f"=== BEINGS ALREADY IN THIS WORLD ({len(prior_beings)}) ===\n"
            f"You can reference these by id in drives/knowledge but DO NOT "
            f"re-create them. Names so far: {', '.join(prior_names)}.\n\n"
            f"Their full data:\n{_json(prior_beings)}\n\n"
        )
    return (
        f"=== WORLD META ===\n{_json(world_meta)}\n\n"
        f"This is being {being_index + 1} of {total_beings}.\n\n"
        f"=== MAPS ===\n{_json(maps_summary)}\n\n"
        f"=== ITEM TEMPLATE IDS (for inventory / drives) ===\n"
        f"{list(item_templates.keys())}\n\n"
        f"=== AVAILABLE VERBS (your drives must reference these) ===\n"
        f"{rule_verbs}\n\n"
        f"{prior_summary}"
        f"=== REFERENCE: one being from the starter (DIFFERENT setting) ===\n"
        f"{_json(reference_being)}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Output the JSON entity instance for this single being."
    )


# ============================================================================
# STAGE 7 — Bond weave
# Second pass over the finished beings to give them pre-existing relationships.
# ============================================================================

STAGE_7_SYSTEM = _system(7, "bond weave", """\
Your job: add bonds and relations to beings so they arrive with history.

bonds: backstory relationships. Emotional quality with a specific detail.
  Keyed by the other being's id. Example:
    "pedlar": "transactional warmth — but he remembered your name"

relations: current dynamic stance. Emotional quality, often in flux.
  Keyed by the other being's id. Example:
    "guard": "wary — he watched you too long yesterday"

Not every pair of beings needs a bond. But at least 50% of beings should
have at least one bond or one relation. Strangers work; but a world of
only-strangers feels dead.

Bonds should reflect the lore and each being's wound, drives, and tone.
Keep them concise — one phrase each, not paragraphs.

OUTPUT: a JSON object keyed by being id. Each value is:
{
  "bonds":     { "other_id": "phrase", ... },
  "relations": { "other_id": "phrase", ... }
}

Beings not in the output are left untouched. Output valid JSON only.""")


def build_stage_7_prompt(lore: str, world_meta: dict[str, Any], beings: list[dict[str, Any]]) -> str:
    # Thicker slim view — bonds should be spatially and factionally grounded.
    # Location/pos/tags let the generator anchor "the guard distrusts the
    # pedlar *because they share this map and the pedlar brought news from
    # the road the guard fears*" rather than producing floating affect.
    slim = [
        {
            "id": b["id"],
            "name": b.get("name", b["id"]),
            "location": b.get("location"),
            "pos": b.get("pos"),
            "tags": b.get("tags", []),
            "personality": b.get("personality", {}),
        }
        for b in beings
    ]
    return (
        f"=== WORLD META ===\n{_json(world_meta)}\n\n"
        f"=== BEINGS (slim view — id/name/location/pos/tags/personality) ===\n{_json(slim)}\n\n"
        f"=== LORE ===\n{lore}\n\n"
        f"Output the JSON object of {{being_id: {{bonds, relations}}}}. "
        f"Aim for ~50-75% coverage. Terse phrases. Bonds should reflect "
        f"shared maps, faction tags, and wounds — not floating affect."
    )


# ============================================================================
# utility
# ============================================================================

def _json(obj: Any) -> str:
    import json as _j
    return _j.dumps(obj, indent=2, ensure_ascii=False)


# ============================================================================
# Public re-exports for the orchestrator
# ============================================================================

STAGE_SYSTEMS = {
    1: STAGE_1_SYSTEM,
    2: STAGE_2_SYSTEM,
    3: STAGE_3_SYSTEM,
    4: STAGE_4_SYSTEM,
    5: STAGE_5_SYSTEM,
    6: STAGE_6_SYSTEM,
    7: STAGE_7_SYSTEM,
}

__all__ = [
    "SHARED_PREAMBLE",
    "FAILURE_MODES",
    "UNIVERSAL_RULES",
    "UNIVERSAL_STATUSES",
    "STAGE_SYSTEMS",
    "build_stage_1_prompt",
    "build_stage_2_prompt",
    "build_stage_3_prompt",
    "build_stage_4_prompt",
    "build_stage_5_prompt",
    "build_stage_6_prompt",
    "build_stage_7_prompt",
]
