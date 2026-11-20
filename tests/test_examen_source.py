"""Examen d'une source par l'IA : conditions générales, API publique, serveur MCP."""

import pytest

from jobsearch import repertoire
from jobsearch.moteur import FauxMoteur, MoteurImpossible
from jobsearch.sources import examen


def un_examen(**champs):
    d = {"nature": "Site d'annonces d'exemple", "conditions_url": "https://www.exemple-emplois.ch/cgu",
         "conditions_lues": True, "automatisation": "interdite",
         "extraits": ["Toute extraction automatisée des annonces est interdite sans accord écrit préalable."],
         "api": "sur accord", "api_url": "https://partenaires.exemple-emplois.ch/api",
         "api_detail": "Réservée aux partenaires.", "mcp": "aucun", "mcp_nom": "x", "mcp_url": "https://x.y",
         "autres_voies": "Alertes e-mail gratuites.", "acces_conseille": "alertes e-mail",
         "raisons": "Les conditions interdisent les robots ; les alertes e-mail restent permises."}
    d.update(champs)
    return examen.Examen(**d)


def test_examiner_range_le_resultat(reg):
    sid = repertoire.ajouter(reg, "Exemple Emplois", "https://www.exemple-emplois.ch", "site d'annonces")
    m = FauxMoteur([un_examen()])
    e = examen.examiner(reg, sid, c=m)
    tache, message = m.appels[0]
    assert set(tache.outils) == {"WebSearch", "WebFetch"} and set(tache.autorises) == {"WebSearch", "WebFetch"}
    assert "https://www.exemple-emplois.ch" in message and "Exemple Emplois" in message
    assert e.acces_conseille == "alertes e-mail"
    assert (e.mcp_nom, e.mcp_url) == ("", "")              # « aucun » serveur MCP : rien à montrer
    lu = examen.lire_tous(reg)[sid]
    assert lu["examen"]["automatisation"] == "interdite" and lu["erreur"] is None


def test_une_source_du_socle_s_examine_aussi(reg):
    m = FauxMoteur([un_examen(automatisation="permise", acces_conseille="à brancher", api="publique")])
    examen.examiner(reg, "jobup", c=m)
    assert "jobup" in m.appels[0][1]
    assert examen.lire_tous(reg)["jobup"]["examen"]["acces_conseille"] == "à brancher"


def test_echec_du_moteur_note(reg):
    sid = repertoire.ajouter(reg, "Exemple Emplois", "https://www.exemple-emplois.ch")
    with pytest.raises(examen.ExamenImpossible):
        examen.examiner(reg, sid, c=FauxMoteur([MoteurImpossible("pas de réponse")]))
    assert examen.lire_tous(reg)[sid] == {"examen": None, "erreur": "pas de réponse",
                                          "examinee_le": examen.lire_tous(reg)[sid]["examinee_le"]}


def test_source_inconnue(reg):
    with pytest.raises(examen.ExamenImpossible):
        examen.examiner(reg, "nulle-part", c=FauxMoteur([]))


def test_conditions_non_lues_rien_n_est_permis():
    e = examen.verifier(un_examen(automatisation="permise", conditions_lues=False, acces_conseille="à brancher"))
    assert e.automatisation == "inconnue"
    e = examen.verifier(un_examen(automatisation="permise", conditions_url="", acces_conseille="à brancher"))
    assert e.automatisation == "inconnue"


def test_interdite_ne_se_branche_pas_sans_voie_prevue():
    e = examen.verifier(un_examen(automatisation="interdite", acces_conseille="à brancher", api="aucune", mcp="aucun"))
    assert e.acces_conseille == "interdit à la plateforme"
    e = examen.verifier(un_examen(automatisation="interdite", acces_conseille="à brancher", api="publique", mcp="aucun"))
    assert e.acces_conseille == "à brancher"               # l'API publique est la voie prévue
    e = examen.verifier(un_examen(automatisation="interdite", acces_conseille="à brancher", api="aucune",
                                  mcp="communautaire", mcp_nom="un-mcp", mcp_url="https://github.com/x/un-mcp"))
    assert e.acces_conseille == "interdit à la plateforme" and e.mcp_nom == "un-mcp"   # montré, pas une voie permise


def test_adresses_et_extraits_verifies():
    e = examen.verifier(un_examen(conditions_url="javascript:alert(1)", api_url="pas une adresse",
                                  extraits=["mot " * 80, "", "a", "b", "c"]))
    assert (e.conditions_url, e.api_url) == ("", "")
    assert e.automatisation == "interdite"                # une interdiction reste, même sans adresse
    assert len(e.extraits) == 3 and e.extraits[0].endswith("…") and len(e.extraits[0].split()) == examen.MOTS_MAX + 1


