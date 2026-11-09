"""Ton parcours : import du CV, entretien guidé, fidélité aux mots de la personne."""

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from jobsearch import evaluation, moteur, parcours, profil
from jobsearch.parcours import (Ajout, ElementCV, ImportCV, Precision, Proposition, Tour, TourCriteres)

CV = """Alex Martin
Product designer

Expérience
Lead UX Designer — Acme SA, Lausanne, 03.2019 – aujourd'hui
• Conduit la refonte de l'application client, adoptée par 12 000 utilisateurs
• Encadré une équipe de 3 designers

Formation
Master en design d'interaction — HEAD Genève, 2016

Langues
Anglais : courant
"""


@pytest.fixture
def base(tmp_path, monkeypatch, profil_de_test):
    monkeypatch.setattr("jobsearch.config.DATA", tmp_path)
    r = parcours.ouvrir()
    yield r
    r.cx.close()


def _import():
    return ImportCV(elements=[
        ElementCV(ref="C1", genre="poste", titre="Lead UX Designer", organisation="Acme SA", lieu="Lausanne",
                  debut="03.2019", citation="Lead UX Designer — Acme SA, Lausanne, 03.2019 – aujourd'hui"),
        ElementCV(ref="C2", parent="C1", genre="realisation",
                  titre="Conduit la refonte de l'application client, adoptée par 12 000 utilisateurs",
                  citation="Conduit la refonte de l'application client, adoptée par 12 000 utilisateurs"),
        ElementCV(ref="C3", parent="C1", genre="realisation", titre="Encadré une équipe de 5 designers",
                  citation="Encadré une équipe de 3 designers"),                       # chiffre inventé
        ElementCV(ref="C4", parent="C1", genre="realisation", titre="Lancé une offre B2B",
                  citation="Lancé une offre B2B pour les PME"),                         # pas dans le CV
        ElementCV(ref="C5", genre="formation", titre="Master en design d'interaction", organisation="HEAD Genève",
                  debut="2016", citation="Master en design d'interaction — HEAD Genève, 2016"),
        ElementCV(ref="C6", genre="langue", titre="Anglais", detail="courant", citation="Anglais : courant"),
    ])


def test_import_ne_garde_que_ce_qui_est_dans_le_cv(base):
    c = moteur.FauxMoteur([_import()])
    bilan = parcours.importer_cv(base, CV, "cv.txt", c=c)
    assert bilan["ajoutes"] == 4
    assert sorted(bilan["ecartes"]) == ["Encadré une équipe de 5 designers", "Lancé une offre B2B"]
    tache, message = c.appels[0]
    assert "<cv>" in tache.contexte and tache.schema is ImportCV       # le CV en contexte : masqué par Protege
    titres = [e["titre"] for e in parcours.elements(base)]
    assert "Conduit la refonte de l'application client, adoptée par 12 000 utilisateurs" in titres
    assert parcours.documents(base)[0]["texte"] == CV.strip()
    # Le même CV une deuxième fois : rien n'est doublé.
    bilan = parcours.importer_cv(base, CV, "cv.txt", c=moteur.FauxMoteur([_import()]))
    assert bilan["ajoutes"] == 0 and bilan["doublons"] == 4


def test_entretien_poste_par_poste(base):
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    etat = parcours.etat_entretien(base)
    poste = etat["element"]
    assert poste["genre"] == "poste" and etat["total"] == 2 and etat["rang"] == 1
    assert etat["echanges"][-1]["question"].startswith("Parle-moi de ton poste « Lead UX Designer » chez Acme SA")

    reponse = ("On partait d'une application notée 2,1 sur l'App Store. J'ai mené 20 entretiens avec des clients "
               "puis j'ai redessiné le parcours de commande : la note est passée à 4,5 en six mois.")
    tour = Tour(ajouts=[
        Ajout(ref="N1", parent="E-%d" % poste["id"], genre="projet", titre="Refonte du parcours de commande",
              detail="application notée 2,1", citation="j'ai redessiné le parcours de commande"),
        Ajout(ref="N2", parent="N1", genre="realisation", titre="Redessiné le parcours de commande",
              resultat="note passée de 2,1 à 4,5 en six mois",
              citation="la note est passée à 4,5 en six mois"),
        Ajout(ref="N3", parent="N1", genre="realisation", titre="Augmenté les ventes de 30 %",
              citation="la note est passée à 4,5 en six mois"),                      # chiffre absent : écarté
    ], precisions=[Precision(element="E-%d" % poste["id"], detail="Arrivée sur une application mal notée",
                             citation="On partait d'une application notée 2,1 sur l'App Store")],
        question="Qu'as-tu appris de ces 20 entretiens ?", suffisant=False)
    c = moteur.FauxMoteur([tour])
    r = parcours.repondre(base, reponse, c=c)
    assert r["ajoutes"] == ["Refonte du parcours de commande", "Redessiné le parcours de commande"]
    assert r["ecartes"] == ["Augmenté les ventes de 30 %"] and not r["suivant"]
    tache, message = c.appels[0]
    assert "<derniere_reponse>" in message and reponse in message and "[E-%d]" % poste["id"] in tache.contexte
    projet = next(e for e in parcours.elements(base) if e["genre"] == "projet")
    assert projet["parent"] == poste["id"] and projet["origine"] == "entretien"
    assert any(e["parent"] == projet["id"] for e in parcours.elements(base))
    assert "Arrivée sur une application mal notée" in parcours.element(base, poste["id"])["detail"]
    ech = parcours.etat_entretien(base)["echanges"]
    assert ech[0]["reponse"] == reponse and ech[-1]["question"] == "Qu'as-tu appris de ces 20 entretiens ?"

    # Suffisant : on passe à la formation ; « passer » la termine.
    parcours.repondre(base, "Rien de plus.", c=moteur.FauxMoteur([Tour(question="", suffisant=True)]))
    assert parcours.etat_entretien(base)["element"]["genre"] == "formation"
    assert not parcours.termine(base)
    parcours.passer(base)
    assert parcours.termine(base) and parcours.etat_entretien(base)["element"] is None
    parcours.reprendre(base, poste["id"])
    assert parcours.etat_entretien(base)["echanges"][-1]["question"].startswith("Qu'aimerais-tu ajouter")


