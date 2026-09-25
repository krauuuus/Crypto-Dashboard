# Crypto Research Dashboard

Interactive dashboard for crypto-asset financial stability research. Built with Python/Dash (main app) and R/Shiny (alternative).

## Features

- **Prices** — Daily close prices and 30-day annualised volatility for 100+ crypto-assets
- **Transaction Flows** — Buyer/seller bucket analysis from Binance & Bybit order flow data
- **Market Stability** — Rolling quantile regression (QR) model identifying Financial Instability (FI) and Financial Fragility (FF) regimes
- **CBDC Sentiment** — Central bank speech sentiment indices (BERT & Jev models)

## Market Stability Methodology

Based on Che et al. (2023). For each asset and each 18-month rolling window:

1. **Common factor** — PCA on 7 reference cryptos (BTC, ETH, XRP, BNB, ADA, TRX, DOGE), first PC approximates a DFM(r=1)
2. **Market shock** — GARCH(1,1) residuals on the factor, scaled by rolling 90-day SD (`adj_shock90`)
3. **Quantile regression** — `adj_return_it ~ adj_shock_t` at τ ∈ {0.05, 0.50, 0.95}, where `adj_return = return / var_90d(return)`
4. **Wald test** — Two-sided test of β(τ_extreme) = β(0.50) at α = 10%
5. **Classification** — FI (contagion): β(0.05) > β(0.50) significantly; FF (fragility): β(0.95) > β(0.50) significantly

## Project Structure

```
├── app.py                          # Dash application entry point
├── assets/dark.css                 # Dark theme stylesheet
├── pipelines/
│   ├── config.py                   # API keys, paths, constants
│   ├── crypto_prices.py            # Price data (CryptoCompare + yfinance fallback)
│   ├── crypto_stability.py         # Stability pipeline (PCA → GARCH → QR → FI/FF)
│   ├── cbdc_speeches.py            # CBDC speech sentiment pipeline
│   ├── global_market_cap.py        # Total market cap data
│   ├── data_repo.py                # Export to crypto-research-data repo
│   └── trade_buckets/              # Transaction flow pipeline (Binance/Bybit)
├── shiny-app/                      # R/Shiny equivalent dashboard
│   ├── app.R
│   └── pipelines/
│       ├── config.R
│       ├── prices.R
│       ├── stability.R             # DFM + eGARCH + QR rolling (R version)
│       └── cbdc.R
├── requirements.txt
├── Procfile                        # Render.com deployment
└── start.sh                        # Startup script (clones data repo, starts gunicorn)
```

## Running the pipelines

```bash
# Prices (incremental, cached 24h)
python -m pipelines.crypto_prices

# Market Stability (~8 min, cached 7 days)
python -m pipelines.crypto_stability

# Transaction Flows — first run
python -m pipelines.trade_buckets.main
# Transaction Flows — update only (new months)
python -m pipelines.trade_buckets.main --update

# CBDC speeches
python -m pipelines.cbdc_speeches
```

## Running the app

```bash
python app.py
# → http://127.0.0.1:8050
```

## Deployment (Render.com)

The app is configured for [Render.com](https://render.com) free tier:
- `Procfile` runs `start.sh` which clones the data repo then starts gunicorn
- Set environment variables (`COINMARKETCAP_API_KEY`, etc.) in the Render dashboard
- Build command: `pip install -r requirements.txt`

## Data repository

Pre-computed parquet files are stored in a separate repo (`krauuuus/Crypto-Dashboard-Data`) and pulled at startup on Render. Locally they live in `data/cache/`.

## Notes

- BLAS operations crash on this environment (numpy 2.5.3 + Windows). All matrix operations in the stability pipeline are implemented element-wise without calling `np.linalg` or `@`.
- The asset universe (144 symbols) is derived from the top-40 cryptos per year (2017–2026) from CoinGecko, excluding stablecoins.
