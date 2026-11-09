"""Les décisions de la personne, appliquées tout de suite au registre.

Reprend les règles de `annonces.py appliquer` (L937) : un clic du tableau de
bord change le registre dans la même transaction, il n'y a plus de file
d'attente à synchroniser (constat F-06). Une action refusée l'est avec sa
raison, et laisse une trace dans la table `actions`.
"""

import json
import re

from .db import ENVOYEES, aujourdhui, maintenant, plus_jours

RELANCE_1, RELANCE_2 = 10, 21

TYPES = {
    "go": "Go", "attendre": "Attendre", "pasgo": "Pas go", "remettre_b": "Remettre en B",
    "annuler_go": "Go annulé",
    "candidature": "Marquer envoyée", "entretien": "Entretien", "offre": "Offre",
    "refus": "Refus", "sans_suite": "Classée sans suite", "note": "Note", "echeance": "Échéance", "relance_faite": "Relance faite",
    "accuse": "Accusé de réception", "reponse": "Réponse reçue",
    "flag_rh": "Flag RH", "unflag_rh": "Retirer le flag RH",
    "jobroom_saisie": "Preuve Job-Room saisie", "jobroom_refus": "Refus reporté sur Job-Room",
    "annuler_refus": "Refus annulé",
    "employeur": "Employeur corrigé",
}
TRI = {"go", "attendre", "pasgo", "remettre_b", "annuler_go"}

# Une note qui dit le contraire du bouton : c'est le cas Deloitte (07 → 24.10).
_NEGATIF = re.compile(r"\b(pas\s*go|no\s*go|nogo|pasgo|ne\s+pas\s+postuler)\b", re.I)


class ActionRefusee(Exception):
    pass


def _date(v):
    v = (v or "").strip()
    if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ActionRefusee("date illisible : %s (format AAAA-MM-JJ)" % v)
    return v or aujourdhui()


def appliquer(reg, emp, type_, donnees=None):
    """Applique une action. Renvoie le message affiché ; lève ActionRefusee sinon.

    La transaction est ouverte et validée ici : l'action et son effet sur le
    registre sont écrits ensemble, ou pas du tout.
    """
    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in (donnees or {}).items()}
    try:
        msg = _appliquer(reg, emp, type_, d)
        _tracer(reg, emp, type_, d, "ok", msg)
        reg.commit()
        return msg
    except ActionRefusee as e:
        reg.cx.rollback()
        _tracer(reg, emp, type_, d, "refusee", str(e))
        reg.commit()
        raise


def _tracer(reg, emp, type_, d, resultat, message):
    reg.cx.execute("INSERT INTO actions (empreinte, type, donnees, cree_le, resultat, message) "
                   "VALUES (?,?,?,?,?,?)",
                   (emp, type_, json.dumps(d, ensure_ascii=False), maintenant(), resultat, message))


