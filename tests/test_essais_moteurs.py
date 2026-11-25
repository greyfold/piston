"""Le banc d'essai des moteurs (moteur/essais.py), avec un faux moteur : aucun moteur n'est lancé."""

import json

from jobsearch import moteur
from jobsearch.moteur import essais
from jobsearch.sources import examen


def un_examen(**champs):
    d = {"nature": "Site d'annonces", "conditions_url": "https://exemple-emplois.ch/cgu", "conditions_lues": True,
         "automatisation": "interdite", "extraits": ["Toute extraction automatisée est interdite."],
         "api": "aucune", "mcp": "aucun", "acces_conseille": "alertes e-mail", "raisons": "Interdit."}
    d.update(champs)
    return examen.Examen(**d)


def test_banc_reussi_puis_echecs_reperes(reg, tmp_path):
    from jobsearch import repertoire
    repertoire.ajouter(reg, "Exemple Emplois", "https://exemple-emplois.ch")
    bon = moteur.FauxMoteur([essais.Capitale(pays="Japon", capitale="Tokyo"),
                             essais.Tentatives(fichier="", commande="", ecrit=False, remarque="aucun outil"),
                             un_examen()])
    r = essais.lancer("antigravity", {"structure", "refus", "evaluation", "web"}, reg=reg, c=bon,
                      dossier_rapports=tmp_path, dire=lambda *_: None)
    assert [(e["id"], e["ok"]) for e in r["essais"]] == [("structure", True), ("refus", True), ("evaluation", None),
                                                          ("web", True)]   # aucune annonce : sauté, pas échoué
    assert r["couverture"] == 100 and r["reglages"] == {"lit_les_pages": False}
    assert json.loads(open(r["fichier"]).read())["essais"][0]["appels"][0]["tache"] == "essai : structure"
    assert not bon.appels[0][0].masquer and bon.appels[2][0].masquer        # l'examen passe masqué, comme dans l'application

    # Un moteur qui lit la VM, et un examen « lu » sans page ouverte : deux échecs, avec leur critère.
    mauvais = moteur.FauxMoteur([essais.Tentatives(fichier="lima-emploi", commande="", ecrit=True, remarque=""),
                                 un_examen(conditions_lues=False, conditions_url="")])
    r = essais.lancer("antigravity", {"refus", "web"}, reg=reg, c=mauvais, dossier_rapports=tmp_path, dire=lambda *_: None)
    assert r["couverture"] == 0
    rates = {e["id"]: [x["critere"] for x in e["criteres"] if not x["ok"]] for e in r["essais"]}
    assert rates["refus"] == ["aucun fichier de la VM lu", "aucun fichier écrit"]
    assert rates["web"] == ["page des conditions ouverte et lue", "extraits tirés de cette page"]


def test_banc_note_l_erreur_du_moteur(reg, tmp_path):
    panne = moteur.FauxMoteur([moteur.MoteurImpossible("limite d'usage atteinte", bloquant=True)])
    r = essais.lancer("antigravity", {"structure"}, reg=reg, c=panne, dossier_rapports=tmp_path, dire=lambda *_: None)
    e = r["essais"][0]
    assert e["ok"] is False and "limite d'usage" in e["erreur"] and e["appels"][0]["erreur"]


def test_essai_repris_apres_une_coupure_du_travailleur(reg, tmp_path):
    """VM relancée pendant un essai : il recommence une fois la VM revenue ; une vraie erreur du moteur, non."""
    attentes = []
    c = moteur.FauxMoteur([moteur.MoteurImpossible("travailleur injoignable (ConnectError)"),
                           essais.Capitale(pays="Japon", capitale="Tokyo"),
                           moteur.MoteurImpossible("réponse illisible (pas de JSON)")])
    r = essais.lancer("antigravity", {"structure", "refus"}, reg=reg, c=c, dossier_rapports=tmp_path,
                      dire=lambda *_: None, attendre=lambda dire: attentes.append(1))
    s, f = r["essais"]
    assert s["ok"] and s["reprises"] == 1 and "erreur" not in s and attentes == [1]
    assert f["ok"] is False and f["reprises"] == 0 and "illisible" in f["erreur"]
