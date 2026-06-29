RAW_TO_REFINED = {
    "ORE": "METALBAR",
    "WOOD": "PLANKS",
    "FIBER": "CLOTH",
    "HIDE": "LEATHER",
    "ROCK": "STONEBLOCK",
}

TIERS = [2, 3, 4, 5, 6, 7, 8]
ENCHANTS = [0, 1, 2, 3, 4]

CITIES = [
    "Caerleon",
    "Bridgewatch",
    "Lymhurst",
    "Fort Sterling",
    "Martlock",
    "Thetford",
]

# Each royal city gives a refining return-rate bonus on one raw material.
REFINE_BONUS_CITY = {
    "ORE": "Thetford",
    "WOOD": "Fort Sterling",
    "FIBER": "Lymhurst",
    "HIDE": "Martlock",
    "ROCK": "Bridgewatch",
}

# Per 1 refined unit: (same-tier raws, previous-tier refined units).
RECIPE = {
    2: (1, 0),
    3: (2, 1),
    4: (2, 1),
    5: (3, 1),
    6: (4, 1),
    7: (5, 1),
    8: (5, 1),
}

# Return rates (no focus). Effective materials needed = base * (1 - return_rate).
RETURN_RATE_BONUS_CITY = 0.367  # royal city + specialty material
RETURN_RATE_ROYAL_CITY = 0.247  # royal city, no specialty

BLACK_MARKET = "Black Market"

BM_TIERS = [4, 5, 6, 7, 8]
BM_ENCHANTS = [0, 1, 2, 3, 4]
QUALITIES = {1: "Normal", 2: "Good", 3: "Outstanding", 4: "Excellent", 5: "Masterpiece"}

# Slot category prefix → broad group used in the UI filter.
SLOT_GROUPS = {
    "Armor": ("HEAD", "ARMOR", "SHOES"),
    "Weapons": ("MAIN", "2H", "OFF"),
    "Accessories": ("BAG", "CAPE"),
}
SLOT_TO_GROUP = {slot: group for group, slots in SLOT_GROUPS.items() for slot in slots}


# ---------- Resource haul (cross-city arbitrage) ----------

# Buy a gathering product cheap in one city, haul it, and list your own sell
# order in another. Covers the 5 raw gatherables AND their refined materials.
REFINED_TO_RAW = {refined: raw for raw, refined in RAW_TO_REFINED.items()}

RESOURCE_DISPLAY = {
    "ORE": "Ore",
    "WOOD": "Wood",
    "FIBER": "Fiber",
    "HIDE": "Hide",
    "ROCK": "Rock",
    "METALBAR": "Metal Bar",
    "PLANKS": "Plank",
    "CLOTH": "Cloth",
    "LEATHER": "Leather",
    "STONEBLOCK": "Stone Block",
}

# Raw/refined resources only enchant up to .3 (unlike artifact gear at .4).
RESOURCE_ENCHANTS = [0, 1, 2, 3]

# Listing your own sell order: 4% sales tax + 2.5% setup fee (premium account).
SELL_ORDER_TAX = 0.065


# ---------- Crafting ----------

# Resource Return Rate (RRR) stacks bonus POINTS, then RRR = 1 - 100/(100+sum).
# Verified June 2026 against community guides:
#   base only (18)        -> 15.2%
#   +specialty (33)       -> 24.8%
#   +focus (77)           -> 43.5%
#   specialty+focus (92)  -> 47.9%
CRAFT_BASE_BONUS = 18  # any royal-city crafting station, always present
CRAFT_SPEC_BONUS = 15  # crafting in the item's specialty (bonus) city
CRAFT_FOCUS_BONUS = 59  # using focus
CRAFT_BONUS_DAY = {"None": 0, "Silver (+10)": 10, "Gold (+20)": 20}


def craft_return_rate(spec: bool = False, focus: bool = False, bonus_day: int = 0) -> float:
    """Fraction of (returnable) materials refunded when crafting."""
    pts = CRAFT_BASE_BONUS
    if spec:
        pts += CRAFT_SPEC_BONUS
    if focus:
        pts += CRAFT_FOCUS_BONUS
    pts += bonus_day
    return 1 - 100 / (100 + pts)


def build_resource_items():
    """All haulable raw + refined resources across tiers/enchants."""
    families = [(raw, raw, "Raw") for raw in RAW_TO_REFINED]
    families += [(refined, raw, "Refined") for raw, refined in RAW_TO_REFINED.items()]
    items = []
    for base, family, kind in families:
        for tier in TIERS:
            for enchant in RESOURCE_ENCHANTS:
                if enchant > 0 and tier < 4:
                    continue
                items.append(
                    {
                        "id": _item_id(tier, base, enchant),
                        "tier": tier,
                        "enchant": enchant,
                        "base": base,
                        "family": family,  # raw-material family for the Material filter
                        "kind": kind,  # "Raw" or "Refined"
                        "label": f"T{tier}.{enchant} {RESOURCE_DISPLAY[base]}",
                    }
                )
    return items


def _item_id(tier: int, base: str, enchant: int) -> str:
    if enchant == 0:
        return f"T{tier}_{base}"
    return f"T{tier}_{base}_LEVEL{enchant}@{enchant}"


def build_pairs():
    pairs = []
    for tier in TIERS:
        for enchant in ENCHANTS:
            # Enchants only exist for T4+
            if enchant > 0 and tier < 4:
                continue
            for raw, refined in RAW_TO_REFINED.items():
                pairs.append(
                    {
                        "tier": tier,
                        "enchant": enchant,
                        "material": raw,
                        "raw_id": _item_id(tier, raw, enchant),
                        "refined_id": _item_id(tier, refined, enchant),
                        "label": f"T{tier}.{enchant} {raw.title()} → {refined.title()}",
                    }
                )
    return pairs
