"""Socle (jobs.ch) et sources ajoutées par l'utilisateur : logiciels reconnus, page lue par l'IA, alertes."""

import datetime as dt
import json

import httpx
import pytest

from jobsearch import repertoire
from jobsearch.moteur import FauxMoteur
from jobsearch.sources import ajoutees, logiciels, page_ia
from jobsearch.sources.alertes import AlertesEmail, liens
from jobsearch.sources.base import Client, RouteCassee
from jobsearch.sources.jobup import Jobup


def client(reponses):
    """Client HTTP simulé : {fragment d'adresse: corps (dict → JSON, str → HTML)}."""
    def repondre(req):
        for frag, corps in reponses.items():
            if frag in str(req.url):
                if isinstance(corps, int):
                    return httpx.Response(corps)
                if isinstance(corps, str):
                    return httpx.Response(200, text=corps, headers={"Content-Type": "text/html"})
                return httpx.Response(200, json=corps)
        return httpx.Response(404)
    return Client(pause=0, transport=httpx.MockTransport(repondre))


@pytest.mark.parametrize("url, attendu", [
    ("https://nestle.wd3.myworkdayjobs.com/fr-FR/External", {"type": "workday", "hote": "nestle.wd3.myworkdayjobs.com",
                                                             "tenant": "nestle", "site": "External"}),
    ("https://boards.greenhouse.io/nexthink", {"type": "greenhouse", "org": "nexthink"}),
    ("https://job-boards.greenhouse.io/nexthink/jobs/123", {"type": "greenhouse", "org": "nexthink"}),
    ("https://jobs.lever.co/exemple", {"type": "lever", "org": "exemple", "eu": False}),
    ("https://careers.smartrecruiters.com/Firmenich", {"type": "smartrecruiters", "org": "Firmenich"}),
    ("https://exemple.recruitee.com/", {"type": "recruitee", "org": "exemple"}),
    ("https://exemple.jobs.personio.de/", {"type": "personio", "org": "exemple", "tld": "de"}),
    ("https://www.exemple.ch/carrieres", {"type": "page-ia", "url": "https://www.exemple.ch/carrieres"}),
])
def test_reconnaitre(url, attendu):
    assert logiciels.reconnaitre(url) == attendu


def test_lieu_suisse():
    assert logiciels.lieu_suisse("Lausanne, Switzerland")[1:] == ("VD", True)
    assert logiciels.lieu_suisse("Genève")[1:] == ("GE", True)
    assert logiciels.lieu_suisse("Zürich, CH")[2] is True
    assert logiciels.lieu_suisse("Boston, MA, United States")[2] is False
    assert logiciels.lieu_suisse("")[2] is True                        # lieu inconnu : gardée


def test_greenhouse_garde_la_suisse():
    c = client({"boards-api.greenhouse.io/v1/boards/nexthink/jobs": {"jobs": [
        {"id": 1, "title": "Product Manager", "absolute_url": "https://x/1", "location": {"name": "Lausanne"},
         "updated_at": "2026-11-01T10:00:00Z", "content": "&lt;p&gt;Own the &lt;b&gt;roadmap&lt;/b&gt;&lt;/p&gt;"},
        {"id": 2, "title": "Sales", "absolute_url": "https://x/2", "location": {"name": "Boston, MA"}}]}})
    src = logiciels.construire(c, "nexthink", "Nexthink SA", {"type": "greenhouse", "org": "nexthink"})
    offres = src.offres()
    assert [(a.titre, a.canton, a.entreprise, a.source) for a in offres] == [("Product Manager", "VD", "Nexthink SA", "nexthink")]
    assert "roadmap" in offres[0].texte and offres[0].detail_charge and offres[0].publiee_le == "2026-11-01"


def test_lever_et_recruitee():
    c = client({"api.lever.co/v0/postings/ex": [{"id": "a", "text": "UX Lead", "hostedUrl": "https://l/a",
                                                 "categories": {"location": "Geneva, Switzerland"}, "createdAt": 1762000000000,
                                                 "descriptionPlain": "Lead UX."}],
                "ex.recruitee.com/api/offers": {"offers": [{"id": 9, "title": "Head of Product", "careers_url": "https://r/9",
                                                            "city": "Nyon", "country": "Switzerland", "description": "<p>x</p>"}]}})
    assert logiciels.construire(c, "ex", "Ex SA", {"type": "lever", "org": "ex"}).offres()[0].canton == "GE"
    assert logiciels.construire(c, "ex", "Ex SA", {"type": "recruitee", "org": "ex"}).offres()[0].canton == "VD"


