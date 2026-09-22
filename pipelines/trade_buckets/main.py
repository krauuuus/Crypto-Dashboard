"""
main.py — Point d'entree du pipeline Bucket Analysis

Usage :
  python main.py                          # calcule tout (START_DATE -> END_DATE)
  python main.py --update                 # seulement les mois manquants
  python main.py --from 2023-01           # recalcule depuis ce mois
  python main.py --asset BTC              # un seul asset (defaut BTC)
  python main.py --no-cftc               # skip CFTC COT
  python main.py --exchanges binance okx  # exchanges specifiques

Le script est idempotent : relancer sans arguments n'effectue que le travail
qui n'a pas encore ete fait (grace aux caches parquet).
"""

import argparse
import asyncio
import datetime
import logging
import sys
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from .config import (ASSETS, EXCHANGE_CONFIG, MONTHLY_DIR,
                     OUTPUT_DIR, START_DATE, END_DATE, BUCKET_NAMES, BUCKET_LABELS)
from .utils import monthly_result_path, aggregated_result_path, missing_months
from .bulk_downloader import run_bulk_exchanges
from .api_fetcher import run_api_exchanges
from .cftc import build_cftc_series

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def parse_date(s: str) -> datetime.date:
    return datetime.datetime.strptime(s, "%Y-%m").replace(day=1).date()


def months_in_range(start: datetime.date,
                    end: datetime.date) -> list[tuple[int, int]]:
    months = []
    cur = start.replace(day=1)
    while cur <= end.replace(day=1):
        months.append((cur.year, cur.month))
        cur += relativedelta(months=1)
    return months


def detect_last_computed(asset: str) -> datetime.date:
    """
    Detecte le dernier mois calcule pour n'importe quel exchange.
    Sert au mode --update pour ne retravailler que les mois nouveaux.
    """
    latest = datetime.date(2019, 1, 1)
    for exch in EXCHANGE_CONFIG:
        for f in (MONTHLY_DIR / exch / asset).glob("*.parquet"):
            try:
                y, m = map(int, f.stem.split("_"))
                d = datetime.date(y, m, 1)
                if d > latest:
                    latest = d
            except ValueError:
                pass
    return latest


# ══════════════════════════════════════════════════════════════════════════════
# Agregation cross-exchange (ponderation par volume total)
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_cross_exchange(asset: str, year: int, month: int) -> pd.DataFrame:
    """
    Agrege les stats de tous les exchanges pour un mois.
    Ponderation : volume de chaque exchange dans le total cross-exchange.
    Retourne un DataFrame avec une ligne par bucket.
    """
    out_path = aggregated_result_path(asset, year, month)
    if out_path.exists():
        return pd.read_parquet(out_path)

    frames = []
    for exch in EXCHANGE_CONFIG:
        p = monthly_result_path(exch, asset, year, month)
        if p.exists():
            frames.append(pd.read_parquet(p))

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    total_cross = df["total_volume_usd"].sum()

    # Ponderer chaque exchange par son poids volumique
    df["weight"] = df["total_volume_usd"] / total_cross

    agg = (
        df.groupby("bucket")
          .apply(lambda g: pd.Series({
              "volume_usd":       g["volume_usd"].sum(),
              "volume_share_pct": (g["volume_usd"].sum() / total_cross * 100),
              "trade_count":      g["trade_count"].sum(),
          }), include_groups=False)
          .reset_index()
    )
    agg["year"]  = year
    agg["month"] = month
    agg["asset"] = asset
    agg["exchange"] = "cross_exchange"

    total_count = agg["trade_count"].sum()
    agg["count_share_pct"] = agg["trade_count"] / total_count * 100

    from .utils import atomic_write_parquet
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(agg, out_path)
    return agg


# ══════════════════════════════════════════════════════════════════════════════
# Export CSV final
# ══════════════════════════════════════════════════════════════════════════════

