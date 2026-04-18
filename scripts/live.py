"""
Generic live-play driver.

Runs a simulation with LLM-driven NPCs, prints a live feed, and optionally
saves a report. No hardcoded world, no hardcoded models, no hardcoded keys.

Examples:
  python scripts/live.py --world examples/starter/world.json --player wanderer --turns 20
  python scripts/live.py -w my_world/world.json -p hero --turns 50 --delay 3 --enable-gm
  python scripts/live.py -w my_world/world.json -p hero --no-color --report out.txt

Environment (see .env.example):
  KEROS_API_KEY     OpenAI-compatible API key. Simulation runs silent without it.
  KEROS_API_BASE    Base URL (default: OpenRouter).
  KEROS_FREE_MODELS Comma-separated models for round-robin on ":free" requests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from engine.engine import GameEngine
from engine.llm_adapter import llm_chat_completion
from engine.runtime import (
    RuntimeConfig,
    run_round,
    build_npc_system_prompt,
)


# ── ANSI palette (disabled with --no-color) ──────────────────────────────────
class C:
    DIM = "\x1b[90m"
    WHITE = "\x1b[97m"
    CYAN = "\x1b[96m"
    YELLOW = "\x1b[93m"
    GREEN = "\x1b[92m"
    RED = "\x1b[91m"
    MAGENTA = "\x1b[95m"
    BLUE = "\x1b[94m"
    BOLD = "\x1b[1m"
    RESET = "\x1b[0m"


def strip_colors() -> None:
    for attr in dir(C):
        if attr.startswith("_") or not attr.isupper():
            continue
        setattr(C, attr, "")


GENERIC_GM_SYSTEM = """\
You are the pressure that moves a world when it gets stuck. You do not run the
world. Beings run themselves. You exist for dysfunction — stuck loops, stalled
needs, information gaps, flat stretches where nothing of consequence happens.

FIRE sparingly. Prefer silence. When you act, prefer the smallest nudge that
restores movement: a rumor, a whisper, a sound from somewhere. Mechanical
changes are a last resort.

Actions: pass | whisper <id> "text" | inject <id> "text" | narrate "text" |
give <id> <item> [n] | mod_stat <id> <stat> <delta> |
add_tag <id> <tag> | remove_tag <id> <tag> |
event <map> <x> <y> "text" | rumor <map> "text" | spawn <tmpl> <x> <y> |
create_character <map> <x> <y> "sketch" | create_map "sketch" | create_rule "sketch"

