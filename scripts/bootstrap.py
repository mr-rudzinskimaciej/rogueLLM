"""
Bootstrap a playable Keros seed world from a lore.md document.

Pipeline (seven LLM stages):
  1. world_meta + scope plan
  2. setting-specific rules     (universal rules appended)
  3. setting-specific statuses  (universal statuses appended)
  4. item templates
  5. maps (one call per map)
  6. beings (one call per being, each seeing prior beings)
  7. bond weave (one pass to add bonds/relations)

Then:
  * assemble the five JSON files into --out
  * static-validate JSON shape + cross-references
  * dry-run the world for 2 turns with a stub decider (catches engine errors)
  * philosophy checks against the generated content

Usage:
  python scripts/bootstrap.py --lore my_setting.md --out examples/myworld/
  python scripts/bootstrap.py --lore lore.md --out out/ --size medium --model anthropic/claude-sonnet-4-6
  python scripts/bootstrap.py --lore lore.md --out out/ --reference examples/starter/

Env:
  KEROS_API_KEY       required (same key the runtime uses)
  KEROS_API_BASE      optional (default OpenRouter)
  KEROS_BOOTSTRAP_MODEL    optional override for --model
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
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

from engine.llm_adapter import llm_chat_completion
from engine.prompts.bootstrap import (
    STAGE_SYSTEMS,
    UNIVERSAL_RULES,
    UNIVERSAL_STATUSES,
    build_stage_1_prompt,
    build_stage_2_prompt,
    build_stage_3_prompt,
    build_stage_4_prompt,
    build_stage_5_prompt,
    build_stage_6_prompt,
    build_stage_7_prompt,
)


# ============================================================================
# Small helpers
# ============================================================================

def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(raw: str) -> Any:
    """Parse JSON from an LLM response, tolerating markdown fences and chatter."""
    if not raw:
        raise ValueError("empty response")
    text = _FENCE.sub("", raw).strip()
    # Try whole string first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back: find the first balanced {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    return json.loads(chunk)
    raise ValueError(f"no JSON found in response: {raw[:200]}")


def call_stage(stage: int, user_prompt: str, model: str, temperature: float = 0.7) -> Any:
    system = STAGE_SYSTEMS[stage]
    log(f"stage {stage}: calling {model}")
    raw = llm_chat_completion(system, user_prompt, model=model, temperature=temperature)
    if not raw or raw == "wait":
        raise RuntimeError(
            f"stage {stage} received no response from the LLM "
            f"(is KEROS_API_KEY set? is the model id valid for your base URL?)"
        )
    try:
        return extract_json(raw)
    except Exception as exc:
        raise RuntimeError(f"stage {stage} returned unparseable output: {exc}\n--- raw ---\n{raw[:500]}")


# ============================================================================
# Reference loading
# ============================================================================

def load_reference(reference_dir: Path) -> dict[str, Any]:
    """Load the starter (or any reference) set for calibration examples."""
    world = read_json(reference_dir / "world.json")
    # Single-map or multi-map:
    if "map_file" in world:
        a_map = read_json(reference_dir / world["map_file"])
    else:
        first = next(iter(world["maps"].values()))
        a_map = read_json(reference_dir / first)
    entities = read_json(reference_dir / world["entities_file"])
    rules_ref = world["rules_file"]
    rules = []
    if isinstance(rules_ref, list):
        for rf in rules_ref:
            rules.extend(read_json(reference_dir / rf))
    else:
        rules = read_json(reference_dir / rules_ref)
    statuses = read_json(reference_dir / world["statuses_file"])
    instances = entities.get("instances", [])
    reference_being = next((i for i in instances if i.get("personality")), instances[0] if instances else {})
    return {
        "world": world,
        "map": a_map,
        "items": entities.get("templates", {}),
        "being": reference_being,
        "rules": rules,
        "statuses": statuses,
    }


# ============================================================================
# Stage runners
# ============================================================================

def run_stage_1(lore: str, size: str, reference: dict[str, Any], model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = build_stage_1_prompt(lore, size, reference["world"])
    data = call_stage(1, prompt, model, temperature=0.6)
    world_meta = data["world_meta"]
    scope = data["scope"]
    log(f"stage 1 → world='{world_meta['world_name']}' scope={scope}")
    return world_meta, scope


def run_stage_2(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], reference: dict[str, Any], model: str) -> list[dict[str, Any]]:
    prompt = build_stage_2_prompt(lore, world_meta, scope, reference["rules"])
    rules = call_stage(2, prompt, model, temperature=0.5)
    if not isinstance(rules, list):
        raise RuntimeError("stage 2 must return a JSON array of rules")
    log(f"stage 2 → {len(rules)} setting-specific rules")
    return rules


def run_stage_3(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], rules: list, reference: dict[str, Any], model: str) -> dict[str, Any]:
    prompt = build_stage_3_prompt(lore, world_meta, scope, rules, reference["statuses"])
    statuses = call_stage(3, prompt, model, temperature=0.5)
    if not isinstance(statuses, dict):
        raise RuntimeError("stage 3 must return a JSON object of statuses")
    log(f"stage 3 → {len(statuses)} setting statuses")
    return statuses


def run_stage_4(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], rules: list, reference: dict[str, Any], model: str) -> dict[str, Any]:
    prompt = build_stage_4_prompt(lore, world_meta, scope, rules, reference["items"])
    items = call_stage(4, prompt, model, temperature=0.6)
    if not isinstance(items, dict):
        raise RuntimeError("stage 4 must return a JSON object of item templates")
    log(f"stage 4 → {len(items)} item templates")
    return items


def run_stage_5(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], reference: dict[str, Any], model: str) -> list[dict[str, Any]]:
    total = max(1, int(scope.get("maps", 1)))
    maps: list[dict[str, Any]] = []
    for i in range(total):
        prompt = build_stage_5_prompt(lore, world_meta, i, total, maps, reference["map"])
        m = call_stage(5, prompt, model, temperature=0.7)
        if not isinstance(m, dict) or "grid" not in m or "legend" not in m:
            raise RuntimeError(f"stage 5 map {i + 1} malformed")
        maps.append(m)
        log(f"stage 5 → map {i + 1}/{total}: {m.get('id')} ({len(m['grid'])}×{len(m['grid'][0]) if m['grid'] else 0})")
    return maps


def run_stage_6(lore: str, world_meta: dict[str, Any], scope: dict[str, Any], maps: list, items: dict[str, Any], rules: list, reference: dict[str, Any], model: str) -> list[dict[str, Any]]:
    total = max(1, int(scope.get("beings", 3)))
    rule_verbs = sorted({r["verb"] for r in rules if "verb" in r})
    beings: list[dict[str, Any]] = []
    for i in range(total):
        prompt = build_stage_6_prompt(lore, world_meta, i, total, maps, items, rule_verbs, beings, reference["being"])
        b = call_stage(6, prompt, model, temperature=0.8)
        if not isinstance(b, dict) or "id" not in b:
            raise RuntimeError(f"stage 6 being {i + 1} malformed")
        beings.append(b)
        log(f"stage 6 → being {i + 1}/{total}: {b.get('name', b['id'])}")
    return beings


def run_stage_7(lore: str, world_meta: dict[str, Any], beings: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    prompt = build_stage_7_prompt(lore, world_meta, beings)
    patch = call_stage(7, prompt, model, temperature=0.6)
    if not isinstance(patch, dict):
        raise RuntimeError("stage 7 must return a JSON object")
    patched = 0
    for b in beings:
        entry = patch.get(b["id"])
        if not isinstance(entry, dict):
            continue
        bonds = entry.get("bonds")
        rels = entry.get("relations")
        if isinstance(bonds, dict):
            b.setdefault("bonds", {}).update(bonds)
        if isinstance(rels, dict):
            b.setdefault("relations", {}).update(rels)
        if bonds or rels:
            patched += 1
    log(f"stage 7 → patched {patched}/{len(beings)} beings with bonds/relations")
    return beings


# ============================================================================
# Assembly
# ============================================================================

def assemble(out: Path, world_meta: dict[str, Any], scope: dict[str, Any],
             maps: list[dict[str, Any]], beings: list[dict[str, Any]],
             item_templates: dict[str, Any], rules: list[dict[str, Any]],
             statuses: dict[str, Any]) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    # maps
    map_files: dict[str, str] = {}
    for m in maps:
        fname = f"map_{m['id']}.json"
        write_json(out / fname, m)
        map_files[m["id"]] = fname
    # entities (templates + instances)
    write_json(out / "entities.json", {"templates": item_templates, "instances": beings})
    # rules (universal + setting)
    all_rules = list(UNIVERSAL_RULES) + list(rules)
    write_json(out / "rules.json", all_rules)
    # statuses (universal + setting)
    all_statuses = {**UNIVERSAL_STATUSES, **statuses}
    write_json(out / "statuses.json", all_statuses)
    # world.json
    start_map = maps[0]["id"]
    world: dict[str, Any] = {
        "turn": 0,
        "rng_seed": 1,
        "start_map": start_map,
        "entities_file": "entities.json",
        "rules_file": "rules.json",
        "statuses_file": "statuses.json",
        "gm_notes": world_meta,
    }
    if len(maps) == 1:
        world["map_file"] = map_files[start_map]
    else:
        world["maps"] = map_files
    world_path = out / "world.json"
    write_json(world_path, world)
    log(f"wrote: {world_path}")
    return world_path


# ============================================================================
# Static validation + cross-reference check
# ============================================================================

def validate_static(out: Path) -> list[str]:
    issues: list[str] = []
    world = read_json(out / "world.json")
    entities = read_json(out / "entities.json")
    rules = read_json(out / "rules.json")
    statuses = read_json(out / "statuses.json")

    # map resolution
    if "map_file" in world:
        map_ids = {world["start_map"]}
        maps_loaded = {world["start_map"]: read_json(out / world["map_file"])}
    else:
        map_ids = set(world.get("maps", {}).keys())
        maps_loaded = {mid: read_json(out / fn) for mid, fn in world.get("maps", {}).items()}

    templates = entities.get("templates", {})
    instances = entities.get("instances", [])
    template_ids = set(templates.keys())

    # Every instance placement/inventory must resolve
    seen_positions: set[tuple[str, tuple[int, int]]] = set()
    for b in instances:
        loc = b.get("location")
        if loc not in map_ids:
            issues.append(f"being '{b.get('id')}' on unknown map '{loc}'")
        pos = b.get("pos")
        if not (isinstance(pos, list) and len(pos) == 2):
            issues.append(f"being '{b.get('id')}' bad pos: {pos}")
        else:
            key = (loc, (pos[0], pos[1]))
            if key in seen_positions:
                issues.append(f"being '{b.get('id')}' collides at {pos} on {loc}")
            seen_positions.add(key)
            # check walkability if possible
            m = maps_loaded.get(loc)
            if m and m.get("grid") and m.get("legend"):
                grid = m["grid"]
                legend = m["legend"]
                if 0 <= pos[1] < len(grid) and 0 <= pos[0] < len(grid[0]):
                    ch = grid[pos[1]][pos[0]]
                    tags = legend.get(ch, {}).get("tags", [])
                    if "walkable" not in tags:
                        issues.append(f"being '{b.get('id')}' placed on non-walkable '{ch}' at {pos}")
                else:
                    issues.append(f"being '{b.get('id')}' pos {pos} out of bounds for map {loc}")
        for iid in b.get("inventory", []):
            if iid not in template_ids:
                issues.append(f"being '{b.get('id')}' references missing template '{iid}'")
        eq = b.get("equipped", {})
        for slot, iid in eq.items():
            if iid and iid not in template_ids:
                issues.append(f"being '{b.get('id')}' equipped '{iid}' missing from templates")

    # Rules: verbs must be strings, effects a list
    for r in rules:
        if not isinstance(r.get("verb"), str) or not isinstance(r.get("effects"), list):
            issues.append(f"rule '{r.get('id')}' malformed")

    # Statuses: must have duration and on_turn/on_expire
    for sid, s in statuses.items():
        if "stats" not in s or "duration" not in s.get("stats", {}):
            issues.append(f"status '{sid}' missing stats.duration")

    return issues


# ============================================================================
# Philosophy check — heuristic pass over generated content
# ============================================================================

HERO_WORDS = {"heroic", "brave", "noble", "pure", "valiant", "chosen"}
VILLAIN_WORDS = {"evil", "malicious", "cruel", "wicked", "depraved"}


def check_philosophy(out: Path) -> list[str]:
    issues: list[str] = []
    entities = read_json(out / "entities.json")
    world = read_json(out / "world.json")
    rules = read_json(out / "rules.json")

    tone = (world.get("gm_notes") or {}).get("world_tone", "")
    if len(tone) < 120:
        issues.append(f"world_tone is short ({len(tone)} chars) — likely generic. Aim for one concrete paragraph.")

    instances = entities.get("instances", [])
    if not instances:
        issues.append("no beings generated")
        return issues

    rule_verbs = {r.get("verb") for r in rules if isinstance(r, dict)}

    # Beings without personality are player placeholders or environmental —
    # the philosophy check only evaluates personality-bearing beings.
    personality_beings = [b for b in instances if b.get("personality")]
    if not personality_beings:
        issues.append("no beings with personality blocks — stage 6 appears to have failed")
        return issues

    bonds_coverage = 0
    for b in personality_beings:
        name = b.get("name", b.get("id", "?"))
        p = b.get("personality") or {}
        if not p.get("identity_anchor"):
            issues.append(f"being '{name}' has no identity_anchor")
        nuance_count = sum(1 for k in ("wound", "contradictions", "inner_voice", "fears", "comfort") if p.get(k))
        if nuance_count < 2:
            issues.append(f"being '{name}' has <2 nuance fields (wound/contradictions/inner_voice/fears/comfort)")
        traits = [str(t).lower() for t in (p.get("traits") or [])]
        if traits and all(any(h in t for h in HERO_WORDS) for t in traits):
            issues.append(f"being '{name}' reads as pure hero — no contradictions in traits")
        if traits and all(any(v in t for v in VILLAIN_WORDS) for t in traits):
            issues.append(f"being '{name}' reads as pure villain — no texture in traits")
        contradictions = p.get("contradictions") or []
        if len(contradictions) < 2:
            issues.append(f"being '{name}' has <2 contradictions")
        # Drives should reference at least one real verb.
        drives = " ".join(str(d) for d in (p.get("drives") or [])).lower()
        if drives and rule_verbs and not any(v and v in drives for v in rule_verbs):
            # Soft warning — drives sometimes use natural language
            issues.append(f"being '{name}' drives reference no known verb; check they're achievable")
        if b.get("bonds") or b.get("relations"):
            bonds_coverage += 1

    if personality_beings and bonds_coverage < max(1, len(personality_beings) // 2):
        issues.append(
            f"only {bonds_coverage}/{len(personality_beings)} beings with personality have "
            f"bonds or relations (< 50%) — world feels rootless"
        )

    # Second-person check — crude but effective.
    for b in personality_beings:
        p = b.get("personality") or {}
        blob = " ".join(str(v) for v in p.values() if isinstance(v, str))
        he_she = len(re.findall(r"\b(he|she|his|her|him)\b", blob, re.IGNORECASE))
        you = len(re.findall(r"\b(you|your)\b", blob, re.IGNORECASE))
        if blob and he_she > you:
            issues.append(f"being '{b.get('name', b.get('id'))}' personality uses more 3rd person than 2nd — breaks the spell")

    return issues


# ============================================================================
# 2-turn dry run — catch engine-level errors without consuming LLM.
# ============================================================================

def dry_run(world_path: Path) -> list[str]:
    issues: list[str] = []
    try:
        from engine.engine import GameEngine
        from engine.runtime import RuntimeConfig, run_round
    except Exception as exc:
        return [f"engine import failed: {exc}"]

    try:
        engine = GameEngine.from_world_file(str(world_path))
    except Exception as exc:
        return [f"engine failed to load world: {exc}"]

    config = RuntimeConfig(llm_activation_radius=1, gm_enabled=False)

    def stub_decider(actor: dict, prompt: str) -> str:
        return "Do: [wait]"

    def auto_player(_a: dict, _e) -> dict[str, str]:
        return {"verb": "wait"}

    instances = engine.state.entities
    # Pick any living being as the player stand-in.
    player_id = next((eid for eid, e in instances.items() if "alive" in e.get("tags", [])), None)
    if not player_id:
        return ["dry-run: no alive entities found"]

    start_events = len(engine.state.event_log)
    for _ in range(2):
        try:
            run_round(
                engine=engine,
                player_id=player_id,
                player_action_provider=auto_player,
                npc_decider=stub_decider,
                gm_decider=None,
                config=config,
            )
        except Exception as exc:
            issues.append(f"dry-run turn raised {type(exc).__name__}: {exc}")
            return issues
    end_events = len(engine.state.event_log)
    if end_events <= start_events:
        issues.append("dry-run produced no events — engine may be silently stuck")
    return issues


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a Keros seed world from lore.")
    parser.add_argument("--lore", required=True, help="Path to the lore document (md/txt)")
    parser.add_argument("--out", required=True, help="Output directory for the new world")
    parser.add_argument("--size", default="small", choices=["small", "medium", "large"])
    parser.add_argument("--reference", default=str(ROOT / "examples" / "starter"),
                        help="Reference world dir (shape/depth calibration)")
    parser.add_argument("--model", default=os.environ.get("KEROS_BOOTSTRAP_MODEL", "anthropic/claude-sonnet-4-6"))
    parser.add_argument("--skip-dry-run", action="store_true")
    parser.add_argument("--skip-philosophy", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("KEROS_API_KEY"):
        print("error: KEROS_API_KEY not set", file=sys.stderr)
        return 2

    lore_path = Path(args.lore).resolve()
    if not lore_path.exists():
        print(f"error: lore not found: {lore_path}", file=sys.stderr)
        return 2
    lore = lore_path.read_text(encoding="utf-8")

    reference_dir = Path(args.reference).resolve()
    if not reference_dir.exists():
        print(f"error: reference dir not found: {reference_dir}", file=sys.stderr)
        return 2
    reference = load_reference(reference_dir)

    out = Path(args.out).resolve()
    log(f"lore: {lore_path}")
    log(f"reference: {reference_dir}")
    log(f"out: {out}")
    log(f"model: {args.model}")
    log(f"size: {args.size}")

    # Pipeline
    world_meta, scope = run_stage_1(lore, args.size, reference, args.model)
    rules = run_stage_2(lore, world_meta, scope, reference, args.model)
    all_rules_for_stage4 = list(UNIVERSAL_RULES) + list(rules)
    statuses = run_stage_3(lore, world_meta, scope, rules, reference, args.model)
    items = run_stage_4(lore, world_meta, scope, all_rules_for_stage4, reference, args.model)
    maps = run_stage_5(lore, world_meta, scope, reference, args.model)
    beings = run_stage_6(lore, world_meta, scope, maps, items, all_rules_for_stage4, reference, args.model)
    beings = run_stage_7(lore, world_meta, beings, args.model)

    world_path = assemble(out, world_meta, scope, maps, beings, items, rules, statuses)

    # Validation
    log("running static validation...")
    static = validate_static(out)
    if static:
        log(f"STATIC ISSUES ({len(static)}):")
        for s in static:
            log(f"  - {s}")
    else:
        log("static validation: OK")

    philosophy: list[str] = []
    if not args.skip_philosophy:
        log("running philosophy checks...")
        philosophy = check_philosophy(out)
        if philosophy:
            log(f"PHILOSOPHY CONCERNS ({len(philosophy)}):")
            for p in philosophy:
                log(f"  - {p}")
        else:
            log("philosophy: OK")

    dry: list[str] = []
    if not args.skip_dry_run:
        log("running 2-turn dry-run...")
        dry = dry_run(world_path)
        if dry:
            log(f"DRY-RUN FAILURES ({len(dry)}):")
            for d in dry:
                log(f"  - {d}")
        else:
            log("dry-run: OK")

    log("=" * 60)
    log(f"bootstrap complete → {world_path}")
    log(f"try it: python scripts/live.py -w {world_path} -p {beings[0]['id']} --turns 10")

    if static or dry:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
