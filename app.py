"""
app.py — Crypto Research Dashboard
Page d'accueil + 4 sections orientées données :
  Prix & Rendements | Flux de transactions | Stabilité du marché | Banques centrales
"""

import logging
import dash
from dash import Dash, html, dcc, Input, Output, State, ctx
import plotly.graph_objects as go

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Dash(__name__, suppress_callback_exceptions=True, title="Crypto Research")

# ── Design tokens ─────────────────────────────────────────────────────────────
C = {
    "bg":       "#07080f",
    "surface":  "#111420",
    "card":     "#161925",
    "border":   "#222538",
    "accent":   "#6366f1",
    "accent2":  "#8b5cf6",
    "green":    "#22c55e",
    "red":      "#ef4444",
    "yellow":   "#f59e0b",
    "cyan":     "#06b6d4",
    "text":     "#e2e8f0",
    "muted":    "#64748b",
    "muted2":   "#94a3b8",
}

FONT = "Inter, -apple-system, sans-serif"

# Sections disponibles
SECTIONS = [
    {
        "id":    "prices",
        "icon":  "",
        "title": "Prix et rendements",
        "desc":  "OHLCV daily, log-rendements, volatilite rolling, correlations",
        "color": C["cyan"],
        "status": "disponible",
    },
    {
        "id":    "buckets",
        "icon":  "",
        "title": "Flux de transactions",
        "desc":  "Taille des ordres par bucket, retail vs institutionnel, 5 exchanges",
        "color": C["accent"],
        "status": "disponible",
    },
    {
        "id":    "stability",
        "icon":  "",
        "title": "Stabilite du marche",
        "desc":  "Facteur latent DFM, choc eGARCH, QR rolling 18 mois, classification FI/FF",
        "color": C["yellow"],
        "status": "disponible",
    },
]
# CBDC section ajoutee quand la pipeline bis_speeches.py sera prete

STATUS_STYLE = {
    "disponible": {"bg": "#16201a", "color": C["green"],  "label": "Disponible"},
    "bientot":    {"bg": "#1c1a10", "color": C["yellow"], "label": "Bientot"},
    "calcul":     {"bg": "#1a1530", "color": C["accent"], "label": "En calcul"},
}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers UI
# ══════════════════════════════════════════════════════════════════════════════

def card_stat(label, value, color=None):
    return html.Div(style={
        "backgroundColor": C["card"], "border": f"1px solid {C['border']}",
        "borderRadius": "8px", "padding": "16px 20px", "minWidth": "140px",
    }, children=[
        html.P(label, style={"margin": 0, "fontSize": "11px", "color": C["muted"],
                              "textTransform": "uppercase", "letterSpacing": "0.05em"}),
        html.P(value, style={"margin": "4px 0 0 0", "fontSize": "22px",
                              "fontWeight": "700", "color": color or C["text"]}),
    ])


