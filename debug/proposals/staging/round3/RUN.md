# Pressure-vector experiment — staged, ready to run

Goal: blind-test whether a substrate seeded with named pressure-vectors
+ a climate-keeper Settling produces a more readable transcript than the
current substrate (15-turn baseline at `reports/ds3_15turn.json`).

## What this round ships

- `world_with_pressures.json` — copy of `examples/droga_smoka_v3/world.json`
  with 8 hand-authored pressure-vectors under a new top-level `pressures: [...]`
- `breath_addendum.txt` — ~70 words to append to `BREATH_SYSTEM`
- `settling_addendum.txt` — ~270 words to append to `SETTLING_SYSTEM`
  (resolve-by-transformation + climate-keeper + pressure-interaction sections)

## Deploy steps (run AFTER the 100-turn baseline lands)

```bash
cd C:/Users/User/Documents/Keros/RogueLLM/rogueLLM

# 1. Swap world.json for the pressure-augmented version
cp debug/proposals/staging/round3/world_with_pressures.json examples/droga_smoka_v3/world.json

# 2. Manually apply addenda to scripts/wet_run_counted.py
#    Append breath_addendum.txt content into BREATH_SYSTEM string (~line 259)
#    Append settling_addendum.txt content into SETTLING_SYSTEM string (~line 292)
#    (Could automate, but manual is safer for first run.)

# 3. Run the experiment with the same seed/config as the baseline
set -a && . ./.env && set +a
py scripts/wet_run_counted.py \
   -w examples/droga_smoka_v3/world.json \
   --turns 15 \
   --model deepseek/deepseek-v4-flash \
   --model-gm deepseek/deepseek-v4-pro \
   --enable-gm --enable-weaver \
   --capture reports/ds3_15turn_pressures.json \
   --delay 0
```

## Blind comparison

After the new capture lands, dispatch a **fresh sonnet subagent** (no prior
exposure to either capture) with prompt:

> "You will read two 15-turn transcripts of the same world (`droga_smoka_v3`)
> with the same characters and same opening. They differ only in what was
> seeded. Read both. Score each on:
> - **gradient-density turns 1-3** (count of distinct tensions touched per
>   turn; proxy for the 'first-page lean')
> - **time-to-first-consequential-action** (turn until a being acts on a
>   tension vs on weather)
> - **Chekhov-economy vs surprise-spotting** (does the transcript reward
>   re-reading? do early threads land later?)
> - **prose voice match to lore** (does the narration carry the world's
>   tonal climate consistently?)
>
> Don't be told which is which. Pick the one you'd rather read again."

Two metrics that matter most: (a) does the new transcript ship with a
"first-page lean" the baseline lacked? (b) does the climate-keeper Settling
produce voice-match the baseline missed?

## Failure modes to watch for

- **Leakage:** beings reference pressures by name (e.g., "I feel pressure
  p3"). If this happens, BREATH/SETTLING addenda need tightening.
- **Dead substrate:** any pressure stays at constant magnitude all 15 turns.
  Means the substrate didn't take.
- **Over-determination:** a pressure resolves through exactly the
  resolvability the prose hinted at, with no surprise. Means the prose was
  too narrow; need to widen.

## What this experiment doesn't test (intentionally)

- Bootstrap pipeline change (Stage 1.5 pressure-extraction) — still authored
  by hand. Can the substrate even use pressures? Test that first; only then
  invest in extraction.
- Pressure-genealogy (`successor_seed`) propagation — staged in two
  pressures (p2, p8) but Weaver doesn't yet promote successors. Future round.
- Bound_pressures on beings — beings reference pressures implicitly via
  `sink`. Not making that explicit until we know the substrate works.

## Cost

~$0.20, ~30 min wall time at current sequential speed. Drops to ~$0.10 /
~10 min once the parallelization bundle from `staging/` (NOT round3/) is
also deployed.
