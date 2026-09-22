"""
bulk_downloader.py
Telecharge les fichiers CSV publics de Binance et Bybit.
Ces exchanges publient des archives completes — c'est 100x plus rapide
que d'appeler leur API trade par trade.

Binance : un ZIP mensuel  (~200-500 MB compresse)
          https://data.binance.vision/data/spot/monthly/trades/BTCUSDT/BTCUSDT-trades-2023-01.zip
          Colonnes : id, price, qty, quoteQty, time, isBuyerMaker, isBestMatch
          quoteQty = valeur en quote (USDT ≈ USD) -> usd_value direct

Bybit   : un CSV.gz journalier
          https://public.bybit.com/trading/BTCUSDT/BTCUSDT2023-01-15.csv.gz
          Colonnes : timestamp, symbol, side, size, price, ...
          usd_value = size * price
"""

import asyncio
import calendar
import datetime
import io
import json
import logging
import os
import time
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import aiohttp
import numpy as np
import pandas as pd
from tqdm import tqdm

from .config import ASSETS, EXCHANGE_CONFIG, CACHE_DIR, OUTPUT_DIR, CSV_CHUNK_ROWS, BUCKET_NAMES
from .utils import (atomic_write_parquet, compute_bucket_stats, assign_buckets,
                    daily_cache_path, monthly_result_path,
                    missing_months, missing_days)

log = logging.getLogger(__name__)

# ── Fichier de statut temps reel (lu par monitor.py) ─────────────────────────
_STATUS_FILE = OUTPUT_DIR / "pipeline_status.json"
_status: dict = {"in_progress": {}, "completed": [], "errors": []}

def _write_status() -> None:
    """Ecriture atomique du statut courant."""
    payload = {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "in_progress": list(_status["in_progress"].values()),
        "completed":   _status["completed"][-20:],   # 20 derniers
        "errors":      _status["errors"][-10:],
    }
    tmp = str(_STATUS_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, str(_STATUS_FILE))

def _start(exchange: str, year: int, month: int) -> float:
    key = f"{exchange}_{year}_{month:02d}"
    t = time.time()
    _status["in_progress"][key] = {
        "exchange":    exchange,
        "month":       f"{year}-{month:02d}",
        "started_at":  datetime.datetime.fromtimestamp(t).isoformat(timespec="seconds"),
        "bytes_done":  0,
        "bytes_total": 0,
        "days_done":   0,
        "days_total":  0,
    }
    _write_status()
    return t

def _progress(exchange: str, year: int, month: int, **fields) -> None:
    """Met a jour les champs de progression intra-mois (sans ecriture si pas dans in_progress)."""
    key = f"{exchange}_{year}_{month:02d}"
    if key in _status["in_progress"]:
        _status["in_progress"][key].update(fields)
        _write_status()

def _done(exchange: str, year: int, month: int, t0: float, n_rows: int = 0) -> None:
    key = f"{exchange}_{year}_{month:02d}"
    dur = time.time() - t0
    _status["in_progress"].pop(key, None)
    _status["completed"].append({
        "exchange":    exchange,
        "month":       f"{year}-{month:02d}",
        "duration_s":  round(dur, 1),
        "n_rows":      n_rows,
    })
    _write_status()

def _error(exchange: str, year: int, month: int, t0: float, msg: str) -> None:
    key = f"{exchange}_{year}_{month:02d}"
    _status["in_progress"].pop(key, None)
    _status["errors"].append({
        "exchange": exchange,
        "month":    f"{year}-{month:02d}",
        "duration_s": round(time.time() - t0, 1),
        "error":    str(msg)[:120],
    })
    _write_status()


# ══════════════════════════════════════════════════════════════════════════════
# BINANCE — telechargement mensuel en bulk
# ══════════════════════════════════════════════════════════════════════════════

