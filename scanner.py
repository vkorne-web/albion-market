from datetime import datetime, timezone
from api import fetch_history, fetch_prices, index_rows, age_seconds
from items import (
    BLACK_MARKET,
    CITIES,
    RAW_TO_REFINED,
    RECIPE,
    REFINE_BONUS_CITY,
    RETURN_RATE_BONUS_CITY,
    RETURN_RATE_ROYAL_CITY,
    SELL_ORDER_TAX,
    SLOT_TO_GROUP,
    build_pairs,
    build_resource_items,
)
import names

MAX_AGE_SECONDS = 60 * 60 * 6  # 6 hours; player-sourced data is often stale


def _cheapest_sell(idx, item_id, cities, now, max_age):
    """Cheapest instant-buy price across cities (lowest sell_price_min)."""
    best = None
    for city in cities:
        row = idx.get((item_id, city))
        if not row:
            continue
        price = row.get("sell_price_min") or 0
        age = age_seconds(row, "sell_price_min_date", now)
        if price > 0 and age is not None and age <= max_age:
            if best is None or price < best["price"]:
                best = {"city": city, "price": price, "age": age}
    return best


def _highest_buy(idx, item_id, cities, now, max_age):
    """Highest instant-sell price across cities (highest buy_price_max)."""
    best = None
    for city in cities:
        row = idx.get((item_id, city))
        if not row:
            continue
        price = row.get("buy_price_max") or 0
        age = age_seconds(row, "buy_price_max_date", now)
        if price > 0 and age is not None and age <= max_age:
            if best is None or price > best["price"]:
                best = {"city": city, "price": price, "age": age}
    return best


# A single troll/misclick listing in a thin market can sit far above the real
# price (e.g. T5 Rock at 67,997 when it's really ~90). For a fungible commodity,
# any listing more than this multiple of the cheapest city's listing is garbage,
# not a genuine sell opportunity — reject it before taking the highest.
LISTING_OUTLIER_MULT = 5


def _highest_listing(idx, item_id, cities, now, max_age, exclude_city=None):
    """Highest *sane* sell-order listing across cities (highest sell_price_min).

    This is the price you'd undercut when posting your own order — not
    buy_price_max, which is the instant-sell price. Outlier listings far above
    the cheapest city are dropped so one bogus price can't fake a profit.
    """
    valid = []
    for city in cities:
        if city == exclude_city:
            continue
        row = idx.get((item_id, city))
        if not row:
            continue
        price = row.get("sell_price_min") or 0
        age = age_seconds(row, "sell_price_min_date", now)
        if price > 0 and age is not None and age <= max_age:
            valid.append({"city": city, "price": price, "age": age})
    if not valid:
        return None
    floor = min(v["price"] for v in valid)
    sane = [v for v in valid if v["price"] <= LISTING_OUTLIER_MULT * floor]
    return max(sane, key=lambda v: v["price"])


