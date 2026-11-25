"""Run: python3 -m pytest tests  (or python3 tests/test_savefile.py)

Synthetic tests always run. Point MEWDECK_TEST_SAVE at a real .sav to also run the
round-trip tests against it (a temp copy is used; the original is never touched)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MEWDECK_BACKUP_DIR", tempfile.mkdtemp(prefix="mewdeck-bk-"))

from mewdeck import savefile as sf  # noqa: E402

STRIKES = ["2alt1", "2alt2", "3alt2", "3alt3"]


def make_synthetic(path: Path, blob_props: bool = False, wal: bool = False) -> None:
    c = sqlite3.connect(path)
    if wal:
        c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
        CREATE TABLE cats (key INTEGER PRIMARY KEY,data BLOB) STRICT;
        CREATE TABLE furniture (key INTEGER PRIMARY KEY,data BLOB) STRICT;
        CREATE TABLE files (key TEXT PRIMARY KEY,data BLOB) STRICT;
        CREATE TABLE properties (key TEXT PRIMARY KEY,data ANY) STRICT;
    """)
    enc = (lambda v: str(v).encode()) if blob_props else (lambda v: v)
    for k, v in {"savescumlocation": 1, "PlotFlag_StevenMet": 1, "on_adventure": 1, "current_day": 50,
                 "house_gold": 10, "house_food": 20}.items():
        c.execute("INSERT INTO properties VALUES (?,?)", (k, enc(v)))
    for s in STRIKES:
        c.execute("INSERT INTO properties VALUES (?,1)", (sf.STRIKE_PREFIX + s,))
    c.commit()
    c.close()


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="mewdeck-t-")) / "steamcampaign01.sav"


def test_steven_cap_int_props():
    p = _tmp()
    make_synthetic(p)
    sf.apply_changes(p, sf.Changes(steven="cap"))
    c = sqlite3.connect(p)
    assert c.execute("SELECT data, typeof(data) FROM properties WHERE key='savescumlocation'").fetchone() == (0, "integer")
    keys = [r[0] for r in c.execute("SELECT key FROM properties WHERE key LIKE 'NPCRSTRACKER%'")]
    assert keys == [sf.STRIKE_PREFIX + "2alt1"]


def test_steven_clear_blob_props_keeps_type():
    p = _tmp()
    make_synthetic(p, blob_props=True)
    sf.apply_changes(p, sf.Changes(steven="clear", props={"house_gold": 999}))
    c = sqlite3.connect(p)
    assert c.execute("SELECT data FROM properties WHERE key='savescumlocation'").fetchone()[0] == b"0"
    assert c.execute("SELECT data FROM properties WHERE key='house_gold'").fetchone()[0] == b"999"
    assert c.execute("SELECT COUNT(*) FROM properties WHERE key LIKE 'NPCRSTRACKER%'").fetchone()[0] == 0


def test_wal_save_with_uncheckpointed_data():
    p = _tmp()
    make_synthetic(p, wal=True)
    hold = sqlite3.connect(p)  # keep a reader open so the WAL isn't checkpointed on close
    w = sqlite3.connect(p)
    w.execute("PRAGMA wal_autocheckpoint=0")
    w.execute("UPDATE properties SET data=77 WHERE key='house_food'")
    w.commit()
    w.close()
    hold.close()
    assert sf.snapshot(p).parsed["basic"]["house_food"] == 77
    sf.apply_changes(p, sf.Changes(steven="cap"))
    c = sqlite3.connect(p)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("SELECT data FROM properties WHERE key='house_food'").fetchone()[0] == 77
    assert c.execute("SELECT data FROM properties WHERE key='savescumlocation'").fetchone()[0] == 0


def test_backup_and_restore():
    p = _tmp()
    make_synthetic(p)
    sf.apply_changes(p, sf.Changes(steven="clear"))
    backups = sf.list_backups(p)
    assert backups and backups[0].penalty == 1
    sf.restore_backup(p, backups[0].path)
    assert sf.snapshot(p).steven.penalty == 1


