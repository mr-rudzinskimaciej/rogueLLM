from __future__ import annotations

from typing import Any


KNOWN_EFFECTS = {
    "damage",
    "heal",
    "move",
    "add_tag",
    "remove_tag",
    "remove_item",
    "remove_status",
    "message",
    "trigger",
    "open_view",
    "transfer_item",
    "mod_stat",
    "portal",
    "door_bump",
}


def _require_keys(obj: dict[str, Any], required: list[str], label: str) -> None:
    missing = [key for key in required if key not in obj]
    if missing:
        raise ValueError(f"{label} missing required keys: {', '.join(missing)}")


def _ensure_list(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")


def validate_map(map_data: dict[str, Any]) -> None:
    _require_keys(map_data, ["id", "name", "grid", "legend"], "map")
    _ensure_list(map_data["grid"], "map.grid")
    if not map_data["grid"]:
        raise ValueError("map.grid must not be empty")
    width = len(map_data["grid"][0])
    for row in map_data["grid"]:
        if len(row) != width:
            raise ValueError("all map.grid rows must have equal width")
    if not isinstance(map_data["legend"], dict) or not map_data["legend"]:
        raise ValueError("map.legend must be a non-empty object")
    for glyph, tile in map_data["legend"].items():
        if len(glyph) != 1:
            raise ValueError("legend keys must be single characters")
        _require_keys(tile, ["name", "tags"], f"legend['{glyph}']")
        _ensure_list(tile["tags"], f"legend['{glyph}'].tags")


def validate_entity_template(template_id: str, template: dict[str, Any]) -> None:
    _require_keys(template, ["id", "name", "glyph", "tags", "stats"], f"template '{template_id}'")
    if template["id"] != template_id:
        raise ValueError(f"template id mismatch: key '{template_id}' != value '{template['id']}'")
    _ensure_list(template["tags"], f"template '{template_id}'.tags")
    if not isinstance(template["stats"], dict):
        raise ValueError(f"template '{template_id}'.stats must be an object")


def validate_entity_instance(entity: dict[str, Any]) -> None:
    _require_keys(entity, ["id", "name", "glyph", "tags", "stats", "pos"], f"entity '{entity.get('id', '?')}'")
    _ensure_list(entity["tags"], f"entity '{entity['id']}'.tags")
    if not isinstance(entity["stats"], dict):
        raise ValueError(f"entity '{entity['id']}'.stats must be an object")
    if not isinstance(entity["pos"], list) or len(entity["pos"]) != 2:
        raise ValueError(f"entity '{entity['id']}'.pos must be [x, y]")
    inventory = entity.get("inventory", [])
    _ensure_list(inventory, f"entity '{entity['id']}'.inventory")
    statuses = entity.get("statuses", [])
    _ensure_list(statuses, f"entity '{entity['id']}'.statuses")


def validate_entities_data(entities_data: dict[str, Any]) -> None:
    _require_keys(entities_data, ["templates", "instances"], "entities")
    if not isinstance(entities_data["templates"], dict):
        raise ValueError("entities.templates must be an object")
    _ensure_list(entities_data["instances"], "entities.instances")
    for template_id, template in entities_data["templates"].items():
        validate_entity_template(template_id, template)
    ids: set[str] = set()
    for entity in entities_data["instances"]:
        validate_entity_instance(entity)
        if entity["id"] in ids:
            raise ValueError(f"duplicate entity id '{entity['id']}'")
        ids.add(entity["id"])


def validate_statuses(statuses_data: dict[str, Any]) -> None:
    if not isinstance(statuses_data, dict):
        raise ValueError("statuses file must be an object")
    for status_id, status in statuses_data.items():
        _require_keys(status, ["id", "name", "stats"], f"status '{status_id}'")
        if status["id"] != status_id:
            raise ValueError(f"status id mismatch: key '{status_id}' != value '{status['id']}'")
        if not isinstance(status["stats"], dict):
            raise ValueError(f"status '{status_id}'.stats must be an object")
        on_turn = status.get("on_turn", [])
        on_expire = status.get("on_expire", [])
        _ensure_list(on_turn, f"status '{status_id}'.on_turn")
        _ensure_list(on_expire, f"status '{status_id}'.on_expire")


def validate_rules(rules_data: list[dict[str, Any]]) -> None:
    _ensure_list(rules_data, "rules")
    ids: set[str] = set()
    for rule in rules_data:
        _require_keys(rule, ["id", "verb", "effects"], f"rule '{rule.get('id', '?')}'")
        if rule["id"] in ids:
            raise ValueError(f"duplicate rule id '{rule['id']}'")
        ids.add(rule["id"])
        if not isinstance(rule["verb"], str) or not rule["verb"]:
            raise ValueError(f"rule '{rule['id']}' has invalid verb")
        _ensure_list(rule["effects"], f"rule '{rule['id']}'.effects")
        for key in ("actor_has", "actor_equipped", "target_has", "item_has", "actor_status"):
            if key in rule:
                _ensure_list(rule[key], f"rule '{rule['id']}.{key}'")
        for effect in rule["effects"]:
            _require_keys(effect, ["effect"], f"rule '{rule['id']}' effect")
            effect_name = effect["effect"]
            if effect_name not in KNOWN_EFFECTS:
                raise ValueError(
                    f"rule '{rule['id']}' uses unsupported effect '{effect_name}'. "
                    "Add a handler before using this effect."
                )


def validate_bundle(
    maps_data: dict[str, dict[str, Any]],
    entities_data: dict[str, Any],
    rules_data: list[dict[str, Any]],
    statuses_data: dict[str, Any],
) -> None:
    if not isinstance(maps_data, dict) or not maps_data:
        raise ValueError("maps must be a non-empty object")
    for map_id, map_data in maps_data.items():
        validate_map(map_data)
        if map_data.get("id") and map_data["id"] != map_id:
            raise ValueError(f"map id mismatch: key '{map_id}' != value '{map_data['id']}'")
    validate_entities_data(entities_data)
    validate_rules(rules_data)
    validate_statuses(statuses_data)
