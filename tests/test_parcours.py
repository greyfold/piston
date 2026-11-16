"""Ton parcours : import du CV, entretien guidé, fidélité aux mots de la personne."""

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from jobsearch import evaluation, moteur, parcours, profil
from jobsearch.parcours import (Ajout, ElementCV, ImportCV, Precision, Tour)

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
    assert poste["genre"] == "poste" and etat["total"] == 1 and etat["rang"] == 1      # la formation n'y passe pas
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

    # Suffisant : le seul poste fait, l'entretien est terminé (la formation n'est pas approfondie).
    assert not parcours.termine(base)
    parcours.repondre(base, "Rien de plus.", c=moteur.FauxMoteur([Tour(question="", suffisant=True)]))
    assert parcours.termine(base) and parcours.etat_entretien(base)["element"] is None
    parcours.reprendre(base, poste["id"])
    assert parcours.etat_entretien(base)["echanges"][-1]["question"].startswith("Qu'aimerais-tu ajouter")


def test_formation_une_ligne_sans_detail(base):
    """Les formations sont à part des expériences : une ligne (intitulé, école, dates), rien dessous."""
    imp = _import()
    imp.elements[4].detail = "Mention très bien"
    imp.elements.append(ElementCV(ref="C7", parent="C5", genre="realisation", titre="Master en design d'interaction",
                                  citation="Master en design d'interaction"))
    bilan = parcours.importer_cv(base, CV, c=moteur.FauxMoteur([imp]))
    assert bilan["ajoutes"] == 4 and "Master en design d'interaction" not in bilan["ecartes"]   # omis, pas écarté
    f = next(e for e in parcours.elements(base) if e["genre"] == "formation")
    assert f["detail"] == "" and not any(e["parent"] == f["id"] for e in parcours.elements(base))
    assert [g for g, _, _ in parcours.arbre(base)][:2] == ["poste", "formation"]
    assert "- [E-%d] Master en design d'interaction | HEAD Genève | 2016" % f["id"] in parcours.rendu(base)
    assert "Raconte ta formation" not in parcours.etat_entretien(base)["echanges"][-1]["question"]
    with pytest.raises(parcours.ParcoursInvalide, match="une formation reste une ligne"):
        parcours.ajouter(base, "projet", "Travail de diplôme", parent=f["id"])
    with pytest.raises(parcours.ParcoursInvalide):
        parcours.reprendre(base, f["id"])
    parcours.modifier(base, f["id"], detail="Cours suivis", titre="Master en design")
    assert parcours.element(base, f["id"])["detail"] == "" and parcours.element(base, f["id"])["titre"] == "Master en design"


def test_echec_de_l_ia_ne_garde_rien(base):
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    parcours.etat_entretien(base)
    with pytest.raises(moteur.MoteurImpossible):
        parcours.repondre(base, "Ma réponse", c=moteur.FauxMoteur([moteur.MoteurImpossible("pas de réponse")]))
    assert parcours.etat_entretien(base)["echanges"][-1]["reponse"] is None


def test_document_pour_l_ia_et_profil(base):
    assert parcours.document(base) == ""
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    doc = parcours.document(base)
    assert doc.startswith('<document nom="Parcours">') and "## Expérience" in doc and "## CV d'origine" in doc
    assert "12 000 utilisateurs" in doc and "Langues" in doc
    assert "Lead UX Designer" in evaluation.profil()


def test_profil_vide(base):
    with pytest.raises(FileNotFoundError, match="Ton parcours"):
        evaluation.profil()


