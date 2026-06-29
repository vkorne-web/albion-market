"""Item ID → name lookup from the ao-bin-dumps dumps (English + Spanish)."""
import json
import os
import re
import sys

import i18n

_NAMES: dict[str, str] | None = None
_NAMES_ES: dict[str, str] | None = None
_GEAR_IDS: list[str] | None = None

# Lines look like:  967: T4_ORE                                : Iron Ore
_LINE_RE = re.compile(r"^\s*\d+:\s+(\S+)\s*:\s*(.+?)\s*$")

# Gear categories that make sense to flip on the Black Market.
# Pattern: prefix after T{n}_
_GEAR_PREFIXES = ("HEAD_", "ARMOR_", "SHOES_", "MAIN_", "2H_", "OFF_", "BAG", "CAPE")
_GEAR_EXCLUDE = ("TOOL", "SEED", "FARM", "JOURNAL")

_ID_RE = re.compile(r"^T([4-8])_([A-Z0-9_]+?)(?:@([0-4]))?$")


def _bundled_path(filename: str) -> str:
    """Find a bundled data file — works in dev and after a PyInstaller bundle."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, filename))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"{filename} not found; bundle it with the app")


def _load():
    global _NAMES, _GEAR_IDS
    if _NAMES is not None:
        return
    names: dict[str, str] = {}
    with open(_bundled_path("items_raw.txt"), "r", encoding="utf-8") as f:
        for line in f:
            m = _LINE_RE.match(line)
            if m:
                names[m.group(1)] = m.group(2)
    _NAMES = names

    gear: list[str] = []
    for item_id in names:
        m = _ID_RE.match(item_id)
        if not m:
            continue
        rest = m.group(2)
        if not any(rest.startswith(p) for p in _GEAR_PREFIXES):
            continue
        if any(x in rest for x in _GEAR_EXCLUDE):
            continue
        gear.append(item_id)
    _GEAR_IDS = gear


def _load_es():
    """Lazily load the Spanish name map (bundled names_es.json)."""
    global _NAMES_ES
    if _NAMES_ES is not None:
        return
    try:
        with open(_bundled_path("names_es.json"), "r", encoding="utf-8") as f:
            _NAMES_ES = json.load(f)
    except FileNotFoundError:
        _NAMES_ES = {}


def get_name(item_id: str) -> str:
    """Localized item name for the current language, falling back to English."""
    _load()
    if i18n.get_lang() == "es":
        _load_es()
        es = _NAMES_ES.get(item_id)
        if es:
            return es
    return _NAMES.get(item_id, item_id)


def gear_ids() -> list[str]:
    _load()
    return list(_GEAR_IDS)


def search_ids(query: str, limit: int = 60) -> list[str]:
    """Gear ids whose name (or id) contains `query`, case-insensitive.

    Matches the localized display name, the English name, AND the id, so a
    Spanish user can search in Spanish while English/id searches still work.
    Sorted by the display name, then tier, then enchant so each tier/enchant
    variant lists in a predictable order for the picker.
    """
    _load()
    es_mode = i18n.get_lang() == "es"
    if es_mode:
        _load_es()
    q = query.strip().lower()
    if not q:
        return []
    matches = []
    for iid in _GEAR_IDS:
        english = _NAMES.get(iid, iid)
        display = _NAMES_ES.get(iid, english) if es_mode else english
        if q in display.lower() or q in english.lower() or q in iid.lower():
            meta = parse_id(iid)
            matches.append((display, meta["tier"] if meta else 0,
                            meta["enchant"] if meta else 0, iid))
    matches.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in matches[:limit]]


def parse_id(item_id: str) -> dict | None:
    """Return tier, enchant, slot category for an item id, or None."""
    m = _ID_RE.match(item_id)
    if not m:
        return None
    tier = int(m.group(1))
    rest = m.group(2)
    enchant = int(m.group(3)) if m.group(3) else 0
    # Slot category: first segment of rest
    slot = rest.split("_")[0]
    return {"tier": tier, "enchant": enchant, "slot_category": slot}
