"""L'hyperviseur de Windows (vm/wsl.py), sans WSL : un faux wsl.exe garde les commandes et tient l'état."""

import subprocess

import pytest

from jobsearch import vm
from jobsearch.vm import invite, wsl


class FauxWSL:
    def __init__(self, distributions=(), allumees=()):
        self.distributions, self.allumees = list(distributions), set(allumees)
        self.commandes, self.scripts = [], []
        self.provisionne = False
        self.wsl_conf_change = True

    def __call__(self, cmd, input=None, **kw):
        assert cmd[0] == "wsl.exe" and kw.get("env", {}).get("WSL_UTF8") == "1"
        assert "text" not in kw and (input is None or isinstance(input, bytes))     # jamais de CRLF ajouté
        if isinstance(input, bytes):
            input = input.decode("utf-8") if not input.startswith(b"\x1f") and b"\x00" not in input[:512] else input
            assert not isinstance(input, str) or "\r" not in input
        args = cmd[1:]
        self.commandes.append(args)
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, stdout=out.encode(), stderr=b"")
        if args[:2] == ["--list", "--quiet"]:
            noms = sorted(self.allumees) if "--running" in args else self.distributions
            return ok("\x00".join("") + "\n".join(noms) + "\n")
        if args[0] == "--import":
            self.distributions.append(args[1])
            return ok()
        if args[0] == "--terminate":
            self.allumees.discard(args[1])
            return ok()
        if args[0] == "--unregister":
            self.distributions.remove(args[1])
            return ok()
        if args[0] == "-d":
            self.allumees.add(args[1])
            utilisateur = args[3]
            self.scripts.append((utilisateur, input if isinstance(input, str) else "<tar>"))
            if isinstance(input, str) and "wsl.conf.piston" in input:
                change, self.wsl_conf_change = self.wsl_conf_change, False
                return ok("change\n" if change else "")
            if isinstance(input, str) and input.startswith("test -e " + wsl.MARQUE_SYSTEME):
                return subprocess.CompletedProcess(cmd, 0 if self.provisionne else 1, stdout=b"", stderr=b"")
            if isinstance(input, str) and "touch " + wsl.MARQUE_SYSTEME in input:
                self.provisionne = True
            return ok()
        return ok()


@pytest.fixture
def faux(monkeypatch, tmp_path):
    f = FauxWSL()
    monkeypatch.setattr(vm, "EXECUTER", f)
    monkeypatch.setattr(vm, "HYPERVISEUR", "wsl")
    monkeypatch.setattr(vm, "DATA", tmp_path)
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path)
    monkeypatch.setattr(wsl, "_image", lambda dossier: tmp_path / "ubuntu.rootfs.tar.gz")
    monkeypatch.setattr(wsl, "garder_allumee", lambda: 4242)
    monkeypatch.setattr(vm, "espace_libre_go", lambda: 100)
    return f


def test_absent_puis_cree_durci(faux):
    assert vm.instance() is None
    vm.creer()
    d = wsl.distribution()
    imp = next(c for c in faux.commandes if c[0] == "--import")
    assert imp[1] == d and imp[-2:] == ["--version", "2"]
    assert vm.instance() == {"name": d, "status": "Stopped"}       # wsl.conf a changé : arrêtée pour s'appliquer
    racine, conf = faux.scripts[0]
    assert racine == "root"
    assert "[interop]\nenabled=false\nappendWindowsPath=false" in conf
    assert "[automount]\nenabled=false" in conf and "default=piston" in conf
    assert "gpasswd -d piston sudo" in conf and "useradd" in conf


def test_demarrer_monte_le_partage_installe_isole_puis_lance(faux, monkeypatch, tmp_path):
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1", "code": "x"})
    monkeypatch.setattr(vm, "code_installe", lambda: None)
    installes = []
    monkeypatch.setattr(vm, "installer_code", lambda: installes.append(1))
    assert vm.demarrer(attente_s=1)["version"] == "1"
    scripts = faux.scripts
    root = [s for u, s in scripts if u == "root"]
    assert any("mount -t drvfs" in s and str((tmp_path / "partage-vm").resolve()) in s and "umask=077" in s for s in root)
    assert any(s.startswith(invite.PROVISION_SYSTEME[:30]) and "socat nftables" in s for s in root)
    isoler = next(s for s in root if "ip netns add" in s)
    assert "bind=10.200.0.2" in isoler and "setpriv --reuid=piston" in isoler and "0.0.0.0" not in isoler
    assert any('"URLBlocklist"' in s for s in root)                        # stratégies de Chrome, comme root
    lancer = next(s for u, s in scripts if u == "piston" and "jobsearch\" travailleur" in s)
    assert lancer.startswith("export JOBSEARCH_CHROME_HOTE=10.200.0.2\n") and "sudo" not in lancer
    assert installes == [1]
    assert all("sudo" not in s for u, s in scripts if u == "piston")       # le compte de la VM n'a pas sudo


