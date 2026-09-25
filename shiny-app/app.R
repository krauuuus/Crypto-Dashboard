library(shiny)
library(plotly)
library(dplyr)
library(arrow)
library(dotenv)
library(lubridate)

source("pipelines/config.R")

# ── Thème ────────────────────────────────────────────────────────────────────

ACCENT <- "#4f46e5"
RED    <- "#dc2626"
BLUE   <- "#3b82f6"
MUTED  <- "#64748b"

# ── Données (lues une fois au démarrage) ─────────────────────────────────────

prices      <- tryCatch(read_parquet(file.path(CACHE_DIR, "crypto_prices.parquet")),      error = function(e) NULL)
factor_tbl  <- tryCatch(read_parquet(file.path(CACHE_DIR, "stability_factor.parquet")),   error = function(e) NULL)
share_tbl   <- tryCatch(read_parquet(file.path(CACHE_DIR, "stability_share.parquet")),    error = function(e) NULL)
cbdc_data   <- tryCatch({ source("pipelines/cbdc.R"); load_cbdc() },                      error = function(e) NULL)

# ── UI ───────────────────────────────────────────────────────────────────────

ui <- fluidPage(
  title = "Crypto Research",
  tags$head(tags$style(HTML("
    body { background: #f5f6f8; font-family: 'Inter', sans-serif; color: #1e293b; }
    .nav-tabs > li > a { color: #64748b; }
    .nav-tabs > li.active > a { color: #4f46e5; border-top: 2px solid #4f46e5; }
    .stat-box { background: white; border-radius: 8px; padding: 14px 18px;
                border: 1px solid #e2e8f0; display: inline-block; min-width: 130px; }
    .stat-label { font-size: 11px; color: #64748b; text-transform: uppercase; }
    .stat-value { font-size: 22px; font-weight: 600; }
  "))),

  h3("Crypto Research Dashboard", style = "padding: 20px 20px 0;"),

  tabsetPanel(
    # ── Onglet Prix ──────────────────────────────────────────────────────────
    tabPanel("Prices",
      fluidRow(
        column(3, selectInput("price_asset", "Asset",
                              choices = if (!is.null(prices)) unique(prices$symbol) else "BTC",
                              selected = "BTC")),
        column(3, selectInput("price_period", "Period",
                              choices = c("1 year" = "1y", "3 years" = "3y", "All" = "all"),
                              selected = "1y"))
      ),
      plotlyOutput("price_chart", height = "320px"),
      plotlyOutput("vol_chart",   height = "240px")
    ),

    # ── Onglet Stability ─────────────────────────────────────────────────────
    tabPanel("Market Stability",
      fluidRow(
        column(12,
          uiOutput("stability_stats"),
          plotlyOutput("factor_chart", height = "280px"),
          plotlyOutput("fi_ff_chart",  height = "380px")
        )
      )
    ),

    # ── Onglet CBDC ──────────────────────────────────────────────────────────
    tabPanel("CBDC Sentiment",
      fluidRow(
        column(12,
          div(style = "margin: 12px 0;",
            actionButton("btn_bert",    "BERT",       class = "btn btn-default"),
            actionButton("btn_jev",     "Jev",        class = "btn btn-default"),
            actionButton("btn_compare", "Comparison", class = "btn btn-default")
          ),
          plotlyOutput("cbdc_chart", height = "360px")
        )
      )
    )
  )
)

# ── Server ───────────────────────────────────────────────────────────────────

server <- function(input, output, session) {

  # ── Prix ───────────────────────────────────────────────────────────────────

  price_df <- reactive({
    req(prices, input$price_asset, input$price_period)
    df <- prices |> filter(symbol == input$price_asset) |> arrange(date)
    cutoff <- switch(input$price_period,
      "1y"  = Sys.Date() - 365,
      "3y"  = Sys.Date() - 365 * 3,
      "all" = as.Date("2000-01-01")
    )
    df |>
      filter(date >= cutoff) |>
      mutate(log_ret = log(close / lag(close)),
             vol_30  = zoo::rollapply(log_ret, 30, sd, fill = NA, align = "right") * sqrt(252) * 100)
  })

  output$price_chart <- renderPlotly({
    df <- price_df()
    plot_ly(df, x = ~date, y = ~close, type = "scatter", mode = "lines",
            line = list(color = ACCENT, width = 1.5)) |>
      layout(title = paste(input$price_asset, "— Close price (USD)"),
             xaxis = list(title = ""), yaxis = list(title = "USD"),
             paper_bgcolor = "white", plot_bgcolor = "#fafbfc")
  })

  output$vol_chart <- renderPlotly({
    df <- price_df()
    plot_ly(df, x = ~date, y = ~vol_30, type = "scatter", mode = "lines",
            fill = "tozeroy", fillcolor = "rgba(245,158,11,0.1)",
            line = list(color = "#d97706", width = 1.5)) |>
      layout(title = "30-day annualised volatility (%)",
             xaxis = list(title = ""), yaxis = list(title = "%"),
             paper_bgcolor = "white", plot_bgcolor = "#fafbfc")
  })

  # ── Stability ──────────────────────────────────────────────────────────────

  output$stability_stats <- renderUI({
    req(share_tbl)
    last <- tail(share_tbl, 1)
    n    <- last$n_assets
    tagList(fluidRow(style = "padding: 12px;",
      column(3, div(class = "stat-box",
        div(class = "stat-label", "FI assets (latest)"),
        div(class = "stat-value", style = paste0("color:", RED),
            paste0(round(last$fi_share * n), "/", n)))),
      column(3, div(class = "stat-box",
        div(class = "stat-label", "FF assets (latest)"),
        div(class = "stat-value", style = paste0("color:", BLUE),
            paste0(round(last$ff_share * n), "/", n)))),
      column(3, div(class = "stat-box",
        div(class = "stat-label", "Latest window"),
        div(class = "stat-value", style = paste0("color:", MUTED),
            format(last$date, "%Y-%m"))))
    ))
  })

  output$factor_chart <- renderPlotly({
    req(factor_tbl)
    df <- factor_tbl |> mutate(level = cumsum(factor))
    plot_ly(df, x = ~date, y = ~level, type = "scatter", mode = "lines",
            line = list(color = ACCENT, width = 1.5)) |>
      layout(title = "Common Market Factor — level (cumulative PC1)",
             xaxis = list(title = ""), yaxis = list(title = "Cumulative PC1"),
             paper_bgcolor = "white", plot_bgcolor = "#fafbfc")
  })

  output$fi_ff_chart <- renderPlotly({
    req(share_tbl)
    plot_ly(share_tbl) |>
      add_lines(x = ~date, y = ~round(fi_share * 100, 1),
                name = "Financial Instability (FI)", line = list(color = RED, width = 2)) |>
      add_lines(x = ~date, y = ~round(ff_share * 100, 1),
                name = "Financial Fragility (FF)",   line = list(color = BLUE, width = 2)) |>
      layout(title = "FI/FF asset share by rolling window (18M)",
             xaxis = list(title = ""), yaxis = list(title = "Share (%)", range = c(0, 100)),
             legend = list(orientation = "h", y = -0.15),
             paper_bgcolor = "white", plot_bgcolor = "#fafbfc")
  })

  # ── CBDC ───────────────────────────────────────────────────────────────────

  cbdc_model <- reactiveVal("bert")
  observeEvent(input$btn_bert,    cbdc_model("bert"))
  observeEvent(input$btn_jev,     cbdc_model("jev"))
  observeEvent(input$btn_compare, cbdc_model("compare"))

  output$cbdc_chart <- renderPlotly({
    req(cbdc_data)
    model <- cbdc_model()

    df <- if (model == "bert") {
      filter(cbdc_data, model == "BERT")
    } else if (model == "jev") {
      filter(cbdc_data, model == "Jev")
    } else {
      cbdc_data
    }

    p <- plot_ly()
    for (mdl in unique(df$model)) {
      sub  <- filter(df, model == mdl)
      dash <- if (mdl == "Jev") "dash" else "solid"
      p <- p |>
        add_lines(data = sub, x = ~date, y = ~stance_idx,
                  name = paste(mdl, "Stance"),
                  line = list(color = RED, dash = dash, width = 1.8)) |>
        add_lines(data = sub, x = ~date, y = ~sentiment_idx,
                  name = paste(mdl, "Sentiment"),
                  line = list(color = BLUE, dash = dash, width = 1.8))
    }
    p |>
      add_segments(x = min(df$date), xend = max(df$date), y = 0, yend = 0,
                   line = list(color = "#e2e8f0", dash = "dot"), showlegend = FALSE) |>
      layout(title = "CBDC Stance & Sentiment indices (3M MA)",
             xaxis = list(title = ""), yaxis = list(title = "Index (±1)"),
             legend = list(orientation = "h", y = -0.15),
             paper_bgcolor = "white", plot_bgcolor = "#fafbfc")
  })
}

shinyApp(ui, server)