def test_ancre_et_chiffres():
    source = "J'ai formé ~30 collègues,\nsur 15 000 idées reçues en 53 pays."
    assert parcours.ancre("formé ~30 collègues, sur 15 000 idées", source)
    assert parcours.ancre("formé  ~30   collègues", source.upper())
    assert not parcours.ancre("formé 40 collègues", source)
    assert parcours.chiffres_absents("15'000 idées, 53 pays", source) == []
    assert parcours.chiffres_absents("60 pays", source) == ["60"]


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
    r = web.post("/parcours/%d/modifier" % ident, data={"titre": "Anglais", "detail": "C1"}, follow_redirects=False)
    assert parcours.element(base, ident)["detail"] == "C1"
    assert r.headers["location"].endswith("#element-%d" % ident)               # retour sur l'élément corrigé
    # Pendant l'entretien, la base est dans son volet ; une fois l'entretien terminé, elle est sur la page,
    # avec de quoi corriger chaque élément et en ajouter. Une seule fois dans les deux cas.
    page = web.get("/parcours").text
    assert 'id="volet-base"' in page and 'class="base-page"' not in page
    assert page.count('action="/parcours/ajouter"') == 1 and page.count('id="element-%d"' % ident) == 1
    for c in parcours.cadres(base):
        base.cx.execute("UPDATE parcours SET approfondi=1 WHERE id=?", (c["id"],))
    base.commit()
    page = web.get("/parcours").text
    assert "Entretien terminé" in page and 'class="base-page"' in page and 'id="volet-base"' not in page
    assert page.count('action="/parcours/ajouter"') == 1 and 'action="/parcours/%d/modifier"' % ident in page
    r = web.post("/parcours/ajouter", data={"genre": "competence", "titre": "Facilitation"}, follow_redirects=False)
    ajoute = next(e["id"] for e in parcours.elements(base) if e["titre"] == "Facilitation")
    assert r.headers["location"].endswith("#element-%d" % ajoute)
    assert 'id="element-%d"' % ajoute in web.get("/parcours").text
    web.post("/parcours/%d/retirer" % ident)
    assert parcours.element(base, ident) is None
    web.post("/parcours/vider", data={})                                       # sans confirmation : rien
    assert not parcours.vide(base)
    web.post("/parcours/vider", data={"confirmer": "oui"})
    assert parcours.vide(base)

    page = web.get("/premiers-pas/criteres").text
    assert "Combien de temps de trajet" in page and 'name="critere.trajet"' in page
    r = web.post("/premiers-pas/criteres", data={"entretien": "1", "critere.trajet": "30",
                                                 "evaluation.interet": "", "evaluation.angles": "",
                                                 "dossier.differenciateur": "IA"}, follow_redirects=False)
    assert r.status_code == 303
    assert profil.lire(profil.charger(), "dossier.differenciateur") == "IA"
    assert profil.lire(profil.charger(), "evaluation.criteres") == {"trajet": "30"}
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


def _base_avec_entretien(base):
    parcours.importer_cv(base, CV, "cv.txt", c=moteur.FauxMoteur([_import()]))
    poste = parcours.etat_entretien(base)["element"]
    tour = Tour(question="Et le résultat ?", suffisant=False, ajouts=[Ajout(
        ref="N1", parent="E-%d" % poste["id"], genre="projet", titre="Refonte de l'application client",
        detail="Avec 3 designers", citation="J'ai mené la refonte de l'application client avec 3 designers")])
    parcours.repondre(base, "J'ai mené la refonte de l'application client avec 3 designers.", c=moteur.FauxMoteur([tour]))
    return poste


def test_export_puis_import_rend_la_meme_base(base):
    _base_avec_entretien(base)
    avant, texte_avant = parcours.rendu(base), parcours.exporter_json(base)
    export = json.loads(texte_avant)
    assert export["format"] == "jobsearch-parcours" and export["documents"][0]["texte"] == CV.strip()
    poste = next(e for e in export["elements"] if e["genre"] == "poste")
    assert [x["genre"] for x in poste["enfants"]] == ["realisation", "projet"]
    assert poste["entretien"][0]["reponse"].startswith("J'ai mené la refonte")
    assert poste["entretien"][-1] == {"question": "Et le résultat ?", "pose_le": poste["entretien"][-1]["pose_le"]}
    assert "id" not in poste and "parent" not in poste                     # aucun identifiant interne

    # Remplacer : la base revient à l'identique, entretien compris (la question en attente aussi).
    b = parcours.importer_export(base, texte_avant.encode(), remplacer=True)
    assert b == {"ajoutes": 5, "doublons": 0, "ecartes": [], "documents": 1}
    assert _sans_refs(parcours.rendu(base)) == _sans_refs(avant)
    assert parcours.etat_entretien(base)["echanges"][-1]["question"] == "Et le résultat ?"
    assert len(parcours.documents(base)) == 1

    # Ajouter le même fichier : rien n'est doublé.
    b = parcours.importer_export(base, texte_avant.encode())
    assert b["ajoutes"] == 0 and b["doublons"] == 5 and b["documents"] == 0
    assert len(parcours.elements(base)) == 5