def section_header(title, icon="", on_back=True):
    back = html.Button("Accueil", id="btn-back", n_clicks=0, style={
        "background": "none", "border": f"1px solid {C['border']}",
        "color": C["muted2"], "cursor": "pointer", "padding": "6px 14px",
        "borderRadius": "6px", "fontSize": "13px", "fontFamily": FONT,
    }) if on_back else html.Div()
    return html.Div(style={
        "display": "flex", "alignItems": "center", "gap": "16px",
        "marginBottom": "28px", "paddingBottom": "20px",
        "borderBottom": f"1px solid {C['border']}",
    }, children=[
        back,
        html.Span(icon, style={"fontSize": "24px"}),
        html.H2(title, style={"margin": 0, "fontSize": "20px", "fontWeight": "600"}),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Page d'accueil
# ══════════════════════════════════════════════════════════════════════════════

def layout_home():
    cards = []
    for s in SECTIONS:
        st = STATUS_STYLE[s["status"]]
        cards.append(html.Div(
            id={"type": "nav-card", "section": s["id"]},
            n_clicks=0,
            style={
                "backgroundColor": C["card"],
                "border": f"1px solid {C['border']}",
                "borderRadius": "12px",
                "padding": "28px",
                "cursor": "pointer" if s["status"] != "bientot" else "default",
                "transition": "border-color 0.15s",
                "borderTop": f"3px solid {s['color']}",
            },
            children=[
                html.Div(style={"display": "flex", "justifyContent": "space-between",
                                "alignItems": "flex-start", "marginBottom": "16px"}, children=[
                    html.Span(s["icon"], style={"fontSize": "32px"}),
                    html.Span(st["label"], style={
                        "backgroundColor": st["bg"], "color": st["color"],
                        "fontSize": "11px", "padding": "3px 10px",
                        "borderRadius": "20px", "fontWeight": "500",
                    }),
                ]),
                html.H3(s["title"], style={"margin": "0 0 8px 0", "fontSize": "17px",
                                            "fontWeight": "600", "color": C["text"]}),
                html.P(s["desc"], style={"margin": 0, "fontSize": "13px",
                                          "color": C["muted2"], "lineHeight": "1.6"}),
            ]
        ))

    return html.Div([
        html.Div(style={"marginBottom": "40px"}, children=[
            html.H1("Crypto Research", style={
                "margin": "0 0 6px 0", "fontSize": "28px", "fontWeight": "700",
            }),
            html.P("Donnees live — Analyses reproductibles",
                   style={"margin": 0, "color": C["muted2"], "fontSize": "14px"}),
        ]),
        html.Div(style={
            "display": "grid",
            "gridTemplateColumns": "repeat(auto-fill, minmax(280px, 1fr))",
            "gap": "20px",
        }, children=cards),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section Prix & Rendements
# ══════════════════════════════════════════════════════════════════════════════

def layout_prices():
    assets = ["BTC", "ETH", "XRP", "BNB", "ADA", "TRX", "DOGE"]
    return html.Div([
        section_header("Prix et rendements"),
        html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "20px",
                        "flexWrap": "wrap", "alignItems": "center"}, children=[
            dcc.Dropdown(
                id="price-asset", options=[{"label": a, "value": a} for a in assets],
                value="BTC", clearable=False, multi=False,
                style={"width": "140px", "backgroundColor": C["card"]},
            ),
            dcc.RadioItems(
                id="price-period",
                options=[
                    {"label": "1 an",   "value": "1y"},
                    {"label": "3 ans",  "value": "3y"},
                    {"label": "Tout",   "value": "all"},
                ],
                value="1y",
                inline=True,
                style={"color": C["muted2"], "fontSize": "13px"},
                inputStyle={"marginRight": "4px", "marginLeft": "12px"},
            ),
        ]),
        dcc.Loading(type="circle", color=C["accent"],
                    children=html.Div(id="price-graphs")),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section Flux de transactions
# ══════════════════════════════════════════════════════════════════════════════

def layout_buckets():
    return html.Div([
        section_header("Flux de transactions"),
        html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "20px",
                        "flexWrap": "wrap"}, children=[
            dcc.Dropdown(
                id="bucket-asset",
                options=[{"label": a, "value": a} for a in ["BTC", "ETH", "XRP", "BNB", "LTC"]],
                value="BTC", clearable=False,
                style={"width": "120px", "backgroundColor": C["card"]},
            ),
            dcc.Dropdown(
                id="bucket-exchange",
                options=[
                    {"label": "Cross-exchange",  "value": "cross_exchange"},
                    {"label": "Binance",         "value": "binance"},
                    {"label": "Bybit",           "value": "bybit"},
                    {"label": "OKX",             "value": "okx"},
                    {"label": "Kraken",          "value": "kraken"},
                    {"label": "Coinbase",        "value": "coinbase"},
                ],
                value="cross_exchange", clearable=False,
                style={"width": "180px", "backgroundColor": C["card"]},
            ),
        ]),
        dcc.Loading(type="circle", color=C["accent"],
                    children=html.Div(id="bucket-graphs")),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section Stabilité du marché
# ══════════════════════════════════════════════════════════════════════════════

def layout_stability():
    assets = ["BTC", "ETH", "XRP", "BNB", "ADA", "TRX", "DOGE"]
    return html.Div([
        section_header("Stabilite du marche"),
        html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "20px"}, children=[
            dcc.Dropdown(
                id="stab-asset",
                options=[{"label": a, "value": a} for a in assets],
                value="BTC", clearable=False,
                style={"width": "120px", "backgroundColor": C["card"]},
            ),
        ]),
        dcc.Loading(type="circle", color=C["accent"],
                    children=html.Div(id="stability-graphs")),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Layout global
# ══════════════════════════════════════════════════════════════════════════════

app.layout = html.Div(
    style={"backgroundColor": C["bg"], "minHeight": "100vh",
           "fontFamily": FONT, "color": C["text"]},
    children=[
        dcc.Store(id="current-section", data="home"),

        # Barre de navigation top
        html.Div(style={
            "backgroundColor": C["surface"],
            "borderBottom": f"1px solid {C['border']}",
            "padding": "0 32px",
            "display": "flex", "alignItems": "center", "height": "52px",
            "position": "sticky", "top": 0, "zIndex": 100,
        }, children=[
            html.Span("Crypto Research", style={"fontWeight": "600", "fontSize": "15px",
                                                 "color": C["text"]}),
        ]),

        # Contenu principal
        html.Div(id="page-content",
                 style={"maxWidth": "1200px", "margin": "0 auto",
                        "padding": "40px 32px"}),
    ]
)


# ══════════════════════════════════════════════════════════════════════════════
# Routing : navigation
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("current-section", "data"),
    Input({"type": "nav-card", "section": "prices"},    "n_clicks"),
    Input({"type": "nav-card", "section": "buckets"},   "n_clicks"),
    Input({"type": "nav-card", "section": "stability"}, "n_clicks"),
    Input("btn-back", "n_clicks"),
    prevent_initial_call=True,
)
def navigate(*_):
    tid = ctx.triggered_id
    if tid == "btn-back":
        return "home"
    if isinstance(tid, dict):
        return tid.get("section", "home")
    return "home"


@app.callback(
    Output("page-content", "children"),
    Input("current-section", "data"),
)
def render_section(section):
    if section == "prices":    return layout_prices()
    if section == "buckets":   return layout_buckets()
    if section == "stability": return layout_stability()
    return layout_home()


# ══════════════════════════════════════════════════════════════════════════════
# Données : Prix & Rendements
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("price-graphs", "children"),
    Input("price-asset", "value"),
    Input("price-period", "value"),
)
def update_prices(asset, period):
    try:
        from pipelines.crypto_prices import load_prices
        import pandas as pd, numpy as np
        df_all = load_prices()
        df = df_all[df_all["symbol"] == asset].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        cutoff = {"1y": 365, "3y": 365*3, "all": 99999}[period]
        df = df.tail(cutoff)

        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
        df["vol_30"]  = df["log_ret"].rolling(30).std() * np.sqrt(252) * 100

        last  = df["close"].iloc[-1]
        ret1y = (df["close"].iloc[-1] / df["close"].iloc[max(-252, -len(df))]) - 1
        vol   = df["vol_30"].iloc[-1]

        stats = html.Div(style={"display": "flex", "gap": "12px",
                                 "marginBottom": "20px", "flexWrap": "wrap"}, children=[
            card_stat("Dernier prix", f"${last:,.0f}"),
            card_stat("Rendement 1 an", f"{ret1y:+.1%}",
                      C["green"] if ret1y > 0 else C["red"]),
            card_stat("Volatilité ann. 30j", f"{vol:.1f}%", C["yellow"]),
        ])

        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=df["date"], y=df["close"], name="Prix",
                                    line={"color": C["cyan"], "width": 1.5}))
        fig_p.update_layout(**_fig_layout(f"{asset} — Prix de clôture (USD)", 320))

        fig_v = go.Figure()
        fig_v.add_trace(go.Scatter(x=df["date"], y=df["vol_30"],
                                    name="Vol. réalisée 30j (ann.)",
                                    line={"color": C["yellow"], "width": 1.5},
                                    fill="tozeroy", fillcolor="rgba(245,158,11,0.08)"))
        fig_v.update_layout(**_fig_layout(f"{asset} — Volatilité annualisée 30j (%)", 240))

        return html.Div([stats, dcc.Graph(figure=fig_p), dcc.Graph(figure=fig_v)])

    except Exception as e:
        return _error(f"Erreur chargement prix : {e}",
                      "Lance d'abord : python -m pipelines.crypto_prices")


