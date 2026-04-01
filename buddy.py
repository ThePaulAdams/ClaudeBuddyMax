#!/usr/bin/env python3
"""
Buddy — Claude Code Virtual Pet
A draggable, always-on-top desktop pet with tamagotchi mechanics.
Reads your buddy's name, species, and stats from ~/.claude.json automatically.
"""

import json, os, math, time, random, sys, traceback, ctypes
import objc
from Foundation import NSObject, NSMakeRect, NSMakePoint, NSDictionary, NSAttributedString, NSTimer, NSDate, NSRunLoop, NSDefaultRunLoopMode
from AppKit import (
    NSApplication, NSWindow, NSView, NSColor, NSFont,
    NSBorderlessWindowMask, NSBackingStoreBuffered, NSScreen,
    NSBezierPath, NSEvent, NSMenu, NSMenuItem,
    NSTrackingArea, NSTrackingMouseEnteredAndExited, NSTrackingActiveAlways, NSTrackingInVisibleRect,
    NSFontAttributeName, NSForegroundColorAttributeName,
)

# ── Constants ─────────────────────────────────────────────────────────────

PET_W = 170
PET_H = 200
STATS_W = 300
STATS_H = 580

SALT = "friend-2026-401"
RARITIES = ["common", "uncommon", "rare", "epic", "legendary"]
RARITY_WEIGHTS = {"common": 60, "uncommon": 25, "rare": 10, "epic": 4, "legendary": 1}
RARITY_TOTAL = sum(RARITY_WEIGHTS.values())
RARITY_FLOOR = {"common": 5, "uncommon": 15, "rare": 25, "epic": 35, "legendary": 50}
SPECIES = ["duck","goose","blob","cat","dragon","octopus","owl","penguin","turtle","snail","ghost","axolotl","capybara","cactus","robot","rabbit","mushroom","chonk"]
EYES = ["·", "✦", "×", "◉", "@", "°"]
HATS = ["none","crown","tophat","propeller","halo","wizard","beanie","tinyduck"]
STAT_NAMES = ["DEBUGGING","PATIENCE","CHAOS","WISDOM","SNARK"]
RARITY_COLORS = {"common":(0.6,0.6,0.6),"uncommon":(0.2,0.8,0.2),"rare":(0.2,0.5,1.0),"epic":(0.7,0.2,0.9),"legendary":(1.0,0.8,0.0)}

# Interaction moods and their particle configs
INTERACTIONS = {
    "pet":    {"chars": ["♥","✦","★","♡","·"], "color": (1,0.3,0.4), "decay": 3, "count": 8},
    "feed":   {"chars": ["🍕","🍩","🍪","🧀","🍔"], "color": (1,0.8,0.2), "decay": 4, "count": 6},
    "stroke": {"chars": ["~","≈","∿","〰","∼"], "color": (0.6,0.8,1.0), "decay": 3, "count": 10},
    "play":   {"chars": ["⚡","✧","★","⭐","💫"], "color": (1,1,0.3), "decay": 5, "count": 12},
    "rest":   {"chars": ["z","Z","💤","☁","·"], "color": (0.4,0.4,0.8), "decay": 3, "count": 6},
}

# ── Tamagotchi engine ────────────────────────────────────────────────────
#
# Stats (all 0-100):
#   hunger     – fullness. Decays over time. 0=starving, >85=overfed.
#   happiness  – mood. Decays faster when hungry/sick/exhausted.
#   energy     – stamina. Play costs energy; recovers when resting.
#   health     – wellbeing. Damaged by starvation, obesity, exhaustion.
#                Slowly recovers when other stats are in good range.
#   weight     – body mass (hidden-ish). Trends toward 50. Overfeeding
#                increases it; play decreases it. High weight = sluggish.
#
# Derived:
#   sick       – bool, triggered by prolonged bad conditions. Doubles
#                decay, caps happiness, halves energy recovery.
#   sleeping   – bool, auto-triggers when energy < 8. Pet won't play.
#   overfed_until – timestamp, feeding when hunger>85 causes food coma.
#
# Diminishing returns:
#   Each interaction type tracks last 5 timestamps. Repeated actions
#   within 60s get progressively weaker (down to 20% effectiveness).
#
# Cooldowns:
#   feed: 20s hard cooldown (pet refuses if fed too soon)
#   play: 15s cooldown
#   pet/stroke: 5s cooldown (soft — works but at 50% if spammed)

TAMA_FILE = os.path.expanduser("~/.claude/buddy_tama.json")

# Base decay per real minute
DECAY_RATES = {
    "hunger":    1.0,   # empties in ~100 min
    "happiness": 0.5,   # empties in ~200 min
    "energy":    0.25,  # empties in ~400 min (but play drains fast)
}

# Ideal ranges (penalties outside these)
IDEAL = {"hunger": (25, 85), "weight": (35, 65)}

COOLDOWNS = {"feed": 20, "play": 15, "pet": 5, "stroke": 5, "rest": 30}

def _default_tama():
    now = time.time()
    return {
        "hunger": 75, "happiness": 75, "energy": 80, "health": 100, "weight": 50,
        "sick": False, "sick_until": 0, "sleeping": False,
        "overfed_until": 0,
        "last_update": now, "age_born": now,
        "total_interactions": 0, "xp": 0, "level": 1,
        "history": {"feed": [], "pet": [], "stroke": [], "play": []},
        "last_action_msg": "",
    }

def load_tama():
    """Load tamagotchi state, applying offline time-based decay."""
    now = time.time()
    default = _default_tama()
    state = None
    if os.path.exists(TAMA_FILE):
        try:
            with open(TAMA_FILE) as f: state = json.load(f)
        except: state = None
    if not state:
        return default

    # Ensure all keys exist (forward compat)
    for k, v in default.items():
        if k not in state: state[k] = v

    # Apply offline decay
    elapsed_min = max(0, (now - state.get("last_update", now)) / 60.0)
    if elapsed_min > 0:
        _apply_decay(state, elapsed_min)

    # Auto-sleep recovery: if pet was sleeping offline, restore energy
    if state["sleeping"] and elapsed_min > 1:
        recovery = min(elapsed_min * 1.5, 100 - state["energy"])  # 1.5/min while sleeping
        state["energy"] = min(100, state["energy"] + recovery)
        if state["energy"] > 25:
            state["sleeping"] = False

    # Expire sickness
    if state["sick"] and now > state.get("sick_until", 0):
        state["sick"] = False

    # Expire overfed
    if now > state.get("overfed_until", 0):
        state["overfed_until"] = 0

    state["last_update"] = now
    return state

def _apply_decay(state, elapsed_min):
    """Apply time-based stat decay for elapsed_min minutes."""
    sick = state.get("sick", False)
    weight = state.get("weight", 50)
    sick_mult = 1.8 if sick else 1.0

    # Hunger decays (you get hungrier)
    hunger_rate = DECAY_RATES["hunger"] * sick_mult
    if weight < 35: hunger_rate *= 1.3  # underweight = hungrier faster
    state["hunger"] = max(0, state["hunger"] - hunger_rate * elapsed_min)

    # Happiness decays — faster when hungry, sick, or exhausted
    happy_rate = DECAY_RATES["happiness"] * sick_mult
    if state["hunger"] < 20: happy_rate *= 1.5
    if state["energy"] < 15: happy_rate *= 1.3
    if sick: state["happiness"] = min(state["happiness"], 45)  # cap when sick
    state["happiness"] = max(0, state["happiness"] - happy_rate * elapsed_min)

    # Energy decays slowly, but faster if overweight
    energy_rate = DECAY_RATES["energy"]
    if weight > 65: energy_rate *= 1.0 + (weight - 65) / 50  # sluggish
    if not state.get("sleeping", False):
        state["energy"] = max(0, state["energy"] - energy_rate * elapsed_min)
    else:
        # Sleeping: energy RECOVERS
        state["energy"] = min(100, state["energy"] + 1.5 * elapsed_min)
        if state["energy"] > 25:
            state["sleeping"] = False

    # Weight trends toward 50 slowly (metabolism)
    w = state["weight"]
    drift = 0.05 * elapsed_min * (1 if w < 50 else -1)
    state["weight"] = max(5, min(95, w + drift))

    # Health: damaged by bad conditions, recovers in good conditions
    h = state["health"]
    damage = 0
    if state["hunger"] < 5: damage += 0.5 * elapsed_min       # starving
    if state["hunger"] < 0.1: damage += 1.0 * elapsed_min     # critical starvation
    if state["weight"] > 75: damage += 0.3 * elapsed_min      # obesity
    if state["weight"] < 20: damage += 0.3 * elapsed_min      # malnourished
    if state["energy"] < 3: damage += 0.2 * elapsed_min       # exhaustion
    if sick: damage += 0.15 * elapsed_min

    # Recovery when conditions are good
    recovery = 0
    if state["hunger"] > 30 and state["energy"] > 20 and not sick and state["weight"] < 70:
        recovery = 0.2 * elapsed_min
    state["health"] = max(0, min(100, h - damage + recovery))

    # Sickness triggers
    if not sick:
        if state["health"] < 25:
            _make_sick(state, 10)  # 10 min sickness
        elif state["hunger"] < 3 and elapsed_min > 2:
            _make_sick(state, 8)
        elif state.get("overfed_until", 0) > time.time() and state["weight"] > 70:
            _make_sick(state, 6)  # food coma → sick

    # Auto-sleep when exhausted
    if state["energy"] < 8 and not state.get("sleeping", False):
        state["sleeping"] = True

