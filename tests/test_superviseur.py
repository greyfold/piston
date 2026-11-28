"""Le superviseur : ce que l'enveloppe native lançait elle-même, testé sans processus ni réseau réels."""

import io
import json
import threading
import time
from pathlib import Path

import httpx

from jobsearch import superviseur as sup


class FauxProcessus:
    def __init__(self, args, sortie="", code=0, vivant=False):
        self.args, self.pid, self.returncode = args, 4000 + len(args), None
        self.stdout = io.StringIO(sortie) if sortie is not None else None
        self._code, self._fini = code, threading.Event()
        self.termine = False
        if not vivant:
            self._finir(code)

    def _finir(self, code):
        self.returncode = code
        self._fini.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._fini.wait(timeout if timeout is not None else 5):
            import subprocess
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def terminate(self):
        self.termine = True
        self._finir(-15)

    def kill(self):
        self._finir(-9)


class Banc:
    """Un superviseur avec de faux processus et un faux tableau de bord."""

    def __init__(self, tmp_path, etat=None, sorties=None, vivants=("web",)):
        self.lances, self.evenements = [], []
        self.sur_le_port = None            # ce que /api/etat rend : None (personne), dict, ou un code HTTP
        self.etat = etat or {}
        self.sorties = sorties or {}
        self.vivants = vivants
        (tmp_path / "jeton").write_text("jeton-test", encoding="utf-8")
        self.s = sup.Superviseur(port=8775, donnees=tmp_path, ecrire=self.recevoir, lancer=self.lancer,
                                 http=httpx.Client(transport=httpx.MockTransport(self.repondre)),
                                 dormir=lambda s: None)
        self.donnees = tmp_path

    def recevoir(self, ligne):
        self.evenements.append(json.loads(ligne))

    def types(self):
        return [e["evenement"] for e in self.evenements]

    def lancer(self, args, **kw):
        p = FauxProcessus(args, self.sorties.get(args[0], ""), vivant=args[0] in self.vivants)
        self.lances.append(p)
        if args[0] == "web":                 # le tableau de bord lancé répond aussitôt
            self.sur_le_port = dict(self.etat_de_cette_installation())
        return p

    def etat_de_cette_installation(self):
        return dict({"donnees": str(self.donnees), "racine": str(sup._racine_paquet()), "pid": 99,
                     "mode": "local", "passage": {"niveau": "ok", "texte": "ok"}}, **self.etat)

    def repondre(self, req):
        if req.url.path == "/api/entree":
            return httpx.Response(200, json={"code": "c0de"})
        if self.sur_le_port is None:
            raise httpx.ConnectError("refusé")
        if isinstance(self.sur_le_port, int):
            return httpx.Response(self.sur_le_port)
        assert req.headers.get("x-jobsearch-jeton") == "jeton-test"
        return httpx.Response(200, json=self.sur_le_port)


def attendre(cond, s=3):
    fin = time.monotonic() + s
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_port_libre_le_tableau_est_lance_puis_pret(tmp_path):
    b = Banc(tmp_path)
    assert b.s.assurer_tableau()
    assert [p.args[:1] for p in b.lances] == [["web"]] and b.lances[0].args[1:] == ["--hote", "127.0.0.1", "--port", "8775"]
    e = b.evenements[-1]
    assert e["evenement"] == "tableau" and e["pret"] and e["adresse"] == "http://localhost:8775/"
    assert e["entree"] == "http://localhost:8775/entree?code=c0de&suivant=%2F%3Fouverture%3D1"


def test_tableau_de_cette_installation_adopte(tmp_path):
    b = Banc(tmp_path)
    b.sur_le_port = b.etat_de_cette_installation()
    assert b.s.assurer_tableau() and b.lances == [] and b.s.web is None


def test_autre_installation_refusee(tmp_path):
    b = Banc(tmp_path)
    b.sur_le_port = dict(b.etat_de_cette_installation(), donnees="/ailleurs")
    assert not b.s.assurer_tableau() and b.lances == []
    assert "autre installation (/ailleurs)" in b.evenements[-1]["erreur"]
    b.sur_le_port = 401
    assert not b.s.assurer_tableau() and "refuse le jeton" in b.evenements[-1]["erreur"]
    b.sur_le_port = 500
    assert not b.s.assurer_tableau() and "autre programme" in b.evenements[-1]["erreur"]


def test_ancien_tableau_remplace(tmp_path, monkeypatch):
    b = Banc(tmp_path)
    b.sur_le_port = dict(b.etat_de_cette_installation(), racine="/ancienne/copie/jobsearch", pid=1234)
    arretes = []

    def terminer(pid):
        arretes.append(pid)
        b.sur_le_port = None
        return True
    monkeypatch.setattr(b.s, "terminer_ancien", terminer)
    assert b.s.assurer_tableau()
    assert arretes == [1234] and [p.args[0] for p in b.lances] == ["web"]


