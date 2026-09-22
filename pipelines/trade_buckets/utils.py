"""
utils.py — Helpers partagés : cache, atomic write, bucket assignment
"""
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