def _make_sick(state, duration_min):
    state["sick"] = True
    state["sick_until"] = time.time() + duration_min * 60
    state["last_action_msg"] = f"💀 {state.get('_name', 'Buddy')} got sick!"

def save_tama(state):
    state["last_update"] = time.time()
    with open(TAMA_FILE, "w") as f: json.dump(state, f, indent=2); f.write("\n")

def _diminishing_factor(history_times, cooldown):
    """Calculate effectiveness multiplier (1.0 → 0.2) based on recent action history."""
    now = time.time()
    recent = [t for t in history_times if now - t < 60]
    if not recent:
        return 1.0
    # Each action in the last 60s reduces effectiveness by 20%
    factor = max(0.2, 1.0 - len(recent) * 0.2)
    return factor

def _on_cooldown(history_times, cooldown):
    """Check if action is on hard cooldown."""
    if not history_times:
        return False
    return (time.time() - history_times[-1]) < cooldown

def tama_interact(state, action):
    """Apply interaction with diminishing returns, cooldowns, and consequences.
    Returns (state, message_str)."""
    now = time.time()
    history = state.get("history", {"feed":[],"pet":[],"stroke":[],"play":[]})
    if action not in history: history[action] = []

    # Prune old history (keep last 60s)
    history[action] = [t for t in history[action] if now - t < 120]

    msg = ""
    cooldown = COOLDOWNS.get(action, 5)

    # ── FEED ──
    if action == "feed":
        if _on_cooldown(history[action], cooldown):
            msg = "🍕 Too soon! Wait a bit..."
            state["last_action_msg"] = msg
            return state, msg
        if state.get("sleeping", False):
            msg = "💤 Zzzz... (sleeping, can't eat)"
            state["last_action_msg"] = msg
            return state, msg

        factor = _diminishing_factor(history[action], cooldown)
        base_feed = 25 * factor

        if state["hunger"] > 85:
            # Overfed! Consequences
            state["hunger"] = min(100, state["hunger"] + 5)
            state["weight"] = min(95, state["weight"] + 4)
            state["happiness"] = max(0, state["happiness"] - 8)
            state["health"] = max(0, state["health"] - 3)
            state["overfed_until"] = now + 120  # 2 min food coma
            msg = "🤢 Overfed! Feeling sick..."
        elif state["hunger"] > 70:
            # Getting full — diminished benefit, slight weight gain
            gained = base_feed * 0.5
            state["hunger"] = min(100, state["hunger"] + gained)
            state["weight"] = min(95, state["weight"] + 1.5)
            state["happiness"] = min(100, state["happiness"] + 3)
            msg = f"🍕 Full... (+{gained:.0f} hunger)"
        else:
            # Good feed in healthy range
            state["hunger"] = min(100, state["hunger"] + base_feed)
            state["happiness"] = min(100, state["happiness"] + 5 * factor)
            state["energy"] = min(100, state["energy"] + 3)
            # Slight weight gain if above ideal
            if state["hunger"] > 60:
                state["weight"] = min(95, state["weight"] + 0.5)
            xp = int(10 * factor)
            state["xp"] = state.get("xp", 0) + xp
            msg = f"🍕 Yum! (+{base_feed:.0f} hunger, +{xp}xp)"

    # ── PET ──
    elif action == "pet":
        factor = _diminishing_factor(history[action], cooldown)
        base = 18 * factor

        if state.get("sleeping", False):
            # Petting a sleeping pet wakes it gently
            state["sleeping"] = False
            state["energy"] = max(0, state["energy"] - 5)  # slight penalty
            state["happiness"] = min(100, state["happiness"] + 8)
            msg = "😴→😊 Woke up from pets!"
        elif state.get("sick", False):
            state["happiness"] = min(45, state["happiness"] + 5)
            # Petting when sick reduces sick duration by 30s
            state["sick_until"] = max(now, state.get("sick_until", now) - 30)
            msg = "🤒 Comforting... (helps recovery)"
        else:
            state["happiness"] = min(100, state["happiness"] + base)
            xp = int(8 * factor)
            state["xp"] = state.get("xp", 0) + xp
            msg = f"♥ Happy! (+{base:.0f} happiness, +{xp}xp)"

    # ── STROKE ──
    elif action == "stroke":
        factor = _diminishing_factor(history[action], cooldown)
        base_happy = 12 * factor
        base_energy = 5 * factor

        if state.get("sick", False):
            # Best action when sick — reduces sick time by 1 minute
            state["sick_until"] = max(now, state.get("sick_until", now) - 60)
            state["happiness"] = min(45, state["happiness"] + 8)
            remaining = max(0, (state.get("sick_until", now) - now) / 60)
            msg = f"✋ Soothing... ({remaining:.0f}min sick left)"
        elif state.get("sleeping", False):
            # Doesn't wake — just comforts
            state["happiness"] = min(100, state["happiness"] + 5)
            msg = "✋ Gentle stroke... (still sleeping)"
        else:
            state["happiness"] = min(100, state["happiness"] + base_happy)
            state["energy"] = min(100, state["energy"] + base_energy)
            # Calming effect: if overfed, helps digestion
            if state.get("overfed_until", 0) > now:
                state["overfed_until"] = max(now, state["overfed_until"] - 30)
                msg = f"✋ Calming... tummy rub helps"
            else:
                xp = int(6 * factor)
                state["xp"] = state.get("xp", 0) + xp
                msg = f"✋ Nice! (+{base_happy:.0f} happy, +{base_energy:.0f} energy)"

    # ── PLAY ──
    elif action == "play":
        if state.get("sleeping", False):
            msg = "💤 Too tired to play..."
            state["last_action_msg"] = msg
            return state, msg
        if _on_cooldown(history[action], cooldown):
            msg = "⚡ Need a breather..."
            state["last_action_msg"] = msg
            return state, msg
        if state.get("sick", False):
            msg = "🤒 Too sick to play..."
            state["last_action_msg"] = msg
            return state, msg

        factor = _diminishing_factor(history[action], cooldown)

        if state["energy"] < 15:
            # Exhausted play — injury risk
            state["energy"] = max(0, state["energy"] - 5)
            state["health"] = max(0, state["health"] - 10)
            state["happiness"] = min(100, state["happiness"] + 5)
            msg = "⚡💢 Too exhausted! (-10 health)"
        elif state["energy"] < 30:
            # Tired play — less fun, more cost
            cost = 18 * factor
            state["energy"] = max(0, state["energy"] - cost)
            state["happiness"] = min(100, state["happiness"] + 10 * factor)
            state["hunger"] = max(0, state["hunger"] - 4)
            state["weight"] = max(5, state["weight"] - 0.8)
            msg = f"⚡ Tired play... (-{cost:.0f} energy)"
        else:
            # Good play
            cost = 22 * factor
            base_happy = 22 * factor
            state["energy"] = max(0, state["energy"] - cost)
            state["happiness"] = min(100, state["happiness"] + base_happy)
            state["hunger"] = max(0, state["hunger"] - 6)
            state["weight"] = max(5, state["weight"] - 1.2)
            xp = int(12 * factor)
            state["xp"] = state.get("xp", 0) + xp
            msg = f"⚡ Wheee! (+{base_happy:.0f} happy, -{cost:.0f} energy, +{xp}xp)"

    # ── REST ──
    elif action == "rest":
        if state.get("sleeping", False):
            msg = "💤 Already sleeping..."
            state["last_action_msg"] = msg
            return state, msg
        if _on_cooldown(history[action], cooldown):
            msg = "💤 Not sleepy yet..."
            state["last_action_msg"] = msg
            return state, msg
        if state["energy"] > 85:
            msg = "😊 Not tired! Too much energy to nap."
            state["last_action_msg"] = msg
            return state, msg
        # Put to sleep — energy recovers via sleeping mechanic in _apply_decay
        state["sleeping"] = True
        state["happiness"] = min(100, state["happiness"] + 5)
        xp = 5
        state["xp"] = state.get("xp", 0) + xp
        msg = f"💤 Nap time... (energy recovers while sleeping, +{xp}xp)"

    # Record in history
    history[action].append(now)
    state["history"] = history
    state["total_interactions"] = state.get("total_interactions", 0) + 1
    state["last_action_msg"] = msg

    # Level up check
    xp = state.get("xp", 0)
    level = state.get("level", 1)
    xp_needed = level * 50  # 50, 100, 150, ...
    if xp >= xp_needed:
        state["xp"] = xp - xp_needed
        state["level"] = level + 1
        state["last_action_msg"] = f"🎉 LEVEL UP! Now level {level + 1}!"
        # Level up bonus: small heal
        state["health"] = min(100, state["health"] + 10)
        state["happiness"] = min(100, state["happiness"] + 15)

    state["last_update"] = time.time()
    return state, msg

