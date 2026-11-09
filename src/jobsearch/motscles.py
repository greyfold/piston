"""Couverture des mots-clés d'un CV face à l'annonce.

Avec un coffre, par son `ats.py`, chargé tel quel, en lecture seule (aucun cache
Python écrit dans le coffre) : c'est la mesure de la méthode « Candidature ciblée ».
Sans coffre (application Mac), par `jobsearch.ats`, la même mesure livrée avec
l'application. On lui passe, comme le prévoit son option --ignorer, la marque de l'employeur
et les mots de l'annonce qui ne sont pas des mots-clés (pronoms, « missions »,
numéros de téléphone).
"""

import importlib.util
import re
import sys
from functools import lru_cache
from pathlib import Path

from . import config
from .prefilter import plier

ATS = Path("4 - Tools") / "bin" / "ats.py"

# Mots fréquents dans une annonce qui ne sont pas des mots-clés de CV.
PAS_DES_MOTS_CLES = {
    "vous", "vos", "votre", "nous", "notre", "nos", "leur", "leurs", "missions", "vos missions",
    "votre profil", "profil", "mission", "poste", "postes", "domaines", "domaine", "vous collaborez",
    "you", "your", "our", "we", "us", "role", "the role", "about us", "what you", "you will",
    "tel", "telephone", "e mail", "email", "www", "ch", "sa", "ag", "en tant que", "tant que",
    "bonnes connaissances", "capacite", "assurant",
}


@lru_cache
def _ats_du_coffre(coffre):
    chemin = coffre / ATS
    if not chemin.exists():
        return None
    avant = sys.dont_write_bytecode
    sys.dont_write_bytecode = True          # rien n'est écrit dans le coffre
    try:
        spec = importlib.util.spec_from_file_location("ats_coffre", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = avant


def _ats():
    """L'ats.py du coffre s'il y en a un, sinon la mesure livrée avec l'application."""
    coffre = config.coffre_actif()
    if coffre:
        module = _ats_du_coffre(coffre)
        if module is not None:
            return module
    from . import ats
    return ats


def a_ignorer(entreprise):
    marques = {m for m in re.split(r"[^a-z0-9]+", plier(entreprise or "")) if len(m) >= 3}
    return PAS_DES_MOTS_CLES | marques


def analyser(cv, annonce, entreprise="", seuil=30):
    """{taux, presents: [(terme, n)], absents: [(terme, n)], format: [...], mesure: « coffre » ou
    « application »}."""
    ats = _ats()
    texte = ats.texte_annonce(annonce)
    ignores = ats.a_ignorer(annonce) | a_ignorer(entreprise)
    presents, absents = ats.couverture(cv, texte, seuil, ignores)
    # Un numéro (« tel 032 889 ») n'est jamais un mot-clé.
    presents = [(t, n) for t, n in presents if not re.search(r"\d", t)]
    absents = [(t, n) for t, n in absents if not re.search(r"\d", t)]
    total = len(presents) + len(absents)
    return {"taux": round(100.0 * len(presents) / total) if total else None,
            "presents": [list(x) for x in presents], "absents": [list(x) for x in absents],
            "format": list(ats.format_cv(cv)), "mesure": "application" if ats.__name__ == "jobsearch.ats" else "coffre"}
