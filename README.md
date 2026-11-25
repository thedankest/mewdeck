<div align="center">

<img src="docs/icon.png" width="112" height="112" alt="MewDeck icon" />

# MewDeck

**Native 1080p, gamepad-first Mewgenics save editor for ROG Ally, Steam Deck & Linux (Bazzite / SteamOS)**

[![Download Installer](https://img.shields.io/badge/Download-One--Click%20Installer%20(v1.4)-ffb44c?style=for-the-badge&logo=steam&logoColor=14101a)](https://github.com/thedankest/mewdeck/releases/latest)
[![Platform](https://img.shields.io/badge/Linux-Bazzite%20%7C%20SteamOS-70acff?style=for-the-badge&logo=linux&logoColor=14101a)](#one-click-install-on-bazzite--steamos)
[![Resolution](https://img.shields.io/badge/UI-Native%201080p%20%C2%B7%20Gamepad%20%2B%20Touch-68d894?style=for-the-badge)](#controls)
[![License](https://img.shields.io/badge/License-AGPL--3.0-826c9e?style=for-the-badge)](LICENSE)

*Edit saves **in place** straight inside your Proton prefix — automatic backups before every write, strict round-trip verification, and full Steam library artwork.*

<br />

<img src="docs/screenshots/home.png" alt="MewDeck Home Screen" width="920" />

</div>

---

## Gallery

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/screenshots/cats.png" alt="Adventure Party & Cats" /><br />
      <sub><b>Home & Adventure Cats</b> — Filters, sorting, CoI% inbreeding badges & Graveyard revive</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/screenshots/breeding.png" alt="Cat Organizer & Breeding Helper" /><br />
      <sub><b>Cat Organizer & Breeding Helper</b> — Room optimizer, ENS pairs, CoI% & 1-press apply</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="docs/screenshots/items.png" alt="Items, Wiki Icons & Armor Set Bonuses" /><br />
      <sub><b>Items & Wiki Icons (989 Sprites)</b> — Home/Adventure transfer, filters, sorting & Set Bonuses</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/screenshots/cat-edit.png" alt="Cat Editor" /><br />
      <sub><b>Cat Editor</b> — Rename, rooms, base + bonus stats, class, abilities, mutations & gear</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="docs/screenshots/abilities.png" alt="Unobfuscated Ability Picker" /><br />
      <sub><b>Unobfuscated Abilities, Passives (+ Tier 1/2) & Disorders</b></sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/screenshots/mutations.png" alt="Mutations & Coat Editor" /><br />
      <sub><b>Mutations & Coat Texture</b> — All 14 body parts, birth defects & L↔R mirror</sub>
    </td>
  </tr>
</table>

---

## One-Click Install on Bazzite / SteamOS

> [!TIP]
> **No ZIP extraction, no `git`, no manual Steam setup.** Download one self-contained file and let the native KDE / GNOME wizard do everything.

1. Switch to **Desktop Mode** (*Steam → Power → Switch to Desktop*).
2. Download **[`MewDeck-Installer.run`](https://github.com/thedankest/mewdeck/releases/latest)** from the latest release.
3. Double-click **`MewDeck-Installer.run`** *(if your browser cleared the executable bit, right-click → Properties → Permissions → **Is Executable**, or paste `bash ~/Downloads/MewDeck-Installer.run --gui` in Konsole)*.
4. Click **Install / Update** in the wizard:
   - Installs MewDeck and all unobfuscated game data
   - Creates a private Python (`pygame-ce`) environment
   - Adds **MewDeck** to Steam automatically with full library artwork *(portrait capsule, wide banner, hero, logo, and icon)*
5. Switch back to **Game Mode** and launch **MewDeck** under **Non-Steam**.

<details>
<summary><b>Install or update via terminal / git instead</b></summary>

```bash
# First time
gh repo clone thedankest/mewdeck ~/mewdeck
cd ~/mewdeck && ./install.sh          # or ./install.sh --gui

# Update later
cd ~/mewdeck && git pull && ./install.sh

# Uninstall (keeps all save backups)
cd ~/mewdeck && ./uninstall.sh
```

</details>

---

## Features

| Area | What MewDeck can do |
| :--- | :--- |
| **In-Game Sketchbook UI & 3,674 Wiki Sprites** | Hand-inked Mewgenics sketchbook-and-cardboard aesthetic with **3,674 offline wiki sprites** across **Items (989)**, **Abilities & Passives (1,913)**, **Disorders (125)**, **Furniture (633)**, and all **14 Class Collars** — plus toggleable cozy UI sound effects. |
| **Explicit Save-Only Workflow** | Every edit, Steven reset, heal, and batch action queues cleanly with a `QUEUED` badge — **nothing writes to disk until you click `Save (N)`** in the header (or press `☰` Menu), with automatic pristine + rolling backups and a live **Backup Diff** viewer. |
| **Steven Reset, Batch Actions & Unlocks** | Queue Steven reset (`savescumlocation = 0`, strike cap/wipe, strip **Déjà Vu**), **Auto-block Steven**, **Heal All Cats**, **Clear Party Disorders**, **Max Party Stats**, **Strip All Birth Defects**, **Repair All Gear**, and a **Class Collar & House Room Unlocker**. |
| **Cat Organizer & Breeding Helper** | Built-in **Room Optimizer** (Simulated Annealing using live furniture Stimulation/Health, Eternal Youth buffs, Lover exclusivity & Hater conflict avoidance) with **1-press Apply Plan**, plus **Best Pairs** with **Offspring Simulator** (expected stats, 7s, Spell/Passive %, exact pedigree **CoI%**), **Partner Finder**, and **Room Organizer**. |
| **Cat Editor, Interactive Cards & Presets** | Hoverable / gamepad-inspectable **Ability, Passive & Disorder Cards** with live tooltip previews (click/`A` to edit that exact slot), **Build Presets & Clone/Paste**, **Equip Full Armor Set** (`Y` in Equipment), **Rename cats**, **Move Rooms**, Base + **Level-Up Bonuses**, Mutations (L↔R mirror), and Class Collars. |
| **Items, Furniture, Cloning & Self-Updater** | Browse, sort, filter, and move items **Home ↔ Adventure**, inspect **101 Armor Set Bonuses** and **633 Furniture** items with live room totals, **Clone Save Slots** (`X` on Save Picker), and **update MewDeck right inside Game Mode**. |

---

## Controls

| Input | Action |
| :--- | :--- |
| **`LB` / `RB`** | Switch tabs (*Home · Cats · Items · Furniture · Backups*) · Prev/next cat in Cat Editor |
| **`LT` / `RT`** | Fast page scroll (±10 rows) in long lists *(also `PageUp` / `PageDown` / `[` / `]`)* |
| **D-Pad / Left Stick** | Navigate · `◀ ▶` adjust values (hold to accelerate) or switch filters / Passive Tier 1–2 |
| **`A`** | Select / open editor / add item / **move owned item Home ↔ Adventure** / confirm |
| **`B`** | Back / cancel |
| **`X`** | Context action: max resource, heal/revive cat, remove item/furniture, clear slot, manual backup |
| **`Y`** | Switch **Home Storage / Adventure Backpack / Trash** (Items) · Mirror L↔R (Mutations) · Clear all disorders · Reload |
| **View / `Select` (or `/`)** | Open built-in **Gamepad + Touch Search** · Clear all birth defects (Mutations) |
| **`☰` Menu** | Save all pending changes (with automatic backup) |

*Touchscreen (including a top-right `×` quit button) and keyboard (`Arrows`, `Enter`, `Esc`, `Q`/`E`, `X`, `Y`, `Tab`) also work everywhere.*

---

<details>
<summary><b>CLI flags, Diagnostics & Development</b></summary>

### Command line
```bash
~/.local/share/mewdeck/app/run.sh --windowed             # resizable window (Desktop Mode)
~/.local/share/mewdeck/app/run.sh --list                 # detected saves, injured count & Steven status
~/.local/share/mewdeck/app/run.sh --reset-steven PATH    # headless Steven + Déjà Vu reset (backs up first)
~/.local/share/mewdeck/app/run.sh --heal-all PATH        # headless heal all active cats (backs up first)
~/.local/share/mewdeck/app/run.sh --diag                 # live display & gamepad diagnostics
```

### Tests & building the one-click installer
```bash
python3 tests/test_savefile.py
MEWDECK_TEST_SAVE=/path/to/steamcampaign01.sav python3 tests/test_savefile.py
./build_installer.sh                                     # rebuilds MewDeck-Installer.run
```

> **Steam Cloud note:** if Steam asks about a Cloud sync conflict the next time you launch Mewgenics, pick the **local** save.

</details>

---

## Credits & License

- **LZ4 codec & ability/passive/disorder text:** [accessiblefish/mewgenics-save-editor](https://github.com/accessiblefish/mewgenics-save-editor) (AGPL-3.0)
- **Item descriptions, set bonuses, mutation/furniture/injury databases & binary format reference:** [michael-trinity/mewgenics-savegame-editor](https://github.com/michael-trinity/mewgenics-savegame-editor) (MIT)
- **Steven anti-save-scum reset:** [TaliesinYang/mewgenics-bye-bye-steven](https://github.com/TaliesinYang/mewgenics-bye-bye-steven)
- **Proton save paths:** [Yggdraszil/mewgenics_patcher](https://github.com/Yggdraszil/mewgenics_patcher)
- **Breeding simulation, pedigree CoI & room optimizer reference:** [PurpleMyst/mewgenics_breeding_helper](https://github.com/PurpleMyst/mewgenics_breeding_helper) (MIT) & [frankieg33/MewgenicsBreedingManager](https://github.com/frankieg33/MewgenicsBreedingManager)
- **Item icons:** [mewgenics.wiki.gg](https://mewgenics.wiki.gg/) (packed offline via `scripts/fetch_item_icons.py`)
- **Typography:** [Nunito](https://github.com/googlefonts/nunito) (SIL OFL 1.1)

Licensed under **AGPL-3.0**. Personal, non-commercial tool.
