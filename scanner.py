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
import recipes

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


# ---------- Manipulation / scam detector ----------
#
# Bundle/trade scam: someone offers a container of items where ONE is listed far
# above its real value, inflating the whole deal's apparent worth. The tell is a
# current listing price way above what the item actually trades at. The /history
# avg_price is the real traded price and is hard to fake — moving it means
# actually selling large volume at the inflated price — so it's a trustworthy
# baseline to compare a live listing against.

# Volume-weighted traded-price baseline window. Long enough to be stable, short
# enough to still track genuine price moves.
BASELINE_DAYS = 30

# Recent-volume window (same convention as the other tabs' Vol/day).
MANIP_VOL_DAYS = 7

# Server-side noise floor: only return listings at least this far above baseline.
# The user's real knob (default 3x) is a client-side filter on top of this, so
# adjusting it never triggers a rescan.
SPIKE_FLOOR = 1.5


def _baseline_by_quality(
    history_rows: list[dict], baseline_days: int = BASELINE_DAYS, vol_days: int = MANIP_VOL_DAYS
) -> dict[tuple[str, int], dict]:
    """{(item_id, quality): {"baseline": volume-weighted avg traded price,
    "vol_per_day": recent avg units/day}}.

    Pools history across all returned locations into one fair-value estimate per
    item+quality, weighting each day's avg_price by that day's item_count so
    heavily-traded days dominate. Days with no price or no volume are ignored.
    """
    agg: dict[tuple[str, int], dict] = {}
    for row in history_rows:
        key = (row.get("item_id"), row.get("quality", 1))
        data = row.get("data") or []
        if not data:
            continue
        recent = data[-baseline_days:]
        wsum = 0.0
        weight = 0
        for d in recent:
            p = d.get("avg_price") or 0
            c = d.get("item_count") or 0
            if p > 0 and c > 0:
                wsum += p * c
                weight += c
        if weight <= 0:
            continue
        vol_recent = data[-vol_days:]
        vtotal = sum(d.get("item_count", 0) for d in vol_recent)
        vol_per_day = vtotal // max(1, len(vol_recent))

        slot = agg.setdefault(key, {"wsum": 0.0, "weight": 0, "vol_per_day": 0})
        slot["wsum"] += wsum
        slot["weight"] += weight
        slot["vol_per_day"] += vol_per_day  # pooled across cities

    out: dict[tuple[str, int], dict] = {}
    for key, slot in agg.items():
        if slot["weight"] <= 0:
            continue
        out[key] = {
            "baseline": slot["wsum"] / slot["weight"],
            "vol_per_day": slot["vol_per_day"],
        }
    return out


