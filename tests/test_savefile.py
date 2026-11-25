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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
