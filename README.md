# Buddy — Claude Code Virtual Pet

A desktop tamagotchi for your [Claude Code](https://claude.ai/claude-code) companion. Reads your buddy's name, species, stats, and personality directly from your Claude config — no setup needed.

![macOS only](https://img.shields.io/badge/platform-macOS-lightgrey)

## Features

- **Your actual Claude buddy** — species, rarity, eye, hat, stats, and personality are computed from your account using the same hash + PRNG as Claude Code
- **Tamagotchi mechanics** — hunger, happiness, energy, health, and weight stats that decay over time and persist across sessions
- **Interactions** — pet, feed, stroke, play, rest — each with cooldowns, diminishing returns, and consequences (overfeeding makes your buddy sick!)
- **Animated sprites** — 3-frame ASCII art for all 18 species with idle, fidget, and special animations
- **Always-on-top** — floats over all windows, drag it anywhere
- **Reroll menu** — change species, rarity, eyes, hat, shiny status (patches the Claude binary to make it permanent)
- **Stats panel** — double-click to see vitals, companion stats, personality, XP, and level

## Install

Requires **macOS** and **Python 3.10+** (ships with macOS or `brew install python`).

```bash
git clone https://github.com/ThePaulAdams/ClaudeBuddyMax.git buddy
cd buddy
./install.sh
```

The install script:
1. Creates a Python virtual environment
2. Installs PyObjC (macOS native UI bindings)
3. Builds a `Buddy.app` bundle with the correct Python path for your system

## Launch

```bash
open Buddy.app
```

Or double-click `Buddy.app` in Finder.

## How It Works

Your buddy is determined by your Claude Code account. The app reads `~/.claude.json` for your `accountUuid` and companion name/personality, then computes species, rarity, eye, hat, and stats using the same algorithm as Claude Code:

1. `wyhash(accountUuid + "friend-2026-401")` → 32-bit seed (matching `Bun.hash`)
2. `mulberry32(seed)` → PRNG stream (with exact JS 32-bit signed integer overflow)
3. Roll rarity → species → eye → hat → shiny → stats

The wyhash implementation (`wyhash.py`) is a pure Python port of the Zig stdlib Wyhash, verified against all official test vectors.

## Controls

| Action | How |
|--------|-----|
| **Pet** | Click the buddy |
| **Stats panel** | Double-click |
| **Drag** | Click and drag |
| **Menu** | Right-click (interactions, reroll, quit) |

## Tamagotchi System

### Stats (0-100)

| Stat | Decays | Notes |
|------|--------|-------|
| Hunger | ~1/min | Ideal range 25-85. Below 5 = starving (health damage). Above 85 = overfed |
| Happiness | ~0.5/min | Faster when hungry, sick, or exhausted |
| Energy | ~0.25/min | Below 8 = auto-sleep. Play costs energy |
| Health | Conditional | Damaged by starvation, obesity, exhaustion. Recovers when other stats are OK |
| Weight | Drifts to 50 | Overfeeding increases it. Play burns it. High weight = sluggish |

### Interactions

| Action | Effect | Cooldown |
|--------|--------|----------|
| **Feed** | +25 hunger, +5 happy, +3 energy. Overfeeding (>85) → weight gain, sickness | 20s |
| **Pet** | +18 happiness. Wakes sleeping buddy | 5s soft |
| **Stroke** | +12 happiness, +5 energy. Best when sick (reduces sick time) | 5s soft |
| **Play** | +22 happiness, -22 energy, burns weight. Refuses when tired/sick | 15s |
| **Rest** | Puts buddy to sleep. Energy recovers at 1.5/min while sleeping | 30s |

All interactions have **diminishing returns** — repeating the same action within 60s gets progressively weaker (floors at 20% effectiveness).

### Status Effects

- **Sick** — triggered by health <25, prolonged starvation, or overfeed food coma. Doubles decay, caps happiness at 45. Stroke reduces sick time.
- **Sleeping** — auto-triggers at energy <8 or via Rest. Energy recovers. Can't play or feed.
- **Stuffed** — 2min food coma from overfeeding when hunger >85.

### Progression

- XP from well-timed interactions → level up (Lv1 needs 50xp, Lv2 needs 100, etc.)
- Level up grants +10 health, +15 happiness
- Age titles: Newborn → Baby → Toddler → Junior → Adult → Elder → Ancient

## Rerolling
I haven't tested this, it might not work
Right-click → Reroll menus let you change your buddy's appearance. This works in two stages:

1. **Instant** — visual update in the pet app
2. **Background** — brute-forces a new salt, patches the Claude Code binary, and re-signs it

**Important:** Claude Code must be quit before the binary patch can apply. After patching, restart Claude and run `/buddy` to see the change.

A backup of the original binary is saved to `~/.local/share/claude/versions/<version>.backup`. Claude Code updates will overwrite the patch — just reroll again after updating.

## File Structure

```
buddy/
├── buddy.py          # Main app — UI, tamagotchi engine, reroll logic
├── wyhash.py         # Pure Python Wyhash (matches Bun.hash)
├── install.sh        # Installer — creates venv + Buddy.app
├── icon_512.png      # App icon source
├── Buddy.icns        # macOS icon (generated from icon_512.png)
└── .gitignore
```

Runtime data (created automatically, not committed):
- `~/.claude/buddy_tama.json` — tamagotchi state (hunger, happiness, etc.)
- `~/.claude/buddy_override.json` — reroll overrides

## Requirements

- macOS (uses PyObjC for native Cocoa windows)
- Python 3.10+ with `venv` support
- Claude Code installed with a hatched companion (`/buddy` run at least once)

