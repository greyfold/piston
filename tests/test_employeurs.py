import datetime as dt

import httpx
import pytest

from conftest import FIXTURES, fixture
from jobsearch import repertoire
from jobsearch.fingerprint import empreinte
from jobsearch.ingest import ingerer
from jobsearch.sources import employeurs, jobup
from jobsearch.sources.base import Client, RouteCassee, code_canton


def client(routes):
    """routes : {(méthode, morceau d'adresse): JSON, nom de fichier .html, ou code HTTP}."""
    def gerer(req):
        for (methode, morceau), rep in routes.items():
            if req.method == methode and morceau in str(req.url):
                if isinstance(rep, int):
                    return httpx.Response(rep)
                if isinstance(rep, str) and rep.endswith(".html"):
                    return httpx.Response(200, text=(FIXTURES / rep).read_text(encoding="utf-8"))
                return httpx.Response(200, json=rep)
        return httpx.Response(404)
    return Client(pause=0, transport=httpx.MockTransport(gerer))


def _src(nom, routes):
    """Deux pages carrière d'exemple, branchées comme la page Sources le ferait."""
    c = client(routes)
    return {"romande-energie": employeurs.Johdi(c, "romande-energie", "Romande Energie SA",
                                                "https://www.romande-energie.ch/carrieres/nos-offres-demploi"),
            "logitech-careers": employeurs.Workday(c, "logitech-careers", "Logitech Europe S.A.",
                                                   "logitech.wd5.myworkdayjobs.com", "logitech", "Logitech",
                                                   pays="187134fccb084a0ea9b4b95f23890dbe",
                                                   cantons={"Lausanne": "VD"})}[nom]


def test_date_relative_workday():
    j = dt.date(2026, 10, 26)
    assert employeurs._relative("Offre publiée il y a 5 jours", j) == "2026-10-21"
    assert employeurs._relative("Offre publiée aujourd'hui", j) == "2026-10-26"
    assert employeurs._relative("Posted Yesterday", j) == "2026-10-25"
    assert employeurs._relative("Offre publiée il y a 30 jours ou plus", j) == ""
    assert employeurs._relative("Posted 30+ Days Ago", j) == ""


def test_code_canton():
    assert code_canton("Vaud") == "VD" and code_canton("Genève") == "GE" and code_canton("VS") == "VS"
    assert code_canton("") is None and code_canton("Lombardie") is None


def test_workday_liste_et_detail():
    src = _src("logitech-careers", {("POST", "/Logitech/jobs"): fixture("workday_liste.json"),
                                    ("GET", "/wday/cxs/logitech/Logitech/job/"): fixture("workday_detail.json")})
    offres = src.offres(aujourdhui=dt.date(2026, 10, 26))
    assert len(offres) == 6 and all(a.entreprise == "Logitech Europe S.A." and a.source == "logitech-careers" for a in offres)
    a = offres[0]
    assert a.id_source == "148542" and a.lieu == "Lausanne VD" and a.canton == "VD"
    assert a.publiee_le == "2026-10-21" and a.requetes == ["page carrière"]
    plusieurs = [o for o in offres if "sites" in o.url or o.lieu == ""]
    assert plusieurs and all(o.canton is None for o in plusieurs)       # « 4 sites » : lieu inconnu avant le détail
    src.detail(a)
    assert a.detail_charge and len(a.texte) > 500 and "<p>" not in a.texte
    assert a.publiee_le == "2026-10-21" and a.url.startswith("https://logitech.wd5.myworkdayjobs.com/Logitech/job/")


def test_workday_route_cassee():
    with pytest.raises(RouteCassee):
        _src("logitech-careers", {("POST", "/jobs"): 500}).tester()


def test_johdi_cle_lue_sur_la_page():
    src = _src("romande-energie", {("GET", "romande-energie.ch"): "johdi_page.html",
                                   ("GET", "/company/CLE-TEST/publicationFlows/web/offers/fr"): fixture("johdi_liste.json"),
                                   ("GET", "/company/CLE-TEST/publicationFlows/web/offer/"): fixture("johdi_detail.json")})
    offres = src.offres()
    assert len(offres) == 6 and all(o.entreprise == "Romande Energie SA" and o.canton == "VD" for o in offres)
    a = offres[4]
    assert a.publiee_le == "2026-10-15" and a.taux_min == 80 and a.taux_max == 100 and a.permanent is True
    assert a.url.endswith("#/offer/%s/%s" % (a.id_source, fixture("johdi_liste.json")[4]["slug"]))
    court = len(a.texte)
    src.detail(a)
    assert len(a.texte) > court and a.url_postuler.startswith("https://ats.johdisuite.ch/")
    assert a.fin_publication == "2026-11-08"


def test_johdi_sans_cle():
    src = _src("romande-energie", {("GET", "romande-energie.ch"): "loro_page.html"})
    with pytest.raises(RouteCassee, match="clé"):
        src.tester()


def test_repertoire_affiche_les_sources_branchees(reg, monkeypatch):
    """Indeed est toujours « automatique » : son connecteur (réglé sur sa page de source) l'allume ou l'éteint."""
    from jobsearch import connecteurs
    connecteurs.activer("indeed", False)
    fiches = repertoire.lister(reg)
    assert fiches["indeed"]["acces"] == "automatique" and fiches["indeed"]["actif"] is False
    connecteurs.activer("indeed", True)
    assert repertoire.lister(reg)["indeed"]["actif"] is True


def test_aucune_page_carriere_dans_le_code():
    assert employeurs.connecteurs(None) == {} and not employeurs.BRANCHEES


def test_passage_lit_toute_la_page_carriere(reg, monkeypatch):
    from jobsearch import courrier, evaluation, pipeline
    from test_pipeline_web import FausseSource
    monkeypatch.setattr(courrier, "lire_boite", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "notifier", lambda *a, **k: None)
    monkeypatch.setattr("jobsearch.moteur.ouvrir", lambda: (_ for _ in ()).throw(evaluation.EvaluationImpossible("test")))
    re_ = _src("romande-energie", {("GET", "romande-energie.ch"): "johdi_page.html",
                                   ("GET", "/offers/fr"): fixture("johdi_liste.json"),
                                   ("GET", "/offer/"): fixture("johdi_detail.json")})
    r = pipeline.Passage(reg, sources={"job-room": FausseSource("job-room"), "romande-energie": re_}).lancer()
    assert r["statut"] == "complet", r
    vues = [f for f in reg.toutes() if "romande-energie" in f["sources"]]
    assert len(vues) == 6
    # Électriciens et apprentis : écartés au pré-tri sur l'intitulé.
    assert all(f["statut"] == "C" for f in vues)
