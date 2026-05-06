"""
Slicer: pure dict transforms over a Keros capture file.

Reads a capture JSON (produced by engine.replay_capture) and a block boundary
(start_turn, end_turn). Emits four wide slices for the per-block judges and
appends to long aspect feeds for trend analysis.

No LLM calls. No engine imports. Just dict-walking and grep.

Wide slices (one per block):
    world_growth.json    inventory diffs (maps/entities) + worldbuilder audit lines
    emergence.json       public events + private THINK + GM whispers
    npc_behavior.json    per-entity action+thought traces over the block
    silent_bugs.json     audit pathologies clustered by token

Long slices (append-only):
    aspects/character_<id>.jsonl    one line per block this entity was on stage
    aspects/gm_thread.jsonl         one line per block where GM intervened
    aspects/errors.jsonl            one line per block, even empty
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


# -------- audit token taxonomy --------

# Each pattern is (token_class, regex). Order matters: first match wins.
_AUDIT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("worldbuilder_parse_fail", re.compile(r"^worldbuilder:.*could not parse JSON")),
    ("worldbuilder_other",      re.compile(r"^worldbuilder:")),
    ("npc_failed",              re.compile(r"^npc_failed:")),
    ("npc_compact_failed",      re.compile(r"^npc_compact_failed")),
    ("rule_failed",             re.compile(r"^rule_failed:")),
    ("create_character",        re.compile(r"^create_character\b")),
    ("create_map",              re.compile(r"^create_map\b")),
    ("create_rule",             re.compile(r"^create_rule\b")),
    ("item_expand",             re.compile(r"^item_expand:")),
    ("rule_expand",             re.compile(r"^rule_expand:")),
    ("weaver_queue",            re.compile(r"^weaver:queue\+")),
    ("needs_deprivation",       re.compile(r"^needs:")),
    ("rule_pick",               re.compile(r"^rule_pick:")),
    ("pos_collide",             re.compile(r"^pos_collide:")),
]

# Tokens we treat as silent-bug evidence (subset of the taxonomy).
_BUG_CLASSES = {
    "worldbuilder_parse_fail",
    "worldbuilder_other",
    "npc_failed",
    "npc_compact_failed",
    "rule_failed",
    "pos_collide",
}

_WORLDBUILDER_SUCCESS_CLASSES = {"create_character", "create_map", "create_rule"}


def _classify_audit_line(line: str) -> str:
    for name, pat in _AUDIT_PATTERNS:
        if pat.match(line):
            return name
    return "other"


# -------- frame access --------

def _frames_in_block(capture: dict, start_turn: int, end_turn: int) -> list[dict]:
    """Frames whose `turn` falls in [start_turn, end_turn] inclusive."""
    return [f for f in capture.get("frames", [])
            if start_turn <= int(f.get("turn", -1)) <= end_turn]


def _state_at(capture: dict, turn: int) -> dict | None:
    """State snapshot of the latest frame at-or-before `turn`. None if none exists."""
    best = None
    for f in capture.get("frames", []):
        t = int(f.get("turn", -1))
        if t <= turn:
            best = f
    if best is not None:
        return best.get("state")
    init = capture.get("initial_state")
    return init


# -------- wide slice: world_growth --------

def slice_world_growth(capture: dict, start_turn: int, end_turn: int) -> dict:
    state_before = _state_at(capture, start_turn - 1) or capture.get("initial_state", {})
    state_after = _state_at(capture, end_turn) or {}

    maps_before = set((state_before.get("maps") or {}).keys())
    maps_after = set((state_after.get("maps") or {}).keys())

    ents_before = state_before.get("entities") or {}
    ents_after = state_after.get("entities") or {}
    eid_before = set(ents_before.keys())
    eid_after = set(ents_after.keys())

    new_entities = []
    for eid in sorted(eid_after - eid_before):
        e = ents_after.get(eid, {})
        new_entities.append({
            "id": eid,
            "name": e.get("name", eid),
            "glyph": e.get("glyph", "?"),
            "location": e.get("location"),
            "pos": e.get("pos"),
            "tags": e.get("tags", []),
        })

    departed = sorted(eid_before - eid_after)

    # Worldbuilder audit lines in the block (successes)
    creations = []
    for f in _frames_in_block(capture, start_turn, end_turn):
        for line in f.get("audit", []) or []:
            cls = _classify_audit_line(line)
            if cls in _WORLDBUILDER_SUCCESS_CLASSES:
                creations.append({"turn": f.get("turn"), "class": cls, "line": line})

    return {
        "block": {"start_turn": start_turn, "end_turn": end_turn},
        "maps": {
            "added": sorted(maps_after - maps_before),
            "removed": sorted(maps_before - maps_after),
            "total_after": len(maps_after),
        },
        "entities": {
            "added": new_entities,
            "removed": departed,
            "total_after": len(eid_after),
        },
        "creations_in_audit": creations,
    }


# -------- wide slice: emergence --------

def slice_emergence(capture: dict, start_turn: int, end_turn: int) -> dict:
    public_lines: list[dict] = []
    private_think: list[dict] = []
    private_say: list[dict] = []
    gm_whispers: list[dict] = []

    for f in _frames_in_block(capture, start_turn, end_turn):
        t = f.get("turn")
        for line in f.get("public", []) or []:
            public_lines.append({"turn": t, "line": line})
        for line in f.get("private", []) or []:
            if "[GM] whisper -> " in line:
                gm_whispers.append({"turn": t, "line": line})
            elif "[THINK]:" in line:
                private_think.append({"turn": t, "line": line})
            elif "[SAY]:" in line:
                private_say.append({"turn": t, "line": line})

    return {
        "block": {"start_turn": start_turn, "end_turn": end_turn},
        "public_events": public_lines,
        "private_think": private_think,
        "private_say": private_say,
        "gm_whispers": gm_whispers,
    }


# -------- wide slice: npc_behavior --------

_PRIVATE_HEADER_RE = re.compile(r"^\[PRIVATE\]\s+(?P<name>.+?)\s+\[(?P<kind>[A-Z_]+)\]:\s*(?P<text>.*)$")
_GM_HEADER_RE = re.compile(r"^\[GM\] whisper -> (?P<name>.+?):\s*(?P<text>.*)$")


def slice_npc_behavior(capture: dict, start_turn: int, end_turn: int) -> dict:
    by_name: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "actions": [],     # extracted from public/private lines (best-effort)
        "thoughts": [],
        "says": [],
        "feels": [],
        "whispers_received": [],
    })

    # Walk private lines per frame, grouped by name.
    for f in _frames_in_block(capture, start_turn, end_turn):
        t = f.get("turn")
        for line in f.get("private", []) or []:
            m = _GM_HEADER_RE.match(line)
            if m:
                by_name[m.group("name")]["whispers_received"].append({"turn": t, "text": m.group("text")})
                continue
            m = _PRIVATE_HEADER_RE.match(line)
            if not m:
                continue
            kind = m.group("kind")
            payload = {"turn": t, "text": m.group("text")}
            buckets = {
                "THINK": "thoughts",
                "SAY": "says",
                "FEEL": "feels",
                "NOTICE": "feels",     # bucket NOTICE with FEEL for v0
                "RELATION": "feels",
            }
            bucket = buckets.get(kind)
            if bucket:
                by_name[m.group("name")][bucket].append(payload)

    # Final state snapshot for stat/pos/location
    state_after = _state_at(capture, end_turn) or {}
    ents = state_after.get("entities") or {}
    name_to_ent = {e.get("name", eid): {"id": eid, **e} for eid, e in ents.items()}

    # Per-entity flags: stillness, repeat-thinking, empty-log
    rows = []
    for name, traces in by_name.items():
        ent = name_to_ent.get(name, {})
        thought_texts = [t["text"] for t in traces["thoughts"]]
        repeats = _detect_repetition(thought_texts)
        rows.append({
            "name": name,
            "id": ent.get("id"),
            "tags": ent.get("tags", []),
            "pos": ent.get("pos"),
            "location": ent.get("location"),
            "thought_count": len(thought_texts),
            "say_count": len(traces["says"]),
            "feel_count": len(traces["feels"]),
            "whispers_received": len(traces["whispers_received"]),
            "thought_repetition_score": repeats,
            "samples": {
                "thoughts": thought_texts[:3],
                "says": [s["text"] for s in traces["says"][:3]],
                "whispers": [w["text"] for w in traces["whispers_received"][:3]],
            },
        })

    # Also list state-still entities (in entities but never appearing in private lines)
    seen_names = set(by_name.keys())
    silent = [
        {"id": eid, "name": e.get("name", eid), "tags": e.get("tags", [])}
        for eid, e in ents.items()
        if e.get("name", eid) not in seen_names
    ]

    return {
        "block": {"start_turn": start_turn, "end_turn": end_turn},
        "entities_with_traces": rows,
        "entities_silent": silent,
    }


def _detect_repetition(lines: list[str]) -> float:
    """Crude: fraction of lines whose first-5-tokens collide with a prior line."""
    if len(lines) < 2:
        return 0.0
    seen: set[str] = set()
    collisions = 0
    for line in lines:
        head = " ".join(line.split()[:5]).lower()
        if head in seen:
            collisions += 1
        seen.add(head)
    return collisions / max(len(lines) - 1, 1)


# -------- wide slice: silent_bugs --------

def slice_silent_bugs(capture: dict, start_turn: int, end_turn: int) -> dict:
    by_class: dict[str, list[dict]] = defaultdict(list)
    total_audit_lines = 0
    for f in _frames_in_block(capture, start_turn, end_turn):
        t = f.get("turn")
        for line in f.get("audit", []) or []:
            total_audit_lines += 1
            cls = _classify_audit_line(line)
            if cls in _BUG_CLASSES or cls in {"weaver_queue", "needs_deprivation"}:
                by_class[cls].append({"turn": t, "line": line})
        # Action rejections surface in public event log, not audit.
        for line in f.get("public", []) or []:
            if " cannot perform " in line:
                by_class["action_rejected"].append({"turn": t, "line": line})

    bug_classes = _BUG_CLASSES | {"action_rejected"}
    bugs = {cls: lines for cls, lines in by_class.items() if cls in bug_classes}
    notes = {cls: lines for cls, lines in by_class.items() if cls not in bug_classes}

    return {
        "block": {"start_turn": start_turn, "end_turn": end_turn},
        "audit_lines_seen": total_audit_lines,
        "bugs_by_class": bugs,
        "noise_by_class": notes,  # weaver/needs — not bugs but eyeballable
        "instrumentation_warnings": _instrumentation_warnings(capture),
    }


def _instrumentation_warnings(capture: dict) -> list[str]:
    """Static warnings about coverage gaps in the capture itself."""
    warns = []
    saturated = 0
    sampled = 0
    for f in capture.get("frames", []):
        a = f.get("audit") or []
        sampled += 1
        if len(a) >= 64:
            saturated += 1
    if sampled and saturated / sampled > 0.5:
        warns.append(
            "audit window may be truncated: >50% of frames hit the 64-line cap "
            "(consider raising in replay_capture)."
        )
    # Pre-instrumentation captures have no llm_calls field at all.
    if capture.get("frames") and "llm_calls" not in (capture["frames"][0] or {}):
        warns.append("legacy capture: no per-frame llm_calls metadata; cannot distinguish 429/parse-fail/empty.")
    return warns


# -------- long slices (append-only) --------

def append_aspect_character(aspects_dir: Path, slice_npc: dict) -> None:
    aspects_dir.mkdir(parents=True, exist_ok=True)
    block = slice_npc["block"]
    for row in slice_npc.get("entities_with_traces", []):
        eid = row.get("id") or row.get("name", "unknown")
        path = aspects_dir / f"character_{_safe(eid)}.jsonl"
        line = {
            "block": block,
            "tags": row.get("tags"),
            "pos": row.get("pos"),
            "location": row.get("location"),
            "thought_count": row.get("thought_count"),
            "say_count": row.get("say_count"),
            "thought_repetition_score": row.get("thought_repetition_score"),
            "sample_thought": (row.get("samples", {}).get("thoughts") or [None])[0],
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def append_aspect_gm(aspects_dir: Path, slice_emergence: dict, slice_world: dict) -> None:
    aspects_dir.mkdir(parents=True, exist_ok=True)
    path = aspects_dir / "gm_thread.jsonl"
    line = {
        "block": slice_emergence["block"],
        "whispers": len(slice_emergence.get("gm_whispers", [])),
        "creations": len(slice_world.get("creations_in_audit", [])),
        "creation_classes": [c["class"] for c in slice_world.get("creations_in_audit", [])],
        "sample_whisper": (slice_emergence.get("gm_whispers") or [{"line": None}])[0]["line"],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def append_aspect_errors(aspects_dir: Path, slice_bugs: dict) -> None:
    aspects_dir.mkdir(parents=True, exist_ok=True)
    path = aspects_dir / "errors.jsonl"
    bugs = slice_bugs.get("bugs_by_class", {})
    line = {
        "block": slice_bugs["block"],
        "audit_lines_seen": slice_bugs.get("audit_lines_seen", 0),
        "by_class": {cls: len(items) for cls, items in bugs.items()},
        "samples": {cls: items[0]["line"] for cls, items in bugs.items() if items},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))


# -------- top-level driver --------

def slice_block(
    capture_path: str | Path,
    run_dir: str | Path,
    block_index: int,
    start_turn: int,
    end_turn: int,
) -> dict[str, Path]:
    """
    Produce the four wide slices for a block, plus append the long-slice rows.
    Returns a dict of slice_name -> written path.
    """
    capture = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    run_dir = Path(run_dir)
    block_dir = run_dir / "blocks" / f"{block_index:04d}"
    slices_dir = block_dir / "slices"
    slices_dir.mkdir(parents=True, exist_ok=True)
    aspects_dir = run_dir / "aspects"

    world = slice_world_growth(capture, start_turn, end_turn)
    emergence = slice_emergence(capture, start_turn, end_turn)
    npc = slice_npc_behavior(capture, start_turn, end_turn)
    bugs = slice_silent_bugs(capture, start_turn, end_turn)

    paths = {
        "world_growth": slices_dir / "world_growth.json",
        "emergence": slices_dir / "emergence.json",
        "npc_behavior": slices_dir / "npc_behavior.json",
        "silent_bugs": slices_dir / "silent_bugs.json",
    }
    paths["world_growth"].write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["emergence"].write_text(json.dumps(emergence, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["npc_behavior"].write_text(json.dumps(npc, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["silent_bugs"].write_text(json.dumps(bugs, ensure_ascii=False, indent=2), encoding="utf-8")

    append_aspect_character(aspects_dir, npc)
    append_aspect_gm(aspects_dir, emergence, world)
    append_aspect_errors(aspects_dir, bugs)

    return paths
