"""
config.py — Configuration centrale du pipeline Bucket Analysis
Adapté pour DASHBOARD CRYPTO (chemins vers data/raw et data/cache du projet)
"""
import datetime
from pathlib import Path

# ── Chemins projet ─────────────────────────────────────────────────────────────
# trade_buckets/ -> pipelines/ -> DASHBOARD CRYPTO/
ROOT        = Path(__file__).parent.parent.parent
OUTPUT_DIR  = ROOT / "data" / "raw"   / "buckets"
MONTHLY_DIR = ROOT / "data" / "raw"   / "buckets" / "monthly"
CACHE_DIR   = ROOT / "data" / "cache" / "buckets"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Periode ───────────────────────────────────────────────────────────────────
START_DATE = "2018-01-01"
END_DATE   = datetime.date.today().strftime("%Y-%m-%d")  # dynamique

# ── Assets analyses ───────────────────────────────────────────────────────────
# BTC + ETH + XRP + BNB sont les actifs principaux pour le bucket analysis.
# LTC conservé pour comparaison historique.
# BNB/ADA/TRX/DOGE : aligne avec le basket CryptoStability.
ASSETS = {
    "BTC": {
        "binance":  "BTCUSDT",
        "bybit":    "BTCUSDT",
        "okx":      "BTC-USDT",
        "kraken":   "XBTUSDT",
        "coinbase": "BTC-USDT",
    },
    "ETH": {
        "binance":  "ETHUSDT",
        "bybit":    "ETHUSDT",
        "okx":      "ETH-USDT",
        "kraken":   "ETHUSDT",
        "coinbase": "ETH-USDT",
    },
    "XRP": {
        "binance":  "XRPUSDT",
        "bybit":    "XRPUSDT",
        "okx":      "XRP-USDT",
        "kraken":   "XRPUSDT",
        "coinbase": "XRP-USDT",
    },
    "BNB": {
        "binance":  "BNBUSDT",
        "bybit":    "BNBUSDT",
        "okx":      "BNB-USDT",
        # Kraken/Coinbase ne listent pas BNB -> None = skip silencieusement
        "kraken":   None,
        "coinbase": None,
    },
    "LTC": {
        "binance":  "LTCUSDT",
        "bybit":    "LTCUSDT",
        "okx":      "LTC-USDT",
        "kraken":   "LTCUSDT",
        "coinbase": "LTC-USDT",
    },
}

# ── Exchanges — methode de telechargement ─────────────────────────────────────
# "bulk"       = ZIP mensuel Binance  (200-500 MB compressé)
# "bulk_daily" = CSV.gz journalier Bybit
# "api"        = ccxt async (paginé jour par jour)
EXCHANGE_CONFIG = {
    "binance": {
        "method":   "bulk",
        "bulk_url": (
            "https://data.binance.vision/data/spot/monthly/aggTrades"
            "/{pair}/{pair}-aggTrades-{year}-{month:02d}.zip"
        ),
        "note": "Retail + institutionnel international",
    },
    "bybit": {
        "method":   "bulk_daily",
        "bulk_url": (
            "https://public.bybit.com/trading"
            "/{pair}/{pair}{year}-{month:02d}-{day:02d}.csv.gz"
        ),
        "note": "Trading professionnel / dérivés",
    },
    "okx": {
        "method":  "api",
        "ccxt_id": "okx",
        "note":    "Institutionnel asiatique",
    },
    "kraken": {
        "method":  "api",
        "ccxt_id": "kraken",
        "note":    "Institutionnel européen/US",
    },
    "coinbase": {
        "method":  "api",
        "ccxt_id": "coinbase",
        "note":    "Institutionnel US (Coinbase Prime)",
    },
}

# ── Buckets de taille en USD (définition principale) ──────────────────────────
# Inspiré de Baur & Dimpfl (2019), Cong, Tang & Wang (2023)
BUCKETS = [
    ("B1_micro_retail",  0,          1_000),
    ("B2_retail",        1_000,      10_000),
    ("B3_semi_inst",     10_000,     100_000),
    ("B4_institutional", 100_000,    1_000_000),
    ("B5_whale",         1_000_000,  float("inf")),
]
BUCKET_NAMES = [b[0] for b in BUCKETS]
BUCKET_LABELS = {
    "B1_micro_retail":  "< $1K",
    "B2_retail":        "$1K–$10K",
    "B3_semi_inst":     "$10K–$100K",
    "B4_institutional": "$100K–$1M",
    "B5_whale":         "> $1M",
}

# ── Robustesse : variantes des limites de buckets ─────────────────────────────
# Pour vérifier que les résultats ne dépendent pas du choix exact des seuils.
ROBUSTNESS_SPECS = {
    "baseline": [0, 1_000, 10_000, 100_000, 1_000_000],       # seuils originaux
    "tight":    [0, 500,   5_000,  50_000,  500_000],          # seuils resserrés
    "loose":    [0, 2_000, 20_000, 200_000, 2_000_000],        # seuils élargis
    "log5":     [0, 3_162, 31_623, 316_228, 3_162_278],        # log-uniformes
}

# ── Paramètres API (exchanges en mode "api") ──────────────────────────────────
RATE_LIMIT_SLEEP_S  = 0.2
MAX_TRADES_PER_CALL = 1_000
MAX_CONCURRENT_EXCH = 3

# ── CFTC COT ─────────────────────────────────────────────────────────────────
CFTC_URL_TEMPLATE = (
    "https://www.cftc.gov/sites/default/files/files/dea/cotarchives"
    "/{year}/futures/deacmesf.zip"
)
CFTC_ASSETS = ["BITCOIN", "ETHER"]

# ── Chunk size pour lecture CSV en mémoire maîtrisée ─────────────────────────
CSV_CHUNK_ROWS = 1_000_000