# ══════════════════════════════════════════════════════════════════════════════
# Données : Bucket Analysis
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
    base = (MONTHLY_DIR / "aggregated" / asset if exchange == "cross_exchange"
            else MONTHLY_DIR / exchange / asset)
    parquets = sorted(base.glob("*.parquet")) if base.exists() else []

    if not parquets:
        return _info(
            f"Aucune donnée pour {asset} / {exchange}.",
            "Le pipeline bucket est en cours ou n'a pas encore été lancé."
        )

    df = pd.concat([pd.read_parquet(p) for p in parquets], ignore_index=True)
    df["ym"] = df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)
    df = df.sort_values("ym")

    COLORS = {
        "B1_micro_retail":  "#64748b",
        "B2_retail":        "#3b82f6",
        "B3_semi_inst":     "#8b5cf6",
        "B4_institutional": C["yellow"],
        "B5_whale":         C["red"],
    }
    LABELS = {
        "B1_micro_retail":  "< $1K",
        "B2_retail":        "$1K–$10K",
        "B3_semi_inst":     "$10K–$100K",
        "B4_institutional": "$100K–$1M",
        "B5_whale":         "> $1M",
    }

    # Stats dernière période disponible
    last_ym   = df["ym"].max()
    last_data = df[df["ym"] == last_ym]
    whale_pct = last_data[last_data["bucket"] == "B5_whale"]["volume_share_pct"].sum()
    inst_pct  = last_data[last_data["bucket"].isin(["B4_institutional", "B5_whale"])]["volume_share_pct"].sum()
    retail_pct = last_data[last_data["bucket"].isin(["B1_micro_retail", "B2_retail"])]["volume_share_pct"].sum()

    stats = html.Div(style={"display": "flex", "gap": "12px",
                             "marginBottom": "20px", "flexWrap": "wrap"}, children=[
        card_stat(f"Whale (>{1}M$)", f"{whale_pct:.1f}%", C["red"]),
        card_stat("Institutionnel+", f"{inst_pct:.1f}%", C["yellow"]),
        card_stat("Retail (<$10K)",  f"{retail_pct:.1f}%", C["cyan"]),
        card_stat("Période",         last_ym, C["muted2"]),
    ])

    fig_v = go.Figure()
    fig_c = go.Figure()
    for b, color in COLORS.items():
        sub = df[df["bucket"] == b]
        lbl = LABELS.get(b, b)
        fig_v.add_trace(go.Bar(x=sub["ym"], y=sub["volume_share_pct"],
                                name=lbl, marker_color=color))
        fig_c.add_trace(go.Bar(x=sub["ym"], y=sub["count_share_pct"],
                                name=lbl, marker_color=color, showlegend=False))

    fig_v.update_layout(barmode="stack", legend=dict(orientation="h", y=-0.25),
                        yaxis_title="% volume",
                        **_fig_layout(f"{asset} — Part de volume par bucket ({exchange})", 380))
    fig_c.update_layout(barmode="stack", yaxis_title="% nombre de trades",
                        **_fig_layout(f"{asset} — Part du nombre de trades ({exchange})", 300))

    return html.Div([stats, dcc.Graph(figure=fig_v), dcc.Graph(figure=fig_c)])


