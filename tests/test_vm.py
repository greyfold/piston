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
        if cmd[1] == "shell" and "poweroff" in cmd or cmd[1:3] == ["stop", "--force"]:
            etat["instance"]["status"] = "Stopped"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(vm, "EXECUTER", executer)
    monkeypatch.setattr(vm, "HYPERVISEUR", "lima")
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
    assert "dl.google.com/linux/direct/google-chrome-stable_current_${ARCH}.deb" in systeme
    assert "dpkg --print-architecture" in systeme and "linux-${NODE_ARCH}.tar.xz" in systeme
    assert "sha256sum -c" in systeme and vm.MCP_CHROME in systeme


def test_lancer_ecran_et_chrome_sur_la_seule_boucle_locale():
    s = vm.LANCER % {"donnees": "/donnees", "code": "c", "port": 8776, "ecran": 8777, "vnc": 5901, "devtools": 9222}
    assert '"PasswordManagerEnabled": false' in vm.POSER_POLITIQUE and '"BrowserSignin": 0' in vm.POSER_POLITIQUE
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
    assert (tmp_path / "pyproject.toml").exists() and (tmp_path / "src" / "jobsearch" / "vm" / "__init__.py").exists()
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


def test_vm_allumee_a_jour_rien_a_refaire(lima, monkeypatch):
    """VM déjà allumée et travailleur sur le code du Mac : ni copie ni relance."""
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Running"}
    monkeypatch.setattr(vm, "ports_a_jour", lambda i: True)
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1", "code": vm.empreinte_code()})
    assert vm.demarrer(attente_s=1)["code"] == vm.empreinte_code()
    assert not [c for c in commandes if c[0] in ("copy", "shell", "start")]


def test_vm_allumee_sur_un_vieux_code_reinstallee(lima, monkeypatch):
    """VM restée allumée pendant une mise à jour : son travailleur tourne sur l'ancien code
    (Indeed y plantait, « list index out of range ») ; demarrer le réinstalle et le relance."""
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Running"}
    monkeypatch.setattr(vm, "ports_a_jour", lambda i: True)
    reponses = iter([{"version": "1", "code": "ancien"}, {"version": "1", "code": vm.empreinte_code()}])
    monkeypatch.setattr(vm, "travailleur_etat", lambda: next(reponses))
    vm.demarrer(attente_s=1)
    verbes = [c[0] for c in commandes]
    assert "copy" in verbes and "start" not in verbes
    assert "jobsearch\" travailleur" in " ".join(c[-1] for c in commandes if c[0] == "shell")


def test_empreinte_du_code_suit_la_copie(tmp_path):
    """Le travailleur calcule l'empreinte sur sa copie : elle doit égaler celle du Mac."""
    vm.copie_du_code(tmp_path)
    assert vm.empreinte_code(tmp_path) == vm.empreinte_code()
    (tmp_path / "src" / "jobsearch" / "vm.py").write_text("# autre\n")
    assert vm.empreinte_code(tmp_path) != vm.empreinte_code()


def test_vm_eteinte_au_code_deja_installe_pas_reinstallee(lima, monkeypatch):
    """L'application éteint la VM en partant : au lancement suivant, la VM redémarre sans recopier
    le code s'il n'a pas changé (l'écran d'attente reste court)."""
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Stopped"}
    monkeypatch.setattr(vm, "ports_a_jour", lambda i: True)
    monkeypatch.setattr(vm, "code_installe", lambda: vm.empreinte_code())
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1", "code": vm.empreinte_code()})
    etapes = []
    vm.demarrer(attente_s=1, annoncer=etapes.append)
    verbes = [c[0] for c in commandes]
    assert "start" in verbes and "copy" not in verbes
    assert "uv pip install" not in " ".join(c[-1] for c in commandes if c[0] == "shell")
    assert etapes[0] == "Démarrage de la machine virtuelle" and etapes[-1] == "Lancement du travailleur"


