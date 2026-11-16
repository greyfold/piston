"""Analyse de l'adéquation au poste, catégorie par catégorie.

La note d'adéquation (`evaluation.py`) dit « combien » ; ce module dit « où ». Pour chaque
catégorie (compétences, expérience, formation, langues), l'IA donne une note sur 100, une phrase
de synthèse, les atouts du profil et les écarts, et range chaque exigence majeure de l'annonce
dans une catégorie. Une catégorie où l'annonce n'exige rien est dite « sans objet » : elle n'a
pas de note.

L'analyse fait partie de la tâche « évaluation » : un seul appel à l'IA par annonce, le même
profil en contexte. Ce module en fixe les catégories, le schéma, la règle de la consigne, et ce
que Python vérifie avant d'écrire (`normaliser`). Le résultat est gardé avec l'évaluation
(`evaluations.resultat`) et part avec elle dans le dossier de candidature.

Rien de propre à une personne ici : les catégories sont les mêmes pour tout le monde, ce qui les
remplit vient du profil et de l'annonce.
"""

from typing import Literal

from pydantic import BaseModel, Field

# (clé, libellé, ce que la catégorie couvre) : l'ordre est celui de l'affichage.
CATEGORIES = (
    ("competences", "Compétences",
     "savoir-faire, méthodes, outils et technologies exigés, et qualités attendues quand l'annonce les nomme"),
    ("experience", "Expérience",
     "années et niveau d'expérience, secteur, type de structure, périmètre et responsabilités (équipe, budget) déjà tenus"),
    ("formation", "Formation",
     "diplômes, titres et certifications exigés ou souhaités"),
    ("langues", "Langues",
     "langues exigées et leur niveau"),
)
CLES = tuple(c for c, _, _ in CATEGORIES)
LIBELLES = {c: lib for c, lib, _ in CATEGORIES}
MAX_POINTS = 4            # atouts et écarts gardés par catégorie

Cle = Literal["competences", "experience", "formation", "langues"]


class Categorie(BaseModel):
    categorie: Cle
    demande: bool = Field(description="faux si l'annonce n'exige ni ne souhaite rien dans cette catégorie")
    note: int = Field(description="0 à 100 : part de ce que l'annonce demande dans cette catégorie que le profil couvre ; 0 si demande est faux")
    synthese: str = Field(description="une phrase en français : ce qui est demandé et où en est le profil")
    atouts: list[str] = Field(description="au plus 4 points du profil qui répondent à la demande, en français, avec la preuve (P-xx, poste, diplôme, certification)")
    ecarts: list[str] = Field(description="au plus 4 points demandés que le profil ne couvre pas ou seulement en partie, en français ; vide si aucun")


REGLE = ("- categories : analyse l'adéquation catégorie par catégorie, une entrée pour chacune : "
         + " ; ".join("%s (%s)" % (c, quoi) for c, _, quoi in CATEGORIES)
         + ". Pour chacune, note de 0 à 100 la part de ce que l'annonce demande que le profil couvre réellement, "
           "comme un recruteur la mesurerait : une exigence impérative non couverte pèse plus qu'un souhait. "
           "Dis en une phrase ce qui est demandé et où en est le profil, puis les atouts (avec leur preuve dans le "
           "profil) et les écarts. Si l'annonce ne demande rien dans une catégorie, demande = faux, note 0, listes "
           "vides : ne suppose pas d'exigence qu'elle ne formule pas. Chaque exigence de la liste « exigences » "
           "porte la catégorie dont elle relève.")


def normaliser(categories):
    """Ce que Python garde de la réponse : une entrée par catégorie connue, dans l'ordre de
    CATEGORIES, note bornée à 0-100 (0 et listes vides quand rien n'est demandé), au plus
    MAX_POINTS atouts et écarts, sans ligne vide."""
    vues = {}
    for c in categories or []:
        if c.categorie in vues:
            continue
        c.note = max(0, min(100, c.note)) if c.demande else 0
        propres = lambda points: [p.strip() for p in points if p and p.strip()][:MAX_POINTS]
        c.atouts, c.ecarts = (propres(c.atouts), propres(c.ecarts)) if c.demande else ([], [])
        c.synthese = (c.synthese or "").strip()
        vues[c.categorie] = c
    return [vues[cle] for cle in CLES if cle in vues]


def niveau(note):
    """Le palier d'une note, le même que celui de la note globale : haute (80 et plus), moyenne, basse."""
    return "haute" if note >= 80 else "moyenne" if note >= 60 else "basse"


def lire(resultat):
    """Les catégories d'une évaluation enregistrée, prêtes à afficher : [{categorie, libelle, demande,
    note, niveau, synthese, atouts, ecarts}]. Vide pour une évaluation d'avant cette analyse."""
    lignes = {}
    for c in (resultat or {}).get("categories") or []:
        cle = c.get("categorie")
        if cle in LIBELLES and cle not in lignes:
            note = int(c.get("note") or 0)
            lignes[cle] = dict(c, libelle=LIBELLES[cle], note=note, niveau=niveau(note),
                               demande=bool(c.get("demande")), atouts=c.get("atouts") or [],
                               ecarts=c.get("ecarts") or [])
    return [lignes[cle] for cle in CLES if cle in lignes]


def resume(resultat):
    """Une ligne pour les journaux et la ligne de commande : « compétences 85 · expérience 70 »."""
    return " · ".join("%s %d" % (c["libelle"].lower(), c["note"]) for c in lire(resultat) if c["demande"])