def tama_mood(state):
    """Determine mood from weighted stats."""
    if state.get("sick", False): return "sick"
    if state.get("sleeping", False): return "sleeping"
    if state.get("overfed_until", 0) > time.time(): return "stuffed"

    # Weighted average: health matters most, then happiness, then hunger/energy
    h = state.get("health", 50)
    hp = state.get("happiness", 50)
    hu = state.get("hunger", 50)
    en = state.get("energy", 50)
    # Hunger: being in the ideal range (25-85) counts as 100%, outside it drops
    hunger_score = 100 if 25 <= hu <= 85 else max(0, 50 - abs(hu - 55))
    avg = h * 0.3 + hp * 0.3 + hunger_score * 0.2 + en * 0.2

    if avg >= 75: return "ecstatic"
    if avg >= 55: return "happy"
    if avg >= 40: return "content"
    if avg >= 25: return "meh"
    if avg >= 12: return "sad"
    return "miserable"

def tama_age_str(state):
    elapsed = time.time() - state.get("age_born", time.time())
    if elapsed < 3600: return f"{int(elapsed/60)}m"
    if elapsed < 86400: return f"{elapsed/3600:.1f}h"
    return f"{elapsed/86400:.1f}d"

def tama_title(state):
    """Age-based title."""
    days = (time.time() - state.get("age_born", time.time())) / 86400
    level = state.get("level", 1)
    if days < 0.04: return "Newborn"   # ~1 hour
    if days < 1: return "Baby"
    if days < 3: return "Toddler"
    if days < 7: return "Junior"
    if days < 30: return "Adult"
    if days < 100: return "Elder"
    return "Ancient"

# ── Helpers ───────────────────────────────────────────────────────────────

def mulberry32(seed):
    """JS-accurate mulberry32 PRNG with exact 32-bit signed integer overflow."""
    state = [ctypes.c_uint32(seed).value]
    def rng():
        s = ctypes.c_int32(ctypes.c_int32(state[0]).value + 1831565813).value
        state[0] = s
        s_u = ctypes.c_uint32(s).value
        q = ctypes.c_int32((ctypes.c_int32(s ^ (s_u >> 15)).value * ctypes.c_int32(1 | s).value) & 0xFFFFFFFF).value
        q_u = ctypes.c_uint32(q).value
        imul2 = ctypes.c_int32((ctypes.c_int32(q ^ (q_u >> 7)).value * ctypes.c_int32(61 | q).value) & 0xFFFFFFFF).value
        q = ctypes.c_int32(q + imul2).value ^ q
        q_u = ctypes.c_uint32(q).value
        return ((q_u ^ (q_u >> 14)) & 0xFFFFFFFF) / 4294967296
    return rng

def pick(rng, arr): return arr[int(rng() * len(arr))]

def roll_stats(seed_int):
    rng = mulberry32(seed_int)
    roll = rng() * RARITY_TOTAL
    rarity = "common"
    for r in RARITIES:
        roll -= RARITY_WEIGHTS[r]
        if roll < 0: rarity = r; break
    species = pick(rng, SPECIES)
    eye = pick(rng, EYES)
    hat = "none" if rarity == "common" else pick(rng, HATS)
    shiny = rng() < 0.01
    floor = RARITY_FLOOR[rarity]
    peak = pick(rng, STAT_NAMES); dump = pick(rng, STAT_NAMES)
    while dump == peak: dump = pick(rng, STAT_NAMES)
    stats = {}
    for n in STAT_NAMES:
        if n == peak: stats[n] = min(100, floor + 50 + int(rng() * 30))
        elif n == dump: stats[n] = max(1, floor - 10 + int(rng() * 15))
        else: stats[n] = floor + int(rng() * 40)
    return {"rarity":rarity,"species":species,"eye":eye,"hat":hat,"shiny":shiny,"stats":stats}

def load_buddy():
    from wyhash import bun_hash
    config_path = os.path.expanduser("~/.claude.json")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.expanduser("~/.claude"), ".config.json")
    companion = {"name":"Gravy","personality":"A mysterious companion.","hatchedAt":0}
    user_id = "anon"
    if os.path.exists(config_path):
        with open(config_path) as f: config = json.load(f)
        if "companion" in config: companion = config["companion"]
        user_id = config.get("oauthAccount",{}).get("accountUuid", config.get("userID","anon"))
    seed = bun_hash(user_id + SALT)
    roll = roll_stats(seed)
    # Apply overrides from reroll menu
    override_path = os.path.expanduser("~/.claude/buddy_override.json")
    if os.path.exists(override_path):
        with open(override_path) as f: ov = json.load(f)
        for k in ("species", "rarity", "eye", "hat", "shiny"):
            if k in ov: roll[k] = ov[k]
    return {
        "name": companion.get("name","Buddy"),
        "personality": companion.get("personality",""),
        "species": roll["species"], "rarity": roll["rarity"],
        "eye": roll["eye"], "hat": roll["hat"],
        "shiny": roll["shiny"], "stats": roll["stats"],
    }

def save_override(key, value):
    """Save single override to local file."""
    path = os.path.expanduser("~/.claude/buddy_override.json")
    data = {}
    if os.path.exists(path):
        with open(path) as f: data = json.load(f)
    data[key] = value
    with open(path, "w") as f: json.dump(data, f, indent=2); f.write("\n")

def save_override_all(traits):
    """Save all trait overrides at once."""
    path = os.path.expanduser("~/.claude/buddy_override.json")
    with open(path, "w") as f: json.dump(traits, f, indent=2); f.write("\n")

# ── Binary patching (makes rerolls permanent in Claude Code) ─────────────

ORIGINAL_SALT = "friend-2026-401"
SALT_LEN = len(ORIGINAL_SALT)

def find_binary_path():
    """Find the Claude Code binary."""
    import subprocess, shutil
    try:
        paths = subprocess.check_output(["which", "-a", "claude"], text=True).strip().split("\n")
        for p in paths:
            try:
                resolved = os.path.realpath(p.strip())
                if os.path.exists(resolved) and os.path.getsize(resolved) > 1_000_000:
                    return resolved
            except: pass
    except: pass
    versions_dir = os.path.expanduser("~/.local/share/claude/versions")
    if os.path.isdir(versions_dir):
        versions = sorted(os.listdir(versions_dir))
        if versions:
            return os.path.join(versions_dir, versions[-1])
    return None

def find_current_salt(binary_data):
    """Find the current salt in the binary (may have been patched before)."""
    orig = ORIGINAL_SALT.encode()
    if orig in binary_data:
        return ORIGINAL_SALT
    # Scan for previously patched salts (padded numeric format)
    text = binary_data.decode("utf-8", errors="ignore")
    import re
    # Look for our padded salt format: x...x<digits> of SALT_LEN length
    pat = re.compile(r'x{' + str(SALT_LEN - 8) + r'}\d{8}')
    for m in pat.finditer(text):
        if len(m.group()) == SALT_LEN:
            return m.group()
    # Look for friend-YYYY-XXX patterns
    pat2 = re.compile(r'friend-\d{4}-.{' + str(SALT_LEN - 12) + r'}')
    for m in pat2.finditer(text):
        if len(m.group()) == SALT_LEN:
            return m.group()
    return None

