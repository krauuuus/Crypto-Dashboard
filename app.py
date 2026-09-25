"""
app.py — Crypto Research Dashboard
Page d'accueil + 4 sections orientées données :
  Prix & Rendements | Flux de transactions | Stabilité du marché | Banques centrales
"""

import logging
from pathlib import Path
import dash
from dash import Dash, html, dcc, Input, Output, State, ctx, ALL
import plotly.graph_objects as go

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Dash(__name__, suppress_callback_exceptions=True, title="Crypto Research")

# Pull du data repo au demarrage (une seule fois, lecture disque ensuite)
try:
    from pipelines.data_repo import pull as _data_pull
    _data_pull()
except Exception as _e:
    log.warning(f"data_repo pull skipped au demarrage : {_e}")

# ── Design tokens ─────────────────────────────────────────────────────────────
C = {
    "bg":       "#f5f6f8",
    "surface":  "#ffffff",
    "card":     "#ffffff",
    "border":   "#e2e8f0",
    "accent":   "#4f46e5",
    "accent2":  "#7c3aed",
    "green":    "#16a34a",
    "red":      "#dc2626",
    "yellow":   "#d97706",
    "cyan":     "#0891b2",
    "text":     "#1e293b",
    "muted":    "#64748b",
    "muted2":   "#94a3b8",
    # couleurs papier CBDC
    "cbdc_blue": "#2563eb",
    "cbdc_red":  "#dc2626",
}

FONT = "Inter, -apple-system, sans-serif"

# Sections disponibles
SECTIONS = [
    {
        "id":    "prices",
        "icon":  "",
        "title": "Prices & Returns",
        "desc":  "OHLCV daily, log-returns, rolling volatility, correlations",
        "color": C["cyan"],
        "status": "disponible",
    },
    {
        "id":    "buckets",
        "icon":  "",
        "title": "Transaction Flows",
        "desc":  "Order size by bucket, retail vs institutional, 5 exchanges",
        "color": C["accent"],
        "status": "disponible",
    },
    {
        "id":    "stability",
        "icon":  "",
        "title": "Market Stability",
        "desc":  "DFM latent factor, eGARCH shock, 18-month rolling QR, FI/FF classification",
        "color": C["yellow"],
        "status": "disponible",
    },
    {
        "id":    "cbdc",
        "icon":  "",
        "title": "Central Banks",
        "desc":  "Monthly CBDC indices from BIS speeches, BERT + Jev classification",
        "color": C["green"],
        "status": "disponible",
    },
]