def test_guard_blocks_game_writes_and_survives_edits():
    p = _tmp()
    make_synthetic(p)
    sf.apply_changes(p, sf.Changes(steven="cap", guard=True))
    c = sqlite3.connect(p)
    # what the game does when a battle starts
    c.execute("INSERT OR REPLACE INTO properties (key, data) VALUES ('savescumlocation', 1)")
    c.execute("UPDATE properties SET data = 3 WHERE key = 'savescumlocation'")
    c.execute("INSERT OR REPLACE INTO properties (key, data) VALUES ('house_gold', 55)")  # other writes still work
    c.commit()
    c.close()
    st = sf.snapshot(p).steven
    assert st.guarded and st.penalty == 0
    assert sf.snapshot(p).parsed["basic"]["house_gold"] == 55
    sf.apply_changes(p, sf.Changes(props={"house_food": 5}))  # unrelated edit keeps the triggers
    assert sf.snapshot(p).steven.guarded
    sf.apply_changes(p, sf.Changes(guard=False))
    c = sqlite3.connect(p)
    c.execute("INSERT OR REPLACE INTO properties (key, data) VALUES ('savescumlocation', 2)")
    c.commit()
    c.close()
    st = sf.snapshot(p).steven
    assert not st.guarded and st.penalty == 2 and st.where == "house boss"


def _real() -> Path | None:
    src = os.environ.get("MEWDECK_TEST_SAVE")
    if not src:
        return None
    p = _tmp()
    shutil.copy(src, p)
    return p


def test_real_save_roundtrip():
    p = _real()
    if not p:
        return
    snap = sf.snapshot(p)
    assert not snap.parsed["_debug"]["parse_errors"]
    cat = next(c for c in snap.parsed["cats"] if sf.cat_editable(snap, c)["level"])
    ch = sf.Changes(props={"house_gold": 4242}, add_items=["Clover"], steven="cap",
                    cats={cat["key"]: {**sf.cat_change_base(snap, cat), "stats": {k: 7 for k in cat["stats"]},
                                       "level": min(cat["level"] + 1, 20), "age": 1}})
    sf.apply_changes(p, ch)
    s2 = sf.snapshot(p)
    c2 = next(c for c in s2.parsed["cats"] if c["key"] == cat["key"])
    assert c2["stats"] == {k: 7 for k in cat["stats"]} and c2["age"] == 1
    assert s2.parsed["basic"]["house_gold"] == 4242 and not s2.steven.active


def test_real_save_bad_edit_is_refused():
    p = _real()
    if not p:
        return
    before = p.read_bytes()
    snap = sf.snapshot(p)
    cat = snap.parsed["cats"][0]
    bad = sf.Changes(cats={cat["key"]: {**sf.cat_change_base(snap, cat), "stats": {k: 10 for k in cat["stats"]}}})
    try:
        sf.apply_changes(p, bad)
    except RuntimeError as e:
        assert "verification failed" in str(e)
    else:
        raise AssertionError("out-of-range stats should be refused")
    assert p.read_bytes() == before


def test_real_save_full_editors_and_heal_all():
    from mewdeck import gamedata as gd
    p = _real()
    if not p:
        return
    snap = sf.snapshot(p)
    assert len(snap.parsed["_debug"]["parse_errors"]) == 0
    dead_cat = snap.parsed["graveyard"][0]
    adv_cat = next(c for c in snap.parsed["cats"] if c["room"] == "(ADVENTURE)")
    ch = sf.Changes(
        props={"house_gold": 9999, "adventure_coins": 555, "blank_collars": 7},
        cats={
            adv_cat["key"]: {
                "stats": {k: 7 for k in adv_cat["stats"]},
                "class": "Mage",
                "abilities": {
                    "active": ["DefaultMove", "BasicMagicShortRanged", "FireShot", "FreezeRay", "MageSwap", "Dash2"],
                    "passive": ["BloodLust", "Overload"],
                    "passive_tiers": [2, 2],
                    "disorder": [None, None],
                },
                "mutations": {"coat": 300, "body": 301, "eyeL": 300, "eyeR": 300},
                "equipment": {"head": "ScrapHat", "weapon": "GlassShard", "trinket": "RatTail"},
            },
            dead_cat["key"]: {"revive": True},
        },
        inv_add={"storage": ["ScrapHat"], "backpack": ["FoodBig"], "trash": []},
        furn_add=[next(iter(gd.furniture()))],
        heal_all=True,
        steven="cap",
    )
    sf.apply_changes(p, ch, reason="full-test")
    s2 = sf.snapshot(p)
    assert not any(c["injured"] or c["wounded"] for c in s2.parsed["cats"])
    assert s2.steven.penalty == 0 and s2.steven.dejavu_cats == 0
    assert s2.parsed["all_cats"][dead_cat["key"]]["room"] == "Attic"
    assert s2.parsed["all_cats"][adv_cat["key"]]["abilities"]["passive_tiers"] == [2, 2]
    assert s2.parsed["all_cats"][adv_cat["key"]]["equipment"]["trinket"]["item_id"] == "RatTail"