def test_echec_de_l_ia_ne_garde_rien(base):
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    parcours.etat_entretien(base)
    with pytest.raises(moteur.MoteurImpossible):
        parcours.repondre(base, "Ma réponse", c=moteur.FauxMoteur([moteur.MoteurImpossible("pas de réponse")]))
    assert parcours.etat_entretien(base)["echanges"][-1]["reponse"] is None


def test_document_pour_l_ia_et_profil_sans_coffre(base, monkeypatch):
    assert parcours.document(base) == ""
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    doc = parcours.document(base)
    assert doc.startswith('<document nom="Parcours">') and "## Expérience" in doc and "## CV d'origine" in doc
    assert "12 000 utilisateurs" in doc and "Langues" in doc
    monkeypatch.setattr("jobsearch.config.coffre_actif", lambda chemin=None: None)     # application autoportante
    texte = evaluation.profil()
    assert "Lead UX Designer" in texte and "Critères de recherches.md" not in texte


def test_profil_vide_sans_coffre(base, monkeypatch):
    monkeypatch.setattr("jobsearch.config.coffre_actif", lambda chemin=None: None)
    with pytest.raises(FileNotFoundError, match="Ton parcours"):
        evaluation.profil()


def test_ancre_et_chiffres():
    source = "J'ai formé ~30 collègues,\nsur 15 000 idées reçues en 53 pays."
    assert parcours.ancre("formé ~30 collègues, sur 15 000 idées", source)
    assert parcours.ancre("formé  ~30   collègues", source.upper())
    assert not parcours.ancre("formé 40 collègues", source)
    assert parcours.chiffres_absents("15'000 idées, 53 pays", source) == []
    assert parcours.chiffres_absents("60 pays", source) == ["60"]


def test_ce_qui_compte_sans_montant(base):
    etat = parcours.etat_criteres(base)
    assert etat["echanges"][0]["question"] == parcours.PREMIERE_CRITERES and etat["proposition"] is None
    tour = TourCriteres(proposition=Proposition(
        criteres=["trajet : 45 minutes au plus", "salaire : 120 000 CHF au moins", "télétravail : deux jours"],
        interet="du design de service", angles=["designer qui mesure"], differenciateur="IA"),
        question="Et le type d'organisation ?", suffisant=False)
    c = moteur.FauxMoteur([tour])
    r = parcours.repondre_criteres(base, "45 minutes de trajet, deux jours de télétravail, pas moins de 120k", c=c)
    assert r["proposition"].criteres == ["trajet : 45 minutes au plus", "télétravail : deux jours"]
    tache, message = c.appels[0]
    assert "120" not in message and "[montant retiré]" in message
    etat = parcours.etat_criteres(base)
    assert "120" not in etat["echanges"][0]["reponse"]
    assert etat["proposition"].differenciateur == "IA" and etat["echanges"][-1]["question"] == "Et le type d'organisation ?"


def test_texte_d_un_cv_word():
    from docx import Document
    doc = Document()
    for ligne in CV.splitlines():
        doc.add_paragraph(ligne)
    flux = BytesIO()
    doc.save(flux)
    assert "Lead UX Designer" in parcours.texte_du_fichier("CV.docx", flux.getvalue())
    with pytest.raises(parcours.ParcoursInvalide):
        parcours.texte_du_fichier("CV.pages", b"x")
    with pytest.raises(parcours.ParcoursInvalide):
        parcours.texte_du_fichier("cv.txt", b"trop court")


