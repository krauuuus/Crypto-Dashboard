"""
crypto_stability.py — Pipeline CryptoStability (spec noscale_base)
Étapes : PCA(r=1) → eGARCH(1,1) → rolling QR (τ=0.05/0.50/0.95) → FI/FF

Le problème de révision DFM :
  La PCA sur la matrice de corrélation change quand on ajoute des données,
  ce qui révise rétrospectivement toutes les estimations passées du choc.
  Solution : cache avec timestamp 24h — on ré-estime seulement si le cache
  est plus vieux que CACHE_TTL_H heures, sinon on sert les estimations figées.

Packages requis : scikit-learn, arch, statsmodels, numpy, pandas
"""

import datetime
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT

log = logging.getLogger(__name__)

CACHE_FILE  = ROOT / "data" / "cache" / "crypto_stability.parquet"
_CACHE_META = ROOT / "data" / "cache" / "crypto_stability_meta.json"
CACHE_TTL_H = 24

# Paramètres du modèle (alignés sur paths.yml du papier)
WINDOW_MONTHS = 18
TAU_VEC       = [0.05, 0.50, 0.95]
MIN_COVERAGE  = 0.90   # fraction minimum d'observations dans une fenêtre


# ══════════════════════════════════════════════════════════════════════════════
# Cache
# ══════════════════════════════════════════════════════════════════════════════

def _cache_is_fresh() -> bool:
    if not CACHE_FILE.exists() or not _CACHE_META.exists():
        return False
    meta = json.loads(_CACHE_META.read_text())
    age_h = (datetime.datetime.now() -
             datetime.datetime.fromisoformat(meta["fetched_at"])
             ).total_seconds() / 3600
    return age_h < CACHE_TTL_H

def _save_meta() -> None:
    _CACHE_META.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_META.write_text(json.dumps({
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds")
    }))


# ══════════════════════════════════════════════════════════════════════════════
# Étape 1 : DFM via PCA (r=1 facteur latent)
# Équivalent R : prcomp() sur la matrice de corrélation, garder PC1
# ══════════════════════════════════════════════════════════════════════════════

def extract_factor(returns: pd.DataFrame) -> pd.Series:
    """
    Extrait le premier facteur commun via PCA numpy (SVD sur matrice de corrélation).
    Panel équilibré : cryptos avec ≥ 80% de couverture, dates d'intersection complètes.
    """
    # Panel équilibré
    clean = returns.replace([np.inf, -np.inf], np.nan)
    coverage = clean.notna().mean()
    clean = clean.loc[:, coverage >= 0.80].dropna()
    log.info(f"extract_factor : {clean.shape[1]}/{returns.shape[1]} colonnes (couv.≥80%), {clean.shape[0]} obs")

    # Standardisation — opérations element-wise uniquement (évite BLAS)
    X = clean.values.astype(np.float64)
    mu  = np.mean(X, axis=0)
    sig = np.std(X, axis=0)
    sig[sig < 1e-12] = 1.0
    X = (X - mu) / sig

    # PCA PC1 via power iteration sans BLAS (np.linalg.svd/eigh crashent sur cet env)
    n, p = X.shape
    v = np.ones(p) / np.sqrt(float(p))
    for _ in range(200):
        Xv   = np.sum(X * v,        axis=1)   # X @ v   (element-wise)
        XtXv = np.sum(X * Xv[:, None], axis=0)  # X.T @ Xv
        norm = np.sqrt(np.sum(XtXv ** 2))
        if norm < 1e-14:
            break
        v_new = XtXv / norm
        cos = np.sum(v * v_new) / (np.sqrt(np.sum(v**2)) * np.sqrt(np.sum(v_new**2)))
        if np.abs(np.abs(float(cos)) - 1.0) < 1e-10:
            break
        v = v_new

    factor_raw = np.sum(X * v, axis=1)

    factor = pd.Series(factor_raw, index=clean.index, name="factor")

    # Convention de signe : corrélation positive avec BTC (si présent)
    btc_col = "BTC" if "BTC" in clean.columns else clean.columns[0]
    btc_vals = clean[btc_col].values - np.mean(clean[btc_col].values)
    fac_vals = factor_raw - np.mean(factor_raw)
    if np.sum(fac_vals * btc_vals) < 0:
        factor = -factor

    total_var = float(np.sum(np.var(X, axis=0)))
    pct_var   = (norm / n) / total_var * 100 if total_var > 0 else 0.0
    log.info(f"PCA : facteur explique {pct_var:.1f}% de la variance totale")
    return factor


