"""Postes recherchés proposés par l'IA d'après le parcours (la base d'expérience tirée du CV).

Un appel à l'IA, que la personne lance depuis le champ « Postes recherchés » : 3 à 6 postes que sa base
permet de viser, chacun avec les autres intitulés sous lesquels il est publié, et l'élément de la base qui
l'appuie. Rien n'est ajouté au profil : la personne reprend un poste d'un clic, puis enregistre.

La dernière proposition vit dans la base (table `postes_proposes`), avec l'empreinte du parcours qu'elle a
lu : un parcours modifié depuis la rend « à refaire ». Les postes déjà recherchés n'y sont plus montrés.
"""

import hashlib
import json
import re

from pydantic import BaseModel, Field

from . import moteur
from .db import maintenant

SCHEMA = """CREATE TABLE IF NOT EXISTS postes_proposes (
  id INTEGER PRIMARY KEY CHECK (id = 1), postes TEXT NOT NULL, base TEXT NOT NULL, propose_le TEXT
)"""
MAX_POSTES = 6
MAX_VARIANTES = 4


class ParcoursVide(Exception):
    """Rien à lire : le CV n'est pas encore importé dans Ton parcours."""


class Poste(BaseModel):
    intitule: str = Field(description="l'intitulé tel qu'il est publié dans les annonces, court (« Chef de projet IA »)")
    variantes: list[str] = Field(default_factory=list,
                                 description="1 à 4 autres intitulés sous lesquels ce poste est publié, autres langues comprises")
    appui: str = Field("", description="ce qui, dans la base, rend ce poste crédible, 12 mots au plus")


class Proposition(BaseModel):
    postes: list[Poste] = Field(description="3 à 6 postes que la base permet de viser")


CONSIGNE = """Tu proposes à %(prenom)s des postes à rechercher sur les plateformes d'emploi, d'après sa base d'expérience (son CV rangé). Tu reçois la base, et dans le message les postes déjà recherchés, le niveau visé et la région.
- postes : 3 à 6 postes que la base permet réellement de viser : le métier exercé, ses suites naturelles, et un ou deux postes voisins où son expérience compte. Pas un poste déjà recherché ; pas deux fois le même métier.
- intitule : l'intitulé le plus courant dans les annonces de la région, court, sans « (H/F) », sans niveau ni taux d'activité, sauf si le niveau fait partie de l'intitulé usuel.
- variantes : 1 à 4 autres intitulés sous lesquels ce même poste est publié, dans les langues des annonces de la région (souvent l'anglais aussi). Des synonymes exacts, pas des postes différents.
- appui : ce qui, dans la base, rend ce poste crédible, 12 mots au plus, tiré de la base : rien d'inventé, aucun chiffre qui n'y soit pas.
Jamais de montant de salaire, rien sur la santé, la famille, l'origine, la religion ou l'âge."""


def _schema(reg):
    reg.cx.execute(SCHEMA)


def _texte(v, n):
    from .parcours import sans_montants
    return sans_montants(re.sub(r"\s+", " ", str(v or "")).strip().strip(",;"))[:n]


def empreinte(base):
    return hashlib.sha256((base or "").encode()).hexdigest()[:16]


def _nettoyer(postes, deja=()):
    """Les postes de l'IA, propres : chaque intitulé une seule fois (ni déjà recherché, ni répété d'un poste à
    l'autre), au plus MAX_POSTES postes de MAX_VARIANTES variantes. Un poste déjà recherché disparaît."""
    vus, out = {t.lower() for t in deja}, []
    for x in postes:
        if _texte(x.get("intitule"), 120).lower() in vus:
            continue
        titres = []
        for t in [x.get("intitule")] + list(x.get("variantes") or []):
            t = _texte(t, 120)
            if t and t.lower() not in vus:
                vus.add(t.lower())
                titres.append(t)
        if titres:
            out.append({"intitule": titres[0], "variantes": titres[1:MAX_VARIANTES + 1],
                        "appui": _texte(x.get("appui"), 140)})
    return out[:MAX_POSTES]


def proposer(reg, c=None):
    """L'appel à l'IA : rend les postes proposés (et les garde). ParcoursVide si la base est vide."""
    from . import parcours, profil
    base = parcours.rendu(reg)
    if not base:
        raise ParcoursVide("importe d'abord ton CV dans Ton parcours")
    p = profil.charger()
    deja = profil.intitules(profil.postes(p))
    niveau, region = profil.lire(p, "recherche.niveau") or "", profil.region(p)
    t = moteur.Tache("postes-proposes", CONSIGNE % {"prenom": parcours._prenom()},
                     contexte="<base>\n%s\n</base>" % base, schema=Proposition, effort="low", delai_s=180)
    message = "<deja_recherches>\n%s\n</deja_recherches>\n<niveau>%s</niveau>\n<region>%s</region>" % (
        "\n".join(deja) or "(aucun)", niveau or "(non précisé)", region or "(non précisée)")
    r = (c or moteur.ouvrir()).executer(t, message).objet
    postes = _nettoyer([x.model_dump() for x in r.postes], deja)
    _schema(reg)
    reg.cx.execute("INSERT INTO postes_proposes (id, postes, base, propose_le) VALUES (1,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET postes=excluded.postes, base=excluded.base, "
                   "propose_le=excluded.propose_le", (json.dumps(postes, ensure_ascii=False), empreinte(base), maintenant()))
    reg.commit()
    return postes


def etat(reg, p=None):
    """Pour le champ « Postes recherchés » : {"parcours": la base a-t-elle quelque chose, "postes": les postes
    proposés encore à reprendre, "propose": une proposition existe, "perime": le parcours a changé depuis}."""
    from . import parcours, profil
    base = parcours.rendu(reg)
    _schema(reg)
    r = reg.cx.execute("SELECT postes, base FROM postes_proposes WHERE id=1").fetchone()
    if r is None:
        return {"parcours": bool(base), "postes": [], "propose": False, "perime": False}
    deja = profil.intitules(profil.postes(profil.charger() if p is None else p))
    return {"parcours": bool(base), "postes": _nettoyer(json.loads(r[0]), deja), "propose": True,
            "perime": bool(base) and r[1] != empreinte(base)}
