"""Ce qui compte pour toi : le questionnaire fixe, sans IA, et son seul appel à l'IA."""

import pytest
from fastapi.testclient import TestClient

from jobsearch import ce_qui_compte as cqc
from jobsearch import moteur, parcours, profil


@pytest.fixture
def base(tmp_path, monkeypatch, profil_de_test):
    monkeypatch.setattr("jobsearch.config.DATA", tmp_path)
    r = parcours.ouvrir()
    yield r
    r.cx.close()


def _repondre_tout(reg):
    cqc.repondre(reg, "trajet", ["45"], {"depuis": "Morges"})
    cqc.repondre(reg, "teletravail", ["2"])
    cqc.repondre(reg, "taux", [], {"min": "80", "max": "100"})
    cqc.repondre(reg, "contrat", ["cdi", "cdd-long", "cdd-cdi"])
    cqc.repondre(reg, "langues", ["français", "anglais"])
    cqc.repondre(reg, "contenu", ["piloter des projets"], {"autres": "animer des ateliers"})
    cqc.repondre(reg, "encadrement", ["non"])
    cqc.repondre(reg, "organisation", ["PME", "secteur public"], {"eviter": "tabac"})
    cqc.repondre(reg, "metiers", ["Vente ou commercial"], {"mots": "Comptable"})
    cqc.repondre(reg, "conditions", ["non"], {"heures": "42", "vacances": "5"})
    cqc.repondre(reg, "interet", [], {"texte": "une équipe qui expérimente, pas moins de 120k"})
    cqc.repondre(reg, "eviter", passer=True)
    cqc.repondre(reg, "rythme", [], {"semaine": "3", "mois": "12"})


def test_les_reponses_remplissent_criteres_pretri_et_objectifs(base):
    _repondre_tout(base)
    e = cqc.etat(base)
    assert e["termine"] and e["faites"] == e["total"] == len(cqc.QUESTIONS)
    p = cqc.proposition(e["reponses"])
    assert p["criteres"]["trajet"] == "45" and p["criteres"]["contrats"] == ["cdi", "cdd-long", "cdd-cdi"]
    assert p["criteres"]["secteurs_exclus"] == ["tabac"] and p["criteres"]["heures_max"] == 42
    assert p["lignes"][:11] == [
        "trajet : 45 minutes au plus depuis Morges", "télétravail : 2 jours par semaine au moins",
        "taux d'activité : 80 à 100 %",
        "contrat : CDI, CDD de 12 à 24 mois, CDD pouvant déboucher sur un CDI",
        "langues de travail : français, anglais",
        "encadrement : pas d'équipe à encadrer", "organisation : PME ou secteur public", "secteurs exclus : tabac",
        "temps de travail : 42 heures par semaine au plus", "vacances : 5 semaines au moins", "déplacements : aucun"]
    assert p["activites"] == ["piloter des projets", "animer des ateliers"]
    assert p["interet_texte"] == "une équipe qui expérimente, pas moins de [montant retiré]"
    actives = set(p["regles"])
    assert not actives & {"taux", "contrat-cdd", "contrat-api-duree-determinee"}
    assert "contrat-freelance" in actives and "langue-allemand" in actives        # 80 % : pas le temps partiel ; pas d'allemand
    assert p["mots_titre"] == ["vente", "commercial", "sales", "comptable"]
    assert (p["semaine"], p["mois"]) == (3, 12)
    assert "120" not in str(cqc.reponses(base))                                 # aucun montant gardé


def test_une_question_passee_garde_le_profil(base):
    cqc.repondre(base, "taux", passer=True)
    p = cqc.proposition(cqc.reponses(base))
    assert set(p["regles"]) == set(profil.effectif("exclusions.regles"))
    # Seuls restent les critères libres du profil (« autres »), qu'aucune question ne remplit.
    assert p["criteres"] == {"autres": profil.criteres_structures()["autres"]}
    assert p["activites"] is None and p["interet_texte"] is None
    assert cqc.etat(base)["fil"][0]["reponse"] == "Passé"
    with pytest.raises(cqc.ReponseInvalide):
        cqc.repondre(base, "trajet", ["inconnu"])
    with pytest.raises(cqc.ReponseInvalide):
        cqc.repondre(base, "langues", [])