# ══════════════════════════════════════════════════════════════════════════════
# Données : CryptoStability
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("stability-graphs", "children"),
    Input("stab-asset", "value"),
)
def update_stability(asset):
    try:
        from pipelines.crypto_stability import load_stability
        result = load_stability()
    except ModuleNotFoundError as e:
        return _error(
            f"Package manquant : {e}",
            "conda install scikit-learn statsmodels -c conda-forge -y   puis   pip install arch"
        )
    except Exception as e:
        return _error(f"Erreur CryptoStability : {e}",
                      "Lance d'abord : python -m pipelines.crypto_stability")

    factor = result["factor"]
    shock  = result["shock"]
    fi_ff  = result["fi_ff"]

    asset_fi = fi_ff[fi_ff["asset"] == asset].sort_values("date")
    latest   = asset_fi.tail(1)
    classif  = latest["classification"].values[0] if len(latest) else "NC"
    classif_color = {"FI": C["green"], "FF": C["red"], "NC": C["muted"]}.get(classif, C["muted"])

    # Compter les FI/FF par date (vue marché global)
    counts = fi_ff.groupby(["date", "classification"]).size().unstack(fill_value=0).reset_index()

    stats = html.Div(style={"display": "flex", "gap": "12px",
                             "marginBottom": "20px", "flexWrap": "wrap"}, children=[
        card_stat(f"{asset} — Classification", classif, classif_color),
        card_stat("Dernière fenêtre",
                  str(asset_fi["date"].max().date()) if len(asset_fi) else "—"),
    ])

    # Facteur + choc
    fig_ts = go.Figure()
    fig_ts.add_trace(go.Scatter(x=factor.index, y=factor.values,
                                 name="Facteur latent",
                                 line={"color": C["accent"], "width": 1.5}))
    fig_ts.add_trace(go.Scatter(x=shock.index, y=shock.values,
                                 name="Choc eGARCH", yaxis="y2",
                                 line={"color": C["yellow"], "width": 1}, opacity=0.6))
    fig_ts.update_layout(
        yaxis2=dict(overlaying="y", side="right", title="Choc"),
        legend=dict(orientation="h", y=-0.2),
        **_fig_layout("Facteur latent + choc eGARCH(1,1)", 340),
    )

    # Beta QR rolling
    fig_qr = go.Figure()
    for col, label, color in [
        ("beta_05", "β(τ=0.05)", C["red"]),
        ("beta_50", "β(τ=0.50)", C["muted2"]),
        ("beta_95", "β(τ=0.95)", C["green"]),
    ]:
        if col in asset_fi.columns:
            fig_qr.add_trace(go.Scatter(x=asset_fi["date"], y=asset_fi[col],
                                         name=label, line={"color": color, "width": 1.5}))
    fig_qr.add_hline(y=0, line_dash="dash", line_color=C["border"])
    fig_qr.update_layout(
        legend=dict(orientation="h", y=-0.2),
        yaxis_title="β",
        **_fig_layout(f"{asset} — Coefficients QR rolling (fenêtre 18 mois)", 320),
    )

    # Nb actifs FI/FF par date
    fig_ff = go.Figure()
    for col, color, label in [("FI", C["green"], "FI"), ("FF", C["red"], "FF")]:
        if col in counts.columns:
            fig_ff.add_trace(go.Bar(x=counts["date"], y=counts[col],
                                    name=label, marker_color=color))
    fig_ff.update_layout(barmode="stack", yaxis_title="Nombre d'actifs",
                          **_fig_layout("Nombre d'actifs FI / FF par fenêtre", 260))

    return html.Div([stats, dcc.Graph(figure=fig_ts),
                     dcc.Graph(figure=fig_qr), dcc.Graph(figure=fig_ff)])


