import httpx
import pytest

from conftest import annonce, fixture
from jobsearch.fingerprint import empreinte
from jobsearch.ingest import ingerer, regrouper
from jobsearch.sources import jobroom, jobup
from jobsearch.sources.base import Annonce, Client, RouteCassee


def client(routes):
    def gerer(req):
        for (methode, morceau), rep in routes.items():
            if req.method == methode and morceau in str(req.url):
                return httpx.Response(200, json=rep) if not isinstance(rep, int) else httpx.Response(rep)
        return httpx.Response(404)
    return Client(pause=0, transport=httpx.MockTransport(gerer))


def test_jobroom_conversion():
    a = jobroom.convertir(fixture("jobroom_recherche.json")[0], "Product Manager")
    assert a.source == "job-room" and a.entreprise == "Audemars Piguet SA"
    assert a.titre == "Product Manager" and "<em>" not in a.titre
    assert a.canton == "VD" and a.taux_max == 100 and a.permanent is True
    assert a.url.endswith(a.id_source) and a.requetes == ["Product Manager"]


def test_jobroom_relais_jobup_ignore():
    brut = fixture("jobroom_recherche.json")[0]
    brut["jobAdvertisement"]["jobContent"]["company"]["name"] = "Jobup"
    assert jobroom.convertir(brut) is None


def test_jobroom_detail_complete_le_texte():
    src = jobroom.JobRoom(client({("GET", "/jobAdvertisements/"): fixture("jobroom_detail.json")}))
    a = jobroom.convertir(fixture("jobroom_recherche.json")[0])
    court = len(a.texte)
    src.detail(a)
    assert len(a.texte) > court and a.detail_charge


def test_jobroom_route_cassee():
    src = jobroom.JobRoom(client({("POST", "_search"): 400}))
    with pytest.raises(RouteCassee):
        src.tester()


def test_jobup_conversion_et_detail():
    doc = fixture("jobup_recherche.json")["documents"][0]
    a = jobup.convertir(doc, "Product Manager")
    assert a.entreprise == "Banque Cantonale Vaudoise" and a.taux_max == 100
    src = jobup.Jobup(client({("GET", "/search/job/"): fixture("jobup_detail.json")}))
    src.detail(a)
    assert a.canton == "VD" and a.url_postuler.startswith("https://jobs.bcv.ch")
    assert len(a.texte) > 500 and "<p>" not in a.texte


def test_jobup_fenetre_filtrée():
    import datetime as dt
    rep = fixture("jobup_recherche.json")
    src = jobup.Jobup(client({("GET", "/public/search?"): rep}))
    tous = src.chercher("Product Manager", "Vaud", 365, aujourdhui=dt.date(2026, 10, 24))
    recents = src.chercher("Product Manager", "Vaud", 3, aujourdhui=dt.date(2026, 10, 24))
    assert len(tous) == len(rep["documents"]) and len(recents) < len(tous)


def _a(**kw):
    base = dict(source="jobup", id_source="x1", entreprise="Exemple SA", titre="Product Manager")
    base.update(kw)
    return Annonce(**base)


def test_regrouper_fusionne_les_requetes():
    res = regrouper([_a(requetes=["UX Lead"]), _a(requetes=["Product Manager"])])
    assert len(res) == 1 and res[0].requetes == ["UX Lead", "Product Manager"]


def test_ingestion_dedoublonne_par_id_puis_par_empreinte(reg):
    b = ingerer(reg, [_a()])
    assert len(b["nouvelles"]) == 1
    # Même id de plateforme, titre retouché : même annonce.
    b = ingerer(reg, [_a(titre="Product Manager (Senior)")])
    assert b["revues"] == [empreinte("Exemple SA", "Product Manager")]
    # Autre plateforme, même entreprise et titre : même annonce, deux sources.
    b = ingerer(reg, [_a(source="job-room", id_source="y9")])
    f = reg.get(b["revues"][0])
    assert f["sources"] == ["jobup", "job-room"] and f["occurrences"] == 3
    assert reg.cx.execute("SELECT COUNT(*) FROM annonces").fetchone()[0] == 1


def test_annonce_ecartee_ne_revient_pas(reg):
    e = annonce(reg, statut="C", motif="déjà écartée", ids_source=["jobup:x1"])
    b = ingerer(reg, [_a()])
    assert b["nouvelles"] == [] and reg.get(e)["statut"] == "C"


def test_expiree_reapparait(reg):
    e = annonce(reg, statut="expiree")
    b = ingerer(reg, [_a()])
    assert b["reapparues"] == [e] and reg.get(e)["statut"] == "nouvelle"


def test_texte_remplace_seulement_si_nettement_plus_long(reg):
    ingerer(reg, [_a(texte="court " * 20)])
    ingerer(reg, [_a(texte="court " * 30)])
    e = empreinte("Exemple SA", "Product Manager")
    assert len(reg.get(e)["texte"]) < 150
    ingerer(reg, [_a(texte="long " * 400)])
    assert len(reg.get(e)["texte"]) > 1500


def test_texte_complet_de_plateforme_remplace_un_resume_plus_long(reg):
    e = annonce(reg, statut="B", texte="Résumé très détaillé " * 200, texte_origine="coffre",
                ids_source=["jobup:x1"])
    ingerer(reg, [_a(texte="extrait court")])                        # extrait de recherche : ignoré
    assert reg.get(e)["texte_origine"] == "coffre"
    ingerer(reg, [_a(texte="Original job ad in English. " * 10, detail_charge=True)])
    f = reg.get(e)
    assert f["texte"].startswith("Original job ad") and f["texte_origine"] == "plateforme"
    assert f["extra"]["resume_coffre"].startswith("Résumé")


def test_jobroom_garde_la_premiere_description():
    brut = fixture("jobroom_recherche.json")[0]
    jc = brut["jobAdvertisement"]["jobContent"]
    jc["jobDescriptions"] = [{"languageIsoCode": "en", "title": "Product Owner", "description": "Original in English."},
                             {"languageIsoCode": "fr", "title": "Product Owner", "description": "Traduction française, plus longue que l'original."}]
    assert jobroom.convertir(brut).texte == "Original in English."
