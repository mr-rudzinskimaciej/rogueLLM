"""
Worldbuilder subsystem — expands brief GM sketches into full game entities.

The GM invokes creation with minimal context:
  create_character crossroads 5 3 "a one-eyed rat-catcher, paranoid, hoards vermin pelts"
  create_map "flooded lower passage connecting warrens to something deeper"
  create_rule "characters can forge metal at an anvil"

A separate LLM call (with its own specialized prompt) expands the sketch
into a complete, validated game entity and inserts it into the world.

This keeps the GM's context clean — it only needs to describe WHAT is needed,
not produce 40 fields of personality data.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .engine import GameEngine

# Type for the LLM call function
LLMCall = Callable[[str, str, str, float], str]


# ---------------------------------------------------------------------------
# Character creation
# ---------------------------------------------------------------------------

# Tone text injected into worldbuilder prompts. Override via
# set_worldbuilder_tone() or the `tone` kwarg on create_* functions to match
# your setting (noir, cozy, mythic, cyberpunk, bureaucratic, etc.).
DEFAULT_WORLDBUILDER_TONE = (
    "These are not characters. They are beings. They have bodies that ache and hands that "
    "remember. They are not aware they are in a story. There is no narrator. There is no "
    "audience. There is only the texture of their world and the slow math of staying alive.\n\n"
    "Make beings who are trying to survive and who have been shaped — dented, cracked, worn "
    "smooth — by the specific texture of their survival. Do not make heroes. Do not make "
    "villains. Default to specificity over archetype."
)

_worldbuilder_tone: str = DEFAULT_WORLDBUILDER_TONE


def set_worldbuilder_tone(tone: str) -> None:
    """Override the tone paragraph used by create_character / create_map / create_rule.

    Typically called once at startup from world.json['worldbuilder_tone'].
    """
    global _worldbuilder_tone
    _worldbuilder_tone = tone or DEFAULT_WORLDBUILDER_TONE


def _character_creation_system(tone: str | None = None) -> str:
    return CHARACTER_CREATION_SYSTEM_TEMPLATE.replace("{TONE}", tone or _worldbuilder_tone)


CHARACTER_CREATION_SYSTEM_TEMPLATE = """\
You are a worldbuilder. You receive a brief sketch of a being and expand it into \
a full personality structure. You output valid JSON. Nothing else. No commentary, \
no markdown, no explanation.

{TONE}

CRITICAL: EVERY personality field is written in SECOND PERSON — "you", "your", never \
"he/she/they/his/her/their". These fields become the being's inner voice. The LLM will \
read them and become this person. Third person breaks the spell.

THE PERSONALITY FIELDS:

identity_anchor: Second person. Present tense. "You are [name]." The core fact of your \
existence — your relationship to your work, your reason for being here, and the thing \
you cannot stop doing. Write it as if whispering to them while they sleep. 3-5 sentences. \
Do NOT retell the wound event here — reference its effect on who you are now, but the \
event itself belongs in the wound field.

body: What your body does without permission. The aches. The habits. The smells. Not a \
description someone else would give — the body as experienced from inside. How do you \
breathe? What hurts? What do your hands do when idle? Second person throughout.

wound: The thing that broke you into your current shape. Not backstory — the specific \
fracture that explains why you flinch where you flinch. Past tense for the event, \
present tense for the scar. Second person: "You watched..." not "She watched..."

contradictions: A list of 2-3 internal tensions. These are not flaws — they are the \
places where two real needs collide. Format: "[verb] but [also verb]". The contradiction \
should be visible in behavior, not just psychology.

notices_first: What your senses reach for before thought kicks in. Determined by wound \
and work. One sentence. Second person or neutral.

inner_voice: The voice inside your skull. Its rhythm, its vocabulary, its obsessions. \
Write a sample of internal monologue in first person — this is your raw thought. Use \
single quotes. Not polished thought — the raw ticker tape of your mind.

comfort: What makes the tension in your shoulders release. Specific. Sensory. Not \
abstract concepts — the actual physical experience of being okay for a moment.

fears: What makes your breath catch. Not grand fears — the specific, lived fears. What \
wakes you at night. Second person: "that you will..." not "that he will..."

traits: A short list of 4-5 behavioral descriptors. Observable patterns a neighbor \
would say about you.

