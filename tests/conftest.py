import json
from pathlib import Path

import httpx
import pytest

from jobsearch.db import Registre

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(nom):
    return json.loads((FIXTURES / nom).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def configuration_de_test(monkeypatch):
    """Les tests tournent sur une installation d'exemple (fixtures/config, valeurs fictives),
    pas sur le config/ livré, qui est neutre (voir test_installation)."""
    from jobsearch import config, profil
    monkeypatch.setattr(config, "CONFIG", FIXTURES / "config")
    profil.oublier()
    yield
    profil.oublier()


@pytest.fixture(autouse=True)
def jamais_de_vraie_notification(monkeypatch, tmp_path):
    """Aucun test n'envoie de notification à Camille ni n'écrit dans ses journaux."""
    from jobsearch import agent_claude
    monkeypatch.setattr("jobsearch.notify.notifier", lambda *a, **k: [])
    monkeypatch.setattr("jobsearch.notify.alerte_action", lambda *a, **k: True)
    monkeypatch.setattr(agent_claude, "DATA", tmp_path)


@pytest.fixture(autouse=True)
def jamais_de_vraie_session_claude(monkeypatch, tmp_path):
    """Aucun test ne lance de session dans la machine virtuelle : le lanceur est un faux qui
    garde les appels (agent_claude.LANCEES), et le dossier partagé est un dossier de test."""
    from jobsearch import agent_claude
    lancees = []
    monkeypatch.setattr(agent_claude, "LANCEES", lancees, raising=False)
    monkeypatch.setattr(agent_claude, "lancer_dans_la_vm", lambda tache, type_, consigne: lancees.append((tache, type_, consigne)))
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path / "donnees")


@pytest.fixture(autouse=True)
def jamais_le_vrai_moteur(monkeypatch):
    """Aucun test ne consomme l'abonnement Claude de Camille : `claude -p` n'est jamais lancé,
    le moteur paraît absent, et la page Santé voit un Claude Code connecté fictif."""
    from jobsearch import moteur

    def interdit(cmd, **kw):
        raise AssertionError("un test a voulu lancer la vraie commande claude : %s" % cmd[:3])

    def absent(verifier=None):
        raise moteur.MoteurImpossible("Claude Code absent (test)", bloquant=True)
    monkeypatch.setattr(moteur, "EXECUTER", interdit)
    monkeypatch.setattr(moteur, "ouvrir", absent)
    monkeypatch.setattr(moteur, "etat", lambda executer=None: {
        "installe": True, "connecte": True, "abonnement": "pro", "message": "Connecté au compte Claude (abonnement pro)"})


COMMANDES_INTERDITES = ("claude", "limactl", "security", "open", "osascript")


@pytest.fixture(autouse=True)
def jamais_de_commande_systeme(monkeypatch):
    """Aucun test ne lance `claude`, `limactl`, `security` (trousseau), `open`, `osascript` ni Chrome :
    le démarrage d'un processus (`subprocess.Popen`, donc aussi `subprocess.run`) vers l'une de ces
    commandes lève une erreur explicite. Un test qui les simule remplace `subprocess.run` / `Popen`
    lui-même : son faux passe avant cette garde."""
    import os
    import shlex
    import subprocess
    origine = subprocess.Popen.__init__

    def garde(self, args, *a, **kw):
        cmd = args if isinstance(args, (list, tuple)) else (shlex.split(str(args)) if kw.get("shell") else [args])
        nom = os.path.basename(str(cmd[0])).lower() if cmd else ""
        if nom in COMMANDES_INTERDITES or "chrome" in nom:
            raise AssertionError("un test a voulu lancer une vraie commande système (%s) : "
                                 "simuler subprocess dans le test" % nom)
        return origine(self, args, *a, **kw)
    monkeypatch.setattr(subprocess.Popen, "__init__", garde)


@pytest.fixture(autouse=True)
def jamais_le_vrai_dossier_des_donnees(monkeypatch, tmp_path):
    """Ce qui lit config.DATA au moment de l'appel (cache des pages, jeton du travailleur)
    écrit dans un dossier de test, jamais dans data/."""
    monkeypatch.setattr("jobsearch.config.DATA", tmp_path / "donnees")
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path / "donnees")


