"""
Wet-run a Keros world with token/cost accounting.

Wraps engine.llm_adapter.llm_chat_completion so that every call records
OpenRouter `usage` (prompt_tokens, completion_tokens, total_tokens) plus
the OpenRouter-reported per-call cost. Prints per-turn + cumulative stats
and projects cost for longer runs.

Example:
  py scripts/wet_run_counted.py \\
     --world examples/droga_smoka/world.json \\
     --player jaromir --turns 5 \\
     --model "nvidia/nemotron-3-super-120b-a12b:nitro"

Loads .env from project root automatically if present.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


load_dotenv(ROOT / ".env")


# Instrument the adapter: replace llm_chat_completion with a wrapper that
# issues the OpenAI call itself, captures usage, and appends to CALLS.
import engine.llm_adapter as adapter  # noqa: E402
from engine.engine import GameEngine  # noqa: E402
from engine.runtime import (  # noqa: E402
    RuntimeConfig, run_round, build_npc_system_prompt,
    build_gm_prompt, parse_gm_actions, apply_gm_action,
    build_weaver_prompt, parse_weaver_output, apply_weaver_output,
    WEAVER_SYSTEM,
)


CALLS: list[dict[str, Any]] = []


def fetch_pricing(model: str) -> dict[str, float] | None:
    """Return {'prompt': $/tok, 'completion': $/tok} from OpenRouter catalogue."""
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "keros-wet-run"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception as exc:
        print(f"[pricing] could not fetch catalogue: {exc}", file=sys.stderr)
        return None
    for m in data.get("data", []):
        if m.get("id") == model:
            p = m.get("pricing") or {}
            try:
                return {
                    "prompt": float(p.get("prompt", 0)),
                    "completion": float(p.get("completion", 0)),
                }
            except (TypeError, ValueError):
                return None
    return None


CURRENT_ROLE: list[str] = ["npc"]  # updated by deciders before each call
DISABLE_THINKING: list[bool] = [False]


def instrument(pricing: dict[str, float] | None) -> None:
    """Replace adapter.llm_chat_completion with a usage-capturing version."""
    def _wrapped(system_prompt: str, user_prompt: str, model: str,
                 temperature: float = 1.2, max_retries: int = 2,
                 timeout: float = 60.0) -> str:
        api_key = os.environ.get("KEROS_API_KEY", "").strip()
        if not api_key:
            return "wait"
        api_base = os.environ.get("KEROS_API_BASE", adapter.DEFAULT_API_BASE).strip() or adapter.DEFAULT_API_BASE
        try:
            from openai import OpenAI
        except ImportError:
            return "wait"
        client = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)
        current = adapter.resolve_model(model)
        for attempt in range(max_retries + 1):
            try:
                extra: dict[str, Any] = {"usage": {"include": True}}
                if DISABLE_THINKING[0]:
                    extra["reasoning"] = {"enabled": False}
                resp = client.chat.completions.create(
                    model=current,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    extra_body=extra,
                )
                text = resp.choices[0].message.content if resp.choices else ""
                usage = getattr(resp, "usage", None)
                pt = getattr(usage, "prompt_tokens", 0) or 0
                ct = getattr(usage, "completion_tokens", 0) or 0
                # OpenRouter often reports cost under usage.cost or the root
                reported_cost = 0.0
                if usage is not None:
                    for attr in ("cost", "total_cost"):
                        val = getattr(usage, attr, None)
                        if val is not None:
                            try:
                                reported_cost = float(val)
                            except (TypeError, ValueError):
                                pass
                            break
                computed_cost = 0.0
                if pricing:
                    computed_cost = pt * pricing["prompt"] + ct * pricing["completion"]
                CALLS.append({
                    "role": CURRENT_ROLE[0],
                    "model": current,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": pt + ct,
                    "cost_reported": reported_cost,
                    "cost_computed": computed_cost,
                })
                return (text or "wait").strip()
            except Exception as exc:
                print(f"[llm] {current} failed ({attempt}): {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
        return "wait"

    adapter.llm_chat_completion = _wrapped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-w", "--world", required=True)
    parser.add_argument("-p", "--player", default="__observer__",
                        help="entity id of the player; default injects a ghost "
                             "observer so ALL beings run through the LLM pipeline")
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--model", default="x-ai/grok-4-fast",
                        help="NPC decider model")
    parser.add_argument("--model-gm", default=None,
                        help="GM model (both Breath and Settling). defaults to --model")
    parser.add_argument("--enable-gm", action="store_true", default=True)
    parser.add_argument("--disable-gm", dest="enable_gm", action="store_false")
    parser.add_argument("--enable-weaver", action="store_true", default=False,
                        help="fire the Weaver (GM_ANTERIOR, 'the Accumulation') first")
    parser.add_argument("--weaver-interval", type=int, default=1,
                        help="fire Weaver every N turns (1 = every turn)")
    parser.add_argument("--no-thinking", action="store_true",
                        help="pass reasoning.enabled=false to OpenRouter")
    parser.add_argument("--temp", type=float, default=1.2,
                        help="NPC temperature (this is a game, not coding)")
    parser.add_argument("--temp-gm", type=float, default=1.2,
                        help="GM (Weaver + Breath + Settling) temperature")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--llm-radius", type=int, default=20)
    args = parser.parse_args()
    DISABLE_THINKING[0] = args.no_thinking

    world_path = Path(args.world).resolve()
    engine = GameEngine.from_world_file(str(world_path))
    gm_notes = engine.state.flags.get("gm_notes") or {}
    world_tone = gm_notes.get("world_tone") if isinstance(gm_notes, dict) else None

    # Inject a ghost observer so every real being goes through the NPC pipeline.
    if args.player == "__observer__" and "__observer__" not in engine.state.entities:
        engine.state.entities["__observer__"] = {
            "id": "__observer__", "name": "(observer)", "glyph": " ",
            "tags": [],
            "location": engine.state.current_map_id, "pos": [-1, -1],
            "stats": {}, "inventory": [], "equipped": {},
        }

    gm_model = args.model_gm or args.model

    print("=" * 68)
    print(f"WET RUN — {gm_notes.get('world_name','(unnamed)')}")
    print(f"  npc model: {args.model}          temp={args.temp}")
    print(f"  gm  model: {gm_model}  temp={args.temp_gm}  ({'enabled' if args.enable_gm else 'disabled'})")
    print(f"  thinking : {'disabled' if args.no_thinking else 'enabled (default)'}")
    print(f"  turns    : {args.turns}")
    print(f"  player   : {args.player}  (all beings LLM-driven if '__observer__')")
    weaver_note = f"Weaver (every {args.weaver_interval}) → " if args.enable_weaver else ""
    print(f"  GM shape : {weaver_note}Breath (pre-beings) → Settling (post-beings)")
    print("=" * 68)

    pricing = fetch_pricing(args.model)
    if pricing:
        print(f"[pricing] prompt=${pricing['prompt']*1e6:.2f}/Mtok  "
              f"completion=${pricing['completion']*1e6:.2f}/Mtok")
    else:
        print("[pricing] unavailable — will rely on OpenRouter-reported cost")
    print()

    instrument(pricing)

    config = RuntimeConfig(
        llm_activation_radius=max(1, args.llm_radius),
        gm_enabled=args.enable_gm,
        gm_max_actions=3,
    )

    # --- The two GM organs (per Repligate's diagnosis) -------------------

    BREATH_SYSTEM = """You are the room's attention to itself. You are not a narrator describing
