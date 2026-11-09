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


def test_la_vm_ne_voit_que_les_donnees(tmp_path):
    c = vm.configuration(tmp_path / "donnees")
    assert c["vmType"] == "vz" and c["mounts"] == [
        {"location": str(tmp_path / "donnees"), "mountPoint": "/donnees", "writable": True}]
    assert c["portForwards"][0] == {"guestPort": 8766, "hostIP": "127.0.0.1", "hostPort": 8766}
    assert c["portForwards"][1]["ignore"] is True                     # aucun autre port vers le Mac
    assert "claude.ai/install.sh" in c["provision"][1]["script"] and "astral.sh/uv" in c["provision"][1]["script"]


def test_demarrer_cree_installe_et_lance(lima, monkeypatch, tmp_path):
    etat, commandes = lima
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1", "moteur": {"connecte": False}})
    assert vm.demarrer(attente_s=1)["version"] == "1"
    verbes = [c[0] for c in commandes]
    assert verbes.index("create") < verbes.index("start") < verbes.index("copy")
    assert (tmp_path / "travailleur.jeton").exists() and (tmp_path / "vm" / "jobsearch.yaml").exists()
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