def find_config_path():
    """Find Claude's config file."""
    legacy = os.path.expanduser("~/.claude/.config.json")
    if os.path.exists(legacy): return legacy
    default = os.path.expanduser("~/.claude.json")
    if os.path.exists(default): return default
    return None

def get_user_id():
    """Get the userId Claude Code uses for companion rolling."""
    config_path = find_config_path()
    if not config_path: return "anon"
    with open(config_path) as f: config = json.load(f)
    return config.get("oauthAccount", {}).get("accountUuid", config.get("userID", "anon"))

def brute_force_salt(user_id, target, progress_cb=None):
    """Find a salt that produces the target companion traits.
    target is a dict with optional keys: species, rarity, eye, hat, shiny."""
    from wyhash import bun_hash
    start = time.time()
    checked = 0
    # Try padded numeric salts: xxxxxxx00000000 format
    for i in range(1_000_000_000):
        salt = str(i).rjust(SALT_LEN, "x")
        checked += 1
        seed = bun_hash(user_id + salt)
        r = roll_stats(seed)
        if matches_target(r, target):
            return {"salt": salt, "result": r, "checked": checked, "elapsed": time.time() - start}
        if checked % 1_000_000 == 0 and progress_cb:
            progress_cb(checked, time.time() - start)
    return None

def matches_target(roll, target):
    for k in ("species", "rarity", "eye", "hat"):
        if k in target and roll[k] != target[k]: return False
    if "shiny" in target and roll["shiny"] != target["shiny"]: return False
    return True

def patch_binary(binary_path, old_salt, new_salt):
    """Replace old_salt with new_salt in the binary. Returns patch count."""
    if len(old_salt) != len(new_salt):
        raise ValueError(f"Salt length mismatch: {len(old_salt)} vs {len(new_salt)}")
    data = bytearray(open(binary_path, "rb").read())
    old_bytes = old_salt.encode()
    new_bytes = new_salt.encode()
    count = 0
    idx = 0
    while True:
        idx = data.find(old_bytes, idx)
        if idx == -1: break
        data[idx:idx+len(new_bytes)] = new_bytes
        count += 1
        idx += len(new_bytes)
    if count == 0:
        raise RuntimeError(f'Salt "{old_salt}" not found in binary')
    with open(binary_path, "wb") as f: f.write(data)
    return count

def resign_binary(binary_path):
    """Ad-hoc codesign on macOS."""
    import subprocess, platform
    if platform.system() != "Darwin": return False
    try:
        subprocess.run(["codesign", "-s", "-", "--force", binary_path],
                       capture_output=True, check=True)
        return True
    except: return False

def clear_companion_cache(config_path):
    """Remove cached companion data so Claude re-hatches."""
    with open(config_path) as f: raw = f.read()
    config = json.loads(raw)
    config.pop("companion", None)
    config.pop("companionMuted", None)
    indent = "  "
    import re
    m = re.match(r'^(\s+)"', raw, re.MULTILINE)
    if m: indent = m.group(1)
    with open(config_path, "w") as f:
        f.write(json.dumps(config, indent=indent) + "\n")

def is_claude_running():
    """Check if Claude Code is currently running."""
    import subprocess
    try:
        out = subprocess.check_output(["pgrep", "-af", "claude"], text=True)
        for line in out.strip().split("\n"):
            if "buddy" not in line.lower() and "gravy" not in line.lower() and line.strip():
                return True
    except: pass
    return False

def apply_reroll(target):
    """Full reroll: brute-force salt, patch binary, clear cache.
    Returns (success: bool, message: str)."""
    binary_path = find_binary_path()
    if not binary_path:
        return False, "Could not find Claude Code binary"

    config_path = find_config_path()
    if not config_path:
        return False, "Could not find Claude config"

    if is_claude_running():
        return False, "Quit Claude Code first! (the binary can't be patched while running)"

    user_id = get_user_id()
    binary_data = open(binary_path, "rb").read()
    current_salt = find_current_salt(binary_data)
    if not current_salt:
        return False, "Could not find companion salt in binary"

    # Check if already matching
    from wyhash import bun_hash
    current = roll_stats(bun_hash(user_id + current_salt))
    if matches_target(current, target):
        return True, "Already matches!"

    print(f"[Buddy] Brute-forcing salt for target: {target}", file=sys.stderr, flush=True)
    def progress(n, elapsed):
        print(f"[Buddy] {n/1e6:.0f}M salts checked ({elapsed:.1f}s)", file=sys.stderr, flush=True)

    found = brute_force_salt(user_id, target, progress)
    if not found:
        return False, "No matching salt found (try fewer constraints)"

    print(f"[Buddy] Found salt in {found['checked']:,} attempts ({found['elapsed']:.1f}s)", file=sys.stderr, flush=True)

    # Backup
    backup = binary_path + ".backup"
    if not os.path.exists(backup):
        import shutil
        shutil.copy2(binary_path, backup)
        print(f"[Buddy] Backup saved to {backup}", file=sys.stderr, flush=True)

    # Patch
    count = patch_binary(binary_path, current_salt, found["salt"])
    print(f"[Buddy] Patched {count} occurrence(s)", file=sys.stderr, flush=True)

    # Re-sign
    if resign_binary(binary_path):
        print("[Buddy] Binary re-signed", file=sys.stderr, flush=True)

    # Clear companion cache
    clear_companion_cache(config_path)
    print("[Buddy] Companion cache cleared", file=sys.stderr, flush=True)

    # Also update the override file so pet updates instantly
    override_path = os.path.expanduser("~/.claude/buddy_override.json")
    with open(override_path, "w") as f:
        json.dump({k: v for k, v in found["result"].items() if k != "stats"}, f, indent=2)
        f.write("\n")

    return True, f"Patched! ({found['checked']:,} attempts, {found['elapsed']:.1f}s). Restart Claude Code and run /buddy."

# ── Sprites: 3 frames per species (idle, fidget, special), 5 lines x 12 chars ──
# {E} is replaced with the eye character at render time.
# Frame 0 = idle, Frame 1 = fidget, Frame 2 = special/emote.
# Line 0 is the hat slot (blank in base frames, replaced if hat equipped).

