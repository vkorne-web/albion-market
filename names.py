"""Item ID → English name lookup from the ao-bin-dumps items.txt."""
import os
import re
import sys

_NAMES: dict[str, str] | None = None
_GEAR_IDS: list[str] | None = None

# Lines look like:  967: T4_ORE                                : Iron Ore
_LINE_RE = re.compile(r"^\s*\d+:\s+(\S+)\s*:\s*(.+?)\s*$")

# Gear categories that make sense to flip on the Black Market.
# Pattern: prefix after T{n}_
_GEAR_PREFIXES = ("HEAD_", "ARMOR_", "SHOES_", "MAIN_", "2H_", "OFF_", "BAG", "CAPE")
_GEAR_EXCLUDE = ("TOOL", "SEED", "FARM", "JOURNAL")

_ID_RE = re.compile(r"^T([4-8])_([A-Z0-9_]+?)(?:@([0-4]))?$")


def _items_path() -> str:
    """Find items_raw.txt — works in dev and after PyInstaller --onefile bundle."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "items_raw.txt"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "items_raw.txt"))
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("items_raw.txt not found; bundle it with the app")


def _load():
    global _NAMES, _GEAR_IDS
    if _NAMES is not None:
        return
    names: dict[str, str] = {}
    with open(_items_path(), "r", encoding="utf-8") as f:
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


def get_name(item_id: str) -> str:
    _load()
    return _NAMES.get(item_id, item_id)


def gear_ids() -> list[str]:
    _load()
    return list(_GEAR_IDS)


def search_ids(query: str, limit: int = 60) -> list[str]:
    """Gear ids whose English name (or id) contains `query`, case-insensitive.

    Sorted by name, then tier, then enchant so a search like 'rotcaller' lists
    each tier/enchant variant in a predictable order for the bundle picker.
    """
    _load()
    q = query.strip().lower()
    if not q:
        return []
    matches = []
    for iid in _GEAR_IDS:
        name = _NAMES.get(iid, iid)
        if q in name.lower() or q in iid.lower():
            meta = parse_id(iid)
            matches.append((name, meta["tier"] if meta else 0,
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
