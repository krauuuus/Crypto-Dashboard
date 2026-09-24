# Crypto Dashboard — Shiny (R)

Équivalent R du dashboard Python/Dash.

## Packages requis

```r
install.packages(c(
  "shiny", "plotly", "dplyr", "arrow", "lubridate",
  "quantmod", "quantreg", "rugarch", "zoo",
  "dotenv", "here"
))
```

## Structure

```
shiny-app/
├── app.R                   # UI + Server Shiny
├── pipelines/
│   ├── config.R            # clés API (.env) et chemins
│   ├── prices.R            # téléchargement des prix
│   ├── stability.R         # PCA → GARCH → QR rolling → FI/FF
│   └── cbdc.R              # lecture des parquets BERT/Jev (Python)
└── data/cache/             # parquets (générés par les pipelines)
```

## Lancer les pipelines

```r
# 1. Prix
Rscript pipelines/prices.R

# 2. Stabilité (long ~10 min)
Rscript pipelines/stability.R

# 3. CBDC : les parquets viennent du pipeline Python (cbdc_speeches.py)
#    Copier crypto_stability/*.parquet dans data/cache/
```

## Lancer l'app

```r
shiny::runApp(".")
# ou depuis RStudio : ouvrir app.R → cliquer Run App
```

## Déployer sur shinyapps.io

```r
library(rsconnect)
rsconnect::deployApp(".")
```