def test_changer_acces(reg):
    sid = repertoire.ajouter(reg, "Exemple Emplois", "https://www.exemple-emplois.ch")
    repertoire.changer_acces(reg, sid, "alertes e-mail")
    assert repertoire.lister(reg)[sid]["acces"] == "alertes e-mail"
    with pytest.raises(repertoire.SourceRefusee):
        repertoire.changer_acces(reg, sid, "automatique")
    with pytest.raises(repertoire.SourceRefusee):
        repertoire.changer_acces(reg, "jobup", "en session")         # vient du fichier
    with pytest.raises(repertoire.SourceRefusee):
        repertoire.changer_acces(reg, "inconnue", "en session")


@pytest.fixture
def web(reg, monkeypatch):
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    return TestClient(appmod.app)


def test_page_sources_examen(web, reg, monkeypatch):
    lances = []
    monkeypatch.setattr(examen, "examiner_en_tache", lances.append)
    r = web.post("/sources/ajouter", data={"nom": "Exemple Emplois", "url": "https://www.exemple-emplois.ch",
                                           "examiner": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/sources/exemple-emplois?")
    assert lances == ["exemple-emplois"]
    assert "Faire examiner la source par l'IA" in web.get("/sources").text

    examen.noter(reg, "exemple-emplois", resultat=examen.verifier(un_examen()))
    page = web.get("/sources/exemple-emplois").text
    assert "Accès automatisé interdit" in page and "Examen de l'IA" in page and "Réexaminer" in page
    assert "https://www.exemple-emplois.ch/cgu" in page
    assert "Noter l'accès « alertes e-mail »" in page

    r = web.post("/sources/appliquer", data={"id": "exemple-emplois", "acces": "alertes e-mail"}, follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    assert repertoire.lister(reg)["exemple-emplois"]["acces"] == "alertes e-mail"
    assert "Noter l'accès" not in web.get("/sources/exemple-emplois").text

    web.post("/sources/examiner", data={"id": "jobup"}, follow_redirects=False)
    assert lances[-1] == "jobup"
    r = web.post("/sources/examiner", data={"id": "nulle-part"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]


def test_page_sources_examen_en_cours_et_echec(web, reg, monkeypatch):
    sid = repertoire.ajouter(reg, "Exemple Emplois", "https://www.exemple-emplois.ch")
    monkeypatch.setattr(examen, "EN_COURS", {sid})
    assert "L'IA examine les conditions" in web.get("/sources/%s" % sid).text
    assert "examen en cours" in web.get("/sources").text
    monkeypatch.setattr(examen, "EN_COURS", set())
    examen.noter(reg, sid, erreur="limite d'usage atteinte")
    assert "Examen impossible : limite d&#39;usage atteinte" in web.get("/sources/%s" % sid).text


def test_liste_des_sources_et_page_d_une_source(web, reg):
    """La liste n'a qu'une ligne par source ; le détail et les réglages sont sur la page de la source."""
    sid = repertoire.ajouter(reg, "Exemple Emplois", "https://www.exemple-emplois.ch", remarques="à voir")
    liste = web.get("/sources").text
    assert 'class="reglage-ligne" href="/sources/%s"' % sid in liste and "à voir" not in liste
    page = web.get("/sources/%s" % sid).text
    assert 'class="retour" href="/sources"' in page and 'class="retour" href="/reglages"' not in page
    assert "<h1>Exemple Emplois</h1>" in page and "à voir" in page and "Retirer cette source" in page
    assert "ok=0" in web.get("/sources/nulle-part", follow_redirects=False).headers["location"]


def test_sources_par_defaut_et_leur_statut(web, reg, monkeypatch):
    """Job-Room, jobup, jobs.ch et Indeed sont là dès l'installation, chacun avec son statut."""
    from pathlib import Path
    from jobsearch import config
    monkeypatch.setattr(config, "CONFIG", Path(__file__).resolve().parents[1] / "config")    # le vrai socle
    liste = web.get("/sources").text
    for sid in ("job-room", "jobup", "jobs-ch", "indeed"):
        assert 'href="/sources/%s"' % sid in liste
    assert "non répertoriée" not in liste
    assert '<span class="puce ">pas encore interrogée</span>' in liste          # Job-Room avant tout passage
    assert '<span class="puce nouvelle">à brancher</span>' in liste             # Indeed, connecteur inactif
    reg.cx.execute("INSERT INTO sources (id, etat, dernier_test_le, dernier_test_ok) VALUES ('jobup', 'en service', '2026-11-16', 1)")
    reg.cx.commit()
    assert '<span class="puce B">en service</span>' in web.get("/sources").text