def _b(species):
    """Return the 3 frames for a species."""
    S = {
      "duck": [
        ["            ","    __      ","  <({E} )___  ","   (  ._>   ","    `--'    "],
        ["            ","    __      ","  <({E} )___  ","   (  ._>   ","    `--'~   "],
        ["            ","    __      ","  <({E} )___  ","   (  .__>  ","    `--'    "],
      ],
      "goose": [
        ["            ","     ({E}>    ","     ||     ","   _(__)_   ","    ^^^^    "],
        ["            ","    ({E}>     ","     ||     ","   _(__)_   ","    ^^^^    "],
        ["            ","     ({E}>>   ","     ||     ","   _(__)_   ","    ^^^^    "],
      ],
      "blob": [
        ["            ","   .----.   ","  ( {E}  {E} )  ","  (      )  ","   `----'   "],
        ["            ","  .------.  "," (  {E}  {E}  ) "," (        ) ","  `------'  "],
        ["            ","    .--.    ","   ({E}  {E})   ","   (    )   ","    `--'    "],
      ],
      "cat": [
        ["            ","   /\\_/\\    ","  ( {E}   {E})  ","  (  w  )   ","  (\")_(\")   "],
        ["            ","   /\\_/\\    ","  ( {E}   {E})  ","  (  w  )   ","  (\")_(\")~  "],
        ["            ","   /\\-/\\    ","  ( {E}   {E})  ","  (  w  )   ","  (\")_(\")   "],
      ],
      "dragon": [
        ["            ","  /^\\  /^\\  "," <  {E}  {E}  > "," (   ~~   ) ","  `-vvvv-'  "],
        ["            ","  /^\\  /^\\  "," <  {E}  {E}  > "," (        ) ","  `-vvvv-'  "],
        ["   ~    ~   ","  /^\\  /^\\  "," <  {E}  {E}  > "," (   ~~   ) ","  `-vvvv-'  "],
      ],
      "octopus": [
        ["            ","   .----.   ","  ( {E}  {E} )  ","  (______)  ","  /\\/\\/\\/\\  "],
        ["            ","   .----.   ","  ( {E}  {E} )  ","  (______)  ","  \\/\\/\\/\\/  "],
        ["     o      ","   .----.   ","  ( {E}  {E} )  ","  (______)  ","  /\\/\\/\\/\\  "],
      ],
      "owl": [
        ["            ","   /\\  /\\   ","  (({E})({E}))  ","  (  ><  )  ","   `----'   "],
        ["            ","   /\\  /\\   ","  (({E})({E}))  ","  (  ><  )  ","   .----.   "],
        ["            ","   /\\  /\\   ","  (({E})(-))  ","  (  ><  )  ","   `----'   "],
      ],
      "penguin": [
        ["            ","  .---.     ","  ({E}>{E})     "," /(   )\\    ","  `---'     "],
        ["            ","  .---.     ","  ({E}>{E})     "," |(   )|    ","  `---'     "],
        ["  .---.     ","  ({E}>{E})     "," /(   )\\    ","  `---'     ","   ~ ~      "],
      ],
      "turtle": [
        ["            ","   _,--._   ","  ( {E}  {E} )  "," /[______]\\ ","  ``    ``  "],
        ["            ","   _,--._   ","  ( {E}  {E} )  "," /[______]\\ ","   ``  ``   "],
        ["            ","   _,--._   ","  ( {E}  {E} )  "," /[======]\\ ","  ``    ``  "],
      ],
      "snail": [
        ["            "," {E}    .--.  ","  \\  ( @ )  ","   \\_`--'   ","  ~~~~~~~   "],
        ["            ","  {E}   .--.  ","  |  ( @ )  ","   \\_`--'   ","  ~~~~~~~   "],
        ["            "," {E}    .--.  ","  \\  ( @  ) ","   \\_`--'   ","   ~~~~~~   "],
      ],
      "ghost": [
        ["            ","   .----.   ","  / {E}  {E} \\  ","  |      |  ","  ~`~``~`~  "],
        ["            ","   .----.   ","  / {E}  {E} \\  ","  |      |  ","  `~`~~`~`  "],
        ["    ~  ~    ","   .----.   ","  / {E}  {E} \\  ","  |      |  ","  ~~`~~`~~  "],
      ],
      "axolotl": [
        ["            ","}~(______)~{","}~({E} .. {E})~{","  ( .--. )  ","  (_/  \\_)  "],
        ["            ","~}(______){~","~}({E} .. {E}){~","  ( .--. )  ","  (_/  \\_)  "],
        ["            ","}~(______)~{","}~({E} .. {E})~{","  (  --  )  ","  ~_/  \\_~  "],
      ],
      "capybara": [
        ["            ","  n______n  "," ( {E}    {E} ) "," (   oo   ) ","  `------'  "],
        ["            ","  n______n  "," ( {E}    {E} ) "," (   Oo   ) ","  `------'  "],
        ["    ~  ~    ","  u______n  "," ( {E}    {E} ) "," (   oo   ) ","  `------'  "],
      ],
      "cactus": [
        ["            "," n  ____  n "," | |{E}  {E}| | "," |_|    |_| ","   |    |   "],
        ["            ","    ____    "," n |{E}  {E}| n "," |_|    |_| ","   |    |   "],
        [" n        n "," |  ____  | "," | |{E}  {E}| | "," |_|    |_| ","   |    |   "],
      ],
      "robot": [
        ["            ","   .[||].   ","  [ {E}  {E} ]  ","  [ ==== ]  ","  `------'  "],
        ["            ","   .[||].   ","  [ {E}  {E} ]  ","  [ -==- ]  ","  `------'  "],
        ["     *      ","   .[||].   ","  [ {E}  {E} ]  ","  [ ==== ]  ","  `------'  "],
      ],
      "rabbit": [
        ["            ","   (\\__/)   ","  ( {E}  {E} )  "," =(  ..  )= ","  (\")__(\" ) "],
        ["            ","   (|__/)   ","  ( {E}  {E} )  "," =(  ..  )= ","  (\")__(\" ) "],
        ["            ","   (\\__/)   ","  ( {E}  {E} )  "," =( .  . )= ","  (\")__(\" ) "],
      ],
      "mushroom": [
        ["            "," .-o-OO-o-. ","(__________)","   |{E}  {E}|   ","   |____|   "],
        ["            "," .-O-oo-O-. ","(__________)","   |{E}  {E}|   ","   |____|   "],
        ["   . o  .   "," .-o-OO-o-. ","(__________)","   |{E}  {E}|   ","   |____|   "],
      ],
      "chonk": [
        ["            ","  /\\    /\\  "," ( {E}    {E} ) "," (   ..   ) ","  `------'  "],
        ["            ","  /\\    /|  "," ( {E}    {E} ) "," (   ..   ) ","  `------'  "],
        ["            ","  /\\    /\\  "," ( {E}    {E} ) "," (   ..   ) ","  `------'~ "],
      ],
    }
    return S.get(species, S["chonk"])

HAT_LINES = {
    "none":      "",
    "crown":     "   \\^^^/    ",
    "tophat":    "   [___]    ",
    "propeller": "    -+-     ",
    "halo":      "   (   )    ",
    "wizard":    "    /^\\     ",
    "beanie":    "   (___)    ",
    "tinyduck":  "    ,>      ",
}

def get_sprite(species, eye, hat, frame=0):
    """Render a sprite with eye substitution and hat placement. Returns list of strings."""
    frames = _b(species)
    body = [line.replace("{E}", eye) for line in frames[frame % len(frames)]]
    # Hat on line 0 if that line is blank
    if hat != "none" and not body[0].strip():
        body[0] = HAT_LINES.get(hat, "")
    return body

# ── NSColor helper ────────────────────────────────────────────────────────

def C(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)

def text(s, x, y, size, color, bold=False):
    fn = NSFont.fontWithName_size_("Menlo-Bold" if bold else "Menlo", size)
    if not fn: fn = NSFont.monospacedSystemFontOfSize_weight_(size, 0.5 if bold else 0)
    attrs = NSDictionary.dictionaryWithObjects_forKeys_([fn, color], [NSFontAttributeName, NSForegroundColorAttributeName])
    NSAttributedString.alloc().initWithString_attributes_(s, attrs).drawAtPoint_(NSMakePoint(x, y))

# ── Draw functions (standalone to avoid PyObjC selector issues) ───────────