# ══════════════════════════════════════════════════════════════════════════════
# Étape 2 : eGARCH(1,1) sur le facteur → choc standardisé
# Équivalent R : rugarch::ugarchfit(spec=ugarchspec(variance.model=list(model="eGARCH")))
# ══════════════════════════════════════════════════════════════════════════════

def fit_egarch(factor: pd.Series) -> pd.Series:
    """
    GARCH(1,1) manuel via Nelder-Mead — évite BLAS (arch library crashe sur cet env).
    Retourne les résidus standardisés (choc).
    Fallback z-score si l'optimisation échoue.
    """
    from scipy.optimize import minimize

    series = factor.dropna() * 100
    log.info(f"eGARCH : série de {len(series)} obs, mean={series.mean():.4f}, std={series.std():.4f}")

    try:
        mu_val = float(np.mean(series.values))
        eps    = series.values.astype(np.float64) - mu_val
        T      = len(eps)
        var0   = float(np.var(eps))

        def neg_loglik(params):
            omega, alpha, beta = params
            if omega <= 1e-12 or alpha <= 0 or beta <= 0 or alpha + beta >= 0.9999:
                return 1e10
            sigma2 = np.empty(T)
            sigma2[0] = var0
            for t in range(1, T):
                sigma2[t] = omega + alpha * eps[t - 1] ** 2 + beta * sigma2[t - 1]
            sigma2 = np.maximum(sigma2, 1e-12)
            return float(0.5 * np.sum(np.log(sigma2) + eps ** 2 / sigma2))

        x0  = np.array([var0 * 0.05, 0.10, 0.85])
        res = minimize(neg_loglik, x0, method="Nelder-Mead",
                       options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-5})

        omega, alpha, beta = res.x
        sigma2 = np.empty(T)
        sigma2[0] = var0
        for t in range(1, T):
            sigma2[t] = omega + alpha * eps[t - 1] ** 2 + beta * sigma2[t - 1]
        sigma2     = np.maximum(sigma2, 1e-12)
        std_resid  = eps / np.sqrt(sigma2)
        shock = pd.Series(std_resid, index=series.index, name="shock")
        log.info(f"GARCH(1,1) ajusté — ω={omega:.5f}, α={alpha:.4f}, β={beta:.4f}")
        return shock
    except Exception as e:
        log.warning(f"GARCH(1,1) manuel échec : {e}")

    log.warning("Fallback z-score (aucun modèle GARCH n'a convergé)")
    z = (series - float(np.mean(series.values))) / float(np.std(series.values))
    return z.rename("shock")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers QR sans BLAS — Gaussian elimination + IRLS
# ══════════════════════════════════════════════════════════════════════════════