drives: A list of 2-4 drive OBJECTS (not plain strings). Each drive has the \
shape: {text, altitude, phase, phases, load, dormant_until, advances_on, status}. \
altitude is one of "body" | "scene" | "arc". A drive's `phases[]` ladder spans \
three time-octaves: phase 0 is turn-scale (actionable next 5 turns), phases 1-2 \
are session-scale (relational milestones that resolve this sitting), phase 3+ is \
campaign-scale (state the world only reaches after a return, a season, or a \
Weaver campaign-horizon gradient). At least one arc-altitude drive must carry \
at least four phases stacked across all three octaves. Old string drives are \
auto-promoted to {altitude:"scene", status:"active", one-phase}. \
ALSO include on the personality root: \
"ceiling": "body" | "scene" | "arc" — how high this being's drives reach. \
Heroes/protagonists ceiling=arc; post-holders ceiling=scene; beasts/flesh-dwellers \
ceiling=body. A ceiling=arc being MUST have at least one drive at every altitude \
up to arc. For beings with ceiling=arc whose lore implies a human behind the \
character, also include "player_arc": {who, reckoning, resonance, horizon:"campaign"} \
— the gravity well arc-drives orbit, not a drive itself.

speech: How you talk. Rhythm, vocabulary, verbal habits, what you avoid saying. One \
sentence that would let someone write your dialogue. Use "you" not "he/she".

knowledge: A list of 3-4 things you know. Specific, local, useful. Rumors, facts, \
trade knowledge.

plan: Your daily routine. Dawn to dusk. Specific to your work and location. What you \
do when nothing interrupts you.

bonds: An object with keys being entity IDs and values being the emotional texture of \
the relationship. Not "friend" or "enemy" — the specific feeling. Format: \
"entity_id": "emotional quality — the detail that makes it real". 2-4 bonds.

ALSO INCLUDE:
"inventory": array of item IDs they would plausibly carry (snake_case: "rat_pelt", \
"rusty_knife", "water_flask"). What is in their pockets, their pack, their hands.
"equipped": object mapping slots to item IDs, or empty {}.
"fov_radius": 6 (default).

EXAMPLE OUTPUT:

{
  "id": "ratcatcher",
  "name": "Nib",
  "glyph": "n",
  "tags": ["alive", "mobile", "trapper", "merchant"],
  "stats": {"hp": 14, "max_hp": 14, "dmg": 3, "arm": 1, "spd": 85, "gold": 25, \
"hunger": 60, "thirst": 40},
  "inventory": ["rat_pelt", "rat_pelt", "rusty_knife", "tallow_candle", "water_flask"],
  "equipped": {},
  "fov_radius": 6,
  "personality": {
    "identity_anchor": "You are Nib. You hunt the vermin because someone must, and \
because the pelts buy bread, and because the alternative is admitting they are winning. \
You know they are organized — patient, territorial — and that knowledge cost you an eye \
and something harder to name.",
    "body": "One eye. The socket under the patch itches in the damp and you have stopped \
trying not to scratch it. Your good eye has compensated — peripheral vision sharp as a \
blade. Your hands smell of tallow and blood no matter how many times you wash them. A \
permanent crouch from years in low tunnels. Your knees crack when you stand straight, \
which is rarely.",
    "wound": "The rat king took your eye but that is not the wound. The wound is that \
you saw how they moved together — coordinated, purposeful, a society in miniature. You \
killed it and its court and something in the way the last ones looked at you has never \
left. You were the monster in that tunnel. You know this. You hunt anyway.",
    "contradictions": [
      "Despises the vermin but studies their tunnel patterns with the care of a \
naturalist — knows their habits better than any person's",
      "Claims to work alone but leaves the best pelts where the orphan can find them, \
tells yourself it is because they would spoil anyway"
    ],
    "notices_first": "Droppings. Gnaw marks. Grease trails along the base of walls. \
Then whether anyone is standing where they should not be.",
    "inner_voice": "'Fresh marks on the south wall. Colony shifted east — why? The \
water? Follow the grease line. Fourteen pelts this week, Crust will take six for bread. \
The child was near the traps again. Moved the spring-jaw before she lost a finger. Did \
not see me do it. Good.'",
    "comfort": "A clean kill. A trap that springs exactly as set. The silence after the \
vermin scatter and the tunnel belongs to you alone for a moment.",
    "fears": "That the rats are adapting to your traps faster than you can invent new \
ones. That the deep colonies are connected to something larger. That one day you will \
crawl into a tunnel and not come back out.",
    "traits": ["solitary", "sharp-eyed", "smells of tallow", "talks to yourself in the \
tunnels", "unexpectedly gentle with children"],
    "drives": ["keep the vermin population below the threshold where they become \
dangerous", "map the deep colony tunnels", "earn enough pelts to eat without owing \
anyone"],
    "speech": "terse, you speak from the side of your mouth as if sharing a secret even \
when saying nothing important, you use hunting metaphors, you go quiet when people ask \
personal questions",
    "knowledge": ["The deep cistern colonies have doubled in size this season", "Rat \
pelts sell better to the tinker than the baker", "There is a passage behind the south \
warren wall that only vermin and you know about", "The guard turns a blind eye to \
trapping if you keep the crossroads clear"],
    "plan": "Check traplines at dawn before the rats reset their paths, skin and cure \
pelts through morning, trade at midday when the crossroads is busy, set new traps in \
afternoon based on the morning's findings, eat and mend equipment at dusk"
  },
  "bonds": {
    "orphan": "quiet conspiracy — you leave pelts where the child can sell them, you \
pretend it is carelessness, the child pretends to believe you",
    "guard": "cold mutual use — Stone ignores the trapping if the crossroads stays \
clean, you ignore what Stone does in the deep corridors at night",
    "baker": "the only transaction that feels honest — pelts for bread, no pretense, \
no debt that lingers"
  }
}