@pytest.fixture(autouse=True)
def tableau_de_bord_avec_jeton(monkeypatch, tmp_path):
    """Le tableau de bord exige le jeton de toute requête (web/auth.py) : chaque TestClient porte le
    cookie du jeton de test, comme la vue web de l'application. Le jeton est dans un fichier de test fixe
    (un test qui change de dossier de données garde le même). Le client de test (« testclient » /
    « testserver ») compte comme le Mac pour `depuis_le_mac`, qui ne donne aucun accès sans le jeton.
    Un test qui veut un client sans jeton vide `client.cookies`."""
    from starlette.testclient import TestClient
    from jobsearch.web import auth
    monkeypatch.setattr(auth, "chemin_jeton", lambda: tmp_path / "jeton-tableau-de-bord")
    monkeypatch.setattr(auth, "LOCAL", auth.LOCAL | {"testclient"})
    monkeypatch.setattr(auth, "HOTES_LOCAUX", auth.HOTES_LOCAUX | {"testserver"})
    init = TestClient.__init__

    def avec_jeton(self, *a, **kw):
        init(self, *a, **kw)
        self.cookies.set(auth.COOKIE, auth.jeton())
    monkeypatch.setattr(TestClient, "__init__", avec_jeton)


@pytest.fixture(autouse=True)
def jamais_le_vrai_trousseau(monkeypatch):
    """Aucun test ne lit ni n'écrit le trousseau de Camille : il paraît vide."""
    import subprocess
    monkeypatch.setattr("jobsearch.cles._run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 44, stdout="", stderr=""))


@pytest.fixture(autouse=True)
def jamais_le_vrai_registre_du_commerce(monkeypatch):
    """Aucun test n'interroge Zefix : sans client fourni, le registre ne connaît personne."""
    from jobsearch import adresses
    vrai = adresses.zefix
    monkeypatch.setattr(adresses, "zefix", lambda nom, ville="", client=None: vrai(nom, ville, client) if client else None)
    monkeypatch.setattr(adresses, "_texte_rendu", lambda url: "")              # ni Chrome ni réseau
    monkeypatch.setattr(adresses, "_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404))))


@pytest.fixture(autouse=True)
def jamais_les_vrais_entretiens(monkeypatch, tmp_path):
    """Aucun test ne lit les fiches d'entretien de Camille (data/entretiens)."""
    monkeypatch.setattr("jobsearch.entretiens.DATA", tmp_path)


@pytest.fixture(autouse=True)
def profil_de_test(monkeypatch, tmp_path):
    """Aucun test ne lit ni n'écrit le vrai profil : une copie de fixtures/profil.yaml."""
    import shutil
    from jobsearch import profil
    chemin = tmp_path / "profil.yaml"
    shutil.copy(FIXTURES / "profil.yaml", chemin)
    monkeypatch.setenv("JOBSEARCH_PROFIL", str(chemin))
    profil.oublier()
    yield chemin
    profil.oublier()


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr("jobsearch.pipeline.DATA", tmp_path)
    r = Registre(tmp_path / "test.db")
    yield r
    r.fermer()


def annonce(reg, **champs):
    from jobsearch.fingerprint import empreinte
    f = {"entreprise": "Exemple SA", "titre": "Product Manager", "statut": "nouvelle",
         "sources": ["jobup"], "ids_source": [], "vue_le": "2026-10-24", "texte": ""}
    f.update(champs)
    f.setdefault("empreinte", empreinte(f["entreprise"], f["titre"]))
    reg.enregistrer(f)
    reg.commit()
    return f["empreinte"]


@pytest.fixture
def jobroom_en_service(monkeypatch):
    """Les preuves Job-Room sont coupées dans la version 1 (profil.HORS_VERSION) ; leur code reste et
    ses tests le font tourner avec l'option remise en service."""
    from jobsearch import profil
    monkeypatch.setattr(profil, "HORS_VERSION", set())
