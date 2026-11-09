import json

import httpx

from conftest import annonce
from jobsearch import adresses, preremplissage
from jobsearch.navigateur import valeurs_jobroom

LISTE = {"list": [
    {"name": "ELCA Informatik AG", "ehraid": 1, "uidFormatted": "CHE-1", "legalSeat": "Bern", "status": "EXISTIEREND"},
    {"name": "ELCA Informatique SA", "ehraid": 2, "uidFormatted": "CHE-105.944.452", "legalSeat": "Pully", "status": "EXISTIEREND"},
    {"name": "ELCA Informatique SA", "ehraid": 3, "uidFormatted": "CHE-3", "legalSeat": "Rapperswil-Jona", "status": "EXISTIEREND"},
    {"name": "Elca Informatique SA, succursale de Genève", "ehraid": 4, "legalSeat": "Genève", "status": "EXISTIEREND"},
]}
FICHE = {"address": {"street": "Avenue Général Guisan", "houseNumber": "70 a", "swissZipCode": "1009", "town": "Pully"}}


def zefix_simule(liste=LISTE, fiche=FICHE):
    vues = []

    def gerer(req):
        vues.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.url.path.endswith("/search.json"):
            return httpx.Response(200, json=liste)
        if req.url.path.endswith("/2.json"):
            return httpx.Response(200, json=fiche)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(gerer)), vues


def test_zefix_siege_dans_la_ville_de_l_annonce():
    c, vues = zefix_simule()
    a = adresses.zefix("ELCA Informatique SA", "pully", c)
    assert a == {"rue": "Avenue Général Guisan", "numero": "70a", "npa": "1009", "ville": "Pully",
                 "origine": "registre du commerce (Zefix, CHE-105.944.452)"}
    assert vues[0][2]["name"] == "ELCA Informatique SA"


def test_zefix_ambigu_sans_ville_rien():
    c, _ = zefix_simule()
    assert adresses.zefix("ELCA Informatique SA", "", c) is None          # deux sièges, aucun indice
    assert adresses.zefix("ELCA Informatique SA", "lausanne", c) is None


def test_zefix_intermediaire_retire_et_service_en_panne():
    c, vues = zefix_simule(liste={"list": []})
    assert adresses.zefix("Logitech Europe S.A. (via ictjobs)", "", c) is None
    assert vues[0][2]["name"] == "Logitech Europe S.A."
    panne = httpx.Client(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    assert adresses.zefix("ELCA Informatique SA", "pully", panne) is None


def test_adresse_dans_le_texte():
    t = adresses.dans_le_texte
    assert t("OMPI, 34, chemin des Colombettes, 1211 Genève 20, Suisse")["npa"] == "1211"
    assert t("Notre siège, Avenue Général Guisan 70a, 1009 Pully, accueille")["rue"] == "Avenue Général Guisan"
    assert t("Bahnhofstrasse 12, CH-8001 Zürich")["ville"] == "Zürich"
    assert t("Nous avons 1200 collaborateurs depuis 2004 à Lausanne.") is None
    assert t("Lieu de travail : Lausanne, 80-100 %.") is None


def test_renseigner_range_et_note(reg, monkeypatch):
    e = annonce(reg, entreprise="ELCA Informatique SA", titre="Consultant", lieu="Pully VD", statut="retenue")
    c, _ = zefix_simule()
    a = adresses.renseigner(reg, e, client=c)
    assert a["npa"] == "1009" and reg.get(e)["extra"]["adresse_employeur"] == a
    assert any("Avenue Général Guisan 70a, 1009 Pully" in j["texte"] for j in reg.journal(e))
    # Déjà connue : plus aucun appel.
    assert adresses.renseigner(reg, e, client=None) == a


def test_introuvable_note_une_seule_fois(reg):
    e = annonce(reg, entreprise="Organisation inconnue", titre="UX Lead", texte="Poste à Genève.", statut="retenue")
    assert adresses.renseigner(reg, e) is None and adresses.renseigner(reg, e) is None
    assert sum("introuvable" in j["texte"] for j in reg.journal(e)) == 1


def test_preuve_jobroom_prend_l_adresse_rangee():
    f = {"postulee_le": "2026-10-29", "entreprise": "ELCA Informatique SA", "titre": "Consultant", "lieu": "Pully VD",
         "extra": {"adresse_employeur": {"rue": "Avenue Général Guisan", "numero": "70a", "npa": "1009", "ville": "Pully",
                                         "origine": "registre du commerce"}}}
    v = valeurs_jobroom(f, "## 5. Job-Room\n- Raison sociale : ELCA Informatique SA\n- Lieu : Pully\n")
    assert (v["rue"], v["numero"], v["npa"], v["ville"]) == ("Avenue Général Guisan", "70a", "1009", "Pully")


def test_preuve_jobroom_cherche_l_adresse_si_dossier_ancien(reg, monkeypatch):
    e = annonce(reg, entreprise="ELCA Informatique SA", titre="Consultant", lieu="Pully VD", statut="candidature",
                postulee_le="2026-10-29")
    c, _ = zefix_simule()
    vrai = adresses.renseigner
    monkeypatch.setattr(adresses, "renseigner", lambda reg, emp, client=None: vrai(reg, emp, client=c))
    v, _ = preremplissage.jobroom(reg, e)
    assert v["npa"] == "1009" and v["ville"] == "Pully" and v["rue"] == "Avenue Général Guisan"


def web_simule(jobroom=None, localites=()):
    """Job-Room et la liste des localités ; Zefix ne trouve rien."""
    def gerer(req):
        if "job-room.ch" in req.url.host:
            return httpx.Response(200, json={"jobContent": jobroom or {}})
        if "openplzapi" in req.url.host:
            return httpx.Response(200, json=[x for x in localites if x["name"] == req.url.params.get("name")])
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(gerer))