def _sans_refs(t):
    import re
    return re.sub(r"\[E-\d+\]", "[E]", t)


def test_import_garde_les_regles_de_la_base(base):
    fichier = {"format": "jobsearch-parcours", "version": 1, "elements": [
        {"genre": "formation", "titre": "Master", "detail": "travail de diplôme",
         "enfants": [{"genre": "projet", "titre": "Projet d'études"}]},              # rien sous une formation
        {"genre": "realisation", "titre": "Seule à la racine"},                        # pas de cadre
        {"genre": "engagement", "titre": "Comité", "approfondi": True, "detail": "x" * 3000,
         "enfants": [{"genre": "competence", "titre": "Animation"}]}]}
    b = parcours.importer_export(base, json.dumps(fichier).encode())
    assert b["ajoutes"] == 3 and sorted(b["ecartes"]) == ["Projet d'études", "Seule à la racine"]
    tous = {e["titre"]: e for e in parcours.elements(base)}
    assert tous["Master"]["detail"] == "" and tous["Comité"]["approfondi"] == 1
    assert len(tous["Comité"]["detail"]) == 3000                              # pas tronqué à l'import
    for mauvais in (b"pas du json", b'{"format": "autre", "elements": []}',
                    b'{"format": "jobsearch-parcours", "version": 99}',
                    b'{"format": "jobsearch-parcours", "elements": [{"genre": "chef", "titre": "x"}]}'):
        with pytest.raises(parcours.ParcoursInvalide):
            parcours.importer_export(base, mauvais)
    assert len(parcours.elements(base)) == 3                                  # un fichier refusé ne touche à rien


def test_reglages_export_et_import(base, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: True)
    web = TestClient(appmod.app)
    page = web.get("/reglages").text
    assert 'href="/reglages/parcours"' in page and "Importer un fichier" not in page   # le détail vit dans sa sous-page
    page = web.get("/reglages/parcours").text
    assert 'id="parcours"' in page and "/reglages/parcours/exporter" not in page   # base vide : rien à exporter
    assert '<a href="/reglages" class="actif" aria-current="true">' in page   # le menu : Réglages ouvert
    assert 'class="retour" href="/reglages"' in page
    _base_avec_entretien(base)
    page = web.get("/reglages/parcours").text
    assert "5 éléments" in page and "Remplacer ma base" in page
    r = web.get("/reglages/parcours/exporter")
    assert r.headers["content-type"].startswith("application/json")
    assert 'attachment; filename="parcours-' in r.headers["content-disposition"]
    contenu = r.content

    r = web.post("/reglages/parcours/importer", files={"fichier": ("parcours.json", contenu, "application/json")},
                 data={"mode": "remplacer"})
    assert "Base remplacée" in r.text and "5 éléments importés" in r.text
    r = web.post("/reglages/parcours/importer", files={"fichier": ("x.json", b"{}", "application/json")},
                 data={"mode": "ajouter"})
    assert "Rien n&#39;a été importé" in r.text and len(parcours.elements(base)) == 5


def test_remplacer_ne_vide_rien_si_rien_n_est_repris(base):
    _base_avec_entretien(base)
    avant = parcours.exporter_json(base)
    rien = {"format": "jobsearch-parcours", "version": 1,
            "elements": [{"genre": "realisation", "titre": "Seule à la racine"}]}       # tout serait écarté
    with pytest.raises(parcours.ParcoursInvalide, match="restée telle quelle"):
        parcours.importer_export(base, json.dumps(rien).encode(), remplacer=True)
    assert len(parcours.elements(base)) == 5 and parcours.etat_entretien(base)["echanges"]
    assert json.loads(parcours.exporter_json(base))["elements"] == json.loads(avant)["elements"]


def test_remplacer_annule_tout_sur_une_erreur(base, monkeypatch):
    _base_avec_entretien(base)
    fichier = json.loads(parcours.exporter_json(base))
    vrai, appels = parcours.ajouter, []

    def ajouter_qui_casse(*a, **k):
        appels.append(1)
        if len(appels) == 3:
            raise RuntimeError("disque plein")
        return vrai(*a, **k)
    monkeypatch.setattr(parcours, "ajouter", ajouter_qui_casse)
    with pytest.raises(RuntimeError):
        parcours.importer_export(base, json.dumps(fichier).encode(), remplacer=True)
    autre = parcours.ouvrir()                         # une autre connexion voit la base d'avant, entière
    try:
        assert len(parcours.elements(autre)) == 5 and len(parcours.documents(autre)) == 1
    finally:
        autre.cx.close()


