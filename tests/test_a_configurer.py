"""Une source qui attend un réglage de la personne (connecteur Indeed pas activé sur claude.ai) :
pas un échec du passage, une seule notification, un bandeau qui dit quoi faire, et la source
reprend d'elle-même une fois le réglage fait."""

import httpx
import pytest
from fastapi.testclient import TestClient

from jobsearch import connecteurs, pipeline
from jobsearch.sources.base import Annonce, ConfigurationRequise, RouteCassee
from jobsearch.travailleur import client as cl
from jobsearch.travailleur import serveur
from test_pipeline_web import FausseSource

MESSAGE = "le connecteur Indeed n'est pas activé sur ton compte Claude (claude.ai › Réglages › Connecteurs)"


class IndeedAbsent:
    nom = "indeed"
    detail_en_session = True

    def __init__(self, absent=True):
        self.absent = absent

    def tester(self):
        return 1

    def offres(self, a_lire=None):
        if self.absent:
            raise ConfigurationRequise(MESSAGE, connecteur="indeed")
        return [Annonce("indeed", "x1", "Exemple SA", "AI Product Manager", texte="Texte.", detail_charge=True)]

    def detail(self, a):
        return a


@pytest.fixture
def notes(monkeypatch):
    from jobsearch import courrier, moteur
    envoyees = []
    monkeypatch.setattr(pipeline, "notifier", lambda *a, **k: envoyees.append((a, k)))
    monkeypatch.setattr(courrier, "lire_boite", lambda *a, **k: [])
    monkeypatch.setattr(moteur, "ouvrir", lambda: (_ for _ in ()).throw(moteur.MoteurImpossible("test", bloquant=True)))
    return envoyees


def _passage(reg, indeed):
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Exemple SA", "AI Product Manager")])
    return pipeline.Passage(reg, sources={"job-room": jr, "indeed": indeed}).lancer()


def _etat(reg):
    return reg.cx.execute("SELECT etat FROM sources WHERE id='indeed'").fetchone()[0]


def test_connecteur_absent_ne_rend_pas_le_passage_partiel(reg, notes):
    r = _passage(reg, IndeedAbsent())
    assert r["statut"] == "complet", r
    assert "⚠" not in r["resume"] and "À configurer : Indeed" in r["resume"]
    assert _etat(reg) == "à configurer"
    a_brancher = [a for a, k in notes if "à brancher" in a[0]]
    assert len(a_brancher) == 1 and notes[-1][1]["lien"] == "/connecteurs"


def test_prevenu_une_seule_fois_puis_reprise_toute_seule(reg, notes):
    _passage(reg, IndeedAbsent())
    _passage(reg, IndeedAbsent())
    assert len([a for a, k in notes if "à brancher" in a[0]]) == 1      # pas à chaque passage
    r = _passage(reg, IndeedAbsent(absent=False))                        # la personne a activé Indeed
    assert r["statut"] == "complet" and "À configurer" not in r["resume"]
    assert _etat(reg) == "en service"


def test_une_vraie_panne_reste_un_echec(reg, notes):
    class Panne(IndeedAbsent):
        def offres(self, a_lire=None):
            raise RouteCassee("indeed : la session Claude s'est arrêtée")
    r = _passage(reg, Panne())
    assert r["statut"] == "partiel" and _etat(reg) == "en panne"


def test_le_travailleur_transmet_l_etat_a_configurer():
    corps = serveur._refus(ConfigurationRequise(MESSAGE, connecteur="indeed")).body
    reponse = lambda req: httpx.Response(422, content=corps, headers={"content-type": "application/json"})
    d = cl.Distant("http://vm", "jeton", http=httpx.Client(transport=httpx.MockTransport(reponse)))
    with pytest.raises(ConfigurationRequise, match="pas activé") as e:
        cl.SourceDistante(d, "indeed", IndeedAbsent()).offres()
    assert e.value.connecteur == "indeed"
    simple = lambda req: httpx.Response(422, json={"erreur": "indeed : panne"})
    d = cl.Distant("http://vm", "jeton", http=httpx.Client(transport=httpx.MockTransport(simple)))
    with pytest.raises(RouteCassee) as e:
        cl.SourceDistante(d, "indeed", IndeedAbsent()).offres()
    assert not isinstance(e.value, ConfigurationRequise)


@pytest.fixture
def web(reg, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    monkeypatch.setattr(connecteurs, "actifs", lambda p=None: {"indeed", "apple-mail"})
    return TestClient(appmod.app)


def test_bandeau_et_carte_disent_quoi_faire(reg, notes, web, monkeypatch):
    _passage(reg, IndeedAbsent())
    accueil = web.get("/").text
    assert "Indeed n'est pas branché" in accueil and 'href="/connecteurs#indeed"' in accueil
    page = web.get("/connecteurs").text
    assert "à configurer" in page and "Pas encore branché" in page and "activer Indeed" in page
    # Un test réussi lève l'état sans attendre le passage suivant.
    monkeypatch.setattr(connecteurs, "tester", lambda nom: (True, "Indeed connecté au compte Claude"))
    web.post("/connecteurs/indeed/tester", headers={"origin": "http://testserver"})
    assert _etat(reg) == "en service" and "n'est pas branché" not in web.get("/").text


def test_pas_de_bandeau_si_indeed_desactive(reg, notes, web, monkeypatch):
    _passage(reg, IndeedAbsent())
    monkeypatch.setattr(connecteurs, "actifs", lambda p=None: {"apple-mail"})
    assert "n'est pas branché" not in web.get("/").text