def test_installation_marquee_a_la_fin(lima):
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Running"}
    vm.installer_code()
    scripts = [c[-1] for c in commandes if c[0] == "shell"]
    i = next(i for i, s in enumerate(scripts) if "uv pip install" in s)
    assert any(vm.MARQUE_CODE in s and vm.empreinte_code() in s for s in scripts[i + 1:])


def test_arreter_a_la_fermeture(lima, monkeypatch):
    """Le système de la VM s'éteint lui-même (`limactl stop` seul ne l'arrête pas) ; puis plus aucun agent."""
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Stopped"}
    assert "déjà arrêtée" in vm.arreter() and not [c for c in commandes if c[0] in ("stop", "shell")]
    etat["instance"]["status"] = "Running"
    tues = []
    monkeypatch.setattr(vm, "_tuer_agents", lambda: tues.append(1))
    assert vm.arreter() == "Machine virtuelle arrêtée." and tues
    assert any(c[0] == "shell" and "poweroff" in c for c in commandes) and not [c for c in commandes if c[0] == "stop"]


def test_arret_force_si_la_vm_ne_s_eteint_pas(lima, monkeypatch):
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Running"}
    sourde = lambda cmd, **kw: (commandes.append(cmd[1:]),
                                 cmd[1:3] == ["stop", "--force"] and etat["instance"].update(status="Stopped"),
                                 subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(etat["instance"]) if cmd[1] == "list" else "",
                                                             stderr=""))[-1]
    monkeypatch.setattr(vm, "EXECUTER", sourde)
    monkeypatch.setattr(vm.time, "sleep", lambda s: None)
    vm._arreter_vm(attente_s=0)
    assert ["stop", "--force", "jobsearch"] in commandes


def test_agents_orphelins_termines(monkeypatch, tmp_path):
    """Des agents Lima restés derrière (le 25.11 : quatre pour une VM) sont terminés, SIGKILL s'il le faut."""
    monkeypatch.setattr(vm, "DATA", tmp_path)
    prise = str(tmp_path / "lima" / vm.nom() / "ha.sock")
    vivants = {101, 102}
    ps = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stderr="", stdout="".join(
        " %d /app/limactl hostagent --socket %s emploi\n" % (p, prise) for p in sorted(vivants))
        + " 200 /opt/homebrew/bin/limactl hostagent --socket /ailleurs/ha.sock autre\n")
    monkeypatch.setattr(vm, "EXECUTER", ps)
    assert vm._agents() == [101, 102]
    signaux = []

    def tuer(pid, sig):
        signaux.append((pid, sig))
        if sig == 9:
            vivants.discard(pid)
    monkeypatch.setattr(vm.os, "kill", tuer)
    monkeypatch.setattr(vm.time, "sleep", lambda s: None)
    vm._tuer_agents()
    assert not vivants and (101, 15) in signaux and (101, 9) in signaux and all(p != 200 for p, _ in signaux)


def test_avancement_du_premier_demarrage(lima, monkeypatch):
    """La première fois, l'avancement suit création, système, application et lancement ; les durées mesurées
    servent d'estimation la fois suivante. Pendant une étape, la barre avance sans annoncer fini."""
    etat, _ = lima
    monkeypatch.setattr(vm, "code_installe", lambda: None)
    monkeypatch.setattr(vm, "installer_code", lambda: None)
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1", "code": vm.empreinte_code()})
    vus = []
    monkeypatch.setattr(vm, "creer", lambda: vus.append(vm.avancement()) or etat.update(
        instance={"name": "jobsearch", "status": "Stopped"}))
    monkeypatch.setattr(vm, "en_demarrage", lambda: True)
    vm.demarrer(attente_s=1)
    a = vus[0]
    assert a["en_cours"] and a["premiere"] and a["detail"].startswith("Téléchargement d'Ubuntu")
    assert [e["id"] for e in a["etapes"]] == ["creation", "systeme", "installation", "lancement"]
    assert [e["etat"] for e in a["etapes"]] == ["en_cours", "a_faire", "a_faire", "a_faire"]
    assert 0 <= a["pourcentage"] < 20 and a["reste_min"] >= 20
    monkeypatch.setattr(vm, "en_demarrage", lambda: False)
    fin = vm.avancement()
    assert fin["fini"] and fin["pourcentage"] == 100 and not fin["erreur"]
    assert all(e["etat"] == "faite" for e in fin["etapes"])
    assert set(json.loads((vm.DATA / "vm" / "durees.json").read_text())) == {"creation", "systeme", "installation",
                                                                            "lancement"}


