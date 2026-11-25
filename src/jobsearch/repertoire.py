"""Répertoire des sources de recherche d'emploi (décision du 24.10, piste E4).

Deux origines : `config/sources.yaml` (versionné, les sources connues au
départ) et les sources ajoutées à la main par la personne, depuis le tableau de
bord ou en conversation (`jobsearch source ajouter`), rangées dans la base.
Une source ajoutée n'est jamais interrogée automatiquement : il faut pour
cela écrire son connecteur, comme pour Job-Room et jobup. Une source dont le
connecteur existe (`sources/employeurs.py`, Indeed, LinkedIn) s'affiche « automatique »,
et « désactivée » (`actif` faux) quand la personne l'a éteinte.
"""

import json
import re
import unicodedata


from .config import lire_yaml, reglages
from .db import maintenant
from .sources.employeurs import BRANCHEES

ACCES = ["en session", "à brancher", "alertes e-mail", "interdit à la plateforme"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS repertoire (
  id TEXT PRIMARY KEY, nom TEXT NOT NULL, url TEXT, nature TEXT, acces TEXT,
  remarques TEXT, ajoutee_le TEXT NOT NULL
);
"""


class SourceRefusee(Exception):
    pass


def _schema(reg):
    reg.cx.executescript(SCHEMA)


def identifiant(nom):
    t = unicodedata.normalize("NFKD", nom)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40]


def du_fichier():
    return lire_yaml("sources.yaml")


def lister(reg):
    """{id: fiche} : le fichier d'abord, puis les ajouts manuels."""
    _schema(reg)
    from .sources.ajoutees import colonnes
    colonnes(reg)
    out = {sid: dict(f, id=sid, manuelle=False) for sid, f in du_fichier().items()}
    for r in reg.cx.execute("SELECT * FROM repertoire ORDER BY ajoutee_le"):
        out[r["id"]] = dict(r, manuelle=True)
        if r["connecteur"]:                     # branchée depuis la page Sources
            conn = json.loads(r["connecteur"])
            out[r["id"]].update(branchee=True, type=conn.get("type"), actif=bool(r["actif"] if r["actif"] is not None else 1),
                                acces="automatique" if (r["actif"] is None or r["actif"]) else "désactivée")
    from . import connecteurs
    for sid in (BRANCHEES | {"jobs-ch"}) & set(out):
        out[sid]["acces"] = "automatique"
    # Indeed (connecteur du compte Claude) et LinkedIn (Apify) sont interrogés à chaque passage
    # quand ils sont actifs : « automatique » eux aussi, et « désactivée » tant qu'ils ne le sont pas.
    if "indeed" in out:
        out["indeed"].update(acces="automatique", connecteur=True, actif=connecteurs.actif("indeed"))
    if "linkedin" in out:
        out["linkedin"].update(acces="automatique", actif=bool((reglages().get("linkedin") or {}).get("actif")))
    return out


def ajouter(reg, nom, url="", nature="", acces="en session", remarques=""):
    nom, url = (nom or "").strip(), (url or "").strip()
    if not nom:
        raise SourceRefusee("le nom est obligatoire")
    if url and not re.match(r"https?://[^\s]+\.[^\s]+$", url):
        raise SourceRefusee("adresse illisible : %s (attendu : https://…)" % url)
    if acces not in ACCES:
        raise SourceRefusee("accès inconnu : %s (choix : %s)" % (acces, ", ".join(ACCES)))
    sid = identifiant(nom)
    if not sid:
        raise SourceRefusee("nom illisible : %s" % nom)
    if sid in lister(reg):
        raise SourceRefusee("la source « %s » est déjà au répertoire" % nom)
    reg.cx.execute("INSERT INTO repertoire (id, nom, url, nature, acces, remarques, ajoutee_le) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (sid, nom, url or None, (nature or "").strip() or None, acces,
                    (remarques or "").strip() or None, maintenant()))
    reg.commit()
    return sid


def changer_acces(reg, sid, acces):
    """Change l'accès noté d'une source ajoutée à la main (par exemple celui que l'examen de l'IA
    conseille). Une source branchée garde « automatique » : on la désactive à part."""
    _schema(reg)
    if acces not in ACCES:
        raise SourceRefusee("accès inconnu : %s (choix : %s)" % (acces, ", ".join(ACCES)))
    if sid in du_fichier():
        raise SourceRefusee("« %s » vient de config/sources.yaml : son accès se change dans le fichier" % sid)
    f = lister(reg).get(sid)
    if not f:
        raise SourceRefusee("aucune source ajoutée à la main sous « %s »" % sid)
    if f.get("branchee"):
        raise SourceRefusee("« %s » est branchée : la désactiver plutôt que changer son accès" % f["nom"])
    reg.cx.execute("UPDATE repertoire SET acces=? WHERE id=?", (acces, sid))
    reg.commit()


def retirer(reg, sid):
    """Retire une source ajoutée à la main. Les annonces qui la citent restent."""
    _schema(reg)
    if sid in du_fichier():
        raise SourceRefusee("« %s » vient de config/sources.yaml : à retirer dans le fichier" % sid)
    n = reg.cx.execute("DELETE FROM repertoire WHERE id=?", (sid,)).rowcount
    reg.commit()
    if not n:
        raise SourceRefusee("aucune source ajoutée à la main sous « %s »" % sid)
