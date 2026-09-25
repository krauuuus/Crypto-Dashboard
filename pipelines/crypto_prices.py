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

from .config import ROOT, CRYPTOCOMPARE_API_KEY, COINMARKETCAP_API_KEY, CRYPTO_BASKET, START_DATE

# Mapping symbole → ticker Yahoo Finance (fallback si CryptoCompare rate limit)
_YF_TICKERS = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "XRP": "XRP-USD",
    "BNB": "BNB-USD", "ADA": "ADA-USD", "TRX": "TRX-USD", "DOGE": "DOGE-USD",
}

log = logging.getLogger(__name__)

CACHE_FILE  = ROOT / "data" / "cache" / "crypto_prices.parquet"
_CACHE_META = ROOT / "data" / "cache" / "crypto_prices_meta.json"
CACHE_TTL_H = 24

# Cache broad (top-N stability)
_BROAD_FILE       = ROOT / "data" / "cache" / "crypto_prices_broad.parquet"
_BROAD_META       = ROOT / "data" / "cache" / "crypto_prices_broad_meta.json"
_SYMBOLS_CACHE    = ROOT / "data" / "cache" / "stability_symbols.json"
_SYMBOLS_TTL_H    = 7 * 24   # liste stable → refresh hebdo

_STABLECOIN_TAGS  = {"stablecoin"}
_STABLECOIN_SYMS  = {
    "USDT","USDC","BUSD","DAI","TUSD","USDP","GUSD","FRAX","LUSD",
    "UST","USDN","FDUSD","PYUSD","CRVUSD","USDE","SUSD","USDJ","HUSD",
}

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