def draw_pet(view, rect):
    b = view.buddy
    if not b: C(0.2,0,0.2,1).set(); NSBezierPath.fillRect_(rect); return
    tama = view.tama

    r, g, bl = RARITY_COLORS.get(b["rarity"], (0.6,0.6,0.6))
    bob = math.sin(view._t) * 3.0
    eye = "─" if view._blink else b["eye"]

    # Mood-based eye override from tamagotchi state
    mood = tama_mood(tama) if tama else "happy"
    if not view._blink and view._action_t <= 0:
        if mood == "miserable": eye = "×"
        elif mood == "sad": eye = "◠"
        elif mood == "sick": eye = "@"
        elif mood == "sleeping": eye = "─"
        elif mood == "stuffed": eye = "◎"
        elif mood == "ecstatic": eye = "✦"

    # Interaction-specific animations
    squash = 1.0
    wobble = 0.0
    if view._action == "stroke" and view._action_t > 0:
        wobble = math.sin(view._t * 8) * 4 * view._action_t
    elif view._action == "play" and view._action_t > 0:
        bob += abs(math.sin(view._t * 6)) * 15 * view._action_t
    elif view._action == "feed" and view._action_t > 0:
        squash = 1.0 + 0.08 * math.sin(view._t * 4) * view._action_t
    elif view._action == "pet" and view._action_t > 0:
        eye = "◡" if not view._blink else "─"

    # Mood-based body animation
    if mood in ("sad", "miserable") and view._action_t <= 0:
        bob -= 5; squash = 0.95
    elif mood == "sick" and view._action_t <= 0:
        wobble = math.sin(view._t * 2) * 2  # queasy wobble
    elif mood == "stuffed" and view._action_t <= 0:
        squash = 1.06  # bloated
    elif mood == "sleeping" and view._action_t <= 0:
        bob = math.sin(view._t * 0.5) * 2  # slow breathing

    # Background
    C(0.08, 0.08, 0.12, 0.92).set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 16, 16).fill()
    C(r, g, bl, 0.4).set()
    p = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 16, 16)
    p.setLineWidth_(1.5); p.stroke()

    # Glow for epic/legendary
    if b["rarity"] in ("epic","legendary"):
        C(r,g,bl, 0.1+0.05*math.sin(view._t*2)).set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(15,15+bob,PET_W-30,PET_H-40)).fill()

    # Shiny shimmer
    if b["shiny"]:
        C(1,1,0.8, 0.08+0.06*math.sin(view._t*3)).set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(20,20+bob,PET_W-40,PET_H-50)).fill()

    # Sprite with animation frames
    color = C(r, g, bl)
    lines = get_sprite(b["species"], eye, b["hat"], view._frame)
    # Sprites are 12 chars wide at ~10px/char = ~120px. Centre in PET_W.
    x_off = max(0, (PET_W - 120) // 2) - 5
    for i, line in enumerate(lines):
        lx = x_off + wobble * (1 if i % 2 == 0 else -1)
        ly = PET_H - 38 + bob - i * 16 * squash
        text(line, lx, ly, 14, color)

    # Name + mood emoji (just above the bars)
    mood_icon = {"ecstatic":"✨","happy":"","content":"","meh":"💤","sad":"💧",
                 "miserable":"💀","sick":"🤢","sleeping":"💤","stuffed":"🫃"}.get(mood,"")
    text(f"~ {b['name']} ~ {mood_icon}", 35, 32 + bob, 11, color, bold=True)

    # Action message bubble (fades out)
    if tama and view._action_t > 0.3:
        msg = tama.get("last_action_msg", "")
        if msg:
            alpha = min(1.0, (view._action_t - 0.3) * 3)
            text(msg[:22], 8, PET_H - 8, 8, C(0.9, 0.9, 0.9, alpha))

    # ── Tamagotchi bars stacked horizontally below the name ──
    if tama:
        bar_w = PET_W - 40
        bar_h = 3
        bars = [
            ("🍕", tama.get("hunger", 50), (1.0, 0.6, 0.2)),
            ("♥",  tama.get("happiness", 50), (1.0, 0.3, 0.5)),
            ("⚡", tama.get("energy", 50), (0.3, 0.8, 1.0)),
            ("❤",  tama.get("health", 100), (0.2, 0.9, 0.3)),
        ]
        for idx, (icon, val, (br, bg, bb)) in enumerate(bars):
            by = 24 - idx * 6
            text(icon, 8, by - 1, 6, C(0.45, 0.45, 0.45))
            C(0.2, 0.2, 0.2, 0.5).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(20, by + 1, bar_w, bar_h), 1.5, 1.5).fill()
            fill = max(1, (val / 100.0) * bar_w)
            if val < 20: br, bg, bb = 1.0, 0.2, 0.2
            elif val > 85 and icon == "🍕": br, bg, bb = 1.0, 0.5, 0.0  # overfed warning
            C(br, bg, bb, 0.85).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(20, by + 1, fill, bar_h), 1.5, 1.5).fill()

    # Action emoji indicator
    if view._action_t > 0:
        action_icons = {"pet": "♥", "feed": "🍕", "stroke": "✋", "play": "⚡"}
        icon = action_icons.get(view._action, "♥")
        alpha = min(1.0, view._action_t * 2)
        float_y = PET_H - 15 + math.sin(view._t * 4) * 5
        text(icon, 10, float_y, 20, C(1, 0.3, 0.4, alpha))

    # Sleeping Zzz
    if tama and mood == "sleeping" and view._action_t <= 0:
        zz_y = PET_H - 25 + math.sin(view._t * 1.5) * 4
        text("z z Z", PET_W - 50, zz_y, 10, C(0.5, 0.5, 0.8, 0.6 + 0.3 * math.sin(view._t * 2)))

    # Sick sweat drops
    if tama and mood == "sick" and view._action_t <= 0:
        if int(view._t * 4) % 3 == 0:
            text("💦", PET_W - 30, PET_H - 50 + math.sin(view._t * 3) * 3, 10, C(0.5, 0.8, 0.5, 0.7))

    # Hunger rumble when very hungry
    if tama and tama.get("hunger", 50) < 15 and view._action_t <= 0:
        if int(view._t * 3) % 4 == 0:
            text("...", 15, 30, 10, C(0.8, 0.5, 0.2, 0.5))

    # Particles
    for pa in view._particles:
        pr, pg, pb = pa.get("cr", 1), pa.get("cg", 0.8), pa.get("cb", 0.2)
        text(pa["char"], pa["x"], pa["y"], pa.get("size", 12), C(pr, pg, pb, pa["life"]))


def draw_stats(view, rect):
    b = view.buddy
    if not b: return
    tama = view.tama

    r, g, bl = RARITY_COLORS.get(b["rarity"], (0.6,0.6,0.6))

    # Background
    C(0.1,0.1,0.12,0.95).set()
    bp = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 10, 10)
    bp.fill()
    C(r,g,bl,0.6).set(); bp.setLineWidth_(2); bp.stroke()

    y = 15
    text(f"✧ {b['name']} ✧", 15, y, 16, C(r,g,bl), bold=True); y += 25
    label = f"{b['species'].upper()}  •  {b['rarity'].upper()}" + ("  ✦SHINY✦" if b["shiny"] else "")
    text(label, 15, y, 11, C(0.7,0.7,0.7)); y += 20
    text(f"Eye: {b['eye']}   Hat: {b['hat']}", 15, y, 11, C(0.5,0.5,0.5)); y += 25

    # ── Tamagotchi vitals ──
    if tama:
        def divider(yy):
            C(0.3,0.3,0.3).set()
            d = NSBezierPath.bezierPath(); d.moveToPoint_(NSMakePoint(15,yy)); d.lineToPoint_(NSMakePoint(STATS_W-15,yy)); d.setLineWidth_(1); d.stroke()

        divider(y); y += 12
        mood = tama_mood(tama)
        mood_labels = {
            "ecstatic":"✨ Ecstatic","happy":"😊 Happy","content":"🙂 Content",
            "meh":"😐 Meh","sad":"😢 Sad","miserable":"💀 Miserable",
            "sick":"🤢 Sick","sleeping":"💤 Sleeping","stuffed":"🫃 Stuffed",
        }
        age = tama_age_str(tama)
        title = tama_title(tama)
        level = tama.get("level", 1)
        text("VITALS", 15, y, 12, C(r,g,bl), bold=True)
        text(f"Lv.{level} {title}", 80, y, 9, C(0.5,0.5,0.5))
        y += 16
        text(f"Age: {age}   Mood: {mood_labels.get(mood, mood)}", 15, y, 9, C(0.45,0.45,0.45))
        y += 16

        # XP bar
        xp = tama.get("xp", 0)
        xp_needed = level * 50
        text(f"XP", 15, y, 9, C(0.5,0.5,0.5))
        xp_bar_w = 100
        C(0.2,0.2,0.2).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(35,y+2,xp_bar_w,6),3,3).fill()
        xp_fill = max(1, (xp / max(1, xp_needed)) * xp_bar_w)
        C(0.8,0.7,0.2).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(35,y+2,xp_fill,6),3,3).fill()
        text(f"{xp}/{xp_needed}", 140, y, 9, C(0.5,0.5,0.5))
        y += 16

        vital_bars = [
            ("Hunger  🍕", tama.get("hunger", 50), (1.0, 0.6, 0.2), True),
            ("Happy   ♥ ", tama.get("happiness", 50), (1.0, 0.3, 0.5), False),
            ("Energy  ⚡", tama.get("energy", 50), (0.3, 0.8, 1.0), False),
            ("Health  ❤ ", tama.get("health", 100), (0.2, 0.9, 0.3), False),
            ("Weight  ⚖ ", tama.get("weight", 50), (0.6, 0.6, 0.8), True),
        ]
        bar_w = 125
        for label_str, val, (br, bg, bb), has_ideal in vital_bars:
            text(label_str, 15, y, 10, C(0.6,0.6,0.6))
            # Background
            C(0.2,0.2,0.2).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(110,y+1,bar_w,10),3,3).fill()
            # Ideal range indicator for hunger/weight
            if has_ideal:
                ideal = IDEAL.get(label_str.strip().split()[0].lower(), None)
                if ideal:
                    lo, hi = ideal
                    ix = 110 + (lo / 100) * bar_w
                    iw = ((hi - lo) / 100) * bar_w
                    C(0.25, 0.25, 0.25).set()
                    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(ix, y+1, iw, 10), 3, 3).fill()
            # Fill
            fill_pct = val / 100.0
            if val < 20: br, bg, bb = 1.0, 0.2, 0.2
            elif has_ideal and label_str.startswith("Hunger") and val > 85: br, bg, bb = 1.0, 0.4, 0.0
            elif has_ideal and label_str.startswith("Weight") and val > 65: br, bg, bb = 0.9, 0.4, 0.4
            C(br, bg, bb).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(110,y+1,max(2,fill_pct*bar_w),10),3,3).fill()
            text(f"{int(val)}", 245, y, 10, C(0.8,0.8,0.8))
            y += 18

        # Status effects
        effects = []
        if tama.get("sick"): effects.append("🤢 SICK")
        if tama.get("sleeping"): effects.append("💤 SLEEPING")
        if tama.get("overfed_until", 0) > time.time(): effects.append("🫃 OVERFED")
        if effects:
            text("  ".join(effects), 15, y, 9, C(1, 0.3, 0.3))
            y += 14

        # Last action message
        msg = tama.get("last_action_msg", "")
        if msg:
            text(msg[:40], 15, y, 9, C(0.6, 0.6, 0.5))
            y += 14

        text(f"Interactions: {tama.get('total_interactions', 0)}", 15, y, 9, C(0.35,0.35,0.35))
        y += 16

    # ── Companion stats ──
    C(0.3,0.3,0.3).set()
    dp = NSBezierPath.bezierPath(); dp.moveToPoint_(NSMakePoint(15,y)); dp.lineToPoint_(NSMakePoint(STATS_W-15,y)); dp.setLineWidth_(1); dp.stroke()
    y += 12

    text("STATS", 15, y, 12, C(r,g,bl), bold=True); y += 22
    bar_w = 130
    for name in STAT_NAMES:
        val = b["stats"].get(name, 0)
        text(name, 15, y, 10, C(0.6,0.6,0.6))
        C(0.2,0.2,0.2).set(); NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(115,y+1,bar_w,10),3,3).fill()
        intensity = val/100.0
        C(r*0.5+intensity*0.5, g*0.5+intensity*0.3, bl*0.5+intensity*0.2).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(115,y+1,max(2,val/100*bar_w),10),3,3).fill()
        text(str(val), 255, y, 10, C(0.8,0.8,0.8))
        y += 20

    y += 10
    C(0.3,0.3,0.3).set()
    dp2 = NSBezierPath.bezierPath(); dp2.moveToPoint_(NSMakePoint(15,y)); dp2.lineToPoint_(NSMakePoint(STATS_W-15,y)); dp2.setLineWidth_(1); dp2.stroke()
    y += 12

    text("PERSONALITY", 15, y, 10, C(r,g,bl), bold=True); y += 18
    words = b["personality"].split(); line = ""
    for w in words:
        test_line = line + (" " if line else "") + w
        if len(test_line) > 42:
            text(line, 15, y, 9, C(0.5,0.5,0.5)); y += 14; line = w
        else: line = test_line
    if line: text(line, 15, y, 9, C(0.5,0.5,0.5))


