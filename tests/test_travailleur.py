"""Le travailleur (machine virtuelle) et le client du Mac, sans VM : l'application en direct."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from conftest import annonce
from jobsearch import config, moteur
from jobsearch.sources.base import Annonce, RouteCassee
from jobsearch.travailleur import client as cl
from jobsearch.travailleur import jeton, serveur


class Reponse(BaseModel):
    note: int


@pytest.fixture
def jeton_(tmp_path, monkeypatch):
    monkeypatch.setattr(jeton, "DATA", tmp_path)
    return jeton.lire()


@pytest.fixture
def distant(jeton_):
    return cl.Distant("http://testserver", jeton_, http=TestClient(serveur.app))


def test_jeton_du_seul_compte_et_exige(jeton_, tmp_path):
    assert (tmp_path / "partage-vm" / "travailleur.jeton").stat().st_mode & 0o777 == 0o600 and jeton.lire() == jeton_
    web = TestClient(serveur.app)
    assert web.get("/etat").status_code == 401
    assert web.get("/etat", headers={"Authorization": "Bearer faux"}).status_code == 401
    e = web.get("/etat", headers={"Authorization": "Bearer " + jeton_}).json()
    assert e["moteur"]["connecte"] and e["sources"] == ["indeed"]
    from jobsearch import vm
    assert e["code"] == vm.empreinte_code()          # le Mac reconnaît un travailleur sur un code périmé


def test_tache_executee_par_le_travailleur(distant, monkeypatch):
    vues = []

    def executer(t, message):
        vues.append((t, message))
        return moteur.Resultat(texte='{"note": 82}', brut={"note": 82}, usage={"input_tokens": 9}, cout_usd=0.02)
    monkeypatch.setattr(moteur, "executer", executer)
    t = moteur.Tache("essai", "Consigne.", contexte="Profil.", schema=Reponse, effort="low")
    r = distant.executer(t, "Annonce.")
    assert r.objet == Reponse(note=82) and r.usage == {"input_tokens": 9} and r.cout_usd == 0.02
    recue, message = vues[0]
    assert message == "Annonce." and recue.schema is None and recue.schema_json == Reponse.model_json_schema()
    assert (recue.consigne, recue.contexte, recue.effort) == ("Consigne.", "Profil.", "low")


def test_erreur_du_moteur_traverse(distant, monkeypatch):
    def refus(t, m):
        raise moteur.MoteurImpossible("Claude Code n'est pas connecté", bloquant=True)
    monkeypatch.setattr(moteur, "executer", refus)
    with pytest.raises(moteur.MoteurImpossible, match="pas connecté") as e:
        distant.executer(moteur.Tache("essai", "C."), "x")
    assert e.value.bloquant


def test_travailleur_eteint_bloque(jeton_):
    import httpx
    def panne(req):
        raise httpx.ConnectError("refusé")
    d = cl.Distant("http://127.0.0.1:1", jeton_, http=httpx.Client(transport=httpx.MockTransport(panne)))
    with pytest.raises(moteur.MoteurImpossible, match="machine virtuelle est-elle démarrée") as e:
        d.executer(moteur.Tache("essai", "C."), "x")
    assert e.value.bloquant


def test_verifie_la_connexion_dans_la_vm(distant, monkeypatch):
    pas = {"installe": True, "connecte": False, "message": "pas connecté"}
    monkeypatch.setattr(moteur, "etat", lambda executer=None, fournisseur_=None: pas)
    monkeypatch.setattr(moteur, "etats", lambda executer=None: {f: pas for f in ("claude", "codex", "gemini", "grok")})
    with pytest.raises(moteur.MoteurImpossible, match="dans la machine virtuelle"):
        distant.verifie()


class FausseIndeed:
    nom = "indeed"
    detail_en_session = True
    r = {"duree_max_s": 10}

    def __init__(self):
        self.lues = []

    def tester(self):
        return 3

    def offres(self, a_lire=None):
        out = [Annonce("indeed", "1", "Exemple SA", "AI Product Manager"),
               Annonce("indeed", "2", "Connue SA", "Head of Product"),
               Annonce("indeed", "3", "Autre SA", "Comptable")]
        for a in out:
            if a_lire is None or a_lire(a):
                a.texte, a.detail_charge = "Texte complet.", True
                self.lues.append(a.id_source)
        return out


def test_source_confiee_ne_lit_que_les_nouvelles(distant, monkeypatch, reg):
    from jobsearch.fingerprint import empreinte
    src = FausseIndeed()
    monkeypatch.setattr(serveur, "_sources", lambda: {"indeed": src})
    annonce(reg, entreprise="Connue SA", titre="Head of Product")
    s = distant.source("indeed", src, lambda: cl.connues(reg, "indeed"))
    assert s.detail_en_session and s.tester() == 3
    res = s.offres(a_lire=lambda a: True)
    assert [a.id_source for a in res] == ["1", "2", "3"] and isinstance(res[0], Annonce)
    assert src.lues == ["1"]                     # « Connue SA » est au registre, « Comptable » écarté sur l'intitulé
    assert res[0].texte == "Texte complet." and not res[1].detail_charge
    assert empreinte("Connue SA", "Head of Product") in cl.connues(reg, "indeed")["empreintes"]


def test_parametres_de_recherche_envoyes_au_travailleur(distant, monkeypatch):
    """La VM n'a pas le profil : le Mac envoie les paramètres de recherche avec la demande."""
    recus = []

    class AvecParametres(FausseIndeed):
        def parametres(self):
            return {"requetes": ["Product Owner"], "cantons": ["VD"], "lieux": ["Lausanne"]}

        def selon(self, p):
            recus.append(p)
            return self
    monkeypatch.setattr(serveur, "_sources", lambda: {"indeed": AvecParametres()})
    distant.source("indeed", AvecParametres()).offres()
    assert recus == [{"requetes": ["Product Owner"], "cantons": ["VD"], "lieux": ["Lausanne"]}]


