"""Dev tool: extract gear crafting recipes from ao-bin-dumps items.json.

Run once (with items_full.json present) to regenerate the bundled recipes.json:

    curl -sL https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json -o items_full.json
    python extract_recipes.py

Output `recipes.json` maps a price-API gear id -> recipe, e.g.:

    "T4_MAIN_SWORD":    {"materials": [{"id": "T4_METALBAR", "count": 16, "ret": true}, ...]}
    "T4_MAIN_SWORD@1":  {"materials": [{"id": "T4_METALBAR_LEVEL1@1", "count": 16, "ret": true}, ...]}

Material ids are already in AODP price-API form (enchanted mats get the @level
suffix). `ret` is False for artifacts/tokens (@maxreturnamount == "0"), which the
crafting return rate does NOT refund.
"""
import json

# Same gear universe as names.py so the search box and recipes line up.
GEAR_PREFIXES = ("HEAD_", "ARMOR_", "SHOES_", "MAIN_", "2H_", "OFF_", "BAG", "CAPE")
GEAR_EXCLUDE = ("TOOL", "SEED", "FARM", "JOURNAL")


def _is_gear(uniquename: str) -> bool:
    # Strip the leading "T{n}_"
    parts = uniquename.split("_", 1)
    if len(parts) != 2 or not parts[0].startswith("T"):
        return False
    rest = parts[1]
    if not any(rest.startswith(p) for p in GEAR_PREFIXES):
        return False
    if any(x in rest for x in GEAR_EXCLUDE):
        return False
    return True


def _price_id(res: dict) -> str:
    """Dump craftresource entry -> AODP price-API item id.

    Enchanted mats are listed as e.g. T4_METALBAR_LEVEL1 with @enchantmentlevel=1;
    the price API wants T4_METALBAR_LEVEL1@1.
    """
    uid = res["@uniquename"]
    lvl = res.get("@enchantmentlevel")
    if lvl and lvl != "0" and "@" not in uid:
        return f"{uid}@{lvl}"
    return uid


def _materials(craftreq: dict) -> list[dict]:
    res = craftreq.get("craftresource")
    if res is None:
        return []
    if isinstance(res, dict):  # single material comes through as a dict
        res = [res]
    mats = []
    for r in res:
        mats.append(
            {
                "id": _price_id(r),
                "count": int(r["@count"]),
                # @maxreturnamount == "0" -> artifact/token, never refunded
                "ret": r.get("@maxreturnamount") != "0",
            }
        )
    return mats


def main():
    data = json.load(open("items_full.json", encoding="utf-8"))
    root = data["items"]
    recipes: dict[str, dict] = {}

    for cat in ("equipmentitem", "weapon"):
        for it in root[cat]:
            uid = it.get("@uniquename", "")
            if not _is_gear(uid):
                continue
            base = it.get("craftingrequirements")
            if not base:
                continue
            # craftingrequirements can itself be a list on a few items; take first.
            if isinstance(base, list):
                base = base[0]
            mats = _materials(base)
            if mats:
                recipes[uid] = {"materials": mats}

            ench = it.get("enchantments")
            if not ench:
                continue
            levels = ench.get("enchantment", [])
            if isinstance(levels, dict):
                levels = [levels]
            for lv in levels:
                level = lv.get("@enchantmentlevel")
                cr = lv.get("craftingrequirements")
                if not cr or not level:
                    continue
                if isinstance(cr, list):
                    cr = cr[0]
                emats = _materials(cr)
                if emats:
                    recipes[f"{uid}@{level}"] = {"materials": emats}

    json.dump(recipes, open("recipes.json", "w", encoding="utf-8"), separators=(",", ":"))
    print(f"wrote recipes.json: {len(recipes)} recipes")


if __name__ == "__main__":
    main()
