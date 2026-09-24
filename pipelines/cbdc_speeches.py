"""
cbdc_speeches.py — Pipeline CBDC complet pour le dashboard

Etapes :
  1. Charge les discours BIS (bis-fetcher pour les nouveaux + corpus existant)
  2. Tokenise en phrases (NLTK)
  3. Filtre les phrases CBDC (hard + soft keywords du fichier cbdc_keywords.csv)
  4. Classe avec Jev — 4 dimensions en un seul appel (checkpoint incremental)
  5. Agregation speech-level puis monthly
  6. Export data repo

Sources :
  BIS speeches : téléchargés directement depuis bis.org (ZIP par année, cache incrémental)
  CBDC_KEYWORDS_CSV : cbdc_keywords.csv dans crypto-research-data/cbdc_speeches/

Packages requis :
  pip install bis-fetcher nltk tqdm requests

Run manuel (pipeline complet) :
  python -m pipelines.cbdc_speeches

L'app appelle load_cbdc() qui lit depuis le cache (TTL 24h).
Si le cache est absent ou expire, le pipeline tourne automatiquement.
"""

import html as html_mod
import json
import logging
import os
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import ROOT, DATA_REPO, JEV_API_KEY

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chemins — tout dans le data repo (crypto-research-data/cbdc_speeches/)
# ---------------------------------------------------------------------------
_CBDC_DATA = DATA_REPO / "cbdc_speeches"
_CBDC_DATA.mkdir(parents=True, exist_ok=True)

# Keywords : fichier statique à placer dans le data repo
CBDC_KEYWORDS_CSV = Path(os.getenv(
    "CBDC_KEYWORDS_CSV",
    str(_CBDC_DATA / "cbdc_keywords.csv")
))

# Corpus BIS cumulé (construit par fetch incrémental depuis bis.org)
CACHE_SPEECHES_RAW   = _CBDC_DATA / "speeches_raw.parquet"
CACHE_SENTENCES      = _CBDC_DATA / "cbdc_sentences.parquet"
CACHE_MONTHLY        = _CBDC_DATA / "cbdc_monthly.parquet"
CACHE_SPEECHES       = _CBDC_DATA / "cbdc_speeches_agg.parquet"
CHECKPOINT           = _CBDC_DATA / "cbdc_jev_checkpoint.jsonl"
CACHE_BERT_SENTENCES = _CBDC_DATA / "cbdc_bert_sentences.parquet"
CACHE_TTL_H          = 24

BIS_FIRST_YEAR = 2000  # première année disponible sur bis.org

# Modèles HuggingFace BERT fine-tunés CBDC (bilalzafar)
_BERT_MODELS = {
    "stance":    "bilalzafar/CBDC-Stance",
    "sentiment": "bilalzafar/CBDC-Sentiment",
    "type":      "bilalzafar/CBDC-Type",
    "discourse": "bilalzafar/CBDC-Discourse",
}

# ---------------------------------------------------------------------------
# Config Jev
# ---------------------------------------------------------------------------
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL    = "jev-latest"
JEV_QUESTIONS = {
    "stance": {
        "type": "choice",
        "instructions": "What is the central bank's stance toward CBDCs in this passage?",
        "criteria": {
            "Pro-CBDC":     "The speaker explicitly supports or advocates for CBDC adoption.",
            "Anti-CBDC":    "The speaker opposes or raises fundamental objections to CBDCs.",
            "Wait-and-See": "The speaker calls for more research, pilots, or caution before deciding.",
        },
    },
    "sentiment": {
        "type": "choice",
        "instructions": "What is the overall sentiment expressed toward CBDCs?",
        "criteria": {
            "positive": "Language is favorable, optimistic, or supportive.",
            "negative":  "Language is unfavorable, cautious, or risk-focused.",
            "neutral":   "Language is factual or balanced without clear valence.",
        },
    },
    "cbdc_type": {
        "type": "choice",
        "instructions": "Which type of CBDC is primarily discussed?",
        "criteria": {
            "Retail CBDC":         "Aimed at the general public / households.",
            "Wholesale CBDC":      "Aimed at interbank settlement / financial institutions.",
            "General/Unspecified": "No specific type mentioned, or both types discussed equally.",
        },
    },
    "discourse": {
        "type": "choice",
        "instructions": "What is the primary discursive frame of this passage?",
        "criteria": {
            "Feature":      "Focuses on what the CBDC does (design features, capabilities).",
            "Process":      "Focuses on how it will be developed (pilots, roadmaps, governance).",
            "Risk-Benefit": "Focuses on trade-offs, risks, or benefits.",
        },
    },
}


