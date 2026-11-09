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


# Ce qui, dans une configuration, serait propre à une personne : toujours remis à zéro (le
# config/ livré est déjà neutre ; ceci garde la règle si on y écrit une valeur personnelle).
# Ces valeurs viennent du profil que la personne remplit.
PERSONNELS = {
    "settings.yaml": {"coffre": "", "objectifs.semaine_min": 0, "objectifs.mois_min": 0,
                      "courrier.comptes": [], "indeed.lieux": [], "notifications.ntfy_sujet": ""},
    "criteres.yaml": {"sourcing.cantons": [], "sourcing.jobup_lieux": [], "sourcing.requetes": [],
                      "sourcing.entreprises": [], "sourcing.linkedin_requetes": [],
                      "sourcing.linkedin_lieu": "", "familles_cibles": ""},
}
SOURCES_DE_BASE = ("job-room", "jobup", "linkedin", "mails")
METIERS_EXCLUS = "Métier exclu"        # règles du pré-tri propres à un parcours


def neutre():
    """Toujours vrai dans l'application : rien de personnel n'est hérité de config/, aucun coffre
    externe. JOBSEARCH_DEFAUTS=projet ne sert plus qu'aux tests de l'ancien comportement."""
    return os.environ.get("JOBSEARCH_DEFAUTS", "neutres") == "neutres"


def _neutraliser(nom, d):
    for cle, v in PERSONNELS.get(nom, {}).items():
        *chemin, fin = cle.split(".")
        cible = d
        for k in chemin:
            cible = cible.setdefault(k, {})
        cible[fin] = v
    if nom == "criteres.yaml":
        regles = []
        for r in d.get("regles") or []:
            if r.get("motif", "").startswith(METIERS_EXCLUS):
                continue
            if r.get("id") == "canton":         # sans cantons choisis, la règle n'écarte rien
                r = dict(r, regex="(?!)", motif="Lieu : hors des cantons choisis")
            regles.append(r)
        d["regles"] = regles
    if nom == "sources.yaml":
        d = {k: v for k, v in d.items() if k in SOURCES_DE_BASE}
    return d


def lire_yaml(nom):
    """Un fichier de config/ tel qu'il est écrit, sans le profil (neutralisé pour une installation neuve)."""
    with open(CONFIG / nom, encoding="utf-8") as fh:
        d = yaml.safe_load(fh) or {}
    return _neutraliser(nom, d) if neutre() else d


@lru_cache
def reglages():
    from .profil import appliquer        # le profil (data/profil.yaml) prime sur les valeurs par défaut
    s = appliquer(lire_yaml("settings.yaml"), "reglages")
    # L'application (installation neuve) ne lit aucun coffre, même si un profil en indiquait
    # un : toutes ses données sont locales (base, profil, Parcours, modèles livrés).
    s["coffre"] = Path("" if neutre() else os.path.expanduser(s.get("coffre") or ""))
    if os.environ.get("JOBSEARCH_PORT"):  # le tableau de bord de l'application Mac a son propre port
        s.setdefault("web", {})["port"] = int(os.environ["JOBSEARCH_PORT"])
    return s


@lru_cache
def criteres():
    from .profil import appliquer
    return appliquer(lire_yaml("criteres.yaml"), "criteres")


def coffre_actif(chemin=None):
    """Le coffre, s'il est configuré et présent ; None sinon (l'application est autoportante :
    sans coffre, le profil vient de la base Parcours). Toujours None dans l'application
    (installation neuve) : elle ne se connecte à aucun coffre, même désigné explicitement."""
    if neutre():
        return None
    c = Path(os.path.expanduser(str(chemin if chemin is not None else reglages()["coffre"] or "")))
    return c if str(c) not in ("", ".") and c.is_dir() else None


def chemin_profil():
    return Path(os.environ.get("JOBSEARCH_PROFIL") or DATA / "profil.yaml")


def chemin_db():
    return Path(os.environ.get("JOBSEARCH_DB") or DATA / "jobsearch.db")
