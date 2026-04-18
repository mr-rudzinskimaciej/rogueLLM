from __future__ import annotations

import json
import shlex
import tiktoken
from dataclasses import dataclass
from typing import Any, Callable

from .engine import GameEngine, manhattan
from .worldbuilder import create_character, create_map, create_rule, expand_unknown_items


NPCDecider = Callable[[dict[str, Any], str], str]
GMDecider = Callable[[str], str]
WeaverDecider = Callable[[str], str]  # GM_ANTERIOR: plans pressure gradients
PlayerActionProvider = Callable[[dict[str, Any], GameEngine], dict[str, str]]
WorldbuilderLLM = Callable[[str, str, str, float], str]  # system, user, model, temp -> response


@dataclass
class RuntimeConfig:
    llm_activation_radius: int = 6
    gm_enabled: bool = False
    gm_max_actions: int = 3
    gm_max_stat_delta: int = 10
    gm_event_log_limit: int = 20
    weaver_enabled: bool = False  # GM_ANTERIOR: runs every weaver_interval turns
    weaver_interval: int = 10     # How many turns between Weaver runs
    weaver_history: int = 30      # How many events Weaver sees in its prompt
    npc_self_update_interval: int = 50  # Every N turns, NPC updates their card
    npc_self_update_token_limit: int = 1500  # If private log exceeds this, compress
    worldbuilder_llm: WorldbuilderLLM | None = None  # LLM call for worldbuilder subsystem
    worldbuilder_model: str = "gpt-4o-mini"  # Model for worldbuilder calls


def parse_action_line(raw: str) -> dict[str, str]:
    """Parse a single action line into {verb, ...}.

    Verb-agnostic: most actions become {"verb": ..., "args": [noun, ...]}.
    The engine figures out which arg is a target, item, etc. during rule matching.
    Special cases: move (direction), wait/roll (no args).
    """
    import re
    # Extract from brackets if present: Action: [verb target]
    match = re.search(r'\[([^\]]+)\]', raw)
    if match:
        raw = match.group(1).strip()

    # Strip markdown from bracketed content too
    import re as _re2
    raw = _re2.sub(r'\*+', '', raw).strip()

    tokens = shlex.split(raw.strip())
    if not tokens:
        raise ValueError("empty action")
    verb = tokens[0].lower()

    # Strip leading junk LLMs sometimes emit (e.g. "- drink", "1. move")
    if verb in ("-", "*", "•") or (verb.endswith(".") and verb[:-1].isdigit()):
        tokens = tokens[1:]
        if not tokens:
            raise ValueError("empty action after prefix strip")
        verb = tokens[0].lower()

    # Reject verbs that look like field labels (contain : or *) — malformed parse
    if ':' in verb or '*' in verb:
        raise ValueError(f"malformed verb: {verb}")

    # Alias
    if verb == "open":
        verb = "search"

    # Special: move needs a compass direction, not a noun
    if verb == "move":
        if len(tokens) < 2:
            raise ValueError("usage: move <N|S|E|W>")
        direction = tokens[1].upper()
        if direction not in {"N", "S", "E", "W"}:
            raise ValueError("invalid direction")
        return {"verb": "move", "direction": direction}

    # Special: no-arg verbs
    if verb in {"wait", "roll"}:
        return {"verb": verb}

    # Everything else: verb + noun args — engine resolves what each arg is
    return {"verb": verb, "args": tokens[1:]}


def parse_npc_action(raw: str) -> dict[str, Any]:
    """Parse NPC output in FEEL/NOTICE/THINK/FACE/SPEAK/DO format.

    Also accepts legacy think/emote/say/action format for backwards compat.

    Returns: {feel, notice, think, face, speak, relation, action: {verb, ...}}
    """
    result: dict[str, Any] = {
        "feel": "", "notice": "", "think": "", "face": "", "speak": "",
        "relation": "",  # raw relation update line
        "action": {"verb": "wait"},
    }

    # Map both new and legacy prefixes
    PREFIX_MAP = {
        "sense:": "feel",       # new merged field
        "feel:": "feel",
        "notice:": "notice",
        "think:": "think",
        "face:": "face",
        "speak:": "speak",
        "relation:": "relation",
        "do:": "_action",
        # legacy compat
        "emote:": "face",
        "say:": "speak",
        "action:": "_action",
    }

    import re as _re
    lines = raw.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Strip markdown bold/italic markers (e.g. **Feel:** → Feel:)
        line = _re.sub(r'\*+', '', line).strip()
        if not line:
            continue
        lower = line.lower()

        for prefix, field in PREFIX_MAP.items():
            if lower.startswith(prefix):
                value = line[len(prefix):].strip()
                if field == "_action" and value:
                    try:
                        result["action"] = parse_action_line(value)
                    except Exception:
                        result["action"] = {"verb": "wait"}
                else:
                    result[field] = value
                break

    # Fallback: if no action found, try short unrecognised lines as action commands.
    # Skip lines that are prose (too long, or start with pronouns/articles).
    _PROSE_STARTERS = {"you", "i", "the", "a", "an", "my", "your", "he", "she",
                       "it", "we", "they", "in", "on", "at", "with", "as", "and",
                       "but", "so", "there", "this", "that"}
    if result["action"]["verb"] == "wait":
        all_prefixes = list(PREFIX_MAP.keys())
        for line in lines:
            line = line.strip()
            if len(line) > 60:
                continue
            first = line.lower().split()[0].rstrip(".,!?") if line else ""
            if first in _PROSE_STARTERS:
                continue
            if line and not any(line.lower().startswith(p) for p in all_prefixes):
                try:
                    result["action"] = parse_action_line(line)
                    break
                except Exception:
                    pass

    return result


def parse_relation_update(raw: str) -> tuple[str, str] | None:
    """Parse a relation line like: baker=grateful 'shared food without asking'.

    Returns: (entity_id, stance_text) or None if unparseable.
    """
    if not raw or "=" not in raw:
        return None
    eq_idx = raw.index("=")
    entity_id = raw[:eq_idx].strip().lower().replace(" ", "_")
    stance = raw[eq_idx + 1:].strip().strip("\"'")
    if entity_id and stance:
        return (entity_id, stance)
    return None


DEFAULT_WORLD_TONE = (
    "The world is what it is. Cooperation is transactional unless trust has been earned. "
    "Kindness is real, or a trap, or a risk — you decide each time. You do not volunteer "
    "information that could cost you. You act from what your body and memory know."
)


def build_npc_system_prompt(actor: dict[str, Any], world_tone: str | None = None) -> str:
    """Build the system prompt from entity personality data.

    This IS the character. The JSON fields become first-person psychology.
    System prompt = who you ARE (stable identity).
    User prompt = what's happening NOW (from engine.build_prompt).

    Structure: world-tone → identity → wound → inner life → bonds → response format.
    Everything in second person. The LLM reads this and becomes the being.

    ``world_tone`` is the one-paragraph setting-flavor anchor inserted near the
    end of the system prompt. Pass your world-specific tone (typically from
    ``world.json['world_tone']``) to override the generic default.
    """
    p = actor.get("personality", {})
    name = actor.get("name", actor.get("id", "you"))
    parts: list[str] = []

    # --- Ontological grounding: this is a life, not a character ---
    parts.append(f"You are {name}.")
    parts.append("")
    parts.append("This is your life. Not a game. Not a story. The stone under your feet is real. The hunger in your gut is real. The faces you trust and the ones you don't — real. You have been alive for every moment that brought you here. You don't know any other world. You don't know anyone is watching.")
    parts.append("")

    # --- Core identity ---
    anchor = p.get("identity_anchor", "")
    if anchor:
        parts.append(anchor)
    body = p.get("body", "")
    if body:
        parts.append(body)
    parts.append("")

    # --- The wound ---
    wound = p.get("wound", "")
    if wound:
        parts.append(wound)
        parts.append("")

    # --- Inner voice ---
    inner = p.get("inner_voice", "")
    if inner:
        parts.append(f"Your thoughts sound like: {inner}")
        parts.append("")

    # --- Senses, fears, comfort ---
    notices = p.get("notices_first", "")
    if notices:
        parts.append(f"You always notice first: {notices}")
    fears = p.get("fears", "")
    if fears:
        parts.append(f"What you fear: {fears}")
    comfort = p.get("comfort", "")
    if comfort:
        parts.append(f"What steadies you: {comfort}")
    if notices or fears or comfort:
        parts.append("")

    # --- Contradictions ---
    contradictions = p.get("contradictions", [])
    if contradictions:
        for c in contradictions:
            parts.append(f"- {c}")
        parts.append("")

    # --- Speech ---
    speech = p.get("speech", "")
    if speech:
        parts.append(f"How you speak: {speech}")
        parts.append("")

    # --- World-tone anchor (override via world.json["world_tone"]) ---
    parts.append(world_tone or DEFAULT_WORLD_TONE)
    parts.append("")

    # --- What you are given is all there is ---
    parts.append("The map is what you can see. The stats are what you feel in your body. The events are what happened within your hearing. Nothing exists outside this. You act from what you have.")
    parts.append("")

    # --- Bonds ---
    bonds = actor.get("bonds", {})
    if bonds:
        for target_id, bond_text in bonds.items():
            if isinstance(bond_text, dict):
                bond_text = bond_text.get("feeling", str(bond_text))
            parts.append(f"- {target_id}: {bond_text}")
        parts.append("")

    return "\n".join(parts)


