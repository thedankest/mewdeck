#!/usr/bin/env python3
"""Fetch item icons from mewgenics.wiki.gg and pack them into a single PNG sprite atlas.

Outputs:
  mewdeck/gamedata/items/item_icons.png   (compact RGBA PNG sprite sheet, 48x48 tiles)
  mewdeck/gamedata/items/item_icons.json  ({"tile": 48, "cols": 32, "icons": {item_id: idx}})
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import math
import os
import re
import struct
import sys
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

ROOT = Path(__file__).resolve().parent.parent
ITEMS_JSON = ROOT / "mewdeck/gamedata/items/items.json"
OUT_PNG = ROOT / "mewdeck/gamedata/items/item_icons.png"
OUT_JSON = ROOT / "mewdeck/gamedata/items/item_icons.json"

TILE = 48
COLS = 32
UA = "MewDeck/1.3 (https://github.com/thedankest/mewdeck)"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def fetch_wiki_item_list() -> list[dict]:
    out = []
    cont = None
    while True:
        params = {
            "action": "query",
            "list": "allimages",
            "aiprefix": "ITEM_",
            "ailimit": 500,
            "format": "json",
        }
        if cont:
            params["aicontinue"] = cont
        url = "https://mewgenics.wiki.gg/api.php?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
        out.extend(d.get("query", {}).get("allimages", []))
        cont = d.get("continue", {}).get("aicontinue")
        if not cont or not cont.startswith("ITEM_"):
            break
    return out


def match_items(items: dict, wiki_imgs: list[dict]) -> dict[str, dict]:
    by_norm: dict[str, dict] = {}
    for x in wiki_imgs:
        name = x["name"]
        if not name.lower().endswith((".svg", ".png", ".jpg", ".jpeg")):
            continue
        stem = name[len("ITEM_"):].rsplit(".", 1)[0]
        # Prefer SVG over raster if both exist
        k = norm(stem)
        if k not in by_norm or name.lower().endswith(".svg"):
            by_norm[k] = x

    matched: dict[str, dict] = {}
    for iid, info in items.items():
        candidates = [
            info.get("name", ""),
            iid,
            re.sub(r"(Cursed|Hat|Mask|Neck|Face|Weapon|Trinket|Consumable|[0-9]+)$", "", iid),
            re.sub(r"\s*\([^)]*\)", "", info.get("name", "")),
            re.sub(r"^The\s+", "", info.get("name", ""), flags=re.I),
        ]
        # Suffix stripping combos
        stripped_id = re.sub(r"[0-9]+$", "", iid)
        candidates.append(re.sub(r"(Hat|Mask|Neck|Face|Weapon|Trinket|Consumable)$", "", stripped_id))
        for cand in candidates:
            k = norm(cand)
            if k and k in by_norm:
                matched[iid] = by_norm[k]
                break
    return matched


def download_one(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception:
            pass
    return None


def write_png_rgba(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Write a compact RGBA PNG using per-row Sub/None filter + zlib level 9."""
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        row = rgba[y * stride : (y + 1) * stride]
        # Choose Sub filter (type 1) which compresses icons well
        sub = bytearray(stride)
        for i in range(stride):
            left = row[i - 4] if i >= 4 else 0
            sub[i] = (row[i] - left) & 0xFF
        raw.append(1)
        raw.extend(sub)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    pygame.init()
    items = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
    print(f"Loaded {len(items)} items from {ITEMS_JSON}")
    wiki_imgs = fetch_wiki_item_list()
    print(f"Found {len(wiki_imgs)} ITEM_* images on mewgenics.wiki.gg")

    matched = match_items(items, wiki_imgs)
    print(f"Matched {len(matched)} / {len(items)} items ({len(matched) * 100 / len(items):.1f}%)")

    # Deduplicate by wiki filename so aliases share one atlas slot
    unique_urls: dict[str, str] = {}
    for info in matched.values():
        unique_urls[info["name"]] = info["url"]
    names = sorted(unique_urls)
    print(f"Downloading {len(names)} unique wiki icon files...")

    raw_blobs: dict[str, bytes] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(download_one, unique_urls[n]): n for n in names}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            n = futs[fut]
            data = fut.result()
            if data:
                raw_blobs[n] = data
            done += 1
            if done % 200 == 0 or done == len(names):
                print(f"  downloaded {done}/{len(names)} ({len(raw_blobs)} ok)")

    try:
        import resvg_py
    except ImportError:
        resvg_py = None

    # Rasterize each icon to TILE x TILE preserving aspect ratio
    rendered: list[tuple[str, pygame.Surface]] = []
    failed = 0
    pad = 2
    inner = TILE - pad * 2
    for n in names:
        data = raw_blobs.get(n)
        if not data:
            failed += 1
            continue
        try:
            if n.lower().endswith(".svg") and resvg_py is not None:
                # Render SVG cleanly at 2x resolution via resvg (full <use>/defs/ffdec support)
                svg_str = data.decode("utf-8", errors="replace")
                png_bytes = resvg_py.svg_to_bytes(svg_string=svg_str, width=TILE * 2)
                src = pygame.image.load(io.BytesIO(png_bytes), "icon.png")
            else:
                src = pygame.image.load(io.BytesIO(data), n)
            # Trim transparent padding so every icon fills its 48x48 tile crisply
            bbox = src.get_bounding_rect(min_alpha=8)
            if bbox.w > 0 and bbox.h > 0:
                src = src.subsurface(bbox).copy()
            w, h = src.get_size()
            if w <= 0 or h <= 0:
                failed += 1
                continue
            scale = min(inner / w, inner / h)
            nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            scaled = pygame.transform.smoothscale(src, (nw, nh))
            tile = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
            tile.blit(scaled, ((TILE - nw) // 2, (TILE - nh) // 2))
            rendered.append((n, tile))
        except Exception:
            failed += 1

    print(f"Rasterized {len(rendered)} unique icons ({failed} failed)")
    rows = math.ceil(len(rendered) / COLS)
    atlas = pygame.Surface((COLS * TILE, rows * TILE), pygame.SRCALPHA)
    name_to_idx: dict[str, int] = {}
    for idx, (n, tile) in enumerate(rendered):
        name_to_idx[n] = idx
        cx = (idx % COLS) * TILE
        cy = (idx // COLS) * TILE
        atlas.blit(tile, (cx, cy))

    rgba = pygame.image.tobytes(atlas, "RGBA")
    write_png_rgba(OUT_PNG, atlas.get_width(), atlas.get_height(), rgba)

    item_map = {iid: name_to_idx[info["name"]] for iid, info in matched.items() if info["name"] in name_to_idx}
    OUT_JSON.write_text(json.dumps({"tile": TILE, "cols": COLS, "icons": item_map}, separators=(",", ":")))
    print(f"Wrote {OUT_PNG} ({OUT_PNG.stat().st_size / 1024:.1f} KB, {atlas.get_width()}x{atlas.get_height()})")
    print(f"Wrote {OUT_JSON} ({len(item_map)} item mappings)")


if __name__ == "__main__":
    main()
