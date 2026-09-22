"""
app.py — Crypto Research Dashboard
3 onglets : Bucket Analysis | CryptoStability | CBDC Stablecoins

Lancement :
    python app.py
    (ou depuis Anaconda Prompt avec l'env finance-dashboard activé)
"""

import logging
import dash
from dash import Dash, html, dcc, Input, Output, State
import plotly.graph_objects as go

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="Crypto Research Dashboard",
)

# ── Palette ───────────────────────────────────────────────────────────────────
C = {
    "bg":       "#0f1117",
    "surface":  "#1a1d27",
    "border":   "#2a2d3e",
    "accent":   "#7c6af7",
    "green":    "#22c55e",
    "red":      "#ef4444",
    "yellow":   "#eab308",
    "text":     "#e2e8f0",
    "muted":    "#64748b",
}

TAB_STYLE = {
    "backgroundColor": C["surface"],
    "color": C["muted"],
    "border": f"1px solid {C['border']}",
    "padding": "10px 24px",
    "fontFamily": "Inter, sans-serif",
    "fontSize": "14px",
}
TAB_SELECTED = {
    **TAB_STYLE,
    "color": C["text"],
    "borderBottom": f"2px solid {C['accent']}",
    "backgroundColor": C["bg"],
}
CARD = {
    "backgroundColor": C["surface"],
    "border": f"1px solid {C['border']}",
    "borderRadius": "8px",
    "padding": "20px",
    "marginBottom": "16px",
}


# ══════════════════════════════════════════════════════════════════════════════
# Layout global
# ══════════════════════════════════════════════════════════════════════════════

app.layout = html.Div(style={"backgroundColor": C["bg"], "minHeight": "100vh",
                              "fontFamily": "Inter, sans-serif", "color": C["text"]}, children=[

    # Header
    html.Div(style={"backgroundColor": C["surface"], "borderBottom": f"1px solid {C['border']}",
                    "padding": "16px 32px", "display": "flex", "alignItems": "center", "gap": "12px"}, children=[
        html.Div("◈", style={"color": C["accent"], "fontSize": "22px"}),
        html.H1("Crypto Research Dashboard",
                style={"margin": 0, "fontSize": "18px", "fontWeight": "600"}),
        html.Span("live data", style={"backgroundColor": C["accent"], "color": "white",
                                       "fontSize": "10px", "padding": "2px 8px",
                                       "borderRadius": "12px", "marginLeft": "8px"}),
    ]),

    # Onglets
    dcc.Tabs(id="tabs", value="buckets", style={"backgroundColor": C["surface"]},
             children=[
        dcc.Tab(label="📊 Bucket Analysis",    value="buckets",
                style=TAB_STYLE, selected_style=TAB_SELECTED),
        dcc.Tab(label="📈 CryptoStability",    value="stability",
                style=TAB_STYLE, selected_style=TAB_SELECTED),
        dcc.Tab(label="🏦 CBDC Stablecoins",   value="cbdc",
                style=TAB_STYLE, selected_style=TAB_SELECTED),
    ]),

    # Contenu de l'onglet actif
    html.Div(id="tab-content", style={"padding": "24px 32px"}),
])


# ══════════════════════════════════════════════════════════════════════════════
# Onglet 1 : Bucket Analysis
# ══════════════════════════════════════════════════════════════════════════════

