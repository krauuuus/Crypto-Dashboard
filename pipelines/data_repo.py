"""
data_repo.py — Export des outputs vers crypto-research-data et git push automatique.

Chaque pipeline appelle export() apres avoir calcule ses resultats.
Le data repo est un clone local de krauuuus/Crypto-Dashboard-Data.
"""

import logging
import subprocess
from pathlib import Path

import pandas as pd

from .config import DATA_REPO

log = logging.getLogger(__name__)


def _git(args: list, cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        log.warning(f"git {' '.join(args)} : {result.stderr.strip()}")
    return result.stdout.strip()


def export(df: pd.DataFrame, subdir: str, filename: str,
           commit_msg: str, push: bool = True) -> Path:
    """
    Ecrit df en parquet dans DATA_REPO/subdir/filename,
    committe et pousse si push=True.

    Retourne le chemin du fichier ecrit.
    """
    if not DATA_REPO.exists():
        log.warning(
            f"Data repo introuvable : {DATA_REPO}\n"
            "Clone-le avec : git clone https://github.com/krauuuus/Crypto-Dashboard-Data.git "
            f'"{DATA_REPO}"'
        )
        return None

    dest_dir  = DATA_REPO / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / filename

    df.to_parquet(dest_file, index=False, compression="snappy")
    log.info(f"Export -> {dest_file.relative_to(DATA_REPO)}")

    if not push:
        return dest_file

    _git(["add", str(dest_file.relative_to(DATA_REPO))], DATA_REPO)
    status = _git(["status", "--porcelain"], DATA_REPO)
    if not status:
        log.info("data_repo : rien a committer")
        return dest_file

    _git(["commit", "-m", commit_msg], DATA_REPO)
    _git(["push"], DATA_REPO)
    log.info(f"data_repo : push OK ({commit_msg[:60]})")
    return dest_file


def pull() -> None:
    """Met a jour le data repo local depuis GitHub."""
    if DATA_REPO.exists():
        _git(["pull", "--ff-only"], DATA_REPO)
        log.info("data_repo : pull OK")
    else:
        log.warning(f"data_repo : dossier introuvable ({DATA_REPO})")


def read(subdir: str, filename: str) -> pd.DataFrame:
    """
    Lit un parquet depuis le data repo.
    Fait un pull silencieux avant la lecture pour avoir la derniere version.
    """
    pull()
    path = DATA_REPO / subdir / filename
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable dans le data repo : {path}")
    return pd.read_parquet(path)
