# pipelines/stability.R — DFM → eGARCH(1,1) → QR rolling → classification FI/FF
# Rscript pipelines/stability.R
source("pipelines/config.R")
library(arrow)
library(dplyr)
library(tidyr)
library(zoo)
library(dfms)       # DFM()
library(rugarch)    # ugarchfit()
library(quantreg)   # rq()
library(progress)

WINDOW_MONTHS <- 18
TAU           <- c(0.05, 0.50, 0.95)
MIN_COVERAGE  <- 0.90
ALPHA         <- 0.10

# 7 cryptos pour le facteur commun (Che et al. 2023)
FACTOR_CRYPTOS <- c("BTC", "ETH", "XRP", "BNB", "ADA", "TRX", "DOGE")

# ── 1. Facteur commun (DFM r=1, p=5) ─────────────────────────────────────────

extract_factor <- function(prices_tbl) {
  wide <- prices_tbl |>
    filter(symbol %in% FACTOR_CRYPTOS) |>
    arrange(date) |>
    select(date, symbol, close) |>
    pivot_wider(names_from = symbol, values_from = close) |>
    filter(date >= as.Date("2017-12-31")) |>
    drop_na()

  zoo_data <- zoo(wide[, -1], order.by = wide$date)
  dfm_fit  <- DFM(zoo_data, r = 1, p = 5)

  factor_level <- data.frame(date = wide$date,
                              crypto_factor = dfm_fit$F_pca[, 1])

  # première différence du facteur
  factor_diff <- zoo(c(NA, diff(factor_level$crypto_factor)),
                     order.by = factor_level$date)

  message("DFM : facteur extrait sur ", nrow(wide), " observations")
  list(level = factor_level, diff = factor_diff)
}

# ── 2. eGARCH(1,1) sur la différence du facteur ───────────────────────────────

fit_egarch <- function(factor_diff_zoo) {
  spec <- ugarchspec(
    mean.model     = list(armaOrder = c(0, 0), include.mean = TRUE, archm = FALSE),
    variance.model = list(model = "eGARCH", garchOrder = c(1, 1),
                          variance.targeting = FALSE),
    distribution.model = "std"
  )
  fit <- ugarchfit(spec, data = na.omit(factor_diff_zoo), solver = "hybrid")

  dates_fit <- index(na.omit(factor_diff_zoo))
  resid_raw <- as.numeric(fit@fit$residuals)

  # choc scalé par écart-type rolling 90 jours
  resid_zoo <- zoo(resid_raw, order.by = dates_fit)
  sd90      <- rollapply(resid_zoo, width = 90, FUN = sd,
                         fill = NA, na.rm = TRUE, align = "right")
  adj_shock <- resid_zoo / sd90

  message(sprintf("eGARCH : résidus sur %d obs", length(resid_raw)))
  data.frame(date        = dates_fit,
             resid_garch = resid_raw,
             adj_shock90 = as.numeric(adj_shock))
}

# ── 3. Base de données finale (retours scalés + choc) ─────────────────────────

build_dataset <- function(prices_tbl, shock_tbl) {
  prices_tbl |>
    arrange(date) |>
    group_by(symbol) |>
    mutate(
      return = c(NA, diff(log(close))),
      sd90   = rollapply(return, width = 90, FUN = var,
                         fill = NA, na.rm = TRUE, align = "right"),
      adj_returns90 = return / sd90
    ) |>
    ungroup() |>
    left_join(shock_tbl |> mutate(date = as.Date(date)), by = "date") |>
    filter(date >= as.Date("2018-01-01")) |>
    filter(!is.na(adj_returns90), !is.na(adj_shock90))
}

# ── 4. QR rolling ─────────────────────────────────────────────────────────────

wald_test <- function(b1, s1, b2, s2, alpha = ALPHA) {
  z <- (b1 - b2) / sqrt(s1^2 + s2^2)
  p <- 2 * pnorm(-abs(z))
  list(reject = (p < alpha), pval = p, direction = if (b1 > b2) "increasing" else "decreasing")
}