# ── Pet View ──────────────────────────────────────────────────────────────

class PetView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(PetView, self).initWithFrame_(frame)
        if self is None: return None
        self.buddy = None
        self.tama = None
        self._t = 0.0
        self._blink = False
        self._blink_t = 0.0
        self._mood = "happy"
        self._action = None       # current interaction: pet/feed/stroke/play
        self._action_t = 0.0      # time remaining for action animation
        self._particles = []
        self._drag_start = None
        self._dragged = False
        self._tama_tick = 0       # counter for periodic tama decay + save
        self._last_save = 0
        self._frame = 0           # sprite animation frame (0=idle, 1=fidget, 2=special)
        self._frame_timer = 0     # ticks until next frame change
        ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect, self, None)
        self.addTrackingArea_(ta)
        return self

    def acceptsFirstMouse_(self, event): return True

    def mouseDown_(self, event):
        self._drag_start = event.locationInWindow()
        self._dragged = False

    def mouseDragged_(self, event):
        self._dragged = True
        win = self.window()
        if not win or not self._drag_start: return
        loc = NSEvent.mouseLocation()
        new_origin = NSMakePoint(loc.x - self._drag_start.x, loc.y - self._drag_start.y)
        win.setFrameOrigin_(new_origin)
        # Move stats window with pet if visible
        delegate = NSApplication.sharedApplication().delegate()
        if delegate and delegate._stats_visible:
            delegate.repositionStats()

    def mouseUp_(self, event):
        if not self._dragged:
            if event.clickCount() >= 2:
                NSApplication.sharedApplication().delegate().toggleStats()
            else:
                do_interaction(self, "pet")
        self._drag_start = None

    def rightMouseDown_(self, event):
        m = self.window().menu()
        if m: NSMenu.popUpContextMenu_withEvent_forView_(m, event, self)

    def tick_(self, timer):
        self._t += 0.05
        self._blink_t += 0.05
        if not self._blink and random.random() < 0.005: self._blink = True; self._blink_t = 0
        if self._blink and self._blink_t > 0.3: self._blink = False

        # Decay action animation
        if self._action_t > 0:
            self._action_t -= 0.02
            if self._action_t <= 0:
                self._action = None
                self._action_t = 0

        # Sprite frame cycling
        self._frame_timer -= 1
        if self._frame_timer <= 0:
            if self._action_t > 0:
                # During interactions, cycle faster between fidget/special
                self._frame = random.choice([1, 2])
                self._frame_timer = random.randint(8, 15)
            elif self.tama and tama_mood(self.tama) == "sleeping":
                self._frame = 0  # idle only when sleeping
                self._frame_timer = 60
            else:
                # Mostly idle, occasionally fidget
                self._frame = 0 if random.random() < 0.7 else 1
                self._frame_timer = random.randint(30, 90)

        # Tamagotchi real-time decay (every ~2 seconds at 30fps = every 60 ticks)
        self._tama_tick += 1
        if self.tama and self._tama_tick % 60 == 0:
            _apply_decay(self.tama, 2.0 / 60.0)  # 2 seconds worth of decay
            # Expire sickness/overfed
            now = time.time()
            if self.tama.get("sick") and now > self.tama.get("sick_until", 0):
                self.tama["sick"] = False
            if now > self.tama.get("overfed_until", 0):
                self.tama["overfed_until"] = 0
            # Sync stats view
            delegate = NSApplication.sharedApplication().delegate()
            if delegate and delegate._stats_visible:
                delegate._stats_view.tama = self.tama
                delegate._stats_view.setNeedsDisplay_(True)
            # Save every ~30 seconds
            if self._tama_tick % 900 == 0:
                save_tama(self.tama)

        # Update particles
        alive = []
        for p in self._particles:
            p["life"] -= 0.025
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["vy"] += 0.02  # slight gravity
            if p["life"] > 0: alive.append(p)
        self._particles = alive

        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        try: draw_pet(self, rect)
        except: traceback.print_exc(file=sys.stderr); C(1,0,0,1).set(); NSBezierPath.fillRect_(rect)


def do_interaction(view, action):
    """Trigger an interaction animation and update tamagotchi state."""
    cfg = INTERACTIONS.get(action, INTERACTIONS["pet"])
    view._action = action
    view._action_t = 1.0
    cr, cg, cb = cfg["color"]
    for _ in range(cfg["count"]):
        view._particles.append({
            "x": random.uniform(15, PET_W-15),
            "y": random.uniform(30, PET_H-30),
            "vx": random.uniform(-2.5, 2.5),
            "vy": random.uniform(-3.5, -0.5),
            "life": 1.0,
            "char": random.choice(cfg["chars"]),
            "cr": cr, "cg": cg, "cb": cb,
            "size": random.choice([10, 12, 14]),
        })
    # Update tamagotchi
    if view.tama:
        _, msg = tama_interact(view.tama, action)
        save_tama(view.tama)
        if msg:
            print(f"[Buddy] {msg}", file=sys.stderr, flush=True)
        # Sync to stats view
        delegate = NSApplication.sharedApplication().delegate()
        if delegate:
            delegate._stats_view.tama = view.tama
            delegate._stats_view.setNeedsDisplay_(True)


# ── Stats View ────────────────────────────────────────────────────────────

class StatsView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(StatsView, self).initWithFrame_(frame)
        if self is None: return None
        self.buddy = None
        self.tama = None
        return self

    def isFlipped(self): return True

    def drawRect_(self, rect):
        try: draw_stats(self, rect)
        except: traceback.print_exc(file=sys.stderr)


# ── App Delegate ──────────────────────────────────────────────────────────