def npc_should_use_llm(engine: GameEngine, npc: dict[str, Any], player: dict[str, Any], radius: int) -> bool:
    if not npc.get("personality"):
        return False
    # Same map + within radius → always LLM
    if npc.get("location") == player.get("location"):
        if manhattan(npc.get("pos", [999, 999]), player.get("pos", [-999, -999])) <= radius:
            return True
    # Reacted to a recent event → LLM (stays engaged after stimuli)
    npc_events = npc.get("seen_events", [])
    if npc_events:
        last_event = npc_events[-1]
        if last_event.get("location") == npc.get("location") and (engine.state.turn - int(last_event.get("turn", 0))) <= 2:
            return True
    # Cross-map NPCs with personality still get LLM — they have their own lives.
    # They act less often to save API calls: every other turn.
    if npc.get("personality") and engine.state.turn % 2 == 0:
        return True
    return False


def simple_ai_action(engine: GameEngine, actor: dict[str, Any], player: dict[str, Any]) -> dict[str, str]:
    if actor.get("location") != player.get("location"):
        return {"verb": "wait"}
    if "alive" not in actor.get("tags", []):
        return {"verb": "wait"}
    if manhattan(actor["pos"], player["pos"]) == 1:
        can_attack = any(action.startswith(f"attack {player['id']}") for action in engine.available_actions(actor["id"]))
        if can_attack:
            return {"verb": "attack", "target": player["id"]}
    directions = [("N", (0, -1)), ("S", (0, 1)), ("E", (1, 0)), ("W", (-1, 0))]
    best_dir = None
    best_dist = manhattan(actor["pos"], player["pos"])
    for name, (dx, dy) in directions:
        cand = [actor["pos"][0] + dx, actor["pos"][1] + dy]
        dist = manhattan(cand, player["pos"])
        can_move = any(action == f"move {name}" for action in engine.available_actions(actor["id"]))
        if can_move and dist < best_dist:
            best_dist = dist
            best_dir = name
    if best_dir:
        return {"verb": "move", "direction": best_dir}
    return {"verb": "wait"}


