# Quickstart

## 1. Install

```bash
pip install -r requirements.txt
```

Only the `openai` package is required. Any OpenAI-compatible endpoint works
(OpenRouter, Together, Fireworks, Anthropic via proxy, a local vLLM/Ollama
gateway, etc.).

## 2. Configure

Copy `.env.example` → `.env` and fill in your key. Or export the variables
directly:

```bash
export KEROS_API_KEY="sk-..."
export KEROS_API_BASE="https://openrouter.ai/api/v1"   # optional
export KEROS_FREE_MODELS="nvidia/nemotron-3-nano-30b-a3b:free,openai/gpt-oss-120b:free"  # optional
```

Without a key, the engine still runs — NPCs will all `wait`. Useful for
verifying loads and testing rule logic.

## 3. Run the starter

```bash
python scripts/live.py \
  --world examples/starter/world.json \
  --player wanderer \
  --turns 20 \
  --delay 2
```

What you'll see: a stone crossing with a Pedlar and a Guard. Both have
personalities, hunger, thirst, small inventories. The Guard is bitter and
underfed. The Pedlar carries bread and water. Without your intervention, the
scene will breathe for twenty turns.

## 4. Poke it

Try these:

```bash
# Enable the GM — it will nudge the scene if things stall.
python scripts/live.py -w examples/starter/world.json -p wanderer --turns 30 --enable-gm

# Save a report.
python scripts/live.py -w examples/starter/world.json -p wanderer --turns 20 --report out.txt

# Swap models.
python scripts/live.py -w examples/starter/world.json -p wanderer \
  --model-npc openai/gpt-4o-mini --model-gm openai/gpt-4o
```

## Common knobs

| Flag                | What                                                     |
|---------------------|----------------------------------------------------------|
| `--world/-w`        | path to world.json                                       |
| `--player/-p`       | entity id the player controls (use any id)               |
| `--turns`           | number of rounds to simulate                             |
| `--delay`           | seconds between LLM calls (rate-limit safety)            |
| `--enable-gm`       | run the generic GM decider each round                    |
| `--llm-radius`      | radius around player in which NPCs get an LLM prompt     |
| `--model-npc`       | model for NPCs (or `$KEROS_MODEL_NPC`)                   |
| `--model-gm`        | model for GM (or `$KEROS_MODEL_GM`)                      |
| `--report`          | write a summary to file                                  |
| `--no-color`        | disable ANSI colors                                      |

## Next

Read [EXTENDING.md](EXTENDING.md) to fork the starter into your own setting.
