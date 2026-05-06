"""
Driver CLI for the debug pipeline. Post-run analysis.

Usage:
    py -m debug.run --capture reports/ds_10turn.json --run-id ds_10turn_v1
    py -m debug.run --capture reports/ds_10turn.json --run-id ds_10turn_v1 --block-size 15
    py -m debug.run --capture reports/ds_10turn.json --run-id ds_10turn_v1 --blocks 0,1,2

Defaults:
    --block-size 5     start small; scale to 15, then 100, only after stability
    --run-id           derived from capture filename + timestamp if omitted
    --no-judges        skip LLM judges (slicer + aggregator only — fast offline view)
    --serial           run judges sequentially instead of in parallel
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from debug.aggregator import aggregate_block
from debug.judges.run import run_all_judges
from debug.slicer import slice_block

# Force UTF-8 stdout on Windows (default is cp1250/cp1252 which mangles arrows etc).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = Path(__file__).resolve().parent / "runs"


def discover_blocks(capture_path: Path, block_size: int) -> list[tuple[int, int, int]]:
    """
    Walk the capture, group frames into fixed-size turn blocks.
    Returns list of (block_index, start_turn, end_turn).
    """
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    turns = sorted({int(f.get("turn", 0)) for f in capture.get("frames", [])})
    if not turns:
        return []
    first, last = turns[0], turns[-1]
    blocks: list[tuple[int, int, int]] = []
    idx = 0
    start = first
    while start <= last:
        end = min(start + block_size - 1, last)
        blocks.append((idx, start, end))
        idx += 1
        start = end + 1
    return blocks


def write_run_config(run_dir: Path, capture_path: Path, block_size: int) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "capture_path": str(capture_path),
        "block_size": block_size,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def parse_block_filter(arg: str | None) -> set[int] | None:
    if not arg:
        return None
    return {int(x.strip()) for x in arg.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keros debug pipeline driver.")
    parser.add_argument("--capture", required=True, help="path to capture JSON")
    parser.add_argument("--run-id", default=None,
                        help="run identifier (default: <capture-stem>_<timestamp>)")
    parser.add_argument("--block-size", type=int, default=5,
                        help="turns per block (default: 5; scale to 15, then 100 once stable)")
    parser.add_argument("--blocks", default=None,
                        help="comma-separated block indices to process (default: all)")
    parser.add_argument("--no-judges", action="store_true",
                        help="skip LLM judges; slice + aggregate only (judges become dry stubs)")
    parser.add_argument("--serial", action="store_true",
                        help="run judges sequentially within a block")
    args = parser.parse_args(argv)

    capture_path = Path(args.capture).resolve()
    if not capture_path.exists():
        print(f"capture not found: {capture_path}", file=sys.stderr)
        return 2

    if args.run_id:
        run_id = args.run_id
    else:
        run_id = f"{capture_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = RUNS_ROOT / run_id

    blocks = discover_blocks(capture_path, args.block_size)
    if not blocks:
        print(f"no frames in capture: {capture_path}", file=sys.stderr)
        return 2

    block_filter = parse_block_filter(args.blocks)
    write_run_config(run_dir, capture_path, args.block_size)

    print(f"run_id: {run_id}")
    print(f"capture: {capture_path}")
    print(f"blocks: {len(blocks)}  block_size: {args.block_size}")
    print(f"output: {run_dir}")
    print()

    for idx, start, end in blocks:
        if block_filter is not None and idx not in block_filter:
            continue
        print(f"--- slicing block {idx} (turns {start}..{end}) ---", flush=True)
        slice_block(
            capture_path=capture_path,
            run_dir=run_dir,
            block_index=idx,
            start_turn=start,
            end_turn=end,
        )

        if args.no_judges:
            print(f"--- judges skipped (--no-judges) ---", flush=True)
            # Still aggregate so summary.md shows slicer output state.
            block_dir = run_dir / "blocks" / f"{idx:04d}"
            verdicts_dir = block_dir / "verdicts"
            verdicts_dir.mkdir(parents=True, exist_ok=True)
            # Minimal stub verdicts so aggregator has something to read.
            from debug.judges.run import JUDGES
            for jname, (skey, _) in JUDGES.items():
                slice_data = json.loads((block_dir / "slices" / f"{skey}.json").read_text(encoding="utf-8"))
                stub = {
                    "judge": jname,
                    "model": "(skipped)",
                    "block": slice_data.get("block"),
                    "verdict": "(judges skipped via --no-judges)",
                    "intensity": "flat",
                    "examples": [],
                    "concerns": [],
                    "parse_ok": True,
                }
                (verdicts_dir / f"{jname}.json").write_text(
                    json.dumps(stub, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        else:
            print(f"--- running judges ---", flush=True)
            verdicts = run_all_judges(run_dir / "blocks" / f"{idx:04d}", parallel=not args.serial)
            for name, v in verdicts.items():
                ok = "ok" if v.get("parse_ok") else "PARSE FAIL"
                print(f"    {name:14s} {ok:10s} intensity={v.get('intensity'):10s} model={v.get('model')}")

        summary_path = aggregate_block(run_dir, idx)
        print()
        print(summary_path.read_text(encoding="utf-8"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
