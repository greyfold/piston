"""Couverture des mots-clés d'un CV face à l'annonce.

Par `jobsearch.ats`, la mesure de la méthode « Candidature ciblée » livrée avec l'application.
On lui passe la marque de l'employeur et les mots de l'annonce qui ne sont pas des mots-clés
(pronoms, « missions », numéros de téléphone).
"""

import re

from . import ats
from .prefilter import plier

# Mots fréquents dans une annonce qui ne sont pas des mots-clés de CV.
PAS_DES_MOTS_CLES = {
    "vous", "vos", "votre", "nous", "notre", "nos", "leur", "leurs", "missions", "vos missions",
    "votre profil", "profil", "mission", "poste", "postes", "domaines", "domaine", "vous collaborez",
    "you", "your", "our", "we", "us", "role", "the role", "about us", "what you", "you will",
    "tel", "telephone", "e mail", "email", "www", "ch", "sa", "ag", "en tant que", "tant que",
    "bonnes connaissances", "capacite", "assurant",
}


def a_ignorer(entreprise):
    marques = {m for m in re.split(r"[^a-z0-9]+", plier(entreprise or "")) if len(m) >= 3}
    return PAS_DES_MOTS_CLES | marques


def analyser(cv, annonce, entreprise="", seuil=30):
    """{taux, presents: [(terme, n)], absents: [(terme, n)], format: [...]}."""
    texte = ats.texte_annonce(annonce)
    ignores = ats.a_ignorer(annonce) | a_ignorer(entreprise)
    presents, absents = ats.couverture(cv, texte, seuil, ignores)
    # Un numéro (« tel 032 889 ») n'est jamais un mot-clé.
    presents = [(t, n) for t, n in presents if not re.search(r"\d", t)]
    absents = [(t, n) for t, n in absents if not re.search(r"\d", t)]
    total = len(presents) + len(absents)
    return {"taux": round(100.0 * len(presents) / total) if total else None,
            "presents": [list(x) for x in presents], "absents": [list(x) for x in absents],
            "format": list(ats.format_cv(cv))}