class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self._buddy = load_buddy()
        self._tama = load_tama()
        self._stats_visible = False

        screens = NSScreen.screens()
        scr = screens[0].visibleFrame() if screens else NSScreen.mainScreen().visibleFrame()
        cx = scr.origin.x + (scr.size.width - PET_W) / 2
        cy = scr.origin.y + (scr.size.height - PET_H) / 2

        self._pet_win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(cx, cy, PET_W, PET_H), NSBorderlessWindowMask, NSBackingStoreBuffered, False)
        self._pet_win.setLevel_(25)
        self._pet_win.setOpaque_(False)
        self._pet_win.setBackgroundColor_(NSColor.clearColor())
        self._pet_win.setHasShadow_(True)

        self._pet_view = PetView.alloc().initWithFrame_(NSMakeRect(0, 0, PET_W, PET_H))
        self._pet_view.buddy = self._buddy
        self._pet_view.tama = self._tama
        self._pet_win.setContentView_(self._pet_view)
        self._pet_win.orderFrontRegardless()

        # Stats window (hidden)
        self._stats_win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(cx - STATS_W - 10, cy, STATS_W, STATS_H), NSBorderlessWindowMask, NSBackingStoreBuffered, False)
        self._stats_win.setLevel_(25)
        self._stats_win.setOpaque_(False)
        self._stats_win.setBackgroundColor_(NSColor.clearColor())
        self._stats_win.setHasShadow_(True)
        self._stats_view = StatsView.alloc().initWithFrame_(NSMakeRect(0, 0, STATS_W, STATS_H))
        self._stats_view.buddy = self._buddy
        self._stats_view.tama = self._tama
        self._stats_win.setContentView_(self._stats_view)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1/30, self._pet_view, "tick:", None, True)

        self.buildMenu()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        print(f"[Buddy] {self._buddy['name']} the {self._buddy['species']}", file=sys.stderr, flush=True)

    def repositionStats(self):
        """Move stats window to track the pet window."""
        f = self._pet_win.frame()
        self._stats_win.setFrameOrigin_(NSMakePoint(
            f.origin.x - STATS_W - 10,
            f.origin.y - (STATS_H - PET_H) / 2))

    def buildMenu(self):
        b = self._buddy
        menu = NSMenu.alloc().init()

        h = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"✧ {b['name']} the {b['species'].title()} ✧", None, "")
        h.setEnabled_(False); menu.addItem_(h)
        menu.addItem_(NSMenuItem.separatorItem())

        # Interactions
        for action_name, emoji in [("Pet ♥", "doPet:"), ("Feed 🍕", "doFeed:"), ("Stroke ✋", "doStroke:"), ("Play ⚡", "doPlay:"), ("Rest 💤", "doRest:")]:
            i = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(action_name, emoji, "")
            i.setTarget_(self); menu.addItem_(i)
        menu.addItem_(NSMenuItem.separatorItem())

        si = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show Stats", "toggleStats", "")
        si.setTarget_(self); menu.addItem_(si)
        menu.addItem_(NSMenuItem.separatorItem())

        # Species submenu
        sp_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Reroll Species", None, "")
        sp_menu = NSMenu.alloc().init()
        for sp in SPECIES:
            label = f"{'● ' if sp == b['species'] else '  '}{sp}"
            i = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "rerollSpecies:", "")
            i.setTarget_(self); i.setRepresentedObject_(sp); sp_menu.addItem_(i)
        sp_item.setSubmenu_(sp_menu); menu.addItem_(sp_item)

        # Rarity submenu
        ra_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Reroll Rarity", None, "")
        ra_menu = NSMenu.alloc().init()
        for ra in RARITIES:
            label = f"{'● ' if ra == b['rarity'] else '  '}{ra.title()}"
            i = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "rerollRarity:", "")
            i.setTarget_(self); i.setRepresentedObject_(ra); ra_menu.addItem_(i)
        ra_item.setSubmenu_(ra_menu); menu.addItem_(ra_item)

        # Eyes submenu
        ey_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Reroll Eyes", None, "")
        ey_menu = NSMenu.alloc().init()
        for ey in EYES:
            label = f"{'● ' if ey == b['eye'] else '  '}{ey}"
            i = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "rerollEye:", "")
            i.setTarget_(self); i.setRepresentedObject_(ey); ey_menu.addItem_(i)
        ey_item.setSubmenu_(ey_menu); menu.addItem_(ey_item)

        # Hat submenu
        ht_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Reroll Hat", None, "")
        ht_menu = NSMenu.alloc().init()
        for ht in HATS:
            label = f"{'● ' if ht == b['hat'] else '  '}{ht}"
            i = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "rerollHat:", "")
            i.setTarget_(self); i.setRepresentedObject_(ht); ht_menu.addItem_(i)
        ht_item.setSubmenu_(ht_menu); menu.addItem_(ht_item)

        sh = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Shiny ✦ ({'ON' if b['shiny'] else 'OFF'})", "toggleShiny:", "")
        sh.setTarget_(self); menu.addItem_(sh)

        menu.addItem_(NSMenuItem.separatorItem())
        ri = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("🎲 Random Reroll!", "randomReroll:", "")
        ri.setTarget_(self); menu.addItem_(ri)
        menu.addItem_(NSMenuItem.separatorItem())
        qi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit Gravy", "quitApp:", "q")
        qi.setTarget_(self); menu.addItem_(qi)

        self._pet_win.setMenu_(menu)

    # ── Interaction handlers ──
    def doPet_(self, sender): do_interaction(self._pet_view, "pet")
    def doFeed_(self, sender): do_interaction(self._pet_view, "feed")
    def doStroke_(self, sender): do_interaction(self._pet_view, "stroke")
    def doPlay_(self, sender): do_interaction(self._pet_view, "play")
    def doRest_(self, sender): do_interaction(self._pet_view, "rest")

    def reloadBuddy(self):
        self._buddy = load_buddy()
        self._pet_view.buddy = self._buddy
        self._pet_view.setNeedsDisplay_(True)
        self._stats_view.buddy = self._buddy
        self._stats_view.setNeedsDisplay_(True)
        self.buildMenu()

    def toggleStats(self):
        if self._stats_visible:
            self._stats_win.orderOut_(None); self._stats_visible = False
        else:
            self.repositionStats()
            self._stats_win.orderFrontRegardless(); self._stats_visible = True

    def rerollSpecies_(self, sender):
        perform_reroll(self,{"species": sender.representedObject()})
    def rerollRarity_(self, sender):
        t = {"rarity": sender.representedObject()}
        if t["rarity"] == "common": t["hat"] = "none"
        perform_reroll(self,t)
    def rerollEye_(self, sender):
        perform_reroll(self,{"eye": sender.representedObject()})
    def rerollHat_(self, sender):
        perform_reroll(self,{"hat": sender.representedObject()})
    def toggleShiny_(self, sender):
        perform_reroll(self,{"shiny": not self._buddy["shiny"]})

    def randomReroll_(self, sender):
        rarity = random.choices(RARITIES, weights=[RARITY_WEIGHTS[r] for r in RARITIES])[0]
        perform_reroll(self,{
            "species": random.choice(SPECIES), "rarity": rarity,
            "eye": random.choice(EYES),
            "hat": "none" if rarity == "common" else random.choice(HATS),
            "shiny": random.random() < 0.05,
        })

    def quitApp_(self, sender):
        if self._pet_view.tama:
            save_tama(self._pet_view.tama)
        NSApplication.sharedApplication().terminate_(None)


def perform_reroll(delegate, target):
    """Build full target from current buddy + overrides, then patch binary in background."""
    full = {
        "species": delegate._buddy["species"], "rarity": delegate._buddy["rarity"],
        "eye": delegate._buddy["eye"], "hat": delegate._buddy["hat"],
        "shiny": delegate._buddy["shiny"],
    }
    full.update(target)

    save_override_all(full)
    delegate.reloadBuddy()
    do_interaction(delegate._pet_view, "play")

    import threading
    def patch():
        ok, msg = apply_reroll(full)
        print(f"[Buddy] Reroll: {msg}", file=sys.stderr, flush=True)
    threading.Thread(target=patch, daemon=True).start()


# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    print("\033[1;35m🐾 Buddy is alive!\033[0m Drag • Click=pet • Dbl-click=stats • Right-click=menu")
    try:
        from PyObjCTools import AppHelper
        AppHelper.runEventLoop()
    except ImportError:
        app.run()
