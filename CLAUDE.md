# CLAUDE.md — Crypto Research Dashboard

Fichier de contexte pour les sessions Claude. Lire entierement avant de toucher au code.

---

## Qui est l'utilisateur

PhD en finance, utilisateur R confirme qui apprend Python. Toujours cadrer les
explications Python par rapport a R quand c'est utile (DataFrame ~ data.frame,
pct_change ~ diff/lag, etc.). Il connait bien l'econometrie, les series temporelles,
les modeles de facteurs.

---

## Objectif du projet

Dashboard interactif de recherche crypto en Python (Dash), alimente par des
pipelines de donnees live. Reproduit et etend 3 papiers de recherche :

1. **Bucket Analysis** — distribution des transactions par taille (retail vs institutionnel)
2. **CryptoStability** — DFM + eGARCH + rolling quantile regression → classification FI/FF
3. **CBDC Stablecoins** — sentiment des discours de banques centrales (BIS speeches)

Plus une 4eme source : **IMF WP-CPER** (taux de change paralleles crypto-based).

---

## Repos GitHub

| Repo | Role |
|------|------|
| `krauuuus/Crypto-Dashboard` | Code : pipelines + dashboard app |
| `krauuuus/Crypto-Dashboard-Data` | Donnees : outputs agreges (parquets) |