def test_route_changee():
    c = client({"boards-api.greenhouse.io": {"autre": []}})
    with pytest.raises(RouteCassee, match="Greenhouse a changé"):
        logiciels.construire(c, "x", "X", {"type": "greenhouse", "org": "x"}).offres()


PAGE = """<html><body><h1>Nos offres</h1>
<a href="/jobs/pm">Product Manager (80-100 %) – Lausanne</a>
<a href="https://ex.ch/jobs/ux">UX Designer</a>
<a href="/valeurs">Nos valeurs</a><a href="#haut">Haut</a></body></html>"""


def test_page_lue_par_l_ia_sans_offre_inventee(tmp_path):
    c = client({"ex.ch/carrieres": PAGE})
    moteur = FauxMoteur([page_ia.Offres(offres=[
        page_ia.Offre(titre="Product Manager", url="https://ex.ch/jobs/pm", lieu="Lausanne"),
        page_ia.Offre(titre="Data Scientist", url="https://ex.ch/jobs/invente")])])       # lien absent : écartée
    src = page_ia.PageIA(c, "ex", "Ex SA", "https://ex.ch/carrieres", moteur=moteur, cache=tmp_path / "ex.json")
    offres = src.offres()
    assert [(a.titre, a.url, a.canton) for a in offres] == [("Product Manager", "https://ex.ch/jobs/pm", "VD")]
    tache, message = moteur.appels[0]
    assert tache.outils == [] and tache.schema is page_ia.Offres and "- UX Designer | https://ex.ch/jobs/ux" in message
    assert "#haut" not in message
    assert src.offres()[0].titre == "Product Manager" and len(moteur.appels) == 1      # page inchangée : pas de Claude
    assert src.tester() == 3


def test_jobs_ch_meme_route_que_jobup():
    vues = []
    def repondre(req):
        vues.append(str(req.url))
        return httpx.Response(200, json={"documents": [{"job_id": "j1", "title": "Product Owner", "company_name": "Ex SA",
                                                        "publication_date": "2026-11-06T08:00:00+01:00"}], "num_pages": 1})
    src = Jobup(Client(pause=0, transport=httpx.MockTransport(repondre)), site="jobs.ch")
    # Le jour est fixé : la fenêtre se compte depuis « aujourd'hui », et sans cela le test cessait
    # de passer sept jours après la date de publication de l'offre d'exemple (constaté le 14.11).
    a = src.chercher("product owner", "Vaud", 7, aujourdhui=dt.date(2026, 11, 6))[0]
    assert src.nom == "jobs-ch" and a.source == "jobs-ch" and vues[0].startswith("https://www.jobs.ch/api/v1/public/search")
    assert a.url == "https://www.jobs.ch/fr/emplois/detail/j1/"


def test_essayer_puis_brancher(reg, monkeypatch):
    c = client({"boards-api.greenhouse.io/v1/boards/nexthink/jobs": {"jobs": [
        {"id": 1, "title": "Product Manager", "absolute_url": "https://x/1", "location": {"name": "Lausanne"}}]}})
    e = ajoutees.essayer(c, "https://boards.greenhouse.io/nexthink", "Nexthink SA")
    assert e["ok"] and e["n"] == 1 and e["libelle"] == "Greenhouse" and e["exemples"][0][0] == "Product Manager"
    assert repertoire.lister(reg).get("page-carriere-nexthink-sa") is None            # rien d'enregistré
    sid = ajoutees.brancher(reg, "Nexthink SA", "https://boards.greenhouse.io/nexthink", e["connecteur"])
    fiche = repertoire.lister(reg)[sid]
    assert fiche["acces"] == "automatique" and fiche["branchee"] and fiche["type"] == "greenhouse"
    assert list(ajoutees.connecteurs(reg, c)) == [sid] and ajoutees.connecteurs(reg, c)[sid].entreprise == "Nexthink SA"
    ajoutees.activer(reg, sid, False)
    assert ajoutees.connecteurs(reg, c) == {} and repertoire.lister(reg)[sid]["acces"] == "désactivée"
    from jobsearch.sources.catalogue import construire
    ajoutees.activer(reg, sid, True)
    assert sid in construire(c, reg)


def test_essai_rate_dit_pourquoi():
    e = ajoutees.essayer(client({}), "https://boards.greenhouse.io/inconnu", "Inconnu SA")
    assert not e["ok"] and "404" in e["erreur"]


ALERTE = {"subject": "Job alert : 2 nouvelles offres", "sender": "alertes@jobs.ch", "received_date": "2026-11-07 07:00:00",
          "content_preview": "Product Manager – Ex SA – Lausanne\nhttps://www.jobs.ch/fr/emplois/detail/abc/\n"
                             "Se désabonner : https://www.jobs.ch/desabonner"}


