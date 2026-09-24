# pipelines/cbdc.R — lecture des indices CBDC pré-calculés (Python → parquet)
# Les scores BERT et Jev sont déjà calculés ; ce pipeline lit et agrège.
source("pipelines/config.R")
library(arrow)
library(dplyr)
library(lubridate)

CUTOFF <- as.Date("2018-01-01")
MA     <- 3  # rolling average en mois

load_cbdc <- function() {
  # Lire les parquets produits par le pipeline Python (cbdc_speeches.py)
  bert_file <- file.path(CACHE_DIR, "cbdc_bert_monthly.parquet")
  jev_file  <- file.path(CACHE_DIR, "cbdc_jev_monthly.parquet")

  if (!file.exists(bert_file)) stop("Fichier BERT introuvable. Lance le pipeline Python d'abord.")
  if (!file.exists(jev_file))  stop("Fichier Jev introuvable. Lance le pipeline Python d'abord.")

  bert <- read_parquet(bert_file) |> mutate(model = "BERT")
  jev  <- read_parquet(jev_file)  |> mutate(model = "Jev")

  bind_rows(bert, jev) |>
    filter(date >= CUTOFF) |>
    group_by(model) |>
    arrange(date) |>
    mutate(
      stance_idx    = zoo::rollmean(stance_index,    MA, fill = NA, align = "right"),
      sentiment_idx = zoo::rollmean(sentiment_index, MA, fill = NA, align = "right")
    ) |>
    ungroup()
}