def export_final_csv(asset: str) -> Path:
    """
    Consolide tous les fichiers mensuels en un seul CSV par exchange
    et un CSV agrege cross-exchange. Pret pour R.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Per-exchange ---
    frames = []
    for exch in list(EXCHANGE_CONFIG.keys()) + ["cross_exchange"]:
        base = (MONTHLY_DIR / exch / asset
                if exch != "cross_exchange"
                else MONTHLY_DIR / "aggregated" / asset)
        for f in sorted(base.glob("*.parquet")):
            frames.append(pd.read_parquet(f))

    if frames:
        df_all = pd.concat(frames, ignore_index=True)
        df_all["year_month"] = (df_all["year"].astype(str) + "-"
                                 + df_all["month"].astype(str).str.zfill(2))
        df_all["bucket_label"] = df_all["bucket"].map(BUCKET_LABELS)

        out = OUTPUT_DIR / f"bucket_analysis_{asset}.csv"
        df_all.to_csv(out, index=False)
        log.info(f"Export CSV : {out}")

    # --- CFTC ---
    cftc_dir = MONTHLY_DIR / "cftc"
    cftc_frames = [pd.read_parquet(f) for f in sorted(cftc_dir.glob("*.parquet"))
                   if not f.name.startswith("_year_")]
    if cftc_frames:
        df_cftc = pd.concat(cftc_frames, ignore_index=True)
        out_cftc = OUTPUT_DIR / "cftc_cot.csv"
        df_cftc.to_csv(out_cftc, index=False)
        log.info(f"Export CFTC : {out_cftc}")

    return OUTPUT_DIR


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline principal
# ══════════════════════════════════════════════════════════════════════════════

async def run(args: argparse.Namespace) -> None:
    asset = args.asset

    # ── Plage de dates ────────────────────────────────────────────────────────
    if args.update:
        last = detect_last_computed(asset)
        start = (last + relativedelta(months=1)).replace(day=1)
        log.info(f"Mode --update : dernier mois calcule = {last:%Y-%m} "
                 f"=> on part de {start:%Y-%m}")
    elif args.from_month:
        start = parse_date(args.from_month)
    else:
        start = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()

    if args.to_month:
        end = parse_date(args.to_month)
    elif END_DATE is None:
        end = datetime.date.today().replace(day=1) - relativedelta(months=1)
    else:
        end = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()

    all_months = months_in_range(start, end)
    if not all_months:
        log.info("Aucun mois a traiter. Tout est deja calcule.")
    else:
        log.info(f"Mois a traiter : {len(all_months)} "
                 f"({all_months[0][0]}-{all_months[0][1]:02d} → "
                 f"{all_months[-1][0]}-{all_months[-1][1]:02d})")

    # ── Exchanges selectionnes ────────────────────────────────────────────────
    if args.exchanges:
        selected = [e for e in args.exchanges if e in EXCHANGE_CONFIG]
    else:
        selected = list(EXCHANGE_CONFIG.keys())

    bulk_exchanges = [e for e in selected
                      if EXCHANGE_CONFIG[e]["method"] in ("bulk", "bulk_daily")]
    api_exchanges  = [e for e in selected
                      if EXCHANGE_CONFIG[e]["method"] == "api"]

    # ── Filtrer les mois vraiment manquants par exchange ──────────────────────
    # sorted() force l'ordre chronologique (le set Python ordonne par hash,
    # ce qui place les mois trimestriels {3,6,9,12} en premier par coïncidence)
    bulk_months = sorted({(y, m) for e in bulk_exchanges
                          for (y, m) in all_months
                          if not monthly_result_path(e, asset, y, m).exists()})
    api_months  = sorted({(y, m) for e in api_exchanges
                          for (y, m) in all_months
                          if not monthly_result_path(e, asset, y, m).exists()})

    # ── Telechargement bulk (Binance + Bybit) ─────────────────────────────────
    if bulk_months and bulk_exchanges:
        log.info(f"Bulk exchanges : {bulk_exchanges} | {len(bulk_months)} mois")
        await run_bulk_exchanges(asset, bulk_months,
                                  max_concurrent=args.max_concurrent)

    # ── Telechargement API (OKX, Kraken, Coinbase) ────────────────────────────
    if api_months and api_exchanges:
        log.info(f"API exchanges : {api_exchanges} | {len(api_months)} mois")
        await run_api_exchanges(api_exchanges, asset, api_months,
                                 max_concurrent=2)

    # ── Agregation cross-exchange ─────────────────────────────────────────────
    log.info("Agregation cross-exchange...")
    for y, m in tqdm(all_months, desc="Agregation", unit="mois"):
        aggregate_cross_exchange(asset, y, m)

    # ── CFTC COT ──────────────────────────────────────────────────────────────
    if not args.no_cftc:
        log.info("Telechargement CFTC COT...")
        start_year = start.year
        end_year   = end.year
        build_cftc_series(start_year, end_year, asset_keyword="BITCOIN")

    # ── Export CSV final ──────────────────────────────────────────────────────
    out_dir = export_final_csv(asset)
    log.info(f"Termine. Resultats dans : {out_dir}")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline d'analyse des buckets de taille de transactions crypto"
    )
    parser.add_argument("--update", action="store_true",
                        help="Ajoute uniquement les mois manquants depuis le dernier run")
    parser.add_argument("--from", dest="from_month", metavar="YYYY-MM",
                        help="Recalcule depuis ce mois (ex: 2018-01)")
    parser.add_argument("--to", dest="to_month", metavar="YYYY-MM",
                        help="S'arrete a ce mois inclus (ex: 2018-12)")
    parser.add_argument("--asset", default="BTC",
                        choices=list(ASSETS.keys()),
                        help="Asset a analyser (defaut: BTC)")
    parser.add_argument("--exchanges", nargs="+",
                        choices=list(EXCHANGE_CONFIG.keys()),
                        help="Exchanges specifiques (defaut: tous)")
    parser.add_argument("--no-cftc", action="store_true",
                        help="Ne pas telecharger les donnees CFTC COT")
    parser.add_argument("--max-concurrent", type=int, default=3,
                        help="Nombre max de telechargements paralleles (defaut: 3)")
    parser.add_argument("--verbose", action="store_true",
                        help="Logs detailles (DEBUG)")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