async def download_binance_month(session: aiohttp.ClientSession,
                                  asset: str,
                                  year: int, month: int) -> Optional[pd.DataFrame]:
    """
    Telecharge le ZIP mensuel Binance en streamant vers un fichier temporaire
    (evite les OOM sur les gros fichiers 2021 de 1-2 GB), lit le CSV en chunks.
    Retourne un DataFrame reduit a ['usd_value'], ou None si fichier indisponible.
    """
    pair = ASSETS[asset]["binance"]
    url  = EXCHANGE_CONFIG["binance"]["bulk_url"].format(
        pair=pair, year=year, month=month
    )

    log.info(f"[binance] Telechargement {asset} {year}-{month:02d} : {url}")

    # sock_read=60s : timeout si aucune donnee recue pendant 60s
    # (reduit de 120s pour detecter les stalls plus vite sur connexion lente)
    # total=None : pas de timeout global, les gros fichiers prennent du temps
    timeout = aiohttp.ClientTimeout(sock_connect=30, sock_read=60)

    tmp_path = None
    try:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status == 404:
                log.warning(f"[binance] Fichier non disponible : {url}")
                return None
            resp.raise_for_status()

            # Stream vers fichier temp + progression en temps reel dans status.json
            fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            content_length = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_progress = -1   # -1 force la premiere mise a jour immediate
            UPDATE_EVERY  = 10 * 1024 * 1024   # mise a jour status toutes les 10 MB
            with os.fdopen(fd, "wb") as f:
                async for chunk in resp.content.iter_chunked(512 * 1024):  # 512 KB chunks
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded - last_progress >= UPDATE_EVERY:
                        _progress("binance", year, month,
                                  bytes_done=downloaded,
                                  bytes_total=content_length)
                        last_progress = downloaded

        log.info(f"[binance] {year}-{month:02d} : {downloaded / 1e6:.0f} MB telecharges")

        # Format aggTrades Binance (variable selon la periode, parfois avec header) :
        #   col 0 : agg_trade_id | col 1 : price | col 2 : qty
        #   col 5 : transact_time (ms) | col 6 : is_buyer_maker
        with zipfile.ZipFile(tmp_path) as zf:
            csv_name = zf.namelist()[0]

            # Detecter le nombre de colonnes (et presence eventuelle d'un header texte)
            with zf.open(csv_name) as f:
                first = pd.read_csv(f, header=None, nrows=2)
            ncols = first.shape[1]
            price_col = 1 if ncols >= 3 else 0
            qty_col   = 2 if ncols >= 3 else 1
            ts_col    = 5 if ncols >= 6 else None

            chunks = []
            use = [price_col, qty_col] + ([ts_col] if ts_col is not None else [])
            with zf.open(csv_name) as f:
                for chunk in pd.read_csv(
                    f,
                    header=None,
                    usecols=use,
                    dtype=str,          # lire en str -> gere les lignes header texte
                    chunksize=CSV_CHUNK_ROWS
                ):
                    chunk[price_col] = pd.to_numeric(chunk[price_col], errors="coerce")
                    chunk[qty_col]   = pd.to_numeric(chunk[qty_col],   errors="coerce")
                    chunk = chunk.dropna(subset=[price_col, qty_col])
                    chunk["usd_value"] = (
                        chunk[price_col].astype(np.float32) *
                        chunk[qty_col].astype(np.float32)
                    )
                    if ts_col is not None:
                        chunk["ts_ms"] = pd.to_numeric(chunk[ts_col], errors="coerce")
                        chunk = chunk[["usd_value", "ts_ms"]]
                    else:
                        chunk = chunk[["usd_value"]]
                    chunks.append(chunk)

        if not chunks:
            log.warning(f"[binance] {year}-{month:02d} : aucune trade valide trouvee")
            return None

        return pd.concat(chunks, ignore_index=True)

    finally:
        # Nettoyage du fichier temporaire dans tous les cas
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def process_binance_month(session: aiohttp.ClientSession,
                                 asset: str,
                                 year: int, month: int,
                                 max_retries: int = 3) -> Optional[pd.DataFrame]:
    """
    Point d'entree principal pour un mois Binance.
    Skip si le resultat mensuel existe deja.
    Retry automatique sur erreurs reseau (ConnectionReset, ContentLength).
    """
    out_path = monthly_result_path("binance", asset, year, month)
    if out_path.exists():
        log.debug(f"[binance] Cache mensuel existant : {out_path.name}")
        return pd.read_parquet(out_path)

    for attempt in range(1, max_retries + 1):
        try:
            trades = await download_binance_month(session, asset, year, month)
            if trades is None:
                return None
            stats = compute_bucket_stats(trades, "binance", asset, year, month)
            atomic_write_parquet(stats, out_path)
            log.info(f"[binance] Mois {year}-{month:02d} enregistre : {out_path.name}")
            return stats
        except (aiohttp.ClientConnectionError,
                aiohttp.ClientPayloadError,
                aiohttp.ServerDisconnectedError,
                asyncio.TimeoutError) as e:
            if attempt < max_retries:
                wait = 5 * attempt
                log.warning(f"[binance] {year}-{month:02d} tentative {attempt}/{max_retries} "
                            f"echouee ({type(e).__name__}), retry dans {wait}s...")
                await asyncio.sleep(wait)
            else:
                log.error(f"[binance] {year}-{month:02d} abandonne apres {max_retries} tentatives : {e}")
                return None


