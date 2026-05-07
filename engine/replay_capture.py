from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import llm_adapter
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


def begin_capture(engine: GameEngine, world_file: str, player_id: str, config: dict[str, Any], save_path: str | Path | None = None) -> dict[str, Any]:
    # If a save_path is given, truncate the JSONL sidecar so a fresh run
    # doesn't append to a previous run's stream.
    if save_path is not None:
        sidecar = _jsonl_sidecar_path(save_path)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("", encoding="utf-8")
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


def _jsonl_sidecar_path(save_path: str | Path) -> Path:
    """`reports/foo.json` -> `reports/foo.jsonl`. Lean live-tail stream."""
    p = Path(save_path)
    return p.with_suffix(".jsonl")


def append_frame(
    capture: dict[str, Any],
    engine: GameEngine,
    audit: list[str],
    last_event_idx: int,
    private_seen: dict[str, int],
    save_path: str | Path | None = None,
) -> tuple[int, dict[str, int]]:
    """Append a per-turn frame and (optionally) flush the whole capture to disk.

    Pass save_path to make the capture resilient to mid-run kills: every frame
    triggers a re-serialization of the full capture. For a 100-turn run the
    capture stays under ~10MB; the per-turn write is microseconds.
    """
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

    extras = list(getattr(engine.state, "_extra_audit", []) or [])
    if extras:
        engine.state._extra_audit.clear()
    llm_calls = list(llm_adapter.call_log)
    llm_adapter.call_log.clear()
    capture["frames"].append(
        {
            "turn": engine.state.turn,
            "state": snapshot_state(engine),
            "public": public_lines,
            "private": private_lines,
            "audit": list(audit[-64:]) + extras,
            "llm_calls": llm_calls,
        }
    )
    if save_path is not None:
        # Live flush: write the partial capture so a mid-run kill keeps
        # everything up to the last completed turn. final_state is rewritten
        # by save_capture() at run end with the canonical end-of-run snapshot.
        out_path = Path(save_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(capture)
        payload["final_state"] = snapshot_state(engine)
        payload.setdefault("meta", {})["partial"] = True
        payload["meta"]["last_turn_landed"] = engine.state.turn
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Append a lean line to the JSONL sidecar (no state snapshot —
        # state lives in the JSON; JSONL is for `tail -f` watching).
        sidecar = _jsonl_sidecar_path(save_path)
        lean = {
            "turn": engine.state.turn,
            "public": public_lines,
            "private": private_lines,
            "audit": list(audit[-64:]) + extras,
            "llm_calls_count": len(llm_calls),
        }
        with sidecar.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(lean, ensure_ascii=False) + "\n")
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
