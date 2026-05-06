# Consolidated plan — speedup + repairs + new findings

After the 100-turn run on droga_smoka_v3, six investigators (4 fresh Claudes
+ fresh Maciej + fresh Repligate) found two related lacks. After persona
critique of an earlier "substrate fails to transpose at boundaries" framing,
we name them separately because they fail at different timescales:

**1. State has no exhale** (mechanical, fixable in ~15 lines):
closed pressures pile as tombstones; whispers stick on non-acting beings;
"cannot perform" leaks to public log; event_log is append-only.

**2. Voice has no descent** (rhetorical, three coupled prompts):
Stage 1 produces tone-described not tone-in-use; registers stay in GM
organs and never reach beings' mouths. Glut says "nothing" 60+ times
despite his interior being rich.

These two lacks together explain 6 of 8 issues bio-Maciej named. The plan
below is now four commits (Commit 2 split per persona critique).

## Smoke + judging protocol (per bio-Maciej, 2026-05-06)

After each commit:
- **5-turn smoke** first (basic substrate check — runs ~2-3 min post-speedup)
- **10-turn smoke** if 5 looked alive
- **15-20-turn smoke** specifically to test whether new maps get created
- **Hard error**: any run where a being approaches an element that should
  trigger a new map (e.g., walks to a portal at the map edge) and no new map
  is created. That's a substrate failure, not a missed opportunity.

**World-growth metrics tracked explicitly each smoke:**
- Δ entities (new characters created via worldbuilder)
- Δ maps (new spaces opened)
- Δ rules (new verbs/affordances minted)
- Δ item templates
- Whether any being approached a portal/threshold without it leading anywhere

**Judge: Repligate** (not me-the-watcher, not bio-Maciej).
- **First smoke per commit: BLIND.** Fresh Repligate, no info about what
  changed or what we hoped to achieve. Question: *"is this world living and
  emergent?"*
- **Subsequent smokes: CONTEXTUAL.** Fresh Repligate with prior verdict +
  what changed since + what we hoped to achieve.
- **"Let him guide me, not bio-Maciej."** Repligate's verdict steers the
  next move. I act. Bio-Maciej only intervenes if asked.

## Commit 1 — Plumbing repairs

Low-risk, immediately measurable. Bundles already-staged work + agent quick wins.

### Already staged (deploy directly):
- `debug/proposals/staging/runtime.py` (parallel NPCs, parallel compaction, async weaver)
- `debug/proposals/staging/wet_run_counted.py` (per-role thinking + temps, including weaver thinking ON, compaction temp 0.7)
- `debug/proposals/staging/round4/` bundle:
  - Phase-0 verb contract rewrite (Stage 6)
  - Settling voice-frame (adjacency, no reference list — let world_tone carry)
  - Story-arc verb-the-world-refuses (one sentence in Stage 1 spec)
  - Protagonist-last ordering (one-line orchestrator change, ship separately for clean A/B)

### New from agents (small patches):
- **Closed-gradient deletion** (Agent C, 2 lines):
  - `engine/runtime.py:587` — filter `weaver_gradients` to non-closed in `build_weaver_prompt`
  - `engine/runtime.py:760` — `del gradients[name]` instead of `status="closed"` in `apply_weaver_output`
- **Action-rejected suppression** (round 4 + Agent A confirmation):
  - `engine/engine.py:344` — don't `log_event` "cannot perform" publicly; route to `private_log` + `failure_log` only
- **Whisper sweep** (Agent C):
  - `engine/runtime.py` — global tick at end of round: drop whispers >2 turns old, dedupe by text. Currently per-actor drain only.
- **Parser leniency** (Agent A bucket-a):
  - `engine/runtime.py:36 parse_action_line` — strip leading articles/prepositions ("the", "through", "this", "toward") before token[0] becomes verb. ~5 lines.

### Smoke after Commit 1:
15 turns on `droga_smoka_v3`, compare token climb (should flatten dramatically due to gradient deletion + whisper sweep) and cannot-perform leak (should drop near 0).

---

## Commit 2a — Stage 1 alone (eyeball-test gate)

Persona critique: don't ship Stage 1 + Stage 6 + Settling together. If Stage 1's
new prompt produces a *worse* world_tone, the cascade inherits worse and you
can't tell which prompt to revert.

