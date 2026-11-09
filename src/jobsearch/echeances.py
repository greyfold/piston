"""Ce qui est dû maintenant : l'application Mac l'appelle toutes les cinq minutes (`jobsearch echeances`).

Tout tourne dans l'application, rien n'est installé à côté (pas d'agent launchd) :
- le passage du matin, une fois par jour, dès l'heure du réglage `passage.heure` (Mac en veille
  ou application fermée à cette heure : il part au réveil ou à l'ouverture, comme launchd) ;
- l'alerte du matin (offres vraiment nouvelles), une fois le passage du jour terminé ;
- la surveillance, au plus une fois par heure : passage manquant, objectif de la semaine.

Chaque tâche ne part qu'une fois : la dernière exécution est notée dans la base (meta). Les
fonctions de chaque tâche restent celles de la ligne de commande (`passage`, `surveiller`,
`python -m jobsearch.matin`).
"""

import datetime as dt

from .config import reglages

SURVEILLANCE_MIN = 60
# Le passage léger de l'accueil (premières offres d'une installation neuve) ne compte pas
# comme passage du matin.
HORS_MATIN = ("accueil",)


def _heure(texte, defaut="06:30"):
    try:
        h, m = str(texte or defaut).split(":")
        return dt.time(int(h), int(m))
    except ValueError:
        return _heure(defaut)


def passage_du_jour(reg, jour):
    r = reg.cx.execute(
        "SELECT * FROM runs WHERE debut >= ? AND COALESCE(declencheur, '') NOT IN (%s) ORDER BY id DESC LIMIT 1"
        % ",".join("?" * len(HORS_MATIN)), (jour.isoformat(), *HORS_MATIN)).fetchone()
    return dict(r) if r else None


def dues(reg, maintenant=None):
    """Les tâches dues à cet instant, dans l'ordre où elles doivent tourner."""
    now = maintenant or dt.datetime.now()
    jour = now.date()
    out = []
    p = passage_du_jour(reg, jour)
    if p is None and now.time() >= _heure((reglages().get("passage") or {}).get("heure")):
        out.append("passage")
    elif p and p["statut"] in ("complet", "partiel") and (reg.meta("alerte_matin_le") or "")[:10] != jour.isoformat():
        out.append("alerte")
    derniere = reg.meta("echeance_surveillance")
    if not derniere or now - dt.datetime.fromisoformat(derniere) >= dt.timedelta(minutes=SURVEILLANCE_MIN):
        out.append("surveillance")
    return out


def executer(reg, maintenant=None, taches=None):
    """Lance les tâches dues ; rend {tâche: résultat}. Une tâche en échec n'arrête pas les suivantes."""
    now = maintenant or dt.datetime.now()
    taches = taches or TACHES
    bilan = {}
    for nom in dues(reg, now):
        try:
            bilan[nom] = taches[nom](reg, now)
        except Exception as e:                       # noté, et la tâche suivante tourne
            bilan[nom] = {"erreur": str(e)}
        if nom == "surveillance":
            reg.poser_meta("echeance_surveillance", now.isoformat(sep=" ", timespec="minutes"))
            reg.commit()
    return bilan


def _passage(reg, now):
    from .pipeline import Passage
    try:
        r = Passage(reg, declencheur="application").lancer()
    except RuntimeError as e:                        # un passage lancé à la main est déjà en cours
        return {"statut": "deja_en_cours", "message": str(e)}
    return {"statut": r["statut"], "resume": r["resume"]}


def _alerte(reg, now):
    from .matin import alerter
    r = alerter(reg, attendre_min=0)
    return {"titre": r["titre"], "nouvelles": r["nouvelles"]}


def _surveillance(reg, now):
    from .pipeline import rappel_rythme, surveiller
    return dict(surveiller(reg, now), rythme=rappel_rythme(reg, now))


TACHES = {"passage": _passage, "alerte": _alerte, "surveillance": _surveillance}
