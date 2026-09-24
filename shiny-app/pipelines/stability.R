# pipelines/stability.R — PCA → GARCH(1,1) → QR rolling → classification FI/FF
# Rscript pipelines/stability.R
source("pipelines/config.R")
library(arrow)
library(dplyr)
library(tidyr)
library(quantreg)   # rq()
library(rugarch)    # ugarchfit()

WINDOW_MONTHS <- 18
TAU           <- c(0.05, 0.50, 0.95)
WALD_CRIT     <- 1.282  # unilatéral α = 10 %

# ── 1. Facteur commun (PCA, r = 1) ────────────────────────────────────────────

extract_factor <- function(returns_wide) {
  # returns_wide : tibble date × assets (colonnes = symbols)
  clean <- returns_wide |>
    select(where(~ mean(!is.na(.)) >= 0.80)) |>
    drop_na()

  X <- scale(as.matrix(clean))
  pc <- prcomp(X, rank. = 1, scale. = FALSE)
  factor <- pc$x[, 1]

  # aligner le signe avec BTC
  btc <- if ("BTC" %in% colnames(clean)) clean[["BTC"]] else clean[[1]]
  if (cor(factor, btc) < 0) factor <- -factor

  pct_var <- pc$sdev[1]^2 / sum(pc$sdev^2) * 100
  message(sprintf("PCA : facteur explique %.1f%% de la variance", pct_var))

  tibble(date = rownames(clean) |> as.Date(), factor = factor)
}

# ── 2. GARCH(1,1) sur le facteur ──────────────────────────────────────────────

fit_garch <- function(factor_tbl) {
  spec <- ugarchspec(
    variance.model = list(model = "sGARCH", garchOrder = c(1, 1)),
    mean.model     = list(armaOrder = c(0, 0), include.mean = TRUE),
    distribution.model = "norm"
  )
  fit <- ugarchfit(spec, data = factor_tbl$factor * 100, solver = "hybrid")
  shock <- residuals(fit, standardize = TRUE)
  tibble(date = factor_tbl$date, shock = as.numeric(shock))
}

# ── 3. QR rolling ─────────────────────────────────────────────────────────────

rolling_qr <- function(returns_wide, shock_tbl) {
  dates   <- returns_wide$date
  assets  <- setdiff(colnames(returns_wide), "date")
  windows <- seq(
    from = min(dates) + months(WINDOW_MONTHS),
    to   = max(dates),
    by   = "month"
  )

  records <- list()
  for (i in seq_along(windows)) {
    end_date   <- windows[i]
    start_date <- end_date - months(WINDOW_MONTHS)
    idx <- dates >= start_date & dates <= end_date
    sub_ret   <- returns_wide[idx, ]
    sub_shock <- shock_tbl$shock[shock_tbl$date %in% sub_ret$date]

    if (length(sub_shock) < 60) next
    if (i %% 10 == 1) message(sprintf("QR rolling : fenêtre %d/%d (%s)", i, length(windows), end_date))

    for (asset in assets) {
      y <- sub_ret[[asset]]
      if (mean(!is.na(y)) < 0.5) next
      keep <- !is.na(y)
      y_   <- y[keep]
      z_   <- sub_shock[keep]
      lag_ <- c(NA, y_[-length(y_)]); lag_[1] <- 0
      X    <- cbind(1, z_, lag_, z_^2)[!is.na(lag_), ]
      y_   <- y_[!is.na(lag_)]

      for (tau in TAU) {
        tryCatch({
          fit  <- rq(y_ ~ z_ + lag_ + I(z_^2), tau = tau,
                     data = data.frame(y_, z_ = z_[!is.na(lag_)],
                                       lag_ = lag_[!is.na(lag_)]),
                     method = "fn")
          coef_ <- coef(summary(fit, covariance = TRUE))
          records[[length(records) + 1]] <- tibble(
            date    = end_date,
            asset   = asset,
            tau     = tau,
            beta    = coef_[2, 1],
            beta_se = coef_[2, 2]
          )
        }, error = function(e) NULL)
      }
    }
  }
  bind_rows(records)
}

# ── 4. Classification FI / FF (test de Wald) ──────────────────────────────────

classify_fi_ff <- function(qr_tbl) {
  qr_wide <- qr_tbl |>
    pivot_wider(names_from = tau, values_from = c(beta, beta_se),
                names_sep = "_")

  qr_wide |>
    mutate(
      z_lower = (beta_0.05 - beta_0.5)  / sqrt(beta_se_0.05^2 + beta_se_0.5^2),
      z_upper = (beta_0.95 - beta_0.5)  / sqrt(beta_se_0.95^2 + beta_se_0.5^2),
      fi = z_lower > WALD_CRIT,
      ff = z_upper > WALD_CRIT,
      classification = case_when(
        fi & ff  ~ "FI+FF",
        fi       ~ "FI",
        ff       ~ "FF",
        TRUE     ~ "NC"
      )
    )
}

fi_ff_share <- function(fi_ff_tbl) {
  fi_ff_tbl |>
    mutate(date = lubridate::floor_date(date, "month")) |>
    group_by(date) |>
    summarise(
      n_assets = n(),
      fi_share = mean(classification %in% c("FI", "FI+FF")),
      ff_share = mean(classification %in% c("FF", "FI+FF")),
      .groups = "drop"
    )
}

# ── Point d'entrée ────────────────────────────────────────────────────────────

run_stability <- function() {
  prices <- read_parquet(file.path(CACHE_DIR, "crypto_prices.parquet"))
  returns_wide <- prices |>
    arrange(date) |>
    group_by(symbol) |>
    mutate(ret = log(close / lag(close))) |>
    ungroup() |>
    select(date, symbol, ret) |>
    pivot_wider(names_from = symbol, values_from = ret)

  factor_tbl <- extract_factor(returns_wide)
  shock_tbl  <- fit_garch(factor_tbl)
  qr_tbl     <- rolling_qr(returns_wide, shock_tbl)
  fi_ff_tbl  <- classify_fi_ff(qr_tbl)
  share_tbl  <- fi_ff_share(fi_ff_tbl)

  write_parquet(factor_tbl, file.path(CACHE_DIR, "stability_factor.parquet"))
  write_parquet(fi_ff_tbl,  file.path(CACHE_DIR, "stability_fi_ff.parquet"))
  write_parquet(share_tbl,  file.path(CACHE_DIR, "stability_share.parquet"))
  message("stability : done")
}

if (!interactive()) run_stability()