# ══════════════════════════════════════════════════════════════════════════════
# BYBIT — telechargement journalier en bulk
# ══════════════════════════════════════════════════════════════════════════════

async def download_bybit_day(session: aiohttp.ClientSession,
                              asset: str,
                              date: datetime.date) -> Optional[pd.DataFrame]:
    """
    Telecharge le CSV.gz journalier Bybit.
    Colonnes : timestamp, symbol, side, size, price
    usd_value = size (en BTC) * price (en USD)
    """
    pair = ASSETS[asset]["bybit"]
    url  = EXCHANGE_CONFIG["bybit"]["bulk_url"].format(
        pair=pair, year=date.year, month=date.month, day=date.day
    )

    cache = daily_cache_path("bybit", asset, date)
    if cache.exists():
        return pd.read_parquet(cache)

    log.debug(f"[bybit] {date} : {url}")

    async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
        if resp.status in (403, 404):
            return None
        resp.raise_for_status()
        raw_bytes = await resp.read()

    df = pd.read_csv(
        io.BytesIO(raw_bytes),
        compression="gzip",
        usecols=["timestamp", "size", "price"],
        dtype={"size": np.float32, "price": np.float32}
    )
    df["usd_value"] = (df["size"] * df["price"]).astype(np.float32)
    df = df[["usd_value"]].copy()

    # Cache journalier
    cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(df, cache)
    return df


