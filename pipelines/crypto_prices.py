"""
crypto_prices.py — Prix daily OHLCV pour le basket CryptoStability
Source : CryptoCompare histoday (2000 jours par appel, pagination auto)
Cache  : data/cache/crypto_prices.parquet, régénéré si > 24h

Usage autonome :
    python -m pipelines.crypto_prices
"""

import datetime
import json
import logging
import time
import urllib.request
from pathlib import Path

import pandas as pd

from .config import ROOT, CRYPTOCOMPARE_API_KEY, CRYPTO_BASKET, START_DATE

log = logging.getLogger(__name__)

CACHE_FILE  = ROOT / "data" / "cache" / "crypto_prices.parquet"
_CACHE_META = ROOT / "data" / "cache" / "crypto_prices_meta.json"
CACHE_TTL_H = 24   # heures avant de rafraîchir

_CC_BASE = "https://min-api.cryptocompare.com/data/v2/histoday"
_MAX_LIMIT = 2000  # max par appel CryptoCompare


# ══════════════════════════════════════════════════════════════════════════════
# Cache management
# ══════════════════════════════════════════════════════════════════════════════

def _cache_is_fresh() -> bool:
    if not CACHE_FILE.exists() or not _CACHE_META.exists():
        return False
    meta = json.loads(_CACHE_META.read_text())
    fetched_at = datetime.datetime.fromisoformat(meta["fetched_at"])
    age_h = (datetime.datetime.now() - fetched_at).total_seconds() / 3600
    return age_h < CACHE_TTL_H

def _save_meta() -> None:
    _CACHE_META.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_META.write_text(json.dumps({
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds")
    }))


# ══════════════════════════════════════════════════════════════════════════════
# Fetch CryptoCompare histoday (avec pagination)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_symbol(symbol: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
    """
    Pagine les appels histoday pour couvrir start→end.
    CryptoCompare retourne les données du plus récent au plus ancien,
    on pagine avec toTs (timestamp de fin du batch).
    """
    all_rows = []
    to_ts = int(datetime.datetime(end.year, end.month, end.day, 23, 59).timestamp())
    start_ts = int(datetime.datetime(start.year, start.month, start.day).timestamp())

    while True:
        url = (
            f"{_CC_BASE}?fsym={symbol}&tsym=USD"
            f"&limit={_MAX_LIMIT}&toTs={to_ts}"
            f"&api_key={CRYPTOCOMPARE_API_KEY}"
        )
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            log.warning(f"[CryptoCompare] {symbol} erreur: {e}")
            break

        if data.get("Response") != "Success":
            log.warning(f"[CryptoCompare] {symbol} réponse: {data.get('Message','?')}")
            break

        rows = data["Data"]["Data"]
        if not rows:
            break

        all_rows.extend(rows)

        earliest_ts = rows[0]["time"]
        if earliest_ts <= start_ts:
            break

        # page suivante : toTs = dernier ts récupéré - 1 jour
        to_ts = earliest_ts - 86400
        time.sleep(0.1)   # éviter de dépasser le rate limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
    df = df[df["date"] >= start][df["date"] <= end].copy()
    df = df[["date", "open", "high", "low", "close", "volumefrom", "volumeto"]].copy()
    df.columns = ["date", "open", "high", "low", "close", "volume_crypto", "volume_usd"]
    df["symbol"] = symbol
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Point d'entrée public
# ══════════════════════════════════════════════════════════════════════════════

def load_prices(force_refresh: bool = False) -> pd.DataFrame:
    """
    Retourne un DataFrame avec colonnes :
        date, symbol, open, high, low, close, volume_crypto, volume_usd
    Utilise le cache parquet si < 24h, sinon re-fetch CryptoCompare.
    """
    if not force_refresh and _cache_is_fresh():
        log.info("crypto_prices : cache frais, pas de re-fetch")
        return pd.read_parquet(CACHE_FILE)

    log.info(f"crypto_prices : fetch CryptoCompare pour {CRYPTO_BASKET}")
    start = datetime.date.fromisoformat(START_DATE)
    end   = datetime.date.today()

    frames = []
    for symbol in CRYPTO_BASKET:
        log.info(f"  → {symbol}")
        df = _fetch_symbol(symbol, start, end)
        if not df.empty:
            frames.append(df)
        else:
            log.warning(f"  ✗ {symbol} : aucune donnée")

    if not frames:
        raise RuntimeError("CryptoCompare : aucune donnée récupérée — vérifier la clé API")

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["symbol", "date"]).reset_index(drop=True)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(CACHE_FILE, index=False, compression="snappy")
    _save_meta()

    log.info(f"crypto_prices : {len(result)} lignes sauvegardées → {CACHE_FILE}")
    return result


def load_returns(force_refresh: bool = False) -> pd.DataFrame:
    """
    Pivot wide : index=date, colonnes=symboles, valeurs=log-rendements daily.
    Équivalent R : xts / zoo avec diff(log(prix)).
    """
    import numpy as np
    prices = load_prices(force_refresh)
    wide = prices.pivot(index="date", columns="symbol", values="close")
    wide = wide[CRYPTO_BASKET]   # ordre canonique du basket
    returns = np.log(wide / wide.shift(1)).dropna()
    returns.columns.name = None
    return returns


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = load_prices(force_refresh=True)
    print(df.groupby("symbol")[["date", "close"]].last().to_string())
    print(f"\n{len(df)} lignes, {df['symbol'].nunique()} symboles")