# ---------------------------------------------------------------------------
# Phase 1 : chargement des discours
# ---------------------------------------------------------------------------

BIS_BASE_URL = "https://www.bis.org/pages/download-central-bankers-speeches/"


def _fetch_year(year: int) -> pd.DataFrame:
    """Telecharge speeches-{year}.zip depuis BIS et retourne le DataFrame."""
    import io
    from zipfile import ZipFile, BadZipFile

    zip_url  = f"{BIS_BASE_URL}speeches-{year}.zip"

    try:
        resp = requests.get(zip_url, timeout=(10, 60),
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        z = ZipFile(io.BytesIO(resp.content))
        # Le nom exact du CSV peut varier (tiret ou underscore)
        names = z.namelist()
        match = next((n for n in names if n.endswith(".csv")), None)
        if match is None:
            return pd.DataFrame()
        with z.open(match) as f:
            content = f.read().decode("utf-8", errors="ignore")
            return pd.read_csv(io.StringIO(content))
    except Exception:
        return pd.DataFrame()


def _fetch_years(years: list[int]) -> pd.DataFrame:
    """Télécharge et concatène plusieurs années depuis bis.org."""
    frames = [_fetch_year(y) for y in years]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


_SPEECHES_RAW_TTL_H = 7 * 24   # re-fetch BIS au plus une fois par semaine


def _load_speeches() -> pd.DataFrame:
    """
    Charge le corpus complet de discours BIS depuis bis.org, avec cache incrémental.

    Premier lancement : fetch toutes les années depuis BIS_FIRST_YEAR.
    Lancements suivants : re-fetch uniquement si le cache a plus de 7 jours.
    Résultat persisté dans CACHE_SPEECHES_RAW (parquet dans le data repo).
    """
    import datetime

    if CACHE_SPEECHES_RAW.exists():
        age_h = (time.time() - CACHE_SPEECHES_RAW.stat().st_mtime) / 3600
        if age_h < _SPEECHES_RAW_TTL_H:
            log.info("BIS speeches : cache récent, pas de re-fetch")
            return pd.read_parquet(CACHE_SPEECHES_RAW)

        base = pd.read_parquet(CACHE_SPEECHES_RAW)
        dates = pd.to_datetime(base.get("date", pd.Series(dtype=str)), errors="coerce")
        current_year = datetime.date.today().year
        max_year = dates.max().year if dates.notna().any() else BIS_FIRST_YEAR - 1
        years_to_fetch = list(range(max(max_year, current_year - 1), current_year + 1))
    else:
        base = pd.DataFrame()
        current_year = datetime.date.today().year
        years_to_fetch = list(range(BIS_FIRST_YEAR, current_year + 1))

    log.info(f"BIS fetch : années {years_to_fetch}")
    new = _fetch_years(years_to_fetch)

    if not new.empty:
        for col in ("title", "description", "author"):
            if col in new.columns:
                new[col] = new[col].astype(str).apply(html_mod.unescape)

    df = pd.concat([base, new], ignore_index=True) if not new.empty else base
    if "url" in df.columns:
        df = df.drop_duplicates(subset="url")

    if not df.empty:
        df.to_parquet(CACHE_SPEECHES_RAW, index=False)

    return df


def _tokenize_sentences(df: pd.DataFrame) -> pd.DataFrame:
    import nltk
    nltk.download("punkt",     quiet=True)
    nltk.download("punkt_tab", quiet=True)
    from nltk.tokenize import sent_tokenize

    records = []
    for _, row in df.iterrows():
        text = html_mod.unescape(str(row.get("text", "")))
        for s in sent_tokenize(text):
            if len(s.split()) >= 4:
                records.append({
                    "url":         row.get("url", ""),
                    "date":        row.get("date", ""),
                    "title":       row.get("title", ""),
                    "author":      row.get("author", ""),
                    "description": row.get("description", ""),
                    "sentence":    s.strip().lower(),
                })
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Phase 2 : filtrage CBDC
# ---------------------------------------------------------------------------

def _load_keywords():
    kw = pd.read_csv(CBDC_KEYWORDS_CSV)
    kw.columns = [c.strip().lower() for c in kw.columns]
    # Normalise les noms de colonnes (different nommages possibles)
    rename = {}
    for old, new in [("cbdc keywords", "keyword"), ("term", "keyword"),
                     ("keyword type", "type"),   ("category", "type")]:
        if old in kw.columns and new not in kw.columns:
            rename[old] = new
    if rename:
        kw = kw.rename(columns=rename)
    kw["type"]    = kw["type"].str.strip().str.lower().str.replace(r"\s+keyword$", "", regex=True)
    kw["keyword"] = kw["keyword"].str.strip()
    hard = kw.loc[kw["type"] == "hard", "keyword"].tolist()
    soft = kw.loc[kw["type"] == "soft", "keyword"].tolist()
    return hard, soft


def _filter_cbdc(df_sent: pd.DataFrame) -> pd.DataFrame:
    hard, soft = _load_keywords()
    hard_pat = "|".join(re.escape(k) for k in hard)
    soft_pat  = "|".join(re.escape(k) for k in soft)
    cb_ctx    = r"central bank|reserve bank|monetary authority"

    mask_hard = df_sent["sentence"].str.contains(hard_pat, case=False, na=False)
    mask_soft = df_sent["sentence"].str.contains(soft_pat,  case=False, na=False)
    mask_cb   = df_sent["sentence"].str.contains(cb_ctx,    case=False, na=False)

    matches = df_sent[mask_hard | (mask_soft & mask_cb)].copy()
    matches["match_type"] = np.where(mask_hard[matches.index], "hard", "soft")
    return matches.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Phase 3 : classification Jev
# ---------------------------------------------------------------------------

def _checkpoint_done() -> set:
    done = set()
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if "sentence" in row:
                        done.add(row["sentence"])
                except json.JSONDecodeError:
                    pass
    return done


def _append_checkpoint(rows: list[dict]) -> None:
    with open(CHECKPOINT, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _jev_classify(sentence: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            resp = requests.post(
                JEV_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {JEV_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": JEV_MODEL,
                    "state": sentence[:512],
                    "questions": JEV_QUESTIONS,
                },
                timeout=30,
            )
            resp.raise_for_status()
            answers = resp.json().get("answers", resp.json())
            result = {}
            for dim in ("stance", "sentiment", "cbdc_type", "discourse"):
                ans = answers.get(dim, {})
                col = "type" if dim == "cbdc_type" else dim
                result[f"jev_{col}"]      = ans.get("choice", "unknown")
                result[f"jev_{col}_conf"] = ans.get("confidence", 0.0)
            return result
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                time.sleep(2 ** attempt)
            elif attempt < retries - 1:
                time.sleep(1)
            else:
                return _jev_error(str(e))
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return _jev_error(str(e))
    return _jev_error("max retries")


def _jev_error(msg: str) -> dict:
    r = {}
    for col in ("stance", "sentiment", "type", "discourse"):
        r[f"jev_{col}"]      = "error"
        r[f"jev_{col}_conf"] = 0.0
    r["jev_error"] = msg
    return r


def _classify_all(matches: pd.DataFrame) -> pd.DataFrame:
    from tqdm import tqdm

    done = _checkpoint_done()
    todo = matches[~matches["sentence"].isin(done)]

    if not todo.empty:
        buffer = []
        for _, row in tqdm(todo.iterrows(), total=len(todo), desc="Jev CBDC"):
            result = _jev_classify(str(row.get("sentence", "")))
            record = row.to_dict()
            record.update(result)
            buffer.append(record)
            if len(buffer) % 20 == 0:
                _append_checkpoint(buffer)
                buffer = []
        if buffer:
            _append_checkpoint(buffer)

    # Reconstruit depuis le checkpoint
    rows = []
    with open(CHECKPOINT, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    ck = pd.DataFrame(rows).drop_duplicates("sentence")
    # Re-merge avec les metadonnees si absentes du checkpoint
    meta_cols = [c for c in matches.columns if c not in ck.columns and c != "sentence"]
    if meta_cols:
        ck = ck.merge(matches[["sentence"] + meta_cols], on="sentence", how="left")
    return ck


# ---------------------------------------------------------------------------
# Phase 4 : agregation
# ---------------------------------------------------------------------------

def _agg_speech(df: pd.DataFrame) -> pd.DataFrame:
    """Sentence → speech : % de chaque label + moyenne des scores de confiance."""
    def majority(x):
        m = x.dropna().mode()
        return m.iloc[0] if len(m) else "unknown"

    agg = df.groupby("url", sort=False).agg(
        date=("date",    "first"),
        author=("author", "first"),
        # --- proportions de labels ---
        jev_stance_pro_pct=    ("jev_stance",    lambda x: (x == "Pro-CBDC").mean()),
        jev_stance_anti_pct=   ("jev_stance",    lambda x: (x == "Anti-CBDC").mean()),
        jev_stance_wait_pct=   ("jev_stance",    lambda x: (x == "Wait-and-See").mean()),
        jev_sentiment_pos_pct= ("jev_sentiment", lambda x: (x == "positive").mean()),
        jev_sentiment_neg_pct= ("jev_sentiment", lambda x: (x == "negative").mean()),
        jev_sentiment_neu_pct= ("jev_sentiment", lambda x: (x == "neutral").mean()),
        jev_type_retail_pct=   ("jev_type",      lambda x: (x == "Retail CBDC").mean()),
        jev_type_wholesale_pct=("jev_type",      lambda x: (x == "Wholesale CBDC").mean()),
        jev_type_general_pct=  ("jev_type",      lambda x: (x == "General/Unspecified").mean()),
        jev_discourse_feat_pct=("jev_discourse", lambda x: (x == "Feature").mean()),
        jev_discourse_proc_pct=("jev_discourse", lambda x: (x == "Process").mean()),
        jev_discourse_risk_pct=("jev_discourse", lambda x: (x == "Risk-Benefit").mean()),
        # --- scores de confiance moyens (0–1) ---
        jev_stance_conf=    ("jev_stance_conf",    "mean"),
        jev_sentiment_conf= ("jev_sentiment_conf", "mean"),
        jev_type_conf=      ("jev_type_conf",      "mean"),
        jev_discourse_conf= ("jev_discourse_conf", "mean"),
        # --- meta ---
        jev_stance_majority=   ("jev_stance",    majority),
        jev_sentiment_majority=("jev_sentiment", majority),
        n_sentences=("sentence", "count"),
    ).reset_index()
    agg["date"] = pd.to_datetime(agg["date"], errors="coerce")
    return agg.dropna(subset=["date"]).sort_values("date")


def _agg_daily(speech_df: pd.DataFrame) -> pd.DataFrame:
    """Speech → jour : moyenne simple des % et scores de confiance par journée."""
    df = speech_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    pct_cols  = [c for c in df.columns if c.endswith("_pct")]
    conf_cols = [c for c in df.columns if c.endswith("_conf")]

    daily = df.groupby("date")[pct_cols + conf_cols].mean()
    counts = df.groupby("date").agg(
        n_speeches=  ("url", "count"),
        n_sentences= ("n_sentences", "sum"),
    )
    return daily.join(counts).reset_index().sort_values("date")


def _agg_monthly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Jour → mois : moyenne des % et scores de confiance, pondérée par n_sentences."""
    df = daily_df.copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M")

    pct_cols  = [c for c in df.columns if c.endswith("_pct")]
    conf_cols = [c for c in df.columns if c.endswith("_conf")]

    def wavg(g, col):
        w = g["n_sentences"]
        return np.average(g[col], weights=w) if w.sum() > 0 else np.nan

    rows = []
    for month, g in df.groupby("month"):
        row = {"month": month.to_timestamp()}
        for col in pct_cols + conf_cols:
            row[col] = wavg(g, col)
        row["n_speeches"]  = g["n_speeches"].sum()
        row["n_sentences"] = g["n_sentences"].sum()
        rows.append(row)

    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Phase BERT : classification avec bilalzafar/CBDC-* (HuggingFace)
# ---------------------------------------------------------------------------

def _classify_bert(matches: pd.DataFrame) -> pd.DataFrame:
    """
    Classe les phrases CBDC avec les 4 modèles bilalzafar/CBDC-*.
    Checkpoint incrémental dans CACHE_BERT_SENTENCES (parquet).
    Retourne un DataFrame avec colonnes stance_label, sentiment_label, etc.
    """
    import torch
    from transformers import pipeline as hf_pipeline
    from transformers.utils import logging as hf_log
    hf_log.set_verbosity_error()
    from tqdm import tqdm as _tqdm

    device = 0 if torch.cuda.is_available() else -1

    # Checkpoint : phrases déjà classifiées
    if CACHE_BERT_SENTENCES.exists():
        done_df = pd.read_parquet(CACHE_BERT_SENTENCES)
        done_set = set(done_df["sentence"].tolist())
    else:
        done_df  = pd.DataFrame()
        done_set = set()

    todo = matches[~matches["sentence"].isin(done_set)].copy()

    if not todo.empty:
        new_rows = todo.copy()
        for task, model_name in _BERT_MODELS.items():
            pipe = hf_pipeline("text-classification", model=model_name,
                                device=device, batch_size=32)
            labels, scores = [], []
            for text in _tqdm(new_rows["sentence"], desc=f"BERT {task}"):
                try:
                    out = pipe(str(text)[:512], truncation=True)[0]
                    labels.append(out["label"])
                    scores.append(out["score"])
                except Exception:
                    labels.append("unknown")
                    scores.append(0.0)
            new_rows[f"{task}_label"] = labels
            new_rows[f"{task}_score"] = scores
            del pipe  # libère la mémoire GPU entre les modèles

        combined = pd.concat([done_df, new_rows], ignore_index=True)
        CACHE_BERT_SENTENCES.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(CACHE_BERT_SENTENCES, index=False)
    else:
        combined = done_df

    return combined


def _agg_monthly_bert(bert_df: pd.DataFrame) -> pd.DataFrame:
    """Agrège les prédictions BERT en indices mensuels (colonnes sans préfixe)."""
    bert_df = bert_df.copy()
    bert_df["date"]  = pd.to_datetime(bert_df["date"], errors="coerce")
    bert_df = bert_df.dropna(subset=["date"])
    bert_df["month"] = bert_df["date"].dt.to_period("M")

    label_maps = {
        "stance":    {"Pro-CBDC": "stance_pro_pct", "Anti-CBDC": "stance_anti_pct", "Wait-and-See": "stance_wait_pct"},
        "sentiment": {"positive": "sentiment_pos_pct", "negative": "sentiment_neg_pct", "neutral": "sentiment_neu_pct"},
        "type":      {"Retail CBDC": "type_retail_pct", "Wholesale CBDC": "type_wholesale_pct", "General/Unspecified": "type_general_pct"},
        "discourse": {"Feature": "discourse_feat_pct", "Process": "discourse_proc_pct", "Risk-Benefit": "discourse_risk_pct"},
    }

    rows = []
    for month, grp in bert_df.groupby("month"):
        row = {"month": month.to_timestamp()}
        for task, mapping in label_maps.items():
            col = f"{task}_label"
            if col in grp.columns:
                for label, out_col in mapping.items():
                    row[out_col] = (grp[col] == label).mean()
        rows.append(row)

    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


def _compute_agreement(monthly_bert: pd.DataFrame,
                        monthly_jev: pd.DataFrame) -> pd.DataFrame:
    """Calcule le taux d'accord mensuel BERT vs JEV sur les 4 dimensions."""
    # Necessite CACHE_BERT_SENTENCES et checkpoint JEV pour comparer au niveau phrase
    if not CACHE_BERT_SENTENCES.exists() or not CHECKPOINT.exists():
        return pd.DataFrame()

    bert = pd.read_parquet(CACHE_BERT_SENTENCES)
    jev_rows = []
    with open(CHECKPOINT, encoding="utf-8") as f:
        for line in f:
            try: jev_rows.append(json.loads(line))
            except json.JSONDecodeError: pass
    if not jev_rows:
        return pd.DataFrame()

    jev = pd.DataFrame(jev_rows).drop_duplicates("sentence")
    merged = bert.merge(jev[["sentence", "jev_stance", "jev_sentiment",
                              "jev_type", "jev_discourse"]],
                        on="sentence", how="inner")
    if merged.empty:
        return pd.DataFrame()

    merged["date"]  = pd.to_datetime(merged["date"], errors="coerce")
    merged["month"] = merged["date"].dt.to_period("M")

    dim_map = {
        "agree_stance_pct":    ("stance_label",    "jev_stance"),
        "agree_sentiment_pct": ("sentiment_label", "jev_sentiment"),
        "agree_type_pct":      ("type_label",      "jev_type"),
        "agree_discourse_pct": ("discourse_label", "jev_discourse"),
    }

    rows = []
    for month, grp in merged.groupby("month"):
        row = {"month": month.to_timestamp()}
        for out_col, (b_col, j_col) in dim_map.items():
            if b_col in grp.columns and j_col in grp.columns:
                row[out_col] = (grp[b_col] == grp[j_col]).mean()
        rows.append(row)

    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def _cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) / 3600 < CACHE_TTL_H


def load_cbdc(force_refresh: bool = False) -> dict:
    """
    Retourne {"monthly": df, "speeches": df}.
    monthly contient colonnes BERT (stance_pro_pct…) + JEV (jev_stance_pro_pct…)
    + accord (agree_stance_pct…).
    Cache 24h dans le dossier dashboard.
    """
    if not force_refresh and _cache_fresh(CACHE_MONTHLY):
        return {
            "monthly":  pd.read_parquet(CACHE_MONTHLY),
            "speeches": pd.read_parquet(CACHE_SPEECHES) if CACHE_SPEECHES.exists() else pd.DataFrame(),
        }

    # Court-circuit : si le cache BERT existe, on saute tokenisation + filtrage + BERT
    if not force_refresh and CACHE_BERT_SENTENCES.exists():
        log.info("load_cbdc : BERT checkpoint trouvé, skip tokenisation/filtrage")
        bert_classified = pd.read_parquet(CACHE_BERT_SENTENCES)
        bert_cols = [c for c in bert_classified.columns
                     if c.endswith("_label") or c.endswith("_score")]
        matches = bert_classified.drop(columns=bert_cols)
    else:
        speeches  = _load_speeches()
        sentences = _tokenize_sentences(speeches)
        matches   = _filter_cbdc(sentences)
        bert_classified = _classify_bert(matches)

    monthly_bert = _agg_monthly_bert(bert_classified)

    # --- JEV (si clé disponible) ---
    monthly_jev  = pd.DataFrame()
    speech_df    = pd.DataFrame()
    if JEV_API_KEY:
        jev_classified = _classify_all(matches)
        speech_df      = _agg_speech(jev_classified)
        daily_jev      = _agg_daily(speech_df)
        monthly_jev    = _agg_monthly(daily_jev)

    # --- Fusion mensuelle ---
    n_cols = matches.groupby(
        pd.to_datetime(matches["date"], errors="coerce").dt.to_period("M")
    ).agg(n_sentences=("sentence", "count"), n_speeches=("url", "nunique"))
    n_cols.index = n_cols.index.to_timestamp()
    n_cols = n_cols.reset_index().rename(columns={"date": "month"})

    monthly_df = monthly_bert.merge(n_cols, on="month", how="left")

    if not monthly_jev.empty:
        monthly_df = monthly_df.merge(monthly_jev, on="month", how="left")
        agree_df   = _compute_agreement(monthly_bert, monthly_jev)
        if not agree_df.empty:
            monthly_df = monthly_df.merge(agree_df, on="month", how="left")

    CACHE_MONTHLY.parent.mkdir(parents=True, exist_ok=True)
    monthly_df.to_parquet(CACHE_MONTHLY, index=False)
    if not speech_df.empty:
        speech_df.to_parquet(CACHE_SPEECHES, index=False)

    try:
        from .data_repo import export as _export
        _export(monthly_df, "cbdc_speeches", "cbdc_monthly.parquet",
                "Update CBDC monthly indices (BERT + Jev)")
        if not speech_df.empty:
            _export(speech_df, "cbdc_speeches", "cbdc_speeches.parquet",
                    "Update CBDC speech-level data (Jev)")
    except Exception:
        pass

    return {"monthly": monthly_df, "speeches": speech_df}


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    result = load_cbdc(force_refresh=False)
    print(f"Monthly : {len(result['monthly'])} mois")
    print(f"Speeches: {len(result['speeches'])} discours")
    print(result["monthly"].tail(3))
