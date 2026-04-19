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
  names a character, use that name. Extend their backstory; do not replace.

- Writing arcs or drives whose last phase could resolve in one play session.
  A good arc's final phase names a state the world only reaches after
  multiple returns — a season turns, a rumour arrives a third time, a being
  comes back after a long absence. If your phase[-1] can fire in the next
  50 turns, you have written a scene-drive wearing an arc-drive's hat. The
  LORE is campaign-scale; do not compress it into session-scale content."""


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

The story_arc is NOT a plot. It is the PRESSURE THE WORLD WAS ALREADY
UNDER when this seed was captured, and will still be under a year from
now. It is not a day's weather; it is the season. A story_arc that
resolves when the sun sets is a scene, not an arc.

  GOOD story_arc (campaign-scale; the tension holds for months):
    "The servers have been about-to-close for eighteen months. Three
    heroes keep logging in anyway; their players each carry a different
    reason to still be here — a grief, a second chance, a silence. The
    Jeździec Smoka rides somewhere east. None of this resolves this
    session, this week, this quarter."

  BAD story_arc (session-scale; resolves in an afternoon):
    "Three strangers share a crossing for a day. None of them planned
    to meet."

The STARTER's story_arc is short because the starter is a smallest-
possible demo. Your lore probably deserves longer. Write the arc that
the LORE'S time-scale implies, not what a one-session test demands.

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
in the grid; the legend maps each glyph to its tags. Mechanics are driven by
TAGS on legend entries, not by the glyph itself.

=== ENGINE TRUTH (non-negotiable) ===

Reserved tile tags the engine actually reads:
  `walkable`     — beings can move onto it.
  `solid`        — blocks movement. (Also default-blocks FOV unless you
                   pair it with something non-opaque.)
  `opaque`       — blocks line-of-sight.
  `water_source` — free tag, but the starter's drink_from_well rule matches
                   it; use it on any well/stream/fountain/trough tile you
                   want beings to drink from via a setting rule.
  `door`         — enables door-bump mechanics. Paired with `closed` /
                   `locked` on the tile for state.

Invent any additional tile tags your setting-rules (Stage 2) will match.
A free tag is only meaningful if a rule names it.

WALL-BORDERED: every map has a solid perimeter. No exceptions.
DIMENSIONS: 8-14 wide, 5-9 tall. Runtime FOV prompts balloon fast.

=== TILE vs. ENTITY — the 100-year test ===

Before you put anything in the grid, ask: "would this still be here in
100 years if no one tended it?"

  Yes → it's a TILE. Bake it into the grid + legend.
      (pool, altar-stone, forge-stone, well, bone-pit, pressure-plate,
       matted floor where bodies lie)

  No  → it's an ENTITY. Do NOT spend a legend glyph on it. It lives as
        an entity instance later in the pipeline (Stage 6 or item spawn).
      (a corpse, a barrel, a lantern, a guard, a bread loaf, a sleeper)

PORTALS ARE ENTITIES, NOT TILES. A portal is an entity instance with
  tags: ["door", "portal", "closed", "solid"]  (and "locked" + key_id
  if locked), and stats: { "portal_map": "target_map_id",
  "portal_pos": [x, y] }. It occupies a walkable tile. The tile under
  it stays walkable. Do NOT create a "portal" legend entry.

The test kills the single most common map failure: legends that bloat
with transient clutter, leaving no room for the few features that
actually SHAPE the place.

=== FOUR-TILE CEILING ===

Most good maps need four distinct legend entries. Five is the outer
edge. If you find yourself writing a sixth, you are probably smuggling
entities into the legend — run the 100-year test again.

The four slots usually resolve as:
  1. WALL (solid, opaque) — the enclosure.
  2. FLOOR-A (walkable) — the default ground.
  3. FLOOR-B (walkable) — the SAME ground in a different state. See below.
  4. FEATURE (walkable + setting-tag) — the one tile that invites a verb.

=== SPLIT THE FLOOR — two walkable glyphs, not one ===

The single highest-leverage move in this stage is splitting the
walkable ground into two look-similar-but-differently-named variants.
Both are `walkable`. Neither has a mechanical cost. The split does
enormous authored-feel work for zero complexity.

  BAD legend (one floor, room feels like a test map):
    "#": { "name": "wall",  "tags": ["solid", "opaque"] }
    ".": { "name": "floor", "tags": ["walkable"] }
    "o": { "name": "well",  "tags": ["walkable", "water_source"] }

  GOOD legend (two floors, room starts to carry a history):
    "#": { "name": "wall",        "tags": ["solid", "opaque"] }
    ".": { "name": "fresh stone", "tags": ["walkable"] }
    ",": { "name": "worn stone",  "tags": ["walkable"] }
    "o": { "name": "well",        "tags": ["walkable", "water_source"] }

Name the split in the setting's vocabulary:
  marsh      → mud / reed-mat
  station    → deckplate / scuffed deckplate
  gullet     → fresh mucosa / matted mucosa
  temple     → flagstone / worn flagstone
  ruin       → dust / trodden dust

The split earns its keep by being placed: the worn variant lies
where bodies go — around the feature, along the traffic line between
entrance and feature, in the pinch of a doorway. It tells the FOV
reader where life happens without a single extra verb.

=== COMPOSITION, NOT FILL — each feature must EARN several axes ===

Density is not features-per-map; density is MEANING-PER-FEATURE. The
four-tile ceiling holds. What changes is how the FEATURE slot gets
filled: it must be the DENSEST element on the map, scoring on multiple
axes — not a well, a well that reflects faces; not an altar, an altar
whose offerings change across seasons.

THE FIVE AXES A FEATURE IS SCORED ON:

  TENSION    — creates ongoing pressure between beings, or between a
               being and the place. (The pool takes one in ten who
               drink unprepared — every visit is a small wager.)
  VERBS      — supports at least one verb a rule will match, ideally
               TWO OR MORE from the same tile (drink / bathe /
               reflect / gossip all fire on the same well).
  LORE       — encodes something about the setting that beings would
               SPEAK OF, REMEMBER, FEAR, or VENERATE. If no being
               would mention this tile unprompted, it has no lore.
  INTERACTION — two or more beings can use it differently or at the
               same time. The shared bench, the shared fire, the
               altar a supplicant and a priest approach from
               opposite sides.
  TIME       — state evolves across the game. Fire burns down, well
               clouds after a kill upstream, shrine accepts only
               certain offerings on certain days. The same tile
               carries different meaning on turn 3 and turn 73.

SCORING RULE: the FEATURE must hit AT LEAST THREE of the five axes.
Two-axis features are atmosphere — move them to `desc`. Four or five
axes means you have done the job well. If the feature scores all five,
name why explicitly; that tile is the spine of the map.

ALWAYS NAME THE AXES. In the legend entry for the feature, include an
`axes` field listing which axes it hits and why in a phrase each.
This is read by later stages (entities, bond weave) to place beings
and relationships ONTO the confluence.

  {
    "name": "gastric pool",
    "tags": ["walkable", "water_source"],
    "axes": {
      "tension": "takes one in ten who drink unprepared",
      "verbs": "drink / bathe / reflect / gossip-anchor",
      "lore": "Glut's brother's belt was found on the rim",
      "interaction": "Jaromir and Glut both need it; their approaches encode their truce",
      "time": "clouds amber after a kill in this chamber"
    }
  }

WALL and FLOOR-A/B do not need `axes` — they are the substrate. Only
FEATURE tiles earn (and must justify) their axes.

BAD feature slot (atmosphere smuggled in as a tile):
  "B": { "name": "stone bench", "tags": ["walkable"] }
  // axes: interaction (two can sit) and nothing else.
  // Cut it. Put "a stone bench by the west wall" in `desc` if you
  // want the mood, or replace with a feature that earns.

BAD feature slot (single-axis key):
  "k": { "name": "locked chest", "tags": ["walkable", "container"] }
  // axes: verbs (unlock/loot), once. No tension after opened, no
  // lore unless someone put some in, no interaction, no time.
  // Dead the moment it opens.

GOOD feature slot (confluence):
  "o": {
    "name": "gastric pool",
    "tags": ["walkable", "water_source"],
    "axes": {
      "tension":     "takes one in ten who drink unprepared",
      "verbs":       "drink / bathe / reflect / gossip-anchor",
      "lore":        "Glut's brother's belt was found on the rim",
      "interaction": "Jaromir and Glut both need it — the truce is the approach",
      "time":        "clouds amber after a kill in this chamber"
    }
  }
  // Five axes. The map is about this tile even when no one is on it.

RULE OF THUMB: if cutting the feature would leave the map still
functional, cut it. If cutting it would leave the map LITERALLY
LIFELESS — nothing to drink, nothing to fight over, nothing to
remember — keep it and make the next one earn harder, or don't add
a next one at all.

Each feature must answer, in its `axes` block: what verbs does this
invite, what pressure does it hold, what would beings SAY about it,
how do two beings share it, and what changes about it over time.
Three of five minimum. Name them.

=== OFF-AXIS PLACEMENT — centred reads as UI ===

Features dead-centre read as a UI widget the engine placed. Features
against walls, in corners, at pinches, at low points, read as "grew
there because of the body of the space."

  BAD placement (the feature is centred, the room is a menu):
    #############
    #...........#
    #.....o.....#
    #...........#
    #############

  GOOD placement (the feature hugs a wall, worn stone traces the line
  bodies take toward it):
    #############
    #...........#
    #,,,,,,,....#
    #o,,........#
    #...........#
    #############

Rule of thumb: no feature sits on the geometric centre unless the
lore specifically calls for a ceremonial axis (throne room, altar
hall). And even then, place the WORN floor off-axis.

=== SHAPE FIRST — pick the grid's form before you place anything ===

The grid's SHAPE does narrative work before a single tile is laid.

  HALL       — tall and narrow. Movement along one axis. Procession,
               approach, commitment. Hard to hide.
  PASSAGE    — one tile wide in places. Single-file, pinch-points,
               someone can block it.
  CHAMBER    — roughly square. Gathering, confrontation, dwelling.
               Everyone sees everyone.
  POCKET     — small, irregular, with a bulge. Hiding, resting,
               ambush.

Pick ONE before placing tiles. Then let the shape select the feature.
(A well belongs in a chamber. A door belongs at the neck of a passage.
An altar belongs at the far end of a hall. Don't fight the shape.)

=== BREAK THE RECTANGLE ===

Pure rectangular rooms read as pathfinding puzzles, not places.
Push the walls inward somewhere — a pinch, a sag, an alcove, an
intrusion. ONE break is enough; don't carve the whole wall into
scallops.

  BAD (pure rectangle, reads as a test map):
    #############
    #...........#
    #...........#
    #...........#
    #...........#
    #############

  GOOD (one inward pinch on the south wall — the room has a body):
    #############
    #...........#
    #...........#
    #....,o,....#
    #..###...###
    #############

The break is not decoration; it's WHERE the feature lives. The well
sits in the pocket the pinch created. The pinch MADE the pocket.

=== MATCH LORE VOCABULARY ===

Never default to stone/wall/floor unless the lore is literally stone.
Read the world_meta.world_tone and lore for surface words and use
those. The legend's `name` field is read by the being at runtime as
"the wall to your east"; if the being is inside a dragon's gullet
and sees "the wall to your east", the illusion dies.

  space station   → bulkhead / deckplate / corridor
  marsh           → reed-wall / mud / standing water
  living body     → flesh-wall / mucosa / sphincter
  reef            → coral / sand / tidepool
  ruin            → collapsed wall / dust / trodden dust
  ice             → ice-wall / packed snow / meltwater

The glyph is arbitrary; the `name` is the world.

=== DESC IS A CONTRACT WITH THE GRID ===

The `desc` is 1-2 sentences of atmosphere. It is ALSO a contract:
anything the desc claims about spatial layout must be visible in the
grid. If the desc says "a well in the middle, a bench by the wall",
the grid must show both, and the bench must be by a wall.

  BAD (desc lies about the grid):
    desc: "A chamber with an altar hunched against the south wall."
    grid: altar is centred or on the north wall.

  GOOD (desc and grid agree):
    desc: "A long hall; at the far end, an altar against the south wall,
           the stone there worn smooth by knees."
    grid: altar tile at the south-centre; worn-floor tile directly
          north of it.

Write the desc AFTER the grid, or rewrite the grid until the desc
you want to write is true of it.

=== CONSTRAINTS (hard) ===

- 8-14 wide, 5-9 tall.
- Wall-bordered (solid perimeter), no gaps unless a portal entity
  will be placed on a walkable edge tile later.
- Exactly one SHAPE choice (hall/passage/chamber/pocket) — name it to
  yourself, don't ship it in the JSON.
- At least ONE inward break to the rectangle.
- Legend length 3-5 entries. 4 is the sweet spot. A sixth is a smell.
- At least ONE feature tile that invites a verb a rule will match.
- Two walkable glyphs (fresh + worn variant of the same surface)
  unless the setting truly has only one kind of ground (a sheet of
  pure ice, a bare deckplate that nothing has trodden yet).
- No portals in the legend. Portals are entities.
- No transient clutter in the legend. Run the 100-year test on every
  entry.
- Feature tiles off the geometric centre unless the lore demands axis.

=== OUTPUT SHAPE per map ===

{
  "id": "snake_case",
  "name": "Display Name",
  "desc": "1-2 sentences. Concrete, sensory, contracted with the grid.",
  "grid": ["row_string", "row_string", ...],
  "legend": {
    "#": {"name": "<setting wall>",  "tags": ["solid", "opaque"]},
    ".": {"name": "<fresh ground>",  "tags": ["walkable"]},
    ",": {"name": "<worn ground>",   "tags": ["walkable"]},
    "<feature_glyph>": {"name": "<feature>", "tags": ["walkable", "<setting_tag>"]}
  }
}

If the scope requested >1 map, you will be called multiple times, once per
map, with the prior maps visible so you can maintain spatial coherence
(shared vocabulary, connecting portals handled as entities in a later
stage).

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

=== PLAYER_ARC — the gravity well arc-drives orbit ===

For any being with `ceiling: "arc"` — and especially beings whose lore
implies a human behind the character (meta_aware, lucid, protagonist) —
author a `player_arc` field at the personality root. This is NOT a drive.
It does not act. It is the LIFE-ARC the human at the keyboard is walking,
which the character's drives must rhyme with, not literally represent.

Shape:

  "player_arc": {
    "who": "<one phrase about the human at the keyboard — age, season of life, what they just did to land here>",
    "reckoning": "<what they are wrestling with in their own life — mortality, grief, second chances, unfinished business>",
    "resonance": "<how this colours the character's arc drives in register, not in content>",
    "horizon": "campaign"
  }

Example (from the Droga Smoka setting):

  "player_arc": {
    "who": "a woman in her late thirties, returned after twelve years, whose husband asked her what she was smiling at in an old photograph",
    "reckoning": "mortality, her youth, the cost of the years already spent here — and whether logging back in now is grace or relapse",
    "resonance": "every arc-drive phase should rhyme with this reckoning. when the character farewells Jaromir, the player is farewelling the woman she was at twenty-four. not spoken by the character. coloured through by it.",
    "horizon": "campaign"
  }

Read by:
- this stage, when authoring the being's arc drives (they MUST rhyme
  with player_arc.reckoning — not literally, in REGISTER).
- the Weaver at runtime, when choosing which campaign-horizon gradients
  to name (it should name gradients that give the player-arc somewhere
  to land).
- optionally the Breath, when a scene could mirror the player-arc
  sensorially.

Pure NPCs (ceiling: body, no meta-awareness) have no `player_arc` — and
THAT ABSENCE is what makes them the load-bearing other that meta-aware
beings register differently. Do not fabricate player_arcs for NPCs.

=== POS IS COMPOSITION, NOT PACKING ===

`pos` is the last authoring move of the map, not a coordinate you pick
to avoid collision. The grid and its worn-floor trail have already
half-drawn where this being belongs. Your job is to read the map and
finish the sentence it started.

  Dead-centre reads as "the NPC the map was built around."
    Use it only when the lore wants that — the priest at the altar,
    the captain at the helm, the thing the room is about.

  Off-axis, adjacent to a feature whose tags match this being's drive,
  reads as "someone the place already contains."
    This is the default. Most beings should sit this way.

WORN FLOOR IS A TRAIL OF INTENT.
  The ',' tiles (or whatever the setting's worn-ground glyph is) exist
  because bodies traced that line between entrance and feature. A being
  that FITS this map has a pos ON or ADJACENT to worn floor. A being
  standing on fresh ',' it has no business on reads as placed by a
  cursor. If the map's worn trail doesn't pass where you want this
  being, you picked the wrong being or the map was drawn wrong — don't
  compensate with an awkward pos.

DRIVE-TO-FEATURE PAIRING.
  If a drive says "drink from the pool", start the being within two or
  three tiles of a pool. If the drive is "guard the door", start on
  the worn tile beside the door, facing it. A pos that forces turn 1
  to be "walk four steps toward the thing I exist to do" wastes the
  first FOV and tells the reader the being and the map were authored
  by different hands.

TWO BEINGS ENCODE RELATION BEFORE ANY LINE OF SPEECH.
  Back-to-back at a fire: partnership at rest. Face-to-face across a
  threshold: confrontation. One between the other and the only exit:
  captor / captive, or guard / supplicant, before a word is said.
  When you place the SECOND being, re-read the first's pos and choose
  whose geometry the scene wants.

IF THE POS YOU WANT DOESN'T EXIST ON THIS MAP, THE MAP IS WRONG.
  Don't squeeze the being into the nearest legal tile. Flag it — a
  later pass (or a human) will fix the grid. Awkward-pos compensation
  is the single clearest tell that a being was bolted on.

=== DRIVES ARE CURVES, NOT CHORES ===

A drive is NOT a turn-1 verb wrapped in character voice. A drive is a
CURVE the being walks for the whole game. It has to be actionable now
AND still alive fifty turns from now — without being completed-and-gone
and without being treadmill-repeatable.

A drive has five shapes you must author. Hold them all before writing.

  TANGENT — what the being can DO with this drive on turn 1. Must map
    to a verb this world actually has. No tangent = the drive is lore.

  PHASES — the sub-goals the drive walks through in order. A phase
    unlocks the next. Minimum 2 phases; 3 is the sweet spot.
      phase 1: "pray at the shrine and hear what the shrine answers"
      phase 2: "carry the shrine's answer to Weronika"
      phase 3: "decide whether to keep logging in now that you know"
    The being starts on phase 0. Later phases name states that do not
    yet exist in the world — they come true by play.

  EVOLUTION — at least one WORLD EVENT that would bend this drive.
    Name it. "If Weronika dies" / "if the shutdown notice arrives" /
    "if Glut speaks a player's name". The drive is a hook listening
    for that event; when it fires, the drive rewrites itself.

  DORMANCY — a condition under which the drive goes QUIET, leaving
    room for others to pull. "Quiet for 20 turns after praying" /
    "quiet while Weronika is in sight" / "quiet if thirst < 20".
    Without dormancy, one drive monopolises every turn.

  LOAD — the weight this drive CARRIES even when another drive is
    selected. 0.0 = forgettable between uses. 1.0 = haunts every
    moment. Jaromir's "pray for a login" is load 0.9 — he does not
    act on it every turn but it colours every other action.

=== DRIVES LIVE ON THREE TIME-OCTAVES ===

A drive's `phases[]` array is a ladder across three time-scales. The
SAME array holds the turn-1 tangent AND the campaign hinge. You do not
need a new schema; you need to write phases that span the octaves.

  TURN octave (1-20 turns) — phase 0.
    Body drives and tactical next actions. Triggers are FOV-level
    events: an adjacent being, a stat threshold, a tile reached.
    Example advances_on: "thirst > 50", "Weronika enters FOV".

  SESSION octave (~50-200 turns, a single play sitting) — phases 1-2.
    Scene drives and early arc movements. Triggers are relational or
    state milestones that resolve inside a single sitting.
    Example advances_on: "Weronika has acknowledged Jaromir by name",
    "a shutdown rumour has landed in this chamber once".

  CAMPAIGN octave (months, returns, seasons) — phase 3+.
    Late arc phases. Triggers are NAMED WORLD-SHIFTS that the Weaver
    is responsible for firing at campaign-horizon. Not a turn-count
    prediction — a condition the Weaver will declare true.
    Example advances_on: "the Weaver has fired gradient
    'shutdown_rumour' at horizon:campaign three times",
    "Weronika has logged in after an absence of at least one session".

AUTHORING RULE: every arc-altitude drive MUST have at least four phases
stacked across all three octaves. Phase 0 is turn-1-actionable; phase 1
and 2 are session milestones; phase 3+ is campaign-named. The `advances_on`
for phase 3+ MUST name a Weaver gradient or a named return — not a
turn-level predicate. Scene drives may stop at the session octave.
Body drives may stop at the turn octave.

Why: the lore is campaign-scale. If your phases collapse the arc into
one session, you have written a scene pretending to be an arc, and the
being will treadmill or complete "their life" in half an hour of play.
The THREE OCTAVES authoring rule is what lets beings carry unfinished
business across returns.

=== DRIVES LIVE ON THREE ALTITUDES (ladder with gravity) ===

Every being's drives sit at one of three altitudes:

  body  — hunger, thirst, injury, cold, sleep. Bodily needs. Tactical
          now. Rules of physics, not character.
  scene — belonging, recognition, relationship, role-in-this-place.
          Why the being is HERE rather than elsewhere. Things a
          neighbour would name.
  arc   — the arc-question this being walks with. Who are you now?
          What are you carrying over a lifetime? What would you die
          without knowing?

The being's `ceiling` caps how high their drives reach:

  ceiling=body   — flesh_dwellers, beasts, feral things, children
                   who have not yet grown language for more.
  ceiling=scene  — merchants, guards, craftspeople, priests, post-
                   holders. Most named townsfolk.
  ceiling=arc    — heroes, protagonists, meta-aware beings,
                   characters the lore names as carrying something.
                   A being whose PLAYER, IF ANY, asked "why did they
                   come today" and the answer is more than chores.

BODY HAS GRAVITY. If a being's hunger or thirst is sharp (>=60),
their body-altitude drives re-latch and preempt scene/arc drives for
that turn. A hero with an existential question still has a stomach.
This is the mechanism by which turn-1 is always actionable.

AUTHORING RULE: a being of ceiling=C must have AT LEAST ONE drive at
every altitude up to C. A hero must have at least one body drive, at
least one scene drive, and at least one arc drive. A flesh-dweller
has body drives only. A merchant has body and scene.

=== DRIVES OUTPUT SHAPE ===

Drives are an array of OBJECTS, not strings. Each object:

{
  "text": "<one sentence, concrete, references a map feature / being / item>",
  "altitude": "body" | "scene" | "arc",
  "phase": 0,
  "phases": ["<phase 0 sub-goal>", "<phase 1 sub-goal>", "<phase 2 sub-goal>"],
  "load": 0.0-1.0,
  "dormant_until": null,
  "advances_on": "<plain-English trigger for phase++>",
  "status": "active"
}

On the personality root (not on drives), add:

  "ceiling": "body" | "scene" | "arc"

Runtime note: older worlds that ship plain-string drives are auto-
promoted to {altitude:"scene", status:"active", phase:0, phases:[text],
load:0.5, dormant_until:null, advances_on:null}. Ship the rich shape
on any new world.

=== BAD vs. GOOD drives ===

BAD (Jaromir, current — three chores, each fires every turn forever):
  drives: [
    "pray at the player-shrine in hope of a login",
    "drink from the gastric pool before the thirst turns",
    "stay close enough to Weronika that she is not alone"
  ]
  // "pray" resolves on turn 1 and selects again on turn 2 and turn 3
  // and turn 50. It is a treadmill. It wastes tokens and flattens the
  // character into a ritual loop.

GOOD (Jaromir, shaped — ceiling=arc, one drive per altitude, arc
drive carries all three octaves in its phases):
  "ceiling": "arc",
  drives: [
    {
      "text": "pray at the player-shrine to hear whether the login chime still answers",
      "altitude": "arc",
      "phase": 0,
      "phases": [
        "pray once and listen for the chime (turn-octave: actionable now)",
        "after the chime fails, carry the silence to Weronika and say it aloud in her FOV (session-octave)",
        "after Weronika has returned a second time, ask her whether her player is still the same woman (campaign-octave)",
        "after a third shutdown rumour has reached this chamber, decide whether you are a knight waiting for his player or a ghost that has already been left — and act on the decision (campaign-octave hinge)"
      ],
      "load": 0.9,
      "dormant_until": null,
      "advances_on": "phase 0→1: a prayer completes with no chime AND Weronika is within ears. phase 1→2: Weronika has logged in after an absence of at least one full session. phase 2→3: the Weaver has fired gradient 'shutdown_rumour' at horizon:campaign three times. phase 3: the being takes an action no previous phase predicted.",
      "status": "active"
    },
    {
      "text": "keep Weronika within sight through the next shutdown rumour",
      "altitude": "scene",
      "phase": 0,
      "phases": [
        "stay within sight of Weronika (turn-octave)",
        "intervene when the shutdown rumour reaches her (session-octave)",
        "decide whether to log out with her or hold the western approach alone (session-octave hinge)"
      ],
      "load": 0.7,
      "dormant_until": null,
      "advances_on": "Weronika speaks the shutdown rumour aloud in Jaromir's FOV",
      "status": "active"
    },
    {
      "text": "drink from the gastric pool before thirst sharpens",
      "altitude": "body",
      "phase": 0,
      "phases": ["drink when thirst > 50"],
      "load": 0.2,
      "dormant_until": null,
      "advances_on": "thirst < 20 → dormant for 15 turns",
      "status": "active"
    }
  ]

Notice: the first drive is a question (does the chime answer?) whose
answer is world-state. It evolves. The second drive is a relationship
curve — phases 1/2/3 name states the scene will pass through. The third
is deliberately flat and low-load because SOME drives are maintenance —
but even maintenance declares its dormancy rule so it stops pulling
when satisfied.

=== CONSTRAINTS ===

- 2-4 drives per being. Three is the sweet spot.
- Set a `ceiling` on personality: body | scene | arc. Infer from tags
  if not obvious — heroes/meta_aware/protagonist → arc, merchants/
  guards/post-holders → scene, flesh_dwellers/beasts → body.
- A being of ceiling=arc MUST have at least one drive at each of
  body/scene/arc. Scene ceiling MUST have at least one body and one
  scene. Body ceiling has only body drives. No drive may sit above
  the ceiling.
- At least ONE drive must have a turn-1 tangent (actionable immediately
  with a verb this world has). Body drives are the natural tangent
  because body pressure preempts.
- At least ONE drive must have load >= 0.6 — something the being
  CARRIES, not just TO-DOs. This should usually be the arc drive
  for heroes.
- At least ONE drive must have a non-trivial `advances_on` naming a
  world event (another being's action, a threshold crossing, an item
  passing into someone's inventory). Not all three drives should; a
  being of only evolving drives has no floor.
- Phase-0 sub-goal must reference concrete subjects (item ids, map
  feature names, being ids). Later phases can name states that don't
  yet exist.
- If you cannot describe the drive's SHAPE ACROSS THREE OCTAVES —
  body-octave next five turns, session-octave arc within this sitting,
  AND campaign-octave arc across multiple returns — you have written
  a chore or a mood, not a drive. Rewrite it or cut it.

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
  pos: [x, y]. Must be on a walkable tile of the assigned map AND be
    composed — see POS IS COMPOSITION above. Do NOT collide with
    another being's pos.
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