def test_un_seul_appel_a_l_ia_adapte_les_exemples(base):
    ctx = cqc.Contexte(activites=["cadrer des cas d'usage d'IA avec les métiers", "former les équipes"],
                       voisins=[cqc.Voisin(libelle="Développement logiciel", mots=["Développeur", "software engineer"])],
                       exemple_interet="un sponsor engagé", exemple_eviter="des démos sans suite", angles=["pont métier-IA"],
                       differenciateur="IA")
    faux = moteur.FauxMoteur([ctx])
    cqc.preparer(base, c=faux)
    cqc.preparer(base, c=faux)                                                  # déjà fait : aucun second appel
    assert len(faux.appels) == 1
    e = cqc.etat(base, modifier="contenu")
    assert e["courante"]["choix"][0][0] == "cadrer des cas d'usage d'IA avec les métiers" and e["courante"]["adaptee"]
    metiers = cqc.etat(base, modifier="metiers")["courante"]
    assert metiers["choix"] == [("Développement logiciel", "Développement logiciel")]
    assert cqc.resolue(cqc.PAR_CLE["interet"], e["contexte"])["champs"][0]["exemple"] == "un sponsor engagé"
    cqc.repondre(base, "metiers", ["Développement logiciel"])
    assert cqc.proposition(cqc.reponses(base))["mots_titre"] == ["developpeur", "software engineer"]


def test_page_et_enregistrement(base, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: False)
    web = TestClient(appmod.app)
    page = web.get("/premiers-pas/criteres").text
    assert "Combien de temps de trajet" in page and 'value="45"' in page and "/criteres/preparer" in page
    assert "Questionnaire · 0 réponse sur 13" in page
    web.post("/criteres/repondre", data={"cle": "trajet", "choix": "45", "champ.depuis": "Morges"})
    page = web.get("/premiers-pas/criteres").text
    assert "45 minutes · Depuis : Morges" in page and "Combien de jours de télétravail" in page
    assert "?modifier=trajet" in page
    r = web.post("/criteres/repondre", data={"cle": "langues"}, follow_redirects=False)
    assert "modifier=langues" in r.headers["location"] and "msg=" in r.headers["location"]
    assert 'value="45" checked' in web.get("/premiers-pas/criteres?modifier=trajet").text

    _repondre_tout(base)
    page = web.get("/premiers-pas/criteres").text
    assert "Ce qui ressort de tes réponses" in page and "Gardées aussi : Contrat : CDD ou mission" in page
    assert "3 par semaine · 12 par mois" in page
    # Le volet : un champ par critère, des pilules pour les listes (décocher retire, « .ajout » ajoute).
    assert 'name="critere.trajet" value="45" checked' in page and 'name="critere.contrats" value="cdi" checked' in page
    assert 'name="exclusions.mots_titre" value="vente" checked' in page and 'name="exclusions.mots_titre.ajout"' in page
    assert 'name="evaluation.criteres"' not in page and "<textarea" not in page.split('id="volet-proposition"')[1]
    form = {"entretien": "1", "pretri": "1", "objectifs": "1", "critere.trajet": "45", "critere.trajet_depuis": "Morges",
            "critere.contrats": ["cdi", "freelance"], "critere.taux_min": "80", "critere.autres.ajout": "pas de télémarketing",
            "evaluation.activites": ["piloter des projets"], "evaluation.activites.ajout": "former, animer",
            "evaluation.interet": "", "evaluation.angles": "", "dossier.differenciateur": "",
            "exclusions.regles": ["contrat-freelance"], "exclusions.mots_titre": "vente",
            "exclusions.mots_titre.ajout": "Comptable, assistant", "objectifs.semaine_min": "3", "objectifs.mois_min": "12"}
    assert web.post("/premiers-pas/criteres", data=form, follow_redirects=False).status_code == 303
    p = profil.charger()
    assert profil.lire(p, "evaluation.criteres") == {
        "trajet": "45", "trajet_depuis": "Morges", "taux_min": 80, "taux_max": 100, "contrats": ["cdi", "freelance"],
        "autres": ["pas de télémarketing"]}
    assert profil.criteres_evaluation()[:3] == [("trajet", "45 minutes au plus depuis Morges"),
                                                ("taux d'activité", "80 à 100 %"), ("contrat", "CDI, freelance ou mandat")]
    assert profil.lire(p, "evaluation.activites") == ["piloter des projets", "former", "animer"]
    assert profil.lire(p, "exclusions.mots_titre") == ["vente", "comptable", "assistant"]
    assert "taux" in profil.lire(p, "exclusions.desactivees") and profil.lire(p, "objectifs.semaine_min") == 3
    web.post("/criteres/recommencer")
    assert cqc.reponses(base) == {}


