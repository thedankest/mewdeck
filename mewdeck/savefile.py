"""Save discovery, safe in-place writes, backups and the Steven (anti-save-scum) reset.

The Mewgenics .sav is a SQLite database. Cat/inventory blob editing is delegated to
the vendored parser from accessiblefish/mewgenics-save-editor (AGPL-3.0); the Steven
reset follows TaliesinYang/mewgenics-bye-bye-steven (properties.savescumlocation=0,
NPCRSTRACKER_steven_savescum_* strike rows capped or cleared).
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
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .vendor import mewparser

APP_ID = "686060"
PREFIX_SAVE_REL = Path("pfx/drive_c/users/steamuser/AppData/Roaming/Glaiel Games/Mewgenics")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewdeck"
BACKUP_ROOT = Path(os.environ.get("MEWDECK_BACKUP_DIR", DATA_HOME / "backups"))
MAX_BACKUPS_PER_SAVE = 40

STRIKE_PREFIX = "NPCRSTRACKER_steven_savescum_"
# savescumlocation enum (from the game's types): where the scum-able state was armed
SCUM_LOCATIONS = {1: "battle", 2: "house boss", 3: "event", 4: "other"}
GUARD_TRIGGERS = ("mewdeck_block_savescum_insert", "mewdeck_block_savescum_update")


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
    # SD cards / external drives on SteamOS-likes (Bazzite mounts under /run/media)
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
    # Native Windows path, handy when testing under Wine directly
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
        globs = ["*.sav", "saves/*.sav", "*/saves/*.sav"]
        for g in globs:
            for p in root.glob(g):
                if not p.is_file() or "backup" in p.name.lower():
                    continue
                try:
                    found.setdefault(str(p.resolve()), p)
                except OSError:
                    continue
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def save_label(path: Path) -> str:
    """e.g. 'steamcampaign01  (7656119…1234)'"""
    parent = path.parent
    owner = parent.parent.name if parent.name == "saves" else parent.name
    if owner.isdigit() and len(owner) > 8:
        owner = owner[:7] + "…" + owner[-4:]
    return f"{path.stem}  ({owner})"


# --------------------------------------------------------------------------- game process

def game_pids() -> list[int]:
    """PIDs whose command line mentions Mewgenics.exe (the Proton/Wine game process)."""
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
                time.sleep(1.0)  # let wine flush/close file handles
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
    """Encode `value` with the same storage type the game used for this property."""
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
        return False
    conn.execute("UPDATE properties SET data = ? WHERE key = ?", (_encode_like(row[0], value), key))
    return True


@dataclass
class StevenStatus:
    penalty: int  # savescumlocation (non-zero = Steven penalty armed)
    strikes: int  # NPCRSTRACKER_steven_savescum_* rows (which Steven scenes have played)
    met: bool
    on_adventure: bool
    guarded: bool = False  # MewDeck's auto-block triggers are installed

    @property
    def where(self) -> str:
        return SCUM_LOCATIONS.get(self.penalty, f"code {self.penalty}") if self.penalty else ""

    @property
    def active(self) -> bool:
        return self.penalty != 0


def steven_status(conn: sqlite3.Connection) -> StevenStatus:
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
    )


def is_guarded(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    return all(t in names for t in GUARD_TRIGGERS)


def set_guard(conn: sqlite3.Connection, on: bool) -> None:
    """Auto-block Steven: the game writes the flag with INSERT OR REPLACE when a scum-able state starts.
    BEFORE triggers with RAISE(IGNORE) silently drop any non-zero write, so the penalty can never arm.
    (RAISE(ABORT) would fail the game's own save transaction, so IGNORE it is.)"""
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
    """mode: 'cap' keeps only the first strike record (Bye Bye Steven default), 'clear' removes all."""
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
    """Consistent snapshot of the DB (including any WAL contents). Returns (bytes, was_wal)."""
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
    if was_wal:  # in-memory DBs can't be WAL; normalise to rollback-journal header
        data[18] = data[19] = 1
    return bytes(data), was_wal


def open_memory(data: bytes) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.deserialize(data)
    return conn


def load(path: Path) -> sqlite3.Connection:
    data, _ = read_bytes(path)
    return open_memory(data)


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
        reason = "pristine"  # the very first backup is kept forever (never rotated out)
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
    props: dict[str, int] = field(default_factory=dict)  # e.g. house_gold, house_food
    cats: dict[int, dict[str, Any]] = field(default_factory=dict)  # upstream cat change dicts (incl. _offsets)
    add_items: list[str] = field(default_factory=list)  # item ids → storage
    steven: str | None = None  # None | 'cap' | 'clear'
    guard: bool | None = None  # install / remove the auto-block triggers

    def empty(self) -> bool:
        return not (self.props or self.cats or self.add_items or self.steven or self.guard is not None)

    def count(self) -> int:
        return (len(self.props) + len(self.cats) + len(self.add_items) + (1 if self.steven else 0)
                + (1 if self.guard is not None else 0))


def apply_changes(save: Path, changes: Changes, *, reason: str = "edit") -> Path:
    """Back up, apply, verify and atomically replace the save. Returns the backup path."""
    if game_running():
        raise GameRunningError("Mewgenics is running - close it first or the game will overwrite your edits")
    backup = create_backup(save, f"before-{reason}")
    data, was_wal = read_bytes(save)
    before = mewparser.parse_save_file(data)

    if changes.cats or changes.add_items:
        with tempfile.TemporaryDirectory(prefix="mewdeck-") as td:
            out = Path(td) / "stage.sav"
            item_changes = {"storage": {"added": [{"item_id": i} for i in changes.add_items], "removed": []}} if changes.add_items else {}
            mewparser.modify_save_file(data, {}, dict(changes.cats), None, item_changes, str(out))
            data = out.read_bytes()

    conn = open_memory(data)
    for key, value in changes.props.items():
        set_prop(conn, key, int(value))
    if changes.guard is not None:
        set_guard(conn, False)  # drop first so the reset below isn't affected either way
    if changes.steven:
        reset_steven(conn, changes.steven)
    if changes.guard:
        set_guard(conn, True)
    conn.commit()
    data = conn.serialize()
    conn.close()

    after = mewparser.parse_save_file(data)  # must still parse
    verify(before, after, changes)
    _write_atomic(save, data, was_wal)
    return backup


VERIFIED_CAT_FIELDS = ("stats", "level", "age", "sex", "retired")


def verify(before: dict[str, Any], after: dict[str, Any], changes: Changes) -> None:
    """Re-parse the edited DB and make sure every edit reads back exactly and nothing else moved."""
    if after["total_cats"] != before["total_cats"]:
        raise RuntimeError("verification failed: cat count changed - nothing was written")
    b = {c["key"]: c for c in before["cats"]}
    a = {c["key"]: c for c in after["cats"]}
    for key, cat in b.items():
        new = a.get(key)
        if new is None:
            raise RuntimeError(f"verification failed: cat {key} vanished - nothing was written")
        wanted = changes.cats.get(key, {})
        for f in VERIFIED_CAT_FIELDS + ("name",):
            expect = wanted.get(f, cat[f])
            if new[f] != expect:
                raise RuntimeError(
                    f"verification failed: {cat['name'] or key} {f} reads back {new[f]!r}, expected {expect!r}"
                    " - nothing was written")
    for k, v in changes.props.items():
        if after["basic"].get(k) != v:
            raise RuntimeError(f"verification failed: {k} - nothing was written")
    added = len(changes.add_items)
    if len(after["items"]["storage"]) != len(before["items"]["storage"]) + added:
        raise RuntimeError("verification failed: storage item count - nothing was written")


def cat_editable(snap: "Snapshot", cat: dict[str, Any]) -> dict[str, bool]:
    """Which fields the vendored parser located reliably for this cat."""
    day = snap.parsed["basic"]["current_day"]
    sane_level = 0 <= cat["level"] <= 99 and cat["_level_offset"] >= 0
    sane_birth = 0 <= cat["_birth_day"] <= day and cat["_birth_day_offset"] >= 0
    return {
        "stats": cat["_stats_offset"] >= 0,
        "level": sane_level and sane_birth,
        "age": sane_level and sane_birth,
        "sex": True,
        "retired": True,
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
    parsed = mewparser.parse_save_file(data)
    conn = open_memory(data)
    st = steven_status(conn)
    conn.close()
    return Snapshot(path=path, parsed=parsed, steven=st, mtime=path.stat().st_mtime)


def cat_change_base(snap: Snapshot, cat: dict[str, Any]) -> dict[str, Any]:
    """Internal offsets the vendored writer needs for a cat edit."""
    return {
        "_name_end": cat["_name_end"],
        "_level_offset": cat["_level_offset"],
        "_birth_day_offset": cat["_birth_day_offset"],
        "_stats_offset": cat["_stats_offset"],
        "_birth_day": cat["_birth_day"],
        "_current_day": snap.parsed["basic"]["current_day"],
    }