def _fetch_symbol_yf(symbol: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
    """Fallback yfinance quand CryptoCompare est rate-limité."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance non installé (pip install yfinance)")
        return pd.DataFrame()
    try:
        ticker = _YF_TICKERS.get(symbol, f"{symbol}-USD")
        raw = yf.download(ticker, start=str(start), end=str(end + datetime.timedelta(days=1)),
                          auto_adjust=True, progress=False)
        if raw.empty:
            return pd.DataFrame()
        raw = raw.reset_index()
        # yfinance renvoie des colonnes MultiIndex si un seul ticker — on aplatit
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0].lower() if c[1] == "" else c[0].lower() for c in raw.columns]
        else:
            raw.columns = [c.lower() for c in raw.columns]
        raw = raw.rename(columns={"date": "date", "volume": "volume_usd"})
        raw["date"] = pd.to_datetime(raw["date"]).dt.date
        raw["volume_crypto"] = raw["volume_usd"]  # yfinance = volume en unités native
        raw["symbol"] = symbol
        cols = ["date", "open", "high", "low", "close", "volume_crypto", "volume_usd", "symbol"]
        return raw[[c for c in cols if c in raw.columns]].sort_values("date").reset_index(drop=True)
    except Exception as e:
        log.warning(f"[yfinance] {symbol} erreur: {e}")
        return pd.DataFrame()


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
            log.warning(f"  ✗ {symbol} : CryptoCompare vide, tentative yfinance...")
            df_yf = _fetch_symbol_yf(symbol, start, end)
            if not df_yf.empty:
                frames.append(df_yf)
                log.info(f"  ✓ {symbol} : yfinance OK ({len(df_yf)} jours)")
            else:
                log.warning(f"  ✗ {symbol} : aucune donnée (CC + yfinance)")

    # Complète les symboles manquants depuis yfinance
    fetched = {df["symbol"].iloc[0] for df in frames} if frames else set()
    missing = [s for s in CRYPTO_BASKET if s not in fetched]
    if missing:
        log.info(f"  yfinance fallback pour : {missing}")
        for symbol in missing:
            df_yf = _fetch_symbol_yf(symbol, start, end)
            if not df_yf.empty:
                frames.append(df_yf)
                log.info(f"  ✓ {symbol} : yfinance OK ({len(df_yf)} jours)")

    if not frames:
        raise RuntimeError("Aucune donnée récupérée — vérifier les clés API")

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["symbol", "date"]).reset_index(drop=True)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(CACHE_FILE, index=False, compression="snappy")
    _save_meta()

    # Export vers le data repo
    try:
        from .data_repo import export as repo_export
        repo_export(
            result, "crypto_prices", "crypto_prices.parquet",
            commit_msg=f"data: update crypto_prices ({datetime.date.today()})",
        )
    except Exception as e:
        log.warning(f"data_repo export skipped: {e}")

    log.info(f"crypto_prices : {len(result)} lignes -> {CACHE_FILE}")
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


# ══════════════════════════════════════════════════════════════════════════════
# Top-N symboles pour la stability (hors stablecoins)
# ══════════════════════════════════════════════════════════════════════════════

def _symbols_from_list_xlsx() -> list[str]:
    """
    Lit list.xlsx (Feuil1) + cryptocategories.xlsx (stablecoins_list) depuis le
    dossier recherche local.  Retourne le top-40 par année, union unique, sans stablecoins.
    Retourne [] si les fichiers ne sont pas trouvés.
    """
    import os
    list_path   = Path(r"C:\Users\fkraus\Desktop\Recherche\CryptoStability\data\raw\list.xlsx")
    stable_path = Path(r"C:\Users\fkraus\Desktop\Recherche\CryptoStability\data\raw\cryptocategories.xlsx")
    if not list_path.exists() or not stable_path.exists():
        return []
    try:
        st = pd.read_excel(stable_path, sheet_name="stablecoins_list")
        stablecoins = set(st["ID"].dropna().astype(str).str.strip().str.upper())
        feuil1 = pd.read_excel(list_path, sheet_name="Feuil1", header=0)
        unique: list[str] = []
        seen: set[str] = set()
        for col in feuil1.columns:
            syms = [str(s).strip().upper() for s in feuil1[col].dropna().tolist()]
            filtered = [s for s in syms
                        if s not in stablecoins and s.isascii() and s.isalpha()][:40]
            for s in filtered:
                if s not in seen:
                    seen.add(s)
                    unique.append(s)
        log.info(f"get_stability_symbols : {len(unique)} symboles depuis list.xlsx")
        return unique
    except Exception as e:
        log.warning(f"_symbols_from_list_xlsx : échec ({e})")
        return []


def get_stability_symbols(n: int = 100) -> list[str]:
    """
    Retourne la liste de cryptos pour la pipeline de stabilité.
    Priorité : (1) list.xlsx local, (2) CoinMarketCap, (3) CRYPTO_BASKET.
    Cache JSON hebdomadaire.
    """
    if _SYMBOLS_CACHE.exists():
        meta = json.loads(_SYMBOLS_CACHE.read_text())
        age_h = (datetime.datetime.now() -
                 datetime.datetime.fromisoformat(meta["fetched_at"])
                 ).total_seconds() / 3600
        if age_h < _SYMBOLS_TTL_H and len(meta.get("symbols", [])) >= n:
            return meta["symbols"][:n]

    # Priorité 1 : list.xlsx local (recherche)
    xlsx_syms = _symbols_from_list_xlsx()
    if xlsx_syms:
        _SYMBOLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SYMBOLS_CACHE.write_text(json.dumps({
            "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "symbols": xlsx_syms,
        }))
        return xlsx_syms[:n] if n < len(xlsx_syms) else xlsx_syms

    if not COINMARKETCAP_API_KEY:
        log.warning("get_stability_symbols : COINMARKETCAP_API_KEY manquante, fallback CRYPTO_BASKET")
        return list(CRYPTO_BASKET)

    url = ("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
           "?limit=250&convert=USD")
    try:
        req = urllib.request.Request(url, headers={"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.warning(f"get_stability_symbols : erreur CMC ({e})")
        return list(CRYPTO_BASKET)

    symbols = []
    for item in data.get("data", []):
        sym  = item.get("symbol", "")
        tags = set(item.get("tags", []))
        if _STABLECOIN_TAGS & tags or sym in _STABLECOIN_SYMS:
            continue
        # Ignorer les symboles non-ASCII (junk tokens)
        if not sym.isascii():
            continue
        symbols.append(sym)
        if len(symbols) >= n:
            break

    if not symbols:
        return list(CRYPTO_BASKET)

    _SYMBOLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _SYMBOLS_CACHE.write_text(json.dumps({
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbols": symbols,
    }))
    log.info(f"get_stability_symbols : {len(symbols)} symboles (ex: {symbols[:5]})")
    return symbols


def _broad_cache_covers(symbols: list[str]) -> bool:
    """Vérifie que le cache broad est frais ET contient tous les symboles demandés."""
    if not _BROAD_FILE.exists() or not _BROAD_META.exists():
        return False
    meta = json.loads(_BROAD_META.read_text())
    age_h = (datetime.datetime.now() -
             datetime.datetime.fromisoformat(meta["fetched_at"])
             ).total_seconds() / 3600
    if age_h >= CACHE_TTL_H:
        return False
    cached_syms = set(meta.get("symbols", []))
    return set(symbols).issubset(cached_syms)


def load_prices_broad(symbols: list[str], force_refresh: bool = False) -> pd.DataFrame:
    """
    OHLCV daily pour une liste arbitraire de symboles.
    Cache séparé (crypto_prices_broad.parquet).
    Fetch incrémental : nouveaux symboles depuis START_DATE,
    symboles existants depuis leur dernière date en cache.
    """
    start_full = datetime.date.fromisoformat(START_DATE)
    end        = datetime.date.today()

    existing = pd.DataFrame()
    if _BROAD_FILE.exists() and not force_refresh:
        existing = pd.read_parquet(_BROAD_FILE)

    if not existing.empty:
        existing["date"] = pd.to_datetime(existing["date"]).dt.date

    existing_syms = set(existing["symbol"].unique()) if not existing.empty else set()

    # Symboles à jour vs à mettre à jour
    up_to_date = set()
    if not existing.empty:
        latest = existing.groupby("symbol")["date"].max()
        up_to_date = set(latest[latest >= end].index)

    frames = [existing[existing["symbol"].isin(symbols)]] if not existing.empty else []

    for i, symbol in enumerate(symbols):
        if symbol in up_to_date and not force_refresh:
            continue  # déjà à jour

        if symbol in existing_syms and not force_refresh:
            # Incrémental : seulement les données manquantes
            last = existing[existing["symbol"] == symbol]["date"].max()
            fetch_start = last + datetime.timedelta(days=1)
            if fetch_start > end:
                continue
            log.info(f"  broad incrémental → {symbol} depuis {fetch_start}")
        else:
            fetch_start = start_full
            log.info(f"  broad → {symbol} ({i+1}/{len(symbols)})")

        df = _fetch_symbol(symbol, fetch_start, end)
        if not df.empty:
            frames.append(df)
        else:
            df_yf = _fetch_symbol_yf(symbol, fetch_start, end)
            if not df_yf.empty:
                frames.append(df_yf)
                log.info(f"  ✓ {symbol} : yfinance fallback OK")
            else:
                log.warning(f"  ✗ {symbol} : aucune donnée (CC + yfinance)")
        time.sleep(0.15)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["date"] = pd.to_datetime(result["date"]).dt.date
    result = result.drop_duplicates(subset=["symbol", "date"])
    result = result.sort_values(["symbol", "date"]).reset_index(drop=True)

    _BROAD_FILE.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(_BROAD_FILE, index=False, compression="snappy")
    _BROAD_META.write_text(json.dumps({
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbols": list(result["symbol"].unique()),
    }))
    log.info(f"crypto_prices_broad : {result['symbol'].nunique()} symboles, {len(result)} lignes")
    return result[result["symbol"].isin(symbols)]


def load_returns_broad(symbols: list[str],
                       min_coverage: float = 0.5,
                       force_refresh: bool = False) -> pd.DataFrame:
    """
    Log-rendements daily (wide) pour les symboles donnés.
    Filtre : conserve uniquement les symboles avec >= min_coverage de la période totale.
    """
    import numpy as np
    prices = load_prices_broad(symbols, force_refresh)
    if prices.empty:
        return pd.DataFrame()
    wide = prices.pivot(index="date", columns="symbol", values="close")
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    returns = np.log(wide / wide.shift(1))
    returns = returns.replace([np.inf, -np.inf], np.nan)
    coverage = returns.notna().mean()
    valid = coverage[coverage >= min_coverage].index.tolist()
    log.info(f"load_returns_broad : {len(valid)}/{len(symbols)} symboles (couverture >= {min_coverage:.0%})")
    return returns[valid].dropna(how="all")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = load_prices(force_refresh=True)
    print(df.groupby("symbol")[["date", "close"]].last().to_string())
    print(f"\n{len(df)} lignes, {df['symbol'].nunique()} symboles")
