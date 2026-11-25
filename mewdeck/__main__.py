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
    ap.add_argument("--diag", action="store_true",
                    help="print display/controller info, then show live pad input for 20 s")
    ap.add_argument("--reset-steven", type=Path, metavar="SAV",
                    help="headless: back up SAV, reset the Steven penalty, exit")
    ap.add_argument("--heal-all", type=Path, metavar="SAV",
                    help="headless: back up SAV, heal all active cats (HP, injuries, scars), exit")
    args = ap.parse_args()

    if args.diag:
        diag()
        return

    if args.list or args.reset_steven or args.heal_all:
        from . import savefile as sf

        if args.list:
            print("search roots:")
            for r in sf.candidate_save_roots():
                print(("  [x] " if r.is_dir() else "  [ ] ") + str(r))
            for p in sf.find_saves():
                snap = sf.snapshot(p)
                inj = sum(1 for c in snap.parsed["cats"] if c["injured"] or c["wounded"])
                print(f"{p}\n    day={snap.parsed['basic']['current_day']} cats={snap.parsed['total_cats']} "
                      f"injured={inj} steven_penalty={snap.steven.penalty} strikes={snap.steven.strikes} "
                      f"dejavu={snap.steven.dejavu_cats}")
        if args.reset_steven:
            backup = sf.apply_changes(args.reset_steven, sf.Changes(steven="cap"), reason="steven-reset")
            st = sf.snapshot(args.reset_steven).steven
            print(f"backup: {backup}\nsavescumlocation={st.penalty} strikes={st.strikes} dejavu={st.dejavu_cats}")
            sys.exit(0 if not st.active else 1)
        if args.heal_all:
            backup = sf.apply_changes(args.heal_all, sf.Changes(heal_all=True), reason="heal-all")
            snap = sf.snapshot(args.heal_all)
            inj = sum(1 for c in snap.parsed["cats"] if c["injured"] or c["wounded"])
            print(f"backup: {backup}\nhealed {snap.parsed['total_cats']} active cats (remaining injured={inj})")
            sys.exit(0 if inj == 0 else 1)
        return

    from .ui import App

    App(windowed=args.windowed, save=args.save).run()


def diag() -> None:
    import os
    import time

    os.environ.setdefault("SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD", "1")
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
    import pygame

    from . import ui

    pygame.init()
    print("env:", {k: v for k, v in os.environ.items() if k.startswith(("SDL", "Steam", "STEAM", "GAMESCOPE", "WAYLAND",
                                                                           "DISPLAY", "XDG_SESSION"))})
    print("desktop sizes:", pygame.display.get_desktop_sizes(), "driver:", pygame.display.get_driver())
    screen = pygame.display.set_mode((640, 200))
    inp = ui.Input()
    print("pads:", inp.names or "NONE")
    print("press buttons / move the stick for 20 s (Ctrl+C to stop)…")
    end = time.time() + 20
    while time.time() < end:
        for ev in pygame.event.get():
            if ev.type not in (pygame.MOUSEMOTION, pygame.WINDOWMOVED, pygame.ACTIVEEVENT):
                print("event:", pygame.event.event_name(ev.type), ev.dict)
            inp.translate(ev)
        for b in inp.poll():
            print("button:", b)
        screen.fill((30, 20, 40))
        pygame.display.flip()
        time.sleep(1 / 60)


if __name__ == "__main__":
    main()
