# MewDeck Improvement Analysis & Roadmap

Audit of MewDeck (`v1.1`) against real Mewgenics v1.1 saves (`300` cats, `44` furniture, `167` properties), `mewgenicseditor.com`, `michael-trinity/mewgenics-savegame-editor`, and Mewgenics Wiki mechanics.

> **Status:** All items in Sections 1–3 below are **implemented, verified against a 300-cat real save (`tests/test_savefile.py`), and shipped in `v1.2`**.

## 1. Data & Unobfuscation Gaps
| Finding | Impact | Plan |
| :--- | :--- | :--- |
| **400-series Birth Defects & `-2` missing parts** (`#412`, `#419`, `#423`, `#-2`) render as raw `#ID` when upstream lacks a localized title | Breaks "zero obfuscation" promise on mutated/inbred cats | Humanize unnamed 400+ birth defects (`Birth Defect (Tail #419)`) and `-2` missing parts (`Missing Eyebrow`, `Missing Mouth`) while preserving full stat penalties & descriptions |
| **Cat Equipment Set Synergy** not aggregated across the 5 slots | Players must mentally count how many pieces of a 3-piece set are equipped | Compute live active Armor Set counts (`2/3`, **`ACTIVE 3/3`**) across all 5 equipped slots on `EquipScreen` |

## 2. Missing Editor & Power-User Capabilities
| Feature | Current State | Plan |
| :--- | :--- | :--- |
| **Variable-length Cat Renaming** | Codec supports it, UI does not expose it | Add **Rename Cat** in `CatEdit` using the built-in gamepad + touch + keyboard modal |
| **Move House Cat Between Rooms** | Read-only room display | Add **Room** selector (`Attic`, `Floor1_Small`, `Floor1_Large`, etc.) in `CatEdit` backed by deterministic `house_state` patching |
| **Cat Level-Up Bonus Stats (`level_bonuses`)** | Parsed/written in `catblob.py`, not editable in UI | Add **Level-Up Bonuses** editor screen / display alongside base stats (`1–7`) |
| **Move Items Home ↔ Adventure & Repair Uses** | Can add/remove per container; no cross-container transfer or use refill | Add **Move Home ↔ Adventure** on owned items + **Refill Uses / Repair Gear** (both per-item and 1-click **Repair All Gear**) |
| **Item Stat / Set / Cursed Filters** | Slot filter + search only | Add `Set Items`, `Cursed`, and stat filter chips (`+Shield`, `+STR`, `+DEX`, `+CON`, `+INT`, `+SPD`, `+CHA`, `+LUCK`) |
| **Advanced Campaign Properties & Unlocks** | Only 10 core properties editable on Home | Add searchable **Properties & Unlocks** editor for all 167 SQLite `properties` (Tink lineage unlocks, weather, boss countdown, pipe/box unlocks, etc.) |
| **Save Integrity Checker & Batch Scrubbing** | Automatic verification on write only | Add **Verify Save Integrity** diagnostic action (SQLite `PRAGMA integrity_check` + LZ4 roundtrip on all cats) + **Clear all birth defects** in `MutationsScreen` |

## 3. Handheld & Gamepad UX Polish
| Improvement | Why it matters on ROG Ally / Steam Deck |
| :--- | :--- |
| **`LT` / `RT` fast page jump (±10 rows)** | Instant paging across 1,021 items, 1,912 abilities, and 633 furniture items without waiting for hold-repeat |
| **Auto-sync Basic Attack on Class Change** | Changing a cat from `Colorless` → `Mage`/`Tank` updates its default basic attack so it behaves properly in combat |
| **Clear search with `B`, live match count, touch pills** | Already shipped; extend keyboard modal into a reusable `TextModal` for both Search and Cat Renaming |