def test_source_non_confiee_et_panne(distant, monkeypatch):
    monkeypatch.setattr(serveur, "_sources", lambda: {})
    with pytest.raises(RouteCassee, match="404|non confiée"):
        distant.source("jobup", object()).tester()

    class Cassee(FausseIndeed):
        def offres(self, a_lire=None):
            raise RouteCassee("indeed : connecteur à réautoriser")
    monkeypatch.setattr(serveur, "_sources", lambda: {"indeed": Cassee()})
    with pytest.raises(RouteCassee, match="réautoriser"):
        distant.source("indeed", Cassee()).offres()


def test_passage_en_mode_distant_confie_indeed(reg, monkeypatch, jeton_):
    from jobsearch import pipeline
    from jobsearch.travailleur.client import SourceDistante
    monkeypatch.setattr(cl, "reglage", lambda: {"mode": "distant", "sources": ["indeed"]})
    monkeypatch.setattr("jobsearch.sources.catalogue.construire",
                        lambda client=None, reg=None: {"jobup": object(), "indeed": FausseIndeed()})
    s = pipeline.Passage(reg).sources()
    assert isinstance(s["indeed"], SourceDistante) and not isinstance(s["jobup"], SourceDistante)
    assert s["indeed"].detail_en_session


def test_dossier_des_donnees(tmp_path):
    assert config.dossier_donnees({"JOBSEARCH_DATA": "/x/y"}, tmp_path) == config.Path("/x/y")
    (tmp_path / "data").mkdir()
    assert config.dossier_donnees({}, tmp_path) == tmp_path / "data"          # installation de développement
    vide = tmp_path / "app"
    vide.mkdir()
    assert config.dossier_donnees({}, vide) == config.APPLICATION            # application installée


def test_le_travailleur_repond_pendant_une_tache(jeton_, monkeypatch):
    """Une tâche du moteur (minutes avec un moteur lent) ne bloque pas le travailleur : /etat répond
    pendant qu'elle tourne. Sinon l'application le croit arrêté et tente de le relancer."""
    import threading
    en_cours, liberer, fini = threading.Event(), threading.Event(), threading.Event()

    def executer(t, message):
        en_cours.set()
        liberer.wait(3)
        fini.set()
        return moteur.Resultat(texte="ok")
    monkeypatch.setattr(moteur, "executer", executer)
    entetes = {"Authorization": "Bearer " + jeton_}
    t = moteur.Tache("essai", "Consigne.", effort="low")
    with TestClient(serveur.app) as web:
        fil = threading.Thread(target=lambda: web.post("/moteur", headers=entetes,
                                                       json={"tache": t.vers_dict(), "message": "x"}))
        fil.start()
        assert en_cours.wait(5)
        try:
            assert web.get("/etat", headers=entetes).status_code == 200
            assert not fini.is_set()                     # /etat a répondu pendant la tâche, pas après
        finally:
            liberer.set()
            fil.join(10)


def test_chrome_rendre_refuse_les_adresses_internes(jeton_):
    web = TestClient(serveur.app)
    r = web.post("/chrome/rendre", headers={"Authorization": "Bearer " + jeton_},
                 json={"url": "http://127.0.0.1:9222/json"})
    assert r.status_code == 422


def test_etat_ne_duplique_pas_l_appel_moteur(jeton_, monkeypatch):
    appels = []
    ok = {"installe": True, "connecte": True, "abonnement": "pro", "message": "ok"}
    monkeypatch.setattr(moteur, "etats", lambda executer=None: appels.append("etats") or {"claude": ok})
    monkeypatch.setattr(moteur, "etat", lambda *a, **k: appels.append("etat") or ok)
    e = TestClient(serveur.app).get("/etat", headers={"Authorization": "Bearer " + jeton_}).json()
    assert e["moteur"] == ok and appels == ["etats"]