async def process_bybit_month(session: aiohttp.ClientSession,
                               asset: str,
                               year: int, month: int) -> Optional[pd.DataFrame]:
    """
    Telecharge et agrege tous les jours du mois pour Bybit.
    Aggregation journee par journee (pas de pd.concat global) pour eviter
    les OOM sur les mois actifs 2024+ qui ont des millions de trades.
    """
    out_path = monthly_result_path("bybit", asset, year, month)
    if out_path.exists():
        return pd.read_parquet(out_path)

    _, n_days = calendar.monthrange(year, month)
    dates = [datetime.date(year, month, d) for d in range(1, n_days + 1)]

    # Accumulateurs : 5 scalaires par metrique — empreinte memoire constante
    vol_by_bucket = {b: 0.0 for b in BUCKET_NAMES}
    cnt_by_bucket = {b: 0   for b in BUCKET_NAMES}
    any_data = False

    days_total = len(dates)
    _progress("bybit", year, month, days_done=0, days_total=days_total)

    for i in range(0, len(dates), 20):
        batch = dates[i:i+20]
        results = await asyncio.gather(
            *[download_bybit_day(session, asset, d) for d in batch],
            return_exceptions=True
        )
        # Un jour a la fois : on ne garde jamais tout le mois en RAM
        for r in results:
            if not isinstance(r, pd.DataFrame) or r.empty:
                continue
            r = r.copy()
            r["bucket"] = assign_buckets(r["usd_value"])
            grp = r.groupby("bucket", observed=False)
            vol = grp["usd_value"].sum()
            cnt = grp["usd_value"].count()
            for b in BUCKET_NAMES:
                vol_by_bucket[b] += float(vol.get(b, 0.0))
                cnt_by_bucket[b] += int(cnt.get(b, 0))
            any_data = True

        days_done = min(i + 20, days_total)
        _progress("bybit", year, month, days_done=days_done, days_total=days_total)

    if not any_data:
        return None

    total_vol = sum(vol_by_bucket.values())
    total_cnt = sum(cnt_by_bucket.values())

    volumes = [vol_by_bucket[b] for b in BUCKET_NAMES]
    counts  = [cnt_by_bucket[b] for b in BUCKET_NAMES]

    stats = pd.DataFrame({
        "year":              year,
        "month":             month,
        "exchange":          "bybit",
        "asset":             asset,
        "bucket":            BUCKET_NAMES,
        "volume_usd":        volumes,
        "volume_share_pct":  [v / total_vol * 100 if total_vol > 0 else 0.0
                              for v in volumes],
        "trade_count":       counts,
        "count_share_pct":   [c / total_cnt * 100 if total_cnt > 0 else 0.0
                              for c in counts],
        "total_volume_usd":  total_vol,
        "total_trade_count": total_cnt,
    })

    atomic_write_parquet(stats, out_path)
    log.info(f"[bybit] Mois {year}-{month:02d} enregistre.")
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrateur bulk (Binance + Bybit en parallele)
# ══════════════════════════════════════════════════════════════════════════════

async def run_bulk_exchanges(asset: str,
                              months: list[tuple[int, int]],
                              max_concurrent: int = 4) -> dict:
    """
    Traite tous les mois manquants pour Binance et Bybit en PARALLELE.

    Semaphores separes (optimisation cle) :
      - Binance : max_concurrent slots (gros ZIP 100-1000 MB, bande passante limitante)
      - Bybit   : 15 slots independants (petits .gz journaliers, serveur different)
    => Bybit tourne pleinement pendant que Binance telecharge, sans se bloquer mutuellement.
    """
    results = {}

    # Semaphores INDEPENDANTS pour Binance et Bybit
    sem_binance = asyncio.Semaphore(max_concurrent)
    sem_bybit   = asyncio.Semaphore(15)   # Bybit : fichiers legers, beaucoup en parallele

    async def bounded_binance(session, y, m):
        async with sem_binance:
            t0 = _start("binance", y, m)
            try:
                res = await process_binance_month(session, asset, y, m)
                n = len(res) if res is not None else 0
                _done("binance", y, m, t0, n)
                return res
            except Exception as e:
                _error("binance", y, m, t0, str(e))
                raise

    async def bounded_bybit(session, y, m):
        async with sem_bybit:
            t0 = _start("bybit", y, m)
            try:
                res = await process_bybit_month(session, asset, y, m)
                n = len(res) if res is not None else 0
                _done("bybit", y, m, t0, n)
                return res
            except Exception as e:
                _error("bybit", y, m, t0, str(e))
                raise

    # Plus de connexions TCP simultanees pour supporter la concurrence accrue
    connector = aiohttp.TCPConnector(limit=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks_b = {(y, m): asyncio.create_task(bounded_binance(session, y, m))
                   for y, m in months}
        tasks_bb = {(y, m): asyncio.create_task(bounded_bybit(session, y, m))
                    for y, m in months}

        for (y, m), task in tqdm(tasks_b.items(),
                                  desc="Binance", unit="mois"):
            try:
                res = await task
                if res is not None:
                    results[("binance", y, m)] = res
            except Exception as e:
                log.error(f"[binance] {y}-{m:02d} erreur : {e}")

        for (y, m), task in tqdm(tasks_bb.items(),
                                  desc="Bybit  ", unit="mois"):
            try:
                res = await task
                if res is not None:
                    results[("bybit", y, m)] = res
            except Exception as e:
                log.error(f"[bybit] {y}-{m:02d} erreur : {e}")

    return results
