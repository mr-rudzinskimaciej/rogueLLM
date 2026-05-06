"""Quick per-turn cost breakdown from a capture's audit log."""
import json, re, sys
from pathlib import Path

cap = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ROLE_RE = re.compile(r"role:(\w+):(\d+)tok:\$([\d.]+)")

print(f"{'turn':>4} {'calls':>6} {'npc_in':>8} {'wb_in':>8} {'gm_in':>8} {'total':>8} {'cost':>9}")
for f in cap.get("frames", []):
    t = f.get("turn")
    by_role = {}
    total_tok = 0
    total_cost = 0.0
    for line in f.get("audit", []):
        m = ROLE_RE.match(line)
        if m:
            role, tok, cost = m.group(1), int(m.group(2)), float(m.group(3))
            by_role.setdefault(role, [0, 0.0])
            by_role[role][0] += tok
            by_role[role][1] += cost
            total_tok += tok
            total_cost += cost
    npc = by_role.get("npc", [0, 0])[0]
    wb = by_role.get("worldbuilder", [0, 0])[0] + by_role.get("npc_compact", [0, 0])[0]
    gm = sum(v[0] for k, v in by_role.items() if k.startswith("gm") or k == "weaver")
    print(f"{t:>4} {len([l for l in f.get('audit',[]) if l.startswith('role:')]):>6} {npc:>8} {wb:>8} {gm:>8} {total_tok:>8} {total_cost:>9.4f}")
