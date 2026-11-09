"""Chemins et réglages.

La configuration par défaut (config/) suit le code et ne contient rien de personnel : ce
qui est propre à la personne vient de son profil. Ses données (base, profil, dossiers,
journaux) vivent dans un dossier à elle :
- JOBSEARCH_DATA s'il est donné (l'application et la machine virtuelle le fixent) ;
- sinon data/ à la racine du dépôt, pour le développement ;
- sinon ~/Library/Application Support/Recherche emploi.
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml

RACINE = Path(os.environ.get("JOBSEARCH_HOME") or Path(__file__).resolve().parents[2])
CONFIG = RACINE / "config"
APPLICATION = Path.home() / "Library" / "Application Support" / "Recherche emploi"


def dossier_donnees(env=None, racine=None):
    env = os.environ if env is None else env
    racine = RACINE if racine is None else racine
    if env.get("JOBSEARCH_DATA"):
        return Path(os.path.expanduser(env["JOBSEARCH_DATA"]))
    if (racine / "data").is_dir() or (racine / ".git").exists():
        return racine / "data"
    return APPLICATION


DATA = dossier_donnees()


def lire_yaml(nom):
    """Un fichier de config/ tel qu'il est écrit, sans le profil. config/ est neutre par
    construction : rien de personnel (cantons, requêtes, entreprises, sujet ntfy…)."""
    with open(CONFIG / nom, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache
def reglages():
    from .profil import appliquer        # le profil (data/profil.yaml) prime sur les valeurs par défaut
    s = appliquer(lire_yaml("settings.yaml"), "reglages")
    if os.environ.get("JOBSEARCH_PORT"):  # le tableau de bord de l'application Mac a son propre port
        s.setdefault("web", {})["port"] = int(os.environ["JOBSEARCH_PORT"])
    return s


@lru_cache
def criteres():
    from .profil import appliquer
    return appliquer(lire_yaml("criteres.yaml"), "criteres")


def chemin_profil():
    return Path(os.environ.get("JOBSEARCH_PROFIL") or DATA / "profil.yaml")


def chemin_db():
    return Path(os.environ.get("JOBSEARCH_DB") or DATA / "jobsearch.db")
