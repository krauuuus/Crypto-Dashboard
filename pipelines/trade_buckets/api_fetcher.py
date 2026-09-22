"""
api_fetcher.py
Telechargement via ccxt async pour les exchanges sans bulk data public :
OKX, Kraken, Coinbase.

Strategie :
  - On pagine les trades jour par jour (timestamp since/until)
  - Chaque jour est cache dans un parquet atomique
  - Si le parquet du jour existe deja -> skip
  - Les mois deja calcules sont aussi skippes
"""

import asyncio
import calendar
import datetime
import logging
from typing import Optional

import ccxt.async_support as ccxt_async
import numpy as np
import pandas as pd
from tqdm.asyncio import tqdm as atqdm

from .config import (ASSETS, EXCHANGE_CONFIG, RATE_LIMIT_SLEEP_S,
                     MAX_TRADES_PER_CALL, CSV_CHUNK_ROWS)
from .utils import (atomic_write_parquet, compute_bucket_stats,
                    daily_cache_path, monthly_result_path, missing_days)

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Initialisation ccxt
# ══════════════════════════════════════════════════════════════════════════════

_EXCHANGE_OVERRIDES = {
    "kraken": {"options": {"fetchTradesMethod": "publicGetTrades"}},
    "coinbase": {},
    "okx": {},
}

def build_exchange(exchange_id: str) -> ccxt_async.Exchange:
    ccxt_id  = EXCHANGE_CONFIG[exchange_id]["ccxt_id"]
    cls      = getattr(ccxt_async, ccxt_id)
    overrides = _EXCHANGE_OVERRIDES.get(exchange_id, {})
    return cls({
        "enableRateLimit": True,
        "options": {"defaultType": "spot", **overrides.get("options", {})},
    })


# ══════════════════════════════════════════════════════════════════════════════
# Telechargement d'un jour complet (pagination timestamp)
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_day_trades(exchange: ccxt_async.Exchange,
                            symbol: str,
                            date: datetime.date) -> pd.DataFrame:
    """
    Recupere tous les trades d'une journee pour un exchange ccxt.
    Pagine via le parametre `since` (timestamp en ms).
    Retourne un DataFrame avec ['usd_value'].
    """
    day_start = int(datetime.datetime.combine(
        date, datetime.time.min,
        tzinfo=datetime.timezone.utc
    ).timestamp() * 1000)
    day_end = int(datetime.datetime.combine(
        date, datetime.time.max,
        tzinfo=datetime.timezone.utc
    ).timestamp() * 1000)

    all_usd_values: list[float] = []
    since = day_start

    while True:
        try:
            trades = await exchange.fetch_trades(
                symbol,
                since=since,
                limit=MAX_TRADES_PER_CALL,
            )
        except (ccxt_async.NetworkError, ccxt_async.ExchangeError) as e:
            log.warning(f"Erreur fetch_trades ({symbol} {date}): {e}. Retry dans 5s.")
            await asyncio.sleep(5)
            continue

        if not trades:
            break

        for t in trades:
            ts = t.get("timestamp") or 0
            if ts >= day_end:
                break
            # cost = amount_base * price = valeur en quote currency (USDT ou USD)
            usd_val = t.get("cost") or (
                (t.get("amount") or 0) * (t.get("price") or 0)
            )
            if usd_val > 0:
                all_usd_values.append(float(usd_val))
        else:
            # Continuer la pagination seulement si le dernier trade est dans la journee
            last_ts = trades[-1].get("timestamp") or 0
            if last_ts >= day_end or len(trades) < MAX_TRADES_PER_CALL:
                break
            since = last_ts + 1
            await asyncio.sleep(RATE_LIMIT_SLEEP_S)
            continue
        break

    if not all_usd_values:
        return pd.DataFrame({"usd_value": pd.Series(dtype=np.float32)})

    return pd.DataFrame({
        "usd_value": np.array(all_usd_values, dtype=np.float32)
    })


# ══════════════════════════════════════════════════════════════════════════════
# Traitement mensuel (avec cache journalier)
# ══════════════════════════════════════════════════════════════════════════════

async def process_api_month(exchange_id: str,
                             asset: str,
                             year: int, month: int) -> Optional[pd.DataFrame]:
    """
    Calcule les bucket stats d'un mois entier pour un exchange API.
    - Skip le mois si le fichier mensuel existe deja.
    - Skip les jours deja caches.
    - Cache chaque jour atomiquement.
    """
    out_path = monthly_result_path(exchange_id, asset, year, month)
    if out_path.exists():
        log.debug(f"[{exchange_id}] Cache mensuel existant : {out_path.name}")
        return pd.read_parquet(out_path)

    symbol = ASSETS[asset][exchange_id]
    exchange = build_exchange(exchange_id)

    try:
        _, n_days = calendar.monthrange(year, month)
        monthly_frames: list[pd.DataFrame] = []

        for day in range(1, n_days + 1):
            date  = datetime.date(year, month, day)
            cache = daily_cache_path(exchange_id, asset, date)

            if cache.exists():
                df = pd.read_parquet(cache)
            else:
                df = await fetch_day_trades(exchange, symbol, date)
                if not df.empty:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_parquet(df, cache)

            if not df.empty:
                monthly_frames.append(df)

        if not monthly_frames:
            return None

        trades = pd.concat(monthly_frames, ignore_index=True)
        stats  = compute_bucket_stats(trades, exchange_id, asset, year, month)
        atomic_write_parquet(stats, out_path)
        log.info(f"[{exchange_id}] {year}-{month:02d} enregistre.")
        return stats

    finally:
        await exchange.close()


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrateur multi-exchange API
# ══════════════════════════════════════════════════════════════════════════════

async def run_api_exchanges(exchanges: list[str],
                             asset: str,
                             months: list[tuple[int, int]],
                             max_concurrent: int = 2) -> dict:
    """
    Traite les exchanges API en limitant la concurrence
    pour ne pas saturer les rate limits.
    """
    results = {}
    sem = asyncio.Semaphore(max_concurrent)

    async def bounded(exch, y, m):
        async with sem:
            return await process_api_month(exch, asset, y, m)

    tasks = {
        (exch, y, m): asyncio.create_task(bounded(exch, y, m))
        for exch in exchanges
        for y, m in months
    }

    for (exch, y, m), task in atqdm(
        tasks.items(), desc="API exchanges", unit="exchange-mois"
    ):
        try:
            res = await task
            if res is not None:
                results[(exch, y, m)] = res
        except Exception as e:
            log.error(f"[{exch}] {y}-{m:02d} : {e}")

    return results
