import json

import httpx
import pytest

from jobsearch.sources import linkedin
from jobsearch.sources.base import Client, RouteCassee

ITEMS = [
    {"jobId": "4012345678", "title": "AI Product Manager", "companyName": "Nexthink",
     "location": "Lausanne, Vaud, Switzerland", "postedAt": "2026-10-25",
     "jobUrl": "https://ch.linkedin.com/jobs/view/ai-product-manager-at-nexthink-4012345678?trk=x",
     "applyUrl": "https://nexthink.example/apply/1",
     "description": "<p>Nous cherchons un <strong>AI Product Manager</strong>.</p><ul><li>Produit</li></ul>"},
    {"id": "4099999999", "jobTitle": "Head of UX", "company": {"name": "Swissquote"},
     "jobLocation": "Gland, Vaud, Switzerland", "postedAt": "il y a 2 jours", "descriptionText": "Texte brut."},
    {"jobUrl": "https://www.linkedin.com/jobs/view/4011111111/", "title": "Design Manager", "companyName": "Logitech",
     "location": "Switzerland"},
    {"title": "", "companyName": "Vide"},
]


def faux_apify(statuts=("RUNNING", "SUCCEEDED"), items=ITEMS):
    appels = []
    etats = {}

    def gerer(req):
        appels.append((req.method, req.url.path, dict(req.url.params), req.headers.get("authorization")))
        p = req.url.path
        if req.method == "POST" and p.endswith("/runs"):
            rid = "r%d" % (len(etats) + 1)
            etats[rid] = list(statuts)
            corps = json.loads(req.content)
            return httpx.Response(201, json={"data": {"id": rid, "status": "READY", "defaultDatasetId": "d" + rid,
                                                     "_query": corps["query"]}})
        if req.method == "GET" and "/actor-runs/" in p:
            rid = p.rsplit("/", 1)[-1]
            st = etats[rid].pop(0) if len(etats[rid]) > 1 else etats[rid][0]
            return httpx.Response(200, json={"data": {"id": rid, "status": st, "defaultDatasetId": "d" + rid}})
        if req.method == "POST" and p.endswith("/abort"):
            return httpx.Response(200, json={"data": {}})
        if "/datasets/" in p:
            return httpx.Response(200, json=items)
        if req.method == "GET" and "/acts/" in p:
            return httpx.Response(200, json={"data": {"name": "linkedin-jobs-scraper"}})
        return httpx.Response(404)

    return Client(pause=0, transport=httpx.MockTransport(gerer)), appels


def _src(client, requetes=("AI Product Manager",), **reglages):
    return linkedin.LinkedIn(client, reglages, list(requetes), "Lausanne, Vaud, Switzerland",
                             jeton="apify_api_TEST", dormir=lambda s: None)


def test_conversion_des_champs_variables():
    a, b, c, vide = (linkedin.convertir(i, "q") for i in ITEMS)
    assert vide is None
    assert (a.source, a.id_source, a.entreprise, a.canton, a.lieu) == ("linkedin", "4012345678", "Nexthink", "VD", "Lausanne VD")
    assert a.url == "https://www.linkedin.com/jobs/view/4012345678/" and a.publiee_le == "2026-10-25"
    assert "<p>" not in a.texte and "**AI Product Manager**" in a.texte and a.detail_charge
    assert b.entreprise == "Swissquote" and b.id_source == "4099999999" and b.publiee_le
    assert c.id_source == "4011111111" and c.canton is None and not c.detail_charge


def test_periode_suit_la_fenetre():
    assert [linkedin.periode(j) for j in (1, 2, 3, 5, 14, 90)] == \
        ["86400", "259200", "259200", "604800", "2592000", "2592000"]


def test_runs_lances_ensemble_puis_lus():
    client, appels = faux_apify()
    src = _src(client, requetes=("AI Product Manager", "UX Lead OR Head of UX"), plafond_usd_par_run=0.5)
    offres = src.offres(jours=2)
    assert len(offres) == 6 and {a.requetes[0] for a in offres} == {"AI Product Manager", "UX Lead OR Head of UX"}
    lancements = [a for a in appels if a[0] == "POST" and a[1].endswith("/runs")]
    assert len(lancements) == 2 and all(a[2] == {"maxTotalChargeUsd": "0.5"} for a in lancements)
    # Les deux runs partent avant la première attente : ils tournent en parallèle chez Apify.
    assert [a[1].endswith("/runs") for a in appels[:2]] == [True, True]
    # Jeton dans l'en-tête, jamais dans l'adresse.
    assert all(a[3] == "Bearer apify_api_TEST" for a in appels)
    assert not any("apify_api_TEST" in json.dumps(a[2]) for a in appels)


def test_run_trop_long_arrete():
    client, appels = faux_apify(statuts=("RUNNING",))
    with pytest.raises(RouteCassee, match="aucun run"):
        _src(client, attente_max_s=0).offres(jours=1)
    assert any(a[0] == "POST" and a[1].endswith("/abort") for a in appels)


def test_tester_ne_lance_aucun_run():
    client, appels = faux_apify()
    assert _src(client).tester() == 1
    assert [a[0] for a in appels] == ["GET"]