Clone local des deux repos :
- `C:\Users\fkraus\Desktop\DASHBOARD CRYPTO\` — code
- `C:\Users\fkraus\Desktop\DASHBOARD CRYPTO\crypto-research-data\` — data (repo imbriqué)

**Branches** : `main` = stable et fonctionnel, `dev` = WIP. Ne jamais merger dev → main
tant qu'une section du dashboard n'est pas entierement validee.

---

## Environnement Python

Conda env : `finance-dashboard` (Python 3.12, sans droits admin)
Toujours utiliser **Anaconda Prompt** pour Python/conda, pas le terminal VS Code.
Le terminal VS Code sert uniquement pour git.

Packages installés :
- dash, plotly, pandas, numpy, yfinance
- python-dotenv, pyarrow, tqdm, aiohttp, requests
- python-dateutil
- ccxt (via pip, pas conda)
- scikit-learn, statsmodels (a confirmer)
- arch (via pip — pas sur conda-forge)

Packages a installer si manquants :
```
conda install scikit-learn statsmodels -c conda-forge -y
pip install arch
```

---

## Cles API (.env — JAMAIS committe)

Fichier : `.env` a la racine du projet (jamais committe — voir .gitignore).

Variables requises :
```
COINMARKETCAP_API_KEY=<votre cle>
CRYPTOCOMPARE_API_KEY=<votre cle>
JEV_API_KEY=<votre cle>
```

Artemis.xyz : pas d'API, donnees Excel statiques uniquement.
IMF CPER : API publique, sans cle.
CoinGecko : API publique (utilisee pour correction USDT/USD), sans cle.

---

## Structure des fichiers

```
DASHBOARD CRYPTO/
├── .env                          ← cles API (gitignore)
├── .gitignore
├── CLAUDE.md                     ← ce fichier
├── app.py                        ← dashboard Dash (3 sections : prix, buckets, stabilite)
├── pipelines/
│   ├── __init__.py
│   ├── config.py                 ← ROOT, DATA_RAW, CACHE, DATA_REPO, cles API, CRYPTO_BASKET
│   ├── data_repo.py              ← export/pull/read vers Crypto-Dashboard-Data
│   ├── crypto_prices.py          ← CryptoCompare histoday, cache 24h, export data repo
│   ├── crypto_stability.py       ← PCA → eGARCH → QR rolling → FI/FF, cache 24h, export
│   └── trade_buckets/            ← sous-package bucket analysis
│       ├── __init__.py
│       ├── config.py             ← chemins, ASSETS, BUCKETS, ROBUSTNESS_SPECS, EXCHANGE_CONFIG
│       ├── utils.py              ← atomic write, assign_buckets, USDT correction (CoinGecko)
│       ├── bulk_downloader.py    ← Binance ZIP mensuel + Bybit CSV.gz journalier (async)
│       ├── api_fetcher.py        ← ccxt async OKX/Kraken/Coinbase
│       ├── cftc.py               ← CFTC COT (positions institutionnelles vs retail)
│       └── main.py               ← CLI orchestrateur (idempotent, parquet cache)
├── data/
│   ├── raw/buckets/              ← stats mensuelles agreges (gitignore)
│   └── cache/                    ← caches intermediaires (gitignore)
└── notebooks/                    ← (gitignore — jamais committe)
```

---

## Pipelines — etat

### trade_buckets (bucket analysis)

**Statut** : pipeline en production, premier run en cours.

Lancement :
```bash
# Depuis C:\Users\fkraus\Desktop\DASHBOARD CRYPTO\
python -m pipelines.trade_buckets.main --asset BTC --exchanges binance
```

- Binance : ZIP mensuel aggTrades (~300-500 MB/mois), traitement ~25 min/mois
- Bybit : CSV.gz journalier, beaucoup plus rapide
- OKX / Kraken / Coinbase : via ccxt async (lent, pagine jour par jour)
- Idempotent : reprend ou il s'est arrete si coupe
- Assets : BTC, ETH, XRP, BNB, LTC
- Buckets : B1 <$1K, B2 $1K-$10K, B3 $10K-$100K, B4 $100K-$1M, B5 >$1M
- Robustesse : 4 specs de seuils (baseline/tight/loose/log5) dans config.py
- Correction USDT/USD : CoinGecko (cache JSON), appliquee dans compute_bucket_stats()
- Export data repo : TODO (a ajouter dans main.py apres validation)

Run complet depuis 2018 : ~14h pour Binance BTC seul.

### crypto_prices

**Statut** : code ecrit, non encore teste.

```bash
python -m pipelines.crypto_prices
```

- Source : CryptoCompare histoday, pagine par tranches de 2000 jours
- Cache local : `data/cache/crypto_prices.parquet`, TTL 24h
- Export data repo : oui (apres chaque re-fetch)
- Output : DataFrame wide (date, symbol, open, high, low, close, volume_crypto, volume_usd)
- `load_returns()` : log-rendements vectorises (np.log), format wide, index=date

### crypto_stability

**Statut** : code ecrit, non encore teste. Necessite scikit-learn + arch + statsmodels.

```bash
python -m pipelines.crypto_stability
```

- Etape 1 : PCA(r=1) sur les log-rendements standardises → facteur latent
- Etape 2 : eGARCH(1,1) sur le facteur → residus standardises = choc
- Etape 3 : rolling QR (fenetre 18 mois, tau=[0.05,0.50,0.95]) par asset vs choc
- Etape 4 : classification FI/FF (beta_05<0 ET beta_95>0 → FI, inverse → FF)
- Cache local : TTL 24h (evite la revision DFM a chaque ouverture de l'app)
- Export data repo : oui (factor_shock.parquet, qr_rolling.parquet, fi_ff.parquet)

**Probleme de revision DFM** : la PCA sur l'echantillon complet change quand on
ajoute des donnees, ce qui revise retrospectivement les estimations passees.
Solution : cache 24h — on n'estime qu'une fois par jour au maximum.

### imf_cper

**Statut** : a ecrire. API IMF publique (SDMX JSON), sans cle.

Dataset : taux de change paralleles crypto-based (Bitcoin local vs US market).
Papier : Graf von Luckner, Koepke & Sgherri (2024), IMF WP 2024/133.
URL : https://data.imf.org/en/datasets/IMF.STA:WPCPER
Frequence : mensuelle, ~50 pays.

### cbdc_speeches

**Statut** : a ecrire. Ne pas ajouter au dashboard avant d'etre complet.

Sources :
- BIS speeches : package garrido ou scraping HTTP direct
- Filtre CBDC : hard keywords (exact) + soft keywords (depuis cbdc_keywords.csv)
- Classification : Jev (TypeSafe AI, structured output) pour sentiment/stance/type
- Artemis.xyz : Excel statique (pas d'API), lire le fichier existant

---

## Dashboard app.py

3 sections accessibles depuis la page d'accueil (cartes cliquables) :
1. **Prix et rendements** — OHLCV, log-rendements, volatilite rolling 30j
2. **Flux de transactions** — bucket analysis (stacked bar volume + count shares)
3. **Stabilite du marche** — facteur DFM, choc eGARCH, beta QR rolling, FI/FF

La section CBDC/Banques centrales sera ajoutee quand le pipeline sera pret.

Architecture :
- Au demarrage : `data_repo.pull()` (une seule requete reseau git pull)
- Lectures : parquets locaux (instantane)
- Callbacks Dash : lazy loading par section (spinner pendant chargement)
- Theme : dark (#07080f background), police Inter

Lancement :
```bash
python app.py
# http://localhost:8050
```

---

## Data repo (Crypto-Dashboard-Data)

Les pipelines exportent leurs outputs agreges vers le clone local
`C:\Users\fkraus\Desktop\DASHBOARD CRYPTO\crypto-research-data\`, qui pousse automatiquement
vers GitHub (krauuuus/Crypto-Dashboard-Data).

Structure du data repo :
```
bucket_analysis/      stats mensuelles buckets (parquet par asset/exchange)
crypto_prices/        crypto_prices.parquet (OHLCV daily 7 cryptos)
crypto_stability/     factor_shock.parquet, qr_rolling.parquet, fi_ff.parquet
imf_cper/             imf_cper.parquet (a creer)
```

Helper : `pipelines/data_repo.py` — fonctions `export()`, `pull()`, `read()`.

---

## Decisions de design importantes

- **Seuils buckets fixes en USD** : justifie car les agents raisonnent en USD.
  Robustesse via 4 specs (baseline/tight/loose/log5) dans ROBUSTNESS_SPECS.
- **aggTrades Binance** : mesure la taille de l'ordre (pas les fills fragmentes),
  meilleure estimation des buckets institutionnels. Kraken/Coinbase = trades bruts
  → legere sous-estimation B4/B5, a mentionner dans la section robustesse.
- **Correction USDT/USD** : CoinGecko, cache JSON. Negligeable en temps normal
  (<0.1%), materielle lors des stress events (LUNA -3%, FTX -3%).
- **Cache 24h DFM** : evite la revision retroactive des estimations du facteur
  quand on ajoute de nouvelles donnees. Solution pragmatique documentee.
- **Imports relatifs** (from .config import ...) : tous les fichiers du sous-package
  trade_buckets utilisent des imports relatifs. Lancer avec python -m, pas python direct.
- **git workflow** : dev pour le WIP, main pour le stable. Ne jamais pusher sur main
  une section incomplete ou non testee.

---

## Prochaines etapes (dans l'ordre)

1. Installer scikit-learn, statsmodels, arch (quand pipeline bucket termine)
2. Tester crypto_prices.py et crypto_stability.py
3. Ajouter export data_repo dans trade_buckets/main.py
4. Ecrire pipelines/imf_cper.py (API IMF SDMX)
5. Valider les 3 sections du dashboard → merger dev → main → tagger v1.0
6. Ecrire pipelines/cbdc_speeches.py
7. Ajouter section Banques centrales dans app.py

---

## Papiers de reference

- **Bucket Analysis** : Baur & Dimpfl (2019), Cong, Tang & Wang (2023)
- **CryptoStability** : papier de l'utilisateur (DFM + eGARCH + rolling QR)
- **CBDC Stablecoins** : papier de l'utilisateur (BIS speeches + Jev classification)
- **IMF CPER** : Graf von Luckner, Koepke & Sgherri (2024), IMF WP 2024/133
