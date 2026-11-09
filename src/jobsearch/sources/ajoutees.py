"""Sources ajoutées par l'utilisateur depuis la page Sources, sans code.

L'utilisateur colle l'adresse d'une page carrière et le nom de l'employeur.
`essayer()` reconnaît le logiciel qui sert la page (logiciels.py) ou, à défaut,
passe à la lecture par l'IA (page_ia.py), interroge la page et rend ce qu'elle
contient, sans rien enregistrer. `brancher()` enregistre la source au répertoire
avec son connecteur ; elle est interrogée dès le passage suivant. Une source
branchée se désactive d'un clic sans être retirée.

Le connecteur vit dans la table `repertoire` (colonne `connecteur`, JSON), avec
les autres sources ajoutées à la main.
"""

import json

from .. import repertoire
from .base import RouteCassee
from .logiciels import LIBELLES, construire, reconnaitre


def colonnes(reg):
    repertoire._schema(reg)
    noms = {r[1] for r in reg.cx.execute("PRAGMA table_info(repertoire)")}
    if "connecteur" not in noms:
        reg.cx.execute("ALTER TABLE repertoire ADD COLUMN connecteur TEXT")
    if "actif" not in noms:
        reg.cx.execute("ALTER TABLE repertoire ADD COLUMN actif INTEGER DEFAULT 1")
    reg.commit()


def connecteurs(reg, client):
    """{id: source} des sources ajoutées branchées et actives."""
    colonnes(reg)
    out = {}
    for r in reg.cx.execute("SELECT id, nom, connecteur, nature FROM repertoire "
                            "WHERE connecteur IS NOT NULL AND COALESCE(actif, 1)=1"):
        conn = json.loads(r["connecteur"])
        out[r["id"]] = construire(client, r["id"], conn.get("entreprise") or r["nom"], conn)
    return out


def essayer(client, url, entreprise, moteur=None):
    """Ce que la page donnerait, sans rien enregistrer : type, nombre d'offres, exemples."""
    conn = reconnaitre(url)
    sid = repertoire.identifiant(entreprise or url) or "essai"
    src = construire(client, sid, entreprise, conn)
    if moteur is not None and hasattr(src, "moteur"):
        src.moteur = moteur
    out = {"type": conn["type"], "libelle": LIBELLES[conn["type"]], "connecteur": conn, "url": url,
           "entreprise": entreprise}
    try:
        offres = src.offres()
        if not offres and conn["type"] == "page-ia":
            # Rien dans la page téléchargée : la liste est peut-être construite en JavaScript.
            src.rendu, src.cache = True, src.cache.with_name(src.cache.stem + "-rendu.json")
            offres = src.offres()
            if offres:
                conn = dict(conn, rendu=True)
                out["connecteur"] = conn
    except RouteCassee as e:
        return dict(out, ok=False, erreur=str(e))
    except Exception as e:                  # moteur indisponible, page illisible…
        return dict(out, ok=False, erreur="%s: %s" % (type(e).__name__, e))
    if not offres:
        return dict(out, ok=False, erreur="aucune offre lisible sur cette page, même rendue par Chrome. Essaie "
                                          "l'adresse de la liste des offres elle-même, ou abonne-toi aux alertes e-mail "
                                          "de l'employeur.")
    return dict(out, ok=True, n=len(offres), exemples=[(a.titre, a.lieu, a.url) for a in offres[:8]])


def brancher(reg, entreprise, url, conn, nature=""):
    """Enregistre la source et son connecteur. Renvoie son identifiant."""
    if not (entreprise or "").strip():
        raise repertoire.SourceRefusee("le nom de l'employeur est obligatoire")
    colonnes(reg)
    conn = dict(conn, entreprise=entreprise.strip())
    sid = repertoire.ajouter(reg, "Page carrière %s" % entreprise.strip(), url=url,
                             nature=nature or "Page carrière (%s)" % LIBELLES[conn["type"]], acces="à brancher")
    reg.cx.execute("UPDATE repertoire SET connecteur=?, actif=1 WHERE id=?", (json.dumps(conn, ensure_ascii=False), sid))
    reg.commit()
    return sid


def activer(reg, sid, oui=True):
    colonnes(reg)
    n = reg.cx.execute("UPDATE repertoire SET actif=? WHERE id=? AND connecteur IS NOT NULL", (int(oui), sid)).rowcount
    reg.commit()
    if not n:
        raise repertoire.SourceRefusee("aucune source branchée sous « %s »" % sid)