def test_sans_jeton(monkeypatch):
    client, _ = faux_apify()
    src = linkedin.LinkedIn(client, {}, ["q"], "Lausanne")
    from jobsearch import hote
    with pytest.raises(RouteCassee, match=hote.libelle("coffre")):
        src.tester()


def test_passage_branche_linkedin_selon_reglage(monkeypatch, reg):
    from jobsearch import pipeline
    monkeypatch.setattr("jobsearch.sources.catalogue.reglages", lambda: {"linkedin": {"actif": True}})
    assert "linkedin" in pipeline.Passage(reg).sources()
    monkeypatch.setattr("jobsearch.sources.catalogue.reglages", lambda: {"linkedin": {"actif": False}})
    assert "linkedin" not in pipeline.Passage(reg).sources()


def faux_compte(couts=(0.06, 0.10), limite=5.0, credit=5.0, utilise=1.2):
    """Le compte Apify vu par l'API : offre, limites du mois, derniers runs de l'acteur."""
    vus = []

    def gerer(req):
        vus.append((req.url.path, dict(req.url.params)))
        p = req.url.path
        if p.endswith("/users/me"):
            return httpx.Response(200, json={"data": {"plan": {"id": "FREE", "description": "Free plan",
                                                                "monthlyUsageCreditsUsd": credit}}})
        if p.endswith("/users/me/limits"):
            return httpx.Response(200, json={"data": {
                "monthlyUsageCycle": {"startAt": "2026-11-10T00:00:00.000Z", "endAt": "2026-12-10T00:00:00.000Z"},
                "limits": {"maxMonthlyUsageUsd": limite}, "current": {"monthlyUsageUsd": utilise}}})
        if p.endswith("/runs"):
            return httpx.Response(200, json={"data": {"items": [{"status": "SUCCEEDED", "usageTotalUsd": c} for c in couts]}})
        return httpx.Response(404, json={})
    return Client(pause=0, transport=httpx.MockTransport(gerer)), vus


def test_compte_apify_lu_sans_rien_lancer():
    client, vus = faux_compte()
    c = linkedin.compte(client, jeton="apify_api_x")
    assert c["offre"] == "Free plan" and c["limite"] == 5.0 and c["utilise"] == 1.2 and c["fin_cycle"] == "2026-12-10"
    assert c["cout_moyen"] == pytest.approx(0.08) and c["runs"] == 2
    assert all(not p.endswith("/runs") or q.get("status") == "SUCCEEDED" for p, q in vus)   # lecture seule


def test_conseil_selon_le_compte():
    import datetime as dt
    client, _ = faux_compte()
    c = linkedin.compte(client, jeton="apify_api_x")
    k = linkedin.conseil(c, 2, 1.0, aujourdhui=dt.date(2026, 11, 25))
    # 3.80 $ restants, 2 recherches par jour pendant 15 jours : 0.12 $ chacune au plus ;
    # une recherche coûte 0.08 $ en moyenne, 0.12 $ avec la marge : le crédit suffit.
    assert k["jours"] == 15 and k["reste"] == pytest.approx(3.8) and k["suffisant"] is True
    assert k["plafond"] == 0.1 and k["a_changer"]
    serre = linkedin.conseil(c, 6, 1.0, aujourdhui=dt.date(2026, 11, 25))
    assert serre["suffisant"] is False and serre["plafond"] == 0.05 and serre["serre"]   # borné par le crédit restant
    assert not k["serre"]
    neuf = linkedin.conseil(dict(c, cout_moyen=None, runs=0), 2, 1.0, aujourdhui=dt.date(2026, 11, 25))
    assert neuf["prevu"] is None and neuf["plafond"] == 0.1              # sans historique : la part du crédit


def test_page_linkedin_guide_puis_compte(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch import cles, profil
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    web = TestClient(appmod.app)
    page = web.get("/sources/linkedin").text
    assert "Ajouter le jeton" in page and "Ton compte Apify" not in page
    r = web.post("/sources/linkedin/activer", follow_redirects=False)          # pas sans jeton
    assert "ok=0" in r.headers["location"] and not profil.lire(profil.charger(), "sources.linkedin.actif")

    monkeypatch.setattr(cles, "etat", lambda nom: dict(cles.SERVICES[nom], id=nom, presente=True, fin="abcd"))
    monkeypatch.setattr(linkedin, "jeton_trousseau", lambda: "apify_api_x")
    monkeypatch.setattr("jobsearch.sources.base.Client", lambda **kw: faux_compte()[0])
    page = web.get("/sources/linkedin").text
    assert "Ton compte Apify · Free plan" in page and "enregistré · …abcd" in page and "Appliquer" in page
    web.post("/sources/linkedin/activer")
    web.post("/sources/linkedin/plafond", data={"plafond": "0.10"})
    from jobsearch.config import reglages
    assert reglages()["linkedin"]["actif"] is True and reglages()["linkedin"]["plafond_usd_par_run"] == 0.1
    assert "ok=0" in web.post("/sources/linkedin/plafond", data={"plafond": "abc"}, follow_redirects=False).headers["location"]