def test_tableau_tombe_relance(tmp_path):
    b = Banc(tmp_path)
    assert b.s.assurer_tableau()
    premier = b.lances[0]
    b.sur_le_port = None                   # mort : le port se libère
    premier.kill()
    assert attendre(lambda: len(b.lances) == 2)
    assert b.s.relances == 1 and b.s.tableau_pret


def test_demarrage_de_la_vm_relaie_ses_etapes(tmp_path):
    sortie = "étape : Démarrage de la machine virtuelle\nétape : Lancement du travailleur\n{}\n"
    b = Banc(tmp_path, sorties={"vm": sortie})
    fini = threading.Event()
    assert b.s.demarrer_vm(ensuite=fini.set)
    assert fini.wait(3)
    t = b.types()
    assert t[0] == "occupe" and t[1] == "page" and b.evenements[1]["page"] == "demarrage"
    assert [e["texte"] for e in b.evenements if e["evenement"] == "etape"] == [
        "Démarrage de la machine virtuelle", "Lancement du travailleur"]
    assert "recharger_accueil" in t
    assert any(e.get("texte") == "Démarrage de la machine virtuelle : terminé." for e in b.evenements)


def test_une_commande_a_la_fois(tmp_path):
    b = Banc(tmp_path, vivants=("web", "passage"))
    assert b.s.executer(["passage"], "Passage")
    assert not b.s.executer(["vm", "arreter"], "Arrêt")
    assert [p.args[0] for p in b.lances] == ["passage"]


def test_echeances_un_seul_a_la_fois(tmp_path):
    b = Banc(tmp_path, vivants=("web", "echeances"))
    assert b.s.lancer_echeances() and not b.s.lancer_echeances()


def test_quitter_eteint_la_vm_puis_le_tableau(tmp_path):
    b = Banc(tmp_path, etat={"mode": "distant"}, vivants=("web", "echeances"))
    b.s.assurer_tableau()
    b.s.rafraichir()
    b.s.lancer_echeances()
    web, ech = b.lances[0], b.lances[1]
    b.s.quitter()
    assert ech.termine and web.termine
    assert [p.args for p in b.lances][-1] == ["vm", "arreter"]
    arret = next(e for e in b.evenements if e["evenement"] == "arret")
    assert arret["passage_interrompu"] is True and b.types()[-1] == "fin"


def test_quitter_en_local_sans_vm(tmp_path):
    b = Banc(tmp_path)
    b.s.assurer_tableau()
    b.s.rafraichir()
    b.s.quitter()
    assert "arret" not in b.types() and ["vm", "arreter"] not in [p.args for p in b.lances]


def test_fin_de_l_entree_arrete_sans_eteindre_la_vm(tmp_path):
    b = Banc(tmp_path, etat={"mode": "distant"})
    b.sur_le_port = b.etat_de_cette_installation()
    lignes = io.StringIO('{"ordre": "entree", "suivant": "/sante", "id": 7}\npas du json\n')
    b.s.demarrer = lambda: None
    assert b.s.boucle(lignes) == 0
    e = next(e for e in b.evenements if e["evenement"] == "entree")
    assert e["id"] == 7 and e["adresse"].endswith("suivant=%2Fsante")
    assert "arret" not in b.types() and b.types()[-1] == "fin"
    assert "ordre illisible" in (tmp_path / "logs" / "superviseur.log").read_text(encoding="utf-8")


def test_entree_a_usage_unique():
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    web = TestClient(appmod.app)
    code = web.post("/api/entree").json()["code"]
    neuf = TestClient(appmod.app)
    neuf.cookies.clear()
    assert neuf.get("/api/etat").status_code == 401
    r = neuf.get("/entree", params={"code": code, "suivant": "/sante"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/sante"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    autre = TestClient(appmod.app)
    autre.cookies.clear()
    r = autre.get("/entree", params={"code": code}, follow_redirects=False)       # déjà servi
    assert r.headers["location"] == "/connexion"
    r = autre.get("/entree", params={"code": "invente"}, follow_redirects=False)
    assert r.headers["location"] == "/connexion"
    sans_jeton = TestClient(appmod.app)
    sans_jeton.cookies.clear()
    assert sans_jeton.post("/api/entree").status_code == 401                       # le code exige le jeton


def test_entree_ne_mene_qu_au_site():
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    web = TestClient(appmod.app)
    code = web.post("/api/entree").json()["code"]
    r = web.get("/entree", params={"code": code, "suivant": "//ailleurs.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_commande_superviseur_dans_la_cli():
    from jobsearch import cli
    assert Path(cli.__file__).read_text(encoding="utf-8").count('"superviseur"') == 1