async def scan_manipulation(
    cities: list[str] | None = None,
    tiers: list[int] | None = None,
    enchants: list[int] | None = None,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    """Find gear listings priced far above their real recent traded value.

    Returns every (item, quality, city) whose current sell_price_min is at least
    SPIKE_FLOOR x its history baseline. Spike threshold / min inflation / min
    volume are applied client-side so tuning them never rescans.
    """
    cities = cities or [c for c in CITIES if c != "Caerleon"]
    tiers_set = set(tiers) if tiers else {4, 5, 6, 7, 8}
    enchants_set = set(enchants) if enchants else {0, 1}

    candidates = []
    for iid in names.gear_ids():
        meta = names.parse_id(iid)
        if not meta:
            continue
        if meta["tier"] not in tiers_set or meta["enchant"] not in enchants_set:
            continue
        candidates.append((iid, meta))

    item_ids = [iid for iid, _ in candidates]
    price_rows = await fetch_prices(item_ids, cities)
    history_rows = await fetch_history(item_ids, cities, time_scale=24)
    now = datetime.now(timezone.utc)
    baselines = _baseline_by_quality(history_rows)

    # Quality-aware price index: (item_id, city, quality) -> row.
    idx: dict[tuple[str, str, int], dict] = {}
    for row in price_rows:
        idx[(row.get("item_id"), row.get("city"), row.get("quality", 1))] = row

    results = []
    for iid, meta in candidates:
        for q in (1, 2, 3, 4, 5):
            base = baselines.get((iid, q))
            if not base or base["baseline"] <= 0:
                continue
            baseline = base["baseline"]
            for city in cities:
                row = idx.get((iid, city, q))
                if not row:
                    continue
                current = row.get("sell_price_min") or 0
                age = age_seconds(row, "sell_price_min_date", now)
                if current <= 0 or age is None or age > max_age_seconds:
                    continue
                spike = current / baseline
                if spike < SPIKE_FLOOR:
                    continue
                results.append(
                    {
                        "id": iid,
                        "name": names.get_name(iid),
                        "tier": meta["tier"],
                        "enchant": meta["enchant"],
                        "quality": q,
                        "slot_category": meta["slot_category"],
                        "group": SLOT_TO_GROUP.get(meta["slot_category"], "Other"),
                        "city": city,
                        "current": current,
                        "baseline": int(round(baseline)),
                        "inflation": int(round(current - baseline)),
                        "spike": spike,
                        "vol_per_day": base["vol_per_day"],
                        "age": age,
                    }
                )

    results.sort(key=lambda r: r["spike"], reverse=True)
    return results


def _verdict(spike, vol_per_day) -> str:
    """Plain-language read on a listing, given its spike and liquidity."""
    if spike is None:
        return "No history — can't judge"
    thin = vol_per_day is not None and vol_per_day < 5
    if spike >= 5:
        return "🚩 Very suspicious" + (" (thin market)" if thin else "")
    if spike >= 3:
        return "⚠ Suspicious" + (" (thin market)" if thin else "")
    if spike >= 1.5:
        return "Slightly high"
    if spike < 0.7:
        return "Below market (cheap)"
    return "✓ Looks normal"


async def check_items(
    item_ids: list[str],
    cities: list[str] | None = None,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    """Verify SPECIFIC items (e.g. the contents of a bundle someone offers you).

    Unlike scan_manipulation this returns EVERY listing for the requested ids —
    including normal-priced ones — each with a plain-language verdict, so you can
    eyeball a whole trade item by item. No spike floor.
    """
    cities = cities or [c for c in CITIES if c != "Caerleon"]
    item_ids = list(dict.fromkeys(item_ids))
    if not item_ids:
        return []

    price_rows = await fetch_prices(item_ids, cities)
    history_rows = await fetch_history(item_ids, cities, time_scale=24)
    now = datetime.now(timezone.utc)
    baselines = _baseline_by_quality(history_rows)

    idx: dict[tuple[str, str, int], dict] = {}
    for row in price_rows:
        idx[(row.get("item_id"), row.get("city"), row.get("quality", 1))] = row

    results = []
    for iid in item_ids:
        meta = names.parse_id(iid) or {}
        for q in (1, 2, 3, 4, 5):
            for city in cities:
                row = idx.get((iid, city, q))
                if not row:
                    continue
                current = row.get("sell_price_min") or 0
                age = age_seconds(row, "sell_price_min_date", now)
                if current <= 0 or age is None or age > max_age_seconds:
                    continue
                base = baselines.get((iid, q))
                baseline = base["baseline"] if base else None
                vol = base["vol_per_day"] if base else None
                spike = (current / baseline) if baseline and baseline > 0 else None
                results.append(
                    {
                        "id": iid,
                        "name": names.get_name(iid),
                        "tier": meta.get("tier"),
                        "enchant": meta.get("enchant"),
                        "quality": q,
                        "city": city,
                        "current": current,
                        "baseline": int(round(baseline)) if baseline else None,
                        "inflation": int(round(current - baseline)) if baseline else None,
                        "spike": spike,
                        "vol_per_day": vol,
                        "age": age,
                        "verdict": _verdict(spike, vol),
                    }
                )

    # Most suspicious first; rows with no baseline (spike None) sink to the bottom.
    results.sort(key=lambda r: (r["spike"] is not None, r["spike"] or 0), reverse=True)
    return results


async def scan_craft(
    item_ids: list[str],
    cities: list[str],
    return_rate: float,
    max_age_seconds: int = MAX_AGE_SECONDS,
) -> list[dict]:
    """Craft-profit for each gear id: material cost vs sell value.

    Each material is bought at its cheapest selected city (instant buy =
    sell_price_min), with the crafting return rate refunding the *returnable*
    materials (artifacts/tokens, ret=False, are never refunded). The crafted
    item is sold by listing your own order at the best sane city price, net of
    SELL_ORDER_TAX. Quality is assumed Normal for both materials and output.
    """
    recs = {iid: recipes.get_recipe(iid) for iid in item_ids}
    fetch_ids = set(item_ids)
    for rec in recs.values():
        if rec:
            for m in rec["materials"]:
                fetch_ids.add(m["id"])

    # Materials are bought in the royal cities; the crafted item can be sold
    # either by listing in a city OR instant-sold to the Black Market.
    rows = await fetch_prices(list(fetch_ids), list({*cities, BLACK_MARKET}))
    idx = index_rows(rows, quality=1)
    now = datetime.now(timezone.utc)

    results = []
    for iid in item_ids:
        meta = names.parse_id(iid)
        base = {
            "id": iid,
            "name": names.get_name(iid),
            "tier": meta["tier"] if meta else 0,
            "enchant": meta["enchant"] if meta else 0,
            "return_rate": return_rate,
        }
        rec = recs[iid]
        if not rec:
            results.append(
                {
                    **base,
                    "no_recipe": True,
                    "materials": [],
                    "craft_cost": None,
                    "sell_price": None,
                    "sell_city": None,
                    "sell_age": None,
                    "net_sell": None,
                    "profit": None,
                    "roi": None,
                    "missing": ["no known recipe"],
                }
            )
            continue

        mats = []
        running_cost = 0.0
        for m in rec["materials"]:
            cs = _cheapest_sell(idx, m["id"], cities, now, max_age_seconds)
            eff = m["count"] * (1 - return_rate) if m["ret"] else m["count"]
            entry = {
                "id": m["id"],
                "name": names.get_name(m["id"]),
                "count": m["count"],
                "ret": m["ret"],
                "eff_count": eff,
            }
            if cs is None:
                entry.update({"unit_price": None, "city": None, "cost": None, "age": None})
            else:
                cost = eff * cs["price"]
                running_cost += cost
                entry.update(
                    {"unit_price": cs["price"], "city": cs["city"], "cost": cost, "age": cs["age"]}
                )
            mats.append(entry)

        missing = [m["name"] for m in mats if m["cost"] is None]
        mats_complete = not missing
        craft_cost = running_cost if mats_complete else None

        # Sell option A: list your own order in a royal city (net 6.5%).
        city_cities = [c for c in cities if c != BLACK_MARKET]
        listing = _highest_listing(idx, iid, city_cities, now, max_age_seconds)
        city_net = listing["price"] * (1 - SELL_ORDER_TAX) if listing else None
        # Sell option B: instant-sell into the Black Market buy orders (net 4%).
        bm_row = idx.get((iid, BLACK_MARKET))
        bm_price = (bm_row or {}).get("buy_price_max") or 0
        bm_age = age_seconds(bm_row, "buy_price_max_date", now) if bm_row else None
        bm_ok = bm_price > 0 and bm_age is not None and bm_age <= max_age_seconds
        bm_net = bm_price * (1 - BM_TAX) if bm_ok else None

        options = []
        if city_net is not None:
            options.append((city_net, f"List @ {listing['city']}", listing["price"], listing["age"]))
        if bm_net is not None:
            options.append((bm_net, "Black Market", bm_price, bm_age))
        if options:
            net_sell, sell_venue, sell_price, sell_age = max(options, key=lambda o: o[0])
        else:
            net_sell = sell_venue = sell_price = sell_age = None
            missing.append("item sell price")

        if mats_complete and net_sell is not None:
            profit = net_sell - craft_cost
            roi = profit / craft_cost if craft_cost > 0 else None
        else:
            profit = roi = None

        results.append(
            {
                **base,
                "no_recipe": False,
                "materials": mats,
                "craft_cost": craft_cost,
                "sell_price": sell_price,
                "sell_venue": sell_venue,
                "sell_age": sell_age,
                "net_sell": net_sell,
                "profit": profit,
                "roi": roi,
                "missing": missing,
            }
        )

    # Most profitable first; incomplete rows (profit None) sink to the bottom.
    results.sort(key=lambda r: (r["profit"] is not None, r["profit"] or 0), reverse=True)
    return results


# The manual Craft calc only wants a starting price the user can then correct, so
# it accepts much older data than the automated scanners (which need fresh prices
# to trust a profit number). The fill shows each price's age so staleness is
# visible; the user judges and overrides.
CRAFTCALC_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14 days


async def fetch_craft_prices(
    items: list[tuple[str, list[str]]],
    cities: list[str],
    max_age_seconds: int = CRAFTCALC_MAX_AGE_SECONDS,
) -> dict:
    """Batch live prices to auto-fill the manual Craft calc tabs.

    `items` is a list of (crafted_id, [material_ids]) — typically one entry per
    enchant level of the same gear. A single network fetch covers every id.
    Returns {crafted_id: {
        "materials": {mat_id: {"price","city","age"} | None},  # cheapest buy
        "sell_city": {"price","city","age"} | None,            # highest sane listing
        "sell_bm":   {"price","city","age"} | None,            # Black Market sell
    }}. Both sell venues are returned so each tab picks the one it's set to. The
    tab applies the return rate, taxes and batch sizing; the user can override any
    field. Prices up to `max_age_seconds` old are returned with their age (this is
    a manual tool, so staleness is shown rather than hidden).
    """
    all_ids: set[str] = set()
    for crafted_id, material_ids in items:
        all_ids.add(crafted_id)
        all_ids.update(material_ids)
    rows = await fetch_prices(list(all_ids), list({*cities, BLACK_MARKET}))
    idx = index_rows(rows, quality=1)
    now = datetime.now(timezone.utc)

    out: dict[str, dict] = {}
    for crafted_id, material_ids in items:
        mats: dict[str, dict | None] = {}
        for mid in material_ids:
            cs = _cheapest_sell(idx, mid, cities, now, max_age_seconds)
            mats[mid] = (
                {"price": int(round(cs["price"])), "city": cs["city"], "age": cs["age"]}
                if cs
                else None
            )
        listing = _highest_listing(idx, crafted_id, cities, now, max_age_seconds)
        sell_city = (
            {"price": int(round(listing["price"])), "city": listing["city"], "age": listing["age"]}
            if listing
            else None
        )
        bm_row = idx.get((crafted_id, BLACK_MARKET))
        bm_price = (bm_row or {}).get("buy_price_max") or 0
        bm_age = age_seconds(bm_row, "buy_price_max_date", now) if bm_row else None
        sell_bm = (
            {"price": int(round(bm_price)), "city": BLACK_MARKET, "age": bm_age}
            if (bm_price > 0 and bm_age is not None and bm_age <= max_age_seconds)
            else None
        )
        out[crafted_id] = {"materials": mats, "sell_city": sell_city, "sell_bm": sell_bm}
    return out
