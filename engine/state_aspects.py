"""State aspects → prompt modulation.

The substrate's nervous system: small, named, read-only scalars/labels
computed from `engine.state` every turn. GM-organ prompt-builders read
them and either swap a paragraph or inject a hint. The point is that
game STATE (entity counts, deaths, body needs, recent failures, ...)
shows up legibly in the language that shapes the next turn.

Convention:
  • One observer = one function `def aspect_<name>(state) -> dict | str`
  • Observers are pure: no engine mutation, no LLM calls, no logging.
  • Output is a small dict the prompt-builders dereference, or a label
    enum-like string (e.g. "low" / "saturated") for paragraph branching.
  • Adding a new observer is one function + one if-branch in whichever
    GM organ reads it. That's the whole framework.

Two reference implementations ship here so the pattern sets in code:
  - `aspect_entity_count_pressure` — Settling's create_character paragraph
    swaps between "mint freely" and "the room is full" branches.
  - `aspect_recent_death_pulse` — Breath's grace-note framing gets a
    one-line prefix when a soul was alive→dead in the last 2 turns.

Future observers can follow either shape (paragraph-swap or prefix-inject).
"""
from __future__ import annotations

import math
from typing import Any


def aspect_entity_count_pressure(state: Any, map_id: str | None = None) -> str:
    """Saturation of the current map's population vs its population_target.

    Returns one of {"low", "saturated"}.

    `population_target` is read from the map's metadata at map-creation time;
    falls back to int(sqrt(walkable_tile_count)) when unset.

    `map_id` defaults to `state.current_map_id`. Pass an explicit id when
    branching for a specific room (Settling-resolver looking at a destination).
    """
    loc = map_id or state.current_map_id
    map_data = state.maps.get(loc, {})
    target = map_data.get("population_target")
    if target is None:
        # Fallback: sqrt of walkable-tile count.
        grid = map_data.get("grid", [])
        legend = map_data.get("legend", {})
        walkable = 0
        for row in grid:
            for ch in row:
                if "walkable" in legend.get(ch, {}).get("tags", []):
                    walkable += 1
        target = max(1, int(math.sqrt(walkable)))

    alive_mobile_here = sum(
        1 for e in state.entities.values()
        if e.get("location") == loc
        and "alive" in e.get("tags", [])
        and "mobile" in e.get("tags", [])
    )
    return "saturated" if alive_mobile_here >= int(target) else "low"


def aspect_recent_death_pulse(state: Any, window: int = 2) -> dict[str, Any]:
    """Was any entity alive→dead in the last `window` turns?

    Returns: {"active": bool, "names": [str], "turns_ago": int | None}

    Detection: state.flags['recent_deaths'] is appended to by the death-handler
    each time a soul transitions out of `alive`. Entries: {turn, id, name}.
    Old entries (turn < state.turn - window) are filtered out here, NOT pruned
    in state — keeping the log lets memorial / GM-resolver reach back further.
    """
    recent = state.flags.get("recent_deaths") or []
    cutoff = state.turn - window
    active = [d for d in recent if int(d.get("turn", 0)) > cutoff]
    if not active:
        return {"active": False, "names": [], "turns_ago": None}
    newest = max(active, key=lambda d: int(d.get("turn", 0)))
    return {
        "active": True,
        "names": [d.get("name", d.get("id", "?")) for d in active],
        "turns_ago": state.turn - int(newest.get("turn", state.turn)),
    }


def aspect_body_needs_climate(state: Any) -> dict[str, Any]:
    """Population-wide body pressure: how hungry, how thirsty are the souls?

    Returns: {
        "label": "calm" | "warming" | "sharp" | "critical",
        "hunger_mean": int,   # 0-100
        "thirst_mean": int,   # 0-100
        "hot_names": [str],   # names whose hunger or thirst >= 80
    }

    Computed over `alive` entities only (dead souls have no body). Labels are
    chosen by the WORST channel — if mean thirst is 70 and mean hunger is 20,
    label is "sharp" because thirst is doing the work.

    Read by Breath (atmospheric prefix when label != "calm") and by Settling
    (which may nudge a fountain whisper if "sharp" persists). The substrate
    can act on this without any soul having explicitly said "I'm hungry."
    """
    alive = [e for e in state.entities.values() if "alive" in e.get("tags", [])]
    if not alive:
        return {"label": "calm", "hunger_mean": 0, "thirst_mean": 0, "hot_names": []}
    hungers = [int(e.get("stats", {}).get("hunger", 0)) for e in alive]
    thirsts = [int(e.get("stats", {}).get("thirst", 0)) for e in alive]
    h_mean = sum(hungers) // len(hungers)
    t_mean = sum(thirsts) // len(thirsts)
    worst = max(h_mean, t_mean)
    if worst >= 85:
        label = "critical"
    elif worst >= 65:
        label = "sharp"
    elif worst >= 40:
        label = "warming"
    else:
        label = "calm"
    hot_names = [
        e.get("name", e.get("id", "?"))
        for e in alive
        if int(e.get("stats", {}).get("hunger", 0)) >= 80
        or int(e.get("stats", {}).get("thirst", 0)) >= 80
    ]
    return {
        "label": label,
        "hunger_mean": h_mean,
        "thirst_mean": t_mean,
        "hot_names": hot_names,
    }
