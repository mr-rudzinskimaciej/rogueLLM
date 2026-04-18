"""
Altitude-tiered drives — the "ladder with gravity" mechanism.

Each drive sits at an altitude (body / scene / arc) with a status
(active / carried / dormant / met). A being has a `ceiling` that caps
how high its drives reach. Body pressure preempts higher altitudes
when hunger or thirst crosses threshold — hunger always wins when
it's sharp.

Backward-compat: a plain-string drive is auto-promoted to the object
shape at runtime (altitude defaults to "scene", status to "active").
Old worlds keep working; new bootstraps ship the rich shape.

Design rationale: beings carry wants, the world carries weather.
Tiered wants live on `personality.drives`; world pressures live on
`state.flags.weaver_gradients`.
"""
from __future__ import annotations

from typing import Any

ALTITUDES = ("body", "scene", "arc")
CEILINGS = ("body", "scene", "arc")

# Default body-preempt thresholds — if a being's hunger or thirst is
# above these, body-altitude drives re-latch and higher drives are
# rendered as "carried" rather than active.
HUNGER_PREEMPT = 60
THIRST_PREEMPT = 60


def normalize_drive(d: Any) -> dict[str, Any]:
    """Wrap a drive into the canonical object shape.

    Old string-shaped drives are promoted to: altitude="scene", status="active",
    one-phase, load=0.5. That keeps starter worlds loading unchanged.
    """
    if isinstance(d, str):
        return {
            "text": d,
            "phase": 0,
            "phases": [d],
            "load": 0.5,
            "dormant_until": None,
            "advances_on": None,
            "altitude": "scene",
            "status": "active",
        }
    if not isinstance(d, dict):
        return {
            "text": str(d),
            "phase": 0,
            "phases": [str(d)],
            "load": 0.5,
            "dormant_until": None,
            "advances_on": None,
            "altitude": "scene",
            "status": "active",
        }
    out = dict(d)
    out.setdefault("text", "")
    out.setdefault("phase", 0)
    out.setdefault("phases", [out.get("text", "")])
    out.setdefault("load", 0.5)
    out.setdefault("dormant_until", None)
    out.setdefault("advances_on", None)
    alt = out.get("altitude") or out.get("tier") or "scene"
    if alt not in ALTITUDES:
        alt = "scene"
    out["altitude"] = alt
    if out.get("status") not in ("active", "carried", "dormant", "met"):
        out["status"] = "active"
    return out


def normalize_drives(raw: list[Any] | None) -> list[dict[str, Any]]:
    return [normalize_drive(d) for d in (raw or [])]


def infer_ceiling(entity: dict[str, Any]) -> str:
    """If the being didn't declare a ceiling, guess one from tags."""
    personality = entity.get("personality") or {}
    declared = personality.get("ceiling")
    if declared in CEILINGS:
        return declared
    tags = set(entity.get("tags", []))
    # Heroes / lucid narrators / meta-aware reach arc.
    if tags & {"hero", "meta_aware", "lucid", "protagonist"}:
        return "arc"
    # Post-holders, merchants, guards, named craftspeople reach scene.
    if tags & {"merchant", "guard", "craftsman", "priest", "healer", "officer", "post"}:
        return "scene"
    # Flesh-dwellers, beasts, pure NPCs stay body.
    if tags & {"flesh_dweller", "beast", "animal", "feral"}:
        return "body"
    # Fallback: scene (most townsfolk).
    return "scene"


def is_body_preempting(entity: dict[str, Any]) -> bool:
    """Does body pressure currently outrank higher altitudes?"""
    stats = entity.get("stats") or {}
    if stats.get("hunger", 0) >= HUNGER_PREEMPT:
        return True
    if stats.get("thirst", 0) >= THIRST_PREEMPT:
        return True
    if stats.get("hp", 999) <= max(1, int(stats.get("max_hp", 0)) * 0.33):
        return True
    return False


def _altitude_allowed(alt: str, ceiling: str) -> bool:
    # body <= scene <= arc; anything at or below ceiling is allowed.
    order = {"body": 0, "scene": 1, "arc": 2}
    return order.get(alt, 1) <= order.get(ceiling, 1)


def active_drives(entity: dict[str, Any], turn: int | None = None) -> list[dict[str, Any]]:
    """Drives that should be surfaced as actionable THIS turn.

    Applies: ceiling trim, dormancy window, status filter, body preemption.
    Carried drives (higher altitude held under body pressure) are NOT in
    this list — see carried_drives() for those.
    """
    personality = entity.get("personality") or {}
    ceiling = infer_ceiling(entity)
    drives = normalize_drives(personality.get("drives") or personality.get("goals", []))
    # Strip anything above the ceiling.
    drives = [d for d in drives if _altitude_allowed(d["altitude"], ceiling)]
    out: list[dict[str, Any]] = []
    preempting = is_body_preempting(entity)
    for d in drives:
        if d.get("status") == "met":
            continue
        du = d.get("dormant_until")
        if du is not None and turn is not None:
            try:
                if int(du) > int(turn):
                    continue
            except (TypeError, ValueError):
                pass
        if preempting and d["altitude"] != "body":
            continue
        out.append(d)
    return out