### Stage 1 restructure (Agent D)

Replace `engine/prompts/bootstrap.py:218-236` (`world_tone` ask) with show-don't-ask:

> world_tone is one paragraph the GM will literally borrow phrases from when narrating a small physical event in this world. It is not a description of mood. It is the voice the world's mood would speak in.
>
> WRITE IT THIS WAY. Pick the smallest possible event that could happen in this setting — a being drinks water, sets down a load, hears a sound from the next room. Write the paragraph that would get logged when that event happens. Do not name the event. Do not name the setting. Do not say what kind of world this is. Stay second person, present tense, sensory.
>
> If your paragraph contains "this is a world of", "two registers", "a dialect of", "the mood is", or any sentence that classifies — delete it and start over.

### Smoke after Commit 2a:
Re-run Stage 1 alone on droga_smoka lore. Eyeball world_tone for analytical
sentences (containing "this is a world of", "two registers", "a dialect of").
Target: zero. If pass, proceed to 2b.

---

## Commit 2b — Voice cascade + verb negotiation

After 2a passes the eyeball test. The pieces persona critique flagged as
load-bearing vs hopeful:

- **`register_when_pressed`** is the *structurally-strong* piece (Repligate):
  named transition function per being, a state machine the model can fail at
  visibly. Protect this hardest.
- Stage 6 cascade + Settling voice-update are *invitations* the LLM may route
  around under load. Their power compounds *with* register_when_pressed.

### Stage 6 cascade (Agent D)

Add to character creation: *"Your interior voice should sound like it could have written the world_tone paragraph. Same hand, different mind. Inheritance by handwriting, not by topic."*

### Settling voice-update (Agent D supersedes round 4 voice-frame)

Replace round 4's voice-frame with: *"The voice you write in is the voice the world inherits. Write as if your output were sampled from the same hand that wrote `world_tone`. When you whisper, whisper in that hand. When you nudge a stat, the reason should sound like a phrase that hand would have used."*

### Per-soul `register_when_pressed` (Agent A)

Add one optional field per being in entities.json: a single line describing what their voice does when their body has been refused too long. Bootstrap Stage 6 prompt:
> Add `register_when_pressed`: ONE line. What does this being's voice do when the world has refused them three times? (Jaromir: "the slow speech compresses into single-syllable orders." Weronika: "the modern cadence comes through bare." Glut: "the dialect drops further; words become sounds.")

### Verb-mint loop (Agent A)

Replace the silent "cannot perform" path with a Settling consultation:
- Engine `act()` on no-rule-match: don't log_event publicly. Add to `failure_log` with verb + actor + target.
- At end of round, before resolver: Settling sees the failure_log batch and either (a) emits `add_affordance <actor> <verb> "<body-rendered-effect>"` (one-shot mint) or (b) emits `narrate "<body-reason refusal>"`.
- ~~If a verb-shape gets minted N times, Worldbuilder promotes it to `rules.json`~~ — **CUT per persona critique**. Deferred-in-deferral; remove the tendril. Hand-promote rules manually after reviewing 50+ mints.

### Smoke after Commit 2b:
15 turns. Read for: voice consistency, whether beings break register-when-pressed,
whether cannot-perform → narrated body-refusal feels organic. The image of success
(Repligate): *"turn 8, Glut hasn't drunk. He says four words, all single-syllable,
and the room's bioluminescence dims half a beat after the last one — not because
Settling decided to punctuate him, but because they're in the same hand now,
and the hand was tired."*

