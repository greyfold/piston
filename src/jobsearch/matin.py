"""Alerte du matin : combien d'offres nouvelles, vraiment nouvelles.

Compte les annonces vues pour la première fois depuis la dernière alerte, qui ont
passé le pré-tri (nouvelle, A ou B), et qui ne sont :
- ni un doublon d'une offre déjà au registre (les doublons sûrs sont fusionnés par
  le passage et passent en « doublon » ; ils ne comptent pas) ;
- ni, selon toute apparence, un poste auquel la personne a déjà postulé : même employeur et
  intitulé proche d'une candidature engagée (Go, envoyée, entretien, offre, refus).
  Celles-là sont listées à part, « déjà postulé ? », avec la candidature en regard.

Lancée par l'application une fois le passage du matin terminé (`jobsearch echeances`). Ne
modifie rien au registre : elle lit et elle prévient.
"""

import datetime as dt
import time

from . import doublons
from .db import ENVOYEES, PIPELINE, maintenant

ENGAGEES = sorted(set(PIPELINE) | set(ENVOYEES))
A_TRANCHER = ("nouvelle", "A", "B")


def depuis(reg, maintenant_dt=None):
    """Début de la période : la dernière alerte, sinon 24 h."""
    derniere = reg.meta("alerte_matin_le")
    if derniere:
        return derniere
    return ((maintenant_dt or dt.datetime.now()) - dt.timedelta(hours=24)).isoformat(sep=" ", timespec="minutes")


def deja_postule(fiche, engagees):
    """La candidature engagée à laquelle cette annonce ressemble, ou None."""
    for e in engagees:
        if e["empreinte"] == fiche["empreinte"]:
            continue
        niveau, _ = doublons.comparer(fiche, e)
        if niveau:
            return e
    return None


def bilan(reg, debut):
    """Ce qui est arrivé depuis `debut` (AAAA-MM-JJ HH:MM)."""
    arrivees = [dict(r) for r in reg.cx.execute(
        "SELECT empreinte FROM annonces WHERE cree_le >= ? ORDER BY cree_le", (debut,))]
    fiches = [reg.get(r["empreinte"]) for r in arrivees]
    engagees = reg.liste(ENGAGEES)
    nouvelles, deja, doublons_, ecartees = [], [], [], []
    for f in fiches:
        if f["statut"] == "doublon":
            doublons_.append(f)
        elif f["statut"] in A_TRANCHER:
            e = deja_postule(f, engagees)
            (deja.append((f, e)) if e else nouvelles.append(f))
        elif f["statut"] in ("C", "expiree"):
            ecartees.append(f)
    return {"nouvelles": nouvelles, "deja_postule": deja, "doublons": doublons_, "ecartees": ecartees,
            "total": len(fiches), "courrier": courrier_lu(reg, debut)}


def courrier_lu(reg, debut):
    """Les réponses lues dans les mails depuis `debut` : ce que le passage a mis à jour, et ce
    qui attend la personne (employeur pas reconnu, offre à lire…)."""
    from .courrier import _schema, motif_court
    _schema(reg)
    lignes = []
    for c in reg.cx.execute(
            "SELECT c.classe, c.etat, c.message, a.entreprise, a.motif_refus FROM courriers c "
            "LEFT JOIN annonces a ON a.empreinte=c.empreinte WHERE c.lu_le >= ? AND c.etat != 'ignore' "
            "AND (c.message IS NULL OR c.message NOT LIKE 'déjà%%') ORDER BY c.recu_le", (debut,)):
        qui = c["entreprise"] or "employeur à identifier"
        if c["etat"] == "a_trancher":
            lignes.append("à trancher : %s (%s)" % (qui, c["message"] or c["classe"]))
        elif c["classe"] == "refus":
            lignes.append("refus %s (%s)" % (qui, motif_court(c["motif_refus"]).lower()))
        else:
            lignes.append({"entretien": "entretien %s, à confirmer", "accuse": "accusé de réception %s",
                           "offre": "offre %s"}.get(c["classe"], "%s") % qui)
    return lignes


def _note(reg, emp):
    try:
        from .evaluation import lire
        e = lire(reg, emp)
        return e["note_globale"] if e else None
    except Exception:
        return None


