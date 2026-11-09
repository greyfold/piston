"""Ingestion : une annonce vue ne revient jamais (règles de `annonces.py ingerer`, L331).

Rapprochement d'abord par l'identifiant de la plateforme (`source:id`), puis
par l'empreinte entreprise + titre. Une annonce expirée qui réapparaît repasse
en « nouvelle ». Le texte n'est remplacé que par un texte nettement plus long.
"""

from dataclasses import asdict

import datetime as dt

from .dates import delai_postulation, fin_publication
from .db import aujourdhui
from .doublons import principal_de
from .fingerprint import empreinte


def regrouper(annonces):
    """Une même annonce trouvée par plusieurs requêtes n'en fait qu'une."""
    vues = {}
    for a in annonces:
        cle = (a.source, a.id_source)
        if cle in vues:
            for q in a.requetes:
                if q not in vues[cle].requetes:
                    vues[cle].requetes.append(q)
        else:
            vues[cle] = a
    return list(vues.values())


def cle_source(a):
    return "%s:%s" % (a.source, a.id_source) if a.id_source else None


def connue(reg, a):
    """L'empreinte déjà au registre pour cette annonce, ou None."""
    ks = cle_source(a)
    emp = (ks and reg.par_id_source(ks)) or empreinte(a.entreprise, a.titre)
    return emp if reg.get(emp) else None


def ingerer(reg, annonces):
    bilan = {"nouvelles": [], "revues": [], "enrichies": [], "reapparues": []}
    for a in annonces:
        b = asdict(a) if not isinstance(a, dict) else a
        entreprise, titre = (b.get("entreprise") or "?").strip(), (b.get("titre") or "").strip()
        if not titre:
            continue
        source = (b.get("source") or "?").strip()
        ks = "%s:%s" % (source, b["id_source"]) if b.get("id_source") else None
        emp = (ks and reg.par_id_source(ks)) or empreinte(entreprise, titre)
        f = reg.get(emp)
        if f and f.get("statut") == "doublon" and reg.get(principal_de(f) or ""):
            emp = principal_de(f)          # doublon fusionné : la vue revient à la fiche principale
            f = reg.get(emp)
        if f is None:
            f = {"empreinte": emp, "entreprise": entreprise, "titre": titre,
                 "lieu": b.get("lieu") or "", "canton": b.get("canton"),
                 "url": b.get("url") or "", "url_postuler": b.get("url_postuler") or "",
                 "sources": [], "ids_source": [], "requetes": [],
                 "publiee_le": b.get("publiee_le") or "", "vue_le": aujourdhui(),
                 "revue_le": aujourdhui(), "occurrences": 0, "statut": "nouvelle",
                 "texte": b.get("texte") or "",
                 "texte_origine": ("plateforme" if b.get("detail_charge") else "extrait")
                 if b.get("texte") else None, "taux_min": b.get("taux_min"),
                 "taux_max": b.get("taux_max"), "permanent": b.get("permanent"),
                 "cv_genere": False}
            bilan["nouvelles"].append(emp)
            reg.noter(emp, "vue · source %s" % source)
        else:
            f["revue_le"] = aujourdhui()
            bilan["revues"].append(emp)
            if f.get("statut") == "expiree":
                f["statut"] = "nouvelle"
                bilan["reapparues"].append(emp)
                reg.noter(emp, "réapparue · source %s" % source)

        f["occurrences"] = (f.get("occurrences") or 0) + 1
        if source not in f["sources"]:
            f["sources"].append(source)
        if ks and ks not in f["ids_source"]:
            f["ids_source"].append(ks)
        for q in b.get("requetes") or []:
            if q not in f["requetes"]:
                f["requetes"].append(q)
        fin = fin_publication(b.get("fin_publication"), b.get("publiee_le") or f.get("publiee_le"))
        if fin and (not f.get("fin_publication") or fin > f["fin_publication"]):
            f["fin_publication"] = fin
        for champ in ("lieu", "url", "publiee_le", "canton", "url_postuler",
                      "taux_min", "taux_max", "permanent"):
            if f.get(champ) in (None, "") and b.get(champ) not in (None, ""):
                f[champ] = b[champ]
                if emp not in bilan["enrichies"] and emp not in bilan["nouvelles"]:
                    bilan["enrichies"].append(emp)
        ancien, neuf = f.get("texte") or "", b.get("texte") or ""
        # Un résumé du coffre cède devant le texte original lu en entier, quelle que
        # soit sa longueur. Sinon, seul un texte nettement plus long remplace l'ancien
        # (la recherche ne sert souvent qu'un extrait).
        original = b.get("detail_charge") and neuf and f.get("texte_origine") != "plateforme"
        if original or len(neuf) > max(len(ancien) * 2, len(ancien) + 500):
            if original and ancien:
                f.setdefault("extra", {})["resume_coffre"] = ancien
            f["texte"] = neuf
            f["texte_origine"] = "plateforme" if b.get("detail_charge") else "extrait"
            f["langue"] = None
            if ancien:
                reg.noter(emp, "texte remplacé · %d → %d caractères" % (len(ancien), len(neuf)))
            if emp not in bilan["enrichies"] and emp not in bilan["nouvelles"]:
                bilan["enrichies"].append(emp)
        if f.get("texte") and not f.get("delai_postulation"):
            pub = f.get("publiee_le")
            f["delai_postulation"] = delai_postulation(
                f["texte"], dt.date.fromisoformat(pub[:10]) if pub and len(pub) >= 10 else None)
        reg.enregistrer(f)
    reg.commit()
    return bilan