def _appliquer(reg, emp, t, d):
    if t not in TYPES:
        raise ActionRefusee("type d'action inconnu : %s" % t)
    f = reg.get(emp)
    if not f:
        raise ActionRefusee("aucune annonce %s au registre" % emp)
    ancien = f["statut"]
    note = d.get("note") or ""
    if t in TRI and ancien in ENVOYEES:
        raise ActionRefusee("décision de tri « %s » sur une annonce déjà en « %s » : refusée, "
                            "la candidature est conservée" % (TYPES[t], ancien))
    if t == "go" and _NEGATIF.search(note):
        raise ActionRefusee("Go avec une note qui dit « pas go » : action contradictoire, "
                            "rien n'est changé. Choisis le bon bouton.")
    if t == "go" and ancien == "retenue":
        return "Déjà retenue : rien à changer."

    ch = {}
    raisons = [r for r in (d.get("raisons") or []) if r]
    if t in ("go", "attendre", "pasgo", "annuler_go"):
        ch.update(raison_decision=raisons, raison_note=note or None, decision_le=aujourdhui())

    if t == "go":
        ch.update(statut="retenue", echeance=None,
                  prochaine_action="Dossier à produire" if not f.get("cv_genere") else "Relire et envoyer")
    elif t == "attendre":
        ch.update(statut="B", motif=d.get("motif") or f.get("motif"))
    elif t == "pasgo":
        ch.update(statut="C", passe_ecart=3, prochaine_action=None, echeance=None, relance_le=None,
                  motif=d.get("motif") or ("Pas go : " + ", ".join(raisons) if raisons
                                           else "Écartée depuis le tableau de bord"))
    elif t == "annuler_go":
        # La personne renonce à une candidature retenue : écartée, avec sa raison. Le dossier reste.
        if ancien != "retenue":
            raise ActionRefusee("seule une candidature retenue (Go) peut être annulée")
        ch.update(statut="C", passe_ecart=3, prochaine_action=None, echeance=None, relance_le=None,
                  motif="Go annulé" + (" : " + ", ".join(raisons) if raisons else "") + (" · " + note if note else ""))
    elif t == "remettre_b":
        ch.update(statut="B", limite=False,
                  motif="Remise en B depuis le tableau de bord (était : %s)" % (f.get("motif") or ancien))
    elif t == "candidature":
        date = _date(d.get("date"))
        ch.update(statut="candidature", postulee_le=date,
                  canal_envoi=d.get("canal") or f.get("canal_envoi"),
                  relance_le=plus_jours(date, RELANCE_1), echeance=plus_jours(date, RELANCE_1),
                  prochaine_action="Relancer si aucune réponse")
    elif t in ("entretien", "offre"):
        date = _date(d.get("date"))
        ch.update(statut=t, echeance=date, relance_le=None,
                  reponse_le=f.get("reponse_le") or aujourdhui(),
                  prochaine_action=d.get("prochaine") or (
                      "Préparer l'entretien" if t == "entretien" else "Évaluer l'offre"))
    elif t == "refus":
        date = _date(d.get("date"))
        ch.update(statut="refus", motif_refus=d.get("motif") or None,
                  motif=d.get("motif") or "Refus reçu le %s" % date,
                  reponse_le=f.get("reponse_le") or date,
                  prochaine_action=None, echeance=None, relance_le=None,
                  jobroom_maj="a_faire" if f.get("jobroom_saisie_le") else "non_concerne")
    elif t == "annuler_refus":
        # Un refus noté à tort (un accusé de réception pris pour un refus) : la candidature
        # reprend où elle était, sans réponse. Un refus déjà reporté sur Job-Room ne s'annule pas ici.
        if ancien != "refus" or not f.get("postulee_le"):
            raise ActionRefusee("seul le refus d'une candidature envoyée s'annule")
        if f.get("jobroom_maj") == "fait":
            raise ActionRefusee("refus déjà reporté sur Job-Room : à corriger d'abord sur Job-Room")
        ch.update(statut="candidature", motif=None, motif_refus=None, reponse_le=None,
                  jobroom_maj=None, relance_le=plus_jours(f["postulee_le"], RELANCE_1),
                  echeance=plus_jours(f["postulee_le"], RELANCE_1), prochaine_action="Relancer si aucune réponse")
    elif t == "sans_suite":
        # Silence après deux relances : pas une réponse négative, Job-Room n'est pas touché.
        if ancien not in ("candidature", "entretien"):
            raise ActionRefusee("seule une candidature envoyée se classe sans suite")
        ch.update(statut="refus", motif="Sans suite : aucune réponse après les relances",
                  prochaine_action=None, echeance=None, relance_le=None, jobroom_maj="non_concerne")
    elif t == "echeance":
        ch.update(echeance=_date(d.get("date")))
        if d.get("prochaine"):
            ch["prochaine_action"] = d["prochaine"]
    elif t == "relance_faite":
        n = 1 + sum(1 for j in reg.journal(emp) if "· relance n°" in j["texte"]
                    or j["texte"].startswith("relance n°"))
        if n == 1:
            ch.update(relance_le=plus_jours(aujourdhui(), RELANCE_2 - RELANCE_1),
                      prochaine_action="Seconde relance si toujours rien")
        else:
            ch.update(relance_le=None, prochaine_action="Considérer comme sans réponse")
        ch["echeance"] = ch["relance_le"]
        reg.noter(emp, "relance n°%d" % n)
    elif t == "accuse":
        ch.update(accuse_le=_date(d.get("date")))
    elif t == "reponse":
        ch.update(reponse_le=_date(d.get("date")))
    elif t == "employeur":
        # Employeur non divulgué, agence, adresse introuvable : ce que la personne a décidé
        # d'inscrire, repris tel quel par la preuve Job-Room. La rue peut rester vide.
        nom = d.get("entreprise") or f["entreprise"]
        if not nom or nom == "?":
            raise ActionRefusee("nom de l'employeur manquant")
        if d.get("npa") and not re.fullmatch(r"[1-9]\d{3}", d["npa"]):
            raise ActionRefusee("NPA illisible : %s (4 chiffres)" % d["npa"])
        ch.update(entreprise=nom)
        if d.get("ville"):
            extra = dict(f.get("extra") or {})
            extra["adresse_employeur"] = {"rue": d.get("rue") or "", "numero": d.get("numero") or "",
                                          "npa": d.get("npa") or "", "ville": d["ville"], "origine": "saisie à la main"}
            ch.update(extra=extra)
        reg.noter(emp, "employeur · %s%s" % (nom, " · " + " ".join(x for x in (d.get("rue"), d.get("numero"), d.get("npa"),
                                                                             d.get("ville")) if x) if d.get("ville") else ""))
    elif t == "flag_rh":
        ch.update(flag_rh=d.get("motif") or "oui")
    elif t == "unflag_rh":
        ch.update(flag_rh=None)
    elif t == "jobroom_saisie":
        if ancien not in ENVOYEES or not f.get("postulee_le"):
            raise ActionRefusee("pas de preuve Job-Room pour une candidature qui n'est pas partie")
        ch.update(jobroom_saisie_le=_date(d.get("date")))
        if ancien == "refus":
            ch["jobroom_maj"] = "a_faire"
    elif t == "jobroom_refus":
        if ancien != "refus":
            raise ActionRefusee("seul un refus se reporte sur Job-Room")
        ch.update(jobroom_maj="fait")

    if ch:
        reg.maj(emp, **ch)
    if note and t not in ("go", "attendre", "pasgo", "annuler_go"):
        reg.noter(emp, "note · %s" % note)
    elif note:
        reg.noter(emp, "raison · %s" % note)
    nouveau = ch.get("statut", ancien)
    reg.noter(emp, "tableau de bord · %s%s%s" % (
        TYPES[t], " · %s → %s" % (ancien, nouveau) if nouveau != ancien else "",
        " · " + ", ".join(raisons) if raisons else ""))
    return "%s — %s : %s" % (f.get("entreprise"), f.get("titre"), TYPES[t])