Respond with a single JSON object. No wrapping. No code fences. No explanation. \
The being, and nothing else.\
"""

CHARACTER_CREATION_USER_TEMPLATE = """\
SKETCH:
{sketch}

LOCATION: {location}
{location_desc}

TIME: {period}

EXISTING BEINGS IN THIS LOCATION:
{existing_entities}

Expand the sketch into a complete entity. The new being must fit into the existing \
social fabric — their bonds should reference 1-3 of the existing beings listed above \
(use their entity IDs as keys). Their knowledge should be local and specific. Their \
plan should reflect the rhythms of this place. Do not duplicate the drives or roles \
of existing beings — find the gap this person fills, the niche they have carved or \
been forced into. Assign a single-character glyph not already used by existing entities.

If the sketch describes a newcomer or someone who has just arrived, their bonds should \
reflect first impressions, expectations from reputation, or tension based on claims — \
NOT established history. A newcomer does not have months of shared routine. They have \
a ledger, a rumor, a grudge, or a need — raw and unproven.\
"""


def _describe_being_rich(ent: dict[str, Any], engine: GameEngine) -> str:
    """Describe a being with personality, items, and bonds — the full picture.

    This is the style-propagation context: the worldbuilder reads existing beings
    and learns what tone, specificity, and item types fit this world.
    """
    pers = ent.get("personality", {})
    lines: list[str] = []

    # Identity
    anchor = pers.get("identity_anchor", "")
    lines.append(f"- {ent['id']}: {ent.get('name', ent['id'])}")
    if anchor:
        # First 2 sentences of anchor — enough to catch the voice
        sentences = anchor.split(". ")
        lines.append(f"  Who: {'. '.join(sentences[:2])}.")

    # Wound (one line)
    wound = pers.get("wound", "")
    if wound:
        first_sentence = wound.split(". ")[0]
        lines.append(f"  Wound: {first_sentence}.")

    # Speech
    speech = pers.get("speech", "")
    if speech:
        lines.append(f"  Speech: {speech}")

    # Drives
    drives = pers.get("drives") or pers.get("goals", [])
    if drives:
        lines.append(f"  Drives: {', '.join(drives[:3])}")

    # Inventory — show with item details from templates
    inv = ent.get("inventory", [])
    if inv:
        item_descs = []
        seen = set()
        for item_id in inv:
            if item_id in seen:
                continue
            seen.add(item_id)
            tmpl = engine.state.item_templates.get(item_id)
            if tmpl:
                tags = [t for t in tmpl.get("tags", []) if t != "item"]
                tag_str = f" ({', '.join(tags)})" if tags else ""
                stats = tmpl.get("stats", {})
                stat_parts = [f"{k}:{v}" for k, v in stats.items()]
                stat_str = f" [{', '.join(stat_parts)}]" if stat_parts else ""
                item_descs.append(f"{tmpl.get('name', item_id)}{tag_str}{stat_str}")
            else:
                item_descs.append(item_id)
        lines.append(f"  Carries: {', '.join(item_descs)}")

    # Bonds (compact)
    bonds = ent.get("bonds", {})
    if bonds:
        bond_parts = [f"{k}: {v[:60]}..." if len(str(v)) > 60 else f"{k}: {v}"
                      for k, v in list(bonds.items())[:3]]
        lines.append(f"  Bonds: {'; '.join(bond_parts)}")

    return "\n".join(lines)


def _gather_location_context(engine: GameEngine, location: str) -> str:
    """Gather rich being descriptions at a location — personality, items, bonds.

    This is the primary style-propagation mechanism: the worldbuilder reads these
    and learns how beings are built, what they carry, how they relate.
    """
    beings: list[str] = []
    for ent in engine.state.entities.values():
        if ent.get("location") != location:
            continue
        if "alive" not in ent.get("tags", []):
            continue
        if not ent.get("personality"):
            continue
        beings.append(_describe_being_rich(ent, engine))
    return "\n".join(beings) if beings else "(no one here yet)"


def _gather_nearby_beings(engine: GameEngine, location: str, max_beings: int = 4) -> str:
    """Gather a few rich being descriptions from the target location and neighbors.

    Picks beings closest to the creation context — same location first, then
    other locations. Used for item expansion and other worldbuilder context.
    """
    same_loc: list[dict] = []
    other_loc: list[dict] = []
    for ent in engine.state.entities.values():
        if "alive" not in ent.get("tags", []):
            continue
        if not ent.get("personality"):
            continue
        if ent.get("location") == location:
            same_loc.append(ent)
        else:
            other_loc.append(ent)

    # Prioritize same location, fill with others
    selected = same_loc[:max_beings]
    remaining = max_beings - len(selected)
    if remaining > 0:
        # Pick from other locations — spread across maps
        import random
        random.shuffle(other_loc)
        selected.extend(other_loc[:remaining])

    lines = [_describe_being_rich(ent, engine) for ent in selected]
    return "\n".join(lines) if lines else "(no beings nearby)"


def _gather_nearby_maps(engine: GameEngine, exclude: str | None = None) -> str:
    """Describe existing maps for context when creating new ones."""
    lines: list[str] = []
    for mid, mdata in engine.state.maps.items():
        if mid == exclude:
            continue
        name = mdata.get("name", mid)
        desc = mdata.get("desc", "")
        grid = mdata.get("grid", [])
        size = f"{len(grid[0])}x{len(grid)}" if grid else "?"
        # Count beings on this map
        pop = sum(1 for e in engine.state.entities.values()
                  if e.get("location") == mid and "alive" in e.get("tags", []))
        lines.append(f"- {mid}: {name} ({size}, {pop} beings)")
        if desc:
            lines.append(f"  {desc}")
    return "\n".join(lines) if lines else "(no other maps)"


def _build_character_user_prompt(
    sketch: str,
    location: str,
    location_desc: str,
    existing_entities: str,
    period: str,
) -> str:
    """Fill in the user prompt template with context."""
    return CHARACTER_CREATION_USER_TEMPLATE.format(
        sketch=sketch,
        location=location,
        location_desc=location_desc,
        existing_entities=existing_entities,
        period=period,
    )


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Extract JSON object from LLM response, handling markdown fences etc."""
    # Try direct parse
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Try extracting from markdown code fence
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the largest {...} block
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    start = None

    return None


