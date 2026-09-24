# pipelines/prices.R — téléchargement des prix via yfinance (via reticulate) ou quantmod
# Rscript pipelines/prices.R
source("pipelines/config.R")
library(arrow)
library(dplyr)
library(quantmod)
library(lubridate)

SYMBOLS <- c("BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
             "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD", "LTC-USD")

load_prices <- function(force_refresh = FALSE) {
  cache_file <- file.path(CACHE_DIR, "crypto_prices.parquet")

  if (!force_refresh && file.exists(cache_file)) {
    age_h <- as.numeric(difftime(Sys.time(), file.mtime(cache_file), units = "hours"))
    if (age_h < 24) {
      message("prices : cache frais")
      return(read_parquet(cache_file))
    }
  }

  message("prices : téléchargement...")
  rows <- list()
  for (sym in SYMBOLS) {
    tryCatch({
      raw <- getSymbols(sym, src = "yahoo", auto.assign = FALSE,
                        from = "2015-01-01", warnings = FALSE)
      df <- data.frame(
        date   = index(raw),
        symbol = sub("-USD", "", sym),
        open   = as.numeric(Op(raw)),
        high   = as.numeric(Hi(raw)),
        low    = as.numeric(Lo(raw)),
        close  = as.numeric(Cl(raw)),
        volume = as.numeric(Vo(raw))
      )
      rows[[sym]] <- df
      message("  ✓ ", sym)
    }, error = function(e) message("  ✗ ", sym, " : ", e$message))
  }

  out <- bind_rows(rows)
  write_parquet(out, cache_file)
  message("prices : ", nrow(out), " lignes sauvegardées")
  out
}

if (!interactive()) load_prices(force_refresh = TRUE)
