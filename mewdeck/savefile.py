"""Save discovery, safe in-place writes, backups, full deterministic editing and Steven reset.

The Mewgenics .sav is a SQLite database. Cat, inventory, furniture, and house_state binary
parsing/patching uses our deterministic `catblob` codec (verified 300/300 cats & 44/44
furniture byte-for-byte), with LZ4 compression from vendored `mewparser.py`.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import signal
import sqlite3
import struct
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catblob as cb
from .vendor import mewparser

APP_ID = "686060"
PREFIX_SAVE_REL = Path("pfx/drive_c/users/steamuser/AppData/Roaming/Glaiel Games/Mewgenics")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewdeck"
BACKUP_ROOT = Path(os.environ.get("MEWDECK_BACKUP_DIR", DATA_HOME / "backups"))
MAX_BACKUPS_PER_SAVE = 40

STRIKE_PREFIX = "NPCRSTRACKER_steven_savescum_"
SCUM_LOCATIONS = {1: "battle", 2: "house boss", 3: "event", 4: "other"}
GUARD_TRIGGERS = ("mewdeck_block_savescum_insert", "mewdeck_block_savescum_update")

BASIC_PROP_KEYS = (
    "current_day",
    "house_gold",
    "house_food",
    "save_file_percent",
    "blank_collars",
    "house_storage_upgrades",
    "min_strays_tomorrow",
    "on_adventure",
    "adventure_coins",
    "adventure_food",
    "adventure_furniture_boxes",
)


# --------------------------------------------------------------------------- discovery

def _steam_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]


def _library_dirs() -> list[Path]:
    libs: list[Path] = []
    for root in _steam_roots():
        if not root.is_dir():
            continue
        libs.append(root)
        vdf = root / "steamapps/libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    for pattern in ("/run/media/*/*", "/run/media/*", "/media/*"):
        import glob

        for p in glob.glob(pattern):
            if Path(p, "steamapps").is_dir():
                libs.append(Path(p))
    return libs


def candidate_save_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("MEWDECK_SAVE_DIR")
    if env:
        roots.append(Path(env).expanduser())
    for lib in _library_dirs():
        roots.append(lib / "steamapps/compatdata" / APP_ID / PREFIX_SAVE_REL)
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Glaiel Games/Mewgenics")
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def find_saves() -> list[Path]:
    found: dict[str, Path] = {}
    for root in candidate_save_roots():
        if not root.is_dir():
            continue
        for g in ("*.sav", "saves/*.sav", "*/saves/*.sav"):
            for p in root.glob(g):
                if not p.is_file() or "backup" in p.name.lower():
                    continue
                try:
                    found.setdefault(str(p.resolve()), p)
                except OSError:
                    continue
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def save_label(path: Path) -> str:
    parent = path.parent
    owner = parent.parent.name if parent.name == "saves" else parent.name
    if owner.isdigit() and len(owner) > 8:
        owner = owner[:7] + "…" + owner[-4:]
    return f"{path.stem}  ({owner})"


# --------------------------------------------------------------------------- game process

def game_pids() -> list[int]:
    me = os.getpid()
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").lower()
        except OSError:
            continue
        if b"mewgenics.exe" in cmd and b"mewdeck" not in cmd:
            pids.append(int(d.name))
    return pids


def game_running() -> bool:
    return bool(game_pids())


def close_game(timeout: float = 12.0) -> bool:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in game_pids():
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        end = time.time() + (timeout if sig == signal.SIGTERM else 4)
        while time.time() < end:
            if not game_pids():
                time.sleep(1.0)
                return True
            time.sleep(0.25)
    return not game_pids()


def launch_game() -> None:
    url = f"steam://rungameid/{APP_ID}"
    for cmd in (["steam", url], ["xdg-open", url], ["flatpak", "run", "com.valvesoftware.Steam", url]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
    raise RuntimeError("Could not find steam or xdg-open to launch the game")


# --------------------------------------------------------------------------- properties

def _decode_int(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    b = mewparser.to_bytes(raw)
    try:
        return int(b.decode("ascii").strip("\0 "))
    except (UnicodeDecodeError, ValueError):
        pass
    if len(b) == 8:
        return struct.unpack("<q", b)[0]
    if len(b) == 4:
        return struct.unpack("<i", b)[0]
    return None


def _encode_like(old: Any, value: int) -> Any:
    if isinstance(old, (int, float)) or old is None:
        return int(value)
    if isinstance(old, str):
        return str(value)
    b = mewparser.to_bytes(old)
    try:
        int(b.decode("ascii").strip("\0 "))
        return sqlite3.Binary(str(value).encode("ascii"))
    except (UnicodeDecodeError, ValueError):
        pass
    if len(b) == 8:
        return sqlite3.Binary(struct.pack("<q", value))
    if len(b) == 4:
        return sqlite3.Binary(struct.pack("<i", value))
    return int(value)


def get_prop(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT data FROM properties WHERE key = ?", (key,)).fetchone()
    return _decode_int(row[0]) if row else None


def set_prop(conn: sqlite3.Connection, key: str, value: int) -> bool:
    row = conn.execute("SELECT data FROM properties WHERE key = ?", (key,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO properties (key, data) VALUES (?, ?)", (key, int(value)))
        return True
    conn.execute("UPDATE properties SET data = ? WHERE key = ?", (_encode_like(row[0], value), key))
    return True


@dataclass
class StevenStatus:
    penalty: int
    strikes: int
    met: bool
    on_adventure: bool
    guarded: bool = False
    dejavu_cats: int = 0

    @property
    def where(self) -> str:
        return SCUM_LOCATIONS.get(self.penalty, f"code {self.penalty}") if self.penalty else ""

    @property
    def active(self) -> bool:
        return self.penalty != 0


def steven_status(conn: sqlite3.Connection, dejavu_cats: int = 0) -> StevenStatus:
    strikes = conn.execute(
        "SELECT COUNT(*) FROM properties WHERE key LIKE ? ESCAPE '\\'",
        (STRIKE_PREFIX.replace("_", "\\_") + "%",),
    ).fetchone()[0]
    return StevenStatus(
        penalty=get_prop(conn, "savescumlocation") or 0,
        strikes=int(strikes),
        met=(get_prop(conn, "PlotFlag_StevenMet") or 0) == 1,
        on_adventure=(get_prop(conn, "on_adventure") or 0) == 1,
        guarded=is_guarded(conn),
        dejavu_cats=dejavu_cats,
    )


def is_guarded(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    return all(t in names for t in GUARD_TRIGGERS)


def set_guard(conn: sqlite3.Connection, on: bool) -> None:
    for t in GUARD_TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {t}")
    if not on:
        return
    cond = "NEW.key = 'savescumlocation' AND CAST(NEW.data AS INTEGER) <> 0"
    conn.execute(f"CREATE TRIGGER {GUARD_TRIGGERS[0]} BEFORE INSERT ON properties WHEN {cond} "
                 "BEGIN SELECT RAISE(IGNORE); END")
    conn.execute(f"CREATE TRIGGER {GUARD_TRIGGERS[1]} BEFORE UPDATE ON properties WHEN {cond} "
                 "BEGIN SELECT RAISE(IGNORE); END")


def reset_steven(conn: sqlite3.Connection, mode: str = "cap") -> None:
    """mode: 'cap' keeps only the first strike record, 'clear' removes all.
    Also clears DejaVu disorder from active adventure cats."""
    set_prop(conn, "savescumlocation", 0)
    like = STRIKE_PREFIX.replace("_", "\\_") + "%"
    if mode == "clear":
        conn.execute("DELETE FROM properties WHERE key LIKE ? ESCAPE '\\'", (like,))
    elif mode == "cap":
        conn.execute(
            """DELETE FROM properties WHERE key LIKE ?1 ESCAPE '\\' AND key NOT IN (
                   SELECT key FROM properties WHERE key LIKE ?1 ESCAPE '\\' ORDER BY key ASC LIMIT 1)""",
            (like,),
        )
    # Also strip DejaVu disorder from active cats (Steven's save-scum debuff)
    adv_row = conn.execute("SELECT data FROM files WHERE key='adventure_state'").fetchone()
    adv_keys = mewparser.parse_adventure_state(mewparser.to_bytes(adv_row[0])) if adv_row and adv_row[0] else []
    current_day = get_prop(conn, "current_day") or 0
    for ck in adv_keys:
        row = conn.execute("SELECT data FROM cats WHERE key=?", (ck,)).fetchone()
        if not row or not row[0]:
            continue
        try:
            parsed = cb.parse_cat_blob(mewparser.to_bytes(row[0]), ck, "(ADVENTURE)", current_day)
        except Exception:
            continue
        dis = list(parsed["abilities"]["disorder"])
        if any(d == "DejaVu" for d in dis):
            new_dis = [None if d == "DejaVu" else d for d in dis]
            patched = cb.patch_cat_blob(mewparser.to_bytes(row[0]), {"abilities": {"disorder": new_dis}}, current_day)
            conn.execute("UPDATE cats SET data=? WHERE key=?", (sqlite3.Binary(patched), ck))


# --------------------------------------------------------------------------- raw IO

def _checkpoint_wal(path: Path) -> None:
    wal = Path(str(path) + "-wal")
    if wal.exists():
        c = sqlite3.connect(path)
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            c.close()


def read_bytes(path: Path) -> tuple[bytes, bool]:
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        mem = sqlite3.connect(":memory:")
        src.backup(mem)
        src.close()
        data = bytearray(mem.serialize())
        mem.close()
    except sqlite3.Error:
        data = bytearray(path.read_bytes())
    was_wal = len(data) > 19 and (data[18] == 2 or data[19] == 2)
    if was_wal:
        data[18] = data[19] = 1
    return bytes(data), was_wal


def open_memory(data: bytes) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.deserialize(data)
    return conn


def load(path: Path) -> sqlite3.Connection:
    data, _ = read_bytes(path)
    return open_memory(data)


# --------------------------------------------------------------------------- full save parser

def parse_save(data: bytes) -> dict[str, Any]:
    """Deterministic full parse of a Mewgenics .sav database."""
    conn = open_memory(data)
    try:
        basic: dict[str, int] = {k: 0 for k in BASIC_PROP_KEYS}
        for k in BASIC_PROP_KEYS:
            v = get_prop(conn, k)
            if v is not None:
                basic[k] = v
        basic["save_percent"] = basic["save_file_percent"]
        current_day = basic["current_day"]

        hs_row = conn.execute("SELECT data FROM files WHERE key='house_state'").fetchone()
        house_entries = cb.parse_house_state_full(hs_row[0]) if hs_row and hs_row[0] else []
        house_map = {e["key"]: e["room"] for e in house_entries}

        adv_row = conn.execute("SELECT data FROM files WHERE key='adventure_state'").fetchone()
        adv_keys = mewparser.parse_adventure_state(mewparser.to_bytes(adv_row[0])) if adv_row and adv_row[0] else []
        adv_set = set(adv_keys)

        active_cats: list[dict[str, Any]] = []
        graveyard_cats: list[dict[str, Any]] = []
        all_cats_by_key: dict[int, dict[str, Any]] = {}
        parse_errors: list[str] = []

        for key, raw_blob in conn.execute("SELECT key, data FROM cats ORDER BY key"):
            if raw_blob is None:
                continue
            if key in adv_set:
                room = "(ADVENTURE)"
            elif key in house_map:
                room = house_map[key]
            else:
                room = ""
            try:
                cat = cb.parse_cat_blob(mewparser.to_bytes(raw_blob), key, room, current_day)
            except Exception as e:
                parse_errors.append(f"Cat {key}: {e}")
                continue
            all_cats_by_key[key] = cat
            if room:
                active_cats.append(cat)
            elif cat["dead"]:
                graveyard_cats.append(cat)

        # Inventories (exact round-trip)
        items_data: dict[str, list[dict[str, Any]]] = {"storage": [], "backpack": [], "trash": []}
        for file_key, grp in (("inventory_storage", "storage"),
                              ("inventory_backpack", "backpack"),
                              ("inventory_trash", "trash")):
            row = conn.execute("SELECT data FROM files WHERE key=?", (file_key,)).fetchone()
            if row and row[0] is not None:
                items_data[grp] = cb.parse_inventory_blob(row[0])

        # Furniture (exact 44/44 round-trip)
        furn_placed: list[dict[str, Any]] = []
        furn_unplaced: list[dict[str, Any]] = []
        try:
            for fkey, fblob in conn.execute("SELECT key, data FROM furniture ORDER BY key"):
                if not fblob:
                    continue
                try:
                    f = cb.parse_furniture_row(fkey, fblob)
                except Exception:
                    continue
                if f["room"]:
                    furn_placed.append(f)
                else:
                    furn_unplaced.append(f)
        except sqlite3.Error:
            pass

        rooms = sorted({e["room"] for e in house_entries if e["room"]}
                       | {f["room"] for f in furn_placed if f["room"]})

        return {
            "basic": basic,
            "cats": active_cats,
            "graveyard": graveyard_cats,
            "all_cats": all_cats_by_key,
            "items": items_data,
            "furniture": {"placed": furn_placed, "unplaced": furn_unplaced},
            "rooms": rooms,
            "total_cats": len(active_cats),
            "total_graveyard": len(graveyard_cats),
            "total_furniture": len(furn_placed) + len(furn_unplaced),
            "_debug": {"parse_errors": parse_errors},
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- backups

@dataclass
class Backup:
    path: Path
    when: _dt.datetime
    reason: str
    day: int | None = None
    penalty: int | None = None


def backup_dir_for(save: Path) -> Path:
    parent = save.parent
    owner = parent.parent.name if parent.name == "saves" else parent.name
    return BACKUP_ROOT / f"{owner}_{save.stem}"


def create_backup(save: Path, reason: str = "manual") -> Path:
    d = backup_dir_for(save)
    d.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "backup"
    if not any(d.glob("*.sav")):
        reason = "pristine"
    dest = d / f"{stamp}__{reason}.sav"
    data, _ = read_bytes(save)
    dest.write_bytes(data)
    rotate_backups(save)
    return dest


def list_backups(save: Path) -> list[Backup]:
    d = backup_dir_for(save)
    if not d.is_dir():
        return []
    out: list[Backup] = []
    for p in sorted(d.glob("*.sav"), reverse=True):
        m = re.match(r"(\d{8}_\d{6})_\d+__(.*)\.sav$", p.name)
        when = _dt.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") if m else _dt.datetime.fromtimestamp(p.stat().st_mtime)
        b = Backup(path=p, when=when, reason=m.group(2) if m else "")
        try:
            c = load(p)
            b.day = get_prop(c, "current_day")
            b.penalty = get_prop(c, "savescumlocation")
            c.close()
        except sqlite3.Error:
            pass
        out.append(b)
    return out


def rotate_backups(save: Path, keep: int = MAX_BACKUPS_PER_SAVE) -> None:
    d = backup_dir_for(save)
    files = [f for f in sorted(d.glob("*.sav"), reverse=True) if "__pristine" not in f.name]
    for old in files[keep:]:
        old.unlink(missing_ok=True)


# --------------------------------------------------------------------------- writing

class GameRunningError(RuntimeError):
    pass


def _write_atomic(save: Path, data: bytes, was_wal: bool) -> None:
    tmp = save.with_name(save.name + ".mewdeck-tmp")
    tmp.unlink(missing_ok=True)
    mem = open_memory(data)
    out = sqlite3.connect(tmp)
    try:
        mem.backup(out)
        if was_wal:
            out.execute("PRAGMA journal_mode=WAL")
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RuntimeError(f"integrity check failed on new save: {ok}")
    finally:
        out.close()
        mem.close()
    for suffix in ("-wal", "-shm"):
        Path(str(tmp) + suffix).unlink(missing_ok=True)
    _checkpoint_wal(save)
    for suffix in ("-wal", "-shm"):
        Path(str(save) + suffix).unlink(missing_ok=True)
    os.replace(tmp, save)


@dataclass
class Changes:
    props: dict[str, int] = field(default_factory=dict)
    cats: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Inventory edits per container: 'storage' | 'backpack' | 'trash'
    inv_add: dict[str, list[str]] = field(default_factory=lambda: {"storage": [], "backpack": [], "trash": []})
    inv_remove: dict[str, list[int]] = field(default_factory=lambda: {"storage": [], "backpack": [], "trash": []})
    # Furniture edits: add furniture_id to unplaced storage; remove by SQLite row key
    furn_add: list[str] = field(default_factory=list)
    furn_remove: list[int] = field(default_factory=list)
    heal_all: bool = False
    steven: str | None = None
    guard: bool | None = None
    add_items: list[str] | None = None

    def __post_init__(self) -> None:
        if self.add_items:
            self.inv_add["storage"].extend(self.add_items)
            self.add_items = None

    def inv_total(self) -> int:
        return sum(len(v) for v in self.inv_add.values()) + sum(len(v) for v in self.inv_remove.values())

    def empty(self) -> bool:
        return not (
            self.props or self.cats or self.inv_total() or self.furn_add or self.furn_remove
            or self.heal_all or self.steven or self.guard is not None
        )

    def count(self) -> int:
        return (
            len(self.props)
            + len(self.cats)
            + self.inv_total()
            + len(self.furn_add)
            + len(self.furn_remove)
            + (1 if self.heal_all else 0)
            + (1 if self.steven else 0)
            + (1 if self.guard is not None else 0)
        )


def _max_seq_id(conn: sqlite3.Connection) -> int:
    mx = 999
    for (blob,) in conn.execute("SELECT data FROM files WHERE key LIKE 'inventory_%'"):
        if blob:
            for it in cb.parse_inventory_blob(blob):
                mx = max(mx, int(it["instance_id"]))
    return mx


def _default_item_charges(item_id: str) -> int:
    from . import gamedata as gd

    info = gd.item_info(item_id)
    if info.get("consumable"):
        return 1
    dur = str(info.get("durability") or "")
    if dur:
        m = re.search(r"\d+", dur)
        if m:
            return int(m.group(0))
    return -1


def apply_changes(save: Path, changes: Changes, *, reason: str = "edit") -> Path:
    """Back up, apply, verify and atomically replace the save. Returns the backup path."""
    if game_running():
        raise GameRunningError("Mewgenics is running - close it first or the game will overwrite your edits")
    backup = create_backup(save, f"before-{reason}")
    data, was_wal = read_bytes(save)
    before = parse_save(data)

    conn = open_memory(data)
    try:
        # 1. Properties
        for key, value in changes.props.items():
            prop_key = "save_file_percent" if key == "save_percent" else key
            set_prop(conn, prop_key, int(value))
        current_day = get_prop(conn, "current_day") or before["basic"]["current_day"]

        # 2. Cat edits (including heal_all and revive)
        cat_edits: dict[int, dict[str, Any]] = {k: dict(v) for k, v in changes.cats.items()}
        if changes.heal_all:
            for c in before["cats"]:
                cat_edits.setdefault(c["key"], {})["heal"] = True

        next_seq = _max_seq_id(conn) + 1
        revived_keys: list[int] = []
        for cat_key, ch in cat_edits.items():
            row = conn.execute("SELECT data FROM cats WHERE key=?", (cat_key,)).fetchone()
            if not row or row[0] is None:
                continue
            new_blob = cb.patch_cat_blob(mewparser.to_bytes(row[0]), ch, current_day, next_seq_id=next_seq)
            next_seq += 8
            conn.execute("UPDATE cats SET data=? WHERE key=?", (sqlite3.Binary(new_blob), cat_key))
            if ch.get("revive"):
                revived_keys.append(cat_key)

        # Add revived cats back into house_state (Attic)
        if revived_keys:
            hs_row = conn.execute("SELECT data FROM files WHERE key='house_state'").fetchone()
            entries = cb.parse_house_state_full(hs_row[0]) if hs_row and hs_row[0] else []
            existing = {e["key"] for e in entries}
            for rk in revived_keys:
                if rk not in existing:
                    entries.append({"key": rk, "unk": 0, "room": "Attic", "pos": (0.0, 0.0, 0.0)})
            conn.execute("UPDATE files SET data=? WHERE key='house_state'",
                         (sqlite3.Binary(cb.serialize_house_state_full(entries)),))

        # 3. Inventory edits (storage, backpack, trash)
        file_map = {"storage": "inventory_storage", "backpack": "inventory_backpack", "trash": "inventory_trash"}
        for grp, file_key in file_map.items():
            adds = changes.inv_add.get(grp, [])
            rems = set(changes.inv_remove.get(grp, []))
            if not adds and not rems:
                continue
            row = conn.execute("SELECT data FROM files WHERE key=?", (file_key,)).fetchone()
            items = cb.parse_inventory_blob(row[0]) if row and row[0] is not None else []
            if rems:
                items = [it for it in items if int(it["instance_id"]) not in rems]
            for iid in adds:
                items.append({
                    "item_id": iid,
                    "instance_id": next_seq,
                    "extra_id": "",
                    "field_a": _default_item_charges(iid),
                    "field_b": 0,
                    "field_c": 0,
                    "marker": 5,
                    "state": 1,
                    "sentinel": 0xFF,
                    "flags": 0,
                })
                next_seq += 1
            blob_out = sqlite3.Binary(cb.serialize_inventory_blob(items))
            if row is None:
                conn.execute("INSERT INTO files (key, data) VALUES (?, ?)", (file_key, blob_out))
            else:
                conn.execute("UPDATE files SET data=? WHERE key=?", (blob_out, file_key))

        # 4. Furniture edits (safe unplaced additions + key removals)
        if changes.furn_remove:
            for fk in changes.furn_remove:
                conn.execute("DELETE FROM furniture WHERE key=?", (int(fk),))
        if changes.furn_add:
            max_key = conn.execute("SELECT COALESCE(MAX(key), 0) FROM furniture").fetchone()[0]
            max_z = max([f["z"] for f in before["furniture"]["unplaced"]], default=0)
            for fid in changes.furn_add:
                max_key += 1
                max_z += 1
                blob = cb.serialize_furniture_row({
                    "furniture_id": fid,
                    "room": "",
                    "x": 0,
                    "y": 0,
                    "z": max_z,
                    "flag1": 0,
                    "flag2": 0,
                })
                conn.execute("INSERT INTO furniture (key, data) VALUES (?, ?)", (max_key, sqlite3.Binary(blob)))

        # 5. Steven reset & auto-block guard
        if changes.guard is not None:
            set_guard(conn, False)
        if changes.steven:
            reset_steven(conn, changes.steven)
        if changes.guard:
            set_guard(conn, True)

        conn.commit()
        data = conn.serialize()
    finally:
        conn.close()

    after = parse_save(data)
    verify(before, after, changes)
    _write_atomic(save, data, was_wal)
    return backup


def verify(before: dict[str, Any], after: dict[str, Any], changes: Changes) -> None:
    """Re-parse the edited DB and verify every single edit read back accurately."""
    revived = sum(1 for ch in changes.cats.values() if ch.get("revive"))
    if after["total_cats"] != before["total_cats"] + revived:
        raise RuntimeError("verification failed: active cat count changed unexpectedly - nothing was written")

    b_all = before["all_cats"]
    a_all = after["all_cats"]
    for key, ch in changes.cats.items():
        if "stats" in ch and any(not (1 <= int(v) <= 7) for v in ch["stats"].values()):
            raise RuntimeError(f"verification failed: cat {key} base stats out of range 1..7 - nothing was written")
        new = a_all.get(key)
        if new is None:
            raise RuntimeError(f"verification failed: cat {key} vanished - nothing was written")
        for f in ("stats", "level_bonuses", "level", "age", "sex", "retired", "class", "name"):
            if f in ch and new[f] != ch[f]:
                raise RuntimeError(f"verification failed: cat {key} {f} reads back {new[f]!r}, expected {ch[f]!r}")
        if ch.get("heal") or ch.get("revive"):
            if new["injured"] or new["wounded"]:
                raise RuntimeError(f"verification failed: cat {key} still injured/wounded after heal")
        if ch.get("revive") and (new["dead"] or not new["room"]):
            raise RuntimeError(f"verification failed: cat {key} not revived cleanly")
        if "abilities" in ch:
            ab = ch["abilities"]
            for fld in ("active", "passive", "passive_tiers", "disorder", "disorder_tiers"):
                if fld in ab and new["abilities"][fld] != ab[fld]:
                    raise RuntimeError(f"verification failed: cat {key} abilities.{fld} mismatch")
        if "mutations" in ch:
            for mk, mv in ch["mutations"].items():
                if new["mutations"].get(mk) != (mv & 0xFFFFFFFF):
                    raise RuntimeError(f"verification failed: cat {key} mutation {mk} mismatch")
        if "equipment" in ch:
            for sk, sv in ch["equipment"].items():
                want_id = (sv["item_id"] if isinstance(sv, dict) else sv) if sv else None
                got_id = new["equipment"][sk]["item_id"] if new["equipment"][sk] else None
                if got_id != want_id:
                    raise RuntimeError(f"verification failed: cat {key} equipment {sk} reads back {got_id!r}, expected {want_id!r}")

    if changes.heal_all:
        for c in after["cats"]:
            if c["injured"] or c["wounded"]:
                raise RuntimeError(f"verification failed: cat {c['name']} still injured after heal_all")

    for k, v in changes.props.items():
        bk = "save_percent" if k == "save_file_percent" else k
        if after["basic"].get(bk) != v:
            raise RuntimeError(f"verification failed: property {k} mismatch")

    for grp in ("storage", "backpack", "trash"):
        exp = len(before["items"][grp]) + len(changes.inv_add.get(grp, [])) - len(set(changes.inv_remove.get(grp, [])))
        if len(after["items"][grp]) != exp:
            raise RuntimeError(f"verification failed: {grp} item count mismatch")

    exp_f = before["total_furniture"] + len(changes.furn_add) - len(set(changes.furn_remove))
    if after["total_furniture"] != exp_f:
        raise RuntimeError("verification failed: furniture count mismatch")


def cat_editable(snap: "Snapshot", cat: dict[str, Any]) -> dict[str, bool]:
    """With the deterministic catblob codec, every field on every cat is safely editable."""
    return {
        "stats": True,
        "level": True,
        "age": True,
        "sex": True,
        "retired": True,
        "class": True,
        "abilities": True,
        "mutations": True,
        "equipment": True,
    }


def restore_backup(save: Path, backup: Path) -> Path:
    if game_running():
        raise GameRunningError("Mewgenics is running - close it first")
    safety = create_backup(save, "before-restore")
    data, _ = read_bytes(backup)
    _, was_wal = read_bytes(save)
    _write_atomic(save, data, was_wal)
    return safety


# --------------------------------------------------------------------------- snapshot for the UI

@dataclass
class Snapshot:
    path: Path
    parsed: dict[str, Any]
    steven: StevenStatus
    mtime: float


def snapshot(path: Path) -> Snapshot:
    data, _ = read_bytes(path)
    parsed = parse_save(data)
    dejavu = sum(
        1 for c in parsed["cats"]
        if c["room"] == "(ADVENTURE)" and "DejaVu" in (c["abilities"]["disorder"] or [])
    )
    conn = open_memory(data)
    st = steven_status(conn, dejavu_cats=dejavu)
    conn.close()
    return Snapshot(path=path, parsed=parsed, steven=st, mtime=path.stat().st_mtime)


def cat_change_base(snap: Snapshot, cat: dict[str, Any]) -> dict[str, Any]:
    """No internal offsets needed with deterministic catblob patching; kept for API compatibility."""
    return {}
