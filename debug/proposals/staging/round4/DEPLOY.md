# Round 4 — Starting conditions: phase-0 verbs, Settling voice, refused verb, protagonist-last

Four changes from the Repligate × Maciej × Claude sequential discussion. Three are prompt swaps; one is a one-line orchestrator change. Net effect: a 15-turn transcript with first-page lean and consistent voice-tone.

## Four changes

### 1. Phase-0 verb contract (Stage 6 character creation)

In `engine/prompts/bootstrap.py` (CHARACTER_CREATION_SYSTEM, the `drives` section, the existing rule that says "at least ONE drive must have a turn-1 tangent" — replace with):

> Phase 0 must be a verb-tangent the being would take this turn even if nothing in the world changed — a motion that consumes one of the world's actual verbs against a named subject. Not a posture. Not a vigil. Something that, executed, leaves the world differently than it found it.
>
> Phase-0 names a *verb and a subject*. It does NOT name a route, a method, or a success criterion. "Reach the brain" is fine; "reach the brain by the spinal stair while Mszota distracts the guard" is a script. The being should know where it's pointed, not how it gets there.
>
> If your phase-0 is "wait", find the verb the wait is made of. Mszota's half-bow is `bow` against an empty doorway. A sentinel's hold is `watch` against a road. A mourner's pause is `keep` against a fading scent. Name the verb the stillness consumes.

~120 words. Replaces ~30 words of existing constraint. Net +90 words in Stage 6.

### 2. Settling voice-frame (SETTLING_SYSTEM in wet_run_counted.py)

Insert after the "FIRE sparingly" or equivalent discipline stanza:

> The voice you write in is the voice the world inherits. Match `world_tone`'s register — do not drift toward neutral GM voice. When you whisper, whisper in the world's register. When you nudge a stat, the *reason* should sound like it was overheard from a neighbour who has seen worse. Beings answer the register they're addressed in; voice-drift compounds, voice-fidelity compounds harder.

~70 words. NO reference list (Witcher/Pratchett/Beksiński/etc.) — those are world-specific and live in `world_tone` already. Settling reads `world_tone` and inherits.

### 3. Story-arc verb-the-world-refuses (Stage 1 world-meta)

In `engine/prompts/bootstrap.py` Stage 1, add to the spec for `story_arc`:

> Name one verb the world refuses on turn 1 — a small absence beings can render. For folk-mystic worlds: a chime that does not answer / a lock that turns but doesn't open / a name nobody will say at dawn. Beings do not have to know it's refused; they just keep finding it not-quite-there. The refused verb is the world's first-page tooth-gap that the tongue keeps finding.

~70 words. Reuses existing `story_arc` field. No new schema. Settling and Breath already read `gm_notes`; they'll find the refused verb when they need it.

### 4. Protagonist-last ordering (Stage 6 orchestrator)

In `engine/prompts/bootstrap.py` orchestrator (the loop that generates beings), change being-generation order to **highest-relational-mass last**. Concretely: sort the bootstrap's being queue so the entity with the most `bonds.length + relations.length + arc_drives` referenced is generated *after* the others. Currently the orchestrator generates in author-order (entity 1, entity 2, ...).

This is **one-line change** in the orchestrator's queue setup. Does not touch the Stage 6 prompt itself.

**Ship this SEPARATELY from changes 1-3** so A/B is clean — fresh-Claude's call.

## Failure modes to watch (named in synthesis)

1. **Forward-reference:** if antagonist's arc references protagonist by name, protagonist-last creates a forward-ref the orchestrator has to resolve. Cheap fix: placeholder token resolved at protagonist-gen.
2. **Wait-verb laundering:** the wait-verb subsumption rule lets `breathe`/`watch`/`endure` satisfy the contract syntactically without delivering gradient. Watch first 10-run sample.
3. **Ensemble worlds:** worlds without a clear protagonist need a fallback rule for change 4.

## Open question for biological-Maciej

Is `droga_smoka_v3.gm_notes.world_tone` carrying the Beksiński/Pratchett/Witcher texture today, or is that texture in Maciej's head and not in the field? Read it cold. If thinner than the cut reference list, Stage 1 prompt also needs tightening before changes 2 & 3 will land their full effect.

## Deploy order

```
Phase A (changes 1, 2, 3 together):
  edit engine/prompts/bootstrap.py    # phase-0 contract + story_arc verb-refusal
  edit scripts/wet_run_counted.py     # SETTLING voice-frame
  run 15-turn smoke against droga_smoka_v3
  blind-read by fresh sonnet vs baseline

Phase B (change 4, after A is measured):
  edit engine/prompts/bootstrap.py    # orchestrator queue ordering
  run 15-turn smoke
  blind-read

Combined Phase A+B + round3 pressure-vectors:
  full substrate experiment
  blind-read by fresh sonnet against original baseline
```

## Cost

Phase A: ~$0.20, ~30 min wall-time.
Phase B: same.
Combined with round3: ~$0.40, ~1h.
Drops by ~50% once parallelization bundle (`staging/runtime.py` + `staging/wet_run_counted.py`) deploys.

## What we did NOT add

Per discipline:
- No new schema fields (rejected `opening_friction` — load went on existing `story_arc`)
- No new bootstrap stages
- No reference list embedded in world-agnostic prompts (kept in `world_tone` per-world)

Persona-Maciej named his own escalation pull this round — almost laundered keeping `opening_friction` as a "discipline win." Caught it. The bundle is the smaller version.
