"""Une annonce ajoutée à la main : lien de plateforme, page quelconque, texte collé."""

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import annonce, fixture
from jobsearch import ajout
from jobsearch.moteur import FauxMoteur
from jobsearch.sources.base import Client

TEXTE = ("Exemple SA recherche un ou une Product Manager pour son équipe à Lausanne. Vous définissez la "
         "feuille de route du produit avec les équipes et les clients, et vous suivez les indicateurs. "
         "Nous offrons un poste à 100 %, un cadre de travail agréable et des possibilités de formation. ") * 2
PAGE = ("<html><nav>Accueil Carrières Contact</nav><main><h1>Product Manager (80-100 %)</h1><p>" + TEXTE +
        "</p></main><footer>© Exemple SA</footer></html>")


def client(reponses):
    def repondre(req):
        for frag, corps in reponses.items():
            if frag in str(req.url):
                if isinstance(corps, str):
                    return httpx.Response(200, text=corps, headers={"Content-Type": "text/html"})
                return httpx.Response(200, json=corps)
        return httpx.Response(404)
    return Client(pause=0, transport=httpx.MockTransport(repondre))


@pytest.fixture(autouse=True)
def jamais_chrome(monkeypatch):
    from jobsearch.sources import page_ia, base
    def interdit(url, attente_ms=5000):
        raise base.RouteCassee("Chrome interdit en test")
    monkeypatch.setattr(page_ia, "html_rendu", interdit)


def test_lien_job_room_par_la_route(reg):
    brut = fixture("jobroom_detail.json")
    url = "https://www.job-room.ch/job-detail/%s" % brut["id"]
    r = ajout.ajouter(reg, url, client=client({"/jobAdvertisements/": brut}))
    f = reg.get(r["empreinte"])
    assert r["nouvelle"] and f["statut"] == "nouvelle"
    assert f["sources"] == ["job-room"] and f["ids_source"] == ["job-room:%s" % brut["id"]]
    assert f["texte_origine"] == "plateforme" and f["extra"]["ajout_manuel"]
    assert any("ajoutée à la main · route job-room" in j["texte"] for j in reg.journal(r["empreinte"]))
    # Le même lien une seconde fois : rien n'est dupliqué.
    r2 = ajout.ajouter(reg, url, client=client({"/jobAdvertisements/": brut}))
    assert r2["empreinte"] == r["empreinte"] and not r2["nouvelle"] and "déjà au registre" in r2["message"]
    assert reg.stats()["total"] == 1


def test_lien_jobs_ch(reg):
    doc = fixture("jobup_detail.json")
    url = "https://www.jobs.ch/fr/offres-emplois/detail/%s/" % doc["job_id"]
    a = ajout.plateforme(url, client({"jobs.ch/api/v1/public/search/job/": doc}))
    assert a.source == "jobs-ch" and a.id_source == doc["job_id"] and a.texte
    assert ajout.plateforme("https://www.exemple.ch/offre/1", client({})) is None


def test_texte_colle_avec_employeur_et_intitule_sans_ia(reg):
    r = ajout.ajouter(reg, texte=TEXTE, entreprise="Exemple SA", titre="Product Manager", lieu="Lausanne")
    f = reg.get(r["empreinte"])
    assert f["sources"] == ["ajout-manuel"] and f["ids_source"][0].startswith("ajout-manuel:texte-")
    assert f["canton"] == "VD" and f["texte"] == TEXTE.strip() and f["requetes"] == ["ajout manuel"]


def test_page_quelconque_lue_par_le_moteur(reg):
    lu = ajout.Lecture(est_une_annonce=True, entreprise="Exemple SA", titre="Product Manager (80-100 %)",
                       lieu="Lausanne", taux_min=80, taux_max=100, duree="indéterminée", publiee_le="",
                       debut="Product Manager (80-100 %) Exemple SA recherche un ou une",
                       fin="un cadre de travail agréable et des possibilités de formation.")
    c = FauxMoteur([lu])
    url = "https://www.exemple.ch/carrieres/pm"
    r = ajout.ajouter(reg, url, client=client({"exemple.ch": PAGE}), c=c)
    f = reg.get(r["empreinte"])
    assert f["titre"] == "Product Manager (80-100 %)" and f["entreprise"] == "Exemple SA"
    assert f["taux_min"] == 80 and f["permanent"] is True and f["url_postuler"] == url
    assert f["texte"].startswith("### Product Manager") and "Accueil Carrières" not in f["texte"]
    assert f["texte"].endswith("possibilités de formation.") and "©" not in f["texte"]
    assert "Accueil Carrières" not in c.appels[0][1]                 # menus retirés avant l'envoi