def test_l_export_n_emporte_pas_ce_qui_est_cache_sous_une_formation(base):
    f = parcours.ajouter(base, "formation", "Master", organisation="EPFL")
    base.cx.execute("UPDATE parcours SET detail='travail de diplôme' WHERE id=?", (f,))  # base d'avant la règle
    base.cx.execute("INSERT INTO parcours (parent, genre, titre, origine, ordre) VALUES (?, 'projet', 'Projet', "
                    "'cv', 99)", (f,))
    base.commit()
    master = json.loads(parcours.exporter_json(base))["elements"][0]
    assert master["titre"] == "Master" and "detail" not in master and "enfants" not in master


def test_import_remplit_certifications_et_langues_du_profil(base):
    """Les certifications et les langues du CV remplissent leur onglet des réglages ; ce qui y est déjà reste."""
    cv = CV + "\nCertifications\nScrum Master certifié — Scrum Alliance, 05.2021\nAllemand : notions\n"
    imp = _import()
    imp.elements += [
        ElementCV(ref="C7", genre="certification", titre="Scrum Master certifié", organisation="Scrum Alliance",
                  fin="05.2021", citation="Scrum Master certifié — Scrum Alliance, 05.2021"),
        ElementCV(ref="C8", genre="langue", titre="Allemand", detail="notions", citation="Allemand : notions"),
    ]
    profil.enregistrer("langues", {"dossier.langues": "anglais | C1, corrigé à la main"})
    bilan = parcours.importer_cv(base, cv, c=moteur.FauxMoteur([imp]))
    assert bilan["profil"] == {"formation": 1, "certification": 1, "langue": 1}
    p = profil.charger()
    assert profil.lire(p, "dossier.formations") == ["Master en design d'interaction | HEAD Genève"]   # 2016 : le début, pas l'obtention
    assert profil.lire(p, "dossier.certifications") == ["Scrum Master certifié | Scrum Alliance | 05.2021"]
    assert profil.lire(p, "dossier.langues") == ["anglais | C1, corrigé à la main", "Allemand | notions"]
    # Le même CV une deuxième fois : rien de plus.
    assert parcours.importer_cv(base, cv, c=moteur.FauxMoteur([imp]))["profil"] == {}


def test_onglet_reprend_le_parcours_deja_importe(base, monkeypatch):
    """Un CV importé avant les onglets : la page propose d'ajouter ce que le parcours contient."""
    from jobsearch.web import app as appmod
    web = TestClient(appmod.app)
    parcours.importer_cv(base, CV, c=moteur.FauxMoteur([_import()]))
    profil.enregistrer("langues", {"dossier.langues": ""})
    page = web.get("/profil/langues").text
    assert "Ton parcours contient 1 langue absente de cette liste." in page
    assert "Récapitulatif" not in page and 'href="/profil/certifications"' in page
    r = web.post("/profil/langues/reprendre", follow_redirects=False)
    assert r.status_code == 303 and "1%20langue%20ajout" in r.headers["location"]
    assert profil.lire(profil.charger(), "dossier.langues") == ["Anglais | courant"]
    assert "absente de cette liste" not in web.get("/profil/langues").text
    assert "Section langues : celles de la liste ci-dessous" in __import__("jobsearch.dossier").dossier.pieces_a_ecrire()["cv"]


def test_import_deduit_le_niveau_vise_sans_ecraser_celui_ecrit(base):
    """Le niveau que le CV permet de viser devient celui de la recherche ; un niveau déjà écrit reste."""
    p = profil.charger()
    p.setdefault("recherche", {}).pop("niveau", None)
    profil.ecrire(p)
    imp = _import()
    imp.niveau = "Senior"
    assert parcours.importer_cv(base, CV, c=moteur.FauxMoteur([imp]))["niveau"] == "Senior"
    assert profil.lire(profil.charger(), "recherche.niveau") == "Senior"
    imp.niveau = "Cadre"
    assert parcours.importer_cv(base, CV, c=moteur.FauxMoteur([imp]))["niveau"] == ""
    assert profil.lire(profil.charger(), "recherche.niveau") == "Senior"
