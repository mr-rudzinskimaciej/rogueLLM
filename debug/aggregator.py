"""
Aggregator: collapse 4 judge verdicts into a one-screen summary + carry open
loops forward.

Output shape (markdown):

    === BLOCK <n>  turns <start_turn>..<end_turn> ===
    intensity-stack: cartographer:breathing  augur:kicking  coroner:flat  npc_observer:twitching

    INTERESTING:
      ...

    BORING:
      ...

    OPEN LOOPS:
      ...

Loops are persisted in loops.jsonl at the run root. Each line:
    {id, opened_block, last_seen_block, age_blocks, source_judge, question, evidence, status}

status ∈ {open, stale, resolved}.  v0 marks a loop `stale` after 3 blocks
without re-mention. Resolution is manual for v0 (user edits the file or we add
an LLM resolution pass in v1).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


JUDGE_ORDER = ["cartographer", "augur", "coroner", "npc_observer"]
INTENSITY_RANK = {"flat": 0, "twitching": 1, "breathing": 2, "kicking": 3}
STALE_AGE_BLOCKS = 3


# -------- loops persistence --------

def _load_loops(run_dir: Path) -> list[dict]:
    path = run_dir / "loops.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _save_loops(run_dir: Path, loops: list[dict]) -> None:
    path = run_dir / "loops.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in loops:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _loop_id(judge: str, question: str) -> str:
    """Deterministic id from judge + question stem so re-mentions can be matched."""
    stem = re.sub(r"\W+", "_", question.lower()).strip("_")[:60]
    return f"{judge}:{stem}"


# -------- verdict ingestion --------

def _verdict_path(block_dir: Path, judge: str) -> Path:
    return block_dir / "verdicts" / f"{judge}.json"


def _load_verdicts(block_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for j in JUDGE_ORDER:
        p = _verdict_path(block_dir, j)
        if p.exists():
            out[j] = json.loads(p.read_text(encoding="utf-8"))
    return out


def _intensity_stack_line(verdicts: dict[str, dict]) -> str:
    parts = []
    for j in JUDGE_ORDER:
        v = verdicts.get(j) or {}
        parts.append(f"{j}:{v.get('intensity') or '—'}")
    return "intensity-stack: " + "  ".join(parts)


# -------- section builders --------

def _format_example(judge: str, ex: dict) -> str:
    """Render one example line. Shape varies by judge — we accept anything."""
    if "arc" in ex and isinstance(ex["arc"], list):
        # augur arcs
        head = f"  • [{judge}] arc — {ex.get('why_unpredicted', '')}"
        body = "\n".join(
            f"      t{step.get('turn')}: {step.get('line')}" for step in ex["arc"]
        )
        return f"{head}\n{body}"
    turn = ex.get("turn")
    name = ex.get("name") or ex.get("class") or ""
    evidence = ex.get("evidence") or ex.get("line") or ex.get("why_it_matters") or ""
    note = ex.get("note") or ex.get("severity") or ex.get("why_it_matters") or ""
    bits = [f"  • [{judge}"]
    if turn is not None:
        bits.append(f", t{turn}")
    if name:
        bits.append(f", {name}")
    bits.append("] ")
    line = "".join(bits) + str(evidence)
    if note and note != evidence:
        line += f"   ({note})"
    return line


def _section_interesting(verdicts: dict[str, dict]) -> str:
    lines: list[str] = []
    for j in JUDGE_ORDER:
        v = verdicts.get(j) or {}
        rank = INTENSITY_RANK.get((v.get("intensity") or "").lower(), 0)
        if rank < 2:  # skip flat / twitching
            continue
        verdict_text = (v.get("verdict") or "").strip()
        if verdict_text:
            lines.append(f"  • [{j}] {verdict_text}")
        for ex in (v.get("examples") or [])[:2]:
            lines.append(_format_example(j, ex))
    return "\n".join(lines) if lines else "  (nothing surfaced)"


def _section_boring(verdicts: dict[str, dict], stuck_loops: list[dict]) -> str:
    lines: list[str] = []
    for j in JUDGE_ORDER:
        v = verdicts.get(j) or {}
        intensity = (v.get("intensity") or "").lower()
        if intensity in {"flat", "twitching"}:
            verdict_text = (v.get("verdict") or "").strip()
            if verdict_text:
                lines.append(f"  • [{j}, {intensity}] {verdict_text}")
    for loop in stuck_loops:
        lines.append(
            f"  • [stale loop, age={loop['age_blocks']}] {loop['question']} "
            f"(opened block {loop['opened_block']})"
        )
    return "\n".join(lines) if lines else "  (block was lively)"


def _section_open_loops(active_loops: list[dict]) -> str:
    if not active_loops:
        return "  (no open threads)"
    lines = []
    for loop in active_loops:
        lines.append(
            f"  • [{loop['source_judge']}, age={loop['age_blocks']}] {loop['question']}"
        )
    return "\n".join(lines)


# -------- loop reconciliation --------

def _harvest_new_loops(verdicts: dict[str, dict], block_index: int) -> list[dict]:
    """Each judge concern becomes a candidate loop."""
    new_loops = []
    for j, v in verdicts.items():
        for concern in (v.get("concerns") or []):
            q = (concern or "").strip()
            if not q:
                continue
            new_loops.append({
                "id": _loop_id(j, q),
                "source_judge": j,
                "question": q,
                "evidence": (v.get("examples") or [None])[0],
                "opened_block": block_index,
                "last_seen_block": block_index,
                "age_blocks": 0,
                "status": "open",
            })
    return new_loops


def _reconcile_loops(prior: list[dict], new: list[dict], block_index: int) -> tuple[list[dict], list[dict]]:
    """
    Merge new loops into prior. Returns (active_loops, stuck_loops_for_boring_section).

    Rules:
      - If a new loop's id matches a prior open loop, refresh last_seen_block, age_blocks=0.
      - Prior open loops not re-mentioned: age_blocks += 1; if >= STALE_AGE_BLOCKS, mark stale.
      - Stale loops are not surfaced again as 'open' but emit one final boring-section entry.
    """
    by_id = {row["id"]: dict(row) for row in prior}

    # Apply new loops
    for nl in new:
        if nl["id"] in by_id:
            existing = by_id[nl["id"]]
            existing["last_seen_block"] = block_index
            existing["age_blocks"] = 0
            existing["status"] = "open"
        else:
            by_id[nl["id"]] = nl

    new_ids = {nl["id"] for nl in new}
    stuck_now: list[dict] = []
    active: list[dict] = []
    for row in by_id.values():
        if row.get("status") == "resolved":
            continue
        if row["id"] not in new_ids and row.get("status") == "open":
            row["age_blocks"] = (row.get("age_blocks", 0) or 0) + 1
            if row["age_blocks"] >= STALE_AGE_BLOCKS:
                if row.get("status") != "stale":
                    row["status"] = "stale"
                    stuck_now.append(row)
                continue
        if row.get("status") == "stale":
            continue
        active.append(row)

    # Persist all (including stale) so they're auditable.
    return list(by_id.values()), stuck_now


# -------- top-level --------

def aggregate_block(run_dir: str | Path, block_index: int) -> Path:
    """
    Read verdicts for runs/<run_id>/blocks/<n>/, write summary.md, update loops.jsonl.
    Returns path to summary.md.
    """
    run_dir = Path(run_dir)
    block_dir = run_dir / "blocks" / f"{block_index:04d}"
    verdicts = _load_verdicts(block_dir)
    if not verdicts:
        raise FileNotFoundError(f"no verdicts under {block_dir}/verdicts/")

    # Block bounds — pull from first available verdict
    sample_block = next((v.get("block") for v in verdicts.values() if v.get("block")), {})
    start_turn = sample_block.get("start_turn")
    end_turn = sample_block.get("end_turn")

    prior_loops = _load_loops(run_dir)
    new_loops = _harvest_new_loops(verdicts, block_index)
    all_loops, stuck_now = _reconcile_loops(prior_loops, new_loops, block_index)
    _save_loops(run_dir, all_loops)

    active = [r for r in all_loops if r.get("status") == "open"]

    parts = [
        f"=== BLOCK {block_index:04d}  turns {start_turn}..{end_turn} ===",
        f"_generated {datetime.now().isoformat(timespec='seconds')}_",
        "",
        _intensity_stack_line(verdicts),
        "",
        "## INTERESTING",
        _section_interesting(verdicts),
        "",
        "## BORING",
        _section_boring(verdicts, stuck_now),
        "",
        "## OPEN LOOPS",
        _section_open_loops(active),
        "",
    ]
    text = "\n".join(parts)

    summary_path = block_dir / "summary.md"
    summary_path.write_text(text, encoding="utf-8")
    return summary_path
