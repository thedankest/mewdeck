"""MewDeck entry point: python -m mewdeck [--windowed] [--save PATH] [--list]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog="mewdeck", description="Gamepad-friendly Mewgenics save editor")
    ap.add_argument("--windowed", action="store_true", help="run in a window instead of fullscreen")
    ap.add_argument("--save", type=Path, help="open this .sav directly")
    ap.add_argument("--list", action="store_true", help="print detected saves and exit")
    ap.add_argument("--reset-steven", type=Path, metavar="SAV",
                    help="headless: back up SAV, reset the Steven penalty, exit")
    args = ap.parse_args()

    if args.list or args.reset_steven:
        from . import savefile as sf

        if args.list:
            print("search roots:")
            for r in sf.candidate_save_roots():
                print(("  [x] " if r.is_dir() else "  [ ] ") + str(r))
            for p in sf.find_saves():
                snap = sf.snapshot(p)
                print(f"{p}\n    day={snap.parsed['basic']['current_day']} cats={snap.parsed['total_cats']} "
                      f"steven_penalty={snap.steven.penalty} strikes={snap.steven.strikes}")
        if args.reset_steven:
            backup = sf.apply_changes(args.reset_steven, sf.Changes(steven="cap"), reason="steven-reset")
            st = sf.snapshot(args.reset_steven).steven
            print(f"backup: {backup}\nsavescumlocation={st.penalty} strikes={st.strikes}")
            sys.exit(0 if not st.active else 1)
        return

    from .ui import App

    App(windowed=args.windowed, save=args.save).run()


if __name__ == "__main__":
    main()
