"""Échéances : relances (J+10, puis J+21), silence, et preuves Job-Room.

Les relances reprennent `annonces.py relances` (L668). La preuve Job-Room est
due avant le 5 du mois qui suit la postulation (obligation envers l'ORP) :
le tableau de bord la montre avec un compte à rebours, au lieu de tenter la
saisie la nuit dans une session de navigateur fermée (constat F-08).

Le rythme compte les postulations de la semaine (lundi à dimanche) et du mois
civil, face aux objectifs que la personne s'est fixés : chacun est facultatif
(0, aucun) et l'option « Objectifs » de l'interface les coupe tous. Pas de maximum.
"""

import datetime as dt

from .db import ENVOYEES, PIPELINE, plus_jours

RELANCE_1 = 10
SILENCE = 21


def _d(v):
    return dt.date.fromisoformat(str(v)[:10])


def echeance_jobroom(postulee_le):
    d = _d(postulee_le)
    an, mois = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return dt.date(an, mois, 5)


def relances(reg, aujourdhui=None, horizon=7):
    auj = aujourdhui or dt.date.today()
    dues, bientot, sans_date, a_classer = [], [], [], []
    for f in reg.liste(PIPELINE, ordre="echeance, entreprise"):
        ech = f.get("echeance") or f.get("relance_le")
        if f["statut"] == "candidature" and not f.get("postulee_le"):
            sans_date.append(f)
            continue
        if not ech:
            if f["statut"] != "candidature":
                continue
            ech = plus_jours(f["postulee_le"], RELANCE_1)
        e = _d(ech)
        f["_echeance"], f["_retard"] = e, (auj - e).days
        if (f.get("prochaine_action") or "").startswith("Considérer comme sans réponse"):
            # Deux relances faites : plus rien à envoyer, seulement à classer.
            if e <= auj:
                a_classer.append(f)
        elif e <= auj:
            dues.append(f)
        elif e <= auj + dt.timedelta(days=horizon):
            bientot.append(f)
    dues.sort(key=lambda f: f["_echeance"])
    bientot.sort(key=lambda f: f["_echeance"])
    return {"dues": dues, "bientot": bientot, "sans_date": sans_date, "a_classer": a_classer}


def sans_reponse(reg, aujourdhui=None):
    auj = aujourdhui or dt.date.today()
    out = []
    for f in reg.liste(["candidature"]):
        if f.get("postulee_le") and not f.get("reponse_le"):
            n = (auj - _d(f["postulee_le"])).days
            if n >= SILENCE:
                f["_jours"] = n
                out.append(f)
    return sorted(out, key=lambda f: -f["_jours"])


def jobroom(reg, aujourdhui=None):
    """Preuves à saisir (délai en cours), à vérifier (délai passé, aucune trace
    au registre), et refus à reporter sur la ligne existante."""
    auj = aujourdhui or dt.date.today()
    a_saisir, a_verifier = [], []
    for f in reg.liste(ENVOYEES, ordre="postulee_le DESC"):
        if not f.get("postulee_le") or f.get("jobroom_saisie_le"):
            continue
        lim = echeance_jobroom(f["postulee_le"])
        f["_limite"], f["_jours_restants"] = lim, (lim - auj).days
        (a_saisir if lim >= auj else a_verifier).append(f)
    a_saisir.sort(key=lambda f: f["_limite"])
    refus = [f for f in reg.liste(["refus"]) if f.get("jobroom_maj") == "a_faire"]
    return {"a_saisir": a_saisir, "a_verifier": a_verifier, "refus_a_reporter": refus}


def rythme(reg, aujourdhui=None):
    """Postulations envoyées cette semaine et ce mois, face aux objectifs. Une période
    sans objectif (0), ou l'option coupée, n'est pas « active ». Alerte à partir du
    vendredi si la semaine a un objectif et ne l'a pas atteint."""
    from . import profil
    auj = aujourdhui or dt.date.today()
    o = profil.objectifs_rythme()
    option = profil.option("objectifs")
    lundi = auj - dt.timedelta(days=auj.weekday())
    periodes = {"semaine": (lundi, lundi + dt.timedelta(days=6), o["semaine"]),
                "mois": (auj.replace(day=1), auj, o["mois"])}
    out = {}
    for nom, (debut, fin, mini) in periodes.items():
        n = reg.cx.execute("SELECT COUNT(*) FROM annonces WHERE statut != 'doublon' "
                           "AND substr(postulee_le, 1, 10) BETWEEN ? AND ?",
                           (debut.isoformat(), fin.isoformat())).fetchone()[0]
        out[nom] = {"n": n, "min": mini, "manque": max(mini - n, 0), "atteint": n >= mini, "debut": debut,
                    "actif": option and mini > 0}
    out["actif"] = out["semaine"]["actif"] or out["mois"]["actif"]
    out["alerte"] = auj.weekday() >= 4 and out["semaine"]["actif"] and not out["semaine"]["atteint"]
    return out
