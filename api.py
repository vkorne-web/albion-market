import asyncio
from datetime import datetime, timezone
import httpx

BASE_URL = "https://west.albion-online-data.com/api/v2/stats/prices"
HISTORY_URL = "https://west.albion-online-data.com/api/v2/stats/history"
CHUNK = 100  # max item IDs per request to stay well under URL limits
HISTORY_CHUNK = 50  # history responses are heavier; smaller chunks
MAX_CONCURRENT = 8


async def _fetch_chunk(client: httpx.AsyncClient, sem: asyncio.Semaphore, item_ids: list[str], locations: list[str]):
    async with sem:
        url = f"{BASE_URL}/{','.join(item_ids)}"
        params = {"locations": ",".join(locations)}
        r = await client.get(url, params=params, timeout=30.0)
        r.raise_for_status()
        return r.json()


async def fetch_prices(item_ids: list[str], locations: list[str]) -> list[dict]:
    """Returns raw rows from the Albion Online Data Project API."""
    unique_ids = list(dict.fromkeys(item_ids))
    chunks = [unique_ids[i : i + CHUNK] for i in range(0, len(unique_ids), CHUNK)]
    rows: list[dict] = []
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(headers={"User-Agent": "albion-market-desktop/0.1"}) as client:
        results = await asyncio.gather(
            *(_fetch_chunk(client, sem, c, locations) for c in chunks),
            return_exceptions=True,
        )
    for res in results:
        if isinstance(res, Exception):
            continue
        rows.extend(res)
    return rows


async def _fetch_history_chunk(client, sem, item_ids, locations, time_scale):
    async with sem:
        url = f"{HISTORY_URL}/{','.join(item_ids)}"
        params = {"locations": ",".join(locations), "time-scale": time_scale}
        r = await client.get(url, params=params, timeout=30.0)
        r.raise_for_status()
        return r.json()


async def fetch_history(
    item_ids: list[str], locations: list[str], time_scale: int = 24
) -> list[dict]:
    unique_ids = list(dict.fromkeys(item_ids))
    chunks = [unique_ids[i : i + HISTORY_CHUNK] for i in range(0, len(unique_ids), HISTORY_CHUNK)]
    rows: list[dict] = []
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(headers={"User-Agent": "albion-market-desktop/0.1"}) as client:
        results = await asyncio.gather(
            *(_fetch_history_chunk(client, sem, c, locations, time_scale) for c in chunks),
            return_exceptions=True,
        )
    for res in results:
        if isinstance(res, Exception):
            continue
        rows.extend(res)
    return rows


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # API returns e.g. "2024-03-12T14:22:00" (UTC, no tz)
        return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def index_rows(rows: list[dict], quality: int = 1) -> dict[tuple[str, str], dict]:
    """Index by (item_id, city). Filters to a single quality (default 1 = normal)."""
    out = {}
    for row in rows:
        if row.get("quality", 1) != quality:
            continue
        key = (row.get("item_id"), row.get("city"))
        out[key] = row
    return out


def age_seconds(row: dict, field: str, now: datetime) -> float | None:
    ts = _parse_ts(row.get(field, ""))
    if ts is None:
        return None
    return (now - ts).total_seconds()