def test_second_demarrage_sans_reinstaller_le_systeme(faux, monkeypatch):
    monkeypatch.setattr(vm, "travailleur_etat", lambda: {"version": "1"})
    monkeypatch.setattr(vm, "code_installe", lambda: vm.empreinte_code())
    monkeypatch.setattr(vm, "installer_moteurs", lambda: None)
    vm.creer()
    faux.provisionne = True
    faux.allumees.clear()
    faux.scripts.clear()
    vm.demarrer(attente_s=1)
    assert not any(s.startswith(invite.PROVISION_SYSTEME[:30]) for _, s in faux.scripts)


def test_sans_isolation_chrome_sur_la_boucle_locale(faux, monkeypatch):
    monkeypatch.setattr(vm, "r", lambda: {"isolation_reseau": False})
    assert wsl._hote_chrome() == "127.0.0.1"
    vm.creer()
    faux.scripts.clear()
    wsl.allumer()
    assert not any("ip netns add" in s for _, s in faux.scripts)
    assert any("rm -f /etc/piston/chrome-hote" in s for _, s in faux.scripts)


def test_eteindre_et_supprimer(faux):
    vm.creer()
    faux.allumees.add(wsl.distribution())
    assert "arrêtée" in vm.arreter() and ["--terminate", wsl.distribution()] in faux.commandes
    assert "supprimée" in vm.supprimer() and ["--unregister", wsl.distribution()] in faux.commandes


def test_wsl_absent_dit_comment_l_installer(monkeypatch, tmp_path):
    def absent(cmd, **kw):
        raise FileNotFoundError("wsl.exe")
    monkeypatch.setattr(vm, "EXECUTER", absent)
    monkeypatch.setattr(vm, "HYPERVISEUR", "wsl")
    with pytest.raises(vm.VMImpossible, match="wsl --install --no-distribution"):
        vm.instance()


def test_wsl_non_installe_utf16le_avant_telechargement(monkeypatch, tmp_path):
    """Avant installation, le wsl.exe système de Windows 11 ignore WSL_UTF8=1 et écrit en UTF-16LE."""
    msg = ("Le Sous-système Windows pour Linux n\u2019est pas installé. Vous pouvez effectuer "
           "l\u2019installation en exécutant « wsl.exe --install ».\r\n").encode("utf-16-le")
    telecharge = []

    def stub(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=msg)
    monkeypatch.setattr(vm, "EXECUTER", stub)
    monkeypatch.setattr(vm, "HYPERVISEUR", "wsl")
    monkeypatch.setattr(vm, "DATA", tmp_path)
    monkeypatch.setattr("jobsearch.travailleur.jeton.DATA", tmp_path)
    monkeypatch.setattr(wsl, "_image", lambda dossier: telecharge.append(1))
    with pytest.raises(vm.VMImpossible, match="wsl --install --no-distribution"):
        vm.instance()
    with pytest.raises(vm.VMImpossible, match="wsl --install --no-distribution"):
        wsl.creer()
    with pytest.raises(vm.VMImpossible, match="wsl --install --no-distribution"):
        vm.demarrer(attente_s=1)
    assert "wsl --install --no-distribution" in (vm.avancement() or {}).get("erreur", "")
    assert not telecharge


def test_copie_du_code_par_tar_sur_l_entree(faux, tmp_path):
    vm.creer()
    d = tmp_path / "code"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    wsl.copier(d, invite.CODE)
    assert faux.scripts[-1] == ("piston", "<tar>")
    assert 'tar -xf - -C "$HOME/code-jobsearch"' in faux.commandes[-1][-1]


def test_architecture(monkeypatch):
    monkeypatch.setattr(wsl.platform, "machine", lambda: "AMD64")
    assert wsl.architecture() == "amd64"
    monkeypatch.setattr(wsl.platform, "machine", lambda: "ARM64")
    assert wsl.architecture() == "arm64"


def test_adresse_de_chrome_dans_la_vm(tmp_path):
    f = tmp_path / "chrome-hote"
    assert invite.hote_chrome({}, str(f)) == "127.0.0.1"
    f.write_text("10.200.0.2\n", encoding="utf-8")
    assert invite.hote_chrome({}, str(f)) == "10.200.0.2"
    assert invite.hote_chrome({"JOBSEARCH_CHROME_HOTE": "10.9.9.9"}, str(f)) == "10.9.9.9"


def test_ecran_joint_dans_l_espace_isole():
    s = vm.LANCER % {"donnees": "/donnees", "code": "c", "port": 8776, "ecran": 8777, "vnc": 5901, "devtools": 9222}
    assert '127.0.0.1:8777 "${JOBSEARCH_CHROME_HOTE:-127.0.0.1}":5901' in s


def test_moteurs_installes_selon_l_architecture():
    from jobsearch.moteur import fournisseurs
    s = fournisseurs.installer_vm()
    assert 'case "$(uname -m)" in' in s and "cli_linux_x64.tar.gz" in s and "cli_linux_arm64.tar.gz" in s
    assert "sudo " not in s.replace("n'exige sudo", "")