def message(reg, b, passage=None):
    """(titre, texte) de l'alerte, lisible sur un téléphone."""
    n = len(b["nouvelles"])
    titre = ("%d nouvelle%s offre%s ce matin" % (n, "s" if n > 1 else "", "s" if n > 1 else "")) if n \
        else "Aucune nouvelle offre ce matin"
    lignes = []
    if passage and passage.get("statut") != "complet":
        lignes.append("⚠ Passage %s : %s" % (passage.get("statut") or "absent", (passage.get("resume") or "").lstrip("⚠ ")[:120]))
    notees = sorted(b["nouvelles"], key=lambda f: -(_note(reg, f["empreinte"]) or -1))
    for f in notees[:5]:
        note = _note(reg, f["empreinte"])
        delai = (" · avant le %s" % dt.date.fromisoformat(f["delai_postulation"]).strftime("%d.%m")
                 if f.get("delai_postulation") else "")
        lignes.append("• %s — %s%s%s" % (f["entreprise"], f["titre"][:60],
                                          " (%d/100)" % note if note is not None else "", delai))
    if n > 5:
        lignes.append("… et %d autre%s sur le tableau de bord." % (n - 5, "s" if n - 5 > 1 else ""))
    if b["deja_postule"]:
        lignes.append("Déjà postulé ? %d : %s" % (len(b["deja_postule"]), "; ".join(
            "%s (comme ta candidature « %s », %s)" % (f["entreprise"], e["titre"][:40], e["statut"])
            for f, e in b["deja_postule"][:3])))
    if b.get("courrier"):
        lignes.append("Courrier : " + " ; ".join(b["courrier"][:4]) + (" …" if len(b["courrier"]) > 4 else ""))
    autres = []
    if b["doublons"]:
        autres.append("%d déjà connue%s sur une autre plateforme" % (len(b["doublons"]), "s" if len(b["doublons"]) > 1 else ""))
    if b["ecartees"]:
        autres.append("%d écartée%s par le pré-tri" % (len(b["ecartees"]), "s" if len(b["ecartees"]) > 1 else ""))
    if autres:
        lignes.append("Aussi : " + ", ".join(autres) + ".")
    return titre, "\n".join(lignes) or "Rien de neuf depuis hier."


def passage_du_jour(reg, jour=None):
    jour = (jour or dt.date.today()).isoformat()
    r = reg.cx.execute("SELECT * FROM runs WHERE debut >= ? ORDER BY id DESC LIMIT 1", (jour,)).fetchone()
    return dict(r) if r else None


def attendre_passage(reg, minutes=90, pause_s=60, horloge=time):
    """Attend que le passage du jour soit terminé (complet ou partiel), au plus `minutes`."""
    fin = horloge.monotonic() + minutes * 60
    while True:
        p = passage_du_jour(reg)
        if p and p["statut"] in ("complet", "partiel"):
            return p
        if horloge.monotonic() >= fin:
            return p
        horloge.sleep(pause_s)
        reg.cx.commit()                      # relire la base écrite par le passage


def alerter(reg, envoyer=None, attendre_min=90):
    """Construit et envoie l'alerte ; note l'heure pour la prochaine fois."""
    from .notify import notifier
    envoyer = envoyer or notifier
    passage = attendre_passage(reg, attendre_min) if attendre_min else passage_du_jour(reg)
    debut = depuis(reg)
    b = bilan(reg, debut)
    titre, texte = message(reg, b, passage or {"statut": "absent", "resume": "aucun passage ce matin"})
    envoyer("Piston · " + titre, texte, lien="/")
    reg.poser_meta("alerte_matin_le", maintenant())
    reg.commit()
    return {"titre": titre, "texte": texte, "nouvelles": len(b["nouvelles"]),
            "deja_postule": len(b["deja_postule"]), "doublons": len(b["doublons"]), "ecartees": len(b["ecartees"])}


def main(argv=None):
    """`python -m jobsearch.matin [--essai] [--attendre MIN]` : l'alerte du matin."""
    import argparse
    from .db import Registre
    p = argparse.ArgumentParser(prog="jobsearch.matin", description="Alerte du matin : offres vraiment nouvelles.")
    p.add_argument("--essai", action="store_true", help="affiche l'alerte sans l'envoyer ni la noter")
    p.add_argument("--attendre", type=int, default=90, help="minutes d'attente du passage du jour (0 : pas d'attente)")
    a = p.parse_args(argv)
    reg = Registre()
    if a.essai:
        b = bilan(reg, depuis(reg))
        titre, texte = message(reg, b, passage_du_jour(reg))
        print(titre + "\n" + texte)
        return
    r = alerter(reg, attendre_min=a.attendre)
    print(r["titre"] + "\n" + r["texte"])


if __name__ == "__main__":
    main()