def test_real_save_v12_features():
    from mewdeck import gamedata as gd
    p = _real()
    if not p:
        return
    ok, notes = sf.check_integrity(p)
    assert ok, notes
    props = sf.raw_int_properties(p)
    assert len(props) > 50

    snap = sf.snapshot(p)
    # Zero raw '#' mutation names on active cats
    for c in snap.parsed["cats"]:
        for k, v in c["mutations"].items():
            cat = "texture" if k == "coat" else gd.MUTATION_SLOT_BY_KEY[k][1]
            info = gd.mutation_info(cat, v)
            assert not info["name"].startswith("#"), (k, v, info)

    house_cat = next(c for c in snap.parsed["cats"] if c["room"] not in ("", "(ADVENTURE)"))
    other_room = next(r for r in snap.parsed["rooms"] if r != house_cat["room"])
    stor_item = snap.parsed["items"]["storage"][0]
    ch = sf.Changes(
        cats={house_cat["key"]: {"name": "Sir Whiskers the Great", "level_bonuses": {k: 2 for k in house_cat["stats"]}}},
        room_moves={house_cat["key"]: other_room},
        inv_move=[("storage", "backpack", int(stor_item["instance_id"]))],
        repair_all=True,
    )
    sf.apply_changes(p, ch, reason="v12-test")
    s2 = sf.snapshot(p)
    c2 = s2.parsed["all_cats"][house_cat["key"]]
    assert c2["name"] == "Sir Whiskers the Great"
    assert c2["room"] == other_room
    assert c2["level_bonuses"]["STR"] == 2
    assert any(int(x["instance_id"]) == int(stor_item["instance_id"]) for x in s2.parsed["items"]["backpack"])


def test_real_save_pedigree_and_breeding_organizer():
    """Verify pedigree CoI parsing, wiki item icon atlas, and breeding room optimizer round-trip."""
    from mewdeck import breeding as br, gamedata as gd
    real = os.environ.get("MEWDECK_TEST_SAVE")
    if not real or not Path(real).is_file():
        return
    # Verify wiki item icon atlas exists and maps >95% of catalog items
    atlas_png = gd.DATA_DIR / "items/item_icons.png"
    atlas_json = gd.DATA_DIR / "items/item_icons.json"
    assert atlas_png.is_file() and atlas_png.stat().st_size > 100_000
    import json
    meta = json.loads(atlas_json.read_text(encoding="utf-8"))
    assert len(meta["icons"]) >= 950

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "campaign.sav"
    shutil.copy2(real, p)
    snap = sf.snapshot(p)
    ped = snap.parsed["pedigree"]
    assert len(ped["parents"]) > 0 and len(ped["pair_coi"]) > 0

    specs = br.compute_room_specs(snap.parsed)
    assert len(specs) >= 1
    res = br.optimize_house(snap.parsed, specs, goal_id="balanced")
    assert res.total_ens > 0
    if res.moves:
        ch = sf.Changes(room_moves=dict(res.moves))
        sf.apply_changes(p, ch, reason="breeding-opt")
        s2 = sf.snapshot(p)
        for ck, want_room in res.moves.items():
            assert s2.parsed["all_cats"][ck]["room"] == want_room


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
