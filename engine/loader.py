from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_bundle(world_file: str | Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    world_path = Path(world_file).resolve()
    world_data = load_json(world_path)
    base_dir = world_path.parent

    maps: dict[str, dict[str, Any]]
    if "maps" in world_data:
        maps = {map_id: load_json(base_dir / map_file) for map_id, map_file in world_data["maps"].items()}
    else:
        map_data = load_json(base_dir / world_data["map_file"])
        map_id = map_data.get("id", "main")
        maps = {map_id: map_data}

    entities_data = load_json(base_dir / world_data["entities_file"])

    rules_ref = world_data["rules_file"]
    if isinstance(rules_ref, list):
        rules_data: list[dict[str, Any]] = []
        for rf in rules_ref:
            rules_data.extend(load_json(base_dir / rf))
    else:
        rules_data = load_json(base_dir / rules_ref)

    statuses_data = load_json(base_dir / world_data["statuses_file"])
    return world_data, maps, entities_data, rules_data, statuses_data