def test_contrats_a_choix_multiple(base):
    cqc.repondre(base, "contrat", ["cdi"])
    p = cqc.proposition(cqc.reponses(base))
    assert "contrat : durée indéterminée seulement" in p["lignes"] and p["criteres"]["contrats"] == ["cdi"]
    assert {"contrat-cdd", "contrat-api-duree-determinee", "contrat-freelance"} <= set(p["regles"])
    cqc.repondre(base, "contrat", ["cdi", "freelance"])
    p = cqc.proposition(cqc.reponses(base))
    assert "contrat-freelance" not in p["regles"] and "contrat-cdd" in p["regles"]
    cqc.repondre(base, "taux", [], {"min": "50"})                       # le temps partiel garde aussi le freelance
    cqc.repondre(base, "contrat", ["cdi"])
    assert "contrat-freelance" not in cqc.proposition(cqc.reponses(base))["regles"]
    cqc.repondre(base, "contrat", list(cqc.CONTRATS))              # tout coché : pas de critère, tout gardé
    p = cqc.proposition(cqc.reponses(base))
    assert not any(c.startswith("contrat") for c in p["lignes"]) and "contrat-cdd" not in p["regles"]
    contrat = next(x for x in cqc.etat(base)["fil"] if x["question"]["cle"] == "contrat")
    assert contrat["reponse"].startswith("CDI, durée indéterminée, CDD de 2 ans ou plus, CDD de 12 à 24 mois")


def test_modifier_une_reponse_sur_place(base, monkeypatch):
    """« Modifier » rouvre la question à sa place dans le fil, pas à la fin."""
    cqc.repondre(base, "trajet", ["60"], {"depuis": "Morges"})
    cqc.repondre(base, "teletravail", ["2"])
    cqc.repondre(base, "taux", [], {"min": "100"})
    e = cqc.etat(base, modifier="trajet")
    assert [(x["question"]["cle"], x["courante"]) for x in e["fil"]] == [
        ("trajet", True), ("teletravail", False), ("taux", False)]
    assert e["modification"] and e["courante"]["cle"] == "trajet" and e["deja"]["choix"] == "60"
    e = cqc.etat(base)                                           # sans modification : la suivante, à la fin
    assert [x["question"]["cle"] for x in e["fil"]][-1] == "contrat" and not e["modification"]

    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: False)
    page = TestClient(appmod.app).get("/premiers-pas/criteres?modifier=trajet").text
    assert page.index('name="cle" value="trajet"') < page.index("Combien de jours de télétravail")
    assert "Ta réponse à modifier" in page and 'value="60" checked' in page
    cqc.repondre(base, "trajet", ["45"], {"depuis": "Morges"})
    assert cqc.reponses(base)["trajet"]["choix"] == "45" and cqc.reponses(base)["teletravail"]["choix"] == "2"


def test_reponse_vide_aux_questions_libres(base, monkeypatch):
    """Sans bouton « Passer » : une question à champ libre accepte une réponse vide ; une question à choix, non."""
    cqc.repondre(base, "interet", [], {"texte": ""})
    cqc.repondre(base, "rythme", [], {"semaine": "", "mois": ""})
    fil = {x["question"]["cle"]: x["reponse"] for x in cqc.etat(base)["fil"]}
    assert fil["interet"] == "Rien à ajouter" and fil["rythme"] == "Pas d'objectif"
    p = cqc.proposition(cqc.reponses(base))
    assert p["interet_texte"] == "" and (p["semaine"], p["mois"]) == (0, 0)
    with pytest.raises(cqc.ReponseInvalide):
        cqc.repondre(base, "taux", [])
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: False)
    page = TestClient(appmod.app).get("/premiers-pas/criteres?modifier=interet").text
    assert ">Passer<" not in page and page.index(">Annuler<") < page.index("Valider")


