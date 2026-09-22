"""
utils.py — Helpers partagés : cache, atomic write, bucket assignment, USDT correction
"""
import datetime
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

from .config import BUCKETS, BUCKET_NAMES, CACHE_DIR, MONTHLY_DIR


# ══════════════════════════════════════════════════════════════════════════════
# Ecriture atomique via temp file
# Garantit qu'on ne laisse jamais un fichier partiel en cas d'interruption
# ══════════════════════════════════════════════════════════════════════════════

def atomic_write_parquet(df: pd.DataFrame, target: Path) -> None:
    """
    Ecrit df en parquet de facon atomique :
    1. Ecrit dans un tempfile dans le meme dossier (meme partition)
    2. Renomme en target (operation atomique sur tous les OS modernes)
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp.parquet",
        dir=target.parent
    )
    os.close(tmp_fd)
    try:
        df.to_parquet(tmp_path, index=False, compression="snappy")
        shutil.move(tmp_path, target)   # atomique si meme partition
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


# ══════════════════════════════════════════════════════════════════════════════
# Chemins de cache standardises
# ══════════════════════════════════════════════════════════════════════════════

def daily_cache_path(exchange: str, asset: str, date: "datetime.date") -> Path:
    return CACHE_DIR / exchange / asset / f"{date.isoformat()}.parquet"

def monthly_result_path(exchange: str, asset: str,
                         year: int, month: int) -> Path:
    return MONTHLY_DIR / exchange / asset / f"{year}_{month:02d}.parquet"

def aggregated_result_path(asset: str, year: int, month: int) -> Path:
    return MONTHLY_DIR / "aggregated" / asset / f"{year}_{month:02d}.parquet"

def cftc_path(year: int, month: int) -> Path:
    return MONTHLY_DIR / "cftc" / f"{year}_{month:02d}.parquet"


# ══════════════════════════════════════════════════════════════════════════════
# Attribution des buckets (vectorisee, tres rapide)
# ══════════════════════════════════════════════════════════════════════════════

# Precompute les limites pour numpy (evite les boucles Python)
_BUCKET_BREAKS = np.array([b[2] for b in BUCKETS])   # bornes superieures (b[2] = borne sup du tuple (nom, inf, sup))
_BUCKET_NAMES  = np.array(BUCKET_NAMES)

def assign_buckets(usd_values: pd.Series) -> pd.Series:
    """
    Attribution vectorisee : np.searchsorted est O(n log k) avec k=5 buckets.
    ~10x plus rapide qu'un apply() Python.
    """
    indices = np.searchsorted(_BUCKET_BREAKS, usd_values.values, side="right")
    indices = np.clip(indices, 0, len(_BUCKET_NAMES) - 1)
    return pd.Categorical(
        _BUCKET_NAMES[indices],
        categories=BUCKET_NAMES,
        ordered=True
    )


# ══════════════════════════════════════════════════════════════════════════════
# Calcul des statistiques de bucket a partir d'un DataFrame de trades
# ══════════════════════════════════════════════════════════════════════════════

def compute_bucket_stats(trades_df: pd.DataFrame,
                          exchange: str,
                          asset: str,
                          year: int,
                          month: int) -> pd.DataFrame:
    """
    Entree  : DataFrame avec colonne 'usd_value' (valeur USD de chaque trade)
    Sortie  : DataFrame avec une ligne par bucket contenant :
                volume_usd, volume_share_pct, trade_count, count_share_pct
    """
    if trades_df.empty:
        return _empty_bucket_stats(exchange, asset, year, month)

    trades_df = trades_df.copy()
    # Correction USDT→USD (négligeable en temps normal, matérielle lors des stress events)
    trades_df["usd_value"] = apply_usdt_correction(trades_df["usd_value"], year, month)
    trades_df["bucket"] = assign_buckets(trades_df["usd_value"])

    grp = trades_df.groupby("bucket", observed=False)
    vol  = grp["usd_value"].sum()
    cnt  = grp["usd_value"].count()

    total_vol = vol.sum()
    total_cnt = cnt.sum()

    result = pd.DataFrame({
        "year":               year,
        "month":              month,
        "exchange":           exchange,
        "asset":              asset,
        "bucket":             BUCKET_NAMES,
        "volume_usd":         vol.reindex(BUCKET_NAMES, fill_value=0).values,
        "volume_share_pct":   (vol.reindex(BUCKET_NAMES, fill_value=0) / total_vol * 100
                               ).values if total_vol > 0 else [0.0] * len(BUCKET_NAMES),
        "trade_count":        cnt.reindex(BUCKET_NAMES, fill_value=0).values,
        "count_share_pct":    (cnt.reindex(BUCKET_NAMES, fill_value=0) / total_cnt * 100
                               ).values if total_cnt > 0 else [0.0] * len(BUCKET_NAMES),
        "total_volume_usd":   total_vol,
        "total_trade_count":  total_cnt,
    })
    return result


def _empty_bucket_stats(exchange, asset, year, month) -> pd.DataFrame:
    return pd.DataFrame({
        "year": year, "month": month,
        "exchange": exchange, "asset": asset,
        "bucket": BUCKET_NAMES,
        "volume_usd": 0.0, "volume_share_pct": 0.0,
        "trade_count": 0, "count_share_pct": 0.0,
        "total_volume_usd": 0.0, "total_trade_count": 0,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Detection des mois manquants (logique d'incremental update)
# ══════════════════════════════════════════════════════════════════════════════

def missing_months(exchange: str, asset: str,
                   start: "datetime.date",
                   end:   "datetime.date") -> list:
    """
    Retourne la liste des (year, month) non encore calcules pour cet exchange/asset.
    """
    from dateutil.relativedelta import relativedelta
    import datetime

    months = []
    current = start.replace(day=1)
    while current <= end.replace(day=1):
        y, m = current.year, current.month
        path = monthly_result_path(exchange, asset, y, m)
        if not path.exists():
            months.append((y, m))
        current += relativedelta(months=1)
    return months


# ══════════════════════════════════════════════════════════════════════════════
# Correction du peg USDT/USD
# quoteQty Binance/Bybit est en USDT, pas USD. Le peg tient à ±0.5% en temps
# normal, mais peut atteindre -3% lors de stress events (LUNA mai 2022,
# FTX nov 2022). On corrige pour ne pas classer de gros trades dans le mauvais
# bucket lors de ces épisodes.
# Source : CoinGecko API publique (pas de clé requise), cache 30 jours.
# ══════════════════════════════════════════════════════════════════════════════

_USDT_CACHE_FILE = CACHE_DIR / "usdt_usd_rates.json"
_USDT_RATES: dict = {}   # {YYYY-MM: float} chargé une seule fois en mémoire

def _load_usdt_cache() -> dict:
    if _USDT_CACHE_FILE.exists():
        try:
            return json.loads(_USDT_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_usdt_cache(rates: dict) -> None:
    _USDT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USDT_CACHE_FILE.write_text(json.dumps(rates))

def get_usdt_usd_rate(year: int, month: int) -> float:
    """
    Retourne le taux USDT/USD moyen du mois (moyenne des taux journaliers).
    Cache local JSON pour éviter les appels répétés à CoinGecko.
    Retourne 1.0 en cas d'échec (peg imparfait vaut mieux que plantage).
    """
    global _USDT_RATES
    if not _USDT_RATES:
        _USDT_RATES = _load_usdt_cache()

    key = f"{year}-{month:02d}"
    if key in _USDT_RATES:
        return _USDT_RATES[key]

    # Ne pas appeler l'API pour le mois en cours (données incomplètes)
    today = datetime.date.today()
    if (year, month) >= (today.year, today.month):
        return 1.0

    try:
        import urllib.request
        import calendar as _cal
        _, last_day = _cal.monthrange(year, month)
        date_from = f"{year:04d}-{month:02d}-01"
        date_to   = f"{year:04d}-{month:02d}-{last_day:02d}"
        # CoinGecko market_chart/range : timestamp Unix en ms
        ts_from = int(datetime.datetime(year, month, 1).timestamp())
        ts_to   = int(datetime.datetime(year, month, last_day, 23, 59).timestamp())
        url = (
            f"https://api.coingecko.com/api/v3/coins/tether/market_chart/range"
            f"?vs_currency=usd&from={ts_from}&to={ts_to}"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        prices = [p[1] for p in data.get("prices", [])]
        rate = float(np.mean(prices)) if prices else 1.0
    except Exception:
        rate = 1.0

    _USDT_RATES[key] = rate
    _save_usdt_cache(_USDT_RATES)
    return rate

def apply_usdt_correction(usd_values: pd.Series, year: int, month: int) -> pd.Series:
    """
    Corrige les valeurs USDT → USD pour un mois donné.
    Si le taux est entre 0.98 et 1.02, la correction est négligeable (<2%)
    et on la signale juste ; en dehors de cette plage on corrige réellement.
    """
    rate = get_usdt_usd_rate(year, month)
    if abs(rate - 1.0) < 0.001:   # < 0.1% : on skip le calcul
        return usd_values
    return usd_values * rate


def missing_days(exchange: str, asset: str,
                 year: int, month: int) -> list:
    """
    Retourne les dates manquantes dans le cache pour ce mois.
    """
    import datetime, calendar
    _, n_days = calendar.monthrange(year, month)
    missing = []
    for d in range(1, n_days + 1):
        date = datetime.date(year, month, d)
        if not daily_cache_path(exchange, asset, date).exists():
            missing.append(date)
    return missing