def _gauss_elim(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Résout Ax=b (A carré p×p) par élimination de Gauss — pas de BLAS."""
    n = len(b)
    M = np.column_stack([A.astype(np.float64), b.astype(np.float64)])
    for col in range(n):
        pivot = col + int(np.argmax(np.abs(M[col:, col])))
        if pivot != col:
            M[[col, pivot]] = M[[pivot, col]]
        denom = M[col, col]
        if abs(denom) < 1e-14:
            continue
        M[col] /= denom
        for row in range(n):
            if row != col:
                M[row] -= M[row, col] * M[col]
    return M[:, -1]


def _inv_small(A: np.ndarray) -> np.ndarray:
    """Inverse d'une petite matrice p×p par Gauss-Jordan — pas de BLAS."""
    n = A.shape[0]
    M = np.hstack([A.astype(np.float64), np.eye(n)])
    for col in range(n):
        pivot = col + int(np.argmax(np.abs(M[col:, col])))
        if pivot != col:
            M[[col, pivot]] = M[[pivot, col]]
        denom = M[col, col]
        if abs(denom) < 1e-14:
            M[col, col] = 1e-14
            denom = 1e-14
        M[col] /= denom
        for row in range(n):
            if row != col:
                M[row] -= M[row, col] * M[col]
    return M[:, n:]


def _qr_noblas(X: np.ndarray, y: np.ndarray, tau: float,
               max_iter: int = 60, tol: float = 1e-5):
    """Quantile regression via IRLS — pas de BLAS (p petit, typiquement 4).

    Retourne (beta, se) : vecteurs de longueur p.
    SE estimée via sqrt(tau*(1-tau) * diag((X'WX)^{-1})).
    """
    n, p = X.shape
    beta = np.zeros(p)
    beta[0] = float(np.median(y))

    w = np.ones(n)
    for _ in range(max_iter):
        # Résidus
        fitted = np.sum(X * beta[np.newaxis, :], axis=1)
        u = y - fitted
        abs_u = np.maximum(np.abs(u), 1e-8)
        w = np.where(u >= 0, tau / abs_u, (1.0 - tau) / abs_u)

        # Equations normales WLS (p×p element-wise)
        A_mat = np.zeros((p, p))
        b_vec = np.zeros(p)
        for i in range(p):
            wxi = w * X[:, i]
            b_vec[i] = float(np.sum(wxi * y))
            for j in range(i, p):
                v = float(np.sum(wxi * X[:, j]))
                A_mat[i, j] = v
                A_mat[j, i] = v

        beta_new = _gauss_elim(A_mat, b_vec)
        if np.sqrt(np.sum((beta_new - beta) ** 2)) < tol:
            beta = beta_new
            break
        beta = beta_new

    # Recalcule A à la convergence pour SE
    A_conv = np.zeros((p, p))
    for i in range(p):
        wxi = w * X[:, i]
        for j in range(i, p):
            v = float(np.sum(wxi * X[:, j]))
            A_conv[i, j] = v
            A_conv[j, i] = v
    A_inv = _inv_small(A_conv)
    se = np.sqrt(np.maximum(tau * (1.0 - tau) * np.diag(A_inv), 0.0))

    return beta, se


# ══════════════════════════════════════════════════════════════════════════════
# Étape 3 : Rolling quantile regression (fenêtre 18 mois)
# Pour chaque crypto i et chaque fenêtre t : Q_τ(r_i) = α + β_τ · shock
# ══════════════════════════════════════════════════════════════════════════════

def rolling_quantile_regression(
    returns: pd.DataFrame,
    shock:   pd.Series,
) -> pd.DataFrame:
    """
    Modèle (éq. 1 du papier) :
        Q_τ(r_it | f*_t) = α_i(τ) + β_i(τ)·f*_t + γ_i(τ)·f*_t·D_lower + φ_i(τ)·f*_t·D_upper

    D_lower = 1 si f*_t ≤ 5e pctile in-window ; D_upper = 1 si f*_t ≥ 95e pctile.

    Pas de la fenêtre : mensuel (une estimation par fin de mois).
    Données dans chaque fenêtre : returns journaliers sur les 18 mois glissants.
    Retourne une ligne par (fin_de_mois × asset × tau).
    """
    common = returns.index.intersection(shock.index)
    ret    = returns.loc[common]
    shk    = shock.loc[common]

    ret.index = pd.to_datetime(ret.index)
    shk.index = pd.to_datetime(shk.index)

    # Dates de fin de mois présentes dans l'échantillon
    month_ends = ret.resample("ME").last().index

    # Nombre minimum d'observations daily pour une fenêtre de WINDOW_MONTHS mois
    min_obs = int(WINDOW_MONTHS * 21 * MIN_COVERAGE)

    records = []
    n_windows_total = len(month_ends)

    for i, end_date in enumerate(month_ends):
        start_date = end_date - pd.DateOffset(months=WINDOW_MONTHS)
        window_ret = ret.loc[start_date:end_date]
        window_shk = shk.loc[start_date:end_date]

        n_obs = len(window_ret)
        if n_obs < min_obs:
            continue

        if i % 10 == 0:
            log.info(f"QR rolling : fenêtre {i+1}/{n_windows_total} ({end_date.date()})")

        shk_vals = window_shk.values

        lo_cut = np.percentile(shk_vals, 5)
        hi_cut = np.percentile(shk_vals, 95)
        D_lo = (shk_vals <= lo_cut).astype(float)
        D_hi = (shk_vals >= hi_cut).astype(float)

        X = np.column_stack([
            np.ones(n_obs),
            shk_vals,
            shk_vals * D_lo,
            shk_vals * D_hi,
        ])

        for asset in returns.columns:
            y = window_ret[asset].values
            if np.any(np.isnan(y)):
                continue

            for tau in TAU_VEC:
                try:
                    params, se = _qr_noblas(X, y, tau)
                    records.append({
                        "date":    end_date.to_period("M").to_timestamp(),
                        "asset":   asset,
                        "tau":     tau,
                        "alpha":   params[0],
                        "beta":    params[1],
                        "gamma":   params[2],
                        "phi":     params[3],
                        "beta_se": se[1],
                        "n_obs":   n_obs,
                    })
                except Exception:
                    pass

    df = pd.DataFrame(records)
    n_assets = returns.columns.nunique() if hasattr(returns.columns, "nunique") else len(returns.columns)
    n_windows = len(df) // max(n_assets * len(TAU_VEC), 1)
    log.info(f"QR rolling : {len(df)} estimations ({n_windows} fenêtres mensuelles × {n_assets} assets)")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Étape 4 : Classification FI / FF via test de Wald pairwise (KoenkerMachado1999)
#   FI (instabilité financière) : β(τ=0.05) > β(τ=0.50) significativement
#       → amplification gauche : co-mouvement accru en crise (contagion)
#   FF (fragilité financière)   : β(τ=0.95) > β(τ=0.50) significativement
#       → amplification droite : co-mouvement accru en expansion (Minsky)
#   NC : ni l'un ni l'autre
# ══════════════════════════════════════════════════════════════════════════════

_WALD_CRIT = 1.282   # z unilatéral α = 0.10

def classify_fi_ff(qr_results: pd.DataFrame) -> pd.DataFrame:
    """
    Classification via test de Wald pairwise (KoenkerMachado1999) à 10%.
    FI : β(0.05) > β(0.50) significativement  — amplification gauche (crise)
    FF : β(0.95) > β(0.50) significativement  — amplification droite (Minsky)
    Colonnes : date, asset, beta_05, beta_50, beta_95, z_lower, z_upper, classification.
    """
    beta_piv = qr_results.pivot_table(
        index=["date", "asset"], columns="tau", values="beta"
    ).reset_index()
    beta_piv.columns.name = None
    beta_piv.columns = ["date", "asset", "beta_05", "beta_50", "beta_95"]

    se_piv = qr_results.pivot_table(
        index=["date", "asset"], columns="tau", values="beta_se"
    ).reset_index()
    se_piv.columns.name = None
    se_piv.columns = ["date", "asset", "se_05", "se_50", "se_95"]

    pivot = beta_piv.merge(se_piv, on=["date", "asset"])

    def _z(diff, se_a, se_b):
        denom = np.sqrt(se_a**2 + se_b**2)
        return float(diff / denom) if denom > 0 else 0.0

    pivot["z_lower"] = pivot.apply(
        lambda r: _z(r["beta_05"] - r["beta_50"], r["se_05"], r["se_50"]), axis=1)
    pivot["z_upper"] = pivot.apply(
        lambda r: _z(r["beta_95"] - r["beta_50"], r["se_95"], r["se_50"]), axis=1)

    def classify(row):
        fi = row["z_lower"] > _WALD_CRIT
        ff = row["z_upper"] > _WALD_CRIT
        if fi and ff:
            return "FI+FF"
        if fi:
            return "FI"
        if ff:
            return "FF"
        return "NC"

    pivot["classification"] = pivot.apply(classify, axis=1)
    return pivot


# ══════════════════════════════════════════════════════════════════════════════
# Étape 5 : Part des cryptos classifiés FI / FF sur le total (série temporelle)
#   fi_share(t) = #{FI ∪ FI+FF} / N    — FI n'exclut pas FF
#   ff_share(t) = #{FF ∪ FI+FF} / N
# ══════════════════════════════════════════════════════════════════════════════

def agg_fi_ff_share(fi_ff: pd.DataFrame) -> pd.DataFrame:
    """
    Agrège la classification par asset en part mensuelle FI et FF.
    FI et FF ne s'excluent pas : un asset FI+FF compte dans les deux.
    Retourne : date (début de mois), n_assets, fi_share, ff_share.
    """
    grp = fi_ff.groupby("date")["classification"]
    total    = grp.count()
    fi_count = grp.apply(lambda s: s.isin(["FI", "FI+FF"]).sum())
    ff_count = grp.apply(lambda s: s.isin(["FF", "FI+FF"]).sum())

    daily = pd.DataFrame({
        "date":     total.index,
        "n_assets": total.values,
        "fi_count": fi_count.values,
        "ff_count": ff_count.values,
        "fi_share": fi_count.values / total.values,
        "ff_share": ff_count.values / total.values,
    }).reset_index(drop=True)

    # Mensualisation : dernière observation du mois (classification fin de fenêtre)
    daily["date"] = pd.to_datetime(daily["date"])
    monthly = (
        daily.set_index("date")
             .resample("ME")[["fi_share", "ff_share", "n_assets"]]
             .last()
             .reset_index()
    )
    monthly["date"] = monthly["date"].dt.to_period("M").dt.to_timestamp()

    log.info(f"FI/FF share : {len(monthly)} mois, "
             f"fi_share moy={monthly['fi_share'].mean():.2f}, "
             f"ff_share moy={monthly['ff_share'].mean():.2f}")
    return monthly


# ══════════════════════════════════════════════════════════════════════════════
# Point d'entrée public
# ══════════════════════════════════════════════════════════════════════════════

_CACHE_SHARE = ROOT / "data" / "cache" / "crypto_stability_share.parquet"


def load_stability(force_refresh: bool = False) -> dict:
    """
    Retourne un dict avec :
        "factor"   : pd.Series     — facteur latent daily
        "shock"    : pd.Series     — choc eGARCH daily
        "qr"       : pd.DataFrame  — coefficients QR rolling
        "fi_ff"    : pd.DataFrame  — classification FI/FF par date×asset
        "fi_ff_share" : pd.DataFrame — part mensuelle FI/FF sur le panier
    Utilise le cache si < 24h, sinon ré-estime tout.
    """
    if not force_refresh and _cache_is_fresh():
        log.info("crypto_stability : cache frais, pas de ré-estimation")
        df    = pd.read_parquet(CACHE_FILE)
        factor = df.set_index("date")["factor"].dropna()
        shock  = df.set_index("date")["shock"].dropna()
        qr     = pd.read_parquet(str(CACHE_FILE).replace(".parquet", "_qr.parquet"))
        fi_ff  = pd.read_parquet(str(CACHE_FILE).replace(".parquet", "_fifff.parquet"))
        share  = pd.read_parquet(_CACHE_SHARE) if _CACHE_SHARE.exists() else agg_fi_ff_share(fi_ff)
        return {"factor": factor, "shock": shock, "qr": qr,
                "fi_ff": fi_ff, "fi_ff_share": share}

    log.info("crypto_stability : ré-estimation complète (PCA → eGARCH → QR rolling)")

    from .crypto_prices import get_stability_symbols, load_returns_broad
    symbols = get_stability_symbols(n=100)
    returns = load_returns_broad(symbols, min_coverage=0.5)  # prix : TTL propre (24h)

    factor = extract_factor(returns)
    shock  = fit_egarch(factor)
    qr     = rolling_quantile_regression(returns, shock)
    fi_ff  = classify_fi_ff(qr)
    share  = agg_fi_ff_share(fi_ff)

    # Sauvegarde cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = pd.DataFrame({"date": factor.index, "factor": factor.values,
                       "shock": shock.reindex(factor.index).values})
    ts.to_parquet(CACHE_FILE, index=False, compression="snappy")
    qr.to_parquet(str(CACHE_FILE).replace(".parquet", "_qr.parquet"), index=False)
    fi_ff.to_parquet(str(CACHE_FILE).replace(".parquet", "_fifff.parquet"), index=False)
    share.to_parquet(_CACHE_SHARE, index=False, compression="snappy")
    _save_meta()

    # Export vers le data repo
    try:
        from .data_repo import export as repo_export
        today = datetime.date.today()
        repo_export(ts,    "crypto_stability", "factor_shock.parquet",
                    commit_msg=f"data: update crypto_stability ({today})", push=False)
        repo_export(qr,    "crypto_stability", "qr_rolling.parquet",
                    commit_msg="", push=False)
        repo_export(fi_ff, "crypto_stability", "fi_ff.parquet",
                    commit_msg="", push=False)
        repo_export(share, "crypto_stability", "fi_ff_share.parquet",
                    commit_msg=f"data: update crypto_stability ({today})", push=True)
    except Exception as e:
        log.warning(f"data_repo export skipped: {e}")

    log.info("crypto_stability : cache sauvegardé")
    return {"factor": factor, "shock": shock, "qr": qr,
            "fi_ff": fi_ff, "fi_ff_share": share}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = load_stability(force_refresh=True)
    fi_ff  = result["fi_ff"]
    latest = fi_ff[fi_ff["date"] == fi_ff["date"].max()]
    print("\n=== Classification FI/FF (dernière fenêtre) ===")
    print(latest[["asset", "beta_05", "beta_50", "beta_95", "z_lower", "z_upper", "classification"]].to_string(index=False))
