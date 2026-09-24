# pipelines/config.R — clés API et chemins
library(dotenv)   # install.packages("dotenv")

load_dot_env(file = here::here(".env"))

CMC_KEY   <- Sys.getenv("COINMARKETCAP_API_KEY")
CC_KEY    <- Sys.getenv("CRYPTOCOMPARE_API_KEY")
JEV_KEY   <- Sys.getenv("JEV_API_KEY")

CACHE_DIR <- here::here("data", "cache")
dir.create(CACHE_DIR, recursive = TRUE, showWarnings = FALSE)
