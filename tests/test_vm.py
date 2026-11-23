"""La machine virtuelle, sans la lancer : configuration Lima et commandes passées."""

import json
import subprocess

import pytest

from jobsearch import vm


@pytest.fixture
def lima(monkeypatch, tmp_path):
    """Lima simulé : garde les commandes, tient l'état de l'instance."""
    etat = {"instance": None}
    commandes = []

    def executer(cmd, **kw):
        commandes.append(cmd[1:])
        if cmd[1] == "list":
            out = json.dumps(etat["instance"]) if etat["instance"] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if cmd[1] == "create":
            etat["instance"] = {"name": "jobsearch", "status": "Stopped"}
        if cmd[1] == "start":
            etat["instance"]["status"] = "Running"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(vm, "EXECUTER", executer)
    monkeypatch.setattr(vm, "limactl", lambda: "/opt/homebrew/bin/limactl")
    monkeypatch.setattr(vm, "DATA", tmp_path)
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path)
    return etat, commandes


def test_la_vm_ne_voit_que_le_dossier_partage(tmp_path, monkeypatch):
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path)
    c = vm.configuration()
    assert c["vmType"] == "vz" and c["mounts"] == [
        {"location": str(tmp_path / "partage-vm"), "mountPoint": "/donnees", "writable": True}]
    assert (tmp_path / "partage-vm").stat().st_mode & 0o777 == 0o700
    assert c["portForwards"][0] == {"guestPort": 8776, "hostIP": "127.0.0.1", "hostPort": 8776}
    assert c["portForwards"][1] == {"guestPort": 8777, "hostIP": "127.0.0.1", "hostPort": 8777}   # l'écran
    assert c["portForwards"][2]["ignore"] is True and c["portForwards"][3]["ignore"] is True   # aucun autre port
    assert "claude.ai/install.sh" in c["provision"][1]["script"] and "astral.sh/uv" in c["provision"][1]["script"]
    systeme = c["provision"][0]["script"]
    assert "dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb" in systeme
    assert "sha256sum -c" in systeme and vm.MCP_CHROME in systeme


def test_lancer_ecran_et_chrome_sur_la_seule_boucle_locale():
    s = vm.LANCER % {"donnees": "/donnees", "code": "c", "port": 8776, "ecran": 8777, "vnc": 5901, "devtools": 9222,
                     "politique": __import__("json").dumps(vm.POLITIQUE_CHROME)}
    assert '"PasswordManagerEnabled": false' in s and '"BrowserSignin": 0' in s
    assert "-localhost yes" in s and "-rfbauth" in s                  # VNC : boucle locale de la VM, mot de passe
    assert "--remote-debugging-address=127.0.0.1" in s               # DevTools jamais exposé
    assert "JOBSEARCH_VM=1" in s


def test_lima_embarque_et_rangé_dans_les_donnees(monkeypatch, tmp_path):
    exe = tmp_path / "limactl"
    exe.write_text("")
    monkeypatch.setenv("JOBSEARCH_LIMACTL", str(exe))
    monkeypatch.setattr(vm, "DATA", tmp_path)
    assert vm.limactl() == str(exe) and vm.environnement_lima()["LIMA_HOME"] == str(tmp_path / "lima")


def test_mot_de_passe_de_l_ecran(monkeypatch, tmp_path):
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path)
    m = vm.ecran_motdepasse()
    f = tmp_path / "partage-vm" / "ecran.motdepasse"
    assert len(m) == 8 and f.stat().st_mode & 0o777 == 0o600 and vm.ecran_motdepasse() == m
    assert vm.adresse_ecran().startswith("http://127.0.0.1:8777/vnc.html?") and "password=" + m in vm.adresse_ecran()


def test_demarrer_cree_installe_et_lance(lima, monkeypatch, tmp_path):
    etat, commandes = lima
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1", "moteur": {"connecte": False}})
    assert vm.demarrer(attente_s=1)["version"] == "1"
    verbes = [c[0] for c in commandes]
    assert verbes.index("create") < verbes.index("start") < verbes.index("copy")
    assert (tmp_path / "partage-vm" / "travailleur.jeton").exists() and (tmp_path / "vm" / "jobsearch.yaml").exists()
    scripts = " ".join(c[-1] for c in commandes if c[0] == "shell")
    assert "uv pip install" in scripts and "jobsearch\" travailleur" in scripts and "JOBSEARCH_DATA=/donnees" in scripts
    copie = next(c for c in commandes if c[0] == "copy")
    assert copie[-1] == "jobsearch:code-jobsearch"


def test_copie_du_code_sans_donnees(tmp_path):
    vm.copie_du_code(tmp_path)
    assert (tmp_path / "pyproject.toml").exists() and (tmp_path / "src" / "jobsearch" / "vm.py").exists()
    assert (tmp_path / "config" / "settings.yaml").exists()
    assert not (tmp_path / "data").exists() and not list(tmp_path.rglob("__pycache__"))


def test_connexion_exige_une_vm_demarree(lima):
    with pytest.raises(vm.VMImpossible, match="démarrer d'abord"):
        vm.connexion()


def test_deconnexion_dans_la_vm(lima):
    etat, commandes = lima
    with pytest.raises(vm.VMImpossible, match="arrêtée"):
        vm.deconnexion()
    etat["instance"] = {"name": "jobsearch", "status": "Running"}
    assert "déconnecté" in vm.deconnexion()
    assert any(c[0] == "shell" and "claude auth logout" in c[-1] for c in commandes)