def test_alertes_email(reg, monkeypatch):
    from jobsearch import connecteurs
    connecteurs.activer("gmail", False)
    moteur = FauxMoteur([page_ia.Offres(offres=[page_ia.Offre(
        titre="Product Manager", url="https://www.jobs.ch/fr/emplois/detail/abc/", entreprise="Ex SA", lieu="Lausanne")])])
    reponse = {"subject": "Votre candidature", "content_preview": "https://x.ch/a"}
    src = AlertesEmail(reg, client({}), lire=lambda mots, debut, comptes, **k: [ALERTE, reponse], moteur=moteur)
    assert src.tester() == 1
    offres = src.offres()
    assert [(a.source, a.entreprise, a.titre, a.canton) for a in offres] == [("alertes-email", "Ex SA", "Product Manager", "VD")]
    assert len(moteur.appels) == 1 and "Job alert" in moteur.appels[0][1] and reg.meta("alertes_lues_le")
    assert liens(ALERTE["content_preview"])[0] == ("Product Manager – Ex SA – Lausanne", "https://www.jobs.ch/fr/emplois/detail/abc/")


def test_page_sources_essayer_et_brancher(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    monkeypatch.setattr(ajoutees, "essayer", lambda c, url, ent: {
        "ok": True, "n": 2, "libelle": "Greenhouse", "url": url, "entreprise": ent, "type": "greenhouse",
        "connecteur": {"type": "greenhouse", "org": "nexthink"}, "exemples": [("Product Manager", "Lausanne", "https://x/1")]})
    web = TestClient(appmod.app)
    assert "Brancher une page carrière" in web.get("/sources").text
    page = web.post("/sources/essayer", data={"url": "https://boards.greenhouse.io/nexthink", "entreprise": "Nexthink SA"}).text
    assert "2 offres en Suisse" in page and "Brancher cette page" in page
    r = web.post("/sources/brancher", data={"url": "https://boards.greenhouse.io/nexthink", "entreprise": "Nexthink SA"},
                 follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    fiche = repertoire.lister(reg)["page-carriere-nexthink-sa"]
    assert json.loads(fiche["connecteur"])["org"] == "nexthink"
    assert web.post("/sources/essayer", data={"url": "pas une adresse", "entreprise": "X"}, follow_redirects=False).status_code == 303


def test_offre_sans_lien_gardee_si_son_intitule_est_dans_la_page(tmp_path):
    page = "<html><body><h1>Postes</h1><div onclick='ouvrir(3)'>Chef de projet SI\nDélai 09.11.2026</div></body></html>"
    moteur = FauxMoteur([page_ia.Offres(offres=[page_ia.Offre(titre="Chef de projet SI"),
                                                page_ia.Offre(titre="Poste inventé")])])
    src = page_ia.PageIA(client({"ex.ch/postes": page}), "ex", "Ex SA", "https://ex.ch/postes", moteur=moteur,
                         cache=tmp_path / "ex.json")
    offres = src.offres()
    assert [a.titre for a in offres] == ["Chef de projet SI"] and offres[0].url == "https://ex.ch/postes"
    a = src.detail(offres[0])
    assert a.texte.startswith("Chef de projet SI Délai 09.11.2026") and a.detail_charge


def test_page_construite_en_javascript_rendue_par_chrome(monkeypatch):
    rendu = "<html><body><a href='https://ex.ch/jobs/1'>Product Owner</a></body></html>"
    monkeypatch.setattr(page_ia, "html_rendu", lambda url, attente_ms=5000: rendu)
    moteur = FauxMoteur([page_ia.Offres(offres=[]),
                         page_ia.Offres(offres=[page_ia.Offre(titre="Product Owner", url="https://ex.ch/jobs/1")])])
    e = ajoutees.essayer(client({"ex.ch/emplois": "<html><body><div id='app'></div></body></html>"}),
                         "https://ex.ch/emplois", "Ex JS SA", moteur=moteur)
    assert e["ok"] and e["n"] == 1 and e["connecteur"]["rendu"] is True
    vide = FauxMoteur([page_ia.Offres(offres=[]), page_ia.Offres(offres=[])])
    e = ajoutees.essayer(client({"ex.ch/vide": "<html><body><a href='/a'>A</a></body></html>"}),
                         "https://ex.ch/vide", "Ex Vide SA", moteur=vide)
    assert not e["ok"] and "même rendue par Chrome" in e["erreur"]