def layout_buckets():
    return html.Div([
        html.Div(style={"display": "flex", "gap": "16px", "marginBottom": "16px",
                        "flexWrap": "wrap"}, children=[
            html.Div(style={**CARD, "minWidth": "180px"}, children=[
                dcc.Dropdown(
                    id="bucket-asset",
                    options=[{"label": a, "value": a} for a in ["BTC", "ETH", "XRP", "BNB", "LTC"]],
                    value="BTC",
                    clearable=False,
                    style={"backgroundColor": C["bg"], "color": C["text"]},
                ),
            ]),
            html.Div(style={**CARD, "minWidth": "180px"}, children=[
                dcc.Dropdown(
                    id="bucket-exchange",
                    options=[
                        {"label": "Cross-exchange (agrégé)", "value": "cross_exchange"},
                        {"label": "Binance", "value": "binance"},
                        {"label": "Bybit",   "value": "bybit"},
                        {"label": "OKX",     "value": "okx"},
                        {"label": "Kraken",  "value": "kraken"},
                        {"label": "Coinbase","value": "coinbase"},
                    ],
                    value="cross_exchange",
                    clearable=False,
                    style={"backgroundColor": C["bg"], "color": C["text"]},
                ),
            ]),
        ]),

        dcc.Loading(type="circle", color=C["accent"], children=[
            html.Div(id="bucket-graphs"),
        ]),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Onglet 2 : CryptoStability
# ══════════════════════════════════════════════════════════════════════════════

def layout_stability():
    return html.Div([
        html.P(
            "DFM (PCA r=1) → eGARCH(1,1) → rolling quantile regression (τ = 0.05 / 0.50 / 0.95)",
            style={"color": C["muted"], "fontSize": "13px", "marginBottom": "16px"}
        ),
        dcc.Dropdown(
            id="stab-asset",
            options=[{"label": a, "value": a}
                     for a in ["BTC", "ETH", "XRP", "BNB", "ADA", "TRX", "DOGE"]],
            value="BTC",
            clearable=False,
            style={"backgroundColor": C["bg"], "color": C["text"],
                   "maxWidth": "200px", "marginBottom": "16px"},
        ),
        dcc.Loading(type="circle", color=C["accent"], children=[
            html.Div(id="stability-graphs"),
        ]),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Onglet 3 : CBDC Stablecoins
# ══════════════════════════════════════════════════════════════════════════════

def layout_cbdc():
    return html.Div([
        html.Div(style=CARD, children=[
            html.P("Pipeline CBDC Stablecoins à venir.",
                   style={"color": C["muted"], "margin": 0}),
            html.P("Sources : BIS speeches + filtre CBDC keywords + Jev classification.",
                   style={"color": C["muted"], "fontSize": "12px", "margin": "4px 0 0 0"}),
        ]),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Callback : routing des onglets
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(Output("tab-content", "children"), Input("tabs", "value"))
def render_tab(tab):
    if tab == "buckets":   return layout_buckets()
    if tab == "stability": return layout_stability()
    if tab == "cbdc":      return layout_cbdc()
    return html.Div("Onglet inconnu")


# ══════════════════════════════════════════════════════════════════════════════
# Callback : Bucket Analysis — graphiques
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("bucket-graphs", "children"),
    Input("bucket-asset", "value"),
    Input("bucket-exchange", "value"),
)
def update_buckets(asset, exchange):
    from pathlib import Path
    import pandas as pd

    MONTHLY_DIR = Path(__file__).parent / "data" / "raw" / "buckets" / "monthly"

    if exchange == "cross_exchange":
        base = MONTHLY_DIR / "aggregated" / asset
    else:
        base = MONTHLY_DIR / exchange / asset

    parquets = sorted(base.glob("*.parquet")) if base.exists() else []

    if not parquets:
        return html.Div(
            f"Aucune donnée disponible pour {asset} / {exchange}. "
            "Lance d'abord le pipeline bucket.",
            style={"color": C["muted"], "padding": "40px", "textAlign": "center"}
        )

    df = pd.concat([pd.read_parquet(p) for p in parquets], ignore_index=True)
    df["year_month"] = df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)
    df = df.sort_values("year_month")

    bucket_colors = {
        "B1_micro_retail":  "#64748b",
        "B2_retail":        "#3b82f6",
        "B3_semi_inst":     "#8b5cf6",
        "B4_institutional": "#f59e0b",
        "B5_whale":         "#ef4444",
    }
    bucket_labels = {
        "B1_micro_retail":  "< $1K",
        "B2_retail":        "$1K–$10K",
        "B3_semi_inst":     "$10K–$100K",
        "B4_institutional": "$100K–$1M",
        "B5_whale":         "> $1M",
    }

    # Graphique 1 : parts de volume par bucket (stacked bar)
    fig_vol = go.Figure()
    for bucket, color in bucket_colors.items():
        sub = df[df["bucket"] == bucket]
        fig_vol.add_trace(go.Bar(
            x=sub["year_month"],
            y=sub["volume_share_pct"],
            name=bucket_labels.get(bucket, bucket),
            marker_color=color,
        ))
    fig_vol.update_layout(
        barmode="stack",
        title=f"{asset} — Part de volume par bucket ({exchange})",
        template="plotly_dark",
        paper_bgcolor=C["surface"],
        plot_bgcolor=C["surface"],
        legend=dict(orientation="h", y=-0.2),
        yaxis_title="% du volume total",
        height=400,
    )

    # Graphique 2 : nombre de trades par bucket
    fig_cnt = go.Figure()
    for bucket, color in bucket_colors.items():
        sub = df[df["bucket"] == bucket]
        fig_cnt.add_trace(go.Bar(
            x=sub["year_month"],
            y=sub["count_share_pct"],
            name=bucket_labels.get(bucket, bucket),
            marker_color=color,
            showlegend=False,
        ))
    fig_cnt.update_layout(
        barmode="stack",
        title=f"{asset} — Part du nombre de trades par bucket ({exchange})",
        template="plotly_dark",
        paper_bgcolor=C["surface"],
        plot_bgcolor=C["surface"],
        yaxis_title="% du nombre de trades",
        height=350,
    )

    return html.Div([dcc.Graph(figure=fig_vol), dcc.Graph(figure=fig_cnt)])


# ══════════════════════════════════════════════════════════════════════════════
# Callback : CryptoStability — graphiques
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("stability-graphs", "children"),
    Input("stab-asset", "value"),
)
def update_stability(asset):
    try:
        from pipelines.crypto_stability import load_stability
        result = load_stability()
        factor = result["factor"]
        shock  = result["shock"]
        fi_ff  = result["fi_ff"]
    except Exception as e:
        return html.Div(
            f"Erreur chargement CryptoStability : {e}",
            style={"color": C["red"], "padding": "20px"}
        )

    # Couleur de classification pour l'asset sélectionné
    latest = fi_ff[fi_ff["asset"] == asset].sort_values("date").tail(1)
    classif = latest["classification"].values[0] if len(latest) else "NC"
    classif_color = {"FI": C["green"], "FF": C["red"], "NC": C["muted"]}.get(classif, C["muted"])

    # Badge classification actuelle
    badge = html.Div(style={"display": "flex", "gap": "16px", "marginBottom": "16px"}, children=[
        html.Div(style={**CARD, "textAlign": "center", "minWidth": "140px"}, children=[
            html.P("Classification actuelle", style={"margin": 0, "fontSize": "12px", "color": C["muted"]}),
            html.H2(classif, style={"margin": "4px 0 0 0", "color": classif_color, "fontSize": "32px"}),
            html.P({"FI": "Flight-to-safety Indicator",
                    "FF": "Flight-from-safety",
                    "NC": "Non classifié"}.get(classif, ""),
                   style={"margin": 0, "fontSize": "11px", "color": C["muted"]}),
        ]),
    ])

    # Graphique facteur + choc
    fig_ts = go.Figure()
    fig_ts.add_trace(go.Scatter(
        x=factor.index, y=factor.values,
        name="Facteur latent (PCA)", line={"color": C["accent"], "width": 1.5}
    ))
    fig_ts.add_trace(go.Scatter(
        x=shock.index, y=shock.values,
        name="Choc eGARCH", line={"color": C["yellow"], "width": 1}, opacity=0.7,
        yaxis="y2"
    ))
    fig_ts.update_layout(
        title="Facteur latent du marché crypto + choc eGARCH(1,1)",
        template="plotly_dark",
        paper_bgcolor=C["surface"],
        plot_bgcolor=C["surface"],
        height=350,
        yaxis=dict(title="Facteur", side="left"),
        yaxis2=dict(title="Choc", overlaying="y", side="right"),
        legend=dict(orientation="h", y=-0.2),
    )

    # Graphique beta QR rolling pour l'asset sélectionné
    asset_qr = fi_ff[fi_ff["asset"] == asset].sort_values("date")
    fig_qr = go.Figure()
    for col, tau_label, color in [
        ("beta_05", "β(τ=0.05)", C["red"]),
        ("beta_50", "β(τ=0.50)", C["text"]),
        ("beta_95", "β(τ=0.95)", C["green"]),
    ]:
        if col in asset_qr.columns:
            fig_qr.add_trace(go.Scatter(
                x=asset_qr["date"], y=asset_qr[col],
                name=tau_label, line={"color": color, "width": 1.5}
            ))
    fig_qr.add_hline(y=0, line_dash="dash", line_color=C["muted"], opacity=0.5)
    fig_qr.update_layout(
        title=f"{asset} — Coefficients β rolling (fenêtre 18 mois)",
        template="plotly_dark",
        paper_bgcolor=C["surface"],
        plot_bgcolor=C["surface"],
        height=350,
        yaxis_title="β",
        legend=dict(orientation="h", y=-0.2),
    )

    return html.Div([badge, dcc.Graph(figure=fig_ts), dcc.Graph(figure=fig_qr)])


# ══════════════════════════════════════════════════════════════════════════════
# Lancement
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(debug=False, port=8050)
