"""Round 10/11 mechanics tests — exercise new code paths in isolation, no LLM.

Covers the five recent commits:
  - 5fcdbbe — state→prompt framework + death pipeline + memorial archive
  - 10a4304 — damage writes wound-trace to private_log
  - 56a0cd6 — minimal map-design items (atlas scaffold, map_gloss
              compaction whitelist, derive_atlas_code helper)
  - da25521 — auto-mirror reverse portal in create_map + `bump` primitive
  - 0c6e8b8 — resolve_entity consolidation, entity-id substring matching
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

# Path setup: tests live in <repo>/tests/, engine package in <repo>/engine/.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.engine import (  # noqa: E402
    GameEngine,
    ResolvedEntity,
    UnresolvedRef,
    resolve_entity,
)
from engine.state_aspects import (  # noqa: E402
    aspect_body_needs_climate,
    aspect_entity_count_pressure,
    aspect_recent_death_pulse,
)
from engine.worldbuilder import create_map, derive_atlas_code  # noqa: E402
from engine import runtime as runtime_module  # noqa: E402

WORLD_FILE = str(REPO_ROOT / "examples" / "droga_smoka_v3" / "world.json")


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """A fresh engine bootstrapped from the droga_smoka_v3 world bundle."""
    return GameEngine.from_world_file(WORLD_FILE)


# ---------------------------------------------------------------------------
# 1. resolve_entity — single trust boundary for LLM-supplied refs
# ---------------------------------------------------------------------------


def test_resolve_entity_seven_cases(engine):
    """Mirror of the unit-test scaffold: 7 cases covering id, lowercase,
    name, substring (gamertag-in-quoted-name), nonexistent, empty, None."""
    ents = engine.state.entities

    # 1. exact id
    r1 = resolve_entity(ents, "jaromir")
    assert isinstance(r1, ResolvedEntity)
    assert r1.id == "jaromir"
    assert r1.entity is ents["jaromir"]

    # 2. lowercase variant of id (in this world the canonical id is already
    #    lowercase; verify uppercase input resolves via the lower() path)
    r2 = resolve_entity(ents, "JAROMIR")
    assert isinstance(r2, ResolvedEntity)
    assert r2.id == "jaromir"

    # 3. exact entity name (case-insensitive)
    r3 = resolve_entity(ents, "Glut")
    assert isinstance(r3, ResolvedEntity)
    assert r3.id == "glut"

    # 4. substring inside a quoted gamertag — the bug 0c6e8b8 hunted
    r4 = resolve_entity(ents, "xXx_slayer420_Xxx")
    assert isinstance(r4, ResolvedEntity)
    assert r4.id == "weronika"

    # 5. completely unknown reference
    r5 = resolve_entity(ents, "ozzy_osbourne")
    assert isinstance(r5, UnresolvedRef)
    assert r5.reason == "no_match"
    assert r5.attempted == "ozzy_osbourne"

    # 6. empty string
    r6 = resolve_entity(ents, "")
    assert isinstance(r6, UnresolvedRef)
    assert r6.reason == "empty"

    # 7. None
    r7 = resolve_entity(ents, None)
    assert isinstance(r7, UnresolvedRef)
    assert r7.reason == "empty"


# ---------------------------------------------------------------------------
# 2. aspect_entity_count_pressure
# ---------------------------------------------------------------------------


def test_state_aspects_entity_count_pressure(engine):
    """Returns 'low' with the world's three starting souls; flips to
    'saturated' once we inflate the alive+mobile count past the
    sqrt-fallback ceiling (map has no explicit population_target)."""
    # World ships no population_target on klatka_zebrowa → fallback to
    # int(sqrt(walkable_tile_count)). The chamber has 50 walkable tiles → 7.
    assert aspect_entity_count_pressure(engine.state) == "low"

    # Manually inflate by spawning placeholder alive+mobile entities at the
    # same location until count exceeds the target.
    for i in range(20):
        sid = f"phantom_{i}"
        engine.state.entities[sid] = {
            "id": sid,
            "name": f"Phantom {i}",
            "glyph": "p",
            "tags": ["alive", "mobile"],
            "location": "klatka_zebrowa",
            "pos": [1, 1],
            "stats": {"hp": 1, "max_hp": 1},
            "inventory": [], "equipped": {}, "statuses": [],
            "seen_events": [], "private_log": [], "relations": {}, "bonds": {},
            "fov_radius": 0,
        }
    assert aspect_entity_count_pressure(engine.state) == "saturated"

    # And: an explicit population_target overrides the sqrt fallback.
    engine2 = GameEngine.from_world_file(WORLD_FILE)
    engine2.state.maps["klatka_zebrowa"]["population_target"] = 2
    # 3 starting alive+mobile vs target 2 → saturated
    assert aspect_entity_count_pressure(engine2.state) == "saturated"
    engine2.state.maps["klatka_zebrowa"]["population_target"] = 100
    assert aspect_entity_count_pressure(engine2.state) == "low"


# ---------------------------------------------------------------------------
# 3. aspect_recent_death_pulse — window filtering
# ---------------------------------------------------------------------------


def test_state_aspects_recent_death_pulse(engine):
    """Empty list → inactive; recent entry inside window → active;
    entry outside window → inactive even though present in state."""
    # No deaths yet
    pulse = aspect_recent_death_pulse(engine.state)
    assert pulse["active"] is False
    assert pulse["names"] == []
    assert pulse["turns_ago"] is None

    # Stamp a death this turn
    engine.state.flags["recent_deaths"] = [
        {"turn": engine.state.turn, "id": "glut", "name": "Glut", "location": "klatka_zebrowa"}
    ]
    pulse = aspect_recent_death_pulse(engine.state, window=2)
    assert pulse["active"] is True
    assert "Glut" in pulse["names"]
    assert pulse["turns_ago"] == 0

    # Window-filter: a record 5 turns ago with window=2 → active False.
    engine.state.turn = 10
    engine.state.flags["recent_deaths"] = [
        {"turn": 5, "id": "glut", "name": "Glut", "location": "klatka_zebrowa"}
    ]
    pulse = aspect_recent_death_pulse(engine.state, window=2)
    assert pulse["active"] is False
    assert pulse["turns_ago"] is None

    # Same record with larger window → active again
    pulse = aspect_recent_death_pulse(engine.state, window=10)
    assert pulse["active"] is True
    assert pulse["turns_ago"] == 5


# ---------------------------------------------------------------------------
# 4. aspect_body_needs_climate
# ---------------------------------------------------------------------------


def test_state_aspects_body_needs_climate(engine):
    """Label reflects population-wide hunger/thirst means. Crank a soul
    past 80 and confirm name surfaces in hot_names + label escalates."""
    # World ships with means around 43/45 → "warming" (>=40 worst channel).
    climate = aspect_body_needs_climate(engine.state)
    assert climate["label"] == "warming"
    assert climate["hot_names"] == []

    # Crank Glut's hunger to 90 → critical (>=85 on worst channel).
    engine.state.entities["glut"]["stats"]["hunger"] = 90
    climate = aspect_body_needs_climate(engine.state)
    assert climate["label"] == "critical"
    assert "Glut" in climate["hot_names"]


# ---------------------------------------------------------------------------
# 5. _handle_death pipeline
# ---------------------------------------------------------------------------


def test_death_pipeline(engine):
    """Direct call to _handle_death: tags shift, body entity mints,
    inventory spills as standalone item entities, memorial + recent_deaths
    flags both receive entries."""
    glut = engine.state.entities["glut"]
    pre_inventory = list(glut.get("inventory", []))
    assert pre_inventory  # sanity: Glut starts carrying things
    pos = list(glut["pos"])
    loc = glut["location"]

    # Death pipeline
    engine._handle_death(glut)

    # (a) alive+mobile stripped, dead added
    assert "alive" not in glut["tags"]
    assert "mobile" not in glut["tags"]
    assert "dead" in glut["tags"]

    # (b) body_of_<id>_<turn> minted at same tile
    body_keys = [k for k in engine.state.entities if k.startswith("body_of_glut_")]
    assert len(body_keys) == 1
    body = engine.state.entities[body_keys[0]]
    assert body["pos"] == pos
    assert body["location"] == loc
    assert "body" in body["tags"]
    assert "corpse" in body["tags"]
    assert "inventory_source" in body["tags"]
    assert body["name"] == "the body of Glut"

    # (c) inventory spilled as standalone entities
    spilled_ids = [
        k for k in engine.state.entities
        if any(k.startswith(f"{item_id}_dropped_") for item_id in pre_inventory)
    ]
    assert len(spilled_ids) == len(pre_inventory)
    for sid in spilled_ids:
        spilled = engine.state.entities[sid]
        assert spilled["location"] == loc
        assert spilled["pos"] == pos
        assert "item" in spilled["tags"]
        assert "dropped" in spilled["tags"]
    assert glut["inventory"] == []
    assert glut["equipped"] == {}

    # (d) memorial archive received an entry
    memorial = engine.state.flags.get("memorial")
    assert memorial and len(memorial) == 1
    entry = memorial[0]
    assert entry["id"] == "glut"
    assert entry["name"] == "Glut"
    assert entry["died_location"] == loc
    # Memorial should preserve the lore_id from gm_notes.world_name
    assert entry["lore_id"] == "Droga Smoka — Klatka Żebrowa i Szpikowy Korytarz"
    assert entry["identity_anchor"]  # carries personality forward
    assert entry["wound"]

    # (e) recent_deaths stamp for aspect_recent_death_pulse
    deaths = engine.state.flags.get("recent_deaths")
    assert deaths and len(deaths) == 1
    assert deaths[0]["id"] == "glut"
    assert deaths[0]["turn"] == engine.state.turn


# ---------------------------------------------------------------------------
# 6. _effect_damage writes wound-trace to private_log
# ---------------------------------------------------------------------------


def test_damage_logs_wound(engine):
    """Damage >=10% of max_hp logs a typed `wound` entry into private_log.
    Below 10% logs nothing. Severity word scales with the percent."""
    jaromir = engine.state.entities["jaromir"]
    max_hp = jaromir["stats"]["max_hp"]  # 32

    def damage_and_get_wound(amount: int):
        # Snapshot private_log length, restore hp, fire the effect.
        before = len(jaromir.get("private_log", []))
        jaromir["stats"]["hp"] = max_hp
        ctx = {"actor": jaromir, "target": jaromir, "result": {}, "action": {}}
        engine._effect_damage({"target": "target", "value": amount}, ctx, 0)
        after = jaromir.get("private_log", [])
        new_entries = after[before:]
        wounds = [e for e in new_entries if e.get("type") == "wound"]
        return wounds[0] if wounds else None

    # 5% of 32 = 1.6 → below threshold, no wound logged
    w = damage_and_get_wound(1)
    assert w is None

    # 12% (4/32) → "lightly"
    w = damage_and_get_wound(4)
    assert w is not None
    assert "lightly" in w["text"]

    # 25% (8/32) → "noticeably"
    w = damage_and_get_wound(8)
    assert w is not None
    assert "noticeably" in w["text"]

    # 40% (13/32) → "deeply"
    w = damage_and_get_wound(13)
    assert w is not None
    assert "deeply" in w["text"]

    # 60% (20/32) → "savagely"
    w = damage_and_get_wound(20)
    assert w is not None
    assert "savagely" in w["text"]


# ---------------------------------------------------------------------------
# 7. atlas scaffold seeded at world-load
# ---------------------------------------------------------------------------


def test_atlas_scaffold(engine):
    """state.flags['atlas'] always present after from_world_file; when the
    world has no `atlas` key, the scaffold is {'nodes': {}, 'edges': []}."""
    atlas = engine.state.flags.get("atlas")
    assert atlas is not None
    assert atlas == {"nodes": {}, "edges": []}

    # Also confirm explicit-shape pass-through: if a world DID ship an
    # `atlas` block we'd surface it. We simulate by re-loading with a patch
    # via a wrapped loader. Easier: just check the key existence on the
    # actual world-load path stays stable.
    assert isinstance(atlas["nodes"], dict)
    assert isinstance(atlas["edges"], list)


# ---------------------------------------------------------------------------
# 8. derive_atlas_code — collision ordering
# ---------------------------------------------------------------------------


def test_derive_atlas_code():
    """Letter-size before digit per bio-Maciej's spec:
       ms → Ms → ms2 → Ms2 → ms3 → ..."""
    existing: set[str] = set()

    c1 = derive_atlas_code("mine shaft", existing)
    assert c1 == "ms"
    existing.add(c1)

    c2 = derive_atlas_code("mine shaft", existing)
    assert c2 == "Ms"
    existing.add(c2)

    c3 = derive_atlas_code("mine shaft", existing)
    assert c3 == "ms2"
    existing.add(c3)

    c4 = derive_atlas_code("mine shaft", existing)
    assert c4 == "Ms2"
    existing.add(c4)

    c5 = derive_atlas_code("mine shaft", existing)
    assert c5 == "ms3"


# ---------------------------------------------------------------------------
# 9. bump primitive — engine.act with verb='bump'
# ---------------------------------------------------------------------------


def test_bump_primitive(engine):
    """Position Jaromir adjacent to the rib_gap_portal, fire bump with the
    substring 'rib-gap', and confirm portal traversal — soul's location
    flips and pos moves to the destination tile."""
    jaromir = engine.state.entities["jaromir"]
    rib_gap = engine.state.entities["rib_gap_portal"]
    portal_dest_map = rib_gap["stats"]["portal_map"]
    portal_dest_pos = list(rib_gap["stats"]["portal_pos"])

    # Position adjacent (manhattan == 1) — door_bump requires adjacency.
    jaromir["pos"] = [rib_gap["pos"][0] - 1, rib_gap["pos"][1]]
    assert jaromir["location"] == "klatka_zebrowa"

    ok = engine.act("jaromir", {"verb": "bump", "args": ["rib-gap"]})
    assert ok is True

    # Traversal landed Jaromir on the destination map at the portal's
    # registered portal_pos.
    assert jaromir["location"] == portal_dest_map
    assert jaromir["pos"] == portal_dest_pos
    assert engine.state.current_map_id == portal_dest_map


# ---------------------------------------------------------------------------
# 10. map_gloss is in compaction's ALLOWED_PERSONALITY whitelist
# ---------------------------------------------------------------------------


def test_map_gloss_compaction_whitelist():
    """ALLOWED_PERSONALITY is a local set inside compact_memory; inspect
    the source to confirm map_gloss landed in the whitelist (commit 56a0cd6)."""
    src = inspect.getsource(runtime_module.compact_memory)
    # Find the literal set definition block.
    assert "ALLOWED_PERSONALITY" in src
    # `map_gloss` must appear inside the same function body — the whitelist.
    assert '"map_gloss"' in src or "'map_gloss'" in src
    # And `wound` (commit 10a4304 leans on this — confirm pre-existing entry).
    assert '"wound"' in src or "'wound'" in src


# ---------------------------------------------------------------------------
# 11. create_map auto-mirrors a reverse portal
# ---------------------------------------------------------------------------


def test_create_map_auto_mirrors_reverse_portal(engine):
    """Mock the worldbuilder LLM to return a map JSON whose single portal
    points back at an existing map. The auto-mirror branch in create_map
    should mint a reverse portal in the destination map without any
    explicit connect_to argument."""
    # Pre-state: only the world's hand-authored two portals exist in
    # klatka_zebrowa pointing at szpikowy_korytarz.
    initial_klatka_portals = [
        eid for eid, e in engine.state.entities.items()
        if e.get("location") == "klatka_zebrowa"
        and "portal" in e.get("tags", [])
        and e.get("stats", {}).get("portal_map") == "marrow_corridor"
    ]
    assert initial_klatka_portals == []

    new_map_id = "marrow_corridor"
    fake_map_json = (
        '{\n'
        f'  "id": "{new_map_id}",\n'
        '  "name": "Marrow Corridor",\n'
        '  "desc": "A test corridor minted by the unit-test stub.",\n'
        '  "population_target": 3,\n'
        '  "grid": ["#####", "#...#", "#...#", "#####"],\n'
        '  "legend": {\n'
        '    "#": {"name": "wall", "tags": ["solid", "opaque"]},\n'
        '    ".": {"name": "marrow-silt", "tags": ["walkable"]}\n'
        '  },\n'
        '  "portals": [\n'
        '    {\n'
        f'      "id": "portal_{new_map_id}_back",\n'
        '      "name": "Passage back",\n'
        '      "glyph": "+",\n'
        '      "tags": ["door", "portal", "closed", "solid"],\n'
        '      "stats": {\n'
        '        "portal_map": "klatka_zebrowa",\n'
        '        "portal_pos": [1, 1],\n'
        '        "open_message": "It opens.",\n'
        '        "portal_message": "You step through."\n'
        '      },\n'
        '      "pos": [3, 2],\n'
        f'      "location": "{new_map_id}"\n'
        '    }\n'
        '  ]\n'
        '}\n'
    )

    def fake_llm(system, user, model, temperature):  # noqa: ARG001
        return fake_map_json

    result = create_map(
        engine,
        sketch="a marrow corridor for testing",
        connect_to=None,
        connect_pos=None,
        llm_call=fake_llm,
    )
    # Sanity: parse succeeded
    assert isinstance(result, dict), f"create_map returned: {result!r}"
    assert new_map_id in engine.state.maps

    # The LLM-supplied portal lives in the new map and points at klatka.
    forward_portal = engine.state.entities[f"portal_{new_map_id}_back"]
    assert forward_portal["location"] == new_map_id
    assert forward_portal["stats"]["portal_map"] == "klatka_zebrowa"

    # Auto-mirror: a reverse portal now exists in klatka_zebrowa pointing
    # back at the new map.
    mirrors = [
        e for e in engine.state.entities.values()
        if e.get("location") == "klatka_zebrowa"
        and "portal" in e.get("tags", [])
        and e.get("stats", {}).get("portal_map") == new_map_id
    ]
    assert len(mirrors) == 1, (
        "expected exactly one auto-mirrored reverse portal in klatka_zebrowa"
    )
    mirror = mirrors[0]
    assert mirror["pos"] is not None
    # Mirror lands on a walkable tile of klatka_zebrowa.
    klatka = engine.state.maps["klatka_zebrowa"]
    mx, my = mirror["pos"]
    glyph = klatka["grid"][my][mx]
    tile_tags = klatka["legend"].get(glyph, {}).get("tags", [])
    assert "walkable" in tile_tags


# ---------------------------------------------------------------------------
# Module entry — when run as `python tests/test_round10_11_mechanics.py`
# we hand off to pytest so the file is self-running.
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
