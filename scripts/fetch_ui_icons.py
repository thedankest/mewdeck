#!/usr/bin/env python3
"""Fetch official Mewgenics UI & Stat SVGs from mewgenics.wiki.gg into ui_icons.png + ui_icons.json."""

from __future__ import annotations

import io
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
GD = ROOT / "mewdeck/gamedata"
UA = "MewDeck/1.5 (https://github.com/thedankest/mewdeck)"

TILE = 48
ICONS = {
    "STR": "Stat_Strength.svg",
    "DEX": "Stat_Dexterity.svg",
    "CON": "Stat_Constitution.svg",
    "INT": "Stat_Intelligence.svg",
    "SPD": "Stat_Speed.svg",
    "CHA": "Stat_Charisma.svg",
    "LUCK": "Stat_Luck.svg",
    "HP": "Stat_Health.svg",
    "Mana": "Stat_Mana.svg",
    "Shield": "Stat_Shield.svg",
    "Damage": "Stat_Damage.svg",
    "Food": "Food_Node.svg",
    "Gold": "Shop_Node.svg",
    "Treasure": "Treasure_Node.svg",
    "Boss": "Boss_Node.svg",
    "Reroll": "Stat_Level_Up_Reroll.svg",
    "Move": "Stat_Movement_Range.svg",
    "Collar": "Collarless_Icon.svg",
    "Heart": "Cleric_Icon.svg",
    "Recycle": "Icon_Recycle_Edited.svg",
}


def main() -> None:
    pygame.init()
    titles = "|".join(f"File:{v}" for v in ICONS.values())
    url = "https://mewgenics.wiki.gg/api.php?" + urllib.parse.urlencode({
        "action": "query", "prop": "imageinfo", "iiprop": "url", "titles": titles, "format": "json"
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    by_title: dict[str, str] = {}
    for p in d.get("query", {}).get("pages", {}).values():
        t = p.get("title", "").replace("File:", "").replace(" ", "_")
        ii = p.get("imageinfo", [{}])[0]
        if ii.get("url"):
            by_title[t] = ii["url"]

    keys = [k for k, fn in ICONS.items() if fn in by_title]
    atlas = pygame.Surface((len(keys) * TILE, TILE), pygame.SRCALPHA)
    id_map: dict[str, int] = {}
    pad = 2
    inner = TILE - pad * 2
    for idx, k in enumerate(keys):
        fn = ICONS[k]
        raw = urllib.request.urlopen(urllib.request.Request(by_title[fn], headers={"User-Agent": UA}), timeout=20).read()
        png_bytes = resvg_py.svg_to_bytes(svg_string=raw.decode("utf-8", errors="replace"), width=TILE * 2)
        src = pygame.image.load(io.BytesIO(png_bytes), "icon.png")
        bbox = src.get_bounding_rect(min_alpha=8)
        if bbox.w > 0 and bbox.h > 0:
            src = src.subsurface(bbox).copy()
        w, h = src.get_size()
        scale = min(inner / w, inner / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        scaled = pygame.transform.smoothscale(src, (nw, nh))
        atlas.blit(scaled, (idx * TILE + (TILE - nw) // 2, (TILE - nh) // 2))
        id_map[k] = idx

    out_png = GD / "ui_icons.png"
    out_json = GD / "ui_icons.json"
    img = Image.frombytes("RGBA", atlas.get_size(), pygame.image.tobytes(atlas, "RGBA"))
    img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(out_png, format="PNG", optimize=True)
    out_json.write_text(json.dumps({"tile": TILE, "cols": len(keys), "icons": id_map}, separators=(",", ":")))
    print(f"Wrote {out_png.name} ({out_png.stat().st_size / 1024:.1f} KB) with {len(id_map)} UI/Stat icons: {list(id_map)}")


if __name__ == "__main__":
    main()