def test_avancement_plafonne_dans_une_etape(tmp_path, monkeypatch):
    monkeypatch.setattr(vm, "DATA", tmp_path)
    monkeypatch.setattr(vm, "en_demarrage", lambda: True)
    a = vm.Avancement(["demarrage", "verification", "lancement"], False, lambda t: None)
    a.etape("demarrage")
    loin = vm.avancement(maintenant=a.etat["debut_etape"] + 3600)      # l'étape dure bien plus que prévu
    total = sum(vm.ETAPES[i][2] for i in ("demarrage", "verification", "lancement"))
    assert loin["pourcentage"] == int(100 * vm.ETAPES["demarrage"][2] * vm.PLAFOND_ETAPE / total)
    a.etape("installation", remplace="verification")                 # le code a changé
    assert [e["id"] for e in vm.avancement()["etapes"]] == ["demarrage", "installation", "lancement"]
    a.finir(erreur="démarrage refusé par Lima")
    monkeypatch.setattr(vm, "en_demarrage", lambda: False)
    e = vm.avancement()
    assert not e["en_cours"] and not e["fini"] and "Lima" in e["erreur"]


def test_en_demarrage_suit_le_verrou(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(vm, "DATA", tmp_path)
    assert not vm.en_demarrage()
    (tmp_path / "vm").mkdir()
    with open(tmp_path / "vm" / "demarrer.lock", "w") as v:
        fcntl.flock(v, fcntl.LOCK_EX)
        assert vm.en_demarrage()
    assert not vm.en_demarrage()


def test_disque_plein_avant_la_creation(lima, monkeypatch):
    """Disque du Mac presque plein : rien n'est créé ni démarré, et le message le dit en clair
    (sinon la VM ne rend que des « Input/output error »)."""
    etat, commandes = lima
    monkeypatch.setattr(vm, "espace_libre_go", lambda: 0.2)
    from jobsearch import hote
    with pytest.raises(vm.VMImpossible, match="disque de %s est presque plein \\(204 Mo libres\\).*15 Go" % hote.libelle("ton")):
        vm.demarrer(attente_s=1)
    assert not any(c[0] in ("create", "start") for c in commandes)


def test_creer_exige_plus_de_place_que_demarrer(lima, monkeypatch):
    etat, commandes = lima
    monkeypatch.setattr(vm, "espace_libre_go", lambda: 8.0)
    with pytest.raises(vm.VMImpossible, match="15 Go"):
        vm.demarrer(attente_s=1)
    etat["instance"] = {"name": "jobsearch", "status": "Stopped"}
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1"})
    assert vm.demarrer(attente_s=1)["version"] == "1"          # 8 Go suffisent pour démarrer


def test_connexion_refusee_disque_plein(lima, monkeypatch):
    etat, commandes = lima
    etat["instance"] = {"name": "jobsearch", "status": "Running"}
    monkeypatch.setattr(vm, "espace_libre_go", lambda: 1.5)
    with pytest.raises(vm.VMImpossible, match="1.5 Go libres"):
        vm.connexion()
    assert not any(c[0] == "shell" for c in commandes)


def test_espace_inconnu_ne_bloque_rien(monkeypatch):
    monkeypatch.setattr(vm, "espace_libre_go", lambda: None)
    assert vm.disque_plein(15) is None