def carried_drives(entity: dict[str, Any], turn: int | None = None) -> list[dict[str, Any]]:
    """Drives that exist and would otherwise be active, but are held down
    by body preemption this turn. Shown to the being as texture so it
    knows its arc is still alive — just not the FOREGROUND right now."""
    personality = entity.get("personality") or {}
    ceiling = infer_ceiling(entity)
    drives = normalize_drives(personality.get("drives") or personality.get("goals", []))
    drives = [d for d in drives if _altitude_allowed(d["altitude"], ceiling)]
    if not is_body_preempting(entity):
        return []
    return [d for d in drives
            if d.get("status") != "met" and d["altitude"] != "body"]


def drives_by_altitude(entity: dict[str, Any], turn: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Group all non-met drives by altitude. Used by GM prompts."""
    personality = entity.get("personality") or {}
    ceiling = infer_ceiling(entity)
    drives = normalize_drives(personality.get("drives") or personality.get("goals", []))
    drives = [d for d in drives if _altitude_allowed(d["altitude"], ceiling)]
    grouped: dict[str, list[dict[str, Any]]] = {a: [] for a in ALTITUDES}
    for d in drives:
        if d.get("status") != "met":
            grouped[d["altitude"]].append(d)
    return grouped


def format_drive_for_npc(d: dict[str, Any]) -> str:
    """Short string — what the being reads in its own prompt."""
    text = d.get("text", "")
    phase = int(d.get("phase", 0))
    phases = d.get("phases") or [text]
    if phase < len(phases) and phases[phase] and phases[phase] != text:
        return f"{text} [now: {phases[phase]}]"
    return text


def format_active_drives_block(entity: dict[str, Any], turn: int | None = None) -> list[str]:
    """Build the 'What drives you' section for an NPC's per-turn prompt."""
    active = active_drives(entity, turn)
    carried = carried_drives(entity, turn)
    out: list[str] = []
    if active:
        by_alt: dict[str, list[dict[str, Any]]] = {a: [] for a in ALTITUDES}
        for d in active:
            by_alt[d["altitude"]].append(d)
        lines = []
        for alt in ALTITUDES:
            if by_alt[alt]:
                tag = {"body": "body", "scene": "scene", "arc": "arc"}[alt]
                lines.extend(f"[{tag}] {format_drive_for_npc(d)}" for d in by_alt[alt])
        out.append("What drives you: " + "; ".join(lines))
    if carried:
        carried_txt = "; ".join(f"[{d['altitude']}] {d.get('text','')}" for d in carried)
        out.append(f"What you still carry (held down by body): {carried_txt}")
    return out


def format_drives_for_gm(entity: dict[str, Any], altitudes: tuple[str, ...] | None = None,
                        turn: int | None = None, max_per_altitude: int = 2) -> list[str]:
    """Build altitude-grouped drive lines for a GM prompt.

    `altitudes` restricts to the organ's tier — e.g. Breath passes ("body",),
    Settling passes ("body","scene"), Weaver passes ("scene","arc"). None = all.
    """
    grouped = drives_by_altitude(entity, turn)
    alts = altitudes or ALTITUDES
    out: list[str] = []
    for alt in alts:
        items = grouped.get(alt, [])[:max_per_altitude]
        if not items:
            continue
        rendered = "; ".join(d.get("text", "") for d in items)
        out.append(f"  drives[{alt}]: {rendered}")
    return out


def promote_drive(entity: dict[str, Any], drive_idx: int, turn: int) -> dict[str, Any]:
    """Advance a drive by one phase. If it passes its last phase, mark met
    and lift the next dormant drive at the same altitude. Returns a small
    dict describing what happened for audit."""
    personality = entity.setdefault("personality", {})
    raw = personality.get("drives") or personality.get("goals") or []
    normalized = normalize_drives(raw)
    if not (0 <= drive_idx < len(normalized)):
        return {"ok": False, "reason": f"no drive at index {drive_idx}"}
    d = normalized[drive_idx]
    d["phase"] = int(d.get("phase", 0)) + 1
    phases = d.get("phases") or [d.get("text", "")]
    result: dict[str, Any] = {"ok": True, "drive_text": d.get("text", ""),
                               "new_phase": d["phase"], "total_phases": len(phases)}
    lifted = None
    if d["phase"] >= len(phases):
        d["status"] = "met"
        d["dormant_until"] = None
        result["status"] = "met"
        # Lift the next dormant drive at the same altitude.
        alt = d["altitude"]
        for other in normalized:
            if other is d:
                continue
            if other["altitude"] == alt and other.get("status") == "dormant":
                other["status"] = "active"
                lifted = other
                break
    else:
        # Advance into next phase — update text head if phases[phase] exists.
        if d["phase"] < len(phases) and phases[d["phase"]]:
            d["text"] = phases[d["phase"]]
        result["status"] = d.get("status", "active")
    personality["drives"] = normalized
    if lifted is not None:
        result["lifted"] = lifted.get("text", "")
    return result
