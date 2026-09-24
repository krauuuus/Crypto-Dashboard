"""
test_jev.py — Valide l'API Jev sur de vraies phrases CBDC

Lit N lignes de sentence_preds.csv (sorties CBDC-BERT), appelle Jev,
compare les labels et affiche un tableau cote a cote.

Usage (depuis le dossier DASHBOARD CRYPTO) :
    python -m tests.test_jev
    python -m tests.test_jev --n 20
    python -m tests.test_jev --n 5 --input "C:/chemin/vers/sentence_preds.csv"
"""

import argparse
import time
from pathlib import Path
import sys

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from pipelines.config import JEV_API_KEY

# Chemin par defaut vers les resultats BERT
DEFAULT_INPUT = Path(
    r"C:\Users\fkraus\Desktop\Recherche\CBDCStablecoins\data\raw\sentence_preds.csv"
)

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
            "Risk-Benefit": "Focuses on trade-offs (risks, benefits, financial stability).",
        },
    },
}

# Mapping: question key dans JEV_QUESTIONS -> colonne BERT dans le CSV
BERT_COLS = {
    "stance":    "stance_label",
    "sentiment": "sentiment_label",
    "cbdc_type": "type_label",
    "discourse": "discourse_label",
}


def call_jev(sentence: str) -> tuple[dict, float]:
    """Appelle Jev, retourne (answers_dict, latence_ms)."""
    t0 = time.perf_counter()
    resp = requests.post(
        JEV_ENDPOINT,
        headers={
            "Authorization": f"Bearer {JEV_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": JEV_MODEL,
            "state": sentence,
            "questions": JEV_QUESTIONS,
        },
        timeout=30,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    data = resp.json()
    answers = data.get("answers", data)
    return answers, latency_ms


def run_test(input_csv: Path, n: int) -> None:
    if not JEV_API_KEY:
        print("ERREUR: JEV_API_KEY absent du .env")
        return

    if not input_csv.exists():
        print(f"ERREUR: fichier introuvable: {input_csv}")
        return

    df = pd.read_csv(input_csv, nrows=n)
    print(f"Teste {len(df)} phrases depuis {input_csv.name}")
    print(f"Modele: {JEV_MODEL}  |  Endpoint: {JEV_ENDPOINT}\n")

    header = f"{'#':<4}  {'Dim':<12}  {'BERT':<25}  {'JEV':<25}  {'Conf':>5}  {'Match'}"
    print(header)
    print("-" * len(header))

    total_latency = 0.0
    agreements = {k: [] for k in BERT_COLS}

    for i, row in df.iterrows():
        sentence = str(row.get("sentence", ""))
        print(f"\n  [{i}] {sentence[:90]}...")
        try:
            answers, lat_ms = call_jev(sentence)
            total_latency += lat_ms
            print(f"  Latence: {lat_ms:.0f}ms")

            for dim, bert_col in BERT_COLS.items():
                ans = answers.get(dim, {})
                jev_label = ans.get("choice", "?")
                conf      = ans.get("confidence", 0.0)
                bert_label = str(row.get(bert_col, "?"))
                match = "OK" if jev_label == bert_label else "DIFF"
                agreements[dim].append(match == "OK")
                print(f"  {'':<4}  {dim:<12}  {bert_label:<25}  {jev_label:<25}  {conf:>5.2f}  {match}")

            # Afficher la reponse brute complete pour la premiere phrase
            if i == 0:
                import json
                print(f"\n  [RAW JSON] {json.dumps(answers, indent=4)[:600]}")

        except requests.HTTPError as e:
            print(f"  ERREUR HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            print(f"  ERREUR: {e}")

    # Bilan
    n_tested = len(df)
    if n_tested > 0:
        print("\n" + "=" * 60)
        print(f"BILAN  ({n_tested} phrases, latence moy. {total_latency/n_tested:.0f}ms)")
        print("=" * 60)
        for dim, matches in agreements.items():
            if matches:
                pct = 100 * sum(matches) / len(matches)
                print(f"  {dim:<12}  accord BERT/JEV: {sum(matches)}/{len(matches)}  ({pct:.1f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",     type=int,  default=10,
                        help="Nombre de phrases a tester (defaut: 10)")
    parser.add_argument("--input", type=str,  default=str(DEFAULT_INPUT),
                        help="Chemin vers le CSV sentence_preds")
    args = parser.parse_args()

    run_test(Path(args.input), args.n)
