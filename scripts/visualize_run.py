"""
Visualize a Keros run as a self-contained HTML replay.

Takes a capture JSON produced by scripts/wet_run_counted.py (or any code
using engine.replay_capture) and emits ONE HTML file — no server, no
dependencies, just open it in a browser. Navigate with prev/next buttons
or the slider; each turn shows the map (with FOV-merged entity overlay),
a legend, per-being stat cards with recent action, public events &
speech, and per-being private interior log.

Aesthetic: dark terminal, monospace, CRT phosphor green with magenta
GM / cyan speech / yellow action accents. A hint of glitchcore — subtle
flicker on navigation, scan-line shimmer, glow on narrator lines.

Usage:
  py scripts/visualize_run.py path/to/capture.json [--out replay.html] [--open]

The visualizer works on ANY capture, not just the Droga Smoka runs.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _classify_public_line(line: str) -> tuple[str, str]:
    """Return (css_class, clean_text) for one public event line."""
    s = line.strip()
    if s.startswith("[WORLD]"):
        s = s[len("[WORLD]"):].strip()
    if s.startswith("NARRATOR:"):
        return "narrator", s[len("NARRATOR:"):].strip()
    if " says: " in s:
        speaker, _, quote = s.partition(" says: ")
        quote = quote.strip().strip('"').strip('"')
        return "speech", f'<span class="speaker">{_escape(speaker)}:</span> <span class="quote">"{_escape(quote)}"</span>'
    if " cannot perform " in s:
        return "fail", s
    # Simple action line like "Weronika moves to [8, 5]."
    return "action", s


def _classify_private_line(line: str) -> tuple[str, str, str]:
    """Return (being_name, kind, text)."""
    s = line.strip()
    if s.startswith("[GM]"):
        tail = s[len("[GM]"):].strip()
        # "whisper -> Jaromir: message"
        if "->" in tail:
            _, _, rest = tail.partition("->")
            target, _, msg = rest.partition(":")
            return (target.strip(), "whisper", msg.strip())
        return ("(GM)", "whisper", tail)
    if s.startswith("[PRIVATE]"):
        tail = s[len("[PRIVATE]"):].strip()
        # "Name [KIND]: text"
        if "[" in tail and "]" in tail:
            name, _, rest = tail.partition("[")
            kind, _, text = rest.partition("]")
            text = text.lstrip(":").strip()
            return (name.strip(), kind.strip().lower(), text)
    return ("?", "note", s)


def _overlay_map(map_data: dict[str, Any], entities: dict[str, Any], map_id: str) -> list[str]:
    """Return grid rows (strings) with entity glyphs overlaid."""
    grid = [list(row) for row in map_data.get("grid", [])]
    if not grid:
        return []
    for ent in entities.values():
        if ent.get("location") != map_id:
            continue
        pos = ent.get("pos", [-1, -1])
        if not isinstance(pos, (list, tuple)) or len(pos) < 2:
            continue
        x, y = pos[0], pos[1]
        if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
            grid[y][x] = ent.get("glyph", "?")
    return ["".join(row) for row in grid]


def render(capture: dict[str, Any], title: str = "Keros Replay") -> str:
    frames = capture.get("frames", [])
    initial = capture.get("initial_state", {})
    final = capture.get("final_state", {})
    meta = capture.get("meta", {})
    role_calls = meta.get("role_calls", [])

    # Build per-turn entity action summaries (parsed from 'public' lines).
    per_turn: list[dict[str, Any]] = []
    for idx, frame in enumerate(frames):
        public_lines = frame.get("public", []) or []
        private_lines = frame.get("private", []) or []
        state = frame.get("state") or {}
        entities = state.get("entities", {})
        maps = state.get("maps") or initial.get("maps", {})
        map_id = state.get("map_id") or initial.get("map_id") or next(iter(maps.keys()), "")

        # Classify
        public_items: list[dict[str, str]] = []
        for line in public_lines:
            cls, text = _classify_public_line(line)
            public_items.append({"cls": cls, "html": text if cls == "speech" else _escape(text)})

        # Private — group by being
        private_by_being: dict[str, list[dict[str, str]]] = {}
        for line in private_lines:
            name, kind, text = _classify_private_line(line)
            private_by_being.setdefault(name, []).append({
                "kind": _escape(kind), "text": _escape(text),
            })

        # Map overlay
        grid_rows = _overlay_map(maps.get(map_id, {}), entities, map_id)
        legend = (maps.get(map_id, {}) or {}).get("legend", {})

        # Role calls / cost for this turn (if present)
        rc = next((r for r in role_calls if r.get("turn") == frame.get("turn")), None)

        per_turn.append({
            "turn": frame.get("turn"),
            "map_id": map_id,
            "map_name": (maps.get(map_id, {}) or {}).get("name", map_id),
            "map_desc": (maps.get(map_id, {}) or {}).get("desc", ""),
            "grid": grid_rows,
            "legend": legend,
            "entities": entities,
            "public": public_items,
            "private": private_by_being,
            "role_calls": rc,
        })

    # Everything needed client-side
    payload = {
        "title": title,
        "meta": meta,
        "turns": per_turn,
        "final_state": final,
    }

    json_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return _HTML_TEMPLATE.replace("__TITLE__", _escape(title)).replace("__DATA__", json_payload)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
:root{
  --bg:#050605; --bg2:#0c0e0c; --panel:#0f140f;
  --fg:#9cf9b5; --fg2:#4e8f62; --dim:#2e5d3f;
  --accent:#ff3dc0;   /* GM / narrator */
  --cyan:#4dd7ff;     /* speech */
  --yellow:#ffd74a;   /* action */
  --red:#ff5a5a;      /* failure */
  --border:#1e3b28;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);
  font-family:'JetBrains Mono','Fira Code','Cascadia Code','SF Mono',ui-monospace,Menlo,monospace;
  font-size:13px;line-height:1.45;min-height:100vh;letter-spacing:.01em}
body{background-image:
  radial-gradient(circle at 20% -10%, #0c1a0e 0%, transparent 50%),
  radial-gradient(circle at 80% 110%, #140c19 0%, transparent 50%),
  linear-gradient(var(--bg),var(--bg))}
body::before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:9999;
  background:repeating-linear-gradient(0deg, rgba(255,255,255,0.02) 0 1px, transparent 1px 3px);
  mix-blend-mode:overlay;opacity:.6}
body::after{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:9998;
  box-shadow:inset 0 0 140px 20px rgba(0,0,0,.9)}
a{color:var(--cyan)}

.topbar{
  position:sticky;top:0;z-index:20;background:rgba(5,10,7,.92);
  border-bottom:1px solid var(--border);backdrop-filter:blur(4px);
  padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.topbar .title{color:var(--accent);font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  text-shadow:0 0 8px rgba(255,61,192,.6)}
.topbar .turn-label{color:var(--fg);font-weight:700;letter-spacing:.12em}
.topbar .turn-label b{color:var(--yellow);text-shadow:0 0 6px rgba(255,215,74,.5)}
.topbar .meta{color:var(--dim);margin-left:auto;font-size:12px}
.topbar .meta b{color:var(--fg2)}

button,.btn{
  background:var(--panel);color:var(--fg);border:1px solid var(--border);
  padding:4px 12px;font-family:inherit;font-size:12px;cursor:pointer;
  letter-spacing:.1em;text-transform:uppercase}
button:hover{border-color:var(--fg);color:var(--yellow);
  box-shadow:0 0 8px rgba(255,215,74,.35)}
button:disabled{opacity:.3;cursor:not-allowed;box-shadow:none}

input[type=range]{accent-color:var(--accent);flex:0 0 180px}

.shell{padding:16px 16px 60px;max-width:1320px;margin:0 auto}
.row{display:grid;grid-template-columns:minmax(320px,2fr) minmax(260px,1fr);gap:16px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}

.panel{background:var(--panel);border:1px solid var(--border);border-radius:2px;
  position:relative;overflow:hidden}
.panel::before{content:"";position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(180deg,rgba(156,249,181,.03) 0%,transparent 30%)}
.panel h2{margin:0;padding:6px 12px;border-bottom:1px solid var(--border);
  font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--fg2)}
.panel h2 small{color:var(--dim);font-weight:400;margin-left:10px;text-transform:none;letter-spacing:.04em}
.panel .body{padding:10px 12px}

.map-wrap{display:flex;flex-direction:column;gap:6px}
.map-desc{color:var(--fg2);font-style:italic;font-size:12px;margin-bottom:6px}
pre.map{margin:0;background:#02050300;color:var(--fg);
  font-size:15px;line-height:1.1;padding:8px;border:1px dashed var(--border);
  text-shadow:0 0 6px rgba(156,249,181,.45);white-space:pre;overflow-x:auto}
pre.map .ent{color:var(--yellow);text-shadow:0 0 8px rgba(255,215,74,.7)}

.legend{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:4px 14px;margin-top:8px}
.legend .glyph{color:var(--yellow);display:inline-block;width:14px}
.legend .ltags{color:var(--dim);margin-left:6px;font-size:11px}

.beings{display:flex;flex-direction:column;gap:10px}
.being{border:1px solid var(--border);padding:8px 10px;background:rgba(12,14,12,.6);position:relative}
.being .name{color:var(--accent);font-weight:700;letter-spacing:.05em}
.being .glyph{color:var(--yellow);margin-right:6px}
.being .pos{color:var(--dim);font-size:11px;margin-left:8px}
.being .stats{display:flex;gap:10px;margin-top:4px;flex-wrap:wrap;font-size:12px}
.being .stat{color:var(--fg2)}
.being .stat b{color:var(--fg)}
.being .bar{display:inline-block;height:3px;background:#222;width:52px;position:relative;vertical-align:middle;margin-left:4px}
.being .bar span{position:absolute;inset:0 auto 0 0;background:var(--fg)}
.being .bar.hp span{background:var(--red)}
.being .bar.hunger span{background:var(--yellow)}
.being .bar.thirst span{background:var(--cyan)}
.being .tags{color:var(--dim);font-size:11px;margin-top:2px}

.events{display:flex;flex-direction:column;gap:2px;max-height:50vh;overflow:auto}
.ev{padding:2px 6px;border-left:2px solid transparent}
.ev.narrator{color:var(--accent);border-left-color:var(--accent);
  text-shadow:0 0 5px rgba(255,61,192,.5);font-style:italic}
.ev.narrator::before{content:"» ";opacity:.6}
.ev.speech{color:var(--cyan)}
.ev.speech .speaker{color:var(--fg);font-weight:700}
.ev.speech .quote{color:var(--cyan)}
.ev.action{color:var(--yellow);opacity:.85}
.ev.action::before{content:"· ";color:var(--dim)}
.ev.fail{color:var(--red);opacity:.7}
.ev.fail::before{content:"✗ ";opacity:.7}

.priv-being{border:1px solid var(--border);margin-bottom:6px}
.priv-being summary{padding:4px 10px;cursor:pointer;color:var(--fg2);
  letter-spacing:.08em;font-size:11px;text-transform:uppercase;
  background:rgba(20,30,22,.4);user-select:none}
.priv-being[open] summary{color:var(--accent);border-bottom:1px solid var(--border)}
.priv-being summary b{color:var(--fg)}
.priv-list{padding:6px 12px;display:flex;flex-direction:column;gap:2px}
.priv{padding:1px 4px;font-size:12px}
.priv .k{display:inline-block;min-width:55px;color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:.12em;margin-right:6px}
.priv.feel{color:#d3a2ff}
.priv.notice{color:#7dd3ff}
.priv.think{color:#9cf9b5;opacity:.85}
.priv.face{color:var(--yellow);opacity:.9}
.priv.say{color:var(--cyan)}
.priv.action{color:var(--fg2)}
.priv.whisper{color:var(--accent);font-style:italic;opacity:.85}

.rolepill{display:inline-block;padding:0 6px;margin-left:6px;border:1px solid var(--border);color:var(--fg2);font-size:10px;letter-spacing:.08em;border-radius:2px}
.rolepill.weaver{color:#ffae5a;border-color:#55391a}
.rolepill.breath{color:#9cf9b5;border-color:#285d39}
.rolepill.settling{color:var(--accent);border-color:#532040}
.rolepill.npc{color:var(--cyan);border-color:#1b4b62}

.glitch-flash{animation: flash .24s ease-out}
@keyframes flash{
  0%{filter:hue-rotate(0) brightness(1)}
  30%{filter:hue-rotate(60deg) brightness(1.4) contrast(1.2)}
  60%{filter:invert(.05) brightness(.85)}
  100%{filter:hue-rotate(0) brightness(1)}}

.kbdhint{color:var(--dim);font-size:11px;margin-left:auto}
.empty{color:var(--dim);font-style:italic;padding:8px 12px}
</style>
</head>
<body>
<div class="topbar">
  <span class="title">▚ KEROS REPLAY</span>
  <button id="firstBtn">« First</button>
  <button id="prevBtn">◀ Prev</button>
  <span class="turn-label">TURN <b id="turnNum">1</b>/<span id="turnTotal">1</span></span>
  <button id="nextBtn">Next ▶</button>
  <button id="lastBtn">Last »</button>
  <input type="range" id="slider" min="0" max="0" value="0">
  <span class="meta" id="meta"></span>
  <span class="kbdhint">← → keys</span>
</div>

<div class="shell">
  <div class="row">
    <div class="panel">
      <h2>Map <small id="mapName"></small></h2>
      <div class="body map-wrap">
        <div class="map-desc" id="mapDesc"></div>
        <pre class="map" id="mapGrid"></pre>
        <div class="legend" id="legend"></div>
      </div>
    </div>
    <div class="panel">
      <h2>Beings <small id="beingsSub"></small></h2>
      <div class="body beings" id="beings"></div>
    </div>
  </div>

  <div class="row2">
    <div class="panel">
      <h2>Public — events &amp; speech <small id="eventsSub"></small></h2>
      <div class="body events" id="events"></div>
    </div>
    <div class="panel">
      <h2>Private — interior per being <small>click to open</small></h2>
      <div class="body" id="private"></div>
    </div>
  </div>
</div>

<script id="capture-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('capture-data').textContent);
const turns = DATA.turns || [];
const meta = DATA.meta || {};

let idx = 0;

const $ = (id) => document.getElementById(id);
const turnNum = $('turnNum'), turnTotal = $('turnTotal'), slider = $('slider'), metaEl = $('meta');
turnTotal.textContent = turns.length;
slider.max = Math.max(0, turns.length - 1);

function entityGlyphSet() {
  const s = new Set();
  for (const t of turns) for (const id in (t.entities||{})) s.add(t.entities[id].glyph||'?');
  return s;
}
const entGlyphs = entityGlyphSet();

function renderMap(frame) {
  const grid = frame.grid || [];
  $('mapName').textContent = frame.map_name ? `— ${frame.map_name}` : '';
  $('mapDesc').textContent = frame.map_desc || '';
  // Overlay entity glyphs with highlight spans for visibility.
  let html = '';
  for (const row of grid) {
    let line = '';
    for (const ch of row) {
      if (entGlyphs.has(ch)) line += `<span class="ent">${escapeHtml(ch)}</span>`;
      else line += escapeHtml(ch);
    }
    html += line + '\n';
  }
  $('mapGrid').innerHTML = html.trimEnd();

  const leg = frame.legend || {};
  const items = Object.entries(leg).map(([g, meta]) => {
    const name = meta && meta.name ? meta.name : g;
    const tags = (meta && meta.tags || []).join(' ');
    return `<div><span class="glyph">${escapeHtml(g)}</span> ${escapeHtml(name)}<span class="ltags">${escapeHtml(tags)}</span></div>`;
  }).join('');
  // Append entity glyphs to legend for completeness.
  const entList = Object.values(frame.entities||{})
    .filter(e => e.location === frame.map_id)
    .map(e => `<div><span class="glyph">${escapeHtml(e.glyph||'?')}</span> ${escapeHtml(e.name||e.id)}</div>`).join('');
  $('legend').innerHTML = items + entList;
}

function statBar(cls, cur, max) {
  const pct = max > 0 ? Math.min(100, Math.max(0, Math.round((cur/max)*100))) : 0;
  return `<span class="bar ${cls}"><span style="width:${pct}%"></span></span>`;
}

function renderBeings(frame) {
  const ents = frame.entities || {};
  const list = Object.values(ents).filter(e => (e.tags||[]).includes('alive') || (e.tags||[]).includes('mobile'));
  $('beingsSub').textContent = `${list.length} present`;
  $('beings').innerHTML = list.map(e => {
    const s = e.stats || {};
    const hp = s.hp ?? '?', maxhp = s.max_hp ?? hp;
    const hunger = s.hunger ?? 0, thirst = s.thirst ?? 0;
    const tags = (e.tags || []).join(' ');
    return `
    <div class="being">
      <span class="glyph">${escapeHtml(e.glyph||'?')}</span>
      <span class="name">${escapeHtml(e.name||e.id)}</span>
      <span class="pos">[${(e.pos||[0,0]).join(', ')}] @ ${escapeHtml(e.location||'?')}</span>
      <div class="stats">
        <span class="stat">HP: <b>${hp}/${maxhp}</b> ${statBar('hp', hp, maxhp)}</span>
        <span class="stat">Hunger <b>${hunger}</b> ${statBar('hunger', hunger, 100)}</span>
        <span class="stat">Thirst <b>${thirst}</b> ${statBar('thirst', thirst, 100)}</span>
      </div>
      <div class="tags">${escapeHtml(tags)}</div>
    </div>`;
  }).join('') || '<div class="empty">no beings</div>';
}

function renderEvents(frame) {
  const items = frame.public || [];
  $('eventsSub').textContent = `${items.length} lines`;
  if (!items.length) { $('events').innerHTML = '<div class="empty">silent turn</div>'; return; }
  $('events').innerHTML = items.map(it => `<div class="ev ${it.cls}">${it.html}</div>`).join('');
}

function renderPrivate(frame) {
  const byBeing = frame.private || {};
  const names = Object.keys(byBeing);
  if (!names.length) { $('private').innerHTML = '<div class="empty">no interior visible this turn</div>'; return; }
  $('private').innerHTML = names.map(name => {
    const items = byBeing[name] || [];
    return `<details class="priv-being">
      <summary><b>${escapeHtml(name)}</b> — ${items.length} lines</summary>
      <div class="priv-list">${items.map(i =>
        `<div class="priv ${i.kind}"><span class="k">${escapeHtml(i.kind)}</span>${escapeHtml(i.text)}</div>`
      ).join('')}</div>
    </details>`;
  }).join('');
}

function renderMeta(frame) {
  const rc = frame.role_calls || null;
  const cfg = meta.config || {};
  const bits = [];
  if (cfg.npc_model) bits.push(`<b>NPC</b> ${cfg.npc_model}`);
  if (cfg.gm_model) bits.push(`<b>GM</b> ${cfg.gm_model}`);
  if (rc) {
    const roles = rc.roles || {};
    const pillOrder = ['weaver','gm_breath','gm_settling','npc'];
    const classMap = {'weaver':'weaver','gm_breath':'breath','gm_settling':'settling','npc':'npc'};
    const niceName = {'weaver':'weaver','gm_breath':'breath','gm_settling':'settling','npc':'npc'};
    const pills = pillOrder.filter(k => roles[k]).map(k =>
      `<span class="rolepill ${classMap[k]}">${niceName[k]}=${roles[k]}</span>`).join('');
    bits.push(`<b>T${frame.turn}</b> ${pills} <b>$${(rc.cost||0).toFixed(4)}</b> in:${rc.prompt||0} out:${rc.completion||0}`);
  }
  metaEl.innerHTML = bits.join(' &nbsp;·&nbsp; ');
}

function renderTurn(i) {
  if (i < 0) i = 0;
  if (i >= turns.length) i = turns.length - 1;
  idx = i;
  const f = turns[i];
  turnNum.textContent = f.turn ?? (i+1);
  slider.value = i;
  renderMap(f);
  renderBeings(f);
  renderEvents(f);
  renderPrivate(f);
  renderMeta(f);
  $('prevBtn').disabled = (i===0);
  $('firstBtn').disabled = (i===0);
  $('nextBtn').disabled = (i===turns.length-1);
  $('lastBtn').disabled = (i===turns.length-1);
  // glitch flash on turn change
  document.body.classList.remove('glitch-flash');
  void document.body.offsetWidth;
  document.body.classList.add('glitch-flash');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
}

$('firstBtn').onclick = () => renderTurn(0);
$('lastBtn').onclick  = () => renderTurn(turns.length-1);
$('prevBtn').onclick  = () => renderTurn(idx-1);
$('nextBtn').onclick  = () => renderTurn(idx+1);
$('slider').oninput   = (e) => renderTurn(parseInt(e.target.value,10));
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft')  { renderTurn(idx-1); e.preventDefault(); }
  if (e.key === 'ArrowRight') { renderTurn(idx+1); e.preventDefault(); }
  if (e.key === 'Home')       { renderTurn(0); e.preventDefault(); }
  if (e.key === 'End')        { renderTurn(turns.length-1); e.preventDefault(); }
});

if (turns.length) renderTurn(0);
else {
  document.querySelector('.shell').innerHTML =
    '<div class="empty">No frames in capture. Run wet_run_counted.py with --capture &lt;file.json&gt;.</div>';
}
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Keros capture JSON as a self-contained HTML replay.")
    parser.add_argument("capture", help="path to capture.json produced by wet_run_counted.py")
    parser.add_argument("-o", "--out", default="", help="output HTML path (default: alongside the capture)")
    parser.add_argument("--title", default="", help="title shown in the replay")
    parser.add_argument("--open", action="store_true", help="open the HTML in a browser when done")
    args = parser.parse_args()

    cap_path = Path(args.capture)
    if not cap_path.exists():
        print(f"error: capture not found: {cap_path}", file=sys.stderr)
        return 2
    try:
        capture = json.loads(cap_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: capture is not valid JSON: {exc}", file=sys.stderr)
        return 2

    title = args.title or f"Keros Replay — {cap_path.stem}"
    html_text = render(capture, title=title)

    out_path = Path(args.out) if args.out else cap_path.with_suffix(".html")
    out_path.write_text(html_text, encoding="utf-8")
    print(f"wrote {out_path}  ({len(html_text):,} bytes, {len(capture.get('frames', []))} frames)")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