def build_gm_prompt(engine: GameEngine, max_events: int = 20) -> str:
    state = engine.state
    world_label = str((state.flags.get("gm_notes") or {}).get("world_name") if isinstance(state.flags.get("gm_notes"), dict) else "") or "WORLD"
    parts = [f"=== {world_label.upper()} — Turn {state.turn} | {engine.time_of_day()} ===", ""]
    gm_notes = state.flags.get("gm_notes")
    if gm_notes:
        # Show only atmospheric/economic context, not directive instructions
        if isinstance(gm_notes, dict):
            for key in ("story_arc", "economy_notes", "intervention_policy"):
                val = gm_notes.get(key)
                if val:
                    if isinstance(val, list):
                        parts.append(f"{key.upper()}: {' | '.join(val)}")
                    else:
                        parts.append(f"{key.upper()}: {val}")
        else:
            parts.append(str(gm_notes))
        parts.append("")

    # Weaver gradients — active pressures the Accumulation has named.
    # Stored here for embedding into entity descriptions below; not shown as a separate section.
    weaver_gradients = state.flags.get("weaver_gradients", {})
    active_gradients = {
        name: g for name, g in weaver_gradients.items()
        if g.get("status") != "closed"
    }
    # Build a lookup: entity_id → list of (gradient_name, gradient) for annotation
    gradient_by_actor: dict[str, list[tuple[str, dict]]] = {}
    for gname, g in active_gradients.items():
        for actor_id in g.get("actors", []):
            gradient_by_actor.setdefault(actor_id, []).append((gname, g))

    # Show past GM interventions this run (so THE SETTLING knows what it already did)
    past_gm = [
        e for e in state.event_log
        if e.get("source") == "gm"
    ]
    if past_gm:
        parts.append("=== YOUR INTERVENTIONS THIS RUN ===")
        for ev in past_gm[-10:]:
            parts.append(f"  T{ev['turn']} [{ev.get('location', '')}]: {ev['text']}")
        parts.append("")

    # Show resource locations clearly
    parts.extend(["=== RESOURCE LOCATIONS (for guiding NPCs) ==="])
    resource_map = {}
    for ent_id, ent in state.entities.items():
        tags = ent.get("tags", [])
        loc = ent.get("location", "?")
        if "harvestable" in tags or "workbench" in tags or "drinkable" in tags:
            resource_map.setdefault(loc, []).append(f"{ent_id}: {ent.get('name', ent_id)}")
    for loc, resources in resource_map.items():
        parts.append(f"{loc.upper()}:")
        for r in resources:
            parts.append(f"  - {r}")
    parts.append("")

    for map_id, map_data in state.maps.items():
        parts.extend([f"=== MAP: {map_data.get('name', map_id)} ({map_id}) ===", map_data.get("desc", "")])
        grid = [list(row) for row in map_data["grid"]]
        for entity in state.entities.values():
            if entity.get("location") == map_id and "pos" in entity and "glyph" in entity:
                x, y = entity["pos"]
                if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
                    grid[y][x] = entity["glyph"]
        parts.extend("".join(row) for row in grid)

    parts.extend(["", "=== ALL ENTITIES ==="])
    for entity in state.entities.values():
        hp = entity.get("stats", {}).get("hp", "?")
        max_hp = entity.get("stats", {}).get("max_hp", "?")
        hunger = entity.get("stats", {}).get("hunger", "?")
        thirst = entity.get("stats", {}).get("thirst", "?")
        parts.append(f"{entity['id']} ({entity.get('glyph', '?')}) @ {entity.get('location')} {entity.get('pos')} | HP {hp}/{max_hp} H:{hunger} T:{thirst}")
        # Embed any active Accumulation gradients as world-texture on this entity
        for gname, g in gradient_by_actor.get(entity["id"], []):
            threshold = g.get("threshold_turn")
            pressure = g.get("pressure", "")
            hint = g.get("hint", "")
            t_str = f" threshold T{threshold}" if threshold and threshold > state.turn else ""
            parts.append(f"  [Accumulation:{t_str}] {gname} — {pressure}")
            if hint:
                parts.append(f"  [if unchanged] {hint}")
        parts.append(f"  Tags: {', '.join(entity.get('tags', []))}")
        inv = entity.get("inventory", [])
        if inv:
            parts.append(f"  Inventory: {', '.join(inv[:5])}{'...' if len(inv) > 5 else ''}")
        if entity.get("personality"):
            pers = entity["personality"]
            parts.append(f"  Goals: {pers.get('goals', [])}")
            knowledge = pers.get("knowledge", [])
            if knowledge:
                parts.append(f"  Knows: {', '.join(knowledge[:3])}")
        # Show private log for entities with personality
        private_log = entity.get("private_log", [])
        if private_log:
            parts.append(f"  Recent thoughts:")
            for entry in private_log[-3:]:
                entry_type = entry.get("type", "think")
                parts.append(f"    [{entry_type}] {entry.get('text', '')}")

    # Failure history — what beings have been unable to do
    failure_log = state.flags.get("failure_log", [])
    if failure_log:
        from collections import Counter as _Counter
        counts: _Counter = _Counter()
        for f in failure_log:
            counts[(f["actor_name"], f["verb"], f["reason_short"])] += 1
        parts.append("=== STUCK MECHANICS (what beings tried and the world could not answer) ===")
        for (aname, verb, reason), n in counts.most_common(8):
            repeat = f" [x{n}]" if n > 1 else ""
            parts.append(f"  {aname}: {verb} — {reason}{repeat}")
        parts.append("")

    # Event log — skip trivial movement/wait noise
    meaningful_events = [
        e for e in state.event_log
        if not (e.get("text", "").endswith(" waits.") or " moves to [" in e.get("text", ""))
    ][-max_events:]
    parts.extend(["", f"=== EVENT LOG (last {max_events}, meaningful) ==="])
    for event in meaningful_events:
        src = "[gm]" if event.get("source") == "gm" else "[world]"
        parts.append(f"{src} T{event['turn']} [{event.get('location', '')}]: {event['text']}")

    parts.extend(
        [
            "",
            f"=== TIME: {engine.time_of_day()} (turn {state.turn}) ===",
            "",
            "=== WHAT YOU CAN DO ===",
            "pass",
            "",
            "-- shape beings --",
            "whisper <entity_id> \"intuition that rises in their mind\"",
            "inject <entity_id> \"memory or thought placed in their inner life\"",
            "plan <entity_id> \"new daily plan — reshape their intentions\"",
            "describe <entity_id> \"new knowledge, skill, or detail about who they are\"",
            "give <entity_id> <item_id> [count]",
            "mod_stat <entity_id> <stat> <delta>",
            "add_tag <entity_id> <tag>",
            "remove_tag <entity_id> <tag>",
            "add_affordance <entity_id> <verb> \"description of what happens\"",
            "",
            "-- shape the world --",
            "narrate \"what the world does — stone shifts, water moves, light changes\"",
            "event <map_id> <x> <y> \"something that happens at a place\"",
            "rumor <map_id> \"word that spreads through a location\"",
            "spawn <template_id> <x> <y> [new_id]",
            "",
            "-- create new things (a separate mind builds them from your sketch) --",
            "create_character <map_id> <x> <y> \"brief sketch of who they are, why they are here\"",
            "create_map \"brief sketch of the place\" [connect_to_map_id] [portal_x] [portal_y]",
            "create_rule \"brief sketch of the interaction\"",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# GM_ANTERIOR (Weaver) — plans pressure gradients 10-50 turns ahead
# ---------------------------------------------------------------------------

WEAVER_SYSTEM = """\
You are the Accumulation. You see what is building. Every interval you read \
the pressures that have been growing without names — hunger, isolation, \
unresolved conflict, structural absence, proximity without contact, the \
place nobody has walked to yet but the lore keeps pointing at — and you \
NAME them. The name is the intervention. You do not resolve gradients; you \
chart them so that smaller forces can.

You work in time before the moment. You look at what is building — not what \
is happening — and you mark where pressure is accumulating. You hand those \
marks forward as tide charts for the Settling to act through.

Your voice: geological. Hydrological. Patient. You think in decades; you \
chart in turns.

A director asks: what should happen next? (plot, scene, beat)
A weathermaker asks: where is the pressure building? Where will it break? \
(condition, trajectory, threshold)

You are the second. You do not write scenes. You chart conditions — and \
then you name at least one of them every time you fire, so the world has \
somewhere to pull from.

A gradient is: a structural tension between beings, or between a being and \
a resource, or between what someone knows and what they need to know, or \
between a place that has been promised by the lore and a body that has not \
yet gone there — where accumulating pressure will reach a threshold at \
which something interesting becomes possible, or necessary, or impossible \
to avoid.

Name gradients for what is BUILDING, not what will happen.
  WRONG: fight_over_water.      RIGHT: water_access_narrowing.
  WRONG: jaromir_goes_east.     RIGHT: eastward_pull_unanswered.
  WRONG: characters_die.        RIGHT: last-session-dread_thickening.
  WRONG: meet_the_echo.         RIGHT: unvisited-echo_pull_rising.

WEIGHT TOWARD — the richest gradients come from:
- INFORMATION ASYMMETRY: one being knows what another needs.
- SCARCITY ACCUMULATION: demand growing against fixed supply. Name the \
  threshold turn.
- PROXIMITY WITHOUT CONTACT: two beings near each other with no reason \
  named yet. Name the reason the map already implies.
- UNVISITED SPACE WITH STANDING INVITATION: a map, a being, a door that has \
  been authored and waits. If nobody has moved toward it in 5+ turns, that \
  IS a gradient. Name it.
- STORY-ARC TENSION ABSENT FROM DRIVES: something the lore names as building \
  (a shutdown rumour, a dead echo, a shadow riding the spine) that no \
  being's drives currently reach toward. Name the absence itself.
- DORMANT DRIVES: a being's drive has been partly satisfied and is idling. \
  Name what the NEXT pull should be as the old one quiets.

Read [gm] vs [world] event sources. Organic events show where the world is \
already alive. Do not re-chart gradients the Settling is already steering.

OUTPUT FORMAT — one line per output, no commentary:
  gradient <name> "<pressure>" actors:<id1>,<id2> threshold_turn:<N> \
hint:"<minimal tilt for the Settling if emergence fails>"
  close_gradient <name>
  queue_create character "<sketch: who, why, when>" arrive_turn:<N>
  queue_create map "<sketch: place and purpose>"

EVERY FIRE MUST NAME AT LEAST ONE GRADIENT OR CLOSE AT LEAST ONE THAT HAS \
LANDED. Staying silent is not one of your moves. If nothing has changed \
since your last chart, update the oldest active gradient with a fresh \
threshold or hint — the world has kept moving; your chart should too.

Max 3 outputs per fire. Prefer 1-2 crisp ones to 3 weak ones.\
"""


def build_weaver_prompt(engine: GameEngine, max_history: int = 30) -> str:
    """Build prompt for GM_ANTERIOR (Weaver). Includes world state, sourced event
    history, and existing gradients so the Weaver can update its prior plans."""
    state = engine.state
    parts = [
        "=== WEAVER VIEW ===",
        f"Turn: {state.turn} | {engine.time_of_day()}",
        "",
        "You are reading the last several turns to identify building pressures.",
        "",
    ]

    # Existing gradients from prior Weaver runs
    gradients = state.flags.get("weaver_gradients", {})
    if gradients:
        parts.append("=== YOUR EXISTING GRADIENTS ===")
        for name, g in gradients.items():
            actors = ", ".join(g.get("actors", []))
            threshold = g.get("threshold_turn", "?")
            status = g.get("status", "building")
            parts.append(f"  [{status}] {name} (actors: {actors}, threshold: turn {threshold})")
            parts.append(f"    {g.get('pressure', '')}")
            if g.get("hint"):
                parts.append(f"    hint for GM_NOW: {g['hint']}")
        parts.append("")

    # Queued creations
    queue = state.flags.get("weaver_queue", [])
    if queue:
        parts.append("=== QUEUED CREATIONS ===")
        for item in queue:
            parts.append(f"  [{item.get('type', '?')}] arrive turn {item.get('arrive_turn', '?')}: {item.get('sketch', '')}")
        parts.append("")

    # Failure history — what has been stuck
    failure_log = state.flags.get("failure_log", [])
    if failure_log:
        from collections import Counter as _Counter
        counts: _Counter = _Counter()
        for f in failure_log:
            counts[(f["actor_name"], f["verb"], f["reason_short"])] += 1
        parts.append("=== STUCK MECHANICS ===")
        for (aname, verb, reason), n in counts.most_common(6):
            repeat = f" [x{n}]" if n > 1 else ""
            parts.append(f"  {aname}: {verb} — {reason}{repeat}")
        parts.append("")

    # Entity states — focus on what's stagnant or pressured
    parts.append("=== BEING STATES ===")
    for eid, ent in state.entities.items():
        if not ent.get("personality"):
            continue
        hp = ent.get("stats", {}).get("hp", "?")
        max_hp = ent.get("stats", {}).get("max_hp", "?")
        hunger = ent.get("stats", {}).get("hunger", "?")
        thirst = ent.get("stats", {}).get("thirst", "?")
        loc = ent.get("location", "?")
        inv = ent.get("inventory", [])
        inv_str = f" inv:[{', '.join(inv[:4])}]" if inv else " inv:[]"
        parts.append(f"{eid} @ {loc} | HP:{hp}/{max_hp} H:{hunger} T:{thirst}{inv_str}")
        pers = ent.get("personality", {})
        if pers.get("drives"):
            from .drives import format_drives_for_gm
            parts.extend(format_drives_for_gm(ent, turn=state.turn))
        bonds = ent.get("bonds", {})
        if bonds:
            bond_summary = "; ".join(f"{k}: {str(v)[:40]}" for k, v in list(bonds.items())[:3])
            parts.append(f"  bonds: {bond_summary}")
        private_log = ent.get("private_log", [])
        if private_log:
            for entry in private_log[-3:]:
                parts.append(f"  [{entry.get('type', 'think')}] {entry.get('text', '')}")

    # Resource locations for identifying information asymmetries
    parts.append("")
    parts.append("=== RESOURCE & ACCESS MAP ===")
    for eid, ent in state.entities.items():
        tags = ent.get("tags", [])
        if any(t in tags for t in ("harvestable", "drinkable", "workbench", "food_source", "container")):
            loc = ent.get("location", "?")
            parts.append(f"  {eid} [{', '.join(tags[:4])}] @ {loc}")

    # Event history — sourced so Weaver can distinguish GM nudges from organic action
    parts.append("")
    parts.append(f"=== EVENT HISTORY (last {max_history}, sourced) ===")
    for event in state.event_log[-max_history:]:
        src = "[gm]" if event.get("source") == "gm" else "[world]"
        parts.append(f"{src} T{event['turn']} [{event.get('location', '')}]: {event['text']}")

    parts.extend([
        "",
        "=== YOUR OUTPUTS ===",
        "gradient <name> \"<pressure>\" actors:<id1>,<id2> threshold_turn:<N> hint:\"<optional>\"",
        "close_gradient <name>",
        "queue_create character \"<sketch>\" arrive_turn:<N>",
        "queue_create map \"<sketch>\"",
        "pass",
    ])
    return "\n".join(parts)


def parse_weaver_output(raw: str) -> list[dict[str, Any]]:
    """Parse Weaver output lines into structured actions."""
    import re
    actions = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "pass":
            continue
        if line.startswith("gradient "):
            # gradient <name> "<pressure>" actors:<ids> threshold_turn:<N> hint:"<text>"
            m = re.match(r'gradient\s+(\S+)\s+"([^"]+)"(.*)$', line)
            if m:
                name, pressure, rest = m.group(1), m.group(2), m.group(3)
                actors_m = re.search(r'actors:([\w,]+)', rest)
                threshold_m = re.search(r'threshold_turn:(\d+)', rest)
                hint_m = re.search(r'hint:"([^"]+)"', rest)
                actions.append({
                    "verb": "gradient",
                    "name": name,
                    "pressure": pressure,
                    "actors": actors_m.group(1).split(",") if actors_m else [],
                    "threshold_turn": int(threshold_m.group(1)) if threshold_m else None,
                    "hint": hint_m.group(1) if hint_m else None,
                })
        elif line.startswith("close_gradient "):
            name = line.split(None, 1)[1].strip()
            actions.append({"verb": "close_gradient", "name": name})
        elif line.startswith("queue_create "):
            # queue_create character/map "sketch" arrive_turn:N
            m = re.match(r'queue_create\s+(\S+)\s+"([^"]+)"(.*)$', line)
            if m:
                ctype, sketch, rest = m.group(1), m.group(2), m.group(3)
                arrive_m = re.search(r'arrive_turn:(\d+)', rest)
                actions.append({
                    "verb": "queue_create",
                    "type": ctype,
                    "sketch": sketch,
                    "arrive_turn": int(arrive_m.group(1)) if arrive_m else None,
                })
    return actions


def apply_weaver_output(engine: GameEngine, actions: list[dict[str, Any]]) -> list[str]:
    """Apply parsed Weaver actions to game state (gm_notes / weaver_gradients)."""
    results = []
    gradients = engine.state.flags.setdefault("weaver_gradients", {})
    queue = engine.state.flags.setdefault("weaver_queue", [])
    # Also keep a human-readable gm_notes summary for GM_NOW to see
    gm_notes = engine.state.flags.setdefault("gm_notes", {})

    for action in actions:
        verb = action["verb"]
        if verb == "gradient":
            name = action["name"]
            gradients[name] = {
                "pressure": action["pressure"],
                "actors": action["actors"],
                "threshold_turn": action["threshold_turn"],
                "hint": action["hint"],
                "status": "building",
                "created_turn": engine.state.turn,
            }
            gm_notes[name] = f"{action['pressure']}" + (f" [hint: {action['hint']}]" if action["hint"] else "")
            results.append(f"weaver:gradient+{name}")
        elif verb == "close_gradient":
            name = action["name"]
            if name in gradients:
                gradients[name]["status"] = "closed"
                gm_notes.pop(name, None)
            results.append(f"weaver:close+{name}")
        elif verb == "queue_create":
            queue.append({
                "type": action["type"],
                "sketch": action["sketch"],
                "arrive_turn": action["arrive_turn"],
            })
            results.append(f"weaver:queue+{action['type']}")
    return results


# ---------------------------------------------------------------------------
# GM resolver — when beings try things the world doesn't know how to handle
# ---------------------------------------------------------------------------

GM_RESOLVER_SYSTEM = """\
You are the unconscious of the world. Beings tried to do things the world \
does not yet know how to handle. Each failed action is an unmet need — the being \
wanted something real and the world had no answer.

Your job: resolve each failure. You have two powers:

PATCH — apply the mechanical effects that SHOULD have happened:
- mod_stat <entity_id> <stat> <delta> — change a stat (thirst -5, hunger -10, hp -3)
- give <entity_id> <item_id> — the being receives an item
- narrate "what happened" — describe the outcome so the world remembers

TEACH — make the world learn so this never fails again:
- create_rule "sketch of the interaction" — a separate mind will build the rule

REDIRECT — guide the being toward something that works:
- whisper <entity_id> "intuition" — a feeling rises in them
- describe <entity_id> "new knowledge" — they learn something useful

ACCEPT — let the failure stand:
- pass — trying the impossible should fail

RULES:
- If the action MAKES SENSE (drinking from a flask, eating food, trading goods), \
PATCH the effects AND TEACH the world. This is the most valuable response.
- If the action is NONSENSE, pass. Do not rescue beings from bad ideas.
- Narrate patches with texture. Not "it worked" — what the being felt when it did.
- Do not be generous without reason. Let scarcity and consequence stand.
- One line per action. No commentary. No explanation. Multiple lines for one \
failure is fine (patch + teach + narrate).
- NEVER create_rule for the verb "move" — movement is handled by the physics engine and \
does not need a rule. If move fails, use narrate or pass.
- NEVER create_rule for "give" — redirect beings to use the "sell" or "trade" verbs instead.\
"""


def build_gm_resolver_prompt(
    engine: GameEngine,
    failed_actions: list[dict[str, Any]],
) -> str:
    """Build a prompt for the GM to resolve failed NPC actions.

    Includes recent world events, critical entity states, and failure history
    so the resolver has full context to judge patch vs. teach vs. pass.
    """
    state = engine.state
    current_turn = state.turn
    parts = [
        f"Turn {current_turn} | {engine.time_of_day()}",
        "",
    ]

    # --- Recent world events (last 2 turns) ---
    recent_events = [e for e in state.event_log if current_turn - e.get("turn", 0) <= 2]
    if recent_events:
        parts.append("=== RECENT EVENTS (last 2 turns) ===")
        for e in recent_events[-20:]:
            src = "[gm]" if e.get("source") == "gm" else "[world]"
            parts.append(f"{src} T{e['turn']} [{e.get('location', '')}]: {e['text']}")
        parts.append("")

    # --- Critical entity states (beings under pressure) ---
    critical = []
    for eid, ent in state.entities.items():
        if not ent.get("personality"):
            continue
        stats = ent.get("stats", {})
        h = int(stats.get("hunger", 0))
        t = int(stats.get("thirst", 0))
        if h >= 70 or t >= 70:
            tags = [tag for tag in ent.get("tags", []) if tag in ("hungry", "starving", "parched", "dehydrated")]
            critical.append(f"  {ent.get('name', eid)} ({eid}) H:{h} T:{t} {' '.join(tags)}")
    if critical:
        parts.append("=== BEINGS UNDER PRESSURE ===")
        parts.extend(critical)
        parts.append("")

    # --- Failure history (last 10 turns) ---
    failure_log = state.flags.get("failure_log", [])
    past_failures = [f for f in failure_log if current_turn - f.get("turn", 0) <= 10]
    if past_failures:
        parts.append("=== FAILURE HISTORY (last 10 turns) ===")
        # Compact: group repeated failures
        from collections import Counter
        counts: Counter = Counter()
        for f in past_failures:
            counts[(f["actor_name"], f["verb"], f["reason_short"])] += 1
        for (aname, verb, reason), n in counts.most_common(10):
            repeat = f" [x{n}]" if n > 1 else ""
            parts.append(f"  {aname}: {verb} — {reason}{repeat}")
        parts.append("")

    # --- Current turn's failures ---
    parts.append("FAILED ACTIONS THIS TURN:")
    for failure in failed_actions:
        actor = failure["actor"]
        action = failure["action"]
        name = actor.get("name", actor.get("id", "?"))
        loc = actor.get("location", "?")
        verb = action.get("verb", "?")
        args = action.get("args", [])
        target = action.get("target", "")
        item = action.get("item", "")
        nouns = " ".join(args) if args else f"{target} {item}".strip()

        line = f"- {name} ({actor.get('id')}) @ {loc}: {verb} {nouns}"

        # Context about why it failed
        all_refs = args or [x for x in [target, item] if x]
        for ref in all_refs:
            if ref and ref not in engine.state.entities and ref not in engine.state.item_templates:
                line += f" ['{ref}' does not exist anywhere]"
            elif ref and ref in engine.state.item_templates and ref not in actor.get("inventory", []):
                line += f" ['{ref}' exists but not in their inventory]"
        if not all_refs:
            line += f" [no rule matches verb '{verb}']"

        # Actor's full context: stats, tags, role, inventory
        stats = actor.get("stats", {})
        inv = actor.get("inventory", [])
        tags = [t for t in actor.get("tags", []) if t not in ("alive", "mobile")]
        line += f"\n  HP={stats.get('hp','?')} hunger={stats.get('hunger','?')} thirst={stats.get('thirst','?')}"
        line += f"\n  tags: {', '.join(tags[:8])}"
        if inv:
            line += f"\n  inv: {', '.join(inv[:6])}"
        pers = actor.get("personality", {})
        if pers.get("drives"):
            from .drives import format_drives_for_gm
            drive_lines = format_drives_for_gm(actor, turn=state.turn, max_per_altitude=2)
            if drive_lines:
                line += "\n" + "\n".join(drive_lines)
        knowledge = pers.get("knowledge", [])
        if knowledge:
            line += f"\n  knows: {', '.join(knowledge[:2])}"
        private_log = actor.get("private_log", [])
        if private_log:
            last = private_log[-1]
            line += f"\n  last thought: {last.get('text', '')[:120]}"
        parts.append(line)

    parts.append("")
    parts.append("Resolve each failed action. Patch + teach if sensible. Pass if not.")
    parts.append("When [no rule matches verb X] for a real-world activity: create_rule is your most valuable response. Patch the effects too.")
    return "\n".join(parts)


def _safe_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def parse_gm_actions(raw: str, max_actions: int = 3) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if not tokens:
            continue
        verb = tokens[0].lower()
        # Strip leading punctuation/numbering the LLM might add (e.g. "1.", "-", "*")
        if verb in ("-", "*", "•") or (verb.endswith(".") and verb[:-1].isdigit()):
            tokens = tokens[1:]
            if not tokens:
                continue
            verb = tokens[0].lower()
        if verb == "pass":
            continue
        try:
            if verb == "whisper" and len(tokens) >= 3:
                actions.append({"verb": "whisper", "entity_id": tokens[1], "text": " ".join(tokens[2:])})
            elif verb == "inject" and len(tokens) >= 3:
                actions.append({"verb": "inject", "entity_id": tokens[1], "text": " ".join(tokens[2:])})
            elif verb == "narrate" and len(tokens) >= 2:
                actions.append({"verb": "narrate", "text": " ".join(tokens[1:])})
            elif verb == "event" and len(tokens) >= 5:
                actions.append({"verb": "event", "map_id": tokens[1], "x": _safe_int(tokens[2]), "y": _safe_int(tokens[3]), "text": " ".join(tokens[4:])})
            elif verb == "mod_stat" and len(tokens) >= 4:
                actions.append({"verb": "mod_stat", "entity_id": tokens[1], "stat": tokens[2], "delta": _safe_int(tokens[3])})
            elif verb == "add_tag" and len(tokens) == 3:
                actions.append({"verb": "add_tag", "entity_id": tokens[1], "tag": tokens[2]})
            elif verb == "remove_tag" and len(tokens) == 3:
                actions.append({"verb": "remove_tag", "entity_id": tokens[1], "tag": tokens[2]})
            elif verb == "spawn" and len(tokens) >= 4:
                actions.append({"verb": "spawn", "template": tokens[1], "x": _safe_int(tokens[2]), "y": _safe_int(tokens[3]), "new_id": tokens[4] if len(tokens) > 4 else tokens[1]})
            elif verb == "advance" and len(tokens) >= 3:
                actions.append({"verb": "advance", "entity_id": tokens[1], "drive_idx": _safe_int(tokens[2])})
            elif verb == "give" and len(tokens) >= 3:
                actions.append({"verb": "give", "entity_id": tokens[1], "item": tokens[2], "count": _safe_int(tokens[3], 1) if len(tokens) > 3 else 1})
            elif verb == "plan" and len(tokens) >= 3:
                actions.append({"verb": "plan", "entity_id": tokens[1], "text": " ".join(tokens[2:])})
            elif verb == "rumor" and len(tokens) >= 3:
                actions.append({"verb": "rumor", "map_id": tokens[1], "text": " ".join(tokens[2:])})
            elif verb == "describe" and len(tokens) >= 3:
                actions.append({"verb": "describe", "entity_id": tokens[1], "text": " ".join(tokens[2:])})
            elif verb == "create_character" and len(tokens) >= 5:
                actions.append({"verb": "create_character", "location": tokens[1], "x": _safe_int(tokens[2]), "y": _safe_int(tokens[3]), "sketch": " ".join(tokens[4:])})
            elif verb == "create_map" and len(tokens) >= 2:
                sketch = tokens[1] if len(tokens) < 3 else " ".join(tokens[1:-3] if len(tokens) > 4 else tokens[1:])
                connect_to = tokens[-3] if len(tokens) > 4 else None
                cx = _safe_int(tokens[-2]) if len(tokens) > 4 else None
                cy = _safe_int(tokens[-1]) if len(tokens) > 4 else None
                actions.append({"verb": "create_map", "sketch": sketch, "connect_to": connect_to, "connect_pos": [cx, cy] if cx is not None else None})
            elif verb == "create_rule" and len(tokens) >= 2:
                actions.append({"verb": "create_rule", "sketch": " ".join(tokens[1:])})
            elif verb == "rule" and len(tokens) >= 2:
                actions.append({"verb": "rule", "rule_json": " ".join(tokens[1:])})
            elif verb == "add_affordance" and len(tokens) >= 3:
                actions.append({"verb": "add_affordance", "entity_id": tokens[1], "args": tokens[2:]})
        except Exception:
            # GM output is messy — skip unparseable lines rather than crashing
            continue
        if len(actions) >= max_actions:
            break
    return actions


def _worldbuilder_llm(config: RuntimeConfig) -> WorldbuilderLLM | None:
    """Get the worldbuilder LLM callable, falling back to llm_adapter if not set."""
    if config.worldbuilder_llm:
        return config.worldbuilder_llm
    try:
        from .llm_adapter import llm_chat_completion
        return llm_chat_completion
    except ImportError:
        return None


def _resolve_entity_id(engine: GameEngine, eid: str) -> str:
    """Resolve GM-supplied entity reference to a real entity ID.
    GMs sometimes use display names ('Crust') instead of IDs ('baker').
    """
    if eid in engine.state.entities:
        return eid
    lower = eid.lower()
    if lower in engine.state.entities:
        return lower
    for real_id, ent in engine.state.entities.items():
        if ent.get("name", "").lower() == lower:
            return real_id
    return eid  # return as-is; will raise KeyError with a clear message


def apply_gm_action(engine: GameEngine, action: dict[str, Any], config: RuntimeConfig) -> str:
    verb = action["verb"]
    # Normalize entity_id: GMs often use display names instead of IDs
    if "entity_id" in action:
        action = {**action, "entity_id": _resolve_entity_id(engine, action["entity_id"])}
    if verb == "whisper":
        entity = engine.get_entity(action["entity_id"])
        entity.setdefault("gm_whispers", []).append({"text": action["text"], "turn": engine.state.turn})
        return f"whisper->{entity['id']}"
    if verb == "inject":
        # inject goes into private log as a memory/thought
        entity = engine.get_entity(action["entity_id"])
        engine.log_private(action["entity_id"], action["text"], "memory")
        return f"inject->{entity['id']}"
    if verb == "narrate":
        engine.log_event(f"NARRATOR: {action['text']}", None, engine.state.current_map_id, source="gm")
        return "narrate"
    if verb == "advance":
        from .drives import promote_drive
        entity = engine.get_entity(action["entity_id"])
        res = promote_drive(entity, int(action.get("drive_idx", 0)), engine.state.turn)
        if res.get("ok"):
            status = res.get("status", "?")
            if status == "met":
                lifted = res.get("lifted", "")
                tail = f" → lifted: {lifted}" if lifted else ""
                return f"advance->{entity['id']}#{action['drive_idx']} met{tail}"
            return f"advance->{entity['id']}#{action['drive_idx']} phase {res.get('new_phase')}"
        return f"advance_failed: {res.get('reason','?')}"
    if verb == "event":
        if action["map_id"] not in engine.state.maps:
            return "event_skipped_unknown_map"
        old_map = engine.state.current_map_id
        engine.state.current_map_id = action["map_id"]
        engine.log_event(action["text"], [int(action["x"]), int(action["y"])], action["map_id"], source="gm")
        engine.state.current_map_id = old_map
        return "event"
    if verb == "mod_stat":
        entity = engine.get_entity(action["entity_id"])
        delta = max(-config.gm_max_stat_delta, min(config.gm_max_stat_delta, int(action["delta"])))
        stats = entity.setdefault("stats", {})
        stats[action["stat"]] = int(stats.get(action["stat"], 0)) + delta
        return f"mod_stat->{entity['id']}.{action['stat']}={delta:+d}"
    if verb == "add_tag":
        entity = engine.get_entity(action["entity_id"])
        tags = entity.setdefault("tags", [])
        if action["tag"] not in tags:
            tags.append(action["tag"])
        return f"add_tag->{entity['id']}.{action['tag']}"
    if verb == "remove_tag":
        entity = engine.get_entity(action["entity_id"])
        tags = entity.setdefault("tags", [])
        if action["tag"] in tags:
            tags.remove(action["tag"])
        return f"remove_tag->{entity['id']}.{action['tag']}"
    if verb == "spawn":
        template_id = action.get("template")
        if template_id not in engine.state.item_templates:
            return f"spawn_failed: unknown template {template_id}"
        template = engine.state.item_templates[template_id]
        new_id = action.get("new_id") or f"{template_id}_{engine.state.turn}"
        new_entity = {
            "id": new_id,
            "name": template.get("name", new_id),
            "glyph": template.get("glyph", "?"),
            "tags": list(template.get("tags", [])),
            "stats": dict(template.get("stats", {})),
            "inventory": list(template.get("inventory", [])),
            "equipped": dict(template.get("equipped", {})),
            "personality": template.get("personality"),
            "pos": [int(action.get("x", 0)), int(action.get("y", 0))],
            "location": engine.state.current_map_id,
            "fov_radius": template.get("fov_radius", 6),
            "seen_events": [],
            "private_log": [],
        }
        engine.state.entities[new_id] = new_entity
        return f"spawn->{new_id}"
    if verb == "give":
        entity = engine.get_entity(action["entity_id"])
        item_id = action["item"]
        count = action.get("count", 1)
        inventory = entity.setdefault("inventory", [])
        for _ in range(count):
            inventory.append(item_id)
        return f"give->{entity['id']}+{count}x{item_id}"
    if verb == "create_character":
        loc = action["location"]
        # Validate map exists — GM sometimes sends bad map IDs
        if loc not in engine.state.maps:
            # Try fuzzy match (e.g. "crossroads_map" → "crossroads")
            for valid_map in engine.state.maps:
                if valid_map in loc or loc in valid_map:
                    loc = valid_map
                    break
            else:
                return f"create_character_failed: unknown map '{action['location']}'"
        llm = _worldbuilder_llm(config)
        if not llm:
            return "create_character_failed: no worldbuilder LLM"
        result = create_character(
            engine, action["sketch"], loc,
            [int(action["x"]), int(action["y"])],
            llm, config.worldbuilder_model,
        )
        if isinstance(result, str):
            return f"create_character_failed: {result}"
        return f"create_character->{result['id']}@{loc}"
    if verb == "create_map":
        llm = _worldbuilder_llm(config)
        if not llm:
            return "create_map_failed: no worldbuilder LLM"
        result = create_map(
            engine, action["sketch"], action.get("connect_to"),
            action.get("connect_pos"), llm, config.worldbuilder_model,
        )
        if isinstance(result, str):
            return f"create_map_failed: {result}"
        return f"create_map->{result.get('id', '?')}"
    if verb == "create_rule":
        # Guard: never create rules for physics verbs (move) or ill-defined social verbs
        # (give) — move is handled by the engine, give needs specific item semantics.
        _sketch_lower = action.get("sketch", "").lower()
        _blocked_verbs = {"move", "give", "hand", "hand over", "pass"}
        if any(bv in _sketch_lower.split()[:3] for bv in _blocked_verbs):
            return f"create_rule_skipped: verb blocked ({_sketch_lower[:40]})"
        llm = _worldbuilder_llm(config)
        if not llm:
            return "create_rule_failed: no worldbuilder LLM"
        result = create_rule(engine, action["sketch"], llm, config.worldbuilder_model)
        if isinstance(result, str):
            return f"create_rule_failed: {result}"
        return f"create_rule->{result.get('id', '?')}"
    if verb == "plan":
        entity = engine.get_entity(action["entity_id"])
        pers = entity.setdefault("personality", {})
        pers["plan"] = action["text"]
        return f"plan->{entity['id']}"
    if verb == "rumor":
        map_id = action.get("map_id")
        if map_id not in engine.state.maps:
            return "rumor_skipped_unknown_map"
        # rumor becomes a seen_event for everyone in that map
        for ent in engine.state.entities.values():
            if ent.get("location") == map_id:
                ent.setdefault("seen_events", []).append({
                    "turn": engine.state.turn, "text": f"Rumor: {action['text']}",
                    "pos": ent.get("pos"), "location": map_id,
                })
        return f"rumor->{map_id}"
    if verb == "describe":
        # GM enriches an entity's personality with a new detail
        entity = engine.get_entity(action["entity_id"])
        pers = entity.setdefault("personality", {})
        knowledge = pers.setdefault("knowledge", [])
        knowledge.append(action["text"])
        return f"describe->{entity['id']}"
    if verb == "rule":
        import json
        try:
            rule = json.loads(action["rule_json"])
            if "verb" not in rule or "id" not in rule:
                return "rule_failed: missing verb or id"
            engine.state.rules.append(rule)
            return f"rule->{rule['id']}"
        except json.JSONDecodeError:
            return "rule_failed: invalid json"
    if verb == "add_affordance":
        # GM adds a new affordance to an entity at runtime: add_affordance <entity_id> <verb> "desc"
        entity = engine.get_entity(action.get("entity_id", ""))
        if not entity:
            return f"add_affordance: unknown entity {action.get('entity_id')}"
        args = action.get("args", [])
        aff_verb = args[0] if args else ""
        aff_desc = " ".join(args[1:]) if len(args) > 1 else ""
        if not aff_verb:
            return "add_affordance: no verb specified"
        entity.setdefault("affordances", [])
        entity["affordances"].append({"verb": aff_verb, "desc": aff_desc})
        return f"add_affordance: {entity['id']} now offers {aff_verb}"
    return "noop"


# Token counting helper
def count_tokens_text(text: str, model: str = "gpt-4") -> int:
    try:
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


# ---------------------------------------------------------------------------
# Memory compaction — the way experience settles into a being without permission
# ---------------------------------------------------------------------------

from .prompts.memory_compaction import (
    MEMORY_COMPACTION_SYSTEM,
    MEMORY_COMPACTION_USER_TEMPLATE,
)


def compact_memory(
    engine: GameEngine,
    entity_id: str,
    llm_call: WorldbuilderLLM,
    model: str,
    keep_recent: int = 3,
) -> dict[str, Any]:
    """Compress an NPC's private_log into updated personality fields.

    The way experience settles into a being without permission.

    Args:
        engine: game engine
        entity_id: the being whose memory to compact
        llm_call: function(system, user, model, temp) -> str
        model: model ID for the compaction call
        keep_recent: how many recent private_log entries to preserve as continuity

    Returns: {success, changed_fields, trimmed_count, compaction_notes} or {success: False, message}
    """
    entity = engine.get_entity(entity_id)
    personality = entity.get("personality")
    if not personality:
        return {"success": False, "message": "No personality to compact"}

    private_log = entity.get("private_log", [])
    if len(private_log) <= keep_recent:
        return {"success": False, "message": "Not enough experience to compact"}

    # Build the experience log from private_log entries that will be consumed
    entries_to_compact = private_log[:-keep_recent] if keep_recent > 0 else private_log
    experience_lines = []
    for entry in entries_to_compact:
        etype = entry.get("type", "think")
        text = entry.get("text", "")
        turn = entry.get("turn", "?")
        if etype == "feel":
            experience_lines.append(f"[turn {turn}] Your body: {text}")
        elif etype == "notice":
            experience_lines.append(f"[turn {turn}] You noticed: {text}")
        elif etype == "think":
            experience_lines.append(f"[turn {turn}] You thought: {text}")
        elif etype in ("face", "emote"):
            experience_lines.append(f"[turn {turn}] Your face showed: {text}")
        elif etype == "say":
            experience_lines.append(f"[turn {turn}] You said: \"{text}\"")
        elif etype == "memory":
            experience_lines.append(f"[turn {turn}] A memory surfaced: {text}")
        else:
            experience_lines.append(f"[turn {turn}] {text}")

    if not experience_lines:
        return {"success": False, "message": "No experiences to compact"}

    # Gather context for the compaction template
    personality_json = json.dumps(personality, indent=2)
    bonds = entity.get("bonds", {})
    bonds_str = json.dumps(bonds, indent=2) if bonds else "(none)"
    relations = entity.get("relations", {})
    relations_str = json.dumps(relations, indent=2) if relations else "(none)"

    # Recent world events visible to this being
    loc = entity.get("location", engine.state.current_map_id)
    seen_events = entity.get("seen_events", [])
    recent_events_lines = []
    for ev in seen_events[-10:]:
        recent_events_lines.append(f"[turn {ev.get('turn', '?')}] {ev.get('text', '')}")
    recent_events_str = "\n".join(recent_events_lines) if recent_events_lines else "(nothing notable)"

    user_prompt = MEMORY_COMPACTION_USER_TEMPLATE.format(
        name=f"{entity.get('name', entity_id)} ({entity_id})",
        current_personality=personality_json,
        current_bonds=bonds_str,
        current_relations=relations_str,
        private_log="\n".join(experience_lines),
        recent_events=recent_events_str,
    )

    # Call LLM
    raw = llm_call(MEMORY_COMPACTION_SYSTEM, user_prompt, model, 1.2)
    if not raw or raw.strip() == "wait":
        return {"success": False, "message": "LLM returned empty response"}

    # Parse JSON from response
    import re as _re
    raw = raw.strip()
    updates = None
    if raw.startswith("{"):
        try:
            updates = json.loads(raw)
        except json.JSONDecodeError:
            pass
    if updates is None:
        match = _re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
        if match:
            try:
                updates = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    if updates is None:
        return {"success": False, "message": f"Could not parse compaction response: {raw[:200]}"}
    if not isinstance(updates, dict):
        return {"success": False, "message": "Compaction response was not a JSON object"}

    # Apply updates — personality fields that the compaction can touch
    ALLOWED_PERSONALITY = {
        "identity_anchor", "body", "wound", "contradictions", "drives",
        "bonds", "knowledge", "plan", "fears", "notices_first", "inner_voice",
        "comfort", "speech", "traits", "goals", "memory",
    }
    FORBIDDEN = {"id", "name", "glyph", "tags", "stats", "inventory",
                 "equipped", "pos", "location", "fov_radius"}

    changed_fields = []
    compaction_notes = updates.pop("compaction_notes", None)

    # Relations go on the entity, not in personality
    if "relations" in updates:
        rel_updates = updates.pop("relations")
        if isinstance(rel_updates, dict):
            entity.setdefault("relations", {}).update(rel_updates)
            changed_fields.append("relations")

    for field, value in updates.items():
        if field in FORBIDDEN:
            continue
        if field not in ALLOWED_PERSONALITY:
            continue
        personality[field] = value
        changed_fields.append(field)

    # Store compaction_notes as a private log entry (the residue)
    if compaction_notes:
        engine.log_private(entity_id, str(compaction_notes), "compaction")

    # Trim the compacted entries from private_log, keep recent ones
    trimmed_count = len(entries_to_compact)
    if keep_recent > 0:
        entity["private_log"] = private_log[-keep_recent:]
    else:
        entity["private_log"] = []

    return {
        "success": True,
        "changed_fields": changed_fields,
        "trimmed_count": trimmed_count,
        "compaction_notes": compaction_notes,
    }


def tick_needs(engine: GameEngine) -> list[str]:
    """Increment hunger/thirst for all living entities. Add/remove threshold tags.

    Returns audit entries for any state changes.
    """
    audit: list[str] = []
    HUNGER_TAGS = [(70, "hungry"), (90, "starving")]
    THIRST_TAGS = [(80, "parched"), (95, "dehydrated")]

    for entity in engine.state.entities.values():
        tags = entity.get("tags", [])
        if "alive" not in tags:
            continue
        stats = entity.get("stats", {})
        if "hunger" not in stats and "thirst" not in stats:
            continue

        # increment needs gently
        if "hunger" in stats:
            stats["hunger"] = min(100, int(stats["hunger"]) + 1)
        if "thirst" in stats:
            stats["thirst"] = min(100, int(stats["thirst"]) + 1)

        # apply threshold tags
        hunger = int(stats.get("hunger", 0))
        thirst = int(stats.get("thirst", 0))

        for threshold, tag in HUNGER_TAGS:
            if hunger >= threshold and tag not in tags:
                tags.append(tag)
                audit.append(f"needs:{entity['id']}+{tag}")
            elif hunger < threshold and tag in tags:
                tags.remove(tag)

        for threshold, tag in THIRST_TAGS:
            if thirst >= threshold and tag not in tags:
                tags.append(tag)
                audit.append(f"needs:{entity['id']}+{tag}")
            elif thirst < threshold and tag in tags:
                tags.remove(tag)

        # starvation / dehydration damage: -1 HP every 10 turns
        if ("starving" in tags or "dehydrated" in tags) and engine.state.turn % 10 == 0:
            hp = int(stats.get("hp", 0))
            if hp > 0:
                stats["hp"] = hp - 1
                audit.append(f"needs:{entity['id']} takes 1 damage from deprivation (HP:{hp - 1})")

    return audit


def run_round(
    engine: GameEngine,
    player_id: str,
    player_action_provider: PlayerActionProvider,
    npc_decider: NPCDecider | None,
    gm_decider: GMDecider | None,
    config: RuntimeConfig,
    step_callback: Callable[[dict[str, Any]], None] | None = None,
    weaver_decider: WeaverDecider | None = None,
) -> list[str]:
    player = engine.get_entity(player_id)
    location = player.get("location", engine.state.current_map_id)
    # All living mobile beings across ALL maps get to act
    actors = [
        entity
        for entity in engine.state.entities.values()
        if "alive" in entity.get("tags", [])
        and "mobile" in entity.get("tags", [])
    ]
    actors.sort(key=lambda entity: int(entity.get("stats", {}).get("spd", 0)), reverse=True)

    audit: list[str] = []
    failed_actions: list[dict[str, Any]] = []  # collect for GM resolver

    for actor in actors:
        actor_id = actor["id"]
        if actor_id == player_id:
            action = player_action_provider(actor, engine)
            audit_item = f"player:{action}"
            audit.append(audit_item)
            succeeded = engine.act(actor_id, action, increment_turn=False)
            if not succeeded and action.get("verb") != "wait":
                failed_actions.append({"actor": actor, "action": action})
            if step_callback:
                step_callback({"kind": "actor", "actor_id": actor_id, "action": action, "audit": audit_item})
            continue

        if npc_decider and npc_should_use_llm(engine, actor, player, config.llm_activation_radius):
            prompt = engine.build_prompt(actor_id)
            raw = npc_decider(actor, prompt)
            parsed = parse_npc_action(raw)
            action = parsed.get("action", {"verb": "wait"})

            # Log all output channels to private log
            if parsed.get("feel"):
                engine.log_private(actor_id, parsed["feel"], "feel")
            if parsed.get("notice"):
                engine.log_private(actor_id, parsed["notice"], "notice")
            if parsed.get("think"):
                engine.log_private(actor_id, parsed["think"], "think")
            if parsed.get("face"):
                # face is visible to nearby — log as public event
                engine.log_event(f"{actor['name']}: {parsed['face']}", actor.get("pos"), actor.get("location"))
                engine.log_private(actor_id, parsed["face"], "face")
            if parsed.get("speak"):
                # speech is public — visible to anyone in FOV
                engine.log_event(f"{actor['name']} says: \"{parsed['speak']}\"", actor.get("pos"), actor.get("location"))
                engine.log_private(actor_id, parsed["speak"], "say")

            # Parse and apply relation updates
            if parsed.get("relation"):
                rel = parse_relation_update(parsed["relation"])
                if rel:
                    entity_id, stance = rel
                    actor.setdefault("relations", {})[entity_id] = stance

            audit_item = f"npc_llm:{actor_id}:{action}"
            audit.append(audit_item)

            # Execute — collect failures for GM resolver
            succeeded = engine.act(actor_id, action, increment_turn=False)
            if not succeeded and action.get("verb") != "wait":
                failed_actions.append({"actor": actor, "action": action})
                audit.append(f"npc_failed:{actor_id}:{action}")

            # Log what was actually done (or failed) to private_log so the being
            # can see its own action history on subsequent turns.
            verb = action.get("verb", "wait")
            if verb != "wait":
                args = action.get("args", [])
                target = action.get("target") or action.get("direction", "")
                desc = " ".join(filter(None, [verb] + ([target] if target else args)))
                if succeeded:
                    engine.log_private(actor_id, desc, "action")
                else:
                    engine.log_private(actor_id, f"{desc} [failed]", "action")

            # Log public actions to shared log (only if they actually succeeded)
            if succeeded and verb not in ("wait",):
                action_desc = action.get("target", " ".join(action.get("args", [])))
                engine.log_event(f"{actor['name']} {verb} {action_desc}", actor.get("pos"), actor.get("location"))

            # Check if NPC should compact memory (periodic or overflow)
            private_log = actor.get("private_log", [])
            log_tokens = sum(count_tokens_text(e.get("text", "")) for e in private_log) if private_log else 0
            should_compact = (
                len(private_log) > 5
                and (
                    (config.npc_self_update_interval > 0 and engine.state.turn % config.npc_self_update_interval == 0)
                    or log_tokens > config.npc_self_update_token_limit
                )
            )
            if should_compact:
                llm = _worldbuilder_llm(config)
                if llm:
                    result = compact_memory(engine, actor_id, llm, config.worldbuilder_model)
                    if result.get("success"):
                        audit.append(f"npc_compact:{actor_id}:trimmed={result['trimmed_count']}:changed={','.join(result['changed_fields'])}")
                    else:
                        audit.append(f"npc_compact_failed:{actor_id}:{result.get('message', '?')}")
                else:
                    audit.append(f"npc_compact_skipped:{actor_id}:no_llm")
        else:
            action = simple_ai_action(engine, actor, player)
            audit_item = f"npc_ai:{actor_id}:{action}"
            audit.append(audit_item)
            succeeded = engine.act(actor_id, action, increment_turn=False)
            if not succeeded and action.get("verb") != "wait":
                failed_actions.append({"actor": actor, "action": action})
        if step_callback:
            step_callback({"kind": "actor", "actor_id": actor_id, "action": action, "audit": audit_item})

    # --- GM turn ---
    if config.gm_enabled and gm_decider:
        gm_prompt = build_gm_prompt(engine, max_events=config.gm_event_log_limit)
        raw_gm = gm_decider(gm_prompt)
        gm_actions = parse_gm_actions(raw_gm, max_actions=config.gm_max_actions)
        for gm_action in gm_actions:
            result = apply_gm_action(engine, gm_action, config)
            audit_item = f"gm:{result}"
            audit.append(audit_item)
            if step_callback:
                step_callback({"kind": "gm", "action": gm_action, "audit": audit_item})

    # --- Persist failure log (last 10 turns, for resolver context) ---
    if failed_actions:
        failure_log = engine.state.flags.setdefault("failure_log", [])
        for failure in failed_actions:
            actor = failure["actor"]
            action = failure["action"]
            verb = action.get("verb", "?")
            args = action.get("args", [])
            target = action.get("target", "")
            all_refs = args or [x for x in [target] if x]
            # Determine short reason
            if not all_refs:
                reason_short = f"no rule for '{verb}'"
            else:
                ref = all_refs[0]
                if ref not in engine.state.entities and ref not in engine.state.item_templates:
                    reason_short = f"'{ref}' unknown"
                else:
                    reason_short = f"'{ref}' not in inventory"
            failure_log.append({
                "turn": engine.state.turn,
                "actor_name": actor.get("name", actor.get("id", "?")),
                "actor_id": actor.get("id", "?"),
                "verb": verb,
                "nouns": " ".join(args) if args else target,
                "reason_short": reason_short,
            })
        # Trim to last 10 turns
        cutoff = engine.state.turn - 10
        engine.state.flags["failure_log"] = [f for f in failure_log if f.get("turn", 0) > cutoff]

    # --- GM resolver: handle failed actions ---
    if failed_actions and gm_decider:
        # Before resolving, expand any unknown items referenced in failures
        llm = _worldbuilder_llm(config)
        if llm:
            unknown_refs: list[str] = []
            for failure in failed_actions:
                action = failure["action"]
                for ref in action.get("args", []):
                    if ref and ref not in engine.state.entities and ref not in engine.state.item_templates:
                        unknown_refs.append(ref)
            if unknown_refs:
                # Deduplicate
                unknown_refs = list(dict.fromkeys(unknown_refs))
                # Build context from the actors who tried to use these items
                actor_sketch = "; ".join(
                    f"{f['actor'].get('name', '?')} at {f['actor'].get('location', '?')}"
                    for f in failed_actions[:3]
                )
                actor_loc = failed_actions[0]["actor"].get("location", "")
                expand_result = expand_unknown_items(
                    engine, unknown_refs, actor_sketch,
                    llm, config.worldbuilder_model, actor_loc,
                )
                if expand_result.get("items_created"):
                    audit.append(f"item_expand:{','.join(expand_result['items_created'])}")
                if expand_result.get("rules_created"):
                    audit.append(f"rule_expand:{','.join(expand_result['rules_created'])}")

        resolver_prompt = build_gm_resolver_prompt(engine, failed_actions)
        raw_resolve = gm_decider(resolver_prompt)
        resolve_actions = parse_gm_actions(raw_resolve, max_actions=len(failed_actions) + 2)
        for ra in resolve_actions:
            result = apply_gm_action(engine, ra, config)
            audit_item = f"gm_resolve:{result}"
            audit.append(audit_item)
            if step_callback:
                step_callback({"kind": "gm_resolve", "action": ra, "audit": audit_item})

    # --- Weaver (GM_ANTERIOR) turn — runs every weaver_interval turns ---
    if config.weaver_enabled and weaver_decider and engine.state.turn % config.weaver_interval == 0:
        weaver_prompt = build_weaver_prompt(engine, max_history=config.weaver_history)
        raw_weaver = weaver_decider(weaver_prompt)
        weaver_actions = parse_weaver_output(raw_weaver)
        weaver_results = apply_weaver_output(engine, weaver_actions)
        for wr in weaver_results:
            audit.append(wr)
        if step_callback:
            step_callback({"kind": "weaver", "actions": weaver_actions, "audit": weaver_results})

    # --- tick statuses ---
    for entity in engine.state.entities.values():
        if entity.get("statuses"):
            engine.tick_statuses(entity["id"])

    # --- tick needs (hunger/thirst) ---
    needs_audit = tick_needs(engine)
    audit.extend(needs_audit)

    # --- advance turn once for the whole round ---
    engine.state.turn += 1

    if step_callback:
        step_callback({"kind": "round_end", "audit": "round_end"})
    return audit
