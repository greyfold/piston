"""Le texte original des annonces, dans leur langue d'origine.

Les mots-clés du CV et de la lettre se prennent sur l'annonce telle que
l'employeur l'a écrite. Or une recherche ne sert souvent qu'un extrait de
l'annonce. Ce module va rechercher l'original sur la plateforme d'où vient
l'annonce.
"""

import datetime as dt
import re

import httpx

from .dates import delai_postulation, fin_publication
from .db import PIPELINE
from .prefilter import langue
from .sources.base import Annonce, RouteCassee

ACTIFS = ["nouvelle", "A", "B"] + PIPELINE
PRIORITE = {s: i for i, s in enumerate(["retenue", "A", "B", "entretien", "offre", "candidature", "nouvelle"])}


def a_recharger(reg, statuts=ACTIFS):
    """Annonces actives sans texte original et rattachées à une plateforme connue."""
    out = []
    for f in reg.liste(statuts):
        if f.get("texte_origine") in ("plateforme", "introuvable"):
            continue
        if any(i.split(":", 1)[0] in ("job-room", "jobup", "jobs-ch") for i in f.get("ids_source") or []):
            out.append(f)
    return sorted(out, key=lambda f: PRIORITE.get(f["statut"], 9))


def _retiree(e):
    """L'annonce a-t-elle disparu de la plateforme ? Seuls HTTP 404 et 410 le disent. Une limite de
    débit (429), une panne (5xx), un format changé ou un refus ne prouvent rien."""
    m = re.search(r"HTTP (\d{3})", str(e))
    return bool(m) and int(m.group(1)) in (404, 410)


def recharger(reg, f, sources):
    """Va chercher l'original. Renvoie True s'il a été trouvé et enregistré.

    Une panne passagère (429, 5xx, délai, connexion) laisse la fiche telle quelle : elle sera
    retentée au prochain passage. Seule une annonce retirée partout est marquée « introuvable »."""
    panne = False
    for ids in f.get("ids_source") or []:
        src, _, ident = ids.partition(":")
        if src not in sources or not ident:
            continue
        a = Annonce(source=src, id_source=ident, entreprise=f["entreprise"], titre=f["titre"])
        try:
            sources[src].detail(a)
        except RouteCassee as e:
            if not _retiree(e):
                panne = True              # panne passagère : à retenter, pas une annonce retirée
            continue                      # sinon retirée de cette plateforme : on essaie la suivante
        except httpx.HTTPError:
            panne = True                  # délai, connexion : panne passagère de cette fiche
            continue
        if a.texte and a.detail_charge:
            pub = f.get("publiee_le")
            ref = dt.date.fromisoformat(pub[:10]) if pub and len(pub) >= 10 else None
            reg.maj(f["empreinte"], delai_postulation=delai_postulation(a.texte, ref) or f.get("delai_postulation"),
                    fin_publication=fin_publication(a.fin_publication, pub) or f.get("fin_publication"))
            reg.maj(f["empreinte"], texte=a.texte, texte_origine="plateforme", langue=langue(a.texte),
                    url_postuler=f.get("url_postuler") or a.url_postuler or None)
            reg.noter(f["empreinte"], "texte original rechargé depuis %s (%d caractères)" % (src, len(a.texte)))
            reg.commit()
            return True
    if panne:
        return False
    reg.maj(f["empreinte"], texte_origine="introuvable")
    reg.noter(f["empreinte"], "texte original introuvable sur les plateformes (annonce retirée ?)")
    reg.commit()
    return False