def test_taux_en_fourchette(base):
    cqc.repondre(base, "taux", [], {"min": "90", "max": "100"})
    p = cqc.proposition(cqc.reponses(base))
    assert "taux d'activité : 90 à 100 %" in p["lignes"] and (p["criteres"]["taux_min"], p["criteres"]["taux_max"]) == (90, 100)
    assert "taux" not in p["regles"] and "contrat-freelance" in p["regles"]
    assert next(x for x in cqc.etat(base)["fil"] if x["question"]["cle"] == "taux")["reponse"] == "De 90 à 100 %"
    cqc.repondre(base, "taux", [], {"min": "100"})
    p = cqc.proposition(cqc.reponses(base))
    assert "taux" in p["regles"] and not any(c.startswith("taux d'activité") for c in p["lignes"])
    cqc.repondre(base, "taux", [], {"min": "60", "max": "80"})
    p = cqc.proposition(cqc.reponses(base))
    assert "taux d'activité : 60 à 80 %" in p["lignes"] and not {"taux", "contrat-freelance"} & set(p["regles"])
    for mauvais in ({"max": "100"}, {"min": "90", "max": "80"}, {"min": "120"}):
        with pytest.raises(cqc.ReponseInvalide):
            cqc.repondre(base, "taux", [], mauvais)
    cqc._ecrire(base, "taux", {"choix": "80", "champs": {}})      # réponse d'avant les champs chiffrés
    assert cqc._fourchette(cqc.reponses(base)) == (80, 100)


def test_ecran_de_succes_avec_modifier(base, monkeypatch):
    """Toutes les questions répondues : un écran de succès résume, chaque ligne rouvre sa question."""
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: False)
    _repondre_tout(base)
    p = cqc.proposition(cqc.reponses(base))
    assert len(p["sources"]) == len(p["lignes"]) and p["sources"][0] == "trajet"
    assert p["activites"] == ["piloter des projets", "animer des ateliers"]
    web = TestClient(appmod.app)
    # La dernière réponse passe par la page : tout est enregistré avant l'écran de succès.
    r = web.post("/criteres/repondre", data={"cle": "rythme", "champ.semaine": "3", "champ.mois": "12"},
                 follow_redirects=False)
    assert "enregistre=1" in r.headers["location"]
    pr = profil.charger()
    assert profil.lire(pr, "evaluation.criteres") == p["criteres"]
    assert profil.lire(pr, "evaluation.activites") == ["piloter des projets", "animer des ateliers"]
    assert [n for n, _ in profil.criteres_evaluation()][:3] == ["trajet", "télétravail", "taux d'activité"]
    assert "taux" in profil.lire(pr, "exclusions.desactivees") and profil.lire(pr, "objectifs.mois_min") == 12
    assert profil.lire(pr, "exclusions.mots_titre") == ["vente", "commercial", "sales", "comptable"]
    page = web.get(r.headers["location"]).text
    assert "Enregistré dans ton profil" in page and 'class="fil"' not in page and "Retour au tableau de bord" in page
    assert 'form="form-proposition"' not in page
    for cle in ("trajet", "taux", "contrat", "conditions", "contenu", "interet", "metiers", "rythme"):
        assert "?modifier=%s#entretien" % cle in page
    assert "Gardées aussi : Contrat : CDD ou mission" in page
    page = web.get("/premiers-pas/criteres?modifier=trajet").text                  # modifier : retour au fil
    assert "Enregistré dans ton profil" not in page and "Ta réponse à modifier" in page
    # Une modification, tout étant répondu : enregistrée aussitôt.
    web.post("/criteres/repondre", data={"cle": "trajet", "choix": "30", "champ.depuis": "Morges"})
    assert profil.lignes_criteres(profil.criteres_structures())[0] == "trajet : 30 minutes au plus depuis Morges"


