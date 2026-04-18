from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine import GameEngine


def snapshot_state(engine: GameEngine) -> dict[str, Any]:
    entities: dict[str, Any] = {}
    for eid, e in engine.state.entities.items():
        entities[eid] = {
            "id": e["id"],
            "name": e.get("name", eid),
            "glyph": e.get("glyph", "?"),
            "location": e.get("location"),
            "pos": list(e.get("pos", [0, 0])),
            "tags": list(e.get("tags", [])),
            "stats": dict(e.get("stats", {})),
        }
    return {
        "turn": engine.state.turn,
        "map_id": engine.state.current_map_id,
        "maps": copy.deepcopy(engine.state.maps),
        "entities": entities,
    }


def begin_capture(engine: GameEngine, world_file: str, player_id: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "meta": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "world_file": world_file,
            "player_id": player_id,
            "config": config,
        },
        "initial_state": snapshot_state(engine),
        "frames": [],
    }


def append_frame(
    capture: dict[str, Any],
    engine: GameEngine,
    audit: list[str],
    last_event_idx: int,
    private_seen: dict[str, int],
) -> tuple[int, dict[str, int]]:
    public_lines: list[str] = []
    private_lines: list[str] = []

    new_events = engine.state.event_log[last_event_idx:]
    next_event_idx = len(engine.state.event_log)
    for ev in new_events:
        txt = str(ev.get("text", "")).strip()
        if txt:
            public_lines.append(f"[WORLD] {txt}")

    for eid, entity in engine.state.entities.items():
        logs = entity.get("private_log", [])
        start = int(private_seen.get(eid, 0))
        for entry in logs[start:]:
            entry_type = str(entry.get("type", "think")).upper()
            text = str(entry.get("text", "")).strip()
            if text:
                private_lines.append(f"[PRIVATE] {entity.get('name', eid)} [{entry_type}]: {text}")
        private_seen[eid] = len(logs)

        for whisper in entity.get("gm_whispers", []):
            msg = str(whisper).strip()
            if msg:
                private_lines.append(f"[GM] whisper -> {entity.get('name', eid)}: {msg}")

    capture["frames"].append(
        {
            "turn": engine.state.turn,
            "state": snapshot_state(engine),
            "public": public_lines,
            "private": private_lines,
            "audit": list(audit[-16:]),
        }
    )
    return next_event_idx, private_seen


def append_step_frame(
    capture: dict[str, Any],
    engine: GameEngine,
    step: dict[str, Any],
    last_event_idx: int,
    private_seen: dict[str, int],
) -> tuple[int, dict[str, int]]:
    public_lines: list[str] = []
    private_lines: list[str] = []

    new_events = engine.state.event_log[last_event_idx:]
    next_event_idx = len(engine.state.event_log)
    for ev in new_events:
        txt = str(ev.get("text", "")).strip()
        if txt:
            public_lines.append(f"[WORLD] {txt}")

    for eid, entity in engine.state.entities.items():
        logs = entity.get("private_log", [])
        start = int(private_seen.get(eid, 0))
        for entry in logs[start:]:
            entry_type = str(entry.get("type", "think")).upper()
            text = str(entry.get("text", "")).strip()
            if text:
                private_lines.append(f"[PRIVATE] {entity.get('name', eid)} [{entry_type}]: {text}")
        private_seen[eid] = len(logs)

        for whisper in entity.get("gm_whispers", []):
            msg = str(whisper).strip()
            if msg:
                private_lines.append(f"[GM] whisper -> {entity.get('name', eid)}: {msg}")

    if not public_lines and not private_lines and step.get("kind") != "round_end":
        return next_event_idx, private_seen

    capture["frames"].append(
        {
            "turn": engine.state.turn,
            "state": snapshot_state(engine),
            "public": public_lines,
            "private": private_lines,
            "step": step,
        }
    )
    return next_event_idx, private_seen


def save_capture(capture: dict[str, Any], path: str | Path, engine: GameEngine) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(capture)
    payload["final_state"] = snapshot_state(engine)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path