async def scan_resource_haul(
    cities: list[str] | None = None,
    min_margin: int = 0,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    """Buy a resource at its cheapest city, list a sell order in the priciest.

    Margin = destination sell_price_min * (1 - SELL_ORDER_TAX) - source
    sell_price_min. All tier/enchant/kind/material filtering happens client-side
    in the UI — this scan always returns every haul so changing those filters
    never triggers a network rescan.
    """
    cities = cities or CITIES
    items = build_resource_items()
    rows = await fetch_prices([it["id"] for it in items], cities)
    idx = index_rows(rows)  # resources are always quality 1
    now = datetime.now(timezone.utc)

    results = []
    for it in items:
        src = _cheapest_sell(idx, it["id"], cities, now, max_age_seconds)
        if not src:
            continue
        # Destination must differ from source, else there's no haul.
        dst = _highest_listing(
            idx, it["id"], cities, now, max_age_seconds, exclude_city=src["city"]
        )
        if not dst:
            continue

        net_sell = dst["price"] * (1 - SELL_ORDER_TAX)
        margin = int(round(net_sell - src["price"]))
        if margin < min_margin:
            continue
        roi = margin / src["price"] if src["price"] else 0.0

        results.append(
            {
                "id": it["id"],
                "label": it["label"],
                "tier": it["tier"],
                "enchant": it["enchant"],
                "family": it["family"],
                "kind": it["kind"],
                "buy_city": src["city"],
                "buy_price": src["price"],
                "buy_age": src["age"],
                "sell_city": dst["city"],
                "sell_price": dst["price"],
                "sell_age": dst["age"],
                "net_sell": int(round(net_sell)),
                "margin": margin,
                "roi": roi,
                "daily_volume": None,  # filled below for the top hauls
            }
        )

    results.sort(key=lambda r: r["margin"], reverse=True)

    # Enrich only the top hauls by margin with daily sell volume AT THE
    # DESTINATION city (where your listed order must fill); lower rows keep
    # daily_volume=None (shown as "—") so rescans stay fast.
    top = results[:VOLUME_TOP_N]
    if top:
        ids_to_history = list({r["id"] for r in top})
        dest_cities = list({r["sell_city"] for r in top})
        try:
            history_rows = await fetch_history(ids_to_history, dest_cities, time_scale=24)
            vol = _avg_daily_volume_by_loc(history_rows)
            for r in top:
                r["daily_volume"] = vol.get((r["id"], r["sell_city"]), 0)
        except Exception:
            pass

    return results


async def scan_gather(
    cities: list[str] | None = None,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    """For manually-gathered raws: per gathered unit, is it better to sell the
    raw or to refine it and sell the refined material?

    Raws are free (you gathered them), so the only cost on the refine path is
    BUYING the prev-tier refined input at market (instant-buy). Both sell paths
    use list-order revenue (sell_price_min minus SELL_ORDER_TAX). The return-rate
    bonus reduces effective material consumption. Ranked by net silver per
    gathered raw unit so tiers/materials are comparable.
    """
    cities = cities or CITIES
    pairs = build_pairs()
    prev_ids = [pid for p in pairs if (pid := _prev_refined_id(p))]
    all_ids = [p["raw_id"] for p in pairs] + [p["refined_id"] for p in pairs] + prev_ids
    rows = await fetch_prices(all_ids, cities)
    idx = index_rows(rows)
    now = datetime.now(timezone.utc)

    results = []
    for p in pairs:
        raws_per, prev_per = RECIPE[p["tier"]]

        # Path 1 — sell the raw directly (list order at its priciest city).
        raw_sell = _highest_listing(idx, p["raw_id"], cities, now, max_age_seconds)
        raw_value = raw_sell["price"] * (1 - SELL_ORDER_TAX) if raw_sell else None

        # Path 2 — refine, then sell the refined material.
        refined_sell = _highest_listing(idx, p["refined_id"], cities, now, max_age_seconds)
        prev_id = _prev_refined_id(p)
        prev_buy = (
            _cheapest_sell(idx, prev_id, cities, now, max_age_seconds)
            if prev_id is not None
            else None
        )

        # Refine where the material gets its return-rate bonus, if that city is
        # in range; otherwise any royal city at the lower rate.
        bonus_city = REFINE_BONUS_CITY.get(p["material"])
        if bonus_city in cities:
            refine_city, return_rate = bonus_city, RETURN_RATE_BONUS_CITY
        else:
            refine_city, return_rate = None, RETURN_RATE_ROYAL_CITY

        refine_value = None
        if refined_sell is not None and (prev_id is None or prev_buy is not None):
            eff_raws = raws_per * (1 - return_rate)
            eff_prev = prev_per * (1 - return_rate)
            refined_net = refined_sell["price"] * (1 - SELL_ORDER_TAX)
            prev_cost = eff_prev * (prev_buy["price"] if prev_buy else 0)
            net_per_refined = refined_net - prev_cost
            if eff_raws > 0:
                refine_value = net_per_refined / eff_raws

        if raw_value is None and refine_value is None:
            continue  # nothing sellable — leave it off the "best options" list

        candidates = []
        if raw_value is not None:
            candidates.append(("raw", raw_value))
        if refine_value is not None:
            candidates.append(("refine", refine_value))
        best, best_value = max(candidates, key=lambda c: c[1])

        # What you'd actually list, and where — used for the liquidity check.
        if best == "raw":
            sell_id, sell_loc = p["raw_id"], raw_sell["city"]
        else:
            sell_id, sell_loc = p["refined_id"], refined_sell["city"]

        results.append(
            {
                "label": f"T{p['tier']}.{p['enchant']} {p['material'].title()}",
                "tier": p["tier"],
                "enchant": p["enchant"],
                "material": p["material"],
                "raws_per": raws_per,
                "prev_per": prev_per,
                "raw_value": raw_value,
                "raw_sell_city": raw_sell["city"] if raw_sell else None,
                "raw_sell_price": raw_sell["price"] if raw_sell else None,
                "raw_age": raw_sell["age"] if raw_sell else None,
                "refine_value": refine_value,
                "refined_name": RAW_TO_REFINED[p["material"]].title(),
                "refine_sell_city": refined_sell["city"] if refined_sell else None,
                "refine_sell_price": refined_sell["price"] if refined_sell else None,
                "refine_age": refined_sell["age"] if refined_sell else None,
                "prev_buy_city": prev_buy["city"] if prev_buy else None,
                "prev_buy_price": prev_buy["price"] if prev_buy else None,
                "refine_city": refine_city,
                "return_rate": return_rate,
                "best": best,
                "best_action": "Sell raw" if best == "raw" else "Refine → sell",
                "best_value": int(round(best_value)),
                "sell_id": sell_id,
                "sell_loc": sell_loc,
                "daily_volume": None,  # filled below for the top options
            }
        )

    results.sort(key=lambda r: r["best_value"], reverse=True)

    # Enrich the top options with daily sell volume of the item you'd LIST, in
    # the city you'd list it. Lower rows keep daily_volume=None ("—").
    top = results[:VOLUME_TOP_N]
    if top:
        ids = list({r["sell_id"] for r in top})
        locs = list({r["sell_loc"] for r in top})
        try:
            history_rows = await fetch_history(ids, locs, time_scale=24)
            vol = _avg_daily_volume_by_loc(history_rows)
            for r in top:
                r["daily_volume"] = vol.get((r["sell_id"], r["sell_loc"]), 0)
        except Exception:
            pass

    return results


def _prev_refined_id(pair):
    """ID of the previous-tier refined item (same enchant) used in the recipe."""
    tier = pair["tier"]
    if tier <= 2:
        return None
    refined_base = RAW_TO_REFINED[pair["material"]]
    enchant = pair["enchant"]
    if enchant == 0:
        return f"T{tier - 1}_{refined_base}"
    return f"T{tier - 1}_{refined_base}_LEVEL{enchant}@{enchant}"


async def scan(
    cities: list[str] | None = None,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    cities = cities or CITIES
    pairs = build_pairs()
    all_ids = [p["raw_id"] for p in pairs] + [p["refined_id"] for p in pairs]
    rows = await fetch_prices(all_ids, cities)
    idx = index_rows(rows)
    now = datetime.now(timezone.utc)

    results = []
    for pair in pairs:
        raws_per, prev_per = RECIPE[pair["tier"]]
        best_buy = _cheapest_sell(idx, pair["raw_id"], cities, now, max_age_seconds)
        best_sell = _highest_buy(idx, pair["refined_id"], cities, now, max_age_seconds)

        # Previous-tier refined input (only for T3+)
        prev_id = _prev_refined_id(pair)
        prev_buy = (
            _cheapest_sell(idx, prev_id, cities, now, max_age_seconds)
            if prev_id is not None
            else None
        )

        # Which required live prices are missing? (A row needs all three to be
        # priced honestly; we still emit incomplete rows so the user can see them.)
        missing = []
        if not best_buy:
            missing.append("raw price")
        if not best_sell:
            missing.append("refined sell order")
        if prev_id is not None and not prev_buy:
            prev_name = RAW_TO_REFINED[pair["material"]].title()
            missing.append(f"T{pair['tier'] - 1} {prev_name} price")

        # Refine city + return rate. The bonus city is fixed per material; the
        # fallback "no bonus" city follows where we'd buy the raws.
        bonus_city = REFINE_BONUS_CITY.get(pair["material"])
        if bonus_city in cities:
            refine_city = bonus_city
            refine_note = "bonus"
            return_rate = RETURN_RATE_BONUS_CITY
        else:
            refine_city = best_buy["city"] if best_buy else None
            refine_note = "no bonus"
            return_rate = RETURN_RATE_ROYAL_CITY

        row = {
            "label": pair["label"],
            "tier": pair["tier"],
            "enchant": pair["enchant"],
            "material": pair["material"],
            "buy_city": best_buy["city"] if best_buy else None,
            "buy_price": best_buy["price"] if best_buy else None,
            "buy_age": best_buy["age"] if best_buy else None,
            "raws_per": raws_per,
            "prev_per": prev_per,
            "prev_buy_city": prev_buy["city"] if prev_buy else None,
            "prev_buy_price": prev_buy["price"] if prev_buy else 0,
            "prev_buy_age": prev_buy["age"] if prev_buy else None,
            "refine_city": refine_city,
            "refine_note": refine_note,
            "return_rate": return_rate,
            "gross_input": None,
            "effective_input": None,
            "sell_city": best_sell["city"] if best_sell else None,
            "sell_price": best_sell["price"] if best_sell else None,
            "sell_age": best_sell["age"] if best_sell else None,
            "margin": None,
            "missing": missing,
        }

        if not missing:
            # Effective input cost per 1 refined unit, accounting for return rate
            raws_cost = raws_per * best_buy["price"]
            prev_cost = prev_per * (prev_buy["price"] if prev_buy else 0)
            gross_input = raws_cost + prev_cost
            effective_input = gross_input * (1 - return_rate)
            row["gross_input"] = gross_input
            row["effective_input"] = effective_input
            row["margin"] = int(round(best_sell["price"] - effective_input))

        results.append(row)

    # Priced rows first (descending margin); data-incomplete rows sink to the end.
    results.sort(
        key=lambda r: (r["margin"] is not None, r["margin"] or 0),
        reverse=True,
    )
    return results


def _avg_daily_volume_by_loc(history_rows: list[dict], days: int = 7) -> dict[tuple[str, str], int]:
    """Return {(item_id, location): avg item_count per day over the last `days`}.

    Keyed by city (not Black Market) for the resource-haul destination check.
    """
    out: dict[tuple[str, str], int] = {}
    for row in history_rows:
        data = row.get("data") or []
        recent = data[-days:] if len(data) >= days else data
        if not recent:
            continue
        total = sum(d.get("item_count", 0) for d in recent)
        avg = total // max(1, len(recent))
        out[(row.get("item_id"), row.get("location"))] = avg
    return out


def _avg_daily_volume(history_rows: list[dict], days: int = 7) -> dict[tuple[str, int], int]:
    """Return {(item_id, quality): avg item_count per day over the last `days`}."""
    out: dict[tuple[str, int], int] = {}
    for row in history_rows:
        if row.get("location") != BLACK_MARKET:
            continue
        data = row.get("data") or []
        recent = data[-days:] if len(data) >= days else data
        if not recent:
            continue
        total = sum(d.get("item_count", 0) for d in recent)
        avg = total // max(1, len(recent))
        key = (row.get("item_id"), row.get("quality", 1))
        out[key] = avg
    return out


# Black Market flip: buy gear cheap in a royal city → sell into BM buy orders in Caerleon.
# The BM sales tax is 4% (premium) on the silver received from NPC orders.
BM_TAX = 0.04

# Fetching daily volume for every profitable flip makes rescans slow. Only the
# highest-margin flips are worth a liquidity check, so cap the history lookup.
VOLUME_TOP_N = 40


def _cheapest_sell_for_quality(rows_by_city: dict, cities, now, max_age):
    best = None
    for city in cities:
        row = rows_by_city.get(city)
        if not row:
            continue
        price = row.get("sell_price_min") or 0
        age = age_seconds(row, "sell_price_min_date", now)
        if price > 0 and age is not None and age <= max_age:
            if best is None or price < best["price"]:
                best = {"city": city, "price": price, "age": age}
    return best


async def scan_black_market(
    source_cities: list[str] | None = None,
    tiers: list[int] | None = None,
    enchants: list[int] | None = None,
    min_margin: int = 0,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    """Scan gear for buy-cheap-sell-to-BM flips. Considers all 5 qualities."""
    source_cities = source_cities or [c for c in CITIES if c != "Caerleon"]
    tiers_set = set(tiers) if tiers else {4, 5, 6, 7, 8}
    enchants_set = set(enchants) if enchants else {0, 1, 2, 3, 4}

    # Build candidate ID list using parsed gear universe from items.txt.
    candidates = []
    for iid in names.gear_ids():
        meta = names.parse_id(iid)
        if not meta:
            continue
        if meta["tier"] not in tiers_set or meta["enchant"] not in enchants_set:
            continue
        candidates.append((iid, meta))

    item_ids = [iid for iid, _ in candidates]
    locations = list({*source_cities, BLACK_MARKET})
    rows = await fetch_prices(item_ids, locations)
    now = datetime.now(timezone.utc)

    # Bucket rows by (item_id, quality) -> {city: row}
    buckets: dict[tuple[str, int], dict[str, dict]] = {}
    for row in rows:
        q = row.get("quality", 1)
        key = (row.get("item_id"), q)
        buckets.setdefault(key, {})[row.get("city")] = row

    results = []
    for iid, meta in candidates:
        for q in (1, 2, 3, 4, 5):
            bucket = buckets.get((iid, q))
            if not bucket:
                continue
            bm_row = bucket.get(BLACK_MARKET)
            if not bm_row:
                continue
            bm_price = bm_row.get("buy_price_max") or 0
            bm_age = age_seconds(bm_row, "buy_price_max_date", now)
            if bm_price <= 0 or bm_age is None or bm_age > max_age_seconds:
                continue

            best_buy = _cheapest_sell_for_quality(bucket, source_cities, now, max_age_seconds)
            if not best_buy:
                continue

            net_sell = bm_price * (1 - BM_TAX)
            margin = int(round(net_sell - best_buy["price"]))
            if margin < min_margin:
                continue

            slot_cat = meta["slot_category"]
            results.append(
                {
                    "id": iid,
                    "name": names.get_name(iid),
                    "tier": meta["tier"],
                    "enchant": meta["enchant"],
                    "quality": q,
                    "slot_category": slot_cat,
                    "group": SLOT_TO_GROUP.get(slot_cat, "Other"),
                    "buy_city": best_buy["city"],
                    "buy_price": best_buy["price"],
                    "buy_age": best_buy["age"],
                    "bm_price": bm_price,
                    "bm_age": bm_age,
                    "net_sell": int(round(net_sell)),
                    "margin": margin,
                    "daily_volume": None,  # filled below
                }
            )

    results.sort(key=lambda r: r["margin"], reverse=True)

    # Enrich only the top flips by margin with daily BM sales volume; lower rows
    # keep daily_volume=None (shown as "—") so rescans stay fast. The history
    # endpoint returns all qualities per item, so we fetch by unique item_id.
    top = results[:VOLUME_TOP_N]
    if top:
        ids_to_history = list({r["id"] for r in top})
        try:
            history_rows = await fetch_history(ids_to_history, [BLACK_MARKET], time_scale=24)
            vol = _avg_daily_volume(history_rows)
            for r in top:
                r["daily_volume"] = vol.get((r["id"], r["quality"]), 0)
        except Exception:
            pass

    return results