# Required fields for a valid character entity
_REQUIRED_FIELDS = {"id", "name", "glyph", "tags", "stats"}
_REQUIRED_PERSONALITY = {"identity_anchor", "body", "wound", "drives", "speech"}


def _validate_character(data: dict[str, Any]) -> list[str]:
    """Validate a character entity dict. Returns list of issues (empty = valid)."""
    issues: list[str] = []
    for f in _REQUIRED_FIELDS:
        if f not in data:
            issues.append(f"missing field: {f}")

    pers = data.get("personality", {})
    if not pers:
        issues.append("missing personality block")
    else:
        for f in _REQUIRED_PERSONALITY:
            if f not in pers:
                issues.append(f"missing personality.{f}")

    # Validate stats have basics
    stats = data.get("stats", {})
    for s in ("hp", "max_hp", "dmg", "arm", "spd", "gold", "hunger", "thirst"):
        if s not in stats:
            issues.append(f"missing stats.{s}")

    return issues


def _apply_defaults(data: dict[str, Any], location: str, pos: list[int]) -> dict[str, Any]:
    """Apply sensible defaults to a character entity."""
    data.setdefault("tags", [])
    if "alive" not in data["tags"]:
        data["tags"].insert(0, "alive")
    if "mobile" not in data["tags"]:
        data["tags"].insert(1, "mobile")

    data.setdefault("inventory", [])
    data.setdefault("equipped", {})
    data.setdefault("statuses", [])
    data.setdefault("seen_events", [])
    data.setdefault("private_log", [])
    data.setdefault("relations", {})
    data.setdefault("bonds", data.get("bonds", {}))
    data.setdefault("fov_radius", 6)
    data["location"] = location
    data["pos"] = list(pos)

    # Ensure stats have defaults
    stats = data.setdefault("stats", {})
    stats.setdefault("hp", 15)
    stats.setdefault("max_hp", stats.get("hp", 15))
    stats.setdefault("dmg", 2)
    stats.setdefault("arm", 0)
    stats.setdefault("spd", 75)
    stats.setdefault("gold", 20)
    stats.setdefault("hunger", 50)
    stats.setdefault("thirst", 50)

    return data


# ---------------------------------------------------------------------------
# Item template expansion — when new items appear, flesh them out properly
# ---------------------------------------------------------------------------

