"""
global_market_cap.py — Market cap totale du marché crypto (via CoinGecko, gratuit)
Cache : data/cache/global_market_cap.parquet, TTL 24h
"""
import datetime
import json
import logging
import urllib.request
from pathlib import Path

import pandas as pd

from .config import ROOT, COINMARKETCAP_API_KEY

log = logging.getLogger(__name__)

CACHE_FILE  = ROOT / "data" / "cache" / "global_market_cap.parquet"
_CACHE_META = ROOT / "data" / "cache" / "global_market_cap_meta.json"
CACHE_TTL_H = 24

_CMC_URL = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/historical"


def _cache_is_fresh() -> bool:
    if not CACHE_FILE.exists() or not _CACHE_META.exists():
        return False
    meta = json.loads(_CACHE_META.read_text())
    age_h = (datetime.datetime.now() -
             datetime.datetime.fromisoformat(meta["fetched_at"])
             ).total_seconds() / 3600
    return age_h < CACHE_TTL_H


def _save_meta() -> None:
    _CACHE_META.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_META.write_text(json.dumps({
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds")
    }))


def load_global_market_cap(force_refresh: bool = False) -> pd.Series:
    """
    Retourne une pd.Series (index=date, values=market_cap_usd) journalière.
    """
    if not force_refresh and _cache_is_fresh():
        log.info("global_market_cap : cache frais")
        df = pd.read_parquet(CACHE_FILE)
        return df.set_index("date")["market_cap_usd"]

    if not COINMARKETCAP_API_KEY:
        log.warning("global_market_cap : COINMARKETCAP_API_KEY manquante")
        return pd.Series(dtype=float, name="market_cap_usd")

    log.info("global_market_cap : fetch CoinMarketCap (historical global metrics)…")
    params = "?time_start=2018-01-01&interval=daily&convert=USD&count=10000"
    url = _CMC_URL + params
    try:
        req = urllib.request.Request(url, headers={"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.warning(f"global_market_cap : erreur CoinMarketCap ({e})")
        if CACHE_FILE.exists():
            log.info("global_market_cap : utilisation du cache périmé")
            df = pd.read_parquet(CACHE_FILE)
            return df.set_index("date")["market_cap_usd"]
        return pd.Series(dtype=float, name="market_cap_usd")

    quotes = data.get("data", {}).get("quotes", [])
    if not quotes:
        log.warning("global_market_cap : réponse CMC vide")
        return pd.Series(dtype=float, name="market_cap_usd")

    records = []
    for q in quotes:
        ts  = q.get("timestamp", "")[:10]
        usd = q.get("quote", {}).get("USD", {})
        mc  = usd.get("total_market_cap")
        if ts and mc:
            records.append({"date": ts, "market_cap_usd": float(mc)})

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").drop_duplicates("date")

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_FILE, index=False, compression="snappy")
    _save_meta()

    log.info(f"global_market_cap : {len(df)} jours -> {CACHE_FILE}")
    return df.set_index("date")["market_cap_usd"]