def test_pages_et_routes(base, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: True)
    web = TestClient(appmod.app)
    page = web.get("/parcours").text
    assert "Importe ton CV" in page
    assert web.post("/parcours/importer", data={"texte": ""}).json() == {"ok": False, "erreur": "choisis ton CV ou colle son texte"}
    monkeypatch.setattr(moteur, "ouvrir", lambda verifier=None: moteur.FauxMoteur([_import()]))
    r = web.post("/parcours/importer", data={"texte": CV}).json()
    assert r["ok"] and r["ajoutes"] == 4
    page = web.get("/premiers-pas/dossier").text
    assert "Lead UX Designer" in page and "Parle-moi de ton poste" in page and "Ta base" in page
    monkeypatch.setattr(moteur, "ouvrir", lambda verifier=None: moteur.FauxMoteur(
        [Tour(question="Et ensuite ?", suffisant=False)]))
    r = web.post("/parcours/repondre", data={"reponse": "J'ai tout repris."}).json()
    assert r["ok"] and r["ajoutes"] == []
    assert "Et ensuite ?" in web.get("/parcours").text
    ident = next(e["id"] for e in parcours.elements(base) if e["genre"] == "langue")
    web.post("/parcours/%d/modifier" % ident, data={"titre": "Anglais", "detail": "C1"})
    assert parcours.element(base, ident)["detail"] == "C1"
    web.post("/parcours/%d/retirer" % ident)
    assert parcours.element(base, ident) is None
    web.post("/parcours/vider", data={})                                       # sans confirmation : rien
    assert not parcours.vide(base)
    web.post("/parcours/vider", data={"confirmer": "oui"})
    assert parcours.vide(base)

    page = web.get("/premiers-pas/criteres").text
    assert "ferait de ton prochain poste un bon poste" in page and 'name="evaluation.criteres"' in page
    r = web.post("/premiers-pas/criteres", data={"entretien": "1", "evaluation.criteres": "trajet : 30 minutes",
                                                 "evaluation.interet": "", "evaluation.angles": "",
                                                 "dossier.differenciateur": "IA"}, follow_redirects=False)
    assert r.status_code == 303
    assert profil.lire(profil.charger(), "dossier.differenciateur") == "IA"
    assert profil.lire(profil.charger(), "evaluation.criteres") == ["trajet : 30 minutes"]
    assert "formulaire" not in web.get("/premiers-pas/criteres?formulaire=1").url.path


def test_appuis_des_questions(base):
    """Sous chaque question : sur quoi elle s'appuie, passages vérifiés mot pour mot."""
    from jobsearch.parcours import Appui
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    etat = parcours.etat_entretien(base)
    poste = etat["element"]
    depart = etat["echanges"][0]["appui"]
    assert depart["fixe"] and depart["appuis"][0]["element"] == poste["id"]
    assert depart["appuis"][0]["libelle"] == "Poste « Lead UX Designer »"

    reponse = "J'ai mené 20 entretiens avec des clients puis redessiné le parcours de commande."
    tour = Tour(question="Qu'as-tu appris de ces entretiens ?", suffisant=False,
                pourquoi="Ce que les entretiens ont changé n'est pas encore dit.",
                appuis=[Appui(source="réponse", passage="J'ai mené 20 entretiens avec des clients"),
                        Appui(source="E-%d" % poste["id"], passage="Acme SA, Lausanne"),
                        Appui(source="réponse", passage="J'ai mené 40 ateliers"),          # introuvable : écarté
                        ])
    parcours.repondre(base, reponse, c=moteur.FauxMoteur([tour]))
    appui = parcours.etat_entretien(base)["echanges"][-1]["appui"]
    assert not appui.get("fixe") and appui["pourquoi"] == "Ce que les entretiens ont changé n'est pas encore dit."
    assert [(a["source"], a["passage"]) for a in appui["appuis"]] == [
        ("reponse", "J'ai mené 20 entretiens avec des clients"), ("element", "Acme SA, Lausanne")]
    assert appui["appuis"][0]["libelle"] == "Ta réponse"

    # Rien de vérifiable ni de pourquoi : rien n'est montré.
    assert parcours.appuis_verifies(base, "", [Appui(source="E-999", passage="inventé")], {}) is None


def test_appuis_ce_qui_compte_sans_montant(base):
    from jobsearch.parcours import Appui
    parcours.etat_criteres(base)
    tour = TourCriteres(proposition=Proposition(criteres=["trajet : 45 minutes au plus"]),
                        question="Et le télétravail ?", suffisant=False,
                        pourquoi="Le télétravail n'est pas dit, ni ce qui va avec 120 000 CHF.",
                        appuis=[Appui(source="réponse", passage="45 minutes de trajet")])
    parcours.repondre_criteres(base, "45 minutes de trajet, pas moins de 120k", c=moteur.FauxMoteur([tour]))
    appui = parcours.etat_criteres(base)["echanges"][-1]["appui"]
    assert "120" not in str(appui) and appui["appuis"][0]["passage"] == "45 minutes de trajet"
