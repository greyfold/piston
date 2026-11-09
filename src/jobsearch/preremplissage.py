"""Prépare et lance un pré-remplissage : vérifications, valeurs, documents.

Lancé depuis le tableau de bord dans un processus à part (la fenêtre Chrome
reste ouverte tant que la personne en a besoin), ou en ligne de commande :
`jobsearch preremplir <empreinte> [--jobroom]`.
"""

from pathlib import Path

from . import dossier as dmod
from .coordonnees import documents, valeurs_formulaire
from .db import ENVOYEES


class PreremplissageImpossible(Exception):
    pass


def _dossier(reg, f):
    """Le dossier produit par la plateforme, s'il est prêt ; None sinon."""
    d = dmod.etat(reg, f["empreinte"])
    if d and d["etat"] in ("pret", "a_revoir"):
        return Path(d["chemin"])
    return None


def _lire(chemin, nom):
    p = chemin / nom
    return p.read_text(encoding="utf-8") if p.exists() else ""


def employeur(reg, emp):
    """(url, valeurs, documents) pour le formulaire de l'employeur."""
    f = reg.get(emp)
    if not f:
        raise PreremplissageImpossible("annonce introuvable")
    if f["statut"] not in ("retenue",) + tuple(ENVOYEES):
        raise PreremplissageImpossible("pas de Go sur cette annonce")
    url = f.get("url_postuler") or f.get("url")
    if not url:
        raise PreremplissageImpossible("aucun lien de postulation connu")
    chemin = _dossier(reg, f)
    if not chemin:
        raise PreremplissageImpossible("le dossier n'est pas prêt")
    dmod.mettre_a_date(reg, emp)                # lettre à la date du jour
    lettre_md = _lire(chemin, "Lettre.md").split("\n---", 1)[-1]
    valeurs = valeurs_formulaire(f, _lire(chemin, "Envoi.md"), lettre_md)
    return url, valeurs, documents(chemin, f)


def noter_preuve(emp):
    """Appelé quand la ligne Job-Room est sauvegardée et constatée une seule fois dans la liste."""
    import datetime as dt
    from . import actions
    from .db import Registre
    from .notify import notifier
    reg = Registre()
    try:
        f = reg.get(emp)
        if f.get("jobroom_saisie_le"):
            return "Déjà notée au registre."
        actions.appliquer(reg, emp, "jobroom_saisie", {"date": dt.date.today().isoformat(),
                          "note": "Job-Room : saisie le %s (sauvegarde automatique, ligne constatée une fois dans la liste)"
                          % dt.date.today().isoformat()})
        notifier("Preuve Job-Room enregistrée", "%s — %s" % (f["entreprise"], f["titre"]))
        return "Notée au registre : « Saisie faite » le %s." % dt.date.today().strftime("%d.%m.%Y")
    finally:
        reg.fermer()


def canal_depuis(url):
    """« jobup », « SuccessFactors », … d'après l'adresse de la page de confirmation."""
    from urllib.parse import urlparse
    hote = urlparse(url or "").netloc.lower()
    for motif, nom in (("jobup", "jobup"), ("jobs.ch", "jobs.ch"), ("successfactors", "SuccessFactors"),
                       ("smartrecruiters", "SmartRecruiters"), ("myworkday", "Workday"), ("workday", "Workday"),
                       ("greenhouse", "Greenhouse"), ("lever.co", "Lever"), ("umantis", "umantis"),
                       ("workable", "Workable"), ("teamtailor", "Teamtailor"), ("personio", "Personio"),
                       ("job-room", "Job-Room")):
        if motif in hote:
            return nom
    return "portail %s" % hote.removeprefix("www.") if hote else "portail"


def noter_envoi(emp, url):
    """Appelé par le navigateur quand la page confirme l'envoi : « Marquer envoyée » (date du jour,
    canal d'après le site), lettre figée à cette date. Une seule fois, et seulement depuis un Go."""
    import datetime as dt
    from . import actions
    from .db import Registre
    from .notify import notifier
    reg = Registre()
    try:
        f = reg.get(emp)
        if not f or f["statut"] != "retenue":
            return "Déjà notée (statut « %s »)." % (f or {}).get("statut")
        canal = canal_depuis(url)
        actions.appliquer(reg, emp, "candidature", {
            "date": dt.date.today().isoformat(), "canal": canal,
            "note": "Envoi constaté sur la page de confirmation : %s" % url[:200]})
        dmod.mettre_a_date(reg, emp)
        from . import agent_claude
        agent_claude.apres_envoi(reg, emp)        # la preuve Job-Room part toute seule
        notifier("Candidature envoyée", "%s — %s (%s). Preuve Job-Room à saisir." % (f["entreprise"], f["titre"], canal))
        return "Notée envoyée le %s (%s) ; lettre datée de ce jour ; relance dans 10 jours." % (
            dt.date.today().strftime("%d.%m.%Y"), canal)
    finally:
        reg.fermer()


def jobroom(reg, emp):
    """(valeurs, jetons anti-doublon) pour la preuve Job-Room. Refuse tout ce qui ferait une
    preuve fausse : candidature pas partie, preuve déjà inscrite au registre."""
    from .courrier import jetons_employeur
    from .navigateur import valeurs_jobroom
    f = reg.get(emp)
    if not f:
        raise PreremplissageImpossible("annonce introuvable")
    if f["statut"] not in ENVOYEES or not f.get("postulee_le"):
        raise PreremplissageImpossible("pas de preuve Job-Room pour une candidature qui n'est pas partie")
    if f.get("jobroom_saisie_le"):
        raise PreremplissageImpossible("preuve déjà saisie le %s selon le registre" % f["jobroom_saisie_le"])
    if not (f.get("extra") or {}).get("adresse_employeur"):
        # Dossier produit avant que l'adresse ne soit cherchée avec lui : on la cherche maintenant.
        from . import adresses
        if adresses.renseigner(reg, emp):
            f = reg.get(emp)
    chemin = _dossier(reg, f)
    v = valeurs_jobroom(f, _lire(chemin, "Envoi.md") if chemin else "")
    jetons = jetons_employeur(f["entreprise"])[:2] or [f["entreprise"].lower()]
    return v, jetons


def lancer_en_arriere_plan(emp, jobroom_=False):
    """Ouvre le pré-remplissage dans le Chrome de la machine virtuelle (la page du tableau de bord
    répond tout de suite) ; la personne le voit dans « Navigateur ». Un seul à la fois, comme les
    sessions Claude : Chrome n'est tenu que par une tâche."""
    from . import agent_claude
    from .db import Registre
    reg = Registre()
    try:
        if agent_claude.occupante(reg):
            raise PreremplissageImpossible("Chrome est occupé par une autre tâche : attends qu'elle finisse")
        return agent_claude.lancer(reg, "pre-jobroom" if jobroom_ else "pre-formulaire", emp)
    except agent_claude.LancementImpossible as e:
        raise PreremplissageImpossible(str(e)) from e
    finally:
        reg.fermer()