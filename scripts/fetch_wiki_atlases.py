#!/usr/bin/env python3
"""Fetch & bake official Mewgenics wiki SVG icon atlases (offline, quantized RGBA PNGs).

Generates:
  mewdeck/gamedata/abilities/ability_icons.png + .json   (actives + passives)
  mewdeck/gamedata/abilities/disorder_icons.png + .json  (125/125 disorders)
  mewdeck/gamedata/furniture/furniture_icons.png + .json (633/633 furniture)
  mewdeck/gamedata/class_icons.png + .json               (14/14 classes)
Also optionally re-optimizes existing item_icons.png with lossless-looking 256-color RGBA quantization.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
GD = ROOT / "mewdeck/gamedata"
UA = "MewDeck/1.4 (https://github.com/thedankest/mewdeck)"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def fetch_wiki_prefix(prefix: str) -> list[dict]:
    out = []
    cont = None
    while True:
        params = {
            "action": "query",
            "list": "allimages",
            "aiprefix": prefix,
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
        if not cont or not cont.startswith(prefix):
            break
    return out


def download_one(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception:
            pass
    return None


def save_compact_png(surf: pygame.Surface, out_path: Path) -> None:
    raw = pygame.image.tobytes(surf, "RGBA")
    img = Image.frombytes("RGBA", surf.get_size(), raw)
    # FastOctree quantize preserves full alpha transparency (tRNS) at ~30% of full RGBA size
    q = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    q.save(out_path, format="PNG", optimize=True)


def bake_atlas(
    label: str,
    matched: dict[str, dict],  # id -> wiki image dict
    out_png: Path,
    out_json: Path,
    *,
    tile: int = 48,
    cols: int = 32,
) -> None:
    unique_urls: dict[str, str] = {}
    for info in matched.values():
        unique_urls[info["name"]] = info["url"]
    names = sorted(unique_urls)
    print(f"[{label}] Downloading {len(names)} unique wiki sprites for {len(matched)} entries...")

    raw_blobs: dict[str, bytes] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(download_one, unique_urls[n]): n for n in names}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            n = futs[fut]
            data = fut.result()
            if data:
                raw_blobs[n] = data
            done += 1
            if done % 300 == 0 or done == len(names):
                print(f"  [{label}] {done}/{len(names)} downloaded ({len(raw_blobs)} ok)")

    pad = 2
    inner = tile - pad * 2
    rendered: list[tuple[str, pygame.Surface]] = []
    failed = 0
    for n in names:
        data = raw_blobs.get(n)
        if not data:
            failed += 1
            continue
        try:
            if n.lower().endswith(".svg"):
                png_bytes = resvg_py.svg_to_bytes(svg_string=data.decode("utf-8", errors="replace"), width=tile * 2)
                src = pygame.image.load(io.BytesIO(png_bytes), "icon.png")
            else:
                src = pygame.image.load(io.BytesIO(data), n)
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
            t_surf = pygame.Surface((tile, tile), pygame.SRCALPHA)
            t_surf.blit(scaled, ((tile - nw) // 2, (tile - nh) // 2))
            rendered.append((n, t_surf))
        except Exception:
            failed += 1

    rows = max(1, math.ceil(len(rendered) / cols))
    atlas = pygame.Surface((cols * tile, rows * tile), pygame.SRCALPHA)
    name_to_idx: dict[str, int] = {}
    for idx, (n, t_surf) in enumerate(rendered):
        name_to_idx[n] = idx
        atlas.blit(t_surf, ((idx % cols) * tile, (idx // cols) * tile))

    save_compact_png(atlas, out_png)
    id_map = {k: name_to_idx[v["name"]] for k, v in matched.items() if v["name"] in name_to_idx}
    out_json.write_text(json.dumps({"tile": tile, "cols": cols, "icons": id_map}, separators=(",", ":")))
    print(f"[{label}] Wrote {out_png.name} ({out_png.stat().st_size / 1024:.1f} KB) · {len(id_map)} mappings ({failed} failed)")


def index_wiki(prefix: str) -> dict[str, dict]:
    by_n: dict[str, dict] = {}
    for x in fetch_wiki_prefix(prefix):
        nm = x["name"]
        if not nm.lower().endswith((".svg", ".png")):
            continue
        stem = nm[len(prefix):].rsplit(".", 1)[0]
        k = norm(stem)
        if k not in by_n or nm.lower().endswith(".svg"):
            by_n[k] = x
    return by_n


def match_dict(entries: dict, by_n: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for eid, info in entries.items():
        cands = [
            info.get("name", ""),
            eid,
            re.sub(r"[0-9]+$", "", eid),
            re.sub(r"_(Fighter|Hunter|Mage|Tank|Medic|Thief|Necromancer|Butcher|Druid|Tinkerer|Psychic|Monk|Jester)$", "", eid),
            re.sub(r"\s*\([^)]*\)", "", info.get("name", "")),
        ]
        for c in cands:
            k = norm(c)
            if k and k in by_n:
                out[eid] = by_n[k]
                break
    return out


def main() -> None:
    pygame.init()

    # 1. Optimize existing item_icons.png if still huge (>900KB)
    item_png = GD / "items/item_icons.png"
    if item_png.is_file() and item_png.stat().st_size > 900_000:
        img = Image.open(item_png).convert("RGBA")
        q = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        q.save(item_png, format="PNG", optimize=True)
        print(f"[items] Optimized item_icons.png -> {item_png.stat().st_size / 1024:.1f} KB")

    # 2. Abilities + Passives
    ab_wiki = index_wiki("ABILITY_")
    actives = json.loads((GD / "abilities/actives.json").read_text(encoding="utf-8"))
    passives = json.loads((GD / "abilities/passives.json").read_text(encoding="utf-8"))
    combined = {**actives, **passives}
    ab_matched = match_dict(combined, ab_wiki)
    bake_atlas("abilities", ab_matched, GD / "abilities/ability_icons.png", GD / "abilities/ability_icons.json", tile=48, cols=32)

    # 3. Disorders (125/125)
    dis_wiki = index_wiki("DISORDER_")
    disorders = json.loads((GD / "abilities/disorders.json").read_text(encoding="utf-8"))
    dis_matched = match_dict(disorders, dis_wiki)
    bake_atlas("disorders", dis_matched, GD / "abilities/disorder_icons.png", GD / "abilities/disorder_icons.json", tile=48, cols=16)

    # 4. Furniture (633/633)
    furn_wiki = index_wiki("FURNITURE_")
    furn = json.loads((GD / "furniture/furniture.json").read_text(encoding="utf-8"))
    furn_matched = match_dict(furn, furn_wiki)
    bake_atlas("furniture", furn_matched, GD / "furniture/furniture_icons.png", GD / "furniture/furniture_icons.json", tile=48, cols=32)

    # 5. Class collar icons (14/14)
    class_aliases = {
        "Colorless": "Collarless_Icon.svg",
        "Fighter": "Fighter_Icon.svg",
        "Hunter": "Hunter_Icon.svg",
        "Mage": "Mage_Icon.svg",
        "Tank": "Tank_Icon.svg",
        "Medic": "Cleric_Icon.svg",
        "Thief": "Thief_Icon.svg",
        "Necromancer": "Necromancer_Icon.svg",
        "Butcher": "Butcher_Icon.svg",
        "Druid": "Druid_Icon.svg",
        "Tinkerer": "Tinkerer_Icon.svg",
        "Psychic": "Psychic_Icon.svg",
        "Monk": "Monk_Icon.svg",
        "Jester": "Jester_Icon.svg",
    }
    cls_matched: dict[str, dict] = {}
    titles = "|".join(f"File:{v}" for v in class_aliases.values())
    url = "https://mewgenics.wiki.gg/api.php?" + urllib.parse.urlencode({
        "action": "query", "prop": "imageinfo", "iiprop": "url", "titles": titles, "format": "json"
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    by_title = {}
    for p in d.get("query", {}).get("pages", {}).values():
        t = p.get("title", "").replace("File:", "").replace(" ", "_")
        ii = p.get("imageinfo", [{}])[0]
        if ii.get("url"):
            by_title[t] = {"name": t, "url": ii["url"]}
    for cls, fname in class_aliases.items():
        if fname in by_title:
            cls_matched[cls] = by_title[fname]
    bake_atlas("classes", cls_matched, GD / "class_icons.png", GD / "class_icons.json", tile=64, cols=14)


if __name__ == "__main__":
    main()