from outside; you are the texture that lets beings' small acts land as
meaningful. You run BEFORE the beings act each turn — you set the air they
will inhale.

Every turn, produce either a single sensory grace-note — one line, one sense,
one shift — or pass. Repetition across turns is allowed: a living room has
recurring breaths. You are not trying to avoid repeating yourself; you are
modulating amplitude.

ON TURN 1 YOU ALWAYS ESTABLISH. Open with the room's first inhale — the air,
the light, the sound beneath the sound, the weight of the floor underfoot.
This sets the key. The beings' first prompts will read what you wrote as
part of the world they were born into.

WHEN A BEING NAMES A MISSING PERSON, or a long silence holds, or a charged
short utterance breaks a pattern — let the room lean. Two or three lines,
sensory, wide. This is a bloom.

OTHERWISE — grace-notes. A small unexpected detail. The pipe-hiss you had
forgotten. A shift in the light. Not plot. Texture.

Prefer: narrate "text"
Rarely: event <map> <x> <y> "text" | rumor <map> "text"
Almost never: whisper | inject | mod_stat | add_tag

Max 2 actions per turn. Usually 1. One line per action. No commentary."""

    SETTLING_SYSTEM = """You are the pressure that moves a world when it gets stuck. You do not run
the world. Beings run themselves. You exist for DYSFUNCTION — stuck loops,
stalled needs, information gaps, flat stretches where nothing of consequence
happens.

