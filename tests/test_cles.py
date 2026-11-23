import re
import subprocess

import httpx
import pytest

from jobsearch import cles

CLE = "apify_api_" + "x" * 40 + "abcd"


class FauxTrousseau:
    """Le trousseau en mémoire, par la même ligne de commande que `security`."""

    def __init__(self, **entrees):
        self.entrees = [(s, v) for s, v in entrees.items()]
        self.commandes = []

    def __call__(self, cmd, input=None, **kw):
        self.commandes.append((cmd, input))
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        non = subprocess.CompletedProcess(cmd, 44, stdout="", stderr="introuvable")
        if cmd[1] == "find-generic-password":
            v = next((v for s, v in self.entrees if s == cmd[3]), None)
            return ok(v + "\n") if v else non
        if cmd[1] == "delete-generic-password":
            i = next((i for i, (s, _) in enumerate(self.entrees) if s == cmd[3]), None)
            if i is None:
                return non
            del self.entrees[i]
            return ok()
        if cmd[1] == "-i":
            m = re.fullmatch(r'add-generic-password -U -a (\S+) -s (\S+) -w "([^"]+)"\n', input)
            self.entrees.append((m.group(2), m.group(3)))
            return ok()
        return non


def service(code):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, json={})))


def test_enregistrer_remplace_les_anciennes_entrees():
    t = FauxTrousseau(**{"jobsearch-apify": "apify_api_ancienne-cle-creee-a-la-main"})
    msg = cles.enregistrer("apify", "  %s  " % CLE, executer=t, client=service(200))
    assert "…abcd" in msg and "acceptée" in msg
    assert t.entrees == [("jobsearch-apify", CLE)]
    # La clé passe par l'entrée standard, jamais par les arguments de la commande.
    assert all(CLE not in " ".join(cmd) for cmd, _ in t.commandes)
    assert cles.etat("apify", t) == dict(cles.SERVICES["apify"], id="apify", presente=True, fin="abcd")


@pytest.mark.parametrize("valeur, motif", [
    ("sk-ant-api03-" + "x" * 30, "commence par"),
    ("apify_api_" + "x" * 30 + '" ; rm -rf ~', "caractères"),
    ("apify_api_court", "caractères"),
])
def test_forme_refusee(valeur, motif):
    t = FauxTrousseau()
    with pytest.raises(cles.CleRefusee, match=motif):
        cles.enregistrer("apify", valeur, executer=t, client=service(200))
    assert t.entrees == []


def test_cle_refusee_par_le_service_pas_rangee():
    t = FauxTrousseau(**{"jobsearch-apify": "apify_api_" + "a" * 30})
    with pytest.raises(cles.CleRefusee, match="refusée"):
        cles.enregistrer("apify", "apify_api_" + "b" * 30, executer=t, client=service(401))
    assert t.entrees == [("jobsearch-apify", "apify_api_" + "a" * 30)]      # l'ancienne reste


def test_service_injoignable_cle_rangee_quand_meme():
    def panne(req):
        raise httpx.ConnectError("hors ligne")
    t = FauxTrousseau()
    msg = cles.enregistrer("apify", "apify_api_" + "b" * 30, executer=t,
                           client=httpx.Client(transport=httpx.MockTransport(panne)))
    assert "injoignable" in msg and t.entrees


def test_retirer():
    t = FauxTrousseau(**{"jobsearch-apify": "apify_api_" + "a" * 30})
    cles.retirer("apify", t)
    assert t.entrees == [] and cles.lire("apify", t) is None


def test_page_sante_et_saisie_sur_le_mac(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    vues = []
    monkeypatch.setattr(cles, "enregistrer", lambda nom, cle: vues.append((nom, cle)) or "Clé enregistrée.")
    web = TestClient(appmod.app)
    page = web.get("/sante").text
    assert "Clés d'accès" in page and 'type="password"' in page and "absente" in page
    assert "API Claude" not in page and "sk-ant-" not in page
    ia = web.get("/ia").text
    assert "Fournisseur d'IA" in ia and "Connecté au compte Claude" in ia and "Clés d'accès" not in ia
    r = web.post("/cles/apify/enregistrer", data={"cle": CLE}, follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"] and vues == [("apify", CLE)]
    assert CLE not in r.headers["location"]


def test_saisie_refusee_depuis_le_telephone(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    monkeypatch.setattr(appmod, "depuis_le_mac", lambda req: False)
    monkeypatch.setattr(appmod, "autorise", lambda req: True)
    vues = []
    monkeypatch.setattr(cles, "enregistrer", lambda nom, cle: vues.append(cle))
    web = TestClient(appmod.app)
    assert 'type="password"' not in web.get("/sante").text
    r = web.post("/cles/apify/enregistrer", data={"cle": CLE}, follow_redirects=False)
    assert "ok=0" in r.headers["location"] and vues == []


def test_plus_de_cle_api_claude():
    assert "anthropic" not in cles.SERVICES
    with pytest.raises(cles.CleRefusee, match="service inconnu"):
        cles.enregistrer("anthropic", "sk-ant-api03-" + "x" * 40)


def test_ia_deconnexion_et_un_seul_fournisseur(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch import moteur, vm
    from jobsearch.travailleur import client as travailleur
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    web = TestClient(appmod.app)
    ia = web.get("/ia").text
    assert "Se déconnecter" in ia and 'action="/ia/claude/deconnecter"' in ia and 'data-connexion-ia="claude"' not in ia

    vues = []
    monkeypatch.setattr(travailleur, "distant", lambda: True)
    monkeypatch.setattr(vm, "deconnexion", lambda: vues.append("logout") or "ok")
    r = web.post("/ia/claude/deconnecter", follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"] and vues == ["logout"]
    assert web.post("/ia/autre/deconnecter", follow_redirects=False).headers["location"].endswith("ok=0")

    # Un seul fournisseur à la fois : un second ne se connecte pas tant que Claude l'est.
    monkeypatch.setattr(moteur, "FOURNISSEURS", moteur.FOURNISSEURS + [{"id": "autre", "nom": "Autre", "detail": "Test"}])
    monkeypatch.setattr(appmod, "etat_claude", lambda: {"connecte": True, "message": "Connecté"})
    page = web.get("/ia").text
    assert "déconnecte d'abord Claude" in page and 'data-connexion-ia="autre"' not in page
    r = web.post("/ia/connexion/autre/demarrer")
    assert r.status_code == 409 and "un seul fournisseur" in r.json()["message"]
