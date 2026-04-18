from __future__ import annotations

import ast
import copy
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .loader import load_bundle
from .metalang import validate_bundle

DIRECTIONS = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
ANSI_RESET = "\x1b[0m"
ANSI_COLORS = {
    "player": "\x1b[97m",
    "hostile": "\x1b[91m",
    "merchant": "\x1b[93m",
    "door": "\x1b[96m",
    "entity": "\x1b[92m",
    "wall": "\x1b[90m",
    "floor": "\x1b[37m",
}
INTERPOLATION_RE = re.compile(r"{([^{}]+)}")
ALLOWED_FUNCTIONS = {"max": max, "min": min, "abs": abs, "round": round, "int": int, "float": float}


def manhattan(a: list[int], b: list[int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class EvalDict(dict):
    def __getattr__(self, key: str) -> Any:
        if key in self:
            return self[key]
        raise AttributeError(key)


def _as_eval_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return EvalDict({k: _as_eval_obj(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_as_eval_obj(v) for v in value]
    return value


def safe_eval(expression: str, context: dict[str, Any]) -> Any:
    parsed = ast.parse(expression, mode="eval")
    compiled = compile(parsed, "<expr>", "eval")
    env = {k: _as_eval_obj(v) for k, v in context.items()}
    env.update(ALLOWED_FUNCTIONS)
    return eval(compiled, {"__builtins__": {}}, env)  # noqa: S307


@dataclass
class GameState:
    turn: int
    maps: dict[str, dict[str, Any]]
    current_map_id: str
    entities: dict[str, dict[str, Any]]
    item_templates: dict[str, dict[str, Any]]
    rules: list[dict[str, Any]]
    statuses: dict[str, dict[str, Any]]
    event_log: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)


class GameEngine:
    MAX_TRIGGER_DEPTH = 8

    def __init__(self, state: GameState, rng_seed: int = 7) -> None:
        self.state = state
        self.rng = random.Random(rng_seed)
        self.effect_handlers: dict[str, Callable[[dict[str, Any], dict[str, Any], int], None]] = {
            "damage": self._effect_damage,
            "heal": self._effect_heal,
            "move": self._effect_move,
            "add_tag": self._effect_add_tag,
            "remove_tag": self._effect_remove_tag,
            "remove_item": self._effect_remove_item,
            "remove_status": self._effect_remove_status,
            "message": self._effect_message,
            "trigger": self._effect_trigger,
            "open_view": self._effect_open_view,
            "transfer_item": self._effect_transfer_item,
            "mod_stat": self._effect_mod_stat,
            "portal": self._effect_portal,
            "door_bump": self._effect_door_bump,
        }

    @classmethod
    def from_world_file(cls, world_file: str) -> "GameEngine":
        world, maps_data, entities_data, rules_data, statuses_data = load_bundle(world_file)
        validate_bundle(maps_data, entities_data, rules_data, statuses_data)
        current_map_id = str(world.get("start_map") or next(iter(maps_data.keys())))
        entities: dict[str, dict[str, Any]] = {}
        for raw in entities_data["instances"]:
            e = copy.deepcopy(raw)
            e.setdefault("inventory", [])
            e.setdefault("equipped", {})
            e.setdefault("statuses", [])
            e.setdefault("seen_events", [])
            e.setdefault("private_log", [])  # think/emote lines visible only to self and GM
            e.setdefault("relations", {})  # dynamic stance toward other entities
            e.setdefault("bonds", {})  # initial/backstory relationships
            e.setdefault("fov_radius", 6)
            e.setdefault("location", current_map_id)
            entities[e["id"]] = e
        state = GameState(
            turn=int(world.get("turn", 1)),
            maps=maps_data,
            current_map_id=current_map_id,
            entities=entities,
            item_templates=entities_data["templates"],
            rules=sorted(rules_data, key=lambda r: r.get("priority", 0), reverse=True),
            statuses=statuses_data,
            flags={"location": current_map_id, "gm_notes": world.get("gm_notes", "")},
        )
        return cls(state=state, rng_seed=int(world.get("rng_seed", 7)))

    @property
    def map_data(self) -> dict[str, Any]:
        return self.state.maps[self.state.current_map_id]

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self.state.entities[entity_id]

    def get_item_template(self, item_id: str) -> dict[str, Any]:
        return self.state.item_templates[item_id]

    def _ctx(self, context: dict[str, Any]) -> dict[str, Any]:
        actor = context.get("actor")
        return {
            "actor": actor,
            "target": context.get("target"),
            "source": context.get("source"),
            "item": context.get("item"),
            "action": context.get("action", {}),
            "status": context.get("status"),
            "bearer": context.get("bearer"),
            "actor_equipped": self._equipped_tags(actor) if isinstance(actor, dict) else set(),
            "result": context.get("result", {}),
            "state": {"turn": self.state.turn, "flags": self.state.flags},
        }

    def _resolve(self, value: Any, context: dict[str, Any]) -> Any:
        if not isinstance(value, str):
            return value
        if any(token in value for token in (".", "(", ")", "+", "-", "*", "/", "<", ">", "=", "[", "]")):
            try:
                return safe_eval(value, self._ctx(context))
            except Exception:
                return value
        return value

    def _interpolate(self, text: str, context: dict[str, Any]) -> str:
        def repl(match: re.Match[str]) -> str:
            try:
                return str(safe_eval(match.group(1).strip(), self._ctx(context)))
            except Exception:
                return f"<{match.group(1)}>"

        return INTERPOLATION_RE.sub(repl, text)

    def _entity_ref(self, ref: str, context: dict[str, Any], default_ref: str) -> dict[str, Any]:
        key = ref or default_ref
        if key in context and isinstance(context[key], dict):
            return context[key]
        return self.get_entity(key)

    def _tags_match(self, owned: set[str], required: list[str]) -> bool:
        for raw in required:
            opts = [p.strip() for p in raw.split("||")]
            if not any(opt in owned for opt in opts):
                return False
        return True

    def _equipped_tags(self, entity: dict[str, Any]) -> set[str]:
        tags = set(entity.get("equipped", {}).keys())
        for item_id in entity.get("equipped", {}).values():
            tags.update(self.state.item_templates.get(item_id, {}).get("tags", []))
        return tags

    def _distance(self, a: dict[str, Any], b: dict[str, Any]) -> int:
        if a.get("location") != b.get("location"):
            return 10000
        return manhattan(a["pos"], b["pos"])

    def _resolve_entity_ref(self, ref: str, actor_id: str) -> str:
        """Resolve entity reference (ID or name) to entity ID. Fuzzy match on name if exact ID fails."""
        # Try exact ID match first
        if ref in self.state.entities:
            return ref
        # Try exact item template ID
        if ref in self.state.item_templates:
            return ref
        # Fuzzy match items in inventory
        actor = self.get_entity(actor_id)
        ref_lower = ref.lower().replace("_", " ")
        for item_id in actor.get("inventory", []):
            item = self.state.item_templates.get(item_id, {})
            item_name = item.get("name", item_id).lower()
            if ref_lower in item_name or item_name in ref_lower or ref_lower == item_id:
                return item_id
        # Fuzzy match entities in same location
        candidates = []
        for ent_id, ent in self.state.entities.items():
            if ent.get("location") == actor.get("location"):
                name_lower = ent.get("name", "").lower()
                if ref_lower in name_lower or name_lower in ref_lower:
                    candidates.append((ent_id, self._distance(actor, ent)))
        if candidates:
            return min(candidates, key=lambda x: x[1])[0]
        return ref  # Fallback

    def _build_action_context(self, actor_id: str, action: dict[str, Any]) -> dict[str, Any]:
        actor = self.get_entity(actor_id)
        context: dict[str, Any] = {"actor": actor, "action": action, "result": {}}

        # --- Legacy format: explicit target/item/direction/source fields ---
        if "target" in action:
            resolved_target = self._resolve_entity_ref(action["target"], actor_id)
            if resolved_target in self.state.entities:
                context["target"] = self.get_entity(resolved_target)
            elif resolved_target in self.state.item_templates and resolved_target in actor.get("inventory", []):
                context["item_id"] = resolved_target
                context["item"] = copy.deepcopy(self.get_item_template(resolved_target))
        if "source" in action:
            resolved_source = self._resolve_entity_ref(action["source"], actor_id)
            if resolved_source in self.state.entities:
                context["source"] = self.get_entity(resolved_source)
        if "item" in action:
            resolved_item = self._resolve_entity_ref(action["item"], actor_id)
            context["item_id"] = resolved_item
            if resolved_item in self.state.item_templates:
                context["item"] = copy.deepcopy(self.get_item_template(resolved_item))
        if "direction" in action:
            dx, dy = DIRECTIONS[action["direction"]]
            target_pos = [actor["pos"][0] + dx, actor["pos"][1] + dy]
            context["target"] = {"id": f"tile:{target_pos[0]},{target_pos[1]}", "pos": target_pos, "tags": self._tile_tags(target_pos)}

        # --- Args format: unified noun resolution ---
        # The parser doesn't know which arg is a target vs item.
        # Resolve each arg against all pools, then populate slots.
        if "args" in action:
            resolved_args = []
            for arg in action["args"]:
                ref = self._resolve_entity_ref(arg, actor_id)
                is_entity = ref in self.state.entities
                is_item = ref in self.state.item_templates and ref in actor.get("inventory", [])
                resolved_args.append({"ref": ref, "is_entity": is_entity, "is_item": is_item})

            # Assign args to slots: first entity arg → target, first item arg → item
            for ra in resolved_args:
                if ra["is_entity"] and "target" not in context:
                    context["target"] = self.get_entity(ra["ref"])
                elif ra["is_item"] and "item" not in context:
                    context["item_id"] = ra["ref"]
                    context["item"] = copy.deepcopy(self.get_item_template(ra["ref"]))

            # If only one arg and it's an inventory item but NOT a map entity,
            # it already got assigned to item slot above. Good.
            # If only one arg and it's BOTH (entity on map AND in inventory),
            # it got assigned to target. Also set item so item-based rules can match.
            if len(resolved_args) == 1:
                ra = resolved_args[0]
                if ra["is_entity"] and ra["is_item"] and "item" not in context:
                    context["item_id"] = ra["ref"]
                    context["item"] = copy.deepcopy(self.get_item_template(ra["ref"]))

        return context

    def _tile_tags(self, pos: list[int]) -> list[str]:
        grid = self.map_data["grid"]
        if not (0 <= pos[1] < len(grid) and 0 <= pos[0] < len(grid[0])):
            return ["solid", "opaque"]
        glyph = grid[pos[1]][pos[0]]
        return list(self.map_data["legend"].get(glyph, {}).get("tags", []))

    def _rule_matches(self, rule: dict[str, Any], context: dict[str, Any]) -> bool:
        actor = context["actor"]
        if "actor_has" in rule and not self._tags_match(set(actor.get("tags", [])), rule["actor_has"]):
            return False
        if "actor_equipped" in rule and not self._tags_match(self._equipped_tags(actor), rule["actor_equipped"]):
            return False
        if "actor_status" in rule:
            statuses = {s["id"] if isinstance(s, dict) else s for s in actor.get("statuses", [])}
            if not self._tags_match(statuses, rule["actor_status"]):
                return False
        if "target_has" in rule:
            target = context.get("target")
            if not target or not self._tags_match(set(target.get("tags", [])), rule["target_has"]):
                return False
        if rule.get("target_near"):
            target = context.get("target")
            if not target or self._distance(actor, target) > 1:
                return False
        if "item_has" in rule:
            item = context.get("item")
            if not item or not self._tags_match(set(item.get("tags", [])), rule["item_has"]):
                return False
        if rule.get("condition"):
            try:
                if not bool(safe_eval(rule["condition"], self._ctx(context))):
                    return False
            except Exception:
                return False
        return True

    def _match_rule(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
        for rule in self.state.rules:
            if rule["verb"] == action["verb"] and self._rule_matches(rule, context):
                return rule
        return None

    def log_event(self, text: str, pos: list[int] | None, location: str | None = None, visible_to: str = "all", source: str = "world") -> None:
        """Log an event. visible_to: 'all' (everyone in FOV), 'self' (only actor), 'self+gm' (actor and GM).
        source: 'world' (being/rule action), 'gm' (GM intervention)."""
        ev = {"turn": self.state.turn, "text": text, "pos": pos, "location": location or self.state.current_map_id, "visible_to": visible_to, "source": source}
        self.state.event_log.append(ev)
        for e in self.state.entities.values():
            if e.get("location") == ev["location"] and pos and manhattan(e["pos"], pos) <= int(e.get("fov_radius", 0)):
                e.setdefault("seen_events", []).append(copy.deepcopy(ev))
                if len(e["seen_events"]) > 10:
                    del e["seen_events"][:-10]

    def act(self, actor_id: str, action: dict[str, Any], increment_turn: bool = True, chain_depth: int = 0) -> bool:
        context = self._build_action_context(actor_id, action)
        actor = context["actor"]
        self.state.current_map_id = actor.get("location", self.state.current_map_id)
        self.state.flags["location"] = self.state.current_map_id
        rule = self._match_rule(action, context)
        if not rule:
            self.log_event(f"{actor['name']} cannot perform '{action['verb']}' in this context.", actor.get("pos"), actor.get("location"))
            if increment_turn:
                self.state.turn += 1
            return False
        for effect in rule.get("effects", []):
            self.effect_handlers[effect["effect"]](effect, context, chain_depth)
        if increment_turn:
            self.state.turn += 1
        return True

    def _effect_damage(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        target = self._entity_ref(effect.get("target", "target"), context, "target")
        amount = max(0, int(round(float(self._resolve(effect.get("formula", effect.get("value", 0)), context)))))
        stats = target.setdefault("stats", {})
        stats["hp"] = max(0, int(stats.get("hp", 0)) - amount)
        context["result"]["damage"] = amount

    def _effect_heal(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        target = self._entity_ref(effect.get("target", "actor"), context, "actor")
        amount = max(0, int(round(float(self._resolve(effect.get("formula", effect.get("value", 0)), context)))))
        stats = target.setdefault("stats", {})
        current = int(stats.get("hp", 0))
        max_hp = int(stats.get("max_hp", current))
        healed = min(max_hp, current + amount)
        stats["hp"] = healed
        context["result"]["heal"] = healed - current

    def _effect_move(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        mover = self._entity_ref(effect.get("target", "actor"), context, "actor")
        to = self._resolve(effect.get("to", "target.pos"), context)
        mover["pos"] = [int(to[0]), int(to[1])]

    def _effect_add_tag(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        entity = self._entity_ref(effect.get("entity", "target"), context, "target")
        tag = str(self._resolve(effect["tag"], context))
        tags = entity.setdefault("tags", [])
        if tag not in tags:
            tags.append(tag)

    def _effect_remove_tag(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        entity = self._entity_ref(effect.get("entity", "target"), context, "target")
        tag = str(self._resolve(effect["tag"], context))
        tags = entity.setdefault("tags", [])
        if tag in tags:
            tags.remove(tag)

    def _effect_remove_item(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        owner = self._entity_ref(effect.get("entity", "actor"), context, "actor")
        item_id = str(self._resolve(effect.get("item", "item.id"), context))
        if item_id in owner.setdefault("inventory", []):
            owner["inventory"].remove(item_id)

    def _effect_remove_status(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        bearer = self._entity_ref(effect.get("entity", "actor"), context, "actor")
        status = str(self._resolve(effect["status"], context))
        if self.rng.random() <= float(effect.get("chance", 1.0)):
            bearer["statuses"] = [s for s in bearer.get("statuses", []) if (s["id"] if isinstance(s, dict) else s) != status]

    def _effect_message(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        actor = context.get("actor", {})
        self.log_event(self._interpolate(effect.get("text", ""), context), actor.get("pos"), actor.get("location"))

    def _effect_trigger(self, effect: dict[str, Any], context: dict[str, Any], depth: int) -> None:
        if depth >= self.MAX_TRIGGER_DEPTH:
            raise RuntimeError("maximum trigger depth reached")
        action: dict[str, Any] = {"verb": effect["verb"]}
        if "target" in context and isinstance(context["target"], dict) and "id" in context["target"]:
            action["target"] = context["target"]["id"]
        self.act(context["actor"]["id"], action, increment_turn=False, chain_depth=depth + 1)

    def _build_open_entries(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        entries = []
        for item_id in source.get("inventory", []):
            tpl = self.state.item_templates.get(item_id, {"name": item_id, "stats": {}})
            hints = source.get("take_consequence_hints", [])
            parts: list[str] = []
            for hint in hints:
                rendered = self._interpolate(
                    str(hint),
                    {"source": source, "item": tpl, "result": {}, "action": {}, "actor": None, "target": source},
                ).strip()
                if rendered:
                    parts.append(rendered)
            entries.append(
                {"source": source["id"], "item": item_id, "name": tpl.get("name", item_id), "consequence": ", ".join(parts) or "none", "command": f"take {source['id']} {item_id}"}
            )
        return entries

    def _effect_open_view(self, effect: dict[str, Any], context: dict[str, Any], __: int) -> None:
        actor, source = context["actor"], context["target"]
        mode = str(effect.get("mode", "open"))
        self.state.flags["open_view"] = {"mode": mode, "actor": actor["id"], "source": source["id"], "entries": self._build_open_entries(source)}
        if mode == "trade":
            self.state.flags["open_trade"] = {"actor": actor["id"], "target": source["id"], "turn": self.state.turn}

    def _effect_transfer_item(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        source = self._entity_ref(effect.get("source", "target"), context, "target")
        target = self._entity_ref(effect.get("target", "actor"), context, "actor")
        item_id = str(self._resolve(effect.get("item", "action.item"), context))
        if item_id in source.setdefault("inventory", []):
            source["inventory"].remove(item_id)
            target.setdefault("inventory", []).append(item_id)

    def _effect_mod_stat(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        entity = self._entity_ref(effect.get("entity", "actor"), context, "actor")
        stat = effect["stat"]
        delta = int(round(float(self._resolve(effect.get("value", 0), context))))
        entity.setdefault("stats", {})[stat] = int(entity.setdefault("stats", {}).get(stat, 0)) + delta

    def _effect_portal(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        traveler = self._entity_ref(effect.get("entity", "actor"), context, "actor")
        target_map = str(self._resolve(effect["map"], context))
        destination = self._resolve(effect["to"], context)
        traveler["location"] = target_map
        traveler["pos"] = [int(destination[0]), int(destination[1])]
        self.state.current_map_id = target_map
        self.state.flags["location"] = target_map

    def _effect_door_bump(self, effect: dict[str, Any], context: dict[str, Any], _: int) -> None:
        actor = context["actor"]
        door = self._entity_ref(effect.get("target", "target"), context, "target")
        tags = door.setdefault("tags", [])
        stats = door.setdefault("stats", {})

        if "locked" in tags:
            self.log_event(str(stats.get("locked_message", "The door is locked.")), actor.get("pos"), actor.get("location"))
            return

        just_opened = False
        if "closed" in tags:
            tags.remove("closed")
            if "open" not in tags:
                tags.append("open")
            if "solid" in tags:
                tags.remove("solid")
            just_opened = True
            self.log_event(str(stats.get("open_message", f"{actor['name']} opens {door['name']}.")), actor.get("pos"), actor.get("location"))

        if "portal" in tags and "portal_map" in stats and "portal_pos" in stats:
            actor["location"] = str(stats["portal_map"])
            actor["pos"] = [int(stats["portal_pos"][0]), int(stats["portal_pos"][1])]
            self.state.current_map_id = actor["location"]
            self.state.flags["location"] = actor["location"]
            portal_message = stats.get("portal_message", f"{actor['name']} steps through {door['name']}.")
            if just_opened:
                portal_message = stats.get("open_portal_message", portal_message)
            self.log_event(str(portal_message), actor.get("pos"), actor.get("location"))

    def tick_statuses(self, entity_id: str) -> None:
        bearer = self.get_entity(entity_id)
        next_statuses: list[dict[str, Any]] = []
        for inst in bearer.get("statuses", []):
            sid = inst["id"] if isinstance(inst, dict) else inst
            rem = int(inst.get("remaining", 1)) if isinstance(inst, dict) else 1
            status = self.state.statuses.get(sid, {})
            ctx = {"actor": bearer, "bearer": bearer, "status": status, "result": {}, "action": {"verb": "__status_tick__"}}
            for effect in status.get("on_turn", []):
                self.effect_handlers[effect["effect"]](effect, ctx, 0)
            rem -= 1
            if rem > 0:
                next_statuses.append({"id": sid, "remaining": rem})
        bearer["statuses"] = next_statuses

    def available_actions(self, actor_id: str) -> list[str]:
        actor = self.get_entity(actor_id)
        actions: set[str] = set()
        for rule in self.state.rules:
            verb = rule["verb"]
            if "actor_has" in rule and not self._tags_match(set(actor.get("tags", [])), rule["actor_has"]):
                continue
            if "actor_equipped" in rule and not self._tags_match(self._equipped_tags(actor), rule["actor_equipped"]):
                continue
            if "actor_status" in rule:
                statuses = {s["id"] if isinstance(s, dict) else s for s in actor.get("statuses", [])}
                if not self._tags_match(statuses, rule["actor_status"]):
                    continue
            if verb == "move":
                for d in DIRECTIONS:
                    if self._rule_matches(rule, self._build_action_context(actor_id, {"verb": "move", "direction": d})):
                        actions.add(f"move {d}")
                continue
            if "target_has" in rule:
                for t in self.state.entities.values():
                    if t["id"] == actor_id or t.get("location") != actor.get("location"):
                        continue
                    if self._rule_matches(rule, {"actor": actor, "target": t, "action": {"verb": verb, "target": t["id"]}, "result": {}}):
                        actions.add(f"{verb} {t['id']}")
                continue
            if "item_has" in rule:
                for item_id in actor.get("inventory", []):
                    item = self.state.item_templates.get(item_id)
                    if item and self._rule_matches(rule, {"actor": actor, "item": item, "action": {"verb": verb, "item": item_id}, "result": {}}):
                        actions.add(f"{verb} {item_id}")
                continue
            actions.add(verb)
        return sorted(actions)

    def _humanize_action(self, action_str: str, actor_id: str) -> str:
        """Convert 'verb entity_id' to natural language, keeping the raw string for parsing."""
        parts = action_str.split(" ", 1)
        verb = parts[0]
        if len(parts) == 1:
            return action_str  # 'wait', 'roll' etc.

        target_ref = parts[1]

        # Move — compass to words
        if verb == "move":
            DIR_NAMES = {"N": "north", "S": "south", "E": "east", "W": "west"}
            return f"move {DIR_NAMES.get(target_ref, target_ref)}  [{action_str}]"

        # Entity or item target — look up a readable name
        entity = self.state.entities.get(target_ref)
        item = self.state.item_templates.get(target_ref)

        if entity:
            name = entity.get("name", target_ref)
            # Add relative direction for non-person entities (patches, barrels, etc.)
            actor = self.get_entity(actor_id)
            if "alive" not in entity.get("tags", []) and actor.get("pos") and entity.get("pos"):
                dx = entity["pos"][0] - actor["pos"][0]
                dy = entity["pos"][1] - actor["pos"][1]
                dirs = []
                if dy < 0: dirs.append("north")
                elif dy > 0: dirs.append("south")
                if dx > 0: dirs.append("east")
                elif dx < 0: dirs.append("west")
                direction = "-".join(dirs) if dirs else "here"
                return f"{verb} {name} (to your {direction})  [{action_str}]"
            return f"{verb} {name}  [{action_str}]"

        if item:
            name = item.get("name", target_ref)
            return f"{verb} {name}  [{action_str}]"

        return f"{action_str}"

    def _entity_state(self, entity: dict[str, Any]) -> str:
        tags = set(entity.get("tags", []))
        out: list[str] = []
        if "door" in tags:
            for s in ("locked", "open", "closed"):
                if s in tags:
                    out.append(s)
                    break
        hp, max_hp = entity.get("stats", {}).get("hp"), entity.get("stats", {}).get("max_hp")
        if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp > 0 and hp < max_hp * 0.5:
            out.append("wounded")
        for s in entity.get("statuses", []):
            out.append(s["id"] if isinstance(s, dict) else str(s))
        return ", ".join(out)

    def _colorize(self, ch: str, color: str) -> str:
        return f"{color}{ch}{ANSI_RESET}"

    def _entity_color(self, entity: dict[str, Any], is_actor: bool) -> str:
        tags = set(entity.get("tags", []))
        if is_actor:
            return ANSI_COLORS["player"]
        if "merchant" in tags:
            return ANSI_COLORS["merchant"]
        if "hostile" in tags:
            return ANSI_COLORS["hostile"]
        if "door" in tags:
            return ANSI_COLORS["door"]
        return ANSI_COLORS["entity"]

    def log_private(self, entity_id: str, text: str, entry_type: str = "think") -> None:
        """Log a private entry (think/emote/action) visible only to self and GM.
        No hard cap — context compaction handles truncation when needed."""
        entity = self.get_entity(entity_id)
        ev = {"turn": self.state.turn, "text": text, "type": entry_type}
        entity.setdefault("private_log", []).append(ev)

    # --------------- time of day ---------------

    PERIODS = ["dawn", "morning", "midday", "afternoon", "dusk", "night"]

    def time_of_day(self) -> str:
        return self.PERIODS[(self.state.turn // 20) % len(self.PERIODS)]

    # --------------- sensory needs description ---------------

    @staticmethod
    def describe_needs(entity: dict[str, Any]) -> str:
        stats = entity.get("stats", {})
        hunger = int(stats.get("hunger", 0))
        thirst = int(stats.get("thirst", 0))
        hp = int(stats.get("hp", 0))
        max_hp = int(stats.get("max_hp", 1))
        parts: list[str] = []

        if hunger >= 95:
            parts.append("Your stomach has forgotten what food feels like. The weakness is in your hands now, in your thoughts.")
        elif hunger >= 80:
            parts.append("Your stomach is a tight knot. The smell of food is a sharp ache.")
        elif hunger >= 60:
            parts.append("A hollow feeling sits behind your ribs. You could eat.")

        if thirst >= 95:
            parts.append("Your tongue is thick and rough. Swallowing hurts. You need water now.")
        elif thirst >= 75:
            parts.append("Your mouth is dry and cracked. Every thought pulls toward water. Find some.")
        elif thirst >= 55:
            parts.append("A thirst is building at the back of your throat.")

        if hp <= max_hp * 0.25:
            parts.append("Pain pulses through you with each heartbeat. You are badly hurt.")
        elif hp <= max_hp * 0.5:
            parts.append("You are wounded. Movement costs you.")

        return " ".join(parts)

    # --------------- build prompt (phenomenological) ---------------

    def _describe_inventory(self, actor: dict[str, Any]) -> list[str]:
        inv = actor.get("inventory", [])
        if not inv:
            return ["You carry nothing."]
        counts: dict[str, int] = {}
        for item_id in inv:
            name = self.state.item_templates.get(item_id, {}).get("name", item_id)
            counts[name] = counts.get(name, 0) + 1
        items = []
        for name, count in counts.items():
            items.append(f"{name} x{count}" if count > 1 else name)
        return [f"You carry: {', '.join(items)}."]

    def _entity_interactions(self, entity: dict[str, Any]) -> list[str]:
        """Return what interactions this entity enables when adjacent.

        If the entity declares its own affordances, those take priority.
        Otherwise falls back to tag-based inference.
        """
        affordances = entity.get("affordances", [])
        if affordances:
            return [a["verb"] for a in affordances]
        # Tag-based fallback for entities without declared affordances
        tags = set(entity.get("tags", []))
        stats = entity.get("stats", {})
        hints: list[str] = []
        if "portal" in tags and "portal_map" in stats:
            dest_id = str(stats["portal_map"])
            dest_map = self.state.maps.get(dest_id, {})
            dest_name = dest_map.get("name", dest_id)
            hints.append(f"bump to enter {dest_name}")
        if "drinkable" in tags and "alive" not in tags:
            hints.append("drink")
        if "harvestable" in tags and "alive" not in tags:
            hints.append("harvest")
        if "workbench" in tags:
            hints.append("craft")
        if "bakeable" in tags or "bakers_oven" in tags:
            hints.append("bake")
        if "inventory_source" in tags and "alive" not in tags:
            hints.append("search")
        if "readable" in tags and "alive" not in tags:
            hints.append("read")
        if "climbable" in tags and "alive" not in tags:
            hints.append("climb")
        return hints

    def _describe_visible_entity(self, actor: dict[str, Any], entity: dict[str, Any], show_bond: bool = True) -> str:
        dist = manhattan(actor["pos"], entity["pos"])
        stats = entity.get("stats", {})
        parts = [f"{entity['name']}"]
        if dist == 0:
            parts.append("[here with you]")
        elif dist == 1:
            parts.append("[right beside you]")
        else:
            parts.append(f"[{dist} steps away]")
        # compact stats for visible beings
        if "hp" in stats and "max_hp" in stats:
            hp_pct = int(stats["hp"]) / max(1, int(stats["max_hp"]))
            if hp_pct < 0.25:
                parts.append("— badly wounded")
            elif hp_pct < 0.5:
                parts.append("— hurt")
        state = self._entity_state(entity)
        if state:
            parts.append(f"({state})")
        # weave in bond or relation — only on first encounter or re-encounter
        if show_bond:
            bond_text = self._get_bond_text(actor, entity["id"])
            if bond_text:
                parts.append(f". {bond_text}")
        # show what this entity enables (move adjacent to unlock if not yet beside)
        interactions = self._entity_interactions(entity)
        if interactions:
            affordances = entity.get("affordances", [])
            if dist <= 1:
                if affordances:
                    # Show rich descriptions for declared affordances
                    rich_parts = []
                    for a in affordances:
                        desc = a.get("desc", "")
                        rich_parts.append(f"{a['verb']}: {desc}" if desc else a["verb"])
                    parts.append(f"[can: {'; '.join(rich_parts)}]")
                else:
                    parts.append(f"[can: {'/'.join(interactions)}]")
            else:
                parts.append(f"[move adjacent to: {'/'.join(interactions)}]")
        return "- " + " ".join(parts)

    def _get_bond_text(self, actor: dict[str, Any], target_id: str) -> str:
        # check dynamic relations first (overrides bonds)
        relations = actor.get("relations", {})
        if target_id in relations:
            return relations[target_id]
        # check static bonds
        bonds = actor.get("bonds", {})
        if target_id in bonds:
            val = bonds[target_id]
            if isinstance(val, dict):
                return val.get("feeling", "")
            return str(val)
        return ""

    def build_prompt(self, actor_id: str) -> str:
        actor = self.get_entity(actor_id)
        loc = actor.get("location", self.state.current_map_id)
        # Guard against bad location — fall back to start_map
        if loc not in self.state.maps:
            loc = next(iter(self.state.maps), loc)
            actor["location"] = loc
        self.state.current_map_id = loc
        map_data = self.map_data
        map_name = map_data.get("name", loc)
        r = int(actor.get("fov_radius", 6))
        vis = [e for e in self.state.entities.values() if e["id"] != actor_id and e.get("location") == loc and manhattan(actor["pos"], e["pos"]) <= r]
        visible_positions = {(x, y) for y in range(len(map_data["grid"])) for x in range(len(map_data["grid"][0])) if manhattan(actor["pos"], [x, y]) <= r}

        # build map rows
        rows: list[str] = []
        for y, row in enumerate(map_data["grid"]):
            out: list[str] = []
            for x, base in enumerate(row):
                if (x, y) not in visible_positions:
                    out.append(" ")
                    continue
                if actor["pos"] == [x, y]:
                    out.append(self._colorize(actor["glyph"], self._entity_color(actor, True)))
                    continue
                entity_here = next((e for e in vis if e["pos"] == [x, y]), None)
                if entity_here:
                    out.append(self._colorize(entity_here["glyph"], self._entity_color(entity_here, False)))
                else:
                    tags = set(map_data["legend"].get(base, {}).get("tags", []))
                    out.append(self._colorize(base, ANSI_COLORS["wall"] if "solid" in tags else ANSI_COLORS["floor"]))
            rows.append("".join(out))

        parts: list[str] = []
        personality = actor.get("personality") or {}
        period = self.time_of_day()

        # --- Body state ---
        parts.append(f"You are in {map_name}. It is {period}.")
        needs_text = self.describe_needs(actor)
        if needs_text:
            parts.append(needs_text)
        parts.append(f"You have {actor['stats'].get('gold', 0)} gold.")
        parts.extend(self._describe_inventory(actor))
        parts.append("")

        # --- Plan and drives ---
        from .drives import format_active_drives_block
        drive_lines = format_active_drives_block(actor, self.state.turn)
        if drive_lines:
            parts.extend(drive_lines)
        knowledge = personality.get("knowledge", [])
        if knowledge:
            parts.append(f"You know: {'; '.join(knowledge)}")
        plan = personality.get("plan")
        if plan:
            parts.append(f"Your plan for today: {plan}")
            parts.append(f"It is {period}.")
        parts.append("")

        # --- Intuition (GM whispers) — persist 2 turns then expire ---
        whispers = actor.get("gm_whispers", [])
        current_turn = self.state.turn
        if whispers:
            # Whispers are stored as {"text": ..., "turn": N} or legacy plain strings
            active = []
            expired = []
            for w in whispers:
                if isinstance(w, dict):
                    if current_turn - w.get("turn", current_turn) < 2:
                        active.append(w["text"])
                    else:
                        expired.append(w)
                else:
                    active.append(str(w))  # legacy plain string — show once then drop
                    expired.append(w)
            actor["gm_whispers"] = [w for w in whispers if w not in expired]
            if active:
                parts.append("A feeling rises in you:")
                for whisper in active:
                    parts.append(f"  {whisper}")
                parts.append("")

        # --- Inner life and action history (all entries; compaction trims when needed) ---
        private_log = actor.get("private_log", [])
        if private_log:
            parts.append("Your recent experience:")
            for entry in private_log:
                text = entry.get("text", "")
                etype = entry.get("type", "think")
                turn = entry.get("turn", "?")
                if etype == "action":
                    parts.append(f"  T{turn} You did: {text}")
                elif etype == "feel":
                    parts.append(f"  T{turn} Your body: {text}")
                elif etype == "notice":
                    parts.append(f"  T{turn} You noticed: {text}")
                elif etype == "think":
                    parts.append(f"  T{turn} You thought: {text}")
                elif etype in ("face", "emote"):
                    parts.append(f"  T{turn} Your face: {text}")
                elif etype == "say":
                    parts.append(f"  T{turn} You said: \"{text}\"")
                else:
                    parts.append(f"  T{turn} {text}")
            parts.append("")

        # --- Map with legend ---
        # Build legend from what's visible
        legend_items: dict[str, str] = {}
        legend_items[actor["glyph"]] = f"you ({actor['name']})"
        for e in vis:
            legend_items[e["glyph"]] = e["name"]
        for y, row_str in enumerate(map_data["grid"]):
            for x, ch in enumerate(row_str):
                if (x, y) in visible_positions and ch not in legend_items:
                    tile = map_data["legend"].get(ch, {})
                    tile_name = tile.get("name", "")
                    if tile_name:
                        legend_items[ch] = tile_name

        parts.append("What you see:")
        parts.extend(rows)
        legend_str = "  ".join(f"{ch}={name}" for ch, name in legend_items.items())
        parts.append(f"({legend_str})")
        parts.append(f"Your glow reaches about {r} paces. Beyond that, darkness.")

        # --- Nearby (show bond only on first encounter or re-encounter) ---
        previously_seen = set(actor.get("last_seen_nearby", []))
        currently_visible_ids = [e["id"] for e in vis]
        if vis:
            parts.append("")
            parts.append("Nearby:")
            for e in sorted(vis, key=lambda x: manhattan(actor["pos"], x["pos"])):
                is_new_encounter = e["id"] not in previously_seen
                parts.append(self._describe_visible_entity(actor, e, show_bond=is_new_encounter))
        else:
            parts.append("")
            parts.append("No one is near. You are alone.")
        actor["last_seen_nearby"] = currently_visible_ids

        # --- Open view (trade/search) ---
        ov = self.state.flags.get("open_view")
        if ov and ov.get("actor") == actor_id:
            entries = ov.get("entries", [])
            if entries:
                parts.append("")
                mode = ov.get("mode", "open")
                source_name = self.state.entities.get(ov.get("source", ""), {}).get("name", ov.get("source", ""))
                parts.append(f"Before you ({mode} — {source_name}):")
                for x in entries:
                    parts.append(f"  {x['name']} — take with: {x['command']}")

        # --- Events (filtered: exclude own actions, mechanical echoes) ---
        actor_name = actor.get("name", "")
        raw_events = [
            e for e in actor.get("seen_events", [])
            if e.get("location") == loc
            and not e.get("text", "").startswith(f"{actor_name} ")
            and not e.get("text", "").startswith(f"{actor_name}:")
        ][-5:]
        if raw_events:
            dramatic = []
            mundane = []
            for ev in raw_events:
                text = ev.get("text", "")
                if " moves to [" in text or text.endswith(" waits."):
                    continue
                if text.startswith("NARRATOR:") or any(w in text.lower() for w in ["crumble", "collapse", "crack", "seep", "rumor", "cold draft", "shifted"]):
                    dramatic.append(f"[!] {text}")
                else:
                    mundane.append(f"- {text}")
            if dramatic or mundane:
                parts.append("")
                parts.append("What has happened:")
                for d in dramatic:
                    parts.append(d)
                for m in mundane:
                    parts.append(m)

        # --- Directed speech ---
        actor_id_lower = actor_id.lower()
        actor_name_lower = actor["name"].lower()
        directed = []
        for ev in raw_events:
            text = ev.get("text", "")
            if " says: " in text:
                speaker = text.split(" says: ")[0]
                if speaker.lower() != actor_name_lower:
                    quote = text.split(" says: ", 1)[1]
                    if actor_name_lower in quote.lower() or actor_id_lower in quote.lower():
                        directed.append(f"{speaker} said to you: {quote}")
        if directed:
            parts.append("")
            parts.append("Someone spoke to you:")
            for d in directed:
                parts.append(f"  {d}")
            parts.append("  You have not responded yet.")

        # --- Available actions (humanized) ---
        parts.append("")
        parts.append("You could:")
        actions = self.available_actions(actor_id)
        for a in actions:
            parts.append(f"  {self._humanize_action(a, actor_id)}")
        parts.append("  ...or something else entirely.")

        # --- Response format (at the very end) ---
        parts.append("")
        parts.append("Respond in this exact format:")
        parts.append("Feel: (what is in your body right now — sensation, weight, what you register before thought)")
        parts.append("Notice: (what you actually attend to — what your specific history makes important)")
        parts.append("Think: (private reasoning — what you conclude, fear, calculate, or hope; what you're not saying aloud)")
        parts.append("Face: (what your body and expression do — what a careful watcher might see; you cannot fully control it)")
        parts.append("Speak: (what you say aloud, or nothing)")
        parts.append("Do: [one action from the list above, copied exactly]")
        parts.append("If your feeling toward someone changed: Relation: entity_id=new_stance 'one line why'")
        parts.append("")
        parts.append("Feel and Think are private — be honest there. If you are repeating yourself, something has changed that you haven't named yet. Find it.")
        parts.append("Write Feel and Think as your own inner experience. Speak is what comes out of your mouth. Do is one action.")

        return "\n".join(parts)