Max 3 actions per turn. Prefer 1. When nothing is wrong: pass.
One line per action. No commentary."""


def print_header(text: str, color: str = C.CYAN) -> None:
    width = 60
    print(f"\n{color}{'─' * width}{C.RESET}")
    print(f"{color}{C.BOLD}{text.center(width)}{C.RESET}")
    print(f"{color}{'─' * width}{C.RESET}")


def print_turn_header(turn: int, time_of_day: str) -> None:
    print(f"\n{C.DIM}{'━' * 60}{C.RESET}")
    print(f"{C.WHITE}{C.BOLD}  TURN {turn}{C.RESET}  {C.DIM}│{C.RESET}  {C.YELLOW}{time_of_day}{C.RESET}")
    print(f"{C.DIM}{'━' * 60}{C.RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live simulation driver.")
    parser.add_argument("-w", "--world", required=True, help="Path to world.json")
    parser.add_argument("-p", "--player", required=True, help="Player entity id (set to any id; pass 'auto' for no player)")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between LLM calls (rate-limit safety)")
    parser.add_argument("--llm-radius", type=int, default=20, help="LLM activation radius around player")
    parser.add_argument("--model-npc", default=os.environ.get("KEROS_MODEL_NPC", "openai/gpt-4o-mini"))
    parser.add_argument("--model-gm", default=os.environ.get("KEROS_MODEL_GM", "openai/gpt-4o-mini"))
    parser.add_argument("--enable-gm", action="store_true")
    parser.add_argument("--temp-npc", type=float, default=0.7)
    parser.add_argument("--temp-gm", type=float, default=0.5)
    parser.add_argument("--report", default="", help="Save summary report to this file")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        strip_colors()

    world_path = Path(args.world).resolve()
    if not world_path.exists():
        print(f"error: world file not found: {world_path}", file=sys.stderr)
        return 2

    engine = GameEngine.from_world_file(str(world_path))

    # Pull world-tone override from gm_notes if the world defines one.
    gm_notes = engine.state.flags.get("gm_notes") or {}
    world_tone = gm_notes.get("world_tone") if isinstance(gm_notes, dict) else None

    config = RuntimeConfig(
        llm_activation_radius=max(1, args.llm_radius),
        gm_enabled=args.enable_gm,
        gm_max_actions=3,
    )

    if not os.environ.get("KEROS_API_KEY"):
        print(f"{C.YELLOW}[warning] KEROS_API_KEY not set — LLM calls will return 'wait'.{C.RESET}", file=sys.stderr)

    call_count = 0

    def npc_decider(actor: dict, prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count > 1 and args.delay > 0:
            time.sleep(args.delay)
        system = build_npc_system_prompt(actor, world_tone=world_tone)
        response = llm_chat_completion(system, prompt, model=args.model_npc, temperature=args.temp_npc)
        return response.strip() if response.strip() else "Do: [wait]"

    def gm_decider(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        if args.delay > 0:
            time.sleep(args.delay)
        return llm_chat_completion(GENERIC_GM_SYSTEM, prompt, model=args.model_gm, temperature=args.temp_gm)

    def auto_player(_actor: dict, _engine: GameEngine) -> dict[str, str]:
        return {"verb": "wait"}

    # ── Intro ────────────────────────────────────────────────────────────
    print_header(gm_notes.get("world_name", "SIMULATION").upper() if isinstance(gm_notes, dict) else "SIMULATION")
    print(f"  {C.DIM}World:{C.RESET} {world_path}")
    print(f"  {C.DIM}Player:{C.RESET} {args.player}")
    print(f"  {C.DIM}NPC model:{C.RESET} {args.model_npc}")
    print(f"  {C.DIM}GM:{C.RESET} {'enabled ('+args.model_gm+')' if args.enable_gm else 'disabled'}")
    print(f"  {C.DIM}Turns:{C.RESET} {args.turns}  {C.DIM}Delay:{C.RESET} {args.delay}s")
    print()

    speech_log: list[dict] = []
    action_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def step_callback(info: dict) -> None:
        if info["kind"] == "actor":
            actor_id = info["actor_id"]
            if actor_id == args.player:
                return
            actor = engine.state.entities.get(actor_id, {})
            name = actor.get("name", actor_id)
            action = info.get("action", {})
            verb = action.get("verb", "wait")
            action_counts[actor_id][verb] += 1
            if verb != "wait":
                target = action.get("target", action.get("direction", " ".join(action.get("args", []))))
                print(f"  {C.GREEN}{name}{C.RESET} {C.CYAN}{verb}{C.RESET} {target}")
        elif info["kind"] == "gm":
            print(f"  {C.RED}[GM]{C.RESET} {info.get('audit', '')}")

    engine.state.turn = 1
    for _ in range(args.turns):
        current_turn = engine.state.turn
        print_turn_header(current_turn, engine.time_of_day())
        prev_event_count = len(engine.state.event_log)

        run_round(
            engine=engine,
            player_id=args.player,
            player_action_provider=auto_player,
            npc_decider=npc_decider,
            gm_decider=gm_decider if args.enable_gm else None,
            config=config,
            step_callback=step_callback,
        )

        new_events = engine.state.event_log[prev_event_count:]
        for ev in new_events:
            text = ev.get("text", "")
            if " says: " in text:
                speaker, _, quote = text.partition(" says: ")
                print(f"  {C.YELLOW}\"{quote}\"{C.RESET} {C.DIM}— {speaker}{C.RESET}")
                speech_log.append({"turn": current_turn, "text": text})
            elif text.startswith("NARRATOR:"):
                print(f"  {C.MAGENTA}{text}{C.RESET}")
            else:
                print(f"  {C.DIM}» {text}{C.RESET}")

    # ── Report ───────────────────────────────────────────────────────────
    if args.report:
        lines = [f"=== REPORT: {args.turns} turns ===", ""]
        lines.append("=== SPEECH ===")
        for s in speech_log:
            lines.append(f"  T{s['turn']}: {s['text']}")
        lines.append("")
        lines.append("=== ACTIONS PER ENTITY ===")
        for eid, acts in action_counts.items():
            lines.append(f"  {eid}: {dict(acts)}")
        lines.append("")
        lines.append("=== FINAL ENTITY STATES ===")
        for eid, e in engine.state.entities.items():
            if "alive" not in e.get("tags", []):
                continue
            stats = e.get("stats", {})
            lines.append(
                f"  {eid} @ {e.get('location')} {e.get('pos')} | "
                f"HP:{stats.get('hp')}/{stats.get('max_hp')} "
                f"H:{stats.get('hunger')} T:{stats.get('thirst')}"
            )
        lines.append("")
        lines.append("=== ALL EVENTS ===")
        for ev in engine.state.event_log:
            lines.append(f"  T{ev['turn']}: {ev['text']}")
        Path(args.report).write_text("\n".join(lines), encoding="utf-8")
        print(f"\n{C.DIM}Report saved: {args.report}{C.RESET}")

    print(f"\n{C.DIM}Done. {call_count} LLM calls across {args.turns} turns.{C.RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
