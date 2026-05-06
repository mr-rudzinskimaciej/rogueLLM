"""
PROPOSAL 4 — Selective `--no-thinking`: NPCs OFF, Weaver ON, GM split.

WHAT:
DeepSeek V4 has dual Thinking/Non-Thinking modes. `wet_run_counted.py`
exposes `--no-thinking` as a global flag (`DISABLE_THINKING[0] = True`).
Currently it's all-or-nothing.

After: per-role control. NPCs go non-thinking (constrained verb set, fast
decisions). GM Breath goes non-thinking (sensory atmosphere, not heavy
reasoning). GM Settling stays thinking (post-round resolution needs care).
Weaver stays MAX thinking per user direction (long-arc oracle).

SAVINGS: ~20-30% NPC tail latency. Stacks with parallelization.

DIFF SHAPE for `scripts/wet_run_counted.py`:

```python
# Replace single DISABLE_THINKING list with per-role override.
# Around line 98:
THINKING_BY_ROLE: dict[str, bool] = {
    "npc": False,           # constrained verb pick — no thinking needed
    "gm_breath": False,     # sensory atmosphere
    "gm_settling": True,    # cares about coherence
    "weaver": True,         # the slow oracle (per user)
    "worldbuilder": True,   # creation deserves thought
}

# In _wrapped at line 119, replace:
#   if DISABLE_THINKING[0]:
#       extra["reasoning"] = {"enabled": False}
# WITH:
role = CURRENT_ROLE[0]
if not THINKING_BY_ROLE.get(role, True):
    extra["reasoning"] = {"enabled": False}
```

WATCH:
- After Proposal 1 (parallel NPC decisions), CURRENT_ROLE is set ONCE before
  the fan-out. So all parallel NPC calls correctly pick `THINKING_BY_ROLE["npc"]`.
- If a future role uses thinking by default, just add to the dict.

ESTIMATED SAVINGS:
- NPCs: 6 × ~30% × 5-15s = ~9-27s saved per turn (after parallelization
  collapses to ~3-5s saved per turn since calls are concurrent — the
  *slowest* NPC call dominates)
- gm_breath: ~3-5s saved per turn

Total: ~5-10s/turn → 8-15 min over 100 turns. Modest but stacks.

USER VERIFICATION: weaver keeps thinking enabled. ✓
"""