def test_intitule_invente_ecarte(reg):
    lu = ajout.Lecture(est_une_annonce=True, entreprise="Exemple SA", titre="Chief Product Officer", lieu="",
                       taux_min=0, taux_max=0, duree="", publiee_le="", debut="", fin="")
    with pytest.raises(ajout.AjoutRefuse, match="intitulé introuvable"):
        ajout.ajouter(reg, texte=TEXTE, c=FauxMoteur([lu]))
    assert reg.stats()["total"] == 0


def test_sans_ia_il_faut_employeur_et_intitule(reg):
    with pytest.raises(ajout.AjoutRefuse, match="à indiquer"):
        ajout.ajouter(reg, texte=TEXTE)                    # moteur absent (conftest)


def test_linkedin_demande_le_texte(reg):
    with pytest.raises(ajout.AjoutRefuse, match="LinkedIn ne se lit pas ici"):
        ajout.ajouter(reg, "https://www.linkedin.com/jobs/view/123", client=client({}))
    r = ajout.ajouter(reg, "https://www.linkedin.com/jobs/view/123", TEXTE, "Exemple SA", "Product Manager")
    assert reg.get(r["empreinte"])["url"] == "https://www.linkedin.com/jobs/view/123"


def test_le_pre_tri_n_ecarte_pas_une_annonce_choisie(reg):
    r = ajout.ajouter(reg, texte=TEXTE, entreprise="Exemple SA", titre="Senior Software Engineer")
    f = reg.get(r["empreinte"])
    assert f["statut"] == "nouvelle" and r["pre_tri"]
    assert "Le pré-tri l'aurait écartée" in r["message"]
    assert any("aurait été écartée" in j["texte"] for j in reg.journal(r["empreinte"]))


def test_annonce_ecartee_par_le_pre_tri_remise_a_trancher(reg):
    e = annonce(reg, entreprise="Exemple SA", titre="Product Manager", statut="C", passe_ecart=1, motif="Hors région")
    r = ajout.ajouter(reg, texte=TEXTE, entreprise="Exemple SA", titre="Product Manager")
    assert r["empreinte"] == e and reg.get(e)["statut"] == "nouvelle" and "remise à trancher" in r["message"]


def test_pas_go_de_la_personne_respecte(reg):
    e = annonce(reg, entreprise="Exemple SA", titre="Product Manager", statut="C", passe_ecart=3, motif="Pas go")
    r = ajout.ajouter(reg, texte=TEXTE, entreprise="Exemple SA", titre="Product Manager")
    assert r["empreinte"] == e and reg.get(e)["statut"] == "C" and "statut « C »" in r["message"]


def test_decouper_garde_le_texte_si_les_bornes_manquent():
    assert ajout.decouper(TEXTE, "mots absents du texte ici", "autres mots absents") == TEXTE
    assert ajout.decouper(TEXTE, "", "") == TEXTE


@pytest.fixture
def web(reg, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    return TestClient(appmod.app)


def test_page_et_formulaire(web, reg):
    assert 'action="/offres/ajouter"' in web.get("/offres/ajouter").text
    assert 'href="/offres/ajouter"' in web.get("/offres").text
    r = web.post("/offres/ajouter", data={"texte": TEXTE, "entreprise": "Exemple SA", "titre": "Product Manager"},
                 follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/annonce/")
    reg.cx.rollback()
    assert reg.stats()["par_statut"] == {"nouvelle": 1}
    refus = web.post("/offres/ajouter", data={"texte": TEXTE, "lieu": "Morges"})
    assert refus.status_code == 200 and "Refusé" in refus.text and "Morges" in refus.text


def test_evaluation_en_cours_affichee(web, reg):
    e = annonce(reg, texte=TEXTE)
    ajout.EN_COURS.add(e)
    try:
        assert "Évaluation en cours" in web.get("/annonce/" + e).text
    finally:
        ajout.EN_COURS.discard(e)


@pytest.mark.parametrize("brut, propre", [
    ("https://www.manpower.ch/en/job/641204//linkedin?utm_source=linkedin&igbTracker=978977973&referer=linkedin.com",
     "https://www.manpower.ch/en/job/641204//linkedin"),
    ("https://jobs.exemple.ch/offre?id=42&utm_campaign=x&gclid=abc", "https://jobs.exemple.ch/offre?id=42"),
    ("https://www.exemple.ch/carrieres/pm", "https://www.exemple.ch/carrieres/pm"),
])
def test_lien_sans_parametres_de_suivi(brut, propre):
    assert ajout.nettoyer(brut) == propre


def test_lien_garde_sans_suivi(reg):
    r = ajout.ajouter(reg, "https://www.exemple.ch/pm?utm_source=linkedin&id=7", TEXTE, "Exemple SA", "Product Manager")
    f = reg.get(r["empreinte"])
    assert f["url"] == f["url_postuler"] == "https://www.exemple.ch/pm?id=7"
    assert f["ids_source"] == ["ajout-manuel:https://www.exemple.ch/pm?id=7"]
