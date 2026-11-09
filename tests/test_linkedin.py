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
    def absent(*a, **k):
        class R:
            returncode, stdout = 44, ""
        return R()
    monkeypatch.setattr("jobsearch.cles._run", absent)
    client, _ = faux_apify()
    src = linkedin.LinkedIn(client, {}, ["q"], "Lausanne")
    with pytest.raises(RouteCassee, match="trousseau"):
        src.tester()


def test_passage_branche_linkedin_selon_reglage(monkeypatch, reg):
    from jobsearch import pipeline
    monkeypatch.setattr("jobsearch.sources.catalogue.reglages", lambda: {"linkedin": {"actif": True}})
    assert "linkedin" in pipeline.Passage(reg).sources()
    monkeypatch.setattr("jobsearch.sources.catalogue.reglages", lambda: {"linkedin": {"actif": False}})
    assert "linkedin" not in pipeline.Passage(reg).sources()