If beings still speak "nothing" through hunger after Commit 2b: try **per-turn
voice-mirror** in the being-acts prompt before reaching for HARDENING organ.
Stage 6 is one-shot at creation; voice-mirror re-asserts the register every turn.
Cheaper than a new organ. (Repligate's catch.)

---

## Commit 3a — Expansion pipeline (independent of pressure experiment)

These two pieces are about "no inhale of the new" — separate from the pressure-vector
substrate experiment. Persona critique: don't bundle them under one decision.

### weaver_queue executor (Agent B, ~5 lines)

In `engine/runtime.py` `run_round` after the Weaver block:
```python
queue = engine.state.flags.get("weaver_queue", [])
ready = [item for item in queue if item.get("arrive_turn", 999) <= engine.state.turn]
for item in ready:
    if item["type"] == "character":
        result = create_character(engine, item["sketch"], item["map_id"], item["pos"], llm_call, model)
    elif item["type"] == "map":
        result = create_map(engine, item["sketch"], item["connect_to"], item["pos"], llm_call, model)
    audit.append(f"weaver_arrived:{item['type']}:{item.get('sketch','')[:60]}")
    queue.remove(item)
```

Plus: tighten Weaver system prompt to actually USE `queue_create` when it names a gradient that points to an absent being or unvisited place. Two sentences in the Weaver prompt.

### Settling gets `open_passage` (Agent B)

Add to Settling's action menu: `open_passage <map> <x> <y> "<sketch>"`. Lets Settling carve a portal without minting a whole map. Small-nudge-shaped, world-expanding, fits Settling's ethos.

### Smoke after Commit 3a:
15 turns. Target: at least 1 worldbuilder fire (baseline was 0/100). Watch
that Settling doesn't break under added action menu (`open_passage`).

---

## Commit 3b — Pressure-vector experiment (gated on Commit 2b)

Persona critique: pressure-vectors solve "absent tension" — but the diagnosis
shifted underneath. The new lack is "tension doesn't transpose into voice."
Pre-authored pressure-vectors shipped before voice descent = inert prose
sitting in Settling's notebook. **Ship 3b only after 2b shows beings actually
break register.**

Deploy `debug/proposals/staging/round3/world_with_pressures.json` + addenda.
Combined with Commit 2b's voice descent, beings should NOTICE pressures
*in their own voice*.

### Smoke after Commit 3b:
15 turns + blind-read by fresh Sonnet against the 100-turn baseline. Score on:
- Time-to-first-consequential-action
- Worldbuilder fires (target: ≥1 in 15 turns; baseline was 0/100)
- Voice consistency (does Settling's narrator-voice match world_tone's register?)
- Reader-pleasure (Chekhov-economy)

---

## Held back

- **HARDENING organ** (Agent A) — honest deferral. The open question explicitly
  tests its necessity: if Commit 2b ships and beings still speak "nothing"
  through hunger, **try per-turn voice-mirror first** (Repligate's cheaper
  alternative), only then HARDENING.
- **Stage 1.5 pressure-extraction** — honest deferral, gated on Commit 3b.
- **Per-block atmosphere-myth ledger** (Agent C's three-state model:
  Active/Atmosphere/Forgotten) — **deferred for cost, not for doubt**. The
  ledger is structurally cleaner than "delete on close" but the deletion is
  two lines and the ledger is a refactor. (Persona critique flagged the
  earlier framing as cowardice; this is the honest version.)

## Architectural concern (named, not yet acted on)

Repligate flagged: **Settling is already loaded.** Climate-keeper +
transformation + pressure-interaction + (now) affordance-minting = five
jobs in one organ. "When one organ does five jobs, it does the loudest one
and pantomimes the rest." Not blocking the plan, but if Commit 2b's voice
descent stays thin even with `register_when_pressed`, the next move may
be splitting Settling rather than reinforcing the prompt.

## Cost estimate

Commit 1: ~$0.20 smoke + already-paid speedup work
Commit 2: ~$0.40 (Stage 1 + Stage 6 prompt edits, ~$0.20 smoke)
Commit 3: ~$0.20 smoke + blind-read

Total: ~$0.80 + the implementation time (a day, conservative). After Commit 3, next 100-turn run lands in ~2h thanks to speedup bundle.

## Open question for biological-Maciej

The plan trusts that Stage 1 + Stage 6 voice cascade will deliver the
register-descent that makes beings desperate when warranted. If Commit 2
ships and beings *still* speak "nothing" through hunger, the diagnosis was
wrong and HARDENING organ is needed. Acceptable to find this out empirically?

## Persona honesty

Both fresh personas this round flagged their own escalation pull:
- Maciej (architecture) escalated by inventing "exhale" as new abstraction; the cut version was "widen compaction scope, stop."
- Repligate (shape) didn't escalate — the "register descent" frame is observation, not addition.
