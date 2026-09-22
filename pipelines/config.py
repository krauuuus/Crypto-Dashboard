"""
config.py — Parametres centraux et chargement des cles API
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

# Basket crypto commun a tous les projets (CryptoStability + CBDCStablecoins)
CRYPTO_BASKET = ["BTC", "ETH", "XRP", "BNB", "ADA", "TRX", "DOGE"]

START_DATE = "2017-01-01"

# Repo de donnees separe (clone local de krauuuus/Crypto-Dashboard-Data)
# Modifiable via variable d'environnement DATA_REPO si clone dans un autre dossier
_default_data_repo = ROOT.parent / "crypto-research-data"
DATA_REPO = Path(os.getenv("DATA_REPO", str(_default_data_repo)))