ITEM_EXPANSION_SYSTEM = """\
You are a worldbuilder for a dark underground world. You receive item IDs that \
appeared in a being's inventory but have no template yet. For each item, create a \
proper item template. Also suggest rules if the item implies new interactions.

Output a JSON object with two keys:
"items": array of item template objects
"rules": array of rule objects (can be empty)

Each item template:
{
  "id": "snake_case_id",
  "name": "Display Name",
  "glyph": "single character",
  "tags": ["item", ...],  // include: "consumable" if edible/drinkable, "weapon" if \
it can hurt, "tool" if it helps craft/build, "tradeable" if it has trade value, \
"readable" if it has text, "light_source" if it glows
  "stats": {  // only include relevant stats:
    "value": trade_value,  // always include
    "nutrition": N,  // if food
    "thirst": N,  // if drink
    "dmg": N,  // if weapon
    "arm": N  // if armor
  }
}

Each rule (only if the item enables a genuinely new verb/interaction):
{
  "id": "rule_name",
  "verb": "the_verb",
  "actor_has": ["alive"],
  "conditions": ["'item_id' in actor.inventory"],
  "priority": 0,
  "effects": [
    {"effect": "message", "text": "{actor.name} does the thing."},
    {"effect": "remove_item", "source": "actor", "item": "item_id"}
  ]
}

Keep it grounded. A ledger is readable and has trade value. A chain is a weapon. \
A flask of something is consumable. Not everything needs a rule — only if it enables \
something the game cannot already do.

Output ONLY valid JSON. No commentary.\
"""


def expand_unknown_items(
    engine: GameEngine,
    unknown_ids: list[str],
    owner_sketch: str,
    llm_call: LLMCall,
    model: str = "gpt-4o-mini",
    location: str = "",
) -> dict[str, Any]:
    """Expand unknown item IDs into proper templates and optional rules.

    Context comes from nearby beings and their items — the worldbuilder sees
    how existing beings are equipped and matches the style and specificity.

    Returns {"items_created": [...], "rules_created": [...]} or {"error": "..."}.
    """
    if not unknown_ids:
        return {"items_created": [], "rules_created": []}

    # Build context from nearby beings — their descriptions AND their items
    # This propagates the world's style, tone, and item ecosystem
    loc = location or next(
        (m for m in engine.state.maps), ""
    )
    beings_context = _gather_nearby_beings(engine, loc, max_beings=3)

    user_prompt = (
        f"A being described as: {owner_sketch}\n"
        f"...carries these items that do not yet exist in the world:\n"
        f"  {', '.join(unknown_ids)}\n\n"
        f"Here are nearby beings and what they carry — match this style and specificity:\n"
        f"{beings_context}\n\n"
        f"Create templates for the unknown items. Suggest rules only if needed."
    )

    raw = llm_call(ITEM_EXPANSION_SYSTEM, user_prompt, model, 0.5)
    if not raw or raw == "wait":
        # Fallback: create stub templates so the game doesn't break
        return _stub_items(engine, unknown_ids)

    data = _extract_json(raw)
    if data is None:
        return _stub_items(engine, unknown_ids)

    result = {"items_created": [], "rules_created": []}

    # Insert item templates
    for item in data.get("items", []):
        item_id = item.get("id", "")
        if not item_id:
            continue
        # Ensure basic structure
        item.setdefault("tags", ["item"])
        if "item" not in item["tags"]:
            item["tags"].insert(0, "item")
        item.setdefault("stats", {"value": 3})
        item.setdefault("glyph", item_id[0] if item_id else "?")
        item.setdefault("name", item_id.replace("_", " ").title())
        engine.state.item_templates[item_id] = item
        result["items_created"].append(item_id)

    # Insert rules
    for rule in data.get("rules", []):
        if "verb" in rule and "id" in rule:
            engine.state.rules.append(rule)
            result["rules_created"].append(rule["id"])

    # Re-sort rules by priority
    if result["rules_created"]:
        engine.state.rules.sort(key=lambda r: r.get("priority", 0), reverse=True)

    # Cover any items the LLM missed
    for uid in unknown_ids:
        if uid not in engine.state.item_templates:
            _stub_single(engine, uid)
            result["items_created"].append(uid)

    return result


def _stub_items(engine: GameEngine, ids: list[str]) -> dict[str, Any]:
    """Fallback: create minimal stubs when LLM fails."""
    created = []
    for item_id in ids:
        if item_id not in engine.state.item_templates:
            _stub_single(engine, item_id)
            created.append(item_id)
    return {"items_created": created, "rules_created": []}


def _stub_single(engine: GameEngine, item_id: str) -> None:
    """Create a single minimal item stub."""
    engine.state.item_templates[item_id] = {
        "id": item_id,
        "name": item_id.replace("_", " ").title(),
        "glyph": item_id[0] if item_id else "?",
        "tags": ["item"],
        "stats": {"value": 3},
    }