def test_reglages_montrent_et_enregistrent_toutes_les_questions(base, monkeypatch):
    """Réglages › Critères : toutes les questions et leurs options, à remplir et enregistrer d'un coup."""
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: False)
    web = TestClient(appmod.app)
    cqc.repondre(base, "trajet", ["45"], {"depuis": "Morges"})
    page = web.get("/profil/criteres").text
    assert "Tes réponses" in page and page.count('class="question-reglage"') == len(cqc.QUESTIONS)
    for q in cqc.QUESTIONS:
        assert q["question"].replace("'", "&#39;") in page or q["question"] in page
    assert "CDD de 2 ans ou plus" in page and "Au moins (%)" in page and 'name="choix.trajet" value="45" checked' in page
    form = {"choix.trajet": "60", "champ.trajet.depuis": "Morges", "champ.taux.min": "90", "champ.taux.max": "100",
            "choix.contrat": ["cdi", "cdd-2ans"], "choix.langues": ["français", "anglais"],
            "champ.rythme.semaine": "2", "champ.rythme.mois": "8"}
    r = web.post("/profil/criteres/reponses", data=form, follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    pr = profil.charger()
    assert profil.lignes_criteres(profil.criteres_structures(pr))[:3] == [
        "trajet : 1 heure au plus depuis Morges", "taux d'activité : 90 à 100 %", "contrat : CDI, CDD de 2 ans ou plus"]
    assert profil.objectifs_rythme() == {"semaine": 2, "mois": 8}
    assert "teletravail" not in cqc.reponses(base)                     # jamais répondue, laissée vide : sans réponse
    r = web.post("/profil/criteres/reponses", data={**form, "champ.taux.min": "120"}, follow_redirects=False)
    assert "msg=" in r.headers["location"] and "ok=1" not in r.headers["location"]


def test_les_reglages_gardent_une_question_passee(base):
    cqc.repondre(base, "teletravail", passer=True)
    cqc.repondre(base, "langues", passer=True)
    cqc.repondre(base, "trajet", ["45"], {"depuis": "Morges"})
    valeurs = {q["cle"]: ([], {}) for q in cqc.QUESTIONS}
    valeurs["trajet"] = (["30"], {"depuis": "Morges"})
    assert cqc.repondre_tout(base, valeurs) == []
    rep = cqc.reponses(base)
    assert rep["teletravail"] == {"passe": True} and rep["langues"] == {"passe": True}
    assert rep["trajet"]["choix"] == "30"


def test_les_choix_donnes_avant_les_exemples_de_l_ia_restent(base):
    cqc.repondre(base, "metiers", ["Vente ou commercial"])
    cqc.repondre(base, "contenu", ["piloter des projets"])
    ctx = cqc.Contexte(activites=["former les équipes"], voisins=[cqc.Voisin(libelle="Développement", mots=["developpeur"])],
                       exemple_interet="a, b, c", exemple_eviter="d, e, f")
    cqc.preparer(base, c=moteur.FauxMoteur([ctx]))
    q = {x["question"]["cle"]: x["question"] for x in cqc.questions(base)}
    assert ("Vente ou commercial", "Vente ou commercial") in q["metiers"]["choix"]
    assert ("piloter des projets", "piloter des projets") in q["contenu"]["choix"]
    # Réenregistrées telles quelles (réglages, « Modifier ») : rien n'est perdu, le tri écarte toujours la vente.
    cqc.repondre(base, "metiers", ["Vente ou commercial", "Développement"])
    cqc.repondre(base, "contenu", ["piloter des projets"])
    rep = cqc.reponses(base)
    assert rep["metiers"]["choix"] == ["Vente ou commercial", "Développement"]
    assert rep["contenu"]["choix"] == ["piloter des projets"]
    assert {"vente", "developpeur"} <= set(cqc.proposition(rep)["mots_titre"])


def test_un_choix_disparu_ne_casse_pas_la_proposition(base):
    cqc._ecrire(base, "trajet", {"choix": "120", "champs": {}})
    cqc._ecrire(base, "teletravail", {"choix": "4", "champs": {}})
    p = cqc.proposition(cqc.reponses(base))                      # ni critère ni erreur
    assert set(p["criteres"]) == {"autres"}                     # les critères libres du profil, rien de plus


def test_deux_preparations_simultanees_un_seul_appel(base):
    import threading
    ctx = cqc.Contexte(activites=["former"], voisins=[], exemple_interet="a", exemple_eviter="b")
    entree, sortie, lances = threading.Event(), threading.Event(), []

    class Lent(moteur.FauxMoteur):
        def executer(self, t, message):
            lances.append(t)                          # compté dès l'entrée, avant d'attendre
            entree.set()
            sortie.wait(5)
            return super().executer(t, message)
    lent = Lent([ctx, ctx])

    def ouvrir_et_preparer():                         # une connexion par fil, comme une requête web
        reg = parcours.ouvrir()
        try:
            cqc.preparer(reg, c=lent)
        finally:
            reg.cx.close()
    fil = threading.Thread(target=ouvrir_et_preparer)
    fil.start()
    assert entree.wait(5)
    second = threading.Thread(target=ouvrir_et_preparer)
    second.start()
    second.join(.5)                                   # le second attend le premier, sans appeler l'IA
    sortie.set()
    fil.join(5), second.join(5)
    assert len(lances) == 1 and cqc.reponses(base)[cqc.CONTEXTE]["activites"] == ["former"]
