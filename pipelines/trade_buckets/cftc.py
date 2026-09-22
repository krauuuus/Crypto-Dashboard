"""
cftc.py
Telecharge et traite les donnees CFTC Commitment of Traders (COT)
pour Bitcoin et Ether (CME futures).

Source : CFTC Disaggregated Futures COT (fichier annuel ZIP)
URL    : https://www.cftc.gov/sites/default/files/files/dea/cotarchives/{year}/futures/deacmesf.zip

Colonnes cles (Disaggregated COT) :
  - Asset_Mgr_Positions_Long_All / Short_All : investisseurs institutionnels (fonds)
  - Lev_Money_Positions_Long_All / Short_All  : hedge funds
  - NonRept_Positions_Long_All / Short_All    : non-reportables = retail
  - Dealer_Positions_Long_All / Short_All     : dealers / market makers
  - Open_Interest_All                          : interet ouvert total

Metriques calculees :
  - institutional_share_pct = (AM_Long + AM_Short) / (2 * OI) * 100
  - retail_share_pct        = (NR_Long + NR_Short) / (2 * OI) * 100
  - hf_share_pct            = (LM_Long + LM_Short) / (2 * OI) * 100
  - institutional_net_long  = (AM_Long - AM_Short) / OI * 100
"""

import io
import logging
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

from .config import CFTC_URL_TEMPLATE, CFTC_ASSETS, MONTHLY_DIR
from .utils import atomic_write_parquet, cftc_path

log = logging.getLogger(__name__)

# Colonnes a lire (reduit la memoire)
_COLS = [
    "Market_and_Exchange_Names", "Report_Date_as_MM_DD_YYYY",
    "Open_Interest_All",
    "Dealer_Positions_Long_All", "Dealer_Positions_Short_All",
    "Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All",
    "Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All",
    "NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
]

_COL_RENAME = {
    "Market_and_Exchange_Names":    "asset",
    "Report_Date_as_MM_DD_YYYY":    "report_date",
    "Open_Interest_All":            "open_interest",
    "Dealer_Positions_Long_All":    "dealer_long",
    "Dealer_Positions_Short_All":   "dealer_short",
    "Asset_Mgr_Positions_Long_All": "am_long",
    "Asset_Mgr_Positions_Short_All":"am_short",
    "Lev_Money_Positions_Long_All": "hf_long",
    "Lev_Money_Positions_Short_All":"hf_short",
    "NonRept_Positions_Long_All":   "retail_long",
    "NonRept_Positions_Short_All":  "retail_short",
}


def download_cftc_year(year: int) -> pd.DataFrame:
    """
    Telecharge le fichier annuel CFTC et retourne un DataFrame brut
    pour Bitcoin et Ether uniquement.
    """
    url = CFTC_URL_TEMPLATE.format(year=year)
    log.info(f"[CFTC] Telechargement {year} : {url}")

    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        log.warning(f"[CFTC] Annee {year} non disponible.")
        return pd.DataFrame()
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as f:
            df = pd.read_csv(
                f,
                usecols=_COLS,
                dtype=str,        # lire tout en str d'abord (format CFTC variable)
                low_memory=False,
            )

    # Filtrer Bitcoin / Ether
    mask = df["Market_and_Exchange_Names"].str.contains(
        "|".join(CFTC_ASSETS), case=False, na=False
    )
    df = df[mask].copy()
    if df.empty:
        log.warning(f"[CFTC] Aucun asset crypto trouve en {year}.")
        return pd.DataFrame()

    df = df.rename(columns=_COL_RENAME)
    df["report_date"] = pd.to_datetime(df["report_date"], format="%m/%d/%Y",
                                        errors="coerce")
    num_cols = [c for c in df.columns if c not in ("asset", "report_date")]
    df[num_cols] = df[num_cols].apply(pd.to_numeric, errors="coerce")
    return df


def compute_cftc_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les metriques de positionnement a partir des colonnes brutes.
    """
    oi = df["open_interest"].replace(0, float("nan"))

    df = df.copy()
    df["institutional_share_pct"] = (df["am_long"] + df["am_short"]) / (2 * oi) * 100
    df["retail_share_pct"]        = (df["retail_long"] + df["retail_short"]) / (2 * oi) * 100
    df["hf_share_pct"]            = (df["hf_long"] + df["hf_short"]) / (2 * oi) * 100
    df["dealer_share_pct"]        = (df["dealer_long"] + df["dealer_short"]) / (2 * oi) * 100

    # Net long institutionnel (signal directionnel)
    df["institutional_net_long_pct"] = (df["am_long"] - df["am_short"]) / oi * 100
    df["hf_net_long_pct"]            = (df["hf_long"] - df["hf_short"]) / oi * 100
    df["retail_net_long_pct"]        = (df["retail_long"] - df["retail_short"]) / oi * 100
    return df


def get_cftc_monthly(year: int, month: int,
                      asset_keyword: str = "BITCOIN") -> pd.DataFrame:
    """
    Retourne les stats CFTC mensuelles (derniere semaine du mois).
    Utilise le cache si disponible.
    """
    cache = cftc_path(year, month)
    if cache.exists():
        df = pd.read_parquet(cache)
        # Filtrer l'asset
        return df[df["asset"].str.contains(asset_keyword, case=False, na=False)]

    # Telecharger l'annee entiere si pas encore en cache
    df_year = download_cftc_year(year)
    if df_year.empty:
        return pd.DataFrame()

    df_year = compute_cftc_metrics(df_year)
    df_year["year"]  = df_year["report_date"].dt.year
    df_year["month"] = df_year["report_date"].dt.month

    # Sauvegarder par mois (un fichier par mois = granularite fine)
    for (y, m), grp in df_year.groupby(["year", "month"]):
        p = cftc_path(int(y), int(m))
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(grp.reset_index(drop=True), p)

    return df_year[
        (df_year["year"] == year) &
        (df_year["month"] == month) &
        df_year["asset"].str.contains(asset_keyword, case=False, na=False)
    ]


def build_cftc_series(start_year: int, end_year: int,
                       asset_keyword: str = "BITCOIN") -> pd.DataFrame:
    """
    Construit une serie temporelle CFTC complete pour un asset.
    Telecharge les annees manquantes uniquement.
    """
    frames = []
    for year in range(start_year, end_year + 1):
        year_path = cftc_path(year, 1).parent.parent / f"_year_{year}.parquet"
        if year_path.exists():
            df_y = pd.read_parquet(year_path)
        else:
            df_y = download_cftc_year(year)
            if df_y.empty:
                continue
            df_y = compute_cftc_metrics(df_y)
            df_y["year"]  = df_y["report_date"].dt.year
            df_y["month"] = df_y["report_date"].dt.month
            year_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(df_y, year_path)

        frames.append(
            df_y[df_y["asset"].str.contains(asset_keyword, case=False, na=False)]
        )

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True).sort_values("report_date")

    # Agregation mensuelle : garder la derniere observation du mois
    df["year_month"] = df["report_date"].dt.to_period("M")
    monthly = (
        df.sort_values("report_date")
          .groupby(["year_month", "asset"])
          .last()
          .reset_index()
    )
    return monthly