fit_window_asset <- function(df_asset) {
  res <- map_dfr(TAU, function(tau) {
    fit <- tryCatch(
      rq(adj_returns90 ~ adj_shock90, data = df_asset, tau = tau),
      error = function(e) NULL
    )
    if (is.null(fit)) return(tibble(tau = tau, beta = NA_real_, se = NA_real_))

    su <- tryCatch(
      summary(fit, se = "nid")$coefficients,
      error = function(e) NULL
    )
    if (is.null(su) || !"adj_shock90" %in% rownames(su))
      return(tibble(tau = tau, beta = NA_real_, se = NA_real_))

    tibble(tau = tau,
           beta = su["adj_shock90", "Value"],
           se   = su["adj_shock90", "Std. Error"])
  })

  b_low  <- res$beta[res$tau == 0.05]; s_low  <- res$se[res$tau == 0.05]
  b_med  <- res$beta[res$tau == 0.50]; s_med  <- res$se[res$tau == 0.50]
  b_high <- res$beta[res$tau == 0.95]; s_high <- res$se[res$tau == 0.95]

  wL <- if (any(is.na(c(b_low,  s_low,  b_med, s_med))))  NULL else wald_test(b_low,  s_low,  b_med, s_med)
  wU <- if (any(is.na(c(b_high, s_high, b_med, s_med)))) NULL else wald_test(b_high, s_high, b_med, s_med)

  left_tail  <- if (is.null(wL) || !wL$reject) "constant" else wL$direction
  right_tail <- if (is.null(wU) || !wU$reject) "constant" else wU$direction

  tibble(
    beta_lower  = b_low,  beta_median = b_med,  beta_upper = b_high,
    p_left      = if (is.null(wL)) NA_real_ else wL$pval,
    p_right     = if (is.null(wU)) NA_real_ else wU$pval,
    left_tail   = left_tail,
    right_tail  = right_tail,
    cont        = (left_tail  == "increasing"),   # FI : contagion
    spec        = (right_tail == "increasing"),   # FF : ripple/speculation
    instable    = cont | spec
  )
}

rolling_qr <- function(data_tbl) {
  dates   <- sort(unique(data_tbl$date))
  windows <- seq(
    from = min(dates) + months(WINDOW_MONTHS),
    to   = max(dates),
    by   = "month"
  )

  pb <- progress_bar$new(total = length(windows),
                          format = "QR rolling [:bar] :current/:total | ETA: :eta",
                          clear = FALSE, width = 60)

  out <- vector("list", length(windows))
  for (i in seq_along(windows)) {
    pb$tick()
    end_date   <- windows[i]
    start_date <- end_date - months(WINDOW_MONTHS)
    sub <- data_tbl |> filter(date >= start_date, date <= end_date)

    # filtre couverture ≥ 90 %
    n_days <- as.integer(end_date - start_date)
    good <- sub |>
      group_by(symbol) |>
      summarise(cov = n() / n_days, .groups = "drop") |>
      filter(cov >= MIN_COVERAGE) |>
      pull(symbol)

    sub <- filter(sub, symbol %in% good)
    if (nrow(sub) == 0) next

    res <- sub |>
      group_by(symbol) |>
      group_modify(~ fit_window_asset(.x)) |>
      ungroup() |>
      mutate(end = end_date)

    out[[i]] <- res
  }
  bind_rows(out)
}

# ── 5. Agrégation des parts FI / FF ───────────────────────────────────────────

fi_ff_share <- function(roll_tbl) {
  roll_tbl |>
    group_by(end) |>
    summarise(
      n_assets = n(),
      fi_share = mean(cont,     na.rm = TRUE),
      ff_share = mean(spec,     na.rm = TRUE),
      .groups  = "drop"
    ) |>
    rename(date = end)
}

# ── Point d'entrée ────────────────────────────────────────────────────────────

run_stability <- function() {
  prices <- read_parquet(file.path(CACHE_DIR, "crypto_prices.parquet"))

  factor_res <- extract_factor(prices)
  shock_tbl  <- fit_egarch(factor_res$diff)
  data_tbl   <- build_dataset(prices, shock_tbl)
  roll_tbl   <- rolling_qr(data_tbl)
  share_tbl  <- fi_ff_share(roll_tbl)

  factor_out <- factor_res$level |>
    mutate(level = cumsum(replace_na(crypto_factor, 0))) |>
    rename(factor = crypto_factor)

  write_parquet(factor_out, file.path(CACHE_DIR, "stability_factor.parquet"))
  write_parquet(roll_tbl,   file.path(CACHE_DIR, "stability_roll.parquet"))
  write_parquet(share_tbl,  file.path(CACHE_DIR, "stability_share.parquet"))
  message("stability : done — ", nrow(share_tbl), " fenêtres")
}

if (!interactive()) run_stability()
