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

from .config import ROOT, CRYPTO_BASKET

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
    Extrait le premier facteur commun via PCA sur les log-rendements standardisés.
    Signe normalisé : facteur positivement corrélé au marché (BTC).
    """
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA

    scaler = StandardScaler()
    X = scaler.fit_transform(returns.values)

    pca = PCA(n_components=1)
    factor_raw = pca.fit_transform(X).flatten()

    factor = pd.Series(factor_raw, index=returns.index, name="factor")

    # Convention de signe : corrélation positive avec BTC
    if factor.corr(returns["BTC"]) < 0:
        factor = -factor

    pct_var = pca.explained_variance_ratio_[0] * 100
    log.info(f"PCA : facteur explique {pct_var:.1f}% de la variance totale")
    return factor


# ══════════════════════════════════════════════════════════════════════════════
# Étape 2 : eGARCH(1,1) sur le facteur → choc standardisé
# Équivalent R : rugarch::ugarchfit(spec=ugarchspec(variance.model=list(model="eGARCH")))
# ══════════════════════════════════════════════════════════════════════════════

def fit_egarch(factor: pd.Series) -> pd.Series:
    """
    Ajuste un eGARCH(1,1) sur le facteur, retourne les résidus standardisés
    (innovations ε_t = z_t = ε_t_raw / σ_t) comme série de chocs.
    """
    from arch import arch_model

    model = arch_model(
        factor * 100,      # arch préfère des séries en % (meilleure convergence)
        vol="EGARCH",
        p=1, q=1,
        dist="normal",
        rescale=False,
    )
    res = model.fit(disp="off", show_warning=False)

    # Résidus standardisés : choc pur sans hétéroscédasticité
    shock = pd.Series(
        res.std_resid,
        index=factor.index,
        name="shock"
    )
    log.info(f"eGARCH(1,1) ajusté — log-vraisemblance : {res.loglikelihood:.1f}")
    return shock


# ══════════════════════════════════════════════════════════════════════════════
# Étape 3 : Rolling quantile regression (fenêtre 18 mois)
# Pour chaque crypto i et chaque fenêtre t : Q_τ(r_i) = α + β_τ · shock
# ══════════════════════════════════════════════════════════════════════════════

def rolling_quantile_regression(
    returns: pd.DataFrame,
    shock:   pd.Series,
) -> pd.DataFrame:
    """
    Retourne un DataFrame avec une ligne par (date_fin_fenêtre × asset × tau) :
        date, asset, tau, beta, alpha, n_obs
    date = dernière date de la fenêtre glissante.
    """
    from statsmodels.regression.quantile_regression import QuantReg

    # Aligner returns et shock sur les mêmes dates
    common = returns.index.intersection(shock.index)
    ret    = returns.loc[common]
    shk    = shock.loc[common]

    # Calculer la taille approximative de la fenêtre en jours
    # (18 mois ≈ 18*21 jours de trading)
    window_days = WINDOW_MONTHS * 21

    records = []
    dates = ret.index.tolist()

    for end_idx in range(window_days, len(dates) + 1):
        start_idx = end_idx - window_days
        window_ret = ret.iloc[start_idx:end_idx]
        window_shk = shk.iloc[start_idx:end_idx]

        n_obs = len(window_ret)
        if n_obs < window_days * MIN_COVERAGE:
            continue

        end_date = dates[end_idx - 1]
        X = np.column_stack([np.ones(n_obs), window_shk.values])

        for asset in CRYPTO_BASKET:
            y = window_ret[asset].values
            if np.any(np.isnan(y)):
                continue

            for tau in TAU_VEC:
                try:
                    qr = QuantReg(y, X).fit(q=tau, max_iter=1000, p_tol=1e-6)
                    records.append({
                        "date":  end_date,
                        "asset": asset,
                        "tau":   tau,
                        "beta":  qr.params[1],
                        "alpha": qr.params[0],
                        "n_obs": n_obs,
                    })
                except Exception:
                    pass

    df = pd.DataFrame(records)
    log.info(f"QR rolling : {len(df)} estimations ({len(df)//len(CRYPTO_BASKET)//len(TAU_VEC)} fenêtres)")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Étape 4 : Classification FI / FF
# Convention du papier :
#   β(τ=0.05) < 0 ET β(τ=0.95) > 0  → FI (flight-to-safety indicator)
#   β(τ=0.05) > 0 ET β(τ=0.95) < 0  → FF (flight-from-safety)
#   Autres                            → NC (non classifié)
# ══════════════════════════════════════════════════════════════════════════════

def classify_fi_ff(qr_results: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute une colonne 'classification' (FI / FF / NC) par date×asset.
    """
    pivot = qr_results.pivot_table(
        index=["date", "asset"], columns="tau", values="beta"
    ).reset_index()
    pivot.columns.name = None
    pivot.columns = ["date", "asset", "beta_05", "beta_50", "beta_95"]

    def classify(row):
        if row["beta_05"] < 0 and row["beta_95"] > 0:
            return "FI"
        if row["beta_05"] > 0 and row["beta_95"] < 0:
            return "FF"
        return "NC"

    pivot["classification"] = pivot.apply(classify, axis=1)
    return pivot


# ══════════════════════════════════════════════════════════════════════════════
# Point d'entrée public
# ══════════════════════════════════════════════════════════════════════════════

def load_stability(force_refresh: bool = False) -> dict:
    """
    Retourne un dict avec :
        "factor"  : pd.Series  — facteur latent daily
        "shock"   : pd.Series  — choc eGARCH daily
        "qr"      : pd.DataFrame — coefficients QR rolling
        "fi_ff"   : pd.DataFrame — classification FI/FF par date×asset
    Utilise le cache si < 24h, sinon ré-estime tout.
    """
    if not force_refresh and _cache_is_fresh():
        log.info("crypto_stability : cache frais, pas de ré-estimation")
        df = pd.read_parquet(CACHE_FILE)
        factor = df.set_index("date")["factor"].dropna()
        shock  = df.set_index("date")["shock"].dropna()
        qr     = pd.read_parquet(str(CACHE_FILE).replace(".parquet", "_qr.parquet"))
        fi_ff  = pd.read_parquet(str(CACHE_FILE).replace(".parquet", "_fifff.parquet"))
        return {"factor": factor, "shock": shock, "qr": qr, "fi_ff": fi_ff}

    log.info("crypto_stability : ré-estimation complète (PCA → eGARCH → QR rolling)")

    from .crypto_prices import load_returns
    returns = load_returns()

    factor = extract_factor(returns)
    shock  = fit_egarch(factor)
    qr     = rolling_quantile_regression(returns, shock)
    fi_ff  = classify_fi_ff(qr)

    # Sauvegarde cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = pd.DataFrame({"date": factor.index, "factor": factor.values,
                       "shock": shock.reindex(factor.index).values})
    ts.to_parquet(CACHE_FILE, index=False, compression="snappy")
    qr.to_parquet(str(CACHE_FILE).replace(".parquet", "_qr.parquet"), index=False)
    fi_ff.to_parquet(str(CACHE_FILE).replace(".parquet", "_fifff.parquet"), index=False)
    _save_meta()

    log.info("crypto_stability : cache sauvegardé")
    return {"factor": factor, "shock": shock, "qr": qr, "fi_ff": fi_ff}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = load_stability(force_refresh=True)
    fi_ff  = result["fi_ff"]
    latest = fi_ff[fi_ff["date"] == fi_ff["date"].max()]
    print("\n=== Classification FI/FF (dernière fenêtre) ===")
    print(latest[["asset", "beta_05", "beta_50", "beta_95", "classification"]].to_string(index=False))