URL_JR = "https://www.job-room.ch/job-detail/60e0ff6b-2d56-4ce2-8487-4fd9d629761f"


def test_adresse_donnee_a_job_room_d_abord():
    c = web_simule({"company": {"name": "Exemple SA", "street": "Rue du Lac", "houseNumber": "12", "postalCode": "1800",
                                "city": "Vevey", "countryIsoCode": "CH"},
                    "location": {"postalCode": "1807", "city": "Blonay", "countryIsoCode": "CH"}})
    a = adresses.trouver({"entreprise": "Exemple SA", "url": URL_JR, "lieu": "Blonay VD"}, c)
    assert (a["rue"], a["numero"], a["npa"], a["ville"]) == ("Rue du Lac", "12", "1800", "Vevey")


def test_agence_sans_adresse_prend_le_lieu_de_travail_job_room():
    c = web_simule({"company": {"name": "ValJob"}, "location": {"postalCode": "1807", "city": "Blonay", "countryIsoCode": "CH"}})
    a = adresses.trouver({"entreprise": "ValJob", "url": URL_JR, "lieu": "Blonay VD", "texte": "Pour l'un de nos clients…"}, c)
    assert (a["rue"], a["npa"], a["ville"]) == ("", "1807", "Blonay") and "lieu de travail" in a["origine"]


def test_lieu_de_travail_hors_job_room_plus_petit_npa_du_canton():
    c = web_simule(localites=[{"postalCode": "1205", "name": "Genève", "canton": {"shortName": "GE"}},
                              {"postalCode": "1201", "name": "Genève", "canton": {"shortName": "GE"}},
                              {"postalCode": "1200", "name": "Genève", "canton": {"shortName": "XX"}}])
    a = adresses.trouver({"entreprise": "Organisation inconnue", "url": "https://ch-fr.indeed.com/viewjob?jk=1",
                          "lieu": "Genève GE"}, c)
    assert (a["npa"], a["ville"]) == ("1201", "Genève")
    assert adresses.trouver({"entreprise": "X", "lieu": "1009 Pully"}, c)["npa"] == "1009"     # NPA déjà dans l'annonce
    assert (lambda a: (a["npa"], a["ville"]))(adresses.trouver({"entreprise": "X", "lieu": "Geneva GE"}, c)) == ("1201", "Genève")
    assert adresses.trouver({"entreprise": "X", "lieu": "Nulle-Part"}, c) is None


def test_preuve_jobroom_sans_rue_garde_npa_et_lieu():
    f = {"postulee_le": "2026-11-03", "entreprise": "ValJob", "titre": "Chef de projet", "lieu": "Blonay VD",
         "extra": {"adresse_employeur": {"rue": "", "numero": "", "npa": "1807", "ville": "Blonay", "origine": "lieu de travail"}}}
    v = valeurs_jobroom(f, "")
    assert v["npa"] == "1807" and v["ville"] == "Blonay" and "rue" not in v


def test_adresse_de_l_agence_sur_la_page_de_postulation(monkeypatch):
    lues = []
    page = ("BLONAY - SAINT-LÉGIER|AGENCE: VALJOB PERMANENT\nValJob Permanent\nperm@valjob.ch\n"
            "ROUTE INDUSTRIELLE 2 1806, ST-LÉGIER-LA CHIÉSAZ\nMap\nRoute Industrielle 2 1806, St-Légier-La Chiésaz\n")
    monkeypatch.setattr(adresses, "_texte_rendu", lambda url: lues.append(url) or page)
    c = web_simule({"company": {"name": "ValJob"}, "location": {"postalCode": "1807", "city": "Blonay", "countryIsoCode": "CH"}})
    f = {"entreprise": "ValJob", "url": URL_JR, "lieu": "Blonay VD",
         "url_postuler": "https://www.valjob.digital/job/chef-de-projet-6120/S92/apply"}
    a = adresses.trouver(f, c)
    assert (a["rue"], a["numero"], a["npa"], a["ville"]) == ("Route Industrielle", "2", "1806", "St-Légier-La Chiésaz")
    assert a["origine"] == "page de l'annonce (valjob.digital)" and lues == [f["url_postuler"]]   # ni Job-Room ni LinkedIn


def test_jamais_linkedin_dans_le_navigateur(monkeypatch):
    lues = []
    monkeypatch.setattr(adresses, "_texte_rendu", lambda url: lues.append(url) or "")
    assert adresses.sur_les_pages({"url": "https://ch.linkedin.com/jobs/view/1", "url_postuler": ""}) is None and lues == []