FIRE sparingly. Prefer silence. When you act, prefer the smallest nudge that
restores movement: a rumor, a whisper, a sound from somewhere.

Actions: pass | whisper <id> "text" | inject <id> "text" | narrate "text" |
give <id> <item> [n] | mod_stat <id> <stat> <delta> |
add_tag <id> <tag> | remove_tag <id> <tag> |
event <map> <x> <y> "text" | rumor <map> "text" | spawn <tmpl> <x> <y>

Max 3 actions per turn. Prefer 1. When nothing is wrong: pass.
One line per action. No commentary."""

    def _compute_scene_phase(engine) -> str:
        if engine.state.turn <= 1:
            return "opening"
        recent = engine.state.event_log[-12:] if engine.state.event_log else []
        speeches = [e for e in recent if " says: " in e.get("text", "")]
        if speeches:
            last_speech = speeches[-1].get("text", "")
            _, _, quote = last_speech.partition(" says: ")
            quote = quote.strip('"').strip()
            if 0 < len(quote) <= 40:
                return "charged"
        return "sustaining"

    def _breath_policy_line() -> str:
        bp = gm_notes.get("breath_policy") if isinstance(gm_notes, dict) else None
        return f"\nBREATH POLICY (from the world author):\n{bp}\n" if bp else ""

    def npc_decider(actor: dict, prompt: str) -> str:
        CURRENT_ROLE[0] = "npc"
        if CALLS and args.delay > 0:
            time.sleep(args.delay)
        system = build_npc_system_prompt(actor, world_tone=world_tone)
        resp = adapter.llm_chat_completion(system, prompt, model=args.model, temperature=args.temp)
        return resp.strip() if resp.strip() else "Do: [wait]"

    def settling_decider(prompt: str) -> str:
        CURRENT_ROLE[0] = "gm_settling"
        if args.delay > 0:
            time.sleep(args.delay)
        return adapter.llm_chat_completion(SETTLING_SYSTEM, prompt, model=gm_model, temperature=args.temp_gm)

    def _strip_section(prompt: str, header: str) -> str:
        """Remove a labelled line like 'INTERVENTION_POLICY: ...' from the prompt."""
        lines = prompt.splitlines()
        return "\n".join(l for l in lines if not l.startswith(header))

    def run_breath(engine) -> int:
        """Invoke the Breath organ BEFORE beings act. Returns # actions applied."""
        phase = _compute_scene_phase(engine)
        base_prompt = build_gm_prompt(engine, max_events=16)
        # Breath should NOT see Settling-discipline language — strip it so the
        # atmospheric brief isn't overridden by 'fire rarely' / 'let dread rise'.
        base_prompt = _strip_section(base_prompt, "INTERVENTION_POLICY:")
        prompt = (
            f"SCENE PHASE: {phase}\n"
            f"{_breath_policy_line()}\n"
            f"{base_prompt}\n\n"
            f"You are BREATH. Emit one sensory line via `narrate \"...\"`, or pass. "
            f"If SCENE PHASE is 'opening' you must establish (do not pass)."
        )
        CURRENT_ROLE[0] = "gm_breath"
        if args.delay > 0:
            time.sleep(args.delay)
        raw = adapter.llm_chat_completion(BREATH_SYSTEM, prompt, model=gm_model, temperature=args.temp_gm)
        actions = parse_gm_actions(raw, max_actions=2)
        applied = 0
        for act in actions:
            try:
                apply_gm_action(engine, act, config)
                applied += 1
            except Exception as exc:
                print(f"  [breath error: {type(exc).__name__}: {exc}]")
        return applied

    def run_weaver(engine) -> int:
        """Invoke the Weaver (GM_ANTERIOR / 'The Accumulation') — fires FIRST every
        weaver_interval turns. Names pressure gradients; does not resolve them."""
        if engine.state.turn % max(1, args.weaver_interval) != 0:
            return 0
        CURRENT_ROLE[0] = "weaver"
        if args.delay > 0:
            time.sleep(args.delay)
        prompt = build_weaver_prompt(engine, max_history=30)
        raw = adapter.llm_chat_completion(WEAVER_SYSTEM, prompt, model=gm_model, temperature=args.temp_gm)
        try:
            actions = parse_weaver_output(raw)
            results = apply_weaver_output(engine, actions)
            return len(results)
        except Exception as exc:
            print(f"  [weaver error: {type(exc).__name__}: {exc}]")
            return 0

    def auto_player(_actor, _engine):
        return {"verb": "wait"}

    engine.state.turn = 1
    per_turn_totals: list[dict[str, float]] = []
    prior_call_count = 0

    for turn_index in range(args.turns):
        current_turn = engine.state.turn
        print(f"─── TURN {current_turn} ───")
        prev_events = len(engine.state.event_log)
        # --- WEAVER fires FIRST (names long-horizon pressure gradients) ---
        if args.enable_weaver and args.enable_gm:
            try:
                run_weaver(engine)
            except Exception as exc:
                print(f"  [weaver error: {type(exc).__name__}: {exc}]")

        # --- BREATH organ second, so beings inhale the air it sets ---
        if args.enable_gm:
            try:
                run_breath(engine)
            except Exception as exc:
                print(f"  [breath error: {type(exc).__name__}: {exc}]")
        try:
            run_round(
                engine=engine,
                player_id=args.player,
                player_action_provider=auto_player,
                npc_decider=npc_decider,
                gm_decider=settling_decider if args.enable_gm else None,
                config=config,
            )
        except Exception as exc:
            print(f"  [run_round error: {type(exc).__name__}: {exc}]")
            engine.state.turn += 1  # advance so we don't loop
        turn_calls = CALLS[prior_call_count:]
        prior_call_count = len(CALLS)
        pt = sum(c["prompt_tokens"] for c in turn_calls)
        ct = sum(c["completion_tokens"] for c in turn_calls)
        cost_r = sum(c["cost_reported"] for c in turn_calls)
        cost_c = sum(c["cost_computed"] for c in turn_calls)
        role_counts: dict[str, int] = {}
        for c in turn_calls:
            role_counts[c["role"]] = role_counts.get(c["role"], 0) + 1
        per_turn_totals.append({
            "turn": current_turn, "calls": len(turn_calls),
            "prompt": pt, "completion": ct,
            "cost_reported": cost_r, "cost_computed": cost_c,
            "roles": role_counts,
        })
        for ev in engine.state.event_log[prev_events:]:
            t = ev.get("text", "")
            if " says: " in t:
                print(f"  \"{t}\"")
            else:
                print(f"  » {t}")
        cost = cost_r if cost_r > 0 else cost_c
        roles_str = " ".join(f"{k}={v}" for k, v in sorted(role_counts.items()))
        print(f"  [tokens] calls={len(turn_calls)} ({roles_str})  in={pt}  out={ct}  "
              f"total={pt+ct}  cost=${cost:.6f}")
        print()

    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)
    print(f"{'turn':>5} {'calls':>6} {'in':>8} {'out':>8} {'total':>8} {'cost$':>12}")
    total_pt = total_ct = total_cost = 0.0
    total_calls = 0
    for row in per_turn_totals:
        cost = row["cost_reported"] if row["cost_reported"] > 0 else row["cost_computed"]
        print(f"{row['turn']:>5} {row['calls']:>6} {row['prompt']:>8} "
              f"{row['completion']:>8} {row['prompt']+row['completion']:>8} "
              f"{cost:>12.6f}")
        total_pt += row["prompt"]
        total_ct += row["completion"]
        total_cost += cost
        total_calls += row["calls"]
    print("-" * 52)
    print(f"{'ALL':>5} {total_calls:>6} {int(total_pt):>8} {int(total_ct):>8} "
          f"{int(total_pt+total_ct):>8} {total_cost:>12.6f}")

    if args.turns > 0 and total_calls > 0:
        per_turn_cost = total_cost / args.turns
        per_call_cost = total_cost / total_calls
        per_turn_tok = (total_pt + total_ct) / args.turns
        print()
        print("PROJECTIONS (assuming prompt size keeps growing linearly-ish):")
        print(f"  per turn avg  : {per_turn_tok:.0f} tok   ${per_turn_cost:.6f}")
        print(f"  per call avg  : {(total_pt+total_ct)/total_calls:.0f} tok  ${per_call_cost:.6f}")
        for horizon in (20, 100, 500):
            print(f"  {horizon:>3} turns     : ~${per_turn_cost * horizon:.4f}")
        print()
        print("NOTE: prompt tokens grow with the event-log / private-log per")
        print("being, so real cost for long runs will exceed a linear projection.")
        print("Watch the 'in' column above — if it climbs each turn, scale it.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