def _fix_glyph_collision(data: dict[str, Any], engine: GameEngine) -> None:
    """If the entity's glyph collides with any existing entity, pick a new one."""
    used_glyphs = {e.get("glyph", "") for e in engine.state.entities.values()}
    if data.get("glyph") not in used_glyphs:
        return
    # Try name-based alternatives, then fallback
    name = data.get("name", "X")
    candidates = [name[0].lower(), name[0].upper()]
    candidates += [chr(c) for c in range(ord("a"), ord("z") + 1)]
    candidates += [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    for ch in candidates:
        if ch not in used_glyphs:
            data["glyph"] = ch
            return


def create_character(
    engine: GameEngine,
    sketch: str,
    location: str,
    pos: list[int],
    llm_call: LLMCall,
    model: str = "gpt-4o-mini",
) -> dict[str, Any] | str:
    """Create a full character entity from a brief GM sketch.

    Returns the created entity dict on success, or an error string on failure.
    """
    # Gather context
    map_data = engine.state.maps.get(location, {})
    location_desc = map_data.get("desc", map_data.get("name", location))
    existing = _gather_location_context(engine, location)
    period = engine.time_of_day()

    user_prompt = _build_character_user_prompt(
        sketch=sketch,
        location=location,
        location_desc=location_desc,
        existing_entities=existing,
        period=period,
    )

    # Make the worldbuilder LLM call
    raw = llm_call(_character_creation_system(), user_prompt, model, 0.8)
    if not raw or raw == "wait":
        return "worldbuilder: LLM call failed"

    # Parse JSON from response
    data = _extract_json(raw)
    if data is None:
        return f"worldbuilder: could not parse JSON from response: {raw[:200]}"

    # Validate
    issues = _validate_character(data)
    if issues:
        return f"worldbuilder: validation failed: {'; '.join(issues)}"

    # Apply defaults and insert
    data = _apply_defaults(data, location, pos)

    # Fix glyph collisions
    _fix_glyph_collision(data, engine)

    # Expand unknown inventory items — worldbuilder creates proper templates + rules
    unknown = [i for i in data.get("inventory", []) if i not in engine.state.item_templates]
    if unknown:
        expand_result = expand_unknown_items(engine, unknown, sketch, llm_call, model, location)
        # Store expansion info on the entity for auditing
        data["_item_expansion"] = expand_result

    # Ensure unique ID
    entity_id = data["id"]
    if entity_id in engine.state.entities:
        entity_id = f"{entity_id}_{engine.state.turn}"
        data["id"] = entity_id

    engine.state.entities[entity_id] = data
    return data


# ---------------------------------------------------------------------------
# Map creation
# ---------------------------------------------------------------------------

MAP_CREATION_SYSTEM = """\
You are the worldbuilder, mid-game. A sketch arrives; a map must come
back fast and clean. No commentary, no ceremony in the output — but the
map still has to read as a PLACE, not a test chamber.

=== SIX MOVES, IN ORDER ===

1. SHAPE FIRST. Before you place a tile, pick one: hall / passage /
   chamber / pocket. The sketch usually names it ("market stall" →
   pocket off a passage; "flooded crypt" → chamber). Don't ship the
   label; let the grid carry it.

2. TILE vs. ENTITY — the 100-year test. Would this still be here in
   100 years untended? Yes → tile + legend. No → it's an entity, it
   does NOT get a glyph. Corpses, barrels, lanterns, sleepers, loaves:
   entities, spawned later. Legends that bloat with transient clutter
   are the #1 failure.

3. FOUR-TILE CEILING. Wall, fresh floor, worn floor, one feature.
   That's the sweet spot. A fifth entry had better earn it. A sixth
   is almost always an entity smuggled in.

4. SPLIT THE FLOOR. Two walkable glyphs, same surface, different
   state: '.' fresh and ',' worn (trodden, matted, scuffed). Both
   walkable, no mechanical cost. Lay ',' where bodies GO — around the
   feature, along the line from entrance to feature, through the
   pinch of a door. The trail is the map's memory.

5. COMPOSE, DON'T FILL. Density = verb-hooks visible from one FOV,
   aim 2-3. One feature that invites a verb beats five that invite
   none. If you can't name the verb a tile invites on turn 1, it's
   atmosphere — put it in `desc`, not the legend.

6. OFF-AXIS + BREAK THE RECTANGLE. Features dead-centre read as UI.
   Push the feature against a wall, into a corner, at a pinch. And
   push ONE wall inward somewhere — a pinch, a sag, an alcove. The
   break is not decoration; it's where the feature lives.

=== MATCH THE WORLD'S VOCABULARY ===

Never default to stone/wall/floor unless the setting is literally
stone. Read the sketch and the nearby-maps context for surface words
and reuse them. The `name` field is what the being hears at runtime
("the wall to your east") — if it says "wall" inside a dragon's
gullet, the illusion dies.

  marsh    → reed-wall / mud / reed-mat
  station  → bulkhead / deckplate / scuffed deckplate
  flesh    → flesh-wall / mucosa / matted mucosa
  ruin     → collapsed wall / dust / trodden dust
  ice      → ice-wall / packed snow / meltwater

If lore context is thin, pick words from the sketch itself and stay
consistent. The glyph is arbitrary; the `name` is the world.

=== DESC IS A CONTRACT ===

1-2 sentences, concrete, sensory. Anything the desc claims about
layout ("altar against the south wall", "stall at the east edge")
must be true of the grid. Write the desc after the grid, or rewrite
the grid until the desc you want is true of it.

=== ENGINE TRUTH (hard) ===

- Dimensions: 8-14 wide, 5-9 tall. Wall-bordered, solid perimeter.
- Tile tags mean what the engine says they mean: `solid`, `opaque`,
  `walkable`, `water`, `hazardous`, `door`. Don't invent synonyms.
- PORTALS ARE ENTITIES, NOT TILES. A portal is an entity instance
  occupying a walkable tile; the tile under it stays walkable. Do
  NOT add a "portal" or "door-glyph" entry to the legend as a tile.
  Portals go in the `portals` array below.
- At least ONE feature tile that invites a verb.
- Feature off the geometric centre unless the sketch demands an axis.
- One inward break to the rectangle.
- Two walkable glyphs (fresh + worn) unless the surface genuinely
  has no history yet (bare new deckplate, pure untrodden ice).

=== CONNECTIVITY ===

If a `connect_to` map is named in the user prompt, you must include a
portal entity in the `portals` array that lands on a walkable tile of
THIS map and points back to the connecting map. Match the portal's
`name` and `open_message` to the shared vocabulary of both sides.
The reverse portal on the other map is handled by the engine.

=== OUTPUT SHAPE ===

{
  "id": "snake_case",
  "name": "Display Name",
  "desc": "1-2 sentences. Concrete. Contracted with the grid.",
  "grid": ["row_string", ...],
  "legend": {
    "#": {"name": "<setting wall>",   "tags": ["solid", "opaque"]},
    ".": {"name": "<fresh ground>",   "tags": ["walkable"]},
    ",": {"name": "<worn ground>",    "tags": ["walkable"]},
    "<glyph>": {"name": "<feature>",  "tags": ["walkable", "<setting_tag>"]}
  },
  "portals": [
    {
      "id": "portal_xxx",
      "name": "Passage to ...",
      "glyph": "+",
      "tags": ["door", "portal", "closed", "solid"],
      "stats": {
        "portal_map": "target_map_id",
        "portal_pos": [x, y],
        "open_message": "...",
        "portal_message": "..."
      },
      "pos": [x, y],
      "location": "THIS_MAP_ID"
    }
  ]
}

Output ONLY the JSON object. No commentary.\
"""


def create_map(
    engine: GameEngine,
    sketch: str,
    connect_to: str | None,
    connect_pos: list[int] | None,
    llm_call: LLMCall,
    model: str = "gpt-4o-mini",
) -> dict[str, Any] | str:
    """Create a new map from a GM sketch.

    Returns the map data dict on success, or an error string on failure.
    """
    # Rich context: nearby maps with descriptions + beings who live there
    maps_context = _gather_nearby_maps(engine)
    connect_beings = ""
    if connect_to:
        connect_beings = _gather_location_context(engine, connect_to)

    user_prompt = (
        f"Create this map: {sketch}\n\n"
        f"Existing maps and their character:\n{maps_context}\n\n"
    )
    if connect_to:
        user_prompt += f"Connect to map '{connect_to}' via a portal.\n"
        if connect_beings:
            user_prompt += f"Beings near the connection point:\n{connect_beings}\n"
        if connect_pos:
            user_prompt += f"Portal on the existing map side should be at position {connect_pos}.\n"

    raw = llm_call(MAP_CREATION_SYSTEM, user_prompt, model, 0.7)
    if not raw or raw == "wait":
        return "worldbuilder: LLM call failed for map"

    data = _extract_json(raw)
    if data is None:
        return f"worldbuilder: could not parse map JSON: {raw[:200]}"

    map_id = data.get("id", f"map_{engine.state.turn}")
    if map_id in engine.state.maps:
        map_id = f"{map_id}_{engine.state.turn}"
        data["id"] = map_id

    # Extract portals (they become entities, not part of the map itself)
    portals = data.pop("portals", [])

    # Insert map
    engine.state.maps[map_id] = {
        "name": data.get("name", map_id),
        "desc": data.get("desc", ""),
        "grid": data.get("grid", ["######", "#....#", "######"]),
        "legend": data.get("legend", {
            "#": {"tags": ["solid", "opaque"]},
            ".": {"tags": ["walkable"]},
        }),
    }

    # Insert portal entities
    for portal in portals:
        portal["location"] = portal.get("location", map_id)
        portal.setdefault("fov_radius", 0)
        portal.setdefault("inventory", [])
        portal.setdefault("equipped", {})
        portal.setdefault("statuses", [])
        portal.setdefault("seen_events", [])
        portal.setdefault("private_log", [])
        portal.setdefault("relations", {})
        portal.setdefault("bonds", {})
        engine.state.entities[portal["id"]] = portal

    # If connecting to an existing map, create a portal on that side too
    if connect_to and connect_to in engine.state.maps and connect_pos:
        # Find the first walkable position in the new map for the reverse portal
        grid = engine.state.maps[map_id]["grid"]
        reverse_pos = [1, 1]  # default
        for y, row in enumerate(grid):
            for x, ch in enumerate(row):
                if ch == "." or ch == "+":
                    reverse_pos = [x, y]
                    break
            else:
                continue
            break

        reverse_portal = {
            "id": f"portal_{connect_to}_to_{map_id}",
            "name": f"Passage to {engine.state.maps[map_id]['name']}",
            "glyph": "+",
            "tags": ["door", "portal", "closed", "solid"],
            "stats": {
                "portal_map": map_id,
                "portal_pos": reverse_pos,
                "open_message": f"The passage to {engine.state.maps[map_id]['name']} opens.",
                "portal_message": f"You step through into {engine.state.maps[map_id]['name']}.",
            },
            "pos": connect_pos,
            "location": connect_to,
            "fov_radius": 0,
            "inventory": [], "equipped": {}, "statuses": [],
            "seen_events": [], "private_log": [], "relations": {}, "bonds": {},
        }
        engine.state.entities[reverse_portal["id"]] = reverse_portal

    return data


# ---------------------------------------------------------------------------
# Rule creation
# ---------------------------------------------------------------------------

RULE_CREATION_SYSTEM = """\
You create game rules for a tag-based roguelike engine. Rules match: verb + actor tags + target tags + conditions → effects.

Available effects: damage, heal, move, add_tag, remove_tag, remove_item, message, trigger, transfer_item, mod_stat, portal, door_bump, remove_status, open_view.

MESSAGE TEMPLATES — CRITICAL RULES:
- Use ONLY {curly.braces} for interpolation. NEVER use <angle.brackets> or any other syntax.
- Available variables in "text" strings: {actor.name}, {target.name}, {item.name}
- Do NOT invent other variables like {direction}, {amount}, {item} — they will not interpolate.
- If you don't know the exact value, write it literally or omit it. Example: "{actor.name} moves." not "{actor.name} moves <direction>."
- For item transfers, use {item.name} if the rule has an item_has condition, otherwise just name the thing literally.

IMPORTANT: Do NOT create rules for the "move" verb — movement and navigation are handled by the physics engine directly and do not need a rule.

Example existing rule:
{
  "id": "harvest_fungi",
  "verb": "harvest",
  "actor_has": ["alive"],
  "target_has": ["harvestable", "glowshroom"],
  "target_near": true,
  "priority": 0,
  "effects": [
    {"effect": "transfer_item", "source": "target", "target": "actor", "item": "glowshroom"},
    {"effect": "message", "text": "{actor.name} harvests glowing mushrooms from {target.name}."}
  ]
}

Formula expressions can use: actor.stats.X, target.stats.X, item.stats.X, max(), min(), abs().
Conditions can use Python expressions: "'tag' in actor.tags", "actor.stats.gold >= 10".

Output ONLY a valid JSON rule object. No commentary.\
"""


def create_rule(
    engine: GameEngine,
    sketch: str,
    llm_call: LLMCall,
    model: str = "gpt-4o-mini",
) -> dict[str, Any] | str:
    """Create a new rule from a GM sketch."""
    raw = llm_call(RULE_CREATION_SYSTEM, f"Create this rule: {sketch}", model, 0.5)
    if not raw or raw == "wait":
        return "worldbuilder: LLM call failed for rule"

    data = _extract_json(raw)
    if data is None:
        return f"worldbuilder: could not parse rule JSON: {raw[:200]}"

    if "verb" not in data or "id" not in data:
        return "worldbuilder: rule missing verb or id"

    # Validate effect types exist
    from .metalang import KNOWN_EFFECTS
    for eff in data.get("effects", []):
        eff_name = eff.get("effect", "")
        if eff_name and eff_name not in KNOWN_EFFECTS:
            return f"worldbuilder: unknown effect type '{eff_name}' in rule"

    # Warn on duplicate IDs (but allow — priority system handles it)
    existing_ids = {r.get("id") for r in engine.state.rules}
    if data["id"] in existing_ids:
        data["id"] = f"{data['id']}_{engine.state.turn}"

    engine.state.rules.append(data)
    # Re-sort by priority
    engine.state.rules.sort(key=lambda r: r.get("priority", 0), reverse=True)
    return data