# ══════════════════════════════════════════════════════════════════════════════
# Helpers graphiques
# ══════════════════════════════════════════════════════════════════════════════

def _fig_layout(title, height):
    return dict(
        title=dict(text=title, font=dict(size=14, color=C["text"])),
        template="plotly_dark",
        paper_bgcolor=C["card"],
        plot_bgcolor=C["card"],
        margin=dict(l=48, r=24, t=44, b=40),
        height=height,
        font=dict(family=FONT, color=C["muted2"]),
        xaxis=dict(gridcolor=C["border"], linecolor=C["border"]),
        yaxis=dict(gridcolor=C["border"], linecolor=C["border"]),
    )

def _error(msg, hint=None):
    return html.Div(style={
        "backgroundColor": "#1f0f0f", "border": f"1px solid {C['red']}",
        "borderRadius": "8px", "padding": "20px",
    }, children=[
        html.P(msg, style={"color": C["red"], "margin": 0, "fontWeight": "500"}),
        html.Code(hint, style={"color": C["muted2"], "fontSize": "12px",
                                "display": "block", "marginTop": "8px"}) if hint else None,
    ])

def _info(msg, sub=None):
    return html.Div(style={
        "backgroundColor": C["card"], "border": f"1px solid {C['border']}",
        "borderRadius": "8px", "padding": "32px", "textAlign": "center",
    }, children=[
        html.P(msg, style={"color": C["muted2"], "margin": 0}),
        html.P(sub, style={"color": C["muted"], "fontSize": "12px",
                            "marginTop": "6px"}) if sub else None,
    ])


if __name__ == "__main__":
    app.run(debug=False, port=8050)
