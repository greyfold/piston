"""Ce qui est dû maintenant : l'application Mac l'appelle toutes les cinq minutes (`jobsearch echeances`).

Tout tourne dans l'application, rien n'est installé à côté (pas d'agent launchd) :
- le passage du matin, une fois par jour, dès l'heure du réglage `passage.heure` (Mac en veille
  ou application fermée à cette heure : il part au réveil ou à l'ouverture, comme launchd) ;
- l'alerte du matin (offres vraiment nouvelles), une fois le passage du jour terminé ;
- le rattrapage de l'IA, au plus une fois par demi-heure : les annonces à trancher que le passage
  n'a pas pu noter (machine virtuelle éteinte, Claude déconnecté) reçoivent leur note et leur
  pré-sélection dès que l'IA répond de nouveau ;
- la surveillance, au plus une fois par heure : passage manquant, objectif de la semaine.

Chaque tâche ne part qu'une fois : la dernière exécution est notée dans la base (meta). Les
fonctions de chaque tâche restent celles de la ligne de commande (`passage`, `surveiller`,
`python -m jobsearch.matin`).
"""

import datetime as dt

from .config import reglages

SURVEILLANCE_MIN = 60
RATTRAPAGE_MIN = 30
# Le passage léger de l'accueil (premières offres d'une installation neuve) ne compte pas
# comme passage du matin.
HORS_MATIN = ("accueil",)
# Un passage tué (veille, fermeture) repart une fois ; s'il meurt encore, on n'insiste pas avant demain.
REPRISES_PAR_JOUR = 1


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


def _reprises(reg, jour):
    """Passages du matin du jour déjà repartis après une interruption : on les compte parmi les
    interrompus, moins le premier (qui n'était pas une reprise)."""
    n = reg.cx.execute(
        "SELECT COUNT(*) FROM runs WHERE debut >= ? AND statut='interrompu' AND COALESCE(declencheur, '') NOT IN (%s)"
        % ",".join("?" * len(HORS_MATIN)), (jour.isoformat(), *HORS_MATIN)).fetchone()[0]
    return max(n - 1, 0)


def dues(reg, maintenant=None):
    """Les tâches dues à cet instant, dans l'ordre où elles doivent tourner."""
    from .pipeline import clore_interrompus
    now = maintenant or dt.datetime.now()
    jour = now.date()
    out = []
    clore_interrompus(reg)               # un passage tué ne doit pas compter comme celui du jour
    p = passage_du_jour(reg, jour)
    if p and p["statut"] == "interrompu" and _reprises(reg, jour) < REPRISES_PAR_JOUR:
        p = None                         # une reprise, une seule fois par jour de panne
    if p is None and now.time() >= _heure((reglages().get("passage") or {}).get("heure")):
        out.append("passage")
    elif p and p["statut"] in ("complet", "partiel") and (reg.meta("alerte_matin_le") or "")[:10] != jour.isoformat():
        out.append("alerte")
    if p and p["statut"] in ("complet", "partiel") and _echue(reg, "rattrapage", now, RATTRAPAGE_MIN) \
            and sans_note(reg):
        out.append("rattrapage")
    if _echue(reg, "surveillance", now, SURVEILLANCE_MIN):
        out.append("surveillance")
    return out


def _echue(reg, nom, now, minutes):
    derniere = reg.meta("echeance_" + nom)
    return not derniere or now - dt.datetime.fromisoformat(derniere) >= dt.timedelta(minutes=minutes)


def sans_note(reg):
    """Les annonces à trancher que l'IA n'a pas encore notées (profil vide : aucune)."""
    from . import evaluation
    try:
        texte_profil = evaluation.profil()
    except FileNotFoundError:
        return []
    return evaluation.a_evaluer(reg, texte_profil=texte_profil)


# Tâches dont la dernière tentative est notée, réussie ou non : elles ne reviennent qu'après leur délai.
NOTEES = ("rattrapage", "surveillance")


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
        if nom in NOTEES:
            reg.poser_meta("echeance_" + nom, now.isoformat(sep=" ", timespec="minutes"))
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


def _rattrapage(reg, now):
    """Note et pré-sélectionne ce que le passage a laissé sans note, si l'IA répond ; sinon on
    réessaie à la prochaine échéance. Même lot et même bilan que l'étape « evaluation » du passage."""
    from . import evaluation, moteur
    from .pipeline import verrou_sans_attendre
    with verrou_sans_attendre() as libre:
        if not libre:                      # un passage tourne : il traite lui-même ce lot
            return {"ignore": "un passage est en cours"}
        return _rattraper(reg, evaluation, moteur)


def _rattraper(reg, evaluation, moteur):
    lot = sans_note(reg)[:int((reglages().get("passage") or {}).get("evaluations_par_passage", 30))]
    if not lot:
        return {"a_evaluer": 0}
    try:
        c = moteur.ouvrir()
    except moteur.MoteurImpossible as e:
        return {"a_evaluer": len(lot), "ignore": str(e)}
    return dict(evaluation.evaluer_lot(reg, lot, c=c), a_evaluer=len(lot))


TACHES = {"passage": _passage, "alerte": _alerte, "rattrapage": _rattrapage, "surveillance": _surveillance}
