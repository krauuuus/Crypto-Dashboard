"""
config.py — Paramètres centraux et chargement des clés API
"""
from pathlib import Path
from dotenv import load_dotenv
import os

ROOT     = Path(__file__).parent.parent
DATA_RAW = ROOT / "data" / "raw"
CACHE    = ROOT / "data" / "cache"
DATA_RAW.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "")

# Basket crypto commun à tous les projets (CryptoStability + CBDCStablecoins)
CRYPTO_BASKET = ["BTC", "ETH", "XRP", "BNB", "ADA", "TRX", "DOGE"]

START_DATE = "2017-01-01"