STATUS_STYLE = {
    "disponible": {"bg": "#f0fdf4", "color": C["green"],  "label": "Available"},
    "bientot":    {"bg": "#fffbeb", "color": C["yellow"], "label": "Coming soon"},
    "calcul":     {"bg": "#eef2ff", "color": C["accent"], "label": "Computing"},
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


def section_header(title, icon=""):
    return html.Div(style={
        "display": "flex", "alignItems": "center", "gap": "16px",
        "marginBottom": "28px", "paddingBottom": "20px",
        "borderBottom": f"1px solid {C['border']}",
    }, children=[
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
            html.P("Live data — Reproducible research",
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
        section_header("Prices & Returns"),
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
                    {"label": "1Y",  "value": "1y"},
                    {"label": "3Y",  "value": "3y"},
                    {"label": "All", "value": "all"},
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
    _dd_style = {"backgroundColor": C["card"], "color": C["text"]}
    return html.Div([
        section_header("Transaction Flows"),
        html.Div(style={"display": "flex", "gap": "16px", "marginBottom": "20px",
                        "flexWrap": "wrap", "alignItems": "center"}, children=[
            dcc.Dropdown(
                id="bucket-asset",
                options=[{"label": a, "value": a} for a in ["BTC", "ETH", "XRP", "LTC"]],
                value="BTC", clearable=False, style={"width": "120px", **_dd_style},
            ),
            html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px"}, children=[
                html.Span("Trade sizes:", style={"color": C["muted2"], "fontSize": "13px",
                                                 "whiteSpace": "nowrap"}),
                dcc.Dropdown(
                    id="bucket-cuts",
                    options=[{"label": v, "value": v}
                             for v in ["$1K", "$10K", "$100K", "$1M"]],
                    value=["$1K", "$10K", "$100K", "$1M"],
                    multi=True,
                    clearable=False,
                    placeholder="Select trade sizes…",
                    style={"minWidth": "280px", "backgroundColor": C["card"],
                           "color": C["text"]},
                ),
            ]),
        ]),
        dcc.Loading(type="circle", color=C["accent"],
                    children=html.Div(id="bucket-graphs")),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section Stabilité du marché
# ══════════════════════════════════════════════════════════════════════════════

def layout_stability():
    return html.Div([
        section_header("Market Stability"),
        dcc.Loading(type="circle", color=C["accent"],
                    children=html.Div(id="stability-graphs")),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Section Banques centrales
# ══════════════════════════════════════════════════════════════════════════════

def layout_cbdc():
    return html.Div([
        section_header("Central Banks"),
        html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "20px",
                        "flexWrap": "wrap", "alignItems": "center"}, children=[
            dcc.RadioItems(
                id="cbdc-model",
                options=[
                    {"label": "BERT",        "value": "bert"},
                    {"label": "Jev",         "value": "jev"},
                    {"label": "Comparison",  "value": "compare"},
                ],
                value="bert",
                inline=True,
                style={"color": C["muted2"], "fontSize": "13px"},
                inputStyle={"marginRight": "4px", "marginLeft": "12px"},
            ),
        ]),
        dcc.Loading(type="circle", color=C["accent"],
                    children=html.Div(id="cbdc-graphs")),
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
            "display": "flex", "alignItems": "center", "gap": "16px",
            "height": "52px", "position": "sticky", "top": 0, "zIndex": 100,
        }, children=[
            html.Button("← Home", id="btn-back", n_clicks=0, style={
                "display": "none",
                "background": "none", "border": f"1px solid {C['border']}",
                "color": C["muted2"], "cursor": "pointer", "padding": "6px 14px",
                "borderRadius": "6px", "fontSize": "13px", "fontFamily": FONT,
            }),
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
    Input({"type": "nav-card", "section": ALL}, "n_clicks"),
    Input("btn-back", "n_clicks"),
    prevent_initial_call=True,
)
def navigate(card_clicks, back_clicks):
    tid = ctx.triggered_id
    if tid == "btn-back":
        return "home"
    if isinstance(tid, dict):
        return tid.get("section", "home")
    return "home"


@app.callback(
    Output("btn-back", "style"),
    Input("current-section", "data"),
)
def toggle_back_button(section):
    base = {
        "background": "none", "border": f"1px solid {C['border']}",
        "color": C["muted2"], "cursor": "pointer", "padding": "6px 14px",
        "borderRadius": "6px", "fontSize": "13px", "fontFamily": FONT,
    }
    if section == "home":
        return {**base, "display": "none"}
    return {**base, "display": "inline-block"}


@app.callback(
    Output("page-content", "children"),
    Input("current-section", "data"),
)
def render_section(section):
    if section == "prices":    return layout_prices()
    if section == "buckets":   return layout_buckets()
    if section == "stability": return layout_stability()
    if section == "cbdc":      return layout_cbdc()
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

_GROUP_COLORS = ["#e5e7eb", "#9ca3af", "#4b5563", "#1f2937", "#111827"]

# ── Standard 5-bucket data (multi-exchange) ───────────────────────────────────
_BUCKET_KEYS    = ["B1_micro_retail", "B2_retail", "B3_semi_inst", "B4_institutional", "B5_whale"]
_CUT_THRESHOLDS = ["$1K", "$10K", "$100K", "$1M"]

# ── Fine 18-bucket data (Binance, aggTrades) ──────────────────────────────────
_FINE_CSV  = Path(__file__).parent / "data" / "raw" / "buckets" / "aggTrades_monthly_fine.csv"
_FINE_COLS = ["<$1k","$1k-$5k","$5k-$10k","$10k-$20k","$20k-$30k","$30k-$40k",
              "$40k-$50k","$50k-$60k","$60k-$70k","$70k-$80k","$80k-$90k","$90k-$100k",
              "$100k-$200k","$200k-$300k","$300k-$400k","$400k-$500k","$500k-$1M",">$1M"]
_FINE_CUTS = ["$1k","$5k","$10k","$20k","$30k","$40k","$50k","$60k","$70k",
              "$80k","$90k","$100k","$200k","$300k","$400k","$500k","$1M"]
_FINE_SYM  = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "XRP": "XRPUSDT", "LTC": "LTCUSDT"}


def _has_fine_data(asset: str) -> bool:
    return _FINE_CSV.exists() and asset in _FINE_SYM


def _assign_colors(groups: list) -> list:
    n = len(groups)
    for i, g in enumerate(groups):
        ci = round(i * (len(_GROUP_COLORS) - 1) / max(n - 1, 1))
        g["color"] = _GROUP_COLORS[ci]
    return groups


def _build_groups(selected_cuts):
    """Groups from 5-bucket parquet data (multi-exchange)."""
    selected = set(selected_cuts or _CUT_THRESHOLDS)
    groups, current, prev_t = [], [], None
    for i, bucket in enumerate(_BUCKET_KEYS):
        current.append(bucket)
        cut_here = i < len(_CUT_THRESHOLDS) and _CUT_THRESHOLDS[i] in selected
        is_last  = (i == len(_BUCKET_KEYS) - 1)
        if cut_here or is_last:
            end_t = _CUT_THRESHOLDS[i] if cut_here else None
            if   prev_t is None and end_t:  label = f"< {end_t}"
            elif end_t  is None:             label = f"> {prev_t}" if prev_t else "Tous"
            else:                            label = f"{prev_t} - {end_t}"
            groups.append({"label": label, "buckets": current.copy()})
            current, prev_t = [], end_t if cut_here else prev_t
    return _assign_colors(groups)


def _build_fine_groups(selected_cuts):
    """Groups from fine 18-bucket CSV data (Binance)."""
    selected = set(c.lower() for c in (selected_cuts or _FINE_CUTS))
    groups, current_cols, prev_t = [], [], None
    for i, col in enumerate(_FINE_COLS):
        current_cols.append(col)
        cut_here = i < len(_FINE_CUTS) and _FINE_CUTS[i].lower() in selected
        is_last  = (i == len(_FINE_COLS) - 1)
        if cut_here or is_last:
            end_t = _FINE_CUTS[i] if cut_here else None
            if   prev_t is None and end_t:  label = f"< {end_t}"
            elif end_t  is None:             label = f"> {prev_t}" if prev_t else "Tous"
            else:                            label = f"{prev_t} - {end_t}"
            groups.append({"label": label, "cols": current_cols.copy()})
            current_cols, prev_t = [], end_t if cut_here else prev_t
    return _assign_colors(groups)


@app.callback(
    Output("bucket-cuts", "options"),
    Output("bucket-cuts", "value"),
    Input("bucket-asset", "value"),
)
def update_cut_options(asset):
    opts = [{"label": v, "value": v} for v in _FINE_CUTS]
    return opts, ["$10k", "$100k"]


@app.callback(
    Output("bucket-graphs", "children"),
    Input("bucket-asset", "value"),
    Input("bucket-cuts", "value"),
)
def update_buckets(asset, selected_cuts):
    try:
        return _update_buckets_inner(asset, selected_cuts)
    except Exception as exc:
        import traceback
        return _error(f"Erreur buckets : {exc}", traceback.format_exc())


def _update_buckets_inner(asset, selected_cuts):
    import pandas as pd

    df_raw = pd.read_csv(_FINE_CSV, encoding="utf-8-sig")
    df_raw = df_raw[df_raw["symbol"] == _FINE_SYM.get(asset, "")].copy()
    if df_raw.empty:
        return _info(f"Aucune donnée pour {asset} sur Binance.",
                     "Symboles disponibles : BTC, ETH, XRP, LTC.")
    df_raw["ym"] = df_raw["ym"]
    groups  = _build_fine_groups(selected_cuts)
    x_vals  = sorted(df_raw["ym"].unique())
    agg_frames = []
    for g in groups:
        valid_cols = [c for c in g["cols"] if c in df_raw.columns]
        sub = df_raw[["ym", "total_usd_volume"] + valid_cols].copy()
        sub["vol_grp"] = sub[valid_cols].sum(axis=1)
        sub["volume_share_pct"] = sub["vol_grp"] / sub["total_usd_volume"] * 100
        sub["group_label"] = g["label"]
        sub["color"]       = g["color"]
        agg_frames.append(sub[["ym", "volume_share_pct", "group_label", "color"]])
    df_agg = pd.concat(agg_frames, ignore_index=True)

    last_ym   = df_raw["ym"].max()
    last_data = df_raw[df_raw["ym"] == last_ym]
    tot = last_data["total_usd_volume"].sum()
    small_cols = [c for c in ["<$1k","$1k-$5k","$5k-$10k"] if c in last_data.columns]
    large_cols = [c for c in ["$100k-$200k","$200k-$300k","$300k-$400k",
                               "$400k-$500k","$500k-$1M",">$1M"] if c in last_data.columns]
    whale_cols = [c for c in [">$1M"] if c in last_data.columns]
    small_pct = last_data[small_cols].sum().sum() / tot * 100 if tot else 0
    large_pct = last_data[large_cols].sum().sum() / tot * 100 if tot else 0
    top_pct   = last_data[whale_cols].sum().sum() / tot * 100 if tot else 0
    data_label = f"Binance · {len(df_raw)} mois"

    stats = html.Div(style={"display": "flex", "gap": "12px",
                             "marginBottom": "20px", "flexWrap": "wrap"}, children=[
        card_stat("< $10K",  f"{small_pct:.1f}%",  "#3b82f6"),
        card_stat("> $100K", f"{large_pct:.1f}%",  C["yellow"]),
        card_stat("> $1M",   f"{top_pct:.1f}%",    C["red"]),
        card_stat("Période", last_ym,               C["muted2"]),
        card_stat("Source",  data_label,            C["muted2"]),
    ])

    def _area(x, y, name, color, showlegend=True):
        return go.Scatter(x=x, y=y, name=name, mode="lines",
                          stackgroup="one", fillcolor=color,
                          line=dict(color=color, width=0.5),
                          showlegend=showlegend)

    fig_v = go.Figure()
    for g in groups:
        sub = df_agg[df_agg["group_label"] == g["label"]]
        sub = sub.set_index("ym").reindex(x_vals).reset_index()
        fig_v.add_trace(_area(sub["ym"], sub["volume_share_pct"].fillna(0),
                              g["label"], g["color"]))

    fig_v.update_layout(legend=dict(orientation="h", y=-0.25),
                        yaxis_title="Share (%)", yaxis_range=[0, 100],
                        xaxis_type="category",
                        **_fig_layout(f"{asset} — Trade-size composition (Binance)", 380))
    return html.Div([stats, dcc.Graph(figure=fig_v)])


# ══════════════════════════════════════════════════════════════════════════════
# Données : CryptoStability
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("stability-graphs", "children"),
    Input("current-section", "data"),
)
def update_stability(section):
    if section != "stability":
        return dash.no_update
    import pandas as pd
    try:
        from pipelines.crypto_stability import load_stability
        result = load_stability()
    except Exception as e:
        return _error(f"CryptoStability error: {e}",
                      "Run first: python -m pipelines.crypto_stability")

    factor = result["factor"]
    share  = result["fi_ff_share"].copy()
    share["date"] = pd.to_datetime(share["date"])

    last_row = share.iloc[-1]
    n_total  = int(last_row["n_assets"])
    n_fi     = round(last_row["fi_share"] * n_total)
    n_ff     = round(last_row["ff_share"] * n_total)
    last_win = str(share["date"].max().date())

    stats = html.Div(style={"display": "flex", "gap": "12px",
                             "marginBottom": "20px", "flexWrap": "wrap"}, children=[
        card_stat("FI assets (latest window)", f"{n_fi}/{n_total}", C["red"]),
        card_stat("FF assets (latest window)", f"{n_ff}/{n_total}", "#3b82f6"),
        card_stat("Assets covered", str(n_total), C["muted2"]),
        card_stat("Latest window", last_win, C["muted2"]),
    ])

    # Common factor (daily PC1)
    factor_level = factor.cumsum()
    fig_factor = go.Figure()
    fig_factor.add_trace(go.Scatter(
        x=pd.to_datetime(factor_level.index), y=factor_level.values,
        mode="lines", line={"color": C["accent"], "width": 1.5}, showlegend=False,
    ))
    fig_factor.update_layout(
        yaxis_title="Cumulative PC1",
        **_fig_layout("Common Market Factor — level (cumulative PC1)", 280),
    )

    # FI / FF share time series
    fig_agg = go.Figure()

    # Fond gris : market cap globale crypto (mensuel, trillion $)
    try:
        from pipelines.global_market_cap import load_global_market_cap
        mcap = load_global_market_cap()
        mcap.index = pd.to_datetime(mcap.index)
        mcap_m = mcap.resample("ME").last().dropna()
        mcap_m.index = mcap_m.index.to_period("M").to_timestamp()
        fig_agg.add_trace(go.Scatter(
            x=mcap_m.index, y=mcap_m.values / 1e12,
            name="Market cap (T$)", yaxis="y2", mode="none",
            fill="tozeroy", fillcolor="rgba(100,116,139,0.15)",
            line=dict(color="rgba(100,116,139,0.3)", width=0.5),
        ))
    except Exception:
        pass

    # FI (rouge) et FF (bleu) — mensuel, part sur le panier
    fig_agg.add_trace(go.Scatter(
        x=share["date"], y=(share["fi_share"] * 100).round(1),
        name="Financial Instability (FI)", mode="lines+markers",
        line=dict(color=C["red"], width=2), marker=dict(size=4),
    ))
    fig_agg.add_trace(go.Scatter(
        x=share["date"], y=(share["ff_share"] * 100).round(1),
        name="Financial Fragility (FF)", mode="lines+markers",
        line=dict(color="#3b82f6", width=2), marker=dict(size=4),
    ))

    _fl = _fig_layout("Financial Stability & Fragility — asset share by rolling window (18M)", 380)
    _fl.pop("yaxis", None)
    fig_agg.update_layout(
        yaxis=dict(title="Share of assets (%)", range=[0, 100]),
        yaxis2=dict(title="Market cap (T$)", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero"),
        legend=dict(orientation="h", y=-0.2),
        **_fl,
    )

    # CRIX — indice de marché crypto (type S&P 500 crypto)
    crix_graph = None
    try:
        crix_path = Path(r"C:\Users\fkraus\Desktop\DASHBOARD CRYPTO\data\raw\CRIX_data.csv")
        crix_df   = pd.read_csv(crix_path, sep=";", parse_dates=["date"])
        crix_df   = crix_df.dropna(subset=["price"]).sort_values("date")
        fig_crix  = go.Figure()
        fig_crix.add_trace(go.Scatter(
            x=crix_df["date"], y=crix_df["price"].round(1),
            mode="lines", line=dict(color=C["accent2"], width=1.5), showlegend=False,
        ))
        fig_crix.update_layout(
            yaxis_title="CRIX level",
            annotations=[dict(
                text="Source: CRIX — Humboldt-Universität zu Berlin (thecrix.de)",
                xref="paper", yref="paper", x=1, y=-0.12, showarrow=False,
                xanchor="right", font=dict(size=10, color=C["muted2"]),
            )],
            **_fig_layout("CRIX — Crypto Market Index (daily)", 260),
        )
        crix_graph = dcc.Graph(figure=fig_crix)
    except Exception:
        pass

    children = [stats, dcc.Graph(figure=fig_agg), dcc.Graph(figure=fig_factor)]
    if crix_graph is not None:
        children.append(crix_graph)
    return html.Div(children)


# ══════════════════════════════════════════════════════════════════════════════
# Donnees : Banques centrales (CBDC)
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("cbdc-graphs", "children"),
    Input("cbdc-model", "value"),
)
def update_cbdc(model):
    import json as _json
    import numpy as _np
    import pandas as _pd
    from pathlib import Path as _Path

    _DATA = _Path(r"C:\Users\fkraus\Desktop\DASHBOARD CRYPTO\crypto-research-data\cbdc_speeches")
    CUTOFF = "2018-01-01"

    STANCE_NUM = {"Pro-CBDC": 1.0, "Wait-and-See": 0.0, "Anti-CBDC": -1.0}
    SENT_NUM   = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
    TYPE_CATS  = ["General/Unspecified", "Retail CBDC", "Wholesale CBDC"]
    DISC_CATS  = ["Feature", "Process", "Risk-Benefit"]

    # ── Monthly aggregate ────────────────────────────────────────────────────
    try:
        monthly = _pd.read_parquet(_DATA / "cbdc_monthly.parquet")
        monthly["month"] = _pd.to_datetime(monthly["month"])
        monthly = monthly[monthly["month"] >= CUTOFF].copy().reset_index(drop=True)
    except Exception as e:
        return _error(f"Error loading cbdc_monthly: {e}")

    if monthly.empty:
        return _info("No CBDC data available (no data after 2018).")

    MA = 3
    for col, a, b in [
        ("bert_stance", "stance_pro_pct",        "stance_anti_pct"),
        ("bert_sent",   "sentiment_pos_pct",      "sentiment_neg_pct"),
        ("jev_stance",  "jev_stance_pro_pct",     "jev_stance_anti_pct"),
        ("jev_sent",    "jev_sentiment_pos_pct",  "jev_sentiment_neg_pct"),
    ]:
        monthly[col] = (monthly[a] - monthly[b]).rolling(MA, min_periods=1).mean()

    # Stats
    n_speeches  = int(monthly["n_speeches_x"].sum()) if "n_speeches_x" in monthly.columns else 0
    n_sentences = int(monthly["n_sentences_x"].sum()) if "n_sentences_x" in monthly.columns else 0
    last_month  = str(monthly["month"].max())[:7]
    _src = "jev" if model in ("jev", "compare") else "bert"
    recent_stance = round(float(monthly[f"{_src}_stance"].iloc[-1]), 2)
    stance_label  = "Pro" if recent_stance > 0.1 else ("Anti" if recent_stance < -0.1 else "Neutral")
    stance_color  = C["green"] if recent_stance > 0.1 else (C["red"] if recent_stance < -0.1 else C["yellow"])

    stats = html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "20px",
                             "flexWrap": "wrap"}, children=[
        card_stat("Speeches analysed",      f"{n_speeches:,}"),
        card_stat("CBDC sentences",         f"{n_sentences:,}"),
            card_stat(f"Recent stance ({_src.upper()})", f"{stance_label} ({recent_stance:+.2f})", stance_color),
        card_stat("Latest period",          last_month, C["muted2"]),
    ])

    def _zero_line(fig):
        fig.add_hline(y=0, line_dash="dot", line_color=C["muted"], line_width=1)

    # ── Chart principal : Stance + Sentiment ─────────────────────────────────
    fig_main = go.Figure()
    if model in ("bert", "compare"):
        fig_main.add_trace(go.Scatter(
            x=monthly["month"], y=monthly["bert_stance"].round(3),
            name="Stance (BERT)", line={"color": C["cbdc_red"], "width": 2},
        ))
        fig_main.add_trace(go.Scatter(
            x=monthly["month"], y=monthly["bert_sent"].round(3),
            name="Sentiment (BERT)", line={"color": C["cbdc_blue"], "width": 2},
        ))
    if model in ("jev", "compare"):
        dash = "dot" if model == "compare" else "solid"
        fig_main.add_trace(go.Scatter(
            x=monthly["month"], y=monthly["jev_stance"].round(3),
            name="Stance (Jev)", line={"color": C["cbdc_red"], "width": 2 if model == "jev" else 1.5, "dash": dash},
        ))
        fig_main.add_trace(go.Scatter(
            x=monthly["month"], y=monthly["jev_sent"].round(3),
            name="Sentiment (Jev)", line={"color": C["cbdc_blue"], "width": 2 if model == "jev" else 1.5, "dash": dash},
        ))
    _zero_line(fig_main)
    lbl = {"bert": "BERT", "jev": "Jev", "compare": "BERT vs Jev"}.get(model, model.upper())
    fig_main.update_layout(
        legend=dict(orientation="h", y=-0.22, font=dict(size=12)),
        **_fig_layout(f"CBDC Stance & Sentiment — {lbl} ({MA}M MA)", 340),
    )
    fig_main.update_yaxes(title_text="Monthly average score", range=[-1.05, 1.05])

    _muted_style = {"color": C["muted"], "fontSize": "13px", "lineHeight": "1.7",
                    "margin": "0 0 6px 0"}
    _link = lambda txt, url: html.A(txt, href=url, target="_blank", style={
        "color": C["accent"], "textDecoration": "none"})

    if model in ("bert", "compare"):
        method_bert = html.Div(style={
            "backgroundColor": C["bg"], "border": f"1px solid {C['border']}",
            "borderRadius": "8px", "padding": "16px 20px", "marginBottom": "16px",
        }, children=[
            html.P(["CBDC-BERT ", html.Strong("(Zafar et al., 2024)"),
                    " is a suite of fine-tuned classifiers applied to CBDC-related sentences "
                    "from BIS central banker speeches. Each sentence is scored along four dimensions: "
                    "Stance (Pro-CBDC → +1, Wait-and-See → 0, Anti-CBDC → −1), "
                    "Sentiment (positive → +1, neutral → 0, negative → −1), "
                    "Type (Retail / Wholesale / General) and Discourse (Feature / Process / Risk-Benefit). "
                    "The monthly index is the arithmetic mean of sentence-level scores, smoothed over ",
                    html.Strong(f"{MA} months"), ". "
                    "Models on Hugging Face: ",
                    _link("bilalzafar/cbdc-bert-stance", "https://huggingface.co/bilalzafar/cbdc-bert-stance"),
                    ", ",
                    _link("bilalzafar/cbdc-bert-sentiment", "https://huggingface.co/bilalzafar/cbdc-bert-sentiment"),
                    "."],
                   style=_muted_style),
        ])
    else:
        method_bert = None

    if model in ("jev", "compare"):
        method_jev = html.Div(style={
            "backgroundColor": C["bg"], "border": f"1px solid {C['border']}",
            "borderRadius": "8px", "padding": "16px 20px", "marginBottom": "16px",
        }, children=[
            html.P(["Jev is an LLM classifier (TypeSafe model via the JEV API) applied to the same "
                    "CBDC-related sentences, using the same four dimensions and numeric encoding as "
                    "CBDC-BERT. Each prediction includes a confidence score."],
                   style=_muted_style),
        ])
    else:
        method_jev = None

    graphs = [stats,
              *([method_bert] if method_bert else []),
              *([method_jev]  if method_jev  else []),
              dcc.Graph(figure=fig_main)]

    # ── Faceted charts par type et discours ──────────────────────────────────
    try:
        bert = _pd.read_parquet(_DATA / "cbdc_bert_sentences.parquet")
        bert["date"]  = _pd.to_datetime(bert["date"])
        bert = bert[bert["date"] >= CUTOFF].copy()
        bert["month"] = bert["date"].dt.to_period("M").dt.to_timestamp()
        bert["stance_num"] = bert["stance_label"].map(STANCE_NUM)
        bert["sent_num"]   = bert["sentiment_label"].map(SENT_NUM)

        with open(_DATA / "cbdc_jev_checkpoint.jsonl", encoding="utf-8") as _f:
            jev_rows = [_json.loads(l) for l in _f]
        jev = _pd.DataFrame(jev_rows)
        jev["date"]  = _pd.to_datetime(jev["date"])
        jev = jev[jev["date"] >= CUTOFF].copy()
        jev["month"] = jev["date"].dt.to_period("M").dt.to_timestamp()
        jev["stance_num"] = jev["jev_stance"].map(STANCE_NUM)
        jev["sent_num"]   = jev["jev_sentiment"].map(SENT_NUM)

        from plotly.subplots import make_subplots as _msub

        def _facet(df, s_col, r_col, grp_col, cats, title):
            grp = df.groupby(["month", grp_col])[[s_col, r_col]].mean()
            fig = _msub(rows=1, cols=len(cats), subplot_titles=cats,
                        shared_yaxes=True, horizontal_spacing=0.04)
            for i, cat in enumerate(cats, 1):
                try:
                    sub = grp.xs(cat, level=grp_col).rolling(MA, min_periods=1).mean()
                except KeyError:
                    continue
                show = (i == 1)
                fig.add_trace(go.Scatter(
                    x=sub.index, y=sub[s_col].round(3), name="Stance",
                    line={"color": C["cbdc_red"], "width": 1.5},
                    legendgroup="stance", showlegend=show,
                ), row=1, col=i)
                fig.add_trace(go.Scatter(
                    x=sub.index, y=sub[r_col].round(3), name="Sentiment",
                    line={"color": C["cbdc_blue"], "width": 1.5},
                    legendgroup="sent", showlegend=show,
                ), row=1, col=i)
                fig.add_hline(y=0, line_dash="dot", line_color=C["muted"],
                              line_width=1, row=1, col=i)
            base = _fig_layout(title, 320)
            base.pop("xaxis", None); base.pop("yaxis", None)
            fig.update_layout(legend=dict(orientation="h", y=-0.18,
                                          font=dict(size=11)), **base)
            fig.update_xaxes(gridcolor=C["border"], linecolor=C["border"],
                             tickfont=dict(color=C["muted"], size=10))
            fig.update_yaxes(range=[-1.05, 1.05], gridcolor=C["border"],
                             linecolor=C["border"])
            return fig

        if model in ("bert", "compare"):
            graphs += [
                dcc.Graph(figure=_facet(bert, "stance_num", "sent_num", "type_label",
                                        TYPE_CATS, f"Stance & Sentiment by Type — BERT ({MA}M MA)")),
                dcc.Graph(figure=_facet(bert, "stance_num", "sent_num", "discourse_label",
                                        DISC_CATS, f"Stance & Sentiment by Discourse — BERT ({MA}M MA)")),
            ]
        if model in ("jev", "compare"):
            graphs += [
                dcc.Graph(figure=_facet(jev, "stance_num", "sent_num", "jev_type",
                                        TYPE_CATS, f"Stance & Sentiment by Type — Jev ({MA}M MA)")),
                dcc.Graph(figure=_facet(jev, "stance_num", "sent_num", "jev_discourse",
                                        DISC_CATS, f"Stance & Sentiment by Discourse — Jev ({MA}M MA)")),
            ]

    except Exception as _e:
        graphs.append(_info(f"Breakdown unavailable: {_e}"))

    # Accord BERT/Jev si mode compare
    if model == "compare":
        agree_cols = [c for c in monthly.columns if c.startswith("agree_") and c.endswith("_pct")]
        if agree_cols:
            fig_agree = go.Figure()
            labels_map = {
                "agree_stance_pct":    "Stance",
                "agree_sentiment_pct": "Sentiment",
                "agree_type_pct":      "Type",
                "agree_discourse_pct": "Discourse",
            }
            colors_agree = [C["accent"], C["cyan"], C["yellow"], C["accent2"]]
            for (col, label), color in zip(labels_map.items(), colors_agree):
                if col in monthly.columns:
                    fig_agree.add_trace(go.Scatter(
                        x=monthly["month"],
                        y=(monthly[col] * 100).round(1),
                        name=label,
                        line={"color": color, "width": 1.5},
                    ))
            fig_agree.add_hline(y=80, line_dash="dot", line_color=C["border"],
                                annotation_text="80%")
            fig_agree.update_layout(
                yaxis_title="% agreement",
                yaxis_range=[0, 105],
                legend=dict(orientation="h", y=-0.2),
                **_fig_layout("Monthly BERT / Jev Agreement by Dimension", 300),
            )
            graphs.append(dcc.Graph(figure=fig_agree))

    return html.Div(graphs)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers graphiques
# ══════════════════════════════════════════════════════════════════════════════

def _fig_layout(title, height):
    return dict(
        title=dict(text=title, font=dict(size=14, color=C["text"])),
        template="plotly_white",
        paper_bgcolor=C["card"],
        plot_bgcolor="#fafbfc",
        margin=dict(l=48, r=24, t=44, b=40),
        height=height,
        font=dict(family=FONT, color=C["muted"]),
        xaxis=dict(gridcolor=C["border"], linecolor=C["border"], tickfont=dict(color=C["muted"])),
        yaxis=dict(gridcolor=C["border"], linecolor=C["border"], tickfont=dict(color=C["muted"])),
    )

def _error(msg, hint=None):
    return html.Div(style={
        "backgroundColor": "#fff5f5", "border": f"1px solid {C['red']}",
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


server = app.server  # for gunicorn: gunicorn app:server

if __name__ == "__main__":
    app.run(debug=False, port=8050)
